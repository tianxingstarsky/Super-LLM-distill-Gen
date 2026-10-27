"""Two blind full-text reviews, then one bounded investigation of actual disputes."""
from copy import deepcopy

from ..domain.reasoning_checklist import checklist_digest
from ..domain.semantic_review import SemanticReviewWorkspace, POLICY, explicit_comparison_defects
from .reasoning_session import run_reasoning_session


def semantic_role_cache_key(config, context, body, phase, label, model_identity):
    """Bind a complete role decision to its actual inputs, never to a run number."""
    from ..domain.review_source_inventory import review_context_identity
    return ["semantic_role_receipt_v1", phase, label, checklist_digest(config),
            review_context_identity(context), checklist_digest(body), model_identity]


class ReviewSemanticReasoning:
    def __init__(self, port, config):
        self.port, self.config = port, config

    def _workspace(self, factory, rules, context, body, *, phase, disputes=None):
        workspace = factory(rules, context, body, phase=phase, disputes=disputes)
        if self.config.get("batch_submission_version") == 1:
            from ..domain.review_submission_batch import ReviewSubmissionBatch
            return ReviewSubmissionBatch(workspace)
        return workspace

    def execute(self, key, context, candidate, *, phase):
        context = self.port.prepare_reasoning_context(context)
        if self.config.get("evidence_policy") in (1, 2):
            from ..domain.review_source_inventory import review_context_identity
            cache_key = ["semantic_evidence_receipt_v1", phase, checklist_digest(self.config),
                         review_context_identity(context), checklist_digest(candidate), self.port.reasoning_review_identity()]
            if self.port.checkpoint_has(cache_key):
                receipt = self.port.checkpoint(cache_key, lambda: None)
                return {**receipt, "reused_review_receipt": checklist_digest(cache_key)}
            audit = self._execute(key, context, candidate, phase=phase)
            # Cache complete positive AND negative decisions. A transport/tool
            # failure keeps its per-turn checkpoints, not a terminal verdict.
            if audit.get("passed") or all(audit.get(label + "_session", {}).get("passed")
                    for label in ("self_check", "independent_check")) and (
                    not audit.get("resolution_session") or audit["resolution_session"].get("passed")):
                self.port.checkpoint(cache_key, lambda: deepcopy(audit))
            return audit
        return self._execute(key, context, candidate, phase=phase)

    def _execute(self, key, context, candidate, *, phase):
        body = {k: candidate[k] for k in ("reasoning", "answer") if k in candidate}
        rules = self.config["rules"]
        workspace_type = SemanticReviewWorkspace
        extras = {}
        if self.config.get("evidence_policy") in (1, 2):
            from ..domain.review_source_inventory import evidence_rules, source_inventory, review_context_identity
            from ..domain.evidence_scoped_review import EvidenceScopedReview
            rules = evidence_rules(rules)
            workspace_type = EvidenceScopedReview
            if self.config.get("evidence_policy") == 2:
                from ..domain.obligation_review import ObligationReview
                workspace_type = ObligationReview
            extras = {"evidence_policy": self.config["evidence_policy"], "source_inventory": source_inventory(context),
                      "review_context_sha256": review_context_identity(context)}
        audit = {"version": 6, "phase": phase, "passed": False, "rules": deepcopy(rules), "policy": deepcopy(POLICY),
                 "candidate_sha256": checklist_digest(body), "rules_sha256": checklist_digest(rules),
                 "context_sha256": checklist_digest(context), **extras}
        if self.config.get("evidence_policy") in (1, 2):
            from ..domain.review_surface_contract import observable_surface_defects
            if defects := observable_surface_defects(context, body, phase):
                findings = [{"id": "surface:" + str(i), "rule_id": d["rule_id"], "category": "defect",
                             "kind": d["kind"], "body": {"source": d["field"], "quote": d["quote"]},
                             "basis": [], "reason": d["issue"], "impact": "Learner-visible task/source contract", "missing_premise": ""}
                            for i, d in enumerate(defects)]
                return {**audit, "issue": "reasoning_agent_found_gap", "decision_basis": "observable_task_contract",
                        "findings": findings, "resolutions": [{"finding_id": f["id"], "decision": "confirmed_defect",
                        "reason": f["reason"], "evidence": [f["body"]], "added_premises": []} for f in findings]}
        if defects := explicit_comparison_defects(body):
            return {**audit, "issue": "reasoning_agent_found_gap", "decision_basis": "deterministic_comparison",
                    "deterministic_defects": defects}
        findings = []
        for role, label in (("generation", "self_check"), ("jev", "independent_check")):
            workspace = self._workspace(workspace_type, rules, context, body, phase=phase)
            role_key = (semantic_role_cache_key(self.config, context, body, phase, label, self.port.reasoning_review_identity())
                        if self.config.get("evidence_policy") in (1, 2) else None)
            if role_key and self.port.checkpoint_has(role_key):
                outcome = self.port.checkpoint(role_key, lambda: None)
                if (not isinstance(outcome, dict) or not outcome.get("passed")
                        or workspace.validate_review(outcome.get("assessment"))
                        or outcome.get("findings") != outcome["assessment"]["findings"]
                        or not outcome.get("verification", {}).get("passed")):
                    raise ValueError("completed_semantic_role_receipt_invalid")
                outcome = {**outcome, "reused_role_receipt": checklist_digest(role_key)}
            else:
                outcome = run_reasoning_session(self.port, [*key, "semantic_review_v1", workspace.binding, label],
                                                role, workspace, context, phase)
                if role_key and outcome.get("passed"):
                    self.port.checkpoint(role_key, lambda: deepcopy(outcome))
            audit[label], audit[label + "_session"] = outcome["assessment"], outcome
            audit[label + "_verification"] = outcome["verification"]
            if not outcome["passed"]:
                return {**audit, "issue": "reasoning_agent_evidence_unresolved"}
            for f in outcome["findings"]:
                findings.append({**deepcopy(f), "id": label + ":" + f["id"], "review": label})
        audit["findings"] = findings
        blocking = [f for f in findings if f["category"] != "presentation"]
        if not blocking:
            return {**audit, "passed": True, "issue": None, "resolutions": []}
        # Findings never disappear by resampling a positive full review. A new
        # session must address every exact allegation against the unchanged body.
        disputes = {"findings": blocking, "reviews": {k: audit[k] for k in ("self_check", "independent_check")}}
        workspace = self._workspace(workspace_type, rules, context, body, phase=phase, disputes=disputes)
        resolved = run_reasoning_session(self.port, [*key, "semantic_resolution_v1", workspace.binding],
                                         "jev", workspace, context, phase)
        audit["resolution_session"] = resolved
        if not resolved["passed"]:
            return {**audit, "issue": "reasoning_agent_evidence_unresolved"}
        decisions = resolved["assessment"]["resolutions"]
        audit["resolutions"] = decisions
        if self.config.get("evidence_policy") == 2:
            confirmed = {d["finding_id"] for d in decisions if d["decision"] == "confirmed_defect"}
            if any(f["id"] in confirmed and f["body"]["source"].startswith("history_") for f in findings):
                return {**audit, "issue": "reasoning_history_requires_repair"}
        if any(d["decision"] == "insufficient_evidence" for d in decisions):
            # Preserve confirmed defects too, but do not buy a rewrite that
            # would require inventing the missing premise.
            return {**audit, "issue": "reasoning_agent_evidence_unresolved"}
        if any(d["decision"] == "confirmed_defect" for d in decisions):
            return {**audit, "issue": "reasoning_agent_found_gap"}
        return {**audit, "passed": True, "issue": None}
