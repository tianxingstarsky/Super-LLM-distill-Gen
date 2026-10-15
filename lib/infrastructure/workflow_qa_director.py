"""Bounded, durable QA planning and actual per-batch generation feedback.

The planner supplies teaching contracts, not executable commands. Workers keep
teacher evidence separate from learner-visible context and independently assess
both answer quality and contract adherence. Historical answers are examples for
coverage and duplicate avoidance, never authoritative evidence.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import uuid
from itertools import islice
from filelock import FileLock, Timeout

from lib.domain.workflow_qa_director import (
    allocate_qa_types, validate_director_task, validate_qa_director,
    validate_director_skip, validate_dialogue_step,
    validate_question_brief, learning_goal_types, scheduled_user_language, ANSWER_POLICIES,
)
from lib.domain.workflow_quality import accepted, canonical, conversation_issue, text_issue
from lib.domain.provider_reasoning import reasoning_provenance, select_candidate_reasoning
from lib.domain.workflow_scale import DEFAULT_CONTEXT_WINDOW_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS
from lib.domain.human_augmentation import (
    human_design, human_messages_identity, validate_human_check,
    HUMAN_DIRECTOR_INSTRUCTION, HUMAN_REVISION_INSTRUCTION, human_generation_instruction, HUMAN_QA_TARGETS,
)
from lib.infrastructure.qa_history import QAHistory, contract_identity, learner_task_identity, MAX_QUESTION_CHARS
from lib.infrastructure.workflow_rows import WorkflowRows
from lib.model_request_reliability import ModelJSONError


_PLAN_VALIDATION_CODES = frozenset({
    "invalid_qa_director_batch", "qa_director_batch_id_mismatch", "qa_director_dialogue_design_required",
    "invalid_qa_director_task", "qa_director_type_mismatch", "qa_director_id_mismatch",
    "invalid_qa_director_answer_policy", "qa_director_closed_book_contract",
    "qa_director_visible_evidence_required", "invalid_qa_director_evidence",
    "qa_director_evidence_not_in_source", "qa_director_visible_context_not_in_source",
    "qa_director_evidence_not_in_source_line_ranges", "qa_director_multiple_evidence_required",
    "qa_director_evidence_not_visible", "invalid_question_brief", "question_brief_contains_private_source",
    "source_line_ranges_required", "source_line_materials_required", "invalid_source_line_ranges",
    "source_line_material_not_found", "source_line_range_out_of_bounds", "source_line_range_empty",
    "invalid_qa_director_skip",
    "invalid_expression_design", "invalid_dialogue_design",
    "qa_director_learning_goal_mismatch", "qa_director_user_language_mismatch",
    "invalid_qa_director_learning_goal", "invalid_qa_director_user_language",
})


def _plan_validation_code(error):
    """Known contract codes only; never echo model text or private source values."""
    code = str(error)
    return code if code in _PLAN_VALIDATION_CODES else "qa_director_contract_validation_failed"


def _typed_director_repair(recipe):
    """New contracts opt in; historical repair requests remain byte identical."""
    prompts = recipe.get("prompts")
    pinned = prompts.get("workflow.qa_director") if isinstance(prompts, dict) else None
    version = pinned.get("version") if isinstance(pinned, dict) else None
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version) if isinstance(version, str) else None
    return bool(match and tuple(map(int, match.groups())) >= (1, 9, 0))


_DIALOGUE_DESIGN_REPAIR = (
    "dialogue_design必须是对象，仅允许interaction_goal、user_intent、context_links、"
    "success_criteria、turn_guidance、stop_when及conversational_frame。interaction_goal是必填非空字符串"
    "且最多4000字符；user_intent和stop_when如提供须是非空字符串且各最多2000字符。"
    "context_links、success_criteria、turn_guidance如提供必须是字符串数组，每个数组最多12项，"
    "每项非空且最多1000字符；尤其turn_guidance应为[\"可调整方向\"]，不能是单个字符串。"
    "conversational_frame如提供须是对象，只允许speaker_role、listener_role、relationship、setting、register"
    "字符串字段，分别最多800、800、1200、2000、800字符。不新增事实或放宽逐字证据与选区校验。"
)


def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _task_controls(contract):
    return {key: contract[key] for key in ("learning_goal", "user_language") if key in contract}


def _fixed_human_input(design):
    if not isinstance(design, dict):
        return False
    seed = design.get("seed") or {}
    return bool(seed.get("question") or seed.get("revision_context") or design.get("context"))


def _candidate_controls(config, offset, design=None):
    """Keep fixed human input outside automatic evidence/language scheduling."""
    if not any(key in config for key in ("learning_goal", "user_languages")):
        return {}
    if _fixed_human_input(design):
        return {"automatic_controls_applied": False, "preserve_human_input": True}
    controls = {"automatic_controls_applied": True}
    if "learning_goal" in config:
        controls["learning_goal"] = config["learning_goal"]
        controls["allowed_qa_types"] = list(learning_goal_types(config["learning_goal"]))
    if "user_languages" in config:
        controls["user_language"] = scheduled_user_language(config["user_languages"], offset)
    return controls


def learner_prompt(contract):
    """This is the only contract projection permitted in training messages."""
    question = contract["question"]
    if contract["visible_context"]:
        if contract.get("user_language") == "en":
            label = "User message:" if contract.get("dialogue_design") else "Question:"
            return "Source material:\n" + contract["visible_context"] + "\n\n" + label + "\n" + question
        label = "用户消息：" if contract.get("dialogue_design") else "问题："
        return "资料：\n" + contract["visible_context"] + "\n\n" + label + "\n" + question
    return question


def actual_qa_input_messages(row, target=None):
    """Project exactly the public training input, with private reasoning removed.

    Single-turn SFT and its CoT projection produce the same list. Multi-turn
    input retains all actual preceding answers and user constraints; an opening
    question alone cannot identify that complete task.
    """
    if target == "cot" or (target is None and row.get("question") is not None
                           and row.get("answer") is not None and row.get("reasoning") is not None):
        question = row.get("question")
        messages = question if isinstance(question, list) else [{"role": "user", "content": question}]
    elif target in {"dpo", "orpo", "rlaif"} or (target is None and isinstance(row.get("prompt"), list)):
        messages = row.get("prompt")
    else:
        original = row.get("messages")
        messages = original[:-1] if isinstance(original, list) and original else None
    if (not isinstance(messages, list) or not messages
            or any(not isinstance(message, dict) for message in messages)
            or not any(message.get("role") == "user" for message in messages)):
        return None
    private = {"reasoning_content", "reasoning", "thinking", "reasoning_summary", "reasoning_details", "thinking_blocks"}
    return [{name: value for name, value in message.items() if name not in private} for message in messages]


def actual_qa_input_identity(row, target=None):
    messages = actual_qa_input_messages(row, target)
    return _digest(messages) if messages is not None else None


def _actual_question_summary(question):
    if len(question) <= MAX_QUESTION_CHARS:
        return question
    note = "\n[History excerpt only; complete input is identified by actual_input_sha256.]\n"
    size = (MAX_QUESTION_CHARS - len(note)) // 2
    return question[:size] + note + question[-size:]


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


class DirectorBatchTooLarge(ValueError):
    """Split a batch before any model request; never trim qualified context."""


class WorkflowQADirector:
    def natural_expressions(self):
        """New snapshots opt in; older request/checkpoint hashes stay valid."""
        return self.recipe.get("qa_expression_version") in {1, 2}

    def participant_utterances(self):
        return self.recipe.get("qa_expression_version") == 2

    def _natural_expression_guidance(self):
        if not self.natural_expressions():
            return {}
        from lib.domain.dialogue_expression import natural_expression_guide
        return {"natural_expression_guidance": natural_expression_guide(self.recipe["qa_expression_version"])}

    def separated_questions(self):
        return self.recipe.get("qa_director", {}).get("question_separation") is True

    def _question_brief_boundary(self, unit, contract, brief, visible_messages=None):
        """No source bodies, reference answers or addressed identities cross."""
        if contract.get("user_language") and isinstance(brief, dict):
            if brief.get("user_language", contract["user_language"]) != contract["user_language"]:
                raise ValueError("qa_director_user_language_mismatch")
            brief = {**brief, "user_language": contract["user_language"]}
        brief = validate_question_brief(brief)
        body = canonical(brief)
        public = "\n".join(message["content"] for message in visible_messages or [])
        def private_span(text, minimum):
            span = text.strip()
            # Short factual statements and numeric answers must not bypass
            # the boundary. A broad concept name may be a valid topic; the
            # source-aware semantic assessment below checks it before sending.
            factual = bool(re.search(r"[0-9。！？.!?；;]", span))
            return bool(span and (len(span) >= minimum or factual) and span in body and span not in public)
        for quote in contract.get("evidence_quotes", []):
            if private_span(quote, 12):
                raise ValueError("question_brief_contains_private_source")
        materials = self._director_evidence_materials(unit) or []
        for material in materials:
            identity = material.get("material_id")
            if identity and identity in body:
                raise ValueError("question_brief_contains_private_source")
            # Briefs describe a topic and action, never reproduce source lines.
            for line in material["text"].splitlines():
                if private_span(line, 24):
                    raise ValueError("question_brief_contains_private_source")
        return brief

    def _assess_question_brief(self, unit, contract, brief, visible_messages, *, key):
        """Check paraphrased facts before they cross into the blind role."""
        from lib.infrastructure.source_memory_messages import directed_source_request
        data = directed_source_request(self.recipe, {
            "source_context": unit, "qa_contract": contract, "question_brief": brief,
            "visible_dialogue": visible_messages, **_task_controls(contract)}, unit, contract)
        for attempt in range(2):
            try:
                result = self.ask([*key, "brief_boundary", _digest(brief), _digest(visible_messages), attempt],
                    "generation", "workflow.question_brief_check", data,
                    model_stage="director", prompt_stage="director", allow_reasoning_fallback=False)
            except ModelJSONError:
                continue
            if (not isinstance(result, dict) or set(result) != {"keep", "reason"}
                    or type(result.get("keep")) is not bool or not isinstance(result.get("reason"), str)
                    or not result["reason"].strip() or len(result["reason"]) > 4000):
                continue
            if not result["keep"]:
                # The source-aware explanation stays in this call's private
                # checkpoint. It is never a retry instruction to the questioner.
                raise ValueError("question_brief_contains_private_source")
            self.event("question_brief_checked", role="generation", visible_turns=len(visible_messages) // 2)
            return
        raise ValueError("invalid_question_brief_assessment")

    def _closed_book_question(self, unit, contract, *, key, visible_messages=None, brief=None, expression_design=None):
        """Separate authorship, then verify the real question against evidence."""
        from lib.infrastructure.dialogue_role_messages import visible_dialogue
        visible_messages = (visible_dialogue([*visible_messages, {"role": "user", "content": "next turn"}])[:-1]
                            if visible_messages else [])
        brief = contract.get("question_brief") if brief is None else brief
        if self.participant_utterances() and isinstance(brief, dict):
            from lib.domain.dialogue_expression import carry_expression_design
            inherited = contract.get("expression_design") if expression_design is None else expression_design
            design = carry_expression_design(inherited, brief.get("expression_design"),
                first_turn=not visible_messages or expression_design is not None)
            if design:
                brief = {**brief, "expression_design": design}
        brief = self._question_brief_boundary(unit, contract, brief, visible_messages)
        self._assess_question_brief(unit, contract, brief, visible_messages, key=key)
        retry_code = None
        for attempt in range(2):
            data = {"question_brief": brief, "question_rules": self.recipe["qa_director"]["question_rules"],
                    **self._natural_expression_guidance()}
            if visible_messages:
                data["visible_dialogue"] = visible_messages
            if retry_code:
                data["retry_code"] = retry_code
            try:
                response = self.ask([*key, "question", attempt], "question", "workflow.question_user", data,
                    model_stage="director", prompt_stage="director", allow_reasoning_fallback=False)
            except ModelJSONError:
                retry_code = "invalid_question_output"
                continue
            if (not isinstance(response, dict) or set(response) != {"message"}
                    or text_issue(response.get("message")) or len(response["message"]) > 6000):
                retry_code = "invalid_question_output"
                continue
            question = response["message"].strip()
            from lib.infrastructure.source_memory_messages import directed_source_request
            fit_data = directed_source_request(self.recipe, {
                "source_context": unit, "qa_contract": contract, "question": question,
                "question_brief": brief, "visible_dialogue": visible_messages or [],
                **_task_controls(contract)}, unit, contract)
            try:
                fit = self.ask([*key, "question_fit", attempt], "generation", "workflow.question_fit", fit_data,
                    model_stage="director", prompt_stage="director", allow_reasoning_fallback=False)
            except ModelJSONError:
                retry_code = "invalid_question_assessment"
                continue
            if (not isinstance(fit, dict) or set(fit) != {"keep", "answer_policy", "reason"}
                    or type(fit.get("keep")) is not bool or fit.get("answer_policy") not in ANSWER_POLICIES
                    or not isinstance(fit.get("reason"), str) or not fit["reason"].strip() or len(fit["reason"]) > 4000):
                retry_code = "invalid_question_assessment"
                continue
            if fit["keep"]:
                self.event("question_dispatched", role="question", qa_type="closed_book",
                           visible_turns=len(visible_messages or []) // 2)
                return {**contract, "question": question, "answer_policy": fit["answer_policy"],
                        "question_brief": brief,
                        **({"expression_design": deepcopy(brief["expression_design"])}
                           if self.natural_expressions() and brief.get("expression_design") else {})}
            # Never send a source-aware judge's free text to the questioner.
            retry_code = "question_not_suitable"
        raise ValueError(retry_code or "invalid_question_output")

    def _require_clean_director_evidence(self, unit):
        """A shared document route must never plan from raw/rejected text."""
        if not self.shared_source_cleaning() or unit.get("kind") != "document":
            return
        cleaning = unit.get("source_cleaning") or {}
        if (unit.get("status") not in {"ready", "eligible"}
                or cleaning.get("status") != "accepted"
                or cleaning.get("cleaned_text_sha256") != _digest(unit.get("text"))):
            raise ValueError("qa_director_clean_source_required")

    def human_package_issue(self, row, target):
        """No derived/cached sample may bypass its human-design assessment."""
        if self.recipe.get("review_repair") is not None:
            receipt = row.get("review_repair", {})
            try:
                expected = _digest(self._review_payload(target, row))
                if (receipt.get("status") == "accepted" and receipt.get("candidate_sha256") == expected
                        and receipt.get("config_sha256") == _digest(self.recipe["review_repair"])):
                    return None
            except (KeyError, TypeError, ValueError):
                pass
            return "review_repair_assessment_missing"
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

    def qa_core_dedup(self, namespace):
        # A multi-turn opening may lead to genuinely different later user
        # constraints. Its complete dialogue is deduplicated after generation.
        return self.recipe.get("qa_agent_memory_version") == 1 and namespace != "multiturn"

    def qa_actual_only(self, namespace):
        return self.recipe.get("qa_agent_memory_version") == 1 and namespace == "multiturn"

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
        if not self.recipe.get("qa_director", {}).get("enabled") and not self.recipe.get("repair_inputs"):
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
                pending_path = None
                pending = None
                if self.recipe.get("qa_agent_memory_version") == 1:
                    directory = self.path / "stage-results"
                    directory.mkdir(parents=True, exist_ok=True)
                    pending_path = directory / (".qa-publication-" + uuid.uuid4().hex + ".sqlite3")
                    pending = sqlite3.connect(pending_path)
                    pending.execute("PRAGMA cache_size=-2048")
                    pending.execute("CREATE TABLE actual_inputs (namespace TEXT, identity TEXT, sample_id TEXT, "
                                    "PRIMARY KEY(namespace,identity), UNIQUE(namespace,sample_id)) WITHOUT ROWID")
                self._qa_publication_actuals = pending
                try:
                    yield history
                finally:
                    self._qa_publication_actuals = None
                    if pending is not None:
                        pending.close()
                    if pending_path is not None:
                        pending_path.unlink(missing_ok=True)
        finally:
            lock.release()

    def register_final_qa_input(self, row, target):
        """Reserve a fully accepted pending input, not a published QA record."""
        pending = getattr(self, "_qa_publication_actuals", None)
        if pending is None or not row.get("qa_contract"):
            return
        identity = actual_qa_input_identity(row, target)
        if identity is None:
            raise ValueError("qa_publication_actual_input_missing")
        namespace = "multiturn" if target == "multiturn" else "sft"
        sample_id = self.state["id"] + ":" + row["id"]
        previous = pending.execute("SELECT identity FROM actual_inputs WHERE namespace=? AND sample_id=?",
                                   (namespace, sample_id)).fetchone()
        if previous is not None and previous[0] != identity:
            raise ValueError("qa_publication_sample_input_conflict")
        inserted = pending.execute("INSERT OR IGNORE INTO actual_inputs VALUES (?,?,?)", (namespace, identity, sample_id))
        if not inserted.rowcount:
            owner = pending.execute("SELECT sample_id FROM actual_inputs WHERE namespace=? AND identity=?",
                                    (namespace, identity)).fetchone()
            if owner is None or owner[0] != sample_id:
                raise ValueError("qa_publication_actual_input_conflict")

    def final_qa_duplicate(self, row, target, history):
        if history is None or row.get("status") != "eligible" or not row.get("qa_contract"):
            return None
        c = row["qa_contract"]
        namespace = "multiturn" if target == "multiturn" else "sft"
        if self.recipe.get("qa_agent_memory_version") == 1:
            actual = actual_qa_input_identity(row, target)
            pending = getattr(self, "_qa_publication_actuals", None)
            if pending is not None and actual is not None:
                prior = pending.execute("SELECT sample_id FROM actual_inputs WHERE namespace=? AND identity=?",
                                        (namespace, actual)).fetchone()
                if prior is not None and prior[0] != self.state["id"] + ":" + row["id"]:
                    return {"id": prior[0], "run_id": self.state["id"], "match_kind": "exact_actual_input",
                            "duplicate_scope": "current_publication"}
            duplicate = history.duplicate_actual(actual, namespace=namespace)
            if (duplicate and duplicate.get("id") != self.state["id"] + ":" + row["id"]
                    and not self.human_revision_duplicate_allowed(c, duplicate)):
                return duplicate
            if self.qa_actual_only(namespace):
                return None
        duplicate = history.duplicate(c["question"], visible_context=c["visible_context"],
            answer_policy=c["answer_policy"], qa_type=c["qa_type"],
            dialogue_design=self.qa_identity_design(c),
            namespace=namespace, learner_task_dedup=self.qa_core_dedup(namespace))
        return (duplicate if duplicate and duplicate.get("run_id") != self.state["id"]
                and not self.human_revision_duplicate_allowed(c, duplicate) else None)

    def human_revision_duplicate_allowed(self, contract, duplicate):
        """Only the exact selected ancestor may have a separately saved version.

        Unrelated duplicate questions and duplicates inside this child still fail.
        Old artifacts remain unchanged; session consumers explicitly choose a
        version rather than combining each iteration as independent examples.
        """
        revision = contract.get("revision_context") or contract.get("human_design", {}).get("seed", {}).get("revision_context")
        if not revision or not duplicate:
            return False
        if not any(duplicate.get("run_id") == ancestor["run_id"] and duplicate.get("id") ==
                   ancestor["run_id"] + ":" + ancestor["candidate_id"] for ancestor in revision["ancestors"]):
            return False
        from lib.infrastructure.human_revision import verified_revision_parent
        verified_revision_parent(self.path.parent.parent, self.state["id"], revision)
        return True

    def qa_history_row(self, row, target=None):
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
        actual_metadata = {}
        if self.recipe.get("qa_agent_memory_version") == 1:
            inputs = actual_qa_input_messages(row, target)
            if inputs is not None:
                actual_metadata["actual_input_sha256"] = _digest(inputs)
                if self.qa_actual_only(target):
                    actual_metadata["actual_input_only_identity"] = True
                user = next((message.get("content") for message in inputs if message.get("role") == "user"), None)
                if isinstance(user, str) and user != learner_prompt(contract):
                    actual_metadata.update(actual_input_revision=True, actual_question=_actual_question_summary(user))
        return {"id": self.state["id"] + ":" + row["id"],
                "run_id": self.state["id"], "family_id": row["family_id"],
                "source_id": row.get("source_id"), "source_name": row.get("source_name"), "status": "eligible",
                "question": contract["question"], "answer": answer,
                "qa_type": contract["qa_type"], "visible_context": contract["visible_context"],
                "answer_policy": contract["answer_policy"],
                **actual_metadata,
                **({"expression_design": deepcopy(contract["expression_design"])}
                   if self.natural_expressions() and contract.get("expression_design") else {}),
                **{name: contract[name] for name in ("topic", "skill") if contract.get(name)},
                **({"dialogue_design": self.qa_identity_design(contract)}
                   if self.qa_identity_design(contract) is not None else {})}

    def qa_contract_check(self, key, unit, messages, *, prompt_id="workflow.sft_directed_check",
                          dialogue_state=None, dialogue_steps=None, review_scope="complete"):
        from lib.domain.learner_context import learner_contract, prompt_at_least
        contract = unit["qa_contract"]
        source = unit.get("source_context", unit)
        if not isinstance(source, dict):
            source = unit
        data = {
            "qa_contract": contract, "teacher_evidence": source.get("text", ""), "source_context": source,
            "learner_messages": messages, "messages": messages, "user_input": messages[:-1],
            "answer": messages[-1].get("content", ""), "reasoning": messages[-1].get("reasoning_content", ""),
            "answer_rules": self.recipe["qa_director"]["answer_rules"],
            "question_rules": self.recipe["qa_director"]["question_rules"],
            "source_kind": source.get("kind", "brief"), "history_is_not_evidence": True,
            **({"dialogue_state": dialogue_state} if dialogue_state is not None else {}),
            **({"dialogue_steps": dialogue_steps} if dialogue_steps is not None else {}),
            **_task_controls(contract),
        }
        minimum = (1, 2, 0) if prompt_id in {"workflow.sft_directed_check", "workflow.multiturn_directed_check"} else (1, 1, 0)
        if prompt_at_least(self.recipe, prompt_id, minimum):
            # The source-aware factual review is separate. This reviewer must
            # not reconstruct missing learner context from private evidence.
            data = {name: value for name, value in data.items()
                    if name not in {"teacher_evidence", "source_context", "dialogue_state", "dialogue_steps"}}
            data.update(qa_contract=learner_contract(contract), learner_context_only=True)
        value = self.ask(key, "jev", prompt_id, data, allow_reasoning_fallback=False)
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
                "review_scope": review_scope,
            }, allow_reasoning_fallback=False,
                **({"instruction": HUMAN_REVISION_INSTRUCTION} if design.get("seed", {}).get("revision_context") else {}))
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
            "teacher_evidence": (unit.get("text", "") if unit.get("source_context_pack")
                                 or self.recipe.get("qa_agent_memory_version") == 1
                                 else unit.get("text", "")[:20_000]),
            **({"source_context_pack": unit["source_context_pack"]} if unit.get("source_context_pack") else {}),
            **({"source_line_material_id": unit["source_line_material_id"]}
               if unit.get("source_line_material_id") else {}),
            "teacher_evidence_not_shared_understanding": True,
            "completed_turns": len(messages) // 2,
            "maximum_turns": self.recipe.get("conversation_turns", 3),
            "question_rules": self.recipe["qa_director"]["question_rules"],
            "answer_rules": self.recipe["qa_director"]["answer_rules"], "feedback": feedback,
            **self._natural_expression_guidance(),
            **_task_controls(unit["qa_contract"]),
        }
        if self.separated_questions():
            from lib.infrastructure.source_memory_messages import directed_source_request
            data = directed_source_request(self.recipe, {**data, "question_separation": True}, unit, unit["qa_contract"])
        runtime = self._dialogue_knowledge_runtime(unit)
        if runtime:
            from lib.infrastructure.source_memory_messages import directed_source_request
            data = directed_source_request(self.recipe, data, unit, unit["qa_contract"])
            data["dialogue_source_tools_scope"] = runtime[1]["dialogue_source_tools_scope"]
        with self.knowledge_planning_lock(runtime) if runtime else nullcontext():
            return self._next_dialogue_plan(key, data, runtime, unit, messages, previous_state)

    def _dialogue_knowledge_runtime(self, unit):
        """Read only the first turn's authenticated evidence, never a new range.

        Continuations share answer-free coverage with the original knowledge
        ledger. Their conversation window is separate from batch planning,
        whose earlier turns may contain evidence outside this task's range.
        Whole-document expansion needs a new evidence/worker contract and is
        deliberately unavailable in this protocol.
        """
        descriptor = unit.get("knowledge_agent_context")
        if (self.recipe.get("qa_dialogue_agent_version") != 1
                or self.recipe.get("knowledge_agent_context_version") != 1
                or unit.get("kind") != "document" or not descriptor):
            return None
        from lib.domain.source_line_ranges import number_source_material, resolve_source_line_ranges
        materials = self._director_evidence_materials(unit)
        if not materials:
            return None
        ranges = unit["qa_contract"].get("source_line_ranges")
        if ranges is None:
            ranges = [{"material_id": item["material_id"], "start_line": 1,
                       "end_line": item["line_count"]} for item in materials]
        selected = resolve_source_line_ranges(materials, ranges)
        originals = {item["material_id"]: item for item in materials}
        narrowed, addresses = [], []
        for block in selected:
            original = originals[block["material_id"]]
            identity = "dialogue-scope-" + _digest([block["material_id"],
                block["start_line"], block["end_line"], original["cleaned_text_sha256"]])
            # A derived material uses local tool addresses. The exact original
            # range stays attached as a receipt, never as additional text.
            narrowed.append(number_source_material({"unit_id": identity,
                "source_id": original["source_id"], "source_name": original.get("source_name", ""),
                "source_location": original.get("source_location", {}), "text": block["text"],
                "cleaning_status": "accepted", "cleaned_text_sha256": _digest(block["text"])}))
            addresses.append({"material_id": identity, "start_line": 1,
                "end_line": narrowed[-1]["line_count"],
                "original_range": {name: block[name] for name in ("material_id", "start_line", "end_line")}})
        scope = {"mode": "first_turn_selected_evidence_only", "materials": addresses,
                 "source_expansion_available": False, "history_is_not_evidence": True}
        context = {"version": 1, "knowledge_id": descriptor["knowledge_id"],
            "session_id": _digest(["dialogue_agent_v1", descriptor["session_id"],
                                   [(item["material_id"], item["cleaned_text_sha256"]) for item in narrowed]]),
            "dialogue_source_tools_scope": scope}
        self.state.setdefault("qa_director", {}).setdefault("knowledge_context", {
            **descriptor, "tool_calls": 0, "history_is_not_evidence": True})
        return narrowed, context

    def _next_dialogue_plan(self, key, data, runtime, unit, messages, previous_state):
        """Validate the actual step and blind actor before durable Agent commit."""
        options = ({"instruction": HUMAN_DIRECTOR_INSTRUCTION + human_generation_instruction(unit["qa_contract"])}
                   if unit["qa_contract"].get("human_design") else {})
        try:
            value = (self.knowledge_agent_plan(key, data, runtime, prompt_stage="director", **options) if runtime else
                self.ask(key, "generation", "workflow.qa_director", data,
                    allow_reasoning_fallback=False, model_stage="director", prompt_stage="director", **options))
        except ModelJSONError as error:
            if runtime:
                self.invalidate_knowledge_plan(key)
            raise DialoguePlanError() from error
        try:
            step = validate_dialogue_step(value, completed_turns=len(messages) // 2,
                                          natural_utterance=self.participant_utterances(),
                                          **_task_controls(unit["qa_contract"]))
            if self.participant_utterances():
                from lib.domain.dialogue_expression import carry_expression_design
                inherited = (previous_state or {}).get("expression_design", unit["qa_contract"].get("expression_design"))
                design = carry_expression_design(inherited, step["dialogue_state"].get("expression_design"))
                if design:
                    step["dialogue_state"]["expression_design"] = design
            if (self.separated_questions() and unit["qa_contract"]["qa_type"] == "closed_book"
                    and step["continue"]):
                # A replacement Agent plan may change the brief under the
                # same operation key. Never reuse an actor reply purchased
                # for a rejected plan; historical recipes keep their old key.
                followup_key = ([*key, "followup", "knowledge_plan", _digest(step)]
                                if runtime else [*key, "followup"])
                dispatched = self._closed_book_question(unit, unit["qa_contract"],
                    key=followup_key, visible_messages=messages, brief=step.get("question_brief"),
                    **({"expression_design": step["dialogue_state"].get("expression_design", {})}
                       if self.participant_utterances() else {}))
                step["user_message"] = dispatched["question"]
                step["response_policy"] = dispatched["answer_policy"]
                if self.participant_utterances() and dispatched.get("expression_design"):
                    step["dialogue_state"]["expression_design"] = deepcopy(dispatched["expression_design"])
            # An omitted update retains the last accepted role/register frame.
            # Explicit updates remain untrusted and are checked with real turns.
            if ("conversational_frame" not in step["dialogue_state"]
                    and isinstance(previous_state, dict) and "conversational_frame" in previous_state):
                step["dialogue_state"]["conversational_frame"] = deepcopy(previous_state["conversational_frame"])
            if runtime:
                self.commit_knowledge_plan(key)
            return step
        except ValueError as error:
            if runtime:
                self.invalidate_knowledge_plan(key)
            else:
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
        separated = self.separated_questions()
        evidence_level = (unit.get("revision_source_level") or (
            "human_provided_and_model_assessed" if unit.get("human_provided") else
            "model_assessed_synthetic" if unit["kind"] == "brief" else
            "model_restructured_source_text_and_model_assessed"
                if (unit.get("document_reading") or {}).get("mode") == "model" else
            "model_transcribed_visual_source_and_model_assessed" if unit.get("document_reading") else
            "source_and_model_assessed"))
        style = self.generation_style("sft", unit["id"])
        native_capture = style is None and self.recipe.get("provider_reasoning_capture_version") == 1
        context = {"source_context": {key: value for key, value in unit.items()
                                      if key not in {"qa_contract", "family_id", "parent_id", "director_batch"}},
                   "qa_contract": contract,
                   "requirement": self.recipe.get("brief", ""),
                   "question_rules": self.recipe["qa_director"]["question_rules"],
                   "answer_rules": self.recipe["qa_director"]["answer_rules"],
                   **_task_controls(contract)}
        if style is not None:
            context = self.without_source_reasoning(context)
            context["generation_style"] = style
        from lib.infrastructure.source_memory_messages import directed_source_request, directed_evidence_text, directed_evidence_blocks
        context = directed_source_request(self.recipe, context, unit, contract)
        if self.recipe.get("source_memory_protocol_version") in {1, 2}:
            context["learner_messages"] = [{"role": "user", "content": learner_prompt(contract)}]
        selected_blocks = (directed_evidence_blocks(self.path, unit, contract)
                           if context.get("source_line_scope") == "selected" else None)
        feedback = None
        check = contract_check = style_check = None
        instructions = ([human_generation_instruction(contract)] if contract.get("human_design") else [])
        if style is not None:
            instructions.append("生成风格要求：\n" + style["instruction"])
        failure_kind = "invalid_output"
        for attempt in range(2):
            candidate = self.ask([unit["id"], "sft_directed", attempt], "generation",
                "workflow.sft_answer" if separated else "workflow.sft_directed", {**context, "feedback": feedback},
                **({"instruction": "\n".join(instructions)} if instructions else {}),
                **({"allow_reasoning_fallback": False} if style is not None else {}),
                **({"capture_response_details": True} if native_capture else {}))
            failure_kind = "invalid_output"
            required_fields = (("answer", "reasoning") if style is not None or not native_capture else ("answer",))
            if not separated:
                required_fields = ("question", *required_fields)
            separated_schema_invalid = False
            if separated and isinstance(candidate, dict):
                if style is not None or not native_capture:
                    separated_schema_invalid = set(candidate) != {"answer", "reasoning", "quotes"}
                else:
                    separated_schema_invalid = (not {"answer", "quotes"}.issubset(candidate) or
                        not set(candidate).issubset({"answer", "reasoning", "quotes"}))
            if (not isinstance(candidate, dict)
                    or separated_schema_invalid
                    or any(text_issue(candidate.get(key)) for key in required_fields)
                    or (not separated and candidate["question"].strip() != contract["question"])):
                feedback = (("只返回answer、reasoning、quotes，不新增或改写用户问题。" if style is not None or not native_capture else
                             "只返回answer、quotes，可附带文本reasoning，不新增或改写用户问题。") if separated else
                            "保持指导员分配的问题原文，返回完整问题和答案；推理字段须为文本。")
                continue
            try:
                reasoning = select_candidate_reasoning(candidate if native_capture else dict(candidate),
                                                       styled=style is not None)
            except ValueError:
                feedback = "推理字段必须是文本；不能把结构化对象当作推理。"
                continue
            quotes = candidate.get("quotes")
            evidence_text = unit.get("text", "")
            if unit.get("source_context_pack"):
                from lib.infrastructure.workflow_source_context import pack_evidence_text
                evidence_text = (directed_evidence_text(self.path, unit, contract)
                    if context.get("source_line_scope") == "selected" else pack_evidence_text(unit, self.path))
            if (not isinstance(quotes, list) or len(quotes) > 32
                    or any(not isinstance(q, str) or not q.strip() for q in quotes)
                    or any(q not in evidence_text for q in quotes)
                    or (selected_blocks is not None and any(not any(q in block for block in selected_blocks) for q in quotes))
                    or (unit["kind"] == "document" and contract["answer_policy"] in {"answer", "correct_premise"}
                        and not quotes)):
                feedback = "证据必须是教师来源中的逐字片段；不能把历史答案当作事实依据。"
                continue
            assistant_message = {"role": "assistant", "content": candidate["answer"]}
            if reasoning["content"]:
                assistant_message["reasoning_content"] = reasoning["content"]
            messages = [{"role": "user", "content": learner_prompt(contract)}, assistant_message]
            issue = conversation_issue(messages)
            if issue:
                feedback = issue
                continue
            if self.recipe.get("review_repair") is not None:
                return [{"id": unit["id"], "source_id": unit["source_id"], "source_name": unit.get("source_name"),
                         "location": unit.get("location"), "source_location": unit.get("source_location"),
                         "status": "eligible", "messages": messages, "tools": [], "quotes": quotes,
                         "source_context": unit, "qa_contract": contract, "family_id": unit["family_id"],
                         "parent_id": unit.get("parent_id"), "generation_check": "structure_and_source_quotes",
                         "repair_attempts": attempt, **reasoning_provenance(reasoning),
                         "evidence_level": evidence_level,
                         **({"generation_style": style} if style else {})}]
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
                         **reasoning_provenance(reasoning),
                         "evidence_level": evidence_level,
                         **({"generation_style": style, "style_check": style_check} if style else {})}]
            feedback = {"quality": check["reason"], "contract": contract_check["reason"],
                        "style": style_check["reason"] if style_check else None}
            failure_kind = "quality"
        return [{**self.rejected(unit, "directed_sft_quality_failed_after_repair"),
                 **({"messages": messages, "quotes": quotes} if self.recipe.get("review_repair") is not None
                     and "messages" in locals() else {}),
                 "qa_contract": contract, "family_id": unit["family_id"],
                 "parent_id": unit.get("parent_id"), "feedback": feedback,
                 **({"generation_failure_kind": failure_kind} if separated else {}),
                 "judge": check, "qa_contract_check": contract_check,
                 **({"generation_style": style, "style_check": style_check} if style else {})}]

    def _director_batch_plan(self, batch, offset, config, local_history, published_history, stages):
        """Persist inputs *before* calling so resume cannot observe changed history."""
        self.activate_directed_stage("director")
        guided = [unit for unit in batch if unit["kind"] != "conversation"]
        if not guided:
            return batch
        for unit in guided:
            self._require_clean_director_evidence(unit)
        runtime = (self.prepare_knowledge_agent(guided, published_history, stages)
                   if self.recipe.get("knowledge_agent_context_version") == 1 else None)
        key = ["director_batch", offset, _digest(guided)]
        adaptive = config.get("planning_mode", "balanced") == "adaptive"
        human = config.get("human_augmentation", {})
        design_offset = offset + (self.state.get("production", {}).get("attempted", 0)
                                  if human.get("enabled") and self.production_enabled() else 0)
        assigned = ((None,) * len(guided) if adaptive else
                    ("closed_book",) * len(guided) if config.get("learning_goal") == "knowledge_application" else
                    allocate_qa_types(len(guided), config["type_weights"], offset=offset))
        # Language assignments are checkpointed with the plan, not inferred
        # from text or translated after generation. Production attempts carry
        # the ordinal across rounds; omitted controls retain the old schedule.
        language_offset = offset + (self.state.get("production", {}).get("attempted", 0)
                                   if "user_languages" in config else 0)
        def snapshot():
            limit = config["history_limit"]
            history = []
            query = " ".join(unit.get("text", "")[:1000] for unit in guided[:4])
            if limit and runtime:
                history = self._knowledge_search(runtime[1]["knowledge_id"], query=query[:4000], limit=limit)
            elif limit:
                def history_candidates():
                    for store in (local_history, published_history):
                        for stage in stages:
                            yield from store.similar(query, limit=limit, namespace=stage,
                                **({"include_expression": True} if self.natural_expressions() else {}))
                    # Source wording can differ from the actual dialogue's
                    # language, register or goal. Empty lexical recall must
                    # not erase the immediately preceding accepted work.
                    for store in (local_history, published_history):
                        for stage in stages:
                            yield from store.recent(limit=limit, namespace=stage,
                                **({"include_expression": True} if self.natural_expressions() else {}))
                seen, examples = set(), []
                for example in history_candidates():
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
                         "teacher_evidence": (unit.get("text", "") if unit.get("source_context_pack")
                                              or self.recipe.get("qa_agent_memory_version") == 1
                                              else unit.get("text", "")[:text_limit]),
                         **({"source_context_pack": deepcopy(unit["source_context_pack"])}
                            if unit.get("source_context_pack") else {}),
                         **({"source_line_material_id": unit["source_line_material_id"]}
                            if unit.get("source_line_material_id") else {}),
                         "generation_variant": unit.get("generation_variant"),
                         "assigned_type": qa_type,
                         "source_is_synthetic": unit["kind"] == "brief",
                         **({"evidence_origin": "qualified_cleaned_source",
                             "source_cleaning": deepcopy(unit["source_cleaning"])}
                            if unit.get("source_cleaning") else {}),
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
                    "targets": list(stages), **self._natural_expression_guidance()}
            if any(name in config for name in ("learning_goal", "user_languages")):
                for index, candidate in enumerate(data["candidates"]):
                    controls = _candidate_controls(config, language_offset + index, candidate.get("human_design"))
                    candidate.update(controls)
                    if controls.get("learning_goal") == "knowledge_application":
                        candidate["assigned_type"] = "closed_book"
                    elif controls.get("learning_goal") == "document_reading" and not adaptive:
                        weights = {kind: weight if kind != "closed_book" else 0
                                   for kind, weight in config["type_weights"].items()}
                        if not sum(weights.values()):
                            raise ValueError("qa_director_learning_goal_weights_conflict")
                        candidate["assigned_type"] = allocate_qa_types(1, weights, offset=design_offset + index)[0]
                data["assigned_types"] = [candidate["assigned_type"] for candidate in data["candidates"]]
                data.update({name: deepcopy(config[name]) for name in ("learning_goal", "user_languages") if name in config})
                data["automatic_controls_preserve_fixed_human_input"] = True
            if runtime:
                data["knowledge_agent_memory"] = self._knowledge_memory(runtime[1]["knowledge_id"])
            elif self.recipe.get("qa_agent_memory_version") == 1:
                from lib.infrastructure.qa_agent_memory import build_agent_memory
                data["agent_memory"] = build_agent_memory(local_history, published_history, stages,
                    history=history, feedback=data["feedback"], candidates=data["candidates"],
                    include_expression=self.natural_expressions(),
                    **({"expression_version": 2} if self.participant_utterances() else {}))
            elif self.natural_expressions():
                # Explicit opt-in without agent memory still uses indexed,
                # bounded accepted coverage rather than scanning old bodies.
                data["expression_coverage"] = {label: {stage: store.dimension_coverage(
                    namespace=stage, limit=8, include_expression=True,
                    **({"expression_version": 2} if self.participant_utterances() else {})) for stage in stages}
                    for label, store in (("current_run", local_history), ("published", published_history))}
            if self.recipe.get("source_line_protocol_version") == 1:
                data["source_line_protocol_version"] = 1
            if self.separated_questions():
                data["question_separation"] = True
            if adaptive:
                data.update(request_phase="batch_design", planning_mode="adaptive",
                    evidence_modes=(list(learning_goal_types(config["learning_goal"]))
                                    if "learning_goal" in config and not any(candidate.get("preserve_human_input")
                                        for candidate in data["candidates"]) else list(config["type_weights"])),
                    type_weights_are_not_quotas=True)
            binding = self.recipe.get("node_models", {}).get("director", {}).get("generation", {})
            context_limit = binding.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
            output_limit = binding.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
            from lib.infrastructure.workflow_agent_experience import experience_system
            experience = experience_system(self.recipe, "workflow.qa_director")
            system = self.recipe["node_prompt_system"] + "\n" + self.prompt_text("workflow.qa_director") + experience
            available = context_limit - output_limit - len(system.encode("utf-8")) - 512
            if self.recipe.get("source_context_pack_version") == 1 and any(
                    candidate.get("source_context_pack") for candidate in data["candidates"]):
                from lib.infrastructure.model_context_prefix import (
                    serialize_context_request, split_context_request, NODE_RULE_HEADER,
                )
                instruction = None
                if human.get("enabled"):
                    instruction = HUMAN_DIRECTOR_INSTRUCTION + (
                        HUMAN_REVISION_INSTRUCTION if any("revision_context" in seed for seed in human["seeds"]) else "")
                if self.recipe.get("source_memory_protocol_version") in {1, 2}:
                    from lib.infrastructure.source_memory_messages import source_memory_messages
                    # Match ask() and ChatClient's real text preflight. Tool
                    # receipts, arguments and the source-memory header all
                    # consume context; a JSON-pack estimate omits those bytes.
                    memory_system = (self.recipe["node_prompt_system"] + NODE_RULE_HEADER
                                     + "\n" + self.prompt_text("workflow.qa_director") + experience)
                    available = context_limit - output_limit - 2048 - 512
                    def request_size():
                        messages = source_memory_messages(memory_system,
                            split_context_request(self.path, data, instruction=instruction,
                                protocol_version=self.recipe["source_memory_protocol_version"]))
                        size = sum(len(message.get("content", "").encode("utf-8")) for message in messages)
                        size += sum(len(json.dumps(message["tool_calls"], ensure_ascii=False).encode("utf-8"))
                                    for message in messages if message.get("tool_calls"))
                        return size + (1024 if any(message.get("role") == "tool" for message in messages) else 0)
                else:
                    # Preserve immutable requests from before source memory.
                    available -= 2048 + len(NODE_RULE_HEADER.encode("utf-8"))
                    def request_size():
                        return len(serialize_context_request(self.path, data, instruction=instruction).encode("utf-8"))
                while data["history"] and request_size() > available:
                    data["history"].pop()
                if data.get("agent_memory"):
                    from lib.infrastructure.qa_agent_memory import compact_agent_memory
                    while request_size() > available and compact_agent_memory(data["agent_memory"]):
                        pass
                if self.natural_expressions() and data.get("natural_expression_guidance"):
                    from lib.domain.dialogue_expression import compact_expression_guide
                    while request_size() > available and compact_expression_guide(data["natural_expression_guidance"]):
                        pass
                if request_size() > available:
                    if len(batch) > 1:
                        raise DirectorBatchTooLarge("qa_director_batch_context_too_large")
                    raise ValueError("qa_director_context_too_small")
                return data
            if self.recipe.get("qa_agent_memory_version") == 1:
                from lib.infrastructure.qa_agent_memory import compact_agent_memory
                while data["history"] and len(canonical(data).encode("utf-8")) > available:
                    data["history"].pop()
                while len(canonical(data).encode("utf-8")) > available and compact_agent_memory(data["agent_memory"]):
                    pass
                if self.natural_expressions() and data.get("natural_expression_guidance"):
                    from lib.domain.dialogue_expression import compact_expression_guide
                    while (len(canonical(data).encode("utf-8")) > available
                           and compact_expression_guide(data["natural_expression_guidance"])):
                        pass
                if len(canonical(data).encode("utf-8")) > available:
                    if len(batch) > 1:
                        raise DirectorBatchTooLarge("qa_director_batch_context_too_large")
                    raise ValueError("qa_director_context_too_small")
                return data
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
        inputs = None
        def plan():
            production = adaptive or (self.recipe.get("version", 0) >= 12 and self.recipe.get("production") is not None)
            repair_feedback = None
            for attempt in range(3 if production else 1):
                call_key = [*key, "plan"] if attempt == 0 else [*key, "plan", attempt]
                data = inputs if repair_feedback is None else {**inputs, "plan_repair": repair_feedback}
                try:
                    options = ({"instruction": HUMAN_DIRECTOR_INSTRUCTION +
                               (HUMAN_REVISION_INSTRUCTION if any("revision_context" in seed for seed in human["seeds"]) else "")}
                               if human.get("enabled") else {})
                    response = (self.knowledge_agent_plan(call_key, data, runtime, **options) if runtime else
                                self.ask(call_key, "generation", "workflow.qa_director", data,
                                         allow_reasoning_fallback=False, **options))
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
                        if (self.recipe.get("source_line_protocol_version") == 1
                                and candidate.get("kind") == "document"
                                and candidate.get("source_context_pack")
                                and "source_line_ranges" not in task
                                and "workflow.qa_director" not in self.recipe.get("node_prompts", {}).get("director", {})):
                            # New default planners must actually select the
                            # blocks that their SFT workers will receive.
                            # Literal custom prompts retain their old contract.
                            raise ValueError("source_line_ranges_required")
                        if candidate.get("preserve_human_input"):
                            # A model's label cannot change or translate fixed
                            # human input. Keep its existing visibility choice.
                            task = {name: value for name, value in task.items()
                                    if name not in {"learning_goal", "user_language"}}
                            if isinstance(task.get("question_brief"), dict):
                                task["question_brief"] = {name: value for name, value in task["question_brief"].items()
                                                          if name != "user_language"}
                        controls = _task_controls(candidate)
                        contracts[candidate["id"]] = validate_director_task(
                            task, expected_type=candidate["assigned_type"],
                            source_text=self._director_evidence_text(candidate),
                            expected_id=candidate["id"],
                            source_materials=self._director_evidence_materials(candidate), **controls)
                        if (self.separated_questions() and task["qa_type"] == "closed_book"
                                and not candidate.get("preserve_human_input")):
                            self._question_brief_boundary(candidate, contracts[candidate["id"]], task.get("question_brief"))
                        if candidate.get("human_design"):
                            contracts[candidate["id"]]["human_design"] = deepcopy(candidate["human_design"])
                    if runtime:
                        self.commit_knowledge_plan(call_key)
                    return contracts
                except ValueError as error:
                    if runtime and (self.recipe.get("knowledge_agent_finalization_version") == 1
                                    or self.recipe.get("qa_dialogue_agent_version") == 1):
                        self.invalidate_knowledge_plan(call_key)
                    else:
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
                    if self.recipe.get("qa_agent_memory_version") == 1:
                        validation_code = _plan_validation_code(error)
                        # Older templates did not identify this code. Keep
                        # their immutable repair/checkpoint input unchanged.
                        if validation_code == "invalid_dialogue_design" and not _typed_director_repair(self.recipe):
                            validation_code = "qa_director_contract_validation_failed"
                        repair_feedback["validation_code"] = validation_code
                    if str(error) == "invalid_dialogue_design" and _typed_director_repair(self.recipe):
                        repair_feedback["instruction"] += _DIALOGUE_DESIGN_REPAIR
                    if self.recipe.get("source_line_protocol_version") == 1:
                        repair_feedback["instruction"] += (
                            "编号资料的 source_line_ranges 只能引用该包 material_id 和实际1-based行号；"
                            "范围包含完整事实及条件，不把辅助行号写入问题、答案或逐字引用。")
                    if self.separated_questions():
                        repair_feedback["instruction"] += (
                            "closed_book须提供question_brief宽泛主题与交往目标，不含原文、摘要、参考问答或标识。")
                    if any(name in config for name in ("learning_goal", "user_languages")):
                        repair_feedback["instruction"] += (
                            "逐候选遵守assigned_type、learning_goal和user_language；knowledge_application只能closed_book且visible_context为空，"
                            "可直接应用私有教师知识回答，不把无线索等同于证据不足；document_reading保留用户可见原文且不能closed_book。"
                            "同一对话保持指定用户语言，不自动翻译或改写preserve_human_input=true的人工问题与上下文。")
                    self.event("director_plan_repair", attempt=attempt + 1,
                        **({"validation_code": repair_feedback["validation_code"]}
                           if "validation_code" in repair_feedback else {}),
                               reason="qa_director_contract_validation_failed")
            raise DirectorPlanError()
        try:
            # A second job on the same knowledge must observe the first
            # completed turn before freezing its request. Other agents remain
            # independent and model admission still controls actual API calls.
            with self.knowledge_planning_lock(runtime) if runtime else nullcontext():
                inputs = self.checkpoint([*key, "inputs"], snapshot)
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
        preserved_human = {candidate["id"] for candidate in inputs["candidates"]
                           if candidate.get("preserve_human_input")}
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
            if (self.separated_questions() and contract["qa_type"] == "closed_book"
                    and unit["id"] not in preserved_human):
                try:
                    contract = self.checkpoint([*key, "question_dispatch", unit["id"], _digest(contract)],
                        lambda: self._closed_book_question(unit, contract, key=[*key, unit["id"]]))
                except ValueError as error:
                    if str(error) not in {"invalid_question_brief", "question_brief_contains_private_source",
                            "invalid_question_output", "invalid_question_assessment", "question_not_suitable",
                            "invalid_question_brief_assessment"}:
                        raise
                    result.append({**unit, "director_plan_failure": str(error),
                        "generation_failure_kind": "quality" if str(error) == "question_not_suitable" else "invalid_output"})
                    self.state["qa_director"]["feedback"][str(error)] = self.state["qa_director"]["feedback"].get(str(error), 0) + 1
                    continue
            result.append({**unit, "qa_contract": contract,
                           "family_id": _digest([unit["source_id"], contract["question"]]),
                           "parent_id": unit.get("generation_variant", {}).get("source_unit_id", unit["id"]),
                           "director_batch": offset // config["batch_size"]})
            self.state["qa_director"]["coverage"][contract["qa_type"]]["planned"] += 1
            self.state["qa_director"]["coverage"][contract["qa_type"]]["assigned"] += len(stages)
            if contract.get("user_language"):
                language_coverage = self.state["qa_director"].setdefault("user_language_coverage", {})
                counts = language_coverage.setdefault(contract["user_language"], {"planned": 0, "assigned": 0, "accepted": 0, "rejected": 0})
                counts["planned"] += 1
                counts["assigned"] += len(stages)
            if unit["id"] in preserved_human:
                self.event("human_learning_controls_preserved", unit_id=unit["id"], automatic_controls_applied=False)
        metrics = self.state["stages"]["director"]
        metrics["done"] += len(guided)
        metrics["outputs"] += len(guided)
        skipped = sum(bool(unit.get("director_skip_reason") or unit.get("director_plan_failure")) for unit in result)
        metrics["eligible"] += len(guided) - skipped
        metrics["quarantined"] += skipped
        metrics["batches_done"] += 1
        self.state["qa_director"]["batches_planned"] += 1
        self.save()
        return result

    def _director_evidence_text(self, candidate):
        if self.recipe.get("source_context_pack_version") == 1 and candidate.get("source_context_pack"):
            from lib.infrastructure.workflow_source_context import pack_evidence_text
            return pack_evidence_text(candidate, self.path)
        return candidate["teacher_evidence"]

    def _director_evidence_materials(self, candidate):
        if (self.recipe.get("source_line_protocol_version") == 1
                and candidate.get("source_context_pack")):
            from lib.infrastructure.workflow_source_context import load_context_pack
            return load_context_pack(self.path, candidate["source_context_pack"])["materials"]
        return None

    def _directed_worker_batch(self, stage, batch, offset, handle, local_history, published_history):
        """Workers may finish out of order; acceptance and dedup commit in order."""
        self.activate_directed_stage(stage)
        self._abort.clear()
        metrics = self.state["stages"][stage]
        self.event("stage_batch_started", batch=metrics["batches_done"] + 1)
        action = getattr(self, stage)
        registered, duplicates = {}, {}
        for index, unit in enumerate(batch):
            if not unit.get("qa_contract") or self.qa_actual_only(stage):
                continue
            c = unit["qa_contract"]
            identity = (learner_task_identity(c["question"], c["visible_context"]) if self.qa_core_dedup(stage)
                        else contract_identity(c["question"], c["visible_context"], c["answer_policy"], c["qa_type"],
                                               dialogue_design=self.qa_identity_design(c)))
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
                    return [{**self.rejected(unit, unit["director_plan_failure"]),
                             **({"generation_failure_kind": unit["generation_failure_kind"]}
                                if unit.get("generation_failure_kind") else {})}]
                if unit.get("director_skip_reason"):
                    return [{**self.rejected(unit, unit["director_skip_reason"]),
                             "director_skip_guidance": unit.get("director_skip_guidance", "")}]
                if self.recipe.get("knowledge_agent_context_version") == 1:
                    duplicate = self.checkpoint(["knowledge_agent_claim", stage, index, _digest(unit)],
                                               lambda: self.claim_knowledge_task(unit, stage))
                    if duplicate:
                        return [{**self.rejected(unit, "duplicate_knowledge_agent_task"),
                                 "qa_contract": unit["qa_contract"], "duplicate_of": duplicate["id"]}]
                if unit.get("qa_contract") and not self.qa_actual_only(stage):
                    c = unit["qa_contract"]
                    def dispatch():
                        if index - offset in duplicates:
                            return {"reason": "duplicate_qa_contract_in_batch", "duplicate_of": duplicates[index - offset]}
                        duplicate = local_history.duplicate(c["question"], visible_context=c["visible_context"],
                            answer_policy=c["answer_policy"], qa_type=c["qa_type"], namespace=stage,
                            dialogue_design=self.qa_identity_design(c),
                            learner_task_dedup=self.qa_core_dedup(stage),
                            exclude_id=self.state["id"] + ":" + unit["id"])
                        if duplicate:
                            return {"reason": "duplicate_qa_contract", "duplicate_of": duplicate["id"]}
                        duplicate = published_history.duplicate(c["question"], visible_context=c["visible_context"],
                            answer_policy=c["answer_policy"], qa_type=c["qa_type"], namespace=stage,
                            dialogue_design=self.qa_identity_design(c),
                            learner_task_dedup=self.qa_core_dedup(stage))
                        if (duplicate and duplicate.get("run_id") != self.state["id"]
                                and not self.human_revision_duplicate_allowed(c, duplicate)):
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
                historical = self.qa_history_row(row, stage)
                if historical is not None and not local_history.add(historical, namespace=stage,
                                                                  learner_task_dedup=self.qa_core_dedup(stage)):
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
                    if language := row["qa_contract"].get("user_language"):
                        counts = self.state["qa_director"].setdefault("user_language_coverage", {}).setdefault(
                            language, {"planned": 0, "assigned": 0, "accepted": 0, "rejected": 0})
                        counts["accepted" if row["status"] == "eligible" else "rejected"] += 1
                if self.recipe.get("knowledge_agent_context_version") == 1:
                    self.record_knowledge_outcome(unit, row, stage)
                handle.write(canonical(row) + "\n")
            metrics["done"] += 1
        handle.flush()
        metrics["batches_done"] += 1
        self.save()

    def directed_generation(self, units, *, needs_sft, needs_multiturn):
        config = validate_qa_director(self.recipe.get("qa_director"))
        if human := config.get("human_augmentation"):
            binding = self.recipe.get("node_models", {}).get("director", {}).get("generation", {})
            from lib.infrastructure.workflow_agent_experience import experience_system
            system = (self.recipe["node_prompt_system"] + "\n" + self.prompt_text("workflow.qa_director", stage="director")
                      + experience_system(self.recipe, "workflow.qa_director"))
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
            self._require_clean_director_evidence(unit)
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
            **({"agent_memory": "bounded_deterministic_ledger_projection",
                "deduplication": "exact_learner_task_for_sft_contract_for_multiturn"}
               if self.recipe.get("qa_agent_memory_version") == 1 else {}),
            **({"evidence_source": "qualified_cleaning"} if self.shared_source_cleaning() else {}),
            **({"question_separation": True,
                "question_dispatch": "source_free_questioner_for_closed_book_otherwise_director",
                "answer_dispatch": "source_memory_tool_and_actual_dialogue_roles"}
               if self.separated_questions() else {}),
        }
        if any(key in config for key in ("learning_goal", "user_languages")):
            self.state["qa_director"].update({key: deepcopy(config[key])
                for key in ("learning_goal", "user_languages") if key in config})
            self.state["qa_director"]["coverage_scope"] = "current_directed_generation_actual_contracts"
            if "user_languages" in config:
                self.state["qa_director"]["user_language_basis"] = "scheduled_request_semantically_reviewed_not_text_inference"
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
                pending_batches = deque()
                while True:
                    if not pending_batches:
                        batch = list(islice(iterator, config["batch_size"]))
                        if not batch:
                            break
                        pending_batches.append(batch)
                    batch = pending_batches.popleft()
                    self.check_cancel()
                    try:
                        planned = self._director_batch_plan(batch, offset, config, local_history, published_history, stages)
                    except DirectorBatchTooLarge:
                        midpoint = len(batch) // 2
                        first, second = batch[:midpoint], batch[midpoint:]
                        pending_batches.appendleft(second)
                        pending_batches.appendleft(first)
                        guided_parts = sum(any(unit["kind"] != "conversation" for unit in part)
                                           for part in (first, second))
                        self.state["stages"]["director"]["batches_total"] += guided_parts - 1
                        for stage in stages:
                            self.state["stages"][stage]["batches_total"] += 1
                        self.event("director_batch_split", source_count=len(batch),
                                   split_sizes=[len(first), len(second)], reason="model_context_window")
                        continue
                    for unit in planned:
                        plans.write(canonical({"id": unit["id"], "source_id": unit["source_id"],
                            "status": "quarantined" if unit.get("director_plan_failure") or unit.get("director_skip_reason") else "ready",
                            **({"reason": unit["director_plan_failure"]} if unit.get("director_plan_failure") else {}),
                            **({"reason": unit["director_skip_reason"],
                                "guidance": unit.get("director_skip_guidance", "")} if unit.get("director_skip_reason") else {}),
                            "qa_contract": unit.get("qa_contract"),
                            "family_id": unit.get("family_id"), "director_batch": unit.get("director_batch"),
                            **({"source_cleaning": unit["source_cleaning"]} if unit.get("source_cleaning") else {}),
                            **({"source_context_pack": unit["source_context_pack"]} if unit.get("source_context_pack") else {}),
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
        if not self.recipe.get("qa_director", {}).get("enabled") and not self.recipe.get("repair_inputs"):
            return
        with QAHistory(self.path.parent.parent / "qa-history.sqlite3") as history:
            for target in ("sft", "multiturn", "dpo", "orpo", "rlaif", "cot"):
                path = destination / f"{target}.records.json"
                if not path.exists():
                    continue
                from lib.infrastructure.json_stream import iter_json_records
                for row in iter_json_records(path):
                    self.check_cancel()
                    if self.recipe.get("knowledge_agent_context_version") == 1:
                        self.record_knowledge_outcome(row, row, target, final=True)
                    record = self.qa_history_row(row, target)
                    if record is not None:
                        namespace = "multiturn" if target == "multiturn" else "sft"
                        inserted = history.add(record, namespace=namespace, learner_task_dedup=self.qa_core_dedup(namespace))
                        if not inserted:
                            duplicate = history.duplicate_actual(record.get("actual_input_sha256"), namespace=namespace)
                            if duplicate is None:
                                contract = row["qa_contract"]
                                duplicate = history.duplicate(contract["question"], visible_context=contract["visible_context"],
                                    answer_policy=contract["answer_policy"], qa_type=contract["qa_type"], namespace=namespace,
                                    dialogue_design=self.qa_identity_design(contract), learner_task_dedup=self.qa_core_dedup(namespace))
                            if not (duplicate and self.human_revision_duplicate_allowed(row["qa_contract"], duplicate)):
                                raise ValueError("qa_publication_history_conflict")
