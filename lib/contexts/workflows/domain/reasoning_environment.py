"""Version 5 review state and deterministic applicability, without choosing a thought order."""
from copy import deepcopy

from .reasoning_checklist import DEFAULT_REASONING_RULES, checklist_digest


def compile_requirements(rules, phase):
    if phase not in {"prefix", "final"}:
        raise ValueError("invalid_reasoning_phase")
    requirements = []
    default_scope = next(r for r in DEFAULT_REASONING_RULES if r["id"] == "scope")
    for rule in rules:
        # Only the exact shipped rule has this known conditional meaning. Never
        # infer applicability from arbitrary user prose, model votes or rule IDs.
        if rule["requirement"] == default_scope["requirement"]:
            parts = [
                ("answer", "答案须服从推理最终成立的条件、数值和边界", ["final"]),
                ("uncertainty", "无法确认的关键点保留未解决，不暴露后台规则", ["prefix", "final"]),
                ("prefix", "前缀阶段检查拟结论及待作答范围，不假装已经有答案", ["prefix"]),
            ]
        else:
            parts = [("body", rule["requirement"], rule.get("phases", ["prefix", "final"]))]
        for name, text, phases in parts:
            phases = [p for p in phases if p in rule.get("phases", ["prefix", "final"])]
            requirements.append({"id": rule["id"] + ":" + name, "rule_id": rule["id"],
                "requirement": text, "phases": phases, "active": phase in phases,
                "allow_na": rule["allow_na"],
                "applicability_reason": "phase_matches" if phase in phases else "different_phase"})
    return requirements


def review_environment(workspace, *, role, remaining_turns):
    return {
        "protocol_version": 5, "runtime_revision": "evidence-recovery-5", "phase": workspace.phase, "role": role,
        "candidate_sha256": checklist_digest(workspace.candidate), "binding": workspace.binding,
        "requirements": deepcopy(workspace.requirements), "pending": workspace.pending(),
        "committed_rule_ids": list(workspace.checks), "committed_anchor_ids": list(workspace.claims),
        "source_scope": {"coverage": "provided_passages_only", "whole_document_verified": False,
                         "source_ids": [p["id"] for p in workspace.passages],
                         "search_absence_is_proof": False},
        "permissions": {"edit_candidate": False, "edit_requirements": False, "waive_required_item": False,
                        "choose_tool_order": True, "declare_final_pass": False},
        "feedback_categories": ["tool_input_error", "candidate_defect", "evidence_unresolved", "evidence_recorded", "committed_record_conflict", "conditional_calculation"],
        "reference_policy": "Use inspect_claim for exact adjacent context. compare_claim_ratio proves arithmetic conditional on your selected operands, not their semantic binding. Explain competing antecedents; if selection requires a new premise, record uncertain. Never borrow the author's inference as source evidence.",
        "scope_policy": "Match the author's actual quantifier and scope using the complete candidate. 'Not always' is not 'never'; a locally separable example does not disprove 'no universal splitting rule'. Check existing qualifications before alleging an omitted condition. Distinguish an inexact quotation from an invalid conclusion; one does not automatically prove the other. A counterexample must contradict the proposition actually asserted, not a stronger substitute.",
        "calculation_policy": "metric_kind and operand_scale describe INPUT operands: counts use quantity/native, even when percent_of outputs percent. baseline/value/reported must be decimal strings, with no %, commas or words. reported_quote must be in this anchor. For verbal/approximate ratios without a literal reported number, use compare_claim_ratio; never invent 0.5 for 'about half' or copy a percent from another anchor. A numeric receipt does not decide approximate semantics.",
        "claim_submission_contract": {"exact_body_citation": "Any supplied claim anchor ID is a registered exact body span. Use evidence=[{citation_id: anchor_id}] instead of retyping it. interpretation may use context_citation_id instead of context_quote; full contextual binding is still validated.",
            "scope_true": "counterexample must contain a nonempty substantive condition/scope test. This applies to supported, unsupported, uncertain and not_claim alike.",
            "numeric_true": "Provide calculations OR a nonempty no_calculation_reason. This never waives factual verification.",
            "rule_citations": "Only candidate reasoning/answer prove rule compliance; source quotations belong in claim evidence. Active observations remain mandatory."},
        "remaining_turns": remaining_turns,
        "completion": "All active requirements and located claims need bound evidence; unresolved is not pass.",
        "new_evidence_required": "Do not repeat committed checks or already supplied reads. Use a tool only to resolve a named uncertainty.",
    }
