"""Bounded, durable QA planning and actual per-batch generation feedback.

The planner supplies teaching contracts, not executable commands. Workers keep
teacher evidence separate from learner-visible context and independently assess
both answer quality and contract adherence. Historical answers are examples for
coverage and duplicate avoidance, never authoritative evidence.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from itertools import islice
from filelock import FileLock, Timeout

from lib.domain.workflow_qa_director import (
    allocate_qa_types, validate_director_task, validate_qa_director,
    validate_director_skip, validate_dialogue_step,
)
from lib.domain.workflow_quality import accepted, canonical, conversation_issue, text_issue
from lib.domain.workflow_scale import DEFAULT_CONTEXT_WINDOW_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS
from lib.domain.human_augmentation import (
    human_design, human_messages_identity, validate_human_check,
    HUMAN_DIRECTOR_INSTRUCTION, HUMAN_GENERATION_INSTRUCTION, HUMAN_QA_TARGETS,
)
from lib.infrastructure.qa_history import QAHistory, contract_identity
from lib.infrastructure.workflow_rows import WorkflowRows
from lib.model_request_reliability import ModelJSONError


def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def learner_prompt(contract):
    """This is the only contract projection permitted in training messages."""
    question = contract["question"]
    if contract["visible_context"]:
        label = "用户消息：" if contract.get("dialogue_design") else "问题："
        return "资料：\n" + contract["visible_context"] + "\n\n" + label + "\n" + question
    return question


class DirectorPlanError(ValueError):
    """Invalid planner output after bounded repair; safe for task feedback."""

    code = "qa_director_plan_invalid_after_repair"
    kind = "invalid"

    def __init__(self):
        super().__init__(self.code)


class DialoguePlanError(ValueError):
    code = "invalid_dialogue_step"

    def __init__(self):
        super().__init__(self.code)


class WorkflowQADirector:
    def human_package_issue(self, row, target):
        """No derived/cached sample may bypass its human-design assessment."""
        if target not in HUMAN_QA_TARGETS:
            return None
        contract = row.get("qa_contract") or {}
        design = contract.get("human_design")
        if design is None:
            if (self.recipe.get("qa_director", {}).get("human_augmentation", {}).get("enabled")
                    and row.get("evidence_level") != "recorded_context_model_assessed"
                    and row.get("human_augmentation_status") != "recorded_conversation_preserved"):
                return "human_augmentation_design_missing"
            return None
        try:
            expected = human_design(self.recipe.get("qa_director", {}).get("human_augmentation"),
                                    seed_id=design["seed"]["id"], validated=True)
        except (KeyError, TypeError, ValueError):
            return "human_augmentation_design_mismatch"
        if design != expected:
            return "human_augmentation_design_mismatch"
        if target in {"dpo", "orpo", "rlaif"}:
            messages = [*row.get("prompt", []), *row.get("chosen", [])]
        elif target == "cot":
            reasoning = row.get("reasoning", [])
            messages = [*row.get("question", []), {"role": "assistant", "content": row.get("answer", ""),
                        "reasoning_content": "\n".join(reasoning) if isinstance(reasoning, list) else reasoning}]
        else:
            messages = row.get("messages", [])
        receipt = row.get("qa_contract_check", {})
        if target == "multiturn" and row.get("turn_reviews"):
            receipt = row["turn_reviews"][-1].get("qa_contract_check", {})
        try:
            if (receipt.get("keep") is not True or type(receipt.get("adherence")) is not int
                    or receipt["adherence"] < 4):
                return "human_augmentation_review_missing"
            check = receipt.get("human_augmentation", {})
            assessed = validate_human_check({key: value for key, value in check.items()
                                             if key not in {"design_sha256", "messages_sha256"}})
            if (not assessed["keep"] or check.get("design_sha256") != _digest(design)
                    or check.get("messages_sha256") != human_messages_identity(messages)):
                return "human_augmentation_review_mismatch"
        except (TypeError, ValueError, AttributeError):
            return "human_augmentation_review_missing"
        return None

    def qa_identity_design(self, contract):
        """Only adaptive contracts opt in; old persistent identities stay valid."""
        return (contract.get("dialogue_design")
                if self.recipe.get("qa_director", {}).get("planning_mode") == "adaptive" else None)

    def _invalidate_director_call(self, key, *, stage=None):
        stage = stage or self.stage
        invalidate = getattr(self, "invalidate_checkpoint", None)
        if callable(invalidate):
            invalidate(["call", key], stage=stage)
        else:
            (self.path / "checkpoints" / stage / f"{_digest(['call', key])}.json").unlink(missing_ok=True)

    def activate_directed_stage(self, stage):
        self.stage = stage
        for key in ("director", "sft", "multiturn"):
            metrics = self.state["stages"].get(key)
            if metrics and metrics.get("status") in {"running", "pending"}:
                metrics["status"] = "running" if key == stage else "pending"
        self.save()

    @contextmanager
    def qa_publication_guard(self):
        """Serialize final QA dedup/write/publication without holding LLM calls."""
        if not self.recipe.get("qa_director", {}).get("enabled"):
            yield None
            return
        lock = FileLock(str(self.path.parent.parent / ".qa-publication.lock"))
        while True:
            self.check_cancel()
            try:
                lock.acquire(timeout=0.25)
                break
            except Timeout:
                continue
        try:
            with QAHistory(self.path.parent.parent / "qa-history.sqlite3") as history:
                yield history
        finally:
            lock.release()

    def final_qa_duplicate(self, row, target, history):
        if history is None or row.get("status") != "eligible" or not row.get("qa_contract"):
            return None
        c = row["qa_contract"]
        duplicate = history.duplicate(c["question"], visible_context=c["visible_context"],
            answer_policy=c["answer_policy"], qa_type=c["qa_type"],
            dialogue_design=self.qa_identity_design(c),
            namespace="multiturn" if target == "multiturn" else "sft")
        return duplicate if duplicate and duplicate.get("run_id") != self.state["id"] else None

    def qa_history_row(self, row):
        contract = row.get("qa_contract")
        messages = row.get("messages", [])
        if not contract or row.get("status") != "eligible":
            return None
        if not messages:
            messages = row.get("chosen", [])
        answer = (next((message["content"] for message in messages if message.get("role") == "assistant"), None)
                  if messages else row.get("answer"))
        if not isinstance(answer, str) or not answer.strip():
            return None
        return {"id": self.state["id"] + ":" + row["id"],
                "run_id": self.state["id"], "family_id": row["family_id"],
                "source_id": row.get("source_id"), "status": "eligible",
                "question": contract["question"], "answer": answer,
                "qa_type": contract["qa_type"], "visible_context": contract["visible_context"],
                "answer_policy": contract["answer_policy"],
                **({"dialogue_design": self.qa_identity_design(contract)}
                   if self.qa_identity_design(contract) is not None else {})}

    def qa_contract_check(self, key, unit, messages, *, prompt_id="workflow.sft_directed_check",
                          dialogue_state=None, dialogue_steps=None):
        contract = unit["qa_contract"]
        source = unit.get("source_context", unit)
        if not isinstance(source, dict):
            source = unit
        value = self.ask(key, "jev", prompt_id, {
            "qa_contract": contract, "teacher_evidence": source.get("text", ""), "source_context": source,
            "learner_messages": messages, "messages": messages, "user_input": messages[:-1],
            "answer": messages[-1].get("content", ""), "reasoning": messages[-1].get("reasoning_content", ""),
            "answer_rules": self.recipe["qa_director"]["answer_rules"],
            "question_rules": self.recipe["qa_director"]["question_rules"],
            "source_kind": source.get("kind", "brief"), "history_is_not_evidence": True,
            **({"dialogue_state": dialogue_state} if dialogue_state is not None else {}),
            **({"dialogue_steps": dialogue_steps} if dialogue_steps is not None else {}),
        }, allow_reasoning_fallback=False)
        if (not isinstance(value, dict) or set(value) != {"keep", "adherence", "reason"}
                or type(value.get("keep")) is not bool
                or type(value.get("adherence")) is not int
                or not 1 <= value["adherence"] <= 5
                or not isinstance(value.get("reason"), str) or not value["reason"].strip()):
            self._invalidate_director_call(key)
            raise ValueError("invalid_qa_director_judge_schema")
        if design := contract.get("human_design"):
            human_key = [*key, "human_augmentation"]
            check = self.ask(human_key, "jev", "workflow.human_augmentation_check", {
                "human_design": design, "qa_contract": contract, "learner_messages": messages,
                "teacher_evidence": source.get("text", source.get("task", "")),
                "source_kind": "human_provided" if source.get("human_provided") else source.get("kind", "document"),
                "question_rules": self.recipe["qa_director"]["question_rules"],
                "answer_rules": self.recipe["qa_director"]["answer_rules"],
                "fact_verification": "not_independently_verified",
            }, allow_reasoning_fallback=False)
            try:
                check = validate_human_check(check)
            except ValueError:
                self._invalidate_director_call(human_key)
                raise
            value["human_augmentation"] = {**check, "design_sha256": _digest(design),
                                            "messages_sha256": human_messages_identity(messages)}
            if not check["keep"]:
                value.update(keep=False, adherence=1, reason="human_augmentation_design_rejected")
        return value

    def next_dialogue_step(self, unit, messages, previous_state, *, attempt=0, feedback=None):
        """Replan from actual accepted messages using the director node binding.

        Never change self.stage: different conversations can be generated in
        parallel. The request and response stay in the current worker's durable
        checkpoints while model and editable prompt are resolved at director.
        """
        key = [unit["id"], "dialogue_director", len(messages) // 2, attempt, _digest(messages)]
        data = {
            "request_phase": "next_turn", "planning_mode": "adaptive",
            "qa_contract": unit["qa_contract"],
            "dialogue_design": unit["qa_contract"].get("dialogue_design", {}),
            "dialogue_messages": messages, "previous_dialogue_state": previous_state,
            "teacher_evidence": unit.get("text", "")[:20_000],
            "teacher_evidence_not_shared_understanding": True,
            "completed_turns": len(messages) // 2,
            "maximum_turns": self.recipe.get("conversation_turns", 3),
            "question_rules": self.recipe["qa_director"]["question_rules"],
            "answer_rules": self.recipe["qa_director"]["answer_rules"], "feedback": feedback,
        }
        try:
            value = self.ask(key, "generation", "workflow.qa_director", data,
                allow_reasoning_fallback=False, model_stage="director", prompt_stage="director",
                **({"instruction": HUMAN_DIRECTOR_INSTRUCTION} if unit["qa_contract"].get("human_design") else {}))
        except ModelJSONError as error:
            raise DialoguePlanError() from error
        try:
            return validate_dialogue_step(value, completed_turns=len(messages) // 2)
        except ValueError as error:
            self._invalidate_director_call(key)
            raise DialoguePlanError() from error

    @staticmethod
    def qa_metadata(sample, check=None):
        if not sample.get("qa_contract"):
            return ({"human_augmentation_status": "recorded_conversation_preserved"}
                    if sample.get("human_augmentation_status") == "recorded_conversation_preserved" else {})
        return {key: sample.get(key) for key in ("qa_contract", "family_id", "parent_id")} | (
            {"qa_contract_check": check} if check is not None else {})

    def directed_sft(self, unit):
        """Consume exactly the director question, evidence visibility and policy."""
        if unit.get("kind") == "conversation":
            raise ValueError("qa_director_recorded_conversation_changed")
        contract = unit["qa_contract"]
        style = self.generation_style("sft", unit["id"])
        context = {"source_context": {key: value for key, value in unit.items()
                                      if key not in {"qa_contract", "family_id", "parent_id", "director_batch"}},
                   "qa_contract": contract,
                   "requirement": self.recipe.get("brief", ""),
                   "question_rules": self.recipe["qa_director"]["question_rules"],
                   "answer_rules": self.recipe["qa_director"]["answer_rules"]}
        if style is not None:
            context = self.without_source_reasoning(context)
            context["generation_style"] = style
        feedback = None
        check = contract_check = style_check = None
        instructions = ([HUMAN_GENERATION_INSTRUCTION] if contract.get("human_design") else [])
        if style is not None:
            instructions.append("生成风格要求：\n" + style["instruction"])
        for attempt in range(2):
            candidate = self.ask([unit["id"], "sft_directed", attempt], "generation",
                "workflow.sft_directed", {**context, "feedback": feedback},
                **({"instruction": "\n".join(instructions)} if instructions else {}),
                **({"allow_reasoning_fallback": False} if style is not None else {}))
            if (not isinstance(candidate, dict)
                    or any(text_issue(candidate.get(key)) for key in ("question", "answer", "reasoning"))
                    or candidate["question"].strip() != contract["question"]):
                feedback = "保持指导员分配的问题原文，返回完整问题、答案和推理字段。"
                continue
            quotes = candidate.get("quotes")
            if (not isinstance(quotes, list) or len(quotes) > 32
                    or any(not isinstance(q, str) or not q.strip() for q in quotes)
                    or any(q not in unit.get("text", "") for q in quotes)
                    or (unit["kind"] == "document" and contract["answer_policy"] in {"answer", "correct_premise"}
                        and not quotes)):
                feedback = "证据必须是教师来源中的逐字片段；不能把历史答案当作事实依据。"
                continue
            messages = [{"role": "user", "content": learner_prompt(contract)},
                        {"role": "assistant", "content": candidate["answer"],
                         "reasoning_content": candidate["reasoning"]}]
            issue = conversation_issue(messages)
            if issue:
                feedback = issue
                continue
            check = self.judge_answer([unit["id"], "sft_directed_judge", attempt],
                {**context, "rendered_prompt": messages[:-1]}, messages,
                **({"allow_reasoning_fallback": False} if style is not None else {}))
            contract_check = self.qa_contract_check([unit["id"], "sft_directed_contract", attempt], unit, messages)
            style_check = (self.style_check([unit["id"], "sft_directed_style", attempt], style,
                           candidate["reasoning"], candidate["answer"]) if style is not None else None)
            if (accepted(check) and contract_check["keep"] and contract_check["adherence"] >= 4
                    and (style_check is None or (style_check["keep"] and style_check["adherence"] >= 4))):
                return [{"id": unit["id"], "source_id": unit["source_id"],
                         "source_name": unit.get("source_name"), "location": unit.get("location"),
                         "source_location": unit.get("source_location"), "status": "eligible",
                         "messages": messages, "tools": [], "quotes": quotes, "judge": check,
                         "source_context": unit, "qa_contract": contract,
                         "qa_contract_check": contract_check, "family_id": unit["family_id"],
                         "parent_id": unit.get("parent_id"), "repair_attempts": attempt,
                         "reasoning_origin": "prompt_styled_generation" if style else "synthetic_explanation",
                         "evidence_level": ("human_provided_and_model_assessed" if unit.get("human_provided") else
                                            "model_assessed_synthetic" if unit["kind"] == "brief"
                                            else "model_transcribed_visual_source_and_model_assessed" if unit.get("document_reading")
                                            else "source_and_model_assessed"),
                         **({"generation_style": style, "style_check": style_check} if style else {})}]
            feedback = {"quality": check["reason"], "contract": contract_check["reason"],
                        "style": style_check["reason"] if style_check else None}
        return [{**self.rejected(unit, "directed_sft_quality_failed_after_repair"),
                 "qa_contract": contract, "family_id": unit["family_id"],
                 "parent_id": unit.get("parent_id"), "feedback": feedback,
                 "judge": check, "qa_contract_check": contract_check,
                 **({"generation_style": style, "style_check": style_check} if style else {})}]

    def _director_batch_plan(self, batch, offset, config, local_history, published_history, stages):
        """Persist inputs *before* calling so resume cannot observe changed history."""
        self.activate_directed_stage("director")
        guided = [unit for unit in batch if unit["kind"] != "conversation"]
        if not guided:
            return batch
        key = ["director_batch", offset, _digest(guided)]
        adaptive = config.get("planning_mode", "balanced") == "adaptive"
        human = config.get("human_augmentation", {})
        design_offset = offset + (self.state.get("production", {}).get("attempted", 0)
                                  if human.get("enabled") and self.production_enabled() else 0)
        assigned = ((None,) * len(guided) if adaptive else
                    allocate_qa_types(len(guided), config["type_weights"], offset=offset))
        def snapshot():
            limit = config["history_limit"]
            history = []
            query = " ".join(unit.get("text", "")[:1000] for unit in guided[:4])
            if limit:
                for store in (local_history, published_history):
                    for stage in stages:
                        history.extend(store.similar(query, limit=limit, namespace=stage))
                seen, examples = set(), []
                for example in history:
                    identity = (example["id"], example.get("run_id"))
                    if identity not in seen:
                        seen.add(identity)
                        examples.append(example)
                    if len(examples) == limit:
                        break
                history = examples
            text_limit = min(20_000, max(1000, 120_000 // len(guided)))
            data = {"candidates": [
                        {"id": unit["id"], "source_id": unit["source_id"], "kind": unit["kind"],
                         "teacher_evidence": unit.get("text", "")[:text_limit],
                         "generation_variant": unit.get("generation_variant"),
                         "assigned_type": qa_type,
                         "source_is_synthetic": unit["kind"] == "brief",
                         **({"human_design": human_design(human, design_offset + index,
                                  seed_id=(unit.get("human_design") or {}).get("seed", {}).get("id"), validated=True),
                             "source_kind": "human_provided" if unit.get("human_provided") else unit["kind"]}
                            if human.get("enabled") else {})}
                        for index, (unit, qa_type) in enumerate(zip(guided, assigned))],
                    "assigned_types": list(assigned), "offset": design_offset,
                    "coverage": deepcopy(self.state["qa_director"]["coverage"]),
                    "feedback": deepcopy(self.state["qa_director"]["feedback"]),
                    "history": history, "history_is_not_evidence": True,
                    "question_rules": config["question_rules"], "answer_rules": config["answer_rules"],
                    "requirement": self.recipe.get("brief", ""),
                    "targets": list(stages)}
            if adaptive:
                data.update(request_phase="batch_design", planning_mode="adaptive",
                    evidence_modes=list(config["type_weights"]),
                    type_weights_are_not_quotas=True)
            binding = self.recipe.get("node_models", {}).get("director", {}).get("generation", {})
            context_limit = binding.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
            output_limit = binding.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
            system = self.recipe["node_prompt_system"] + "\n" + self.prompt_text("workflow.qa_director")
            available = context_limit - output_limit - len(system.encode("utf-8")) - 512
            original = [candidate["teacher_evidence"] for candidate in data["candidates"]]
            for candidate in data["candidates"]:
                candidate["teacher_evidence"] = ""
            while data["history"] and len(canonical(data).encode("utf-8")) + 128 * len(guided) > available:
                data["history"].pop()
            source_budget = available - len(canonical(data).encode("utf-8"))
            if source_budget < 128 * len(guided):
                raise ValueError("qa_director_context_too_small")
            per_source = source_budget // len(guided)
            for candidate, text in zip(data["candidates"], original):
                # Leave headroom for JSON control-character escaping and UTF-8.
                clipped = text.encode("utf-8")[:max(64, per_source - 32)].decode("utf-8", errors="ignore")
                candidate["teacher_evidence"] = clipped
            while len(canonical(data).encode("utf-8")) > available:
                longest = max(data["candidates"], key=lambda item: len(item["teacher_evidence"]))
                if len(longest["teacher_evidence"]) < 64:
                    raise ValueError("qa_director_context_too_small")
                longest["teacher_evidence"] = longest["teacher_evidence"][:len(longest["teacher_evidence"]) // 2]
            return data
        inputs = self.checkpoint([*key, "inputs"], snapshot)
        def plan():
            production = adaptive or (self.recipe.get("version", 0) >= 12 and self.recipe.get("production") is not None)
            repair_feedback = None
            for attempt in range(3 if production else 1):
                call_key = [*key, "plan"] if attempt == 0 else [*key, "plan", attempt]
                data = inputs if repair_feedback is None else {**inputs, "plan_repair": repair_feedback}
                try:
                    response = self.ask(call_key, "generation", "workflow.qa_director", data,
                                        allow_reasoning_fallback=False,
                                        **({"instruction": HUMAN_DIRECTOR_INSTRUCTION} if human.get("enabled") else {}))
                except ModelJSONError:
                    if not production:
                        raise
                    response = None
                try:
                    tasks = response.get("tasks") if isinstance(response, dict) else None
                    if (not isinstance(tasks, list) or len(tasks) != len(guided)
                            or any(not isinstance(task, dict) for task in tasks)):
                        raise ValueError("invalid_qa_director_batch")
                    # A malformed (unhashable) id is output invalidity too.
                    if any(not isinstance(task.get("id"), str) for task in tasks):
                        raise ValueError("qa_director_batch_id_mismatch")
                    by_id = {task["id"]: task for task in tasks}
                    if len(by_id) != len(guided) or set(by_id) != {unit["id"] for unit in guided}:
                        raise ValueError("qa_director_batch_id_mismatch")
                    contracts = {}
                    for candidate in inputs["candidates"]:
                        task = by_id[candidate["id"]]
                        if adaptive and "skip_reason" in task:
                            contracts[candidate["id"]] = validate_director_skip(task, candidate["id"])
                            continue
                        if adaptive and "dialogue_design" not in task:
                            raise ValueError("qa_director_dialogue_design_required")
                        contracts[candidate["id"]] = validate_director_task(
                            task, expected_type=candidate["assigned_type"],
                            source_text=candidate["teacher_evidence"],
                            expected_id=candidate["id"])
                        if candidate.get("human_design"):
                            contracts[candidate["id"]]["human_design"] = deepcopy(candidate["human_design"])
                    return contracts
                except ValueError:
                    self._invalidate_director_call(call_key)
                    if not production:
                        raise
                    # Fixed feedback explains the contract without echoing the
                    # rejected model response, provider text or source excerpts.
                    repair_feedback = {
                        "code": "qa_director_contract_validation_failed",
                        "instruction": "返回与 candidates 数量及 id 完全一致的 tasks；balanced 保持分配题型，"
                            "adaptive 选择依据合适的类型并附 dialogue_design，或明确 skip_reason。"
                            "字段与类型须符合模板，visible_context 和 evidence_quotes 必须逐字来自对应"
                            " teacher_evidence；无线索题的 visible_context 必须为空。不得新增资料或引用历史为事实。",
                        "attempt": attempt + 1,
                    }
                    self.event("director_plan_repair", attempt=attempt + 1,
                               reason="qa_director_contract_validation_failed")
            raise DirectorPlanError()
        try:
            contracts = self.checkpoint([*key, "contracts"], plan)
        except DirectorPlanError:
            # Preserve unrelated batches and recorded conversations. No worker
            # may silently fall back to unguided generation for this batch.
            metrics = self.state["stages"]["director"]
            metrics["done"] += len(guided)
            metrics["outputs"] += len(guided)
            metrics["quarantined"] += len(guided)
            metrics["batches_done"] += 1
            feedback = self.state["qa_director"]["feedback"]
            feedback[DirectorPlanError.code] = feedback.get(DirectorPlanError.code, 0) + len(guided)
            self.event("director_batch_quarantined", reason=DirectorPlanError.code, count=len(guided))
            return [unit if unit["kind"] == "conversation" else
                    {**unit, "director_plan_failure": DirectorPlanError.code} for unit in batch]
        result = []
        for unit in batch:
            if unit["kind"] == "conversation":
                result.append(unit)
                continue
            contract = contracts[unit["id"]]
            if "skip_reason" in contract:
                result.append({**unit, "director_skip_reason": contract["skip_reason"],
                               "director_skip_guidance": contract.get("guidance", "")})
                feedback = self.state["qa_director"]["feedback"]
                feedback[contract["skip_reason"]] = feedback.get(contract["skip_reason"], 0) + 1
                self.state["qa_director"]["skipped"] = self.state["qa_director"].get("skipped", 0) + 1
                continue
            result.append({**unit, "qa_contract": contract,
                           "family_id": _digest([unit["source_id"], contract["question"]]),
                           "parent_id": unit.get("generation_variant", {}).get("source_unit_id", unit["id"]),
                           "director_batch": offset // config["batch_size"]})
            self.state["qa_director"]["coverage"][contract["qa_type"]]["planned"] += 1
            self.state["qa_director"]["coverage"][contract["qa_type"]]["assigned"] += len(stages)
        metrics = self.state["stages"]["director"]
        metrics["done"] += len(guided)
        metrics["outputs"] += len(guided)
        skipped = sum(bool(unit.get("director_skip_reason")) for unit in result)
        metrics["eligible"] += len(guided) - skipped
        metrics["quarantined"] += skipped
        metrics["batches_done"] += 1
        self.state["qa_director"]["batches_planned"] += 1
        self.save()
        return result

    def _directed_worker_batch(self, stage, batch, offset, handle, local_history, published_history):
        """Workers may finish out of order; acceptance and dedup commit in order."""
        self.activate_directed_stage(stage)
        self._abort.clear()
        metrics = self.state["stages"][stage]
        self.event("stage_batch_started", batch=metrics["batches_done"] + 1)
        action = getattr(self, stage)
        registered, duplicates = {}, {}
        for index, unit in enumerate(batch):
            if not unit.get("qa_contract"):
                continue
            c = unit["qa_contract"]
            identity = contract_identity(c["question"], c["visible_context"], c["answer_policy"], c["qa_type"],
                                         dialogue_design=self.qa_identity_design(c))
            if identity in registered:
                duplicates[index] = registered[identity]
            else:
                registered[identity] = unit["id"]
        def process(index, unit):
            self.check_cancel()
            item_key = ["item", index, _digest(unit)]
            has_checkpoint = getattr(self, "checkpoint_has", None)
            cached = (has_checkpoint(item_key, stage=stage) if callable(has_checkpoint) else
                      (self.path / "checkpoints" / stage / f"{_digest(item_key)}.json").exists())
            def generate():
                if unit.get("director_plan_failure"):
                    return [self.rejected(unit, DirectorPlanError.code)]
                if unit.get("director_skip_reason"):
                    return [{**self.rejected(unit, unit["director_skip_reason"]),
                             "director_skip_guidance": unit.get("director_skip_guidance", "")}]
                if unit.get("qa_contract"):
                    c = unit["qa_contract"]
                    def dispatch():
                        if index - offset in duplicates:
                            return {"reason": "duplicate_qa_contract_in_batch", "duplicate_of": duplicates[index - offset]}
                        duplicate = local_history.duplicate(c["question"], visible_context=c["visible_context"],
                            answer_policy=c["answer_policy"], qa_type=c["qa_type"], namespace=stage,
                            dialogue_design=self.qa_identity_design(c),
                            exclude_id=self.state["id"] + ":" + unit["id"])
                        if duplicate:
                            return {"reason": "duplicate_qa_contract", "duplicate_of": duplicate["id"]}
                        duplicate = published_history.duplicate(c["question"], visible_context=c["visible_context"],
                            answer_policy=c["answer_policy"], qa_type=c["qa_type"], namespace=stage,
                            dialogue_design=self.qa_identity_design(c))
                        if duplicate and duplicate.get("run_id") != self.state["id"]:
                            return {"reason": "duplicate_qa_contract", "duplicate_of": duplicate["id"]}
                        return {"reason": None}
                    decision = self.checkpoint(["qa_dispatch", index, _digest(unit)], dispatch)
                    if decision["reason"]:
                        return [{**self.rejected(unit, decision["reason"]), "qa_contract": c,
                                 "family_id": unit["family_id"], "duplicate_of": decision["duplicate_of"]}]
                run_safely = getattr(self, "run_item_safely", None)
                return run_safely(stage, unit, action) if callable(run_safely) else action(unit)
            return self.checkpoint(item_key, generate), cached
        ordered = {}
        workers = self.recipe.get("concurrency", 1)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, offset + index, unit): index for index, unit in enumerate(batch)}
            try:
                for future in as_completed(futures):
                    ordered[futures[future]] = future.result()
            except BaseException:
                self._abort.set()
                for future in futures:
                    future.cancel()
                raise
        for index, unit in enumerate(batch):
            rows, cached = ordered[index]
            metrics["cached"] += int(cached)
            for row in rows:
                historical = self.qa_history_row(row)
                if historical is not None and not local_history.add(historical, namespace=stage):
                    row = {**self.rejected(unit, "duplicate_qa_contract"),
                           "qa_contract": unit["qa_contract"], "family_id": unit["family_id"]}
                metrics["outputs"] += 1
                metrics["eligible"] += int(row["status"] == "eligible")
                metrics["quarantined"] += int(row["status"] == "quarantined")
                if row.get("qa_contract"):
                    qa_type = row["qa_contract"]["qa_type"]
                    self.state["qa_director"]["coverage"][qa_type][
                        "accepted" if row["status"] == "eligible" else "rejected"] += 1
                    if row.get("reason"):
                        feedback = self.state["qa_director"]["feedback"]
                        feedback[row["reason"]] = feedback.get(row["reason"], 0) + 1
                handle.write(canonical(row) + "\n")
            metrics["done"] += 1
        handle.flush()
        metrics["batches_done"] += 1
        self.save()

    def directed_generation(self, units, *, needs_sft, needs_multiturn):
        config = validate_qa_director(self.recipe.get("qa_director"))
        if human := config.get("human_augmentation"):
            binding = self.recipe.get("node_models", {}).get("director", {}).get("generation", {})
            system = self.recipe["node_prompt_system"] + "\n" + self.prompt_text("workflow.qa_director", stage="director")
            available = (binding.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
                         - binding.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
                         - len(system.encode("utf-8")) - len(HUMAN_DIRECTOR_INSTRUCTION.encode("utf-8")) - 4096)
            largest = max(len(canonical(human_design(human, index, validated=True)).encode("utf-8"))
                          for index in range(len(human["seeds"]))) + 2048
            if available < largest:
                raise ValueError("human_augmentation_context_too_small")
            config["batch_size"] = min(config["batch_size"], max(1, available // largest))
        stages = [stage for stage, active in (("sft", needs_sft), ("multiturn", needs_multiturn)) if active]
        directory = self.path / "stage-results"
        directory.mkdir(exist_ok=True)
        guided_count, guided_batches, previous_batch = 0, 0, -1
        for index, unit in enumerate(units):
            if unit["kind"] != "conversation":
                guided_count += 1
                current_batch = index // config["batch_size"]
                if current_batch != previous_batch:
                    guided_batches += 1
                    previous_batch = current_batch
        self.state["qa_director"] = {
            "enabled": True, "batches_planned": 0, "batch_size": config["batch_size"],
            "planning_mode": config.get("planning_mode", "balanced"), "skipped": 0,
            "history_limit": config["history_limit"], "coverage": {
                qa_type: {"planned": 0, "assigned": 0, "accepted": 0, "rejected": 0} for qa_type in config["type_weights"]},
            "feedback": {}, "recorded_conversations_preserved": len(units) - guided_count,
            "deduplication": ("exact_message_context_type_policy_goal_intent" if config.get("planning_mode") == "adaptive"
                              else "normalized_exact_question_context_type_policy"),
            "history_recall": "bounded_lexical_examples_not_evidence",
            "teacher_evidence_export": "only_explicit_visible_context",
            "acceptance_basis": "generation_quality_and_contract_checks",
            "publication_basis": "final_package_eligible",
        }
        if config.get("human_augmentation"):
            self.state["qa_director"]["human_augmentation"] = {
                "enabled": True, "seeds": len(config["human_augmentation"]["seeds"]),
                "evidence_kind": "human_provided", "fact_verification": "not_independently_verified",
                "checks": ["question_and_answer_design", "source_consistency", "quality", "deduplication"],
                "identity_sha256": _digest(config["human_augmentation"])}
        for stage, total in [("director", guided_count), *[(stage, len(units)) for stage in stages]]:
            self.state["stages"].setdefault(stage, {"label": "问答指导与调度"})
            self.state["stages"][stage].update(status="running" if stage == "director" else "pending", done=0, total=total, outputs=0,
                eligible=0, quarantined=0, cached=0, batch_size=config["batch_size"],
                concurrency=1 if stage == "director" else self.recipe.get("concurrency", 1),
                batches_done=0, batches_total=(guided_batches if stage == "director" else
                    (total + config["batch_size"] - 1) // config["batch_size"]),
                started_at=_now())
        self.save()
        handles = {}
        iterator = None
        plan_path = directory / ".director.pending"
        try:
            with QAHistory(self.path / "qa-history.sqlite3") as local_history, \
                    QAHistory(self.path.parent.parent / "qa-history.sqlite3") as published_history, \
                    plan_path.open("w", encoding="utf-8") as plans:
                for stage in stages:
                    handles[stage] = (directory / f".{stage}.pending").open("w", encoding="utf-8")
                iterator, offset = iter(units), 0
                while batch := list(islice(iterator, config["batch_size"])):
                    self.check_cancel()
                    planned = self._director_batch_plan(batch, offset, config, local_history, published_history, stages)
                    for unit in planned:
                        plans.write(canonical({"id": unit["id"], "source_id": unit["source_id"],
                            "status": "quarantined" if unit.get("director_plan_failure") or unit.get("director_skip_reason") else "ready",
                            **({"reason": unit["director_plan_failure"]} if unit.get("director_plan_failure") else {}),
                            **({"reason": unit["director_skip_reason"],
                                "guidance": unit.get("director_skip_guidance", "")} if unit.get("director_skip_reason") else {}),
                            "qa_contract": unit.get("qa_contract"),
                            "family_id": unit.get("family_id"), "director_batch": unit.get("director_batch"),
                            "route": "recorded_conversation_preserved" if unit["kind"] == "conversation" else
                                [] if unit.get("director_plan_failure") or unit.get("director_skip_reason") else list(stages)}) + "\n")
                    plans.flush()
                    for stage in stages:
                        self._directed_worker_batch(stage, planned, offset, handles[stage], local_history, published_history)
                    offset += len(batch)
        finally:
            # Future exception tracebacks may retain this frame until cyclic GC.
            # Close the disk iterator now so an immediate Windows resume can
            # atomically replace generation-inputs.jsonl without a sharing error.
            if callable(close_iterator := getattr(iterator, "close", None)):
                close_iterator()
            for handle in handles.values():
                handle.close()
        plan_path.replace(directory / "director.jsonl")
        result = {}
        for stage in stages:
            (directory / f".{stage}.pending").replace(directory / f"{stage}.jsonl")
            metrics = self.state["stages"][stage]
            metrics.update(status="completed", finished_at=_now())
            result[stage] = WorkflowRows(directory / f"{stage}.jsonl", metrics["outputs"])
        self.stage = "director"
        self.state["stages"]["director"].update(status="completed", finished_at=_now())
        self.event("stage_completed", outputs=guided_count)
        return result

    def publish_qa_history(self, destination):
        """Publish only rows that survived all configured downstream filters."""
        if not self.recipe.get("qa_director", {}).get("enabled"):
            return
        with QAHistory(self.path.parent.parent / "qa-history.sqlite3") as history:
            for target in ("sft", "multiturn", "dpo", "orpo", "rlaif", "cot"):
                path = destination / f"{target}.records.json"
                if not path.exists():
                    continue
                from lib.infrastructure.json_stream import iter_json_records
                for row in iter_json_records(path):
                    self.check_cancel()
                    record = self.qa_history_row(row)
                    if record is not None:
                        history.add(record, namespace="multiturn" if target == "multiturn" else "sft")
