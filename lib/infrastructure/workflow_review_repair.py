"""Checkpointed scoring and correction in one dedicated workflow node.

Correction consumes a selected candidate as editable material, never as a new
SFT seed. All model calls use Workflow.ask, so shared scheduling, token limits,
streaming, cancellation and request checkpoints remain the ordinary ones.
"""
from __future__ import annotations

from copy import deepcopy

from lib.domain.review_repair import validate_review_repair, validate_repair_inputs
from lib.domain.workflow_quality import accepted, canonical, conversation_issue, text_issue, verdict
from lib.domain.workflow_targets import (TARGETS, TRAINING_FIELDS, PREFERENCE_TARGETS, training_record,
                                         preference_score_issue, rlaif_feedback_issue)
from lib.domain.math_tasks import validate_math_candidate
from lib.domain.source_quote import quote_spans
from lib.domain.corpus_quality import inspect_corpus
from lib.domain.workflow_reasoning_route import cot_updates_sft, finalized_cot_sft_row
from lib.infrastructure.workflow_rows import RowSpool, WorkflowRows, write_json_array
from lib.infrastructure.human_revision import verified_revision_parent, revision_evidence


def _payload_issue(target, row):
    if target == "gsm8k" and not validate_math_candidate(row):
        return "gsm8k_arithmetic_verification_failed"
    if target == "cpt":
        quality = inspect_corpus(row.get("text"))
        return None if quality["keep"] else quality["reason"]
    if target in PREFERENCE_TARGETS:
        prompt, chosen, rejected = row.get("prompt"), row.get("chosen"), row.get("rejected")
        if not all(isinstance(value, list) for value in (prompt, chosen, rejected)):
            return "review_repair_invalid_preference"
        return conversation_issue([*prompt, *chosen]) or conversation_issue([*prompt, *rejected])
    try:
        payload = training_record(target, row)
    except (KeyError, TypeError, ValueError):
        return "review_repair_content_unavailable"
    if target in {"sft", "multiturn", "agent"}:
        return conversation_issue(payload["messages"])
    if target == "cot":
        question = row.get("question")
        if not isinstance(question, (str, list)) or not question:
            return "review_repair_invalid_question"
        if isinstance(question, list) and (issue := conversation_issue(question, final=False)):
            return issue
        reasoning = row.get("reasoning")
        if isinstance(reasoning, list):
            reasoning = "\n".join(reasoning) if all(isinstance(part, str) for part in reasoning) else None
        return text_issue(row.get("answer")) or text_issue(reasoning)
    return next((text_issue(payload[field]) for field in TRAINING_FIELDS[target] if text_issue(payload[field])), None)


def _sync_cot_messages(row):
    reasoning = row.get("reasoning")
    if isinstance(reasoning, str):
        row["reasoning"] = [reasoning]
    question = row.get("question")
    if isinstance(question, (str, list)) and isinstance(row.get("answer"), str):
        prefix = question if isinstance(question, list) else [{"role": "user", "content": question}]
        values = row.get("reasoning")
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            row["messages"] = [*deepcopy(prefix), {"role": "assistant", "content": row["answer"],
                                                  "reasoning_content": "\n".join(values)}]
    return row


def _manual_patch(target, record, item):
    row = deepcopy(record)
    patch = item.get("corrected_record", {})
    if set(patch) - set(TRAINING_FIELDS[target]):
        raise ValueError("manual_review_patch_target_mismatch")
    row.update(deepcopy(patch))
    if target == "rlaif" and "responses" in patch:
        responses = patch["responses"]
        if (not isinstance(responses, list) or len(responses) != 2
                or any(not isinstance(response, dict) or not isinstance(response.get("response"), list) for response in responses)):
            raise ValueError("manual_review_patch_target_mismatch")
        ranked = sorted(responses, key=lambda response: response.get("preference_rank", 99))
        row["chosen"], row["rejected"] = deepcopy(ranked[0]["response"]), deepcopy(ranked[1]["response"])
        row.pop("responses", None)
        row.pop("criterion", None)
    if item.get("messages") is not None:
        if target not in {"sft", "multiturn"}:
            raise ValueError("manual_review_messages_target_mismatch")
        row["messages"] = deepcopy(item["messages"])
    if target in {"sft", "multiturn"}:
        messages = row.get("messages", [])
        if item.get("question"):
            first = next((message for message in messages if message.get("role") == "user"), None)
            if first is not None:
                first["content"] = item["question"]
        if item.get("answer"):
            last = next((message for message in reversed(messages) if message.get("role") == "assistant"), None)
            if last is not None:
                last["content"] = item["answer"]
    elif target == "cot":
        if item.get("question"):
            row["question"] = item["question"]
        if item.get("answer"):
            row["answer"] = item["answer"]
    elif item.get("question") or item.get("answer"):
        raise ValueError("manual_review_patch_target_mismatch")
    return _sync_cot_messages(row) if target == "cot" else row


