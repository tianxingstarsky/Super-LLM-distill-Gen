"""Score and repair one candidate without editing its user task or evidence.

The bounded loop, repeated-body detection, pending-review stop and exact receipt
binding are application policy. Provider protocols and source reads stay behind
AutomaticReviewPort. Every historical recipe branch retains its original keys.
"""
from __future__ import annotations
from copy import deepcopy
from .automatic_ports import AutomaticReviewPort
from ..domain.repair import validate_review_repair
from ..domain.candidate_content import (_manual_patch, _payload_issue, _immutable_tool_facts,
    candidate_digest as digest)
from ..domain.automatic_rules import _authored_warmup_requirements
from ..domain.training_lineage import repaired_candidate_identity
from lib.domain.workflow_quality import canonical, accepted
from lib.contexts.workflows.contracts import PREFERENCE_TARGETS, preference_score_issue, rlaif_feedback_issue


class ReviewCandidate:
    def __init__(self, port: AutomaticReviewPort, recipe: dict):
        self._port = port
        self.recipe = recipe

    def execute(self, item):
        target, original = item["target"], item["record"]
        config = validate_review_repair(self.recipe["review_repair"])
        missing_dialogue = original.get("messages") is None or original.get("messages") == []
        historical_terminal = (self.recipe.get("multiturn_terminal_review_version") == 1
            and (original.get("reason") == "multiturn_natural_end_insufficient_turns"
                 or original.get("reason") == "multiturn_turn_failed_after_repair" and missing_dialogue))
        contract_output_terminal = (type(self.recipe.get("multiturn_failure_audit_version")) is int
            and self.recipe["multiturn_failure_audit_version"] == 1
            and original.get("reason") == "multiturn_contract_output_invalid" and missing_dialogue)
        if (target == "multiturn" and original.get("status") == "quarantined"
                and (historical_terminal or contract_output_terminal)):
            # A terminal single turn or failed generation without a dialogue
            # cannot become a multi-turn item by editing assistant text.
            # Preserve the actual cause and any partial-turn diagnostics;
            # missing input is not an API/parse failure or a quality verdict.
            # failed_generation is diagnostic text, never a replacement for
            # the absent main messages or an implicit training candidate.
            row = deepcopy(original)
            try:
                candidate_hash = digest(self._port.payload(target, row))
            except (KeyError, TypeError, ValueError):
                candidate_hash = None
            row["review_repair"] = {"mode": config["mode"], "status": "rejected", "score": None,
                "attempts": 0, "history": [], "node": "review", "terminal": True,
                "assessment": "not_performed_terminal_generation", "reason": original["reason"],
                "original_reason": original["reason"], "candidate_sha256": candidate_hash,
                "config_sha256": digest(config)}
            row["_review_target"] = target
            return [row]
        if target == "cot" and config.get("native_regeneration") is True:
            return self._port.regenerate_native(item)
        row = _manual_patch(target, original, item)
        if item.get("revision_context"):
            row["revision_context"] = deepcopy(item["revision_context"])
            row["id"] = repaired_candidate_identity(self._port.run_id, target, row, item["revision_context"],
                format_lineage=(self.recipe.get("training_format_lineage_version") == 1
                                and self._port.authenticated_revision))
            if row.get("qa_contract"):
                row["qa_contract"] = deepcopy(row["qa_contract"])
                row["qa_contract"]["revision_context"] = deepcopy(item["revision_context"])
        instruction = item.get("instruction", "")
        receipt = {"mode": config["mode"], "status": "pending", "score": None,
                   "attempts": 0, "history": [], "node": "review", "score_threshold": config["score_threshold"]}
        row["review_repair"] = receipt
        evidence = self._port.evidence(row)
        issue = _payload_issue(target, row)
        if canonical(_immutable_tool_facts(row)) != canonical(_immutable_tool_facts(original)):
            issue = "review_repair_tool_facts_changed"
        # An explicit correction cannot rehabilitate forged trajectories or
        # missing facts by changing the text. These require new verified input.
        blocked = any(token in original.get("reason", "") for token in (
            "source_snapshot", "tool_error", "simulated_tool", "unverified", "trajectory", "orphan_tool", "missing_tool"))
        if ((issue in {"training_surface_hidden_source_reference", "training_surface_internal_process_reference"}
                or self.recipe.get("knowledge_application_voice_version") == 3
                and issue == "training_surface_knowledge_source_narration")
                and config["mode"] == "auto" and config["max_rounds"] > 0
                and (self.recipe.get("reasoning_revision_protocol_version") == 1
                     or item.get("revision_context") and self._port.authenticated_revision)
                and evidence is not None and not blocked):
            # This selected parent has already been authenticated as an
            # explicit correction. A leaked old body is editable input, not
            # an approval: repair and final surface checks must still pass.
            receipt["initial_surface_issue"] = issue
            issue = None
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
                        or canonical(self._port.payload(target, row)) != canonical(self._port.payload(target, original))):
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
            row, receipt = self._review_automatically(target, original, row, evidence, instruction, item, config, receipt)
        if target == "cot" and receipt["status"] == "accepted" and row["status"] == "eligible":
            row.update(self._port.independent_cot_assessment(row, human=config["mode"] == "human"))
        # Binding the receipt to exactly the content that was assessed prevents
        # cached/manual approval from being reused after another content edit.
        try:
            receipt["candidate_sha256"] = digest(self._port.payload(target, row))
        except (ValueError, KeyError, TypeError):
            receipt["candidate_sha256"] = None
        receipt["config_sha256"] = digest(config)
        row["_review_target"] = target
        return [row]

    def _review_automatically(self, target, original, row, evidence, instruction, item, config, receipt):
        check = None
        audit_enabled = (type(self.recipe.get("reasoning_distillation_audit_version")) is int
            and self.recipe["reasoning_distillation_audit_version"] == 1
            and bool(_authored_warmup_requirements(self.recipe, target, row)))
        assessed_candidates = []
        # Each entry belongs to this one item's fixed evidence, prompt and
        # instruction. A repair that returns to an already rejected body
        # has made no progress; another paid score cannot add new evidence.
        scored_candidates = {}
        score_fields = ("preference", "rlaif", "learner_context_check")
        contrastive = target in PREFERENCE_TARGETS and self.recipe.get("preference_design_version") in {1, 2}
        if contrastive:
            score_fields += ("preference_assessment",)
        if self.recipe.get("claim_review_protocol_version") in {1, 2}:
            score_fields += ("claim_review", "reasoning_assessment_method", "reasoning_assessment_reference")
            if self.recipe.get("claim_refutation_release_version") == 1:
                score_fields += ("claim_release_decision",)
        elif audit_enabled:
            score_fields += ("reasoning_assessment_method", "reasoning_assessment_reference")
        force_repair = bool(item.get("revision_context")) and config["max_rounds"] > 0 and not contrastive
        if self.recipe.get("review_reference_reassessment_version") == 1 and config.get("reference_sources"):
            # Newly supplied verified facts may resolve an old uncertainty
            # without changing a single character of the training record.
            force_repair = False
        pair_scope = (digest({"candidate": self._port.payload(target, row), "evidence": evidence,
            "instruction": instruction, "design": self.recipe.get("preference_design"),
            "requirements": (row.get("revision_context") or {}).get("design_requirements", {})}) if contrastive else None)
        candidate_limit = config["max_rounds"] if force_repair else config["max_rounds"] + 1
        for attempt in range(candidate_limit):
            key = [row["id"], target, "review_repair", attempt]
            if contrastive:
                key = [row["id"], "shared_preference_v1", pair_scope, attempt]
            if force_repair or attempt:
                row, issue = self._port.repair(target, row, evidence, key,
                    check or original.get("review_repair", {}).get("verdict") or original.get("judge")
                        or {"reason": instruction or original.get("reason", "")}, instruction)
                receipt = row["review_repair"]
                receipt["attempts"] += 1
                if issue:
                    receipt["history"].append({"attempt": attempt, "repair_issue": issue})
                    check = {"reason": issue}
                    force_repair = False
                    continue
            candidate_hash = digest(self._port.payload(target, row))
            if candidate_hash in scored_candidates:
                check, score_metadata = scored_candidates[candidate_hash]
                for field in score_fields:
                    row.pop(field, None)
                row.update(deepcopy(score_metadata))
                score = min(check["scores"].values()) / 5
                receipt["history"].append({"attempt": attempt, "score": score,
                    "status": "rejected", "verdict": deepcopy(check),
                    "reused_assessment": True, "repair_issue": "review_repair_repeated_candidate"})
                receipt.update(status="rejected", score=score, verdict=deepcopy(check),
                    reason=check["reason"], stop_reason="review_repair_repeated_candidate")
                row.update(status="quarantined", reason="review_repair_exhausted")
                break
            check = self._port.score(target, row, evidence, key, instruction)
            if audit_enabled:
                from lib.domain.reasoning_candidates import assessment_from_verdict
                payload = deepcopy(self._port.payload(target, row))
                assessment_reference = row.get("reasoning_assessment_reference",
                    {"kind": "supplied_review_verdict", "attempt_key": key})
                if row.get("reasoning_assessment_method") == "llm_claim_review":
                    # Full per-turn checkpoint keys live in the separately
                    # authenticated claim receipt; a bounded reference must
                    # not truncate them or exceed the historic ref limit.
                    assessment_reference = {"kind": "llm_claim_review",
                        "reference_sha256": digest(assessment_reference),
                        "claim_review_sha256": digest(row["claim_review"])}
                assessment = assessment_from_verdict(payload, check,
                    evidence_ref=canonical({"assessment": assessment_reference,
                        "source_sha256": digest(evidence)}),
                    score_threshold=config["score_threshold"],
                    method=row.get("reasoning_assessment_method", "llm_review"),
                    **({"claim_review": row["claim_review"]}
                       if row.get("reasoning_assessment_method") == "llm_claim_review" else {}))
                assessed_candidates.append({"candidate": payload, "assessment": assessment})
            scored_candidates[candidate_hash] = (deepcopy(check),
                {field: deepcopy(row[field]) for field in score_fields if field in row})
            score = min(check["scores"].values()) / 5
            keep = accepted(check) and score >= config["score_threshold"]
            if target in PREFERENCE_TARGETS and preference_score_issue(row):
                keep = False
            receipt["history"].append({"attempt": attempt, "score": score,
                "status": "accepted" if keep else "rejected", "verdict": deepcopy(check),
                **({"claim_release_decision": deepcopy(row["claim_release_decision"])}
                   if self.recipe.get("claim_refutation_release_version") == 1
                   and "claim_release_decision" in row else {})})
            receipt.update(score=score, verdict=deepcopy(check), reason=check["reason"])
            force_repair = False
            if (self.recipe.get("claim_review_condition_contract_version") == 1
                    and (any(entry.get("review", {}).get("condition_guard", {}).get("needs_review")
                        for entry in row.get("claim_review", {}).get("assessments", []))
                        or contrastive and row.get("preference_assessment", {}).get("stop_reason") == "condition_support_pending_review")):
                # Missing support is not an error verdict. Keep the body
                # and raw scores; don't pay for speculative auto-rewrites.
                receipt.update(status="waiting_manual_review", reason="condition_support_pending_review",
                               stop_reason="condition_support_pending_review")
                receipt["history"][-1]["status"] = "waiting_manual_review"
                row.update(status="quarantined", reason="condition_support_pending_review")
                break
            if keep:
                receipt["status"] = "accepted"
                row.update(status="eligible", judge=deepcopy(check))
                row.pop("reason", None)
                if receipt["attempts"] > 0:
                    self._port.record_revision_style(target, original, row)
                break
        else:
            receipt["status"] = "rejected"
            row.update(status="quarantined", reason="review_repair_exhausted")
        if audit_enabled:
            from lib.domain.reasoning_candidates import select_reasoning_candidate
            selection = select_reasoning_candidate(assessed_candidates,
                {"candidate_limit": candidate_limit})
            # The existing score/repair node owns retries and early stopping.
            # Keep every assessed body, not just the last verdict. These
            # are feedback-conditioned revisions, not IID samples or RL.
            if ((selection["status"] == "accepted") != (receipt["status"] == "accepted")):
                raise ValueError("reasoning_distillation_selection_mismatch")
            selected = selection.pop("candidate")
            if selected is not None and digest(selected) != digest(self._port.payload(target, row)):
                raise ValueError("reasoning_distillation_selected_body_mismatch")
            selection.update(candidate_policy="bounded_feedback_revisions",
                training_method="supervised_distillation_data_only",
                stop_reason=receipt.get("stop_reason") or (
                    "first_passing_candidate" if selected is not None else "review_limit_reached"),
                final_candidate_sha256=digest(self._port.payload(target, row)),
                final_status=receipt["status"])
            selection["selection_status"] = selection["status"]
            selection["unused_assessment_slots"] = selection["remaining"]
            selection["remaining"] = 0  # This review invocation has stopped.
            selection["repair_attempts_consumed"] = receipt["attempts"]
            selection["status"] = ("accepted" if selected is not None else
                "waiting_manual_review" if receipt["status"] == "waiting_manual_review" else
                "rejected" if receipt.get("stop_reason") else "exhausted")
            if original.get("reasoning_distillation"):
                selection["parent_audit_sha256"] = digest(original["reasoning_distillation"])
            row["reasoning_distillation"] = selection
        return row, receipt
