"""Checkpointed scoring and correction in one dedicated workflow node.

Correction consumes a selected candidate as editable material, never as a new
SFT seed. All model calls use Workflow.ask, so shared scheduling, token limits,
streaming, cancellation and request checkpoints remain the ordinary ones.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from ..domain.repair import validate_review_repair, validate_repair_inputs
from lib.domain.workflow_quality import accepted, canonical, conversation_issue, text_issue, verdict
from lib.contexts.workflows.contracts import (TARGETS, TRAINING_FIELDS, PREFERENCE_TARGETS, training_record,
                                         preference_score_issue, rlaif_feedback_issue)
from lib.domain.math_tasks import validate_math_candidate
from lib.domain.source_quote import quote_spans
from lib.domain.corpus_quality import inspect_corpus
from lib.domain.workflow_reasoning_route import cot_updates_sft, finalized_cot_sft_row
from lib.infrastructure.workflow_rows import RowSpool, WorkflowRows, write_json_array
from lib.infrastructure.human_revision import verified_revision_parent, verified_revision_batch, revision_evidence
from ..domain.reasoning_history import retire_reasoning_prefill
from lib.domain.authored_reasoning import AUTHORED_REASONING_VERSION
from lib.domain.training_surface import training_surface_issue
from lib.domain.review_context import project_review_request
from lib.prompts.workflow_reasoning import RATIONALE_CHECK_BODY


from ..domain.candidate_content import (
    _authored_cot,
    _authored_review_audit,
    _automatic_repair_task,
    _evidence_quote_spans,
    _immutable_tool_facts,
    _manual_patch,
    _payload_issue,
    _row_training_surface_issue,
    _selected_review_request,
    _sync_cot_messages,
    preserve_provider_reasoning
)

from ..domain.automatic_rules import (
    _AUTHORED_REPAIR_PROTOCOL_V2,
    _AUTHORED_REPAIR_RULES,
    _CONCEPT_TRANSLATION_REPAIR_V3,
    _CONCEPT_TRANSLATION_REVIEW_V3,
    _COT_DELTA_REPAIR_RULES,
    _CURRENT_STYLE_V2_RULE,
    _CURRENT_STYLE_V3_RULE,
    _TRANSLATION_REVIEW_V2,
    _authored_cot_language_v2,
    _authored_reasoning_language_issue,
    _authored_reasoning_language_repair_rules,
    _authored_reasoning_language_requirements,
    _authored_reasoning_language_review_rules,
    _authored_reasoning_warmup_rules,
    _authored_reasoning_warmup_scope,
    _authored_repair_rules,
    _authored_review_rules,
    _authored_warmup_requirements,
    _knowledge_application_review_rules,
    _knowledge_source_narration_reason
)

from .review_style import (
    _current_review_generation_style,
    _record_accepted_revision_style,
    _validate_reasoning_depth_input_control,
    current_claim_review_requirements
)

from .review_protocol import (
    _AUTHORED_REVIEW_RULES,
    _BLIND_REFERENCE_WIRE_PROTOCOL,
    _CLAIM_ADJUDICATION_V4_WIRE_PROTOCOL,
    _CLAIM_CHALLENGE_V4_WIRE_PROTOCOL,
    _CLAIM_FORMAT_ERROR_CODES,
    _CLAIM_REVIEW_V2_WIRE_PROTOCOL,
    _CLAIM_REVIEW_V3_WIRE_PROTOCOL,
    _CLAIM_REVIEW_WIRE_PROTOCOL,
    _CONDITION_ADJUDICATION_WIRE_PROTOCOL,
    _CONDITION_INVENTORY_WIRE_PROTOCOL,
    _claim_format_boundaries,
    _claim_format_quote_diagnostic,
    _safe_claim_format_code
)


class WorkflowReviewRepair:
    def independent_cot_assessment(self, row, *, human=False):
        """Describe a final assessment without promoting recorded source facts.

        Only the new independent-CoT preparation marker creates this level.
        Call this after an accepted final content assessment, never after its
        structural input check or the rationale-prefix-only check.
        """
        if (self.recipe.get("independent_cot_input_version") == 1
                and row.get("evidence_level") == "recorded_context_unassessed"):
            return {"evidence_level": ("recorded_context_human_assessed" if human
                                       else "recorded_context_model_assessed"),
                    "source_verification": "recorded_not_independently_verified"}
        return {}

    def _review_payload(self, target, row):
        from lib.contexts.datasets.contracts import preferred_training_record
        if target == "rlaif":
            payload = {key: row[key] for key in ("prompt", "chosen", "rejected")}
            if row.get("tools"):
                payload["tools"] = row["tools"]
            return payload
        return preferred_training_record(target, row, self.recipe.get("generation_preferences"),
                                         sft_output_style=self.recipe.get("sft_output_style"))

    def _review_evidence(self, row):
        from lib.infrastructure.workflow_repair_references import append_repair_reference_sources
        return append_repair_reference_sources(self, self._original_review_evidence(row))

    def _original_review_evidence(self, row):
        source_ids = self.recipe.get("source_memory_protocol_version") == 2
        selected = (self.recipe.get("source_line_protocol_version") == 1
                    and (row.get("qa_contract") or {}).get("source_line_ranges") is not None)
        context = row.get("revision_context")
        if context:
            if context.get("evidence_pack_sha256") is not None:
                return revision_evidence(row, self.recipe, self.path, with_blocks=True, include_source_ids=source_ids)
            if selected:
                raise ValueError("human_session_source_evidence_unavailable")
            return {key: context[key] for key in ("teacher_evidence", "source_id", "source_kind")}
        source = row.get("source_context") or {}
        reference = source.get("source_context_pack") or row.get("source_context_pack")
        if self.recipe.get("source_context_pack_version") == 1 and reference is not None:
            # The authentic pack verifies provenance; a director selection
            # limits both judges and repairs to the same original blocks.
            # A missing/changed pack must never fall back to a small anchor.
            evidence = revision_evidence(row, self.recipe, self.path, with_blocks=True, include_source_ids=source_ids)
            return {**evidence, "source_context_pack": deepcopy(reference),
                    "teacher_evidence_is_pack": True}
        if selected:
            # Selected evidence has no raw-anchor fallback. Missing pack
            # provenance is a dependency error even if cached text exists.
            return revision_evidence(row, self.recipe, self.path, with_blocks=True, include_source_ids=source_ids)
        try:
            return revision_evidence(row, self.recipe)
        except ValueError:
            source = row.get("source_context") or {}
            if source.get("kind") == "conversation":
                # Invalid/explicitly simulated recorded evidence cannot be
                # rehabilitated by an unrelated open-task brief or quotes.
                return None
            text = source.get("text") or source.get("task") or self.recipe.get("brief", "")
            if not isinstance(text, str) or not text.strip():
                return None
            return {"teacher_evidence": text, "source_id": row.get("source_id"),
                    "source_kind": "document" if source.get("kind") == "document" else "synthetic"}

    def _review_learner_context(self, row, messages, key, role):
        from lib.domain.learner_context import learner_contract
        from lib.domain.learner_context_review import learner_context_verdict, learner_context_format_feedback
        payload = {"learner_messages": messages, "qa_contract": learner_contract(row["qa_contract"]),
                   "learner_context_only": True}
        if self.recipe.get("learner_context_retry_version") != 1:
            # Preserve historical keys, payload and failure/invalidation policy.
            value = self.ask(key, role, "workflow.learner_context_check", payload,
                             allow_reasoning_fallback=False, prompt_stage="review")
            try:
                return learner_context_verdict(value)
            except ValueError:
                self.invalidate_checkpoint(["call", key])
                raise

        # Only schema failures get one new assessment. Provider/network errors
        # still propagate through ask's existing policy, without another loop.
        # Keep invalid raw JSON checkpoints: resume can reuse both attempts,
        # while every retrieval must still pass the strict domain validator.
        row.pop("learner_context_check", None)
        failures = []
        feedback = None
        for attempt in (1, 2):
            call_key = key if attempt == 1 else [*key, "format_repair", 1]
            request = payload if feedback is None else {**payload, "format_repair": feedback}
            value = self.ask(call_key, role, "workflow.learner_context_check", request,
                             allow_reasoning_fallback=False, prompt_stage="review")
            try:
                result = learner_context_verdict(value)
            except ValueError:
                feedback = learner_context_format_feedback(value)
                failures.append({"attempt": attempt, "code": feedback["code"],
                    "checkpoint": {"stage": self.stage, "sha256": hashlib.sha256(
                        canonical(["call", call_key]).encode("utf-8")).hexdigest()},
                    "response_sha256": hashlib.sha256(canonical(value).encode("utf-8")).hexdigest(),
                    "format_feedback": feedback})
                row["learner_context_retry"] = {"version": 1, "attempts": attempt,
                    "status": "invalid_after_retry" if attempt == 2 else "format_retry_pending",
                    "schema_failures": deepcopy(failures)}
                if attempt == 2:
                    raise
            else:
                row["learner_context_retry"] = {"version": 1, "attempts": attempt,
                    "status": "schema_valid", "schema_failures": deepcopy(failures)}
                return result

    def _claim_refutation_release(self, row, check):
        """Block a reported false claim without rewriting the provider review.

        This gate only acts on claims the reviewer actually marked refuted.
        Absence of such a report does not establish factual correctness.
        """
        from lib.infrastructure.workflow_store import digest
        version = self.recipe.get("claim_refutation_release_version")
        if version is None:
            return check
        if type(version) is not int or version != 1:
            raise ValueError("invalid_claim_refutation_release_version")
        audit = row["claim_review"]
        refuted = []
        for item in audit["assessments"]:
            review = item["review"]
            actual_claims = (review["normalized_response"] if audit["version"] in {3, 4, 5} else review["raw_response"])
            for claim in actual_claims["claim_checks"]:
                if claim["status"] == "refuted":
                    refuted.append({"message_index": review["request"]["scope"]["message_index"],
                        "field": claim["field"], "quote": claim["quote"], "severity": claim["severity"],
                        "reason": claim["basis"]["derivation"]})
        row["claim_release_decision"] = {"version": 1, "source_claim_review_sha256": digest(audit),
            "blocked": bool(refuted), "refuted_claims": deepcopy(refuted),
            "reason": "reviewer_reported_refutation" if refuted else "no_reviewer_reported_refutation"}
        if not refuted:
            return check
        # Keep scores, turn verdicts, raw replies and audit.derived_verdict
        # unchanged. The existing bounded repair loop consumes this veto.
        return {**deepcopy(check), "keep": False,
            "reason": "; ".join([check["reason"], "claim_release_refuted_claim: " + canonical(refuted)])}

    def _review_claim_score(self, target, row, evidence, key, *, instruction=""):
        """Assess real assistant prefixes without disclosing future turns.

        This replaces the primary aggregate score for explicitly versioned new
        runs. It does not add a second opinion after the same score. The raw
        provider replies and the derived conservative result remain separate;
        every request still uses the workflow's durable scheduling/checkpoints.
        """
        from lib.domain.claim_review import (claim_review_requests, validate_claim_review,
                                             merge_claim_reviews)
        from lib.infrastructure.workflow_store import digest
        version = self.recipe.get("claim_review_protocol_version")
        if type(version) is not int or version not in {1, 2}:
            raise ValueError("invalid_claim_review_protocol_version")
        release_version = self.recipe.get("claim_refutation_release_version")
        if release_version is not None and (type(release_version) is not int or release_version != 1):
            raise ValueError("invalid_claim_refutation_release_version")
        quote_version = self.recipe.get("claim_review_quote_matching_version")
        if quote_version is not None and (type(quote_version) is not int or quote_version != 1):
            raise ValueError("invalid_claim_review_quote_matching_version")
        reference_version = self.recipe.get("claim_reference_policy_version")
        if reference_version is not None and (type(reference_version) is not int or reference_version != 1):
            raise ValueError("invalid_claim_reference_policy_version")
        anchor_version = self.recipe.get("claim_review_anchor_protocol_version")
        if anchor_version is not None and (type(anchor_version) is not int or anchor_version != 1):
            raise ValueError("invalid_claim_review_anchor_protocol_version")
        adversarial_version = self.recipe.get("claim_review_adversarial_protocol_version")
        if adversarial_version is not None and (type(adversarial_version) is not int or adversarial_version != 1):
            raise ValueError("invalid_claim_review_adversarial_protocol_version")
        for name in ("claim_review_wire_schema_version", "claim_review_focus_version", "claim_review_condition_contract_version"):
            setting = self.recipe.get(name)
            if setting is not None and (type(setting) is not int or setting != 1):
                raise ValueError("invalid_" + name)
        depth_version = self.recipe.get("reasoning_depth_contract_version", 0)
        if type(depth_version) is not int or depth_version not in {0, 1}:
            raise ValueError("invalid_reasoning_depth_contract_version")
        if adversarial_version == 1:
            return self._review_adversarial_claim_score(target, row, evidence, key, instruction=instruction)
        if version == 2:
            return self._review_blind_claim_score(target, row, evidence, key, instruction=instruction)
        payload = self._review_payload(target, row)
        requests = claim_review_requests(target, payload, evidence,
            reasoning_required=self.recipe.get("reasoning_required", False),
            review_requirements=current_claim_review_requirements(self, target, row, instruction=instruction) or None)
        audit = {"version": 1, "method": "prefix_only_model_claim_review",
                 "candidate_sha256": digest(payload), "source_input_sha256": digest(evidence),
                 "source_evidence_sha256": requests[0]["scope"]["source_evidence_sha256"],
                 "status": "pending", "assessments": [], "schema_failures": []}
        row["claim_review"] = audit
        role = "jev" if self.recipe.get("package_review", {}).get("enabled") else "generation"
        receipts = []
        for request in requests:
            scope = request["scope"]
            base_key = [*key, "claim_review", scope["message_index"], scope["scope_sha256"]]
            for format_attempt in (1, 2):
                call_key = base_key if format_attempt == 1 else [*base_key, "format_repair", 1]
                call_data = request if format_attempt == 1 else {**request, "format_repair": {
                    "code": "invalid_claim_review_response",
                    "instruction": "Return the complete claim-review contract. Copy this request's exact scope; preserve every required field and check the actual candidate, not the previous grade."}}
                # JEV is a normal configured chat reviewer in this project.
                # Preserve the node's scoring-model choice; only the review
                # contract changes from aggregate grades to concrete claims.
                raw = self.ask(call_key, role, "workflow.review_score", call_data,
                    allow_reasoning_fallback=False, model_stage="review", prompt_stage="review",
                    instruction=_CLAIM_REVIEW_WIRE_PROTOCOL)
                checkpoint = {"stage": self.stage, "key": deepcopy(call_key),
                    "checkpoint_sha256": digest(["call", call_key]),
                    "request_sha256": digest(call_data), "review_request_sha256": digest(request),
                    "response_sha256": digest(raw),
                    **({"format_repair": deepcopy(call_data["format_repair"])} if format_attempt == 2 else {})}
                try:
                    receipt = (validate_claim_review(raw, request, quote_matching_version=1)
                        if quote_version == 1 else validate_claim_review(raw, request))
                except ValueError as error:
                    audit["schema_failures"].append({"message_index": scope["message_index"],
                        "attempt": format_attempt, "code": str(error), "checkpoint": checkpoint})
                    audit["status"] = "invalid_after_retry" if format_attempt == 2 else "format_retry_pending"
                    # Do not invalidate real failed replies: resume reuses and
                    # revalidates both bounded attempts rather than paying again.
                    if format_attempt == 2:
                        raise
                else:
                    receipts.append(receipt)
                    audit["assessments"].append({"review": deepcopy(receipt), "checkpoint": checkpoint})
                    break
        check = merge_claim_reviews(payload, receipts)
        audit.update(status="complete", derived_verdict=deepcopy(check))
        row["reasoning_assessment_method"] = "llm_claim_review"
        row["reasoning_assessment_reference"] = {"kind": "llm_claim_review",
            "scope": "independent_prefix_requests_not_aggregate_provider_grade",
            "candidate_sha256": digest(payload), "source_input_sha256": digest(evidence),
            "source_evidence_sha256": audit["source_evidence_sha256"],
            "checkpoints": [deepcopy(item["checkpoint"]) for item in audit["assessments"]]}
        return self._claim_refutation_release(row, check)

    def _claim_v2_call(self, request, *, prompt_id, base_key, role, validator, audit, phase, instruction):
        """One bounded schema retry, retaining both real request checkpoints."""
        from lib.infrastructure.workflow_store import digest
        format_version = self.recipe.get("claim_review_format_feedback_version")
        if format_version is not None and (type(format_version) is not int or format_version not in {1, 2}):
            raise ValueError("invalid_claim_review_format_feedback_version")
        boundaries = _claim_format_boundaries(request, phase) if format_version in {1, 2} else None
        if boundaries is not None and "review_window" in request:
            boundaries["current_target_unit_ids"] = request["review_window"]["unit_ids"]
            boundaries["response_scope_sha256"] = request["review_window"]["sha256"]
        schema_version = self.recipe.get("claim_review_wire_schema_version")
        focus_version = self.recipe.get("claim_review_focus_version")
        for name, setting in (("claim_review_wire_schema_version", schema_version),
                              ("claim_review_focus_version", focus_version)):
            if setting is not None and (type(setting) is not int or setting != 1):
                raise ValueError("invalid_" + name)
        wire_metadata = {}
        condition_contract = request.get("condition_contract_version") == 1
        transport_version = self.recipe.get("claim_review_transport_version")
        if transport_version is not None and (type(transport_version) is not int or transport_version != 1):
            raise ValueError("invalid_claim_review_transport_version")
        if condition_contract:
            instruction = (_CONDITION_INVENTORY_WIRE_PROTOCOL if phase == "claim_challenge"
                           else _CONDITION_ADJUDICATION_WIRE_PROTOCOL)
            if transport_version == 1:
                from .review_protocol import CONDITION_REVIEW_COMPLETE_WIRE_PROTOCOL
                instruction += CONDITION_REVIEW_COMPLETE_WIRE_PROTOCOL
        if request.get("claim_review_protocol_version") == 4:
            if schema_version == 1 or condition_contract:
                from lib.infrastructure.claim_review_wire_schema import claim_review_wire_schema_metadata
                wire_metadata.update(claim_review_wire_schema_metadata(request))
            if focus_version == 1 and not condition_contract:
                from lib.domain.review_claim_focus import claim_review_focus_units
                wire_metadata["claim_review_focus"] = claim_review_focus_units(request["target_units"])
        call_instruction = instruction
        if (request.get("review_requirements") or {}).get("revision_instruction"):
            call_instruction += ("\nreview_requirements.revision_instruction是本次用户明确的修正要求。"
                "核对当前候选是否实际落实相关要求；它不是事实证据，不能补造前提、覆盖用户原话或修改工具记录。"
                "尚未落实时在verdict说明实际缺口并拒绝放行；不能将表达要求未落实虚构为事实反证。\n")
        if wire_metadata:
            from lib.infrastructure.claim_review_wire_schema import CLAIM_REVIEW_DYNAMIC_WIRE_HEADER
            call_instruction += CLAIM_REVIEW_DYNAMIC_WIRE_HEADER
        if boundaries is not None:
            boundary_note = ("\n以下仅说明JSON引用路径与实际messages数组索引；数组索引不是对话轮次编号。"
                "context引用只能使用列出的合法数组索引及实际字段；scope只复制列出的字段及request原值，不增加target等字段。\n")
            if request.get("claim_review_protocol_version") in {3, 4}:
                boundary_note = ("\n以下仅说明实际messages索引与本次候选单元id；context证据只能使用合法实际索引。"
                    "仅复制request.scope.scope_sha256为返回的scope_sha256，不返回整个scope；候选评审只引用本次unit_id，不抄quote。\n")
            call_instruction += boundary_note + canonical(boundaries)
            if "review_window" in request:
                call_instruction += ("\n本次为有界返回窗口。上面的scope复制说明以review_window.sha256为准；"
                    "仅current_target_unit_ids需要本次返回，完整target_units仍供通读与条件引用。\n")
        if wire_metadata:
            call_instruction += ("\n以下为本次实际输入派生的JSON传输元数据。response_json_schema是完整返回格式说明，"
                "不表示上游已启用json_schema；仍使用现有json_object传输。source_path是相对source_evidence内部的键/整数索引数组，"
                "basis.evidence始终为数组。claim_review_focus若存在仅提示当前原文中限制/依赖措辞的位置，"
                "不是错误标签、事实证据或额外放行规则；所有候选单元仍须按节点自定义标准评审。\n" + canonical(wire_metadata))
        previous_error = None
        previous_detail = {}
        for attempt in (1, 2):
            call_key = base_key if attempt == 1 else [*base_key, "format_repair", 1]
            feedback = {"code": ("invalid_blind_review_response" if phase == "blind_reference"
                                  else "invalid_challenge_review_response" if request.get("claim_review_protocol_version") == 4
                                       and phase == "claim_challenge"
                                  else "invalid_adversarial_review_response" if request.get("claim_review_protocol_version") == 4
                                  else "invalid_claim_review_v3_response" if request.get("claim_review_protocol_version") == 3
                                  else "invalid_claim_review_v2_response"),
                        "instruction": "Return the complete current protocol contract and copy its scope exactly. Preserve the real evidence and candidate boundaries; do not fabricate missing fields."}
            if request.get("claim_review_protocol_version") == 3:
                feedback["instruction"] = ("Return the complete current protocol-3 contract. Copy only request.scope.scope_sha256 "
                    "as scope_sha256; use only current target_units unit_id values. Do not return scope, field, or candidate quote.")
            elif request.get("claim_review_protocol_version") == 4:
                feedback["instruction"] = ("Return the complete current protocol-4 phase contract. Copy only request.scope.scope_sha256 "
                    "as scope_sha256; use only current target_units unit_id values and actual challenge issue ids. "
                    "Do not return scope, field, candidate quote, or a different phase's fields.")
            if transport_version == 1:
                if "review_window" in request:
                    feedback["instruction"] = feedback["instruction"].replace(
                        "request.scope.scope_sha256", "request.review_window.sha256")
                feedback["instruction"] += (
                    " Source-reference diagnostics only locate exact existing quotations. "
                    "For quote_at_other_source_path, inspect the named response item and choose an actual "
                    "exact_quote_source_paths location. For quote_not_in_source, cite real source text or "
                    "declare insufficient support; never paraphrase a quotation or invent a supporting condition. "
                    "Resubmit the complete window, including the unchanged verdict contract and all units.")
            if boundaries is not None and previous_error is not None:
                feedback["instruction"] += "\nSchema-only diagnostic: " + canonical({
                    "error_code": previous_error, **boundaries, **previous_detail})
            data = request if attempt == 1 else {**request, "format_repair": feedback}
            raw = self.ask(call_key, role, prompt_id, data, allow_reasoning_fallback=False,
                model_stage="review", prompt_stage="review", instruction=call_instruction)
            reference = {"stage": self.stage, "key": deepcopy(call_key),
                "checkpoint_sha256": digest(["call", call_key]), "request_sha256": digest(data),
                "review_request_sha256": digest(request), "response_sha256": digest(raw),
                **({"format_repair": deepcopy(feedback)} if attempt == 2 else {})}
            try:
                receipt = validator(raw, request)
            except ValueError as error:
                previous_error = (_safe_claim_format_code(error, phase, request.get("claim_review_protocol_version"))
                                  if boundaries is not None else str(error))
                if format_version == 2:
                    previous_detail = _claim_format_quote_diagnostic(raw, request, phase,
                        quote_matching_version=self.recipe.get("claim_review_quote_matching_version"),
                        error_code=previous_error)
                if transport_version == 1 and request.get("claim_review_protocol_version") == 4:
                    from ..domain.wire_schema import review_wire_diagnostics
                    previous_detail.update(review_wire_diagnostics(raw, request))
                audit["schema_failures"].append({"phase": phase, "message_index": request["scope"]["message_index"],
                    "attempt": attempt, "code": previous_error, "checkpoint": reference})
                audit["status"] = "invalid_after_retry" if attempt == 2 else "format_retry_pending"
                if attempt == 2:
                    raise
            else:
                return receipt, reference

    def _review_adversarial_claim_score(self, target, row, evidence, key, *, instruction=""):
        """Inventory conditions or challenge, then review in separate contexts.

        The opt-in inventory supplies no pre-judgment. Historical runs retain
        their candidate-aware challenge contract. Neither is blind or truth;
        both actual, fallible model outputs remain checkpointed.
        """
        from lib.domain.adversarial_claim_review import (challenge_review_requests, validate_challenge_review,
            adversarial_claim_review_requests, validate_adversarial_claim_review, merge_adversarial_claim_reviews)
        from lib.infrastructure.workflow_store import digest
        payload = self._review_payload(target, row)
        requirements = current_claim_review_requirements(self, target, row, instruction=instruction) or None
        condition_version = self.recipe.get("claim_review_condition_contract_version")
        inputs = challenge_review_requests(target, payload, evidence,
            reasoning_required=self.recipe.get("reasoning_required", False), review_requirements=requirements,
            condition_contract_version=condition_version)
        transport_version = self.recipe.get("claim_review_transport_version")
        if transport_version is not None and (type(transport_version) is not int or transport_version != 1):
            raise ValueError("invalid_claim_review_transport_version")
        if transport_version == 1 and condition_version == 1:
            from ..domain.claim_windows import plan_review_windows
            windowed = any(len(plan_review_windows(request)) > 1 for request in inputs)
        else:
            windowed = False
        audit = {"version": 4, "method": "adversarial_anchored_claim_review",
            "candidate_sha256": digest(payload), "source_input_sha256": digest(evidence),
            "source_evidence_sha256": inputs[0]["scope"]["source_evidence_sha256"],
            "status": "pending", "challenge_reviews": [], "assessments": [], "schema_failures": []}
        row["claim_review"] = audit
        if windowed:
            audit.update(version=5, method="windowed_condition_claim_review", window_failures=[])
        role = "jev" if self.recipe.get("package_review", {}).get("enabled") else "generation"
        challenges = []
        for request in inputs:
            call_key = ["claim_challenge", target, request["scope"]["scope_sha256"]]
            if windowed:
                from lib.bootstrap.automatic_review import claim_windows
                receipt, record = claim_windows(self, role=role, audit=audit, phase="claim_challenge",
                    instruction=_CLAIM_CHALLENGE_V4_WIRE_PROTOCOL).execute(request)
            else:
                receipt, checkpoint = self._claim_v2_call(request, prompt_id="workflow.review_reference",
                    base_key=call_key, role=role, validator=validate_challenge_review, audit=audit,
                    phase="claim_challenge", instruction=_CLAIM_CHALLENGE_V4_WIRE_PROTOCOL)
                record = {"review": deepcopy(receipt), "checkpoint": checkpoint}
            challenges.append(receipt)
            audit["challenge_reviews"].append(record)
        requests = adversarial_claim_review_requests(target, payload, evidence,
            reasoning_required=self.recipe.get("reasoning_required", False), review_requirements=requirements,
            challenge_reviews=challenges, condition_contract_version=condition_version)
        receipts = []
        for request in requests:
            call_key = [*key, "claim_adjudication", request["scope"]["message_index"], request["scope"]["scope_sha256"]]
            if windowed:
                receipt, record = claim_windows(self, role=role, audit=audit, phase="claim_adjudication",
                    instruction=_CLAIM_ADJUDICATION_V4_WIRE_PROTOCOL).execute(request)
            else:
                receipt, checkpoint = self._claim_v2_call(request, prompt_id="workflow.review_score",
                    base_key=call_key, role=role, validator=validate_adversarial_claim_review, audit=audit,
                    phase="claim_adjudication", instruction=_CLAIM_ADJUDICATION_V4_WIRE_PROTOCOL)
                record = {"review": deepcopy(receipt), "checkpoint": checkpoint}
            receipts.append(receipt)
            audit["assessments"].append(record)
        check = merge_adversarial_claim_reviews(payload, receipts)
        audit.update(status="complete", derived_verdict=deepcopy(check))
        row["reasoning_assessment_method"] = "llm_claim_review"
        references = lambda collection: [deepcopy(window["checkpoint"]) for record in collection
            for window in (record["windows"] if windowed else [record])]
        row["reasoning_assessment_reference"] = {"kind": "llm_claim_review", "version": audit["version"],
            "scope": ("literal_condition_inventory_then_anchored_review_not_truth_certification"
                if condition_version == 1 else "candidate_aware_challenge_then_anchored_adjudication_not_truth_certification"),
            "candidate_sha256": digest(payload), "source_input_sha256": digest(evidence),
            "source_evidence_sha256": audit["source_evidence_sha256"],
            "challenge_checkpoints": references(audit["challenge_reviews"]),
            "checkpoints": references(audit["assessments"])}
        return self._claim_refutation_release(row, check)

    def _review_blind_claim_score(self, target, row, evidence, key, *, instruction=""):
        """Establish candidate-blind reference points before inspecting a draft.

        Blind references are fallible model assessments, not truth labels.
        Their durable keys omit the current draft/attempt, so correcting that
        draft reuses the independently prepared reference for this prefix.
        """
        from lib.domain.blind_claim_review import (blind_review_requests, validate_blind_review,
            claim_review_requests_v2, validate_claim_review_v2, merge_claim_reviews_v2)
        from lib.infrastructure.workflow_store import digest
        payload = self._review_payload(target, row)
        anchored = self.recipe.get("claim_review_anchor_protocol_version") == 1
        if anchored:
            from lib.domain.anchored_claim_review import (claim_review_requests_v3, validate_claim_review_v3,
                                                         merge_claim_reviews_v3)
        requirements = current_claim_review_requirements(self, target, row, instruction=instruction) or None
        inputs = blind_review_requests(target, payload, evidence,
            reasoning_required=self.recipe.get("reasoning_required", False), review_requirements=requirements)
        audit = {"version": 3 if anchored else 2, "method": ("anchored_reference_model_claim_review" if anchored
                else "blind_reference_model_claim_review"),
            "candidate_sha256": digest(payload), "source_input_sha256": digest(evidence),
            "source_evidence_sha256": inputs[0]["scope"]["source_evidence_sha256"],
            "status": "pending", "blind_reviews": [], "assessments": [], "schema_failures": []}
        row["claim_review"] = audit
        role = "jev" if self.recipe.get("package_review", {}).get("enabled") else "generation"
        blind_receipts = []
        for request in inputs:
            # No item id, candidate hash or repair-attempt number enters this
            # key. The immutable run still pins the actual reviewer and prompt.
            base_key = ["blind_reference", target, request["scope"]["scope_sha256"]]
            receipt, checkpoint = self._claim_v2_call(request, prompt_id="workflow.review_reference",
                base_key=base_key, role=role, validator=validate_blind_review, audit=audit,
                phase="blind_reference", instruction=_BLIND_REFERENCE_WIRE_PROTOCOL)
            blind_receipts.append(receipt)
            audit["blind_reviews"].append({"review": deepcopy(receipt), "checkpoint": checkpoint})
        build_requests = claim_review_requests_v3 if anchored else claim_review_requests_v2
        requests = build_requests(target, payload, evidence,
            reasoning_required=self.recipe.get("reasoning_required", False), review_requirements=requirements,
            blind_reviews=blind_receipts)
        receipts = []
        validator = validate_claim_review_v3 if anchored else validate_claim_review_v2
        policies = {}
        if not anchored and self.recipe.get("claim_review_quote_matching_version") == 1:
            policies["quote_matching_version"] = 1
        if self.recipe.get("claim_reference_policy_version") == 1:
            policies["reference_policy_version"] = 1
        if policies:
            validate_grade = validator
            validator = lambda raw, request: validate_grade(raw, request, **policies)
        grade_instruction = _CLAIM_REVIEW_V3_WIRE_PROTOCOL if anchored else _CLAIM_REVIEW_V2_WIRE_PROTOCOL
        if not anchored and self.recipe.get("claim_reference_policy_version") == 1:
            grade_instruction = grade_instruction.replace("kind（source/context）", "kind（source/context/logic）").replace(
                "同时claim_checks中同field和quote须有source/context真引文支持的supported项目。",
                "同时claim_checks中同field和quote须有supported项目，basis.kind为source/context/logic；"
                "source/context的evidence含实际引文，logic的evidence可为空数组，derivation仍为非空具体反证。")
        for request in requests:
            base_key = [*key, "claim_review", request["scope"]["message_index"], request["scope"]["scope_sha256"]]
            receipt, checkpoint = self._claim_v2_call(request, prompt_id="workflow.review_score",
                base_key=base_key, role=role, validator=validator, audit=audit,
                phase="claim_review", instruction=grade_instruction)
            receipts.append(receipt)
            audit["assessments"].append({"review": deepcopy(receipt), "checkpoint": checkpoint})
        check = (merge_claim_reviews_v3 if anchored else merge_claim_reviews_v2)(payload, receipts)
        audit.update(status="complete", derived_verdict=deepcopy(check))
        row["reasoning_assessment_method"] = "llm_claim_review"
        row["reasoning_assessment_reference"] = {"kind": "llm_claim_review", "version": 3 if anchored else 2,
            "scope": ("candidate_blind_reference_then_program_bound_units_not_provider_quotes" if anchored else
                      "candidate_blind_reference_then_prefix_claim_review_not_truth_certification"),
            "candidate_sha256": digest(payload), "source_input_sha256": digest(evidence),
            "source_evidence_sha256": audit["source_evidence_sha256"],
            "blind_checkpoints": [deepcopy(item["checkpoint"]) for item in audit["blind_reviews"]],
            "checkpoints": [deepcopy(item["checkpoint"]) for item in audit["assessments"]]}
        return self._claim_refutation_release(row, check)

    def _review_score(self, target, row, evidence, key, instruction):
        self.state["stages"]["review"]["phase"] = "scoring"
        self.save(force=False)
        claim_version = self.recipe.get("claim_review_protocol_version")
        if claim_version is not None and (type(claim_version) is not int or claim_version not in {1, 2}):
            raise ValueError("invalid_claim_review_protocol_version")
        if claim_version in {1, 2}:
            # An inherited receipt describes its original body, never a new
            # deterministic rejection or a pending revised candidate.
            row.pop("claim_review", None)
            if self.recipe.get("claim_refutation_release_version") == 1:
                row.pop("claim_release_decision", None)
        required_issue = None
        if (self.recipe.get("reasoning_revision_protocol_version") == 1
                and self.recipe.get("reasoning_required") is True and target in {"sft", "multiturn", "agent"}):
            from lib.domain.turn_review import reasoning_text
            if any(isinstance(message, dict) and message.get("role") == "assistant"
                   and not (message.get("tool_calls") or message.get("toolCalls")) and not reasoning_text(message)
                   for message in row.get("messages", [])):
                required_issue = "review_repair_reasoning_missing"
        language_requirements = _authored_reasoning_language_requirements(self.recipe, target, row)
        if issue := (required_issue or _authored_reasoning_language_issue(row, language_requirements)
                     or _row_training_surface_issue(target, row, self.recipe)):
            if self.recipe.get("reasoning_distillation_audit_version") == 1:
                row["reasoning_assessment_method"] = "deterministic_guard"
                row["reasoning_assessment_reference"] = {"kind": "deterministic_guard", "code": issue}
            if issue == "training_surface_knowledge_source_narration":
                issue = _knowledge_source_narration_reason(target, row, self.recipe)
            return {"keep": False, "grounded": False, "reasoning_valid": False, "correctness": 1,
                    "scores": {name: 1 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                    "reason": issue}
        role = "jev" if self.recipe.get("package_review", {}).get("enabled") else "generation"
        if self.recipe.get("reasoning_distillation_audit_version") == 1:
            row["reasoning_assessment_method"] = "llm_review"
        data = {"target": target, "candidate": self._review_payload(target, row),
                "source_evidence": evidence, "instruction": instruction,
                "design_requirements": (row.get("revision_context") or {}).get("design_requirements", {}),
                "qa_contract": row.get("qa_contract"), "generation_style": _current_review_generation_style(
                    self, target, row, language_requirements)}
        _validate_reasoning_depth_input_control(self, target, row, data["generation_style"])
        data.update(_authored_review_audit(target, row))
        warmup_scope = _authored_reasoning_warmup_scope(self.recipe, target, row)
        if warmup_scope:
            data["authored_reasoning_warmup"] = warmup_scope
        data = _selected_review_request(data, evidence)
        turn_scope = self.recipe.get("turn_review_protocol_version") == 1 and target in {"sft", "multiturn", "agent"}
        if turn_scope:
            from lib.domain.turn_review import supervised_assistant_turns
            data["supervised_assistant_turns"] = supervised_assistant_turns(data["candidate"],
                reasoning_required=self.recipe.get("reasoning_required", False))
        def assessed(call_key, call_data):
            rules = ({"instruction": _authored_review_rules(self.recipe)}
                     if _authored_cot(target, row) and not call_data.get("learner_context_only") else {})
            reliable_transport = self.recipe.get("claim_review_transport_version") == 1
            if reliable_transport:
                from .review_protocol import AGGREGATE_REVIEW_COMPLETE_WIRE_PROTOCOL
                rules["instruction"] = rules.get("instruction", "") + AGGREGATE_REVIEW_COMPLETE_WIRE_PROTOCOL
            if not call_data.get("learner_context_only") and language_requirements:
                rules["instruction"] = "\n".join(filter(None, (
                    rules.get("instruction"), _authored_reasoning_language_review_rules(language_requirements))))
            if not call_data.get("learner_context_only") and (
                    any(item.get("protocol_version") in {2, 3} for item in language_requirements)
                    or _authored_cot_language_v2(self.recipe, target, row)):
                language_version = self.recipe.get("reasoning_language_contract_version")
                parts = [rules.get("instruction"), _CURRENT_STYLE_V3_RULE if language_version == 3 else _CURRENT_STYLE_V2_RULE]
                if not language_requirements:
                    parts.append("冻结 CoT 节点配置给出的待审语言标准：当前新撰写解释。" + (
                        _CONCEPT_TRANSLATION_REVIEW_V3 if language_version == 3 else _TRANSLATION_REVIEW_V2))
                rules["instruction"] = "\n".join(filter(None, parts))
            if not call_data.get("learner_context_only"):
                knowledge_rules = _knowledge_application_review_rules(self.recipe, row, phase="review")
                if knowledge_rules:
                    rules["instruction"] = "\n".join(filter(None, (rules.get("instruction"), knowledge_rules)))
                warmup_rules = _authored_reasoning_warmup_rules(self.recipe, target, row, phase="review")
                if warmup_rules:
                    rules["instruction"] = "\n".join(filter(None, (rules.get("instruction"), warmup_rules)))
            if call_data.get("supervised_assistant_turns") is not None:
                from lib.domain.turn_review import turn_review_verdict
                failures = []
                for format_attempt in (1, 2):
                    current_key = call_key if format_attempt == 1 else [*call_key, "turn_format_repair", 1]
                    request = call_data if not failures else {**call_data, "format_repair": {
                        "code": failures[-1]["code"],
                        "instruction": "Return the complete aggregate and every listed assistant-turn verdict. Copy the exact original indices and hashes; recheck the actual content, never fabricate a missing grade."}}
                    value = self.ask(current_key, role, "workflow.review_score",
                        project_review_request(self.recipe, request, phase="review"),
                        allow_reasoning_fallback=False, **rules)
                    try:
                        result = turn_review_verdict(value, call_data["supervised_assistant_turns"])
                    except ValueError as error:
                        failures.append({"attempt": format_attempt, "code": str(error),
                            "response_sha256": hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()})
                        row["assistant_turn_review_retry"] = {"version": 1, "attempts": format_attempt,
                            "status": "invalid_after_retry" if format_attempt == 2 else "format_retry_pending",
                            "schema_failures": deepcopy(failures)}
                        # Keep each raw response in its original request checkpoint.
                        if format_attempt == 2:
                            raise
                    else:
                        row["assistant_turn_review_retry"] = {"version": 1, "attempts": format_attempt,
                            "status": "schema_valid", "schema_failures": deepcopy(failures)}
                        if self.recipe.get("reasoning_distillation_audit_version") == 1:
                            row["reasoning_assessment_reference"] = {"kind": "llm_review", "stage": "review",
                                "key": deepcopy(current_key), "checkpoint_sha256": hashlib.sha256(
                                    canonical(["call", current_key]).encode("utf-8")).hexdigest()}
                        return result
            if reliable_transport:
                from jsonschema import Draft202012Validator
                from ..domain.wire_schema import review_grade_schema, schema_diagnostics
                failures, diagnostic = [], None
                for format_attempt in (1, 2):
                    actual_key = call_key if format_attempt == 1 else [*call_key, "aggregate_format_repair", 1]
                    request = call_data if diagnostic is None else {**call_data, "format_repair": {
                        "code": "invalid_aggregate_review_response",
                        "instruction": "Return the complete score contract; judge the original candidate. Schema-only diagnostic: "
                                       + canonical(diagnostic)}}
                    value = self.ask(actual_key, role, "workflow.review_score",
                        project_review_request(self.recipe, request, phase="review"),
                        allow_reasoning_fallback=False, **rules)
                    try:
                        if not Draft202012Validator(review_grade_schema()).is_valid(value):
                            raise ValueError("invalid_aggregate_review_response")
                        result = verdict(value)
                    except ValueError:
                        diagnostic = schema_diagnostics(value, review_grade_schema())
                        failures.append({"attempt": format_attempt, "response_sha256": hashlib.sha256(
                            canonical(value).encode("utf-8")).hexdigest(), "diagnostic": diagnostic})
                        if format_attempt == 2:
                            raise
                    else:
                        if failures:
                            row.setdefault("aggregate_review_retries", []).append({"key": deepcopy(actual_key),
                                "attempts": format_attempt, "status": "schema_valid", "schema_failures": failures})
                        call_key = actual_key
                        break
            else:
                value = self.ask(call_key, role, "workflow.review_score",
                                 project_review_request(self.recipe, call_data, phase="review"),
                                 allow_reasoning_fallback=False, **rules)
                try:
                    result = verdict(value)
                except ValueError:
                    self.invalidate_checkpoint(["call", call_key])
                    raise
            if self.recipe.get("reasoning_distillation_audit_version") == 1:
                row["reasoning_assessment_reference"] = {"kind": "llm_review", "stage": "review",
                    "key": deepcopy(call_key), "checkpoint_sha256": hashlib.sha256(
                        canonical(["call", call_key]).encode("utf-8")).hexdigest()}
            return result
        check = (self._review_claim_score(target, row, evidence, key, instruction=instruction)
                 if claim_version in {1, 2} and target in {"sft", "multiturn", "cot"}
                 else assessed([*key, "score"], data))
        if target in PREFERENCE_TARGETS:
            # A failed preferred answer/pair cannot be released regardless of
            # its negative response's score. Do not pay for that second score
            # until correction produces an acceptable candidate. Inherited
            # labels describe an older body and must not survive this verdict.
            row.pop("preference", None)
            row.pop("rlaif", None)
            threshold = validate_review_repair(self.recipe["review_repair"])["score_threshold"]
            if not accepted(check) or min(check["scores"].values()) / 5 < threshold:
                return check
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
            if self.recipe.get("claim_review_transport_version") == 1:
                from ..domain.preference_feedback import preference_release_feedback
                check = preference_release_feedback(row, check)
                if not accepted(check):
                    return check
        from lib.domain.learner_context import learner_contract, learner_messages, prompt_at_least
        if (row.get("qa_contract") and accepted(check)
                and prompt_at_least(self.recipe, "workflow.review_score", (1, 1, 0))):
            messages = learner_messages(target, data["candidate"])
            if messages:
                if self.recipe.get("learner_context_review_version") == 1:
                    context_key = [*key, "learner_context"]
                    context_check = self._review_learner_context(row, messages, context_key, role)
                    row["learner_context_check"] = {**context_check,
                        "scope": "actual_training_messages_only", "version": 1}
                    if self.recipe.get("reasoning_distillation_audit_version") == 1:
                        actual_context_key = ([*context_key, "format_repair", 1]
                            if row.get("learner_context_retry", {}).get("attempts") == 2 else context_key)
                        row["reasoning_assessment_reference"]["learner_context_checkpoint"] = {
                            "key": actual_context_key, "checkpoint_sha256": hashlib.sha256(
                                canonical(["call", actual_context_key]).encode("utf-8")).hexdigest()}
                    if not context_check["keep"]:
                        check = {**check, "keep": False, "reason": context_check["reason"]}
                else:
                    context_check = assessed([*key, "learner_context"], {
                        "target": "sft", "candidate": {"messages": messages},
                        "learner_messages": messages, "source_evidence": None,
                        "qa_contract": learner_contract(row["qa_contract"]),
                        "assessment": "learner_context_only", "learner_context_only": True,
                    })
                    row["learner_context_check"] = {**context_check, "scope": "actual_training_messages_only"}
                    if not accepted(context_check):
                        check = {**check, "keep": False, "reason": context_check["reason"],
                                 "scores": {name: min(score, context_check["scores"][name])
                                            for name, score in check["scores"].items()}}
        return check

    def _repair_content(self, target, row, evidence, key, check, instruction):
        self.state["stages"]["review"]["phase"] = "repairing"
        self.save(force=False)
        from lib.infrastructure.workflow_store import digest
        payload = self._review_payload(target, row)
        rules = {"instruction": _authored_repair_rules(self.recipe)} if _authored_cot(target, row) else {}
        if target in PREFERENCE_TARGETS and self.recipe.get("claim_review_transport_version") == 1:
            from ..domain.preference_feedback import PREFERENCE_REPAIR_CONTRACT
            rules["instruction"] = rules.get("instruction", "") + PREFERENCE_REPAIR_CONTRACT
        language_requirements = _authored_reasoning_language_requirements(self.recipe, target, row)
        current_style = _current_review_generation_style(self, target, row, language_requirements)
        _validate_reasoning_depth_input_control(self, target, row, current_style)
        language_repair = _authored_reasoning_language_repair_rules(language_requirements)
        cot_language_v2 = _authored_cot_language_v2(self.recipe, target, row)
        if cot_language_v2:
            language_repair = ("冻结 CoT 节点配置给出的互译理解返工要求：" + _CONCEPT_TRANSLATION_REPAIR_V3
                if self.recipe.get("reasoning_language_contract_version") == 3 else
                "冻结 CoT 节点配置给出的互译返工要求：保留并修正完整显式解释，"
                "补齐所有相关论证，并给每个中文分析自然段配完整忠实英文译段，不只加术语、标题或摘要；"
                "逐项核对前提、条件、数值、单位、实体、关系、否定、例外、限定、结论和不确定性，"
                "不新增事实，不删 reasoning，不靠凑长，不改用户或工具事实。")
        if language_repair:
            rules["instruction"] = "\n".join(filter(None, (
                rules.get("instruction"), _CURRENT_STYLE_V3_RULE if self.recipe.get(
                    "reasoning_language_contract_version") == 3 else _CURRENT_STYLE_V2_RULE, language_repair)))
        knowledge_rules = _knowledge_application_review_rules(self.recipe, row, phase="repair")
        if knowledge_rules:
            rules["instruction"] = "\n".join(filter(None, (rules.get("instruction"), knowledge_rules)))
        warmup_rules = _authored_reasoning_warmup_rules(self.recipe, target, row, phase="repair")
        if warmup_rules:
            rules["instruction"] = "\n".join(filter(None, (rules.get("instruction"), warmup_rules)))
        cot_delta = self.recipe.get("qa_agent_memory_version") == 1 and target == "cot"
        from lib.domain.learner_context import prompt_at_least
        multiturn_delta = target == "multiturn" and prompt_at_least(self.recipe, "workflow.review_repair", (1, 7, 0))
        reasoning_revision = self.recipe.get("reasoning_revision_protocol_version") == 1
        assistant_delta = reasoning_revision and target in {"sft", "multiturn"}
        required_reasoning = []
        if reasoning_revision and target in {"sft", "multiturn", "agent"}:
            from lib.domain.turn_review import supervised_assistant_turns
            # Export preferences can omit reasoning from the projection. They
            # cannot turn an existing explanation into an answer-only repair.
            required_reasoning = [turn["message_index"] for turn in supervised_assistant_turns(row,
                reasoning_required=self.recipe.get("reasoning_required", False)) if turn["requires_reasoning"]]
        if cot_delta:
            rules["instruction"] = (rules.get("instruction", "") + "\n" + _COT_DELTA_REPAIR_RULES).lstrip()
        data = _selected_review_request(
            {"target": target, "candidate": payload, "source_evidence": evidence,
             "review_feedback": check, "instruction": instruction,
             "design_requirements": (row.get("revision_context") or {}).get("design_requirements", {}),
             "qa_contract": row.get("qa_contract"),
             "generation_style": current_style,
             **_authored_review_audit(target, row)}, evidence)
        warmup_scope = _authored_reasoning_warmup_scope(self.recipe, target, row)
        if warmup_scope:
            data["authored_reasoning_warmup"] = warmup_scope
        if assistant_delta:
            data["required_reasoning_indices"] = required_reasoning
        value = self.ask([*key, "repair"], "generation", "workflow.review_repair",
                         project_review_request(self.recipe, data, phase="repair"),
                         allow_reasoning_fallback=False, **rules)
        if (multiturn_delta or assistant_delta) and (not isinstance(value, dict) or set(value) != {"record", "quotes", "uncertain"}
                or not isinstance(value.get("quotes"), list) or len(value["quotes"]) > 32
                or any(not isinstance(quote, str) or not quote.strip() or len(quote) > 20_000
                       or "\x00" in quote for quote in value["quotes"])):
            return row, "review_repair_invalid_schema"
        if (not isinstance(value, dict) or type(value.get("uncertain", False)) is not bool
                or value.get("uncertain") or not isinstance(value.get("record"), dict)):
            return row, "review_repair_content_unavailable"
        candidate = value["record"]
        if multiturn_delta or assistant_delta:
            from lib.domain.assistant_message_delta import apply_assistant_message_delta
            try:
                # Index the unmodified original list; the model never copies
                # user articles, role metadata or tool facts into its output.
                messages = apply_assistant_message_delta(row.get("messages"), candidate,
                    **({"required_reasoning_indices": required_reasoning} if assistant_delta else {}))
            except ValueError as error:
                return row, str(error)
            candidate = {**deepcopy(payload), "messages": messages}
        if cot_delta and set(candidate) == {"reasoning", "answer"}:
            # The model edits only its answer. The program owns the immutable
            # question, article and tools, including exact literal escapes.
            candidate = {**deepcopy(payload), **deepcopy(candidate)}
        if set(candidate) != set(payload):
            return row, "review_repair_invalid_schema"
        updated = {**deepcopy(row), **deepcopy(candidate)}
        if target == "cot":
            _sync_cot_messages(updated)
        if canonical(_immutable_tool_facts(updated)) != canonical(_immutable_tool_facts(row)):
            return row, "review_repair_tool_facts_changed"
        if ((self.recipe.get("qa_agent_memory_version") == 1 or assistant_delta)
                and canonical(_automatic_repair_task(target, updated)) != canonical(_automatic_repair_task(target, row))):
            return row, "review_repair_learner_task_changed"
        preserve_provider_reasoning(target, row, updated, origin="prompt_revision")
        if target in PREFERENCE_TARGETS and self.recipe.get("claim_review_transport_version") == 1:
            from ..domain.preference_feedback import preference_reasoning_issue
            if issue := preference_reasoning_issue(row, updated):
                return row, issue
        if reasoning_revision and target in {"sft", "multiturn", "agent"}:
            from lib.domain.turn_review import reasoning_text
            if any(not reasoning_text(updated["messages"][index]) for index in required_reasoning):
                return row, "review_repair_reasoning_missing"
        if assistant_delta:
            updates = value["record"]["assistant_updates"]
            audits = []
            for update in updates:
                index = update["message_index"]
                if update.get("reasoning_content"):
                    updated["reasoning_origin"] = "prompt_revision"
                    for turn in updated.get("turn_reviews", []):
                        if isinstance(turn, dict) and turn.get("end_message_index") == index:
                            turn["reasoning_origin"] = "prompt_revision"
                    audits.append({"message_index": index, "reasoning_origin": "prompt_revision",
                        "scope": "model_authored_task_explanation_not_native_reasoning",
                        "content_sha256": digest(updated["messages"][index]),
                        "reasoning_trace": deepcopy(update.get("reasoning_trace"))})
            if audits:
                updated["reasoning_revision_audits"] = [*deepcopy(row.get("reasoning_revision_audits", [])), *audits]
        if issue := (_payload_issue(target, updated) or _row_training_surface_issue(target, updated, self.recipe)):
            return row, issue
        if evidence["source_kind"] == "document":
            spans = _evidence_quote_spans(evidence, value.get("quotes", []))
            if spans is None:
                return row, "review_repair_source_quotes_missing"
            updated["quotes"], updated["quote_spans"] = value["quotes"], spans
        updated["repair_parent_sha256"] = digest(row)
        if _authored_cot(target, row) and canonical(candidate) != canonical(payload):
            updated["reasoning_trace_status"] = "superseded_by_repair"
        return updated, None

    def _review_repair_item(self, item):
        from lib.bootstrap.automatic_review import automatic_review
        return automatic_review(self).execute(item)

    def review_repair_collections(self, collections):
        from lib.bootstrap.automatic_review import review_collections
        return review_collections(self).execute(collections)

    def skips_post_generation_review(self, target):
        from ..domain.repair import skips_post_generation_review
        return skips_post_generation_review(self.recipe, target)

    def _split_review_rows(self, reviewed, collections=None, *, targets=None):
        result = dict(collections or {target: [] for target in TARGETS})
        spools = {target: RowSpool(self.path / "stage-results" / f"review-{target}.jsonl")
                  for target in (self.recipe["targets"] if targets is None else targets)}
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
        from lib.bootstrap.automatic_review import repair_branch
        return repair_branch(self).execute()