def _immutable_tool_facts(row):
    return {"tools": row.get("tools", []), **{field: [message for message in row.get(field, [])
        if message.get("role") in {"system", "tool"} or message.get("tool_calls") or message.get("toolCalls")]
        for field in ("messages", "prompt", "chosen", "rejected", "question")
        if isinstance(row.get(field, []), list)}}


class WorkflowReviewRepair:
    def _review_payload(self, target, row):
        from lib.infrastructure.training_workflow import preferred_training_record
        if target == "rlaif":
            payload = {key: row[key] for key in ("prompt", "chosen", "rejected")}
            if row.get("tools"):
                payload["tools"] = row["tools"]
            return payload
        return preferred_training_record(target, row, self.recipe.get("generation_preferences"),
                                         sft_output_style=self.recipe.get("sft_output_style"))

    def _review_evidence(self, row):
        context = row.get("revision_context")
        if context:
            return {key: context[key] for key in ("teacher_evidence", "source_id", "source_kind")}
        try:
            return revision_evidence(row, self.recipe)
        except ValueError:
            source = row.get("source_context") or {}
            text = source.get("text") or source.get("task") or self.recipe.get("brief", "")
            if not isinstance(text, str) or not text.strip():
                return None
            return {"teacher_evidence": text, "source_id": row.get("source_id"),
                    "source_kind": "document" if source.get("kind") == "document" else "synthetic"}

    def _review_score(self, target, row, evidence, key, instruction):
        self.state["stages"]["review"]["phase"] = "scoring"
        self.save(force=False)
        role = "jev" if self.recipe.get("package_review", {}).get("enabled") else "generation"
        data = {"target": target, "candidate": self._review_payload(target, row),
                "source_evidence": evidence, "instruction": instruction,
                "design_requirements": (row.get("revision_context") or {}).get("design_requirements", {}),
                "qa_contract": row.get("qa_contract"), "generation_style": row.get("generation_style")}
        def assessed(call_key, call_data):
            value = self.ask(call_key, role, "workflow.review_score", call_data, allow_reasoning_fallback=False)
            try:
                return verdict(value)
            except ValueError:
                self.invalidate_checkpoint(["call", call_key])
                raise
        check = assessed([*key, "score"], data)
        if target in PREFERENCE_TARGETS:
            # A repaired preference needs fresh evidence for both response scores;
            # inherited scores must never describe edited responses.
            rejected_check = assessed([*key, "rejected_score"],
                {**data, "target": "sft", "candidate": {"messages": [*row["prompt"], *row["rejected"]]},
                 "assessment": "Score this deliberately negative response accurately; it need not pass."})
            row["preference"] = {"dimension": "correctness", "minimum_gap": 2,
                                 "chosen": check, "rejected": rejected_check}
            if target == "rlaif":
                row["rlaif"] = {"criterion": "correctness", "chosen_feedback": check["reason"],
                    "rejected_feedback": rejected_check["reason"], "judge": "jev",
                    "judge_role": role, "review_node": "review",
                    "chosen_dimensions": check["scores"], "rejected_dimensions": rejected_check["scores"]}
        return check

    def _repair_content(self, target, row, evidence, key, check, instruction):
        self.state["stages"]["review"]["phase"] = "repairing"
        self.save(force=False)
        from lib.infrastructure.training_workflow import digest
        payload = self._review_payload(target, row)
        value = self.ask([*key, "repair"], "generation", "workflow.review_repair",
            {"target": target, "candidate": payload, "source_evidence": evidence,
             "review_feedback": check, "instruction": instruction,
             "design_requirements": (row.get("revision_context") or {}).get("design_requirements", {}),
             "generation_style": row.get("generation_style")}, allow_reasoning_fallback=False)
        if (not isinstance(value, dict) or type(value.get("uncertain", False)) is not bool
                or value.get("uncertain") or not isinstance(value.get("record"), dict)):
            return row, "review_repair_content_unavailable"
        candidate = value["record"]
        if set(candidate) != set(payload):
            return row, "review_repair_invalid_schema"
        updated = {**deepcopy(row), **deepcopy(candidate)}
        if target == "cot":
            _sync_cot_messages(updated)
        if canonical(_immutable_tool_facts(updated)) != canonical(_immutable_tool_facts(row)):
            return row, "review_repair_tool_facts_changed"
        if issue := _payload_issue(target, updated):
            return row, issue
        if evidence["source_kind"] == "document":
            spans = quote_spans(evidence["teacher_evidence"], value.get("quotes", []))
            if spans is None:
                return row, "review_repair_source_quotes_missing"
            updated["quotes"], updated["quote_spans"] = value["quotes"], spans
        updated["repair_parent_sha256"] = digest(row)
        return updated, None

    def _review_repair_item(self, item):
        from lib.infrastructure.training_workflow import digest
        target, original = item["target"], item["record"]
        config = validate_review_repair(self.recipe["review_repair"])
        row = _manual_patch(target, original, item)
        if item.get("revision_context"):
            row["revision_context"] = deepcopy(item["revision_context"])
            row["id"] = digest([self.state["id"], target, item["revision_context"]["candidate_id"]])
            if row.get("qa_contract"):
                row["qa_contract"] = deepcopy(row["qa_contract"])
                row["qa_contract"]["revision_context"] = deepcopy(item["revision_context"])
        instruction = item.get("instruction", "")
        receipt = {"mode": config["mode"], "status": "pending", "score": None,
                   "attempts": 0, "history": [], "node": "review", "score_threshold": config["score_threshold"]}
        row["review_repair"] = receipt
        evidence = self._review_evidence(row)
        issue = _payload_issue(target, row)
        if canonical(_immutable_tool_facts(row)) != canonical(_immutable_tool_facts(original)):
            issue = "review_repair_tool_facts_changed"
        # An explicit correction cannot rehabilitate forged trajectories or
        # missing facts by changing the text. These require new verified input.
        blocked = any(token in original.get("reason", "") for token in (
            "source_snapshot", "tool_error", "simulated_tool", "unverified", "trajectory", "orphan_tool", "missing_tool"))
        if issue or blocked or evidence is None:
            receipt.update(status="rejected", reason=issue or "review_repair_source_evidence_unavailable")
            row.update(status="quarantined", reason=receipt["reason"])
        elif config["mode"] == "human":
            if "score" not in item:
                receipt.update(status="waiting_manual_review", reason="manual_review_required")
                row.update(status="quarantined", reason="manual_review_required")
            else:
                score = item["score"]
                keep = item["approved"] and score >= config["score_threshold"]
                if target == "rlaif" and (rlaif_feedback_issue(row)
                        or canonical(self._review_payload(target, row)) != canonical(self._review_payload(target, original))):
                    keep = False
                    receipt["reason"] = "manual_rlaif_change_requires_ai_review"
                receipt.update(status="accepted" if keep else "rejected", score=score,
                    approved=item["approved"], reason=receipt.get("reason") or instruction or "manual_review_submitted")
                row.update(status="eligible" if keep else "quarantined")
                if keep:
                    row.pop("reason", None)
                else:
                    row["reason"] = receipt["reason"] if receipt["reason"] == "manual_rlaif_change_requires_ai_review" else "manual_review_rejected"
        else:
            check = None
            force_repair = bool(item.get("revision_context")) and config["max_rounds"] > 0
            for attempt in range(config["max_rounds"] if force_repair else config["max_rounds"] + 1):
                key = [row["id"], target, "review_repair", attempt]
                if force_repair or attempt:
                    row, issue = self._repair_content(target, row, evidence, key,
                        check or original.get("review_repair", {}).get("verdict") or original.get("judge")
                            or {"reason": instruction or original.get("reason", "")}, instruction)
                    receipt = row["review_repair"]
                    receipt["attempts"] += 1
                    if issue:
                        receipt["history"].append({"attempt": attempt, "repair_issue": issue})
                        check = {"reason": issue}
                        force_repair = False
                        continue
                check = self._review_score(target, row, evidence, key, instruction)
                score = min(check["scores"].values()) / 5
                keep = accepted(check) and score >= config["score_threshold"]
                if target in PREFERENCE_TARGETS and preference_score_issue(row):
                    keep = False
                receipt["history"].append({"attempt": attempt, "score": score,
                    "status": "accepted" if keep else "rejected", "verdict": deepcopy(check)})
                receipt.update(score=score, verdict=deepcopy(check), reason=check["reason"])
                force_repair = False
                if keep:
                    receipt["status"] = "accepted"
                    row.update(status="eligible", judge=deepcopy(check))
                    row.pop("reason", None)
                    break
            else:
                receipt["status"] = "rejected"
                row.update(status="quarantined", reason="review_repair_exhausted")
        # Binding the receipt to exactly the content that was assessed prevents
        # cached/manual approval from being reused after another content edit.
        try:
            receipt["candidate_sha256"] = digest(self._review_payload(target, row))
        except (ValueError, KeyError, TypeError):
            receipt["candidate_sha256"] = None
        receipt["config_sha256"] = digest(config)
        row["_review_target"] = target
        return [row]

    def review_repair_collections(self, collections):
        from lib.infrastructure.training_workflow import digest
        config = validate_review_repair(self.recipe.get("review_repair"))
        if config is None:
            return collections
        workflow = self
        linked = bool(cot_updates_sft(self.recipe) and len(collections.get("cot", []))
                      and not self.recipe.get("repair_inputs")
                      and not getattr(self, "_production_finalizing", False))
        skipped_sft = (sum(row.get("reasoning_route") == "sft_via_cot" for row in collections.get("sft", []))
                       if linked else 0)
        class Items:
            def __len__(self):
                return sum(len(collections.get(target, [])) for target in workflow.recipe["targets"]) - skipped_sft

            def __iter__(self):
                for target in workflow.recipe["targets"]:
                    for record in collections.get(target, []):
                        workflow.check_cancel()
                        if linked and target == "sft" and record.get("reasoning_route") == "sft_via_cot":
                            continue
                        yield {"target": target, "record": record}

        def process(item):
            receipt = item["record"].get("review_repair", {})
            try:
                valid = (receipt.get("status") in {"accepted", "rejected", "waiting_manual_review"}
                    and receipt.get("config_sha256") == digest(config)
                    and receipt.get("candidate_sha256") == digest(workflow._review_payload(item["target"], item["record"])))
            except (KeyError, TypeError, ValueError):
                valid = False
            return [{**item["record"], "_review_target": item["target"]}] if valid else workflow._review_repair_item(item)
        reviewed = self.stage_items("review", Items(), process)
        result = self._split_review_rows(reviewed, collections)
        if linked and skipped_sft:
            canonical, unlinked = iter(result["cot"]), iter(result["sft"])
            spool = RowSpool(self.path / "stage-results" / "review-sft-from-cot.jsonl")
            try:
                for original in collections["sft"]:
                    self.check_cancel()
                    derived = original.get("reasoning_route") == "sft_via_cot"
                    row = next(canonical if derived else unlinked, None)
                    if row is None or row.get("id") != original.get("id"):
                        raise ValueError("cot_sft_lineage_mismatch")
                    if derived:
                        row = finalized_cot_sft_row(row)
                        receipt = deepcopy(row.get("review_repair", {}))
                        receipt.update(derived_from={"target": "cot", "candidate_id": row["id"]},
                                       candidate_sha256=digest(self._review_payload("sft", row)))
                        row["review_repair"] = receipt
                    spool.append(row)
                if next(canonical, None) is not None or next(unlinked, None) is not None:
                    raise ValueError("cot_sft_lineage_mismatch")
            finally:
                spool.close()
                for iterator in (canonical, unlinked):
                    if callable(close := getattr(iterator, "close", None)):
                        close()
            result["sft"] = spool
        return result

    def _split_review_rows(self, reviewed, collections=None):
        result = dict(collections or {target: [] for target in TARGETS})
        spools = {target: RowSpool(self.path / "stage-results" / f"review-{target}.jsonl")
                  for target in self.recipe["targets"]}
        try:
            for original in reviewed:
                row = dict(original)
                target = row.pop("_review_target")
                spools[target].append(row)
        finally:
            for spool in spools.values():
                spool.close()
        result.update(spools)
        return result

    def execute_repair_branch(self):
        from lib.infrastructure.training_workflow import digest, now
        inputs = validate_repair_inputs(self.recipe["repair_inputs"], self.recipe["review_repair"])
        # Authenticate every selected immutable parent before any model request.
        for item in inputs:
            actual = verified_revision_parent(self.path.parent.parent, self.state["id"], item["revision_context"])
            if digest(actual) != digest(item["record"]):
                raise ValueError("human_session_result_changed")
        for stage, metrics in self.state["stages"].items():
            if stage not in {"review", "package"}:
                metrics.update(status="skipped", done=0, total=0)
        self.state["stages"]["ingest"].update(status="completed", phase="verified_repair_inputs",
            done=len(inputs), total=len(inputs), outputs=len(inputs), eligible=len(inputs))
        self.state["input_summary"] = {"units": len(inputs), "ready": len(inputs), "quarantined": 0,
            "skipped": 0, "deferred": 0, "generation_candidates": 0, "repair_candidates": len(inputs),
            "targets": list(self.recipe["targets"])}
        write_json_array(self.path / "input_records.json", [{"id": item["record"]["id"], "status": "ready",
            "source_id": item["revision_context"]["source_id"], "kind": "repair_candidate"} for item in inputs])
        rows = self.stage_items("review", inputs, self._review_repair_item)
        collections = self._split_review_rows(rows)
        self.stage = "package"
        self.state["stages"]["package"].update(status="running", done=0, total=1)
        self.save()
        self.package(collections)
        self.state["stages"]["package"].update(status="completed", phase="completed", done=1, total=1, finished_at=now())
        self.state["status"] = "completed" if all(value["eligible"] == value["total"]
            for value in self.state["quality"]["targets"].values()) else "needs_attention"
        self.event("run_finished")
        return self.state
