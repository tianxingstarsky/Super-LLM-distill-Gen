"""Incomplete material and changed drafts cannot inherit old positive evidence."""
from copy import deepcopy
import json

import pytest

from lib.contexts.workflows.domain.review_source_inventory import (
    source_inventory, text_sha256, evidence_rules, reusable_prefix, review_context_identity,
)
from lib.contexts.workflows.domain.review_surface_contract import observable_surface_defects
from lib.contexts.workflows.domain.evidence_scoped_review import EvidenceScopedReview
from lib.contexts.workflows.domain.reasoning_checklist import checklist_digest, validate_reasoning_checklist
from lib.contexts.workflows.application.check_reasoning import CheckReasoning


RULES = [{"id": "task", "requirement": "Solve the actual task with supported facts.", "allow_na": False}]
BODY = {"reasoning": "The rule applies only under the stated assumption.", "answer": "Use that rule under its assumption."}
CONTEXT = {"prompt": [{"role": "user", "content": "How should this rule be used under the specified assumption?"}],
           "source": {"text": "The rule is conditional."}}
CONFIG = {"version": 6, "rules": RULES, "evidence_policy": 1}


def test_missing_candidate_citation_reports_exact_rule_without_accepting_it():
    workspace = EvidenceScopedReview(RULES, CONTEXT, BODY, phase="final")
    submission = {"covered_sections": [s["id"] for s in workspace.sections], "findings": [],
                  "checks": [{"rule_id": "task", "status": "met", "reason": "Compare the claim to the source.",
                              "evidence": [{"source": "source.text", "quote": "The rule is conditional."}]}]}
    original = deepcopy(submission)
    rejected = workspace.execute("submit_semantic_review", submission)
    assert rejected["issue"] == "rule_citation_not_in_body"
    detail = next(d for d in rejected["recovery"]["details"] if d.get("message") == "missing_candidate_evidence")
    assert detail["path"] == "$.checks[0].evidence" and detail["rule_id"] == "task"
    assert detail["available_body_sections"] == [s["id"] for s in workspace.sections]
    assert submission == original and workspace.candidate == BODY and not workspace.snapshot()["passed"]
    submission["checks"][0]["evidence"].append({"citation_id": workspace.sections[0]["id"]})
    assert workspace.execute("submit_semantic_review", submission)["ok"]
    assert workspace.submission["checks"][0]["status"] == "met"
    assert workspace.submission["checks"][0]["evidence"][0] == original["checks"][0]["evidence"][0]


def test_feedback_keeps_literal_source_check_and_locates_unexpected_field():
    workspace = EvidenceScopedReview(RULES, CONTEXT, BODY, phase="final")
    submission = {"covered_sections": [s["id"] for s in workspace.sections], "findings": [],
                  "checks": [{"rule_id": "task", "status": "met", "reason": "Review.", "reasoning_note": "extra",
                              "evidence": [{"citation_id": workspace.sections[0]["id"]},
                                           {"source": "source.text", "quote": "An invented statement."}]}]}
    result = workspace.execute("submit_semantic_review", submission)
    assert result["issue"] == "invalid_rule_review"
    details = result["recovery"]["details"]
    assert any(d.get("unexpected_fields") == ["reasoning_note"] and d["path"] == "$.checks[0]" for d in details)
    assert any(d["path"] == "$.checks[0].evidence[1]" and d.get("diagnosis") == "quotation_not_present" for d in details)
    del submission["checks"][0]["reasoning_note"]
    assert workspace.execute("submit_semantic_review", submission)["issue"] == "rule_citation_not_in_body"
    assert not workspace.snapshot()["passed"]


def test_new_tool_schema_does_not_mutate_historical_or_other_workspaces():
    from lib.contexts.workflows.domain.semantic_review import semantic_tools
    before = deepcopy(semantic_tools())
    workspace = EvidenceScopedReview(RULES, CONTEXT, BODY, phase="final")
    tools = workspace.tools()
    checks = tools[0]["function"]["parameters"]["properties"]["checks"]["items"]["properties"]
    assert checks["rule_id"]["enum"] == ["task"]
    assert "enum" not in checks["reason"]
    assert semantic_tools() == before


@pytest.mark.parametrize("citation", [7, None, {"source": [], "quote": "bad"}, {"source": "reasoning", "quote": 7}])
def test_malformed_citation_returns_feedback_without_crashing(citation):
    workspace = EvidenceScopedReview(RULES, CONTEXT, BODY, phase="final")
    submission = {"covered_sections": [s["id"] for s in workspace.sections], "findings": [],
                  "checks": [{"rule_id": "task", "status": "met", "reason": "Review.", "evidence": [citation]}]}
    result = workspace.execute("submit_semantic_review", submission)
    assert not result["ok"] and result["issue"] == "rule_citation_not_in_body"
    assert any(d["path"] == "$.checks[0].evidence[0]" for d in result["recovery"]["details"])
    assert not workspace.snapshot()["passed"]


def complete_context():
    text = "Page one.\nPage two."
    return {"source": {"text": text, "coverage_manifest": {"version": 1, "text_sha256": text_sha256(text),
        "original_sha256": "a" * 64, "page_count": 2, "pages": [
            {"page": 1, "start": 0, "end": 10, "text_sha256": text_sha256(text[:10])},
            {"page": 2, "start": 10, "end": len(text), "text_sha256": text_sha256(text[10:])}]}}}


def test_page_manifest_preserves_uncertainty_and_checks_actual_text():
    assert source_inventory(CONTEXT)["coverage"] == "unknown"
    context = complete_context()
    assert source_inventory(context)["coverage"] == "all_text_pages"
    assert not source_inventory(context)["original_file_verified"]
    assert source_inventory({**context, "source_line_scope": "selected"})["coverage"] == "selected_ranges"
    context["source"]["text"] += "tampered"
    with pytest.raises(ValueError, match="source_coverage_manifest_invalid"):
        source_inventory(context)


@pytest.mark.parametrize("mutation", [
    lambda m: m["pages"].pop(), lambda m: m["pages"][1].update(page=3),
    lambda m: m["pages"][1].update(start=9), lambda m: m["pages"][0].update(text_sha256="b" * 64),
])
def test_missing_duplicate_or_changed_pages_cannot_claim_whole_text(mutation):
    context = complete_context(); mutation(context["source"]["coverage_manifest"])
    with pytest.raises(ValueError, match="source_coverage_"):
        source_inventory(context)


def test_prefix_reuse_requires_same_entire_context_rules_and_reasoning():
    rules = evidence_rules(RULES)
    receipt = {"passed": True, "phase": "prefix", "evidence_policy": 1,
               "candidate_sha256": checklist_digest({"reasoning": BODY["reasoning"]}),
               "rules_sha256": checklist_digest(rules), "review_context_sha256": review_context_identity(CONTEXT)}
    assert reusable_prefix(receipt, CONTEXT, BODY, rules)
    for changed in ({**BODY, "reasoning": BODY["reasoning"] + " changed"},):
        assert reusable_prefix(receipt, CONTEXT, changed, rules) is None
    assert reusable_prefix(receipt, {**CONTEXT, "source": "changed"}, BODY, rules) is None
    assert reusable_prefix(receipt, {**CONTEXT, "prompt": []}, BODY, rules) is None
    assert reusable_prefix(receipt, CONTEXT, BODY, rules[:-1]) is None
    assert reusable_prefix({**receipt, "passed": False}, CONTEXT, BODY, rules) is None


class Port:
    def __init__(self):
        self.calls, self.receipts = [], {}
        self.model_identity = "frozen-model-and-prompt"
    def prepare_reasoning_context(self, value): return deepcopy(value)
    def reasoning_review_identity(self): return self.model_identity
    def checkpoint_has(self, key): return checklist_digest(key) in self.receipts
    def checkpoint(self, key, action):
        identity = checklist_digest(key)
        if identity not in self.receipts: self.receipts[identity] = action()
        return deepcopy(self.receipts[identity])
    def check_reasoning_cancel(self): pass
    def reasoning_progress(self, *args): pass
    def reasoning_tool_turn(self, key, role, messages, tools):
        self.calls.append((key, role, messages))
        task = json.loads(messages[-1]["content"])
        sections = task["environment"]["sections"]
        checks = [{"rule_id": r["id"], "status": "met", "evidence": [{"citation_id": sections[0]["id"]}],
                   "reason": "A protocol fixture, not a semantic correctness verdict."} for r in task["rules"]]
        submission = {"covered_sections": [s["id"] for s in sections], "checks": checks, "findings": []}
        return {"role": "assistant", "tool_calls": [{"id": str(len(self.calls)), "type": "function",
                 "function": {"name": "submit_semantic_review", "arguments": json.dumps(submission)}}]}


def test_completed_review_reuses_receipt_but_changed_inputs_require_fresh_review():
    port = Port(); checker = CheckReasoning(port, CONFIG)
    first = checker.execute(["a", 0], CONTEXT, BODY, phase="final")
    assert first["passed"] and len(port.calls) == 2
    assert checker.execute(["b", 1], CONTEXT, BODY, phase="final")["reused_review_receipt"]
    assert len(port.calls) == 2
    assert checker.execute(["a"], {**CONTEXT, "source": "Different evidence"}, BODY, phase="final")["passed"]
    assert len(port.calls) == 4
    assert checker.execute(["a"], CONTEXT, {**BODY, "answer": "Changed answer."}, phase="final")["passed"]
    assert len(port.calls) == 6
    port.model_identity = "different-reviewer"
    checker.execute(["a"], CONTEXT, BODY, phase="final")
    assert len(port.calls) == 8
    assert port.calls[0][2][0]["role"] == "system"
    assert "Reference evidence only" in port.calls[0][2][0]["content"]
    assert "candidate" not in port.calls[0][2][0]["content"]


def test_new_contract_requires_language_provenance_and_scope_separately():
    workspace = EvidenceScopedReview(evidence_rules(RULES), CONTEXT, BODY, phase="final")
    assert {r["id"] for r in workspace.rules} == {"task", "evidence_scope", "learner_surface", "answer_language"}
    prefix = EvidenceScopedReview(evidence_rules(RULES), CONTEXT, {"reasoning": BODY["reasoning"]}, phase="prefix")
    assert "answer_language" not in {r["id"] for r in prefix.rules}
    bad = {"covered_sections": [s["id"] for s in workspace.sections], "findings": [], "checks": []}
    assert workspace.validate_review(bad) == "rule_coverage_missing"


def test_factual_dispute_cannot_dismiss_using_only_candidates_own_claim():
    w = EvidenceScopedReview(evidence_rules(RULES), CONTEXT, BODY, phase="final", disputes={"findings": [{"id": "f", "kind": "fact"}]})
    value = {"resolutions": [{"finding_id": "f", "decision": "reviewer_error", "reason": "candidate says so",
        "added_premises": [], "evidence": [{"source": "reasoning", "quote": BODY["reasoning"]}]}]}
    assert w.validate_resolution(value) == "factual_dismissal_requires_external_evidence"
    value["resolutions"][0]["evidence"].append({"source": "source.text", "quote": CONTEXT["source"]["text"]})
    assert w.validate_resolution(value) is None


def test_language_miss_and_hidden_source_narration_block_before_paid_review():
    port = Port()
    body = {"reasoning": "资料也明确说，这些字段可以分开。", "answer": "这是中文回答。" * 30}
    audit = CheckReasoning(port, CONFIG).execute(["regression"], CONTEXT, body, phase="final")
    assert not audit["passed"] and audit["issue"] == "reasoning_agent_found_gap" and not port.calls
    assert {f["reason"] for f in audit["findings"]} == {"hidden_source_narration", "answer_language_mismatch_en"}
    assert body["reasoning"] == "资料也明确说，这些字段可以分开。"


@pytest.mark.parametrize("user", [
    "Please translate this passage into Chinese and explain the result in Chinese.",
    "资料：\nThe rule is conditional.\n用户消息：\n请解释这段话。",
    "请解释‘资料也明确说’这句话为什么不好。",
])
def test_translation_reading_and_quoted_wording_are_not_source_leakage(user):
    body = {"reasoning": "资料也明确说，这些字段可以分开。", "answer": "这是中文回答。" * 30}
    if "translate" in user:
        body["reasoning"] = "The requested translation preserves the meaning."
    assert observable_surface_defects({"prompt": [{"role": "user", "content": user}]}, body, "final") == []


def test_policy_is_opt_in_and_legacy_config_unchanged():
    old = {"version": 6, "rules": RULES}
    assert validate_reasoning_checklist(old) == old
    assert validate_reasoning_checklist(CONFIG) == CONFIG
    for version in (True, 0, 3, "1"):
        with pytest.raises(ValueError): validate_reasoning_checklist({**CONFIG, "evidence_policy": version})


def test_pdf_layout_search_returns_original_span_and_keeps_literal_validation():
    context = {"source": {"text": "The Input-\nfirst Approach generates instruc-\ntions."}}
    workspace = EvidenceScopedReview(RULES, context, BODY, phase="final")
    response = workspace.execute("search_sources", {"query": "Input-first Approach"})
    hit = response["hits"][0]
    assert hit["match_quote"] == "Input-\nfirst Approach" and hit["match_kind"] == "layout_navigation"
    assert workspace.execute("search_sources", {"query": "instructions"})["hits"][0]["match_quote"] == "instruc-\ntions"
    submission = {"covered_sections": [s["id"] for s in workspace.sections], "findings": [],
                  "checks": [{"rule_id": "task", "status": "met", "reason": "A tool protocol fixture, not proof of the statement.",
                              "evidence": [{"citation_id": workspace.sections[0]["id"]},
                                           {"source": "source.text", "quote": "Input-first Approach"}]}]}
    assert workspace.execute("submit_semantic_review", submission)["issue"] == "rule_citation_not_in_body"
    submission["checks"][0]["evidence"][1] = {"citation_id": hit["citation_id"]}
    assert workspace.execute("submit_semantic_review", submission)["ok"]
    assert workspace.submission["checks"][0]["evidence"][1] == {"source": "source.text", "quote": "Input-\nfirst Approach"}
    assert workspace.candidate == BODY


def test_source_span_cannot_replace_body_or_cross_unregistered_workspaces():
    workspace = EvidenceScopedReview(RULES, CONTEXT, BODY, phase="final")
    receipt = workspace.execute("read_source", {"source_id": "source.text", "start": 0, "length": 24})
    citation = {"citation_id": receipt["citation_id"]}
    submission = {"covered_sections": [s["id"] for s in workspace.sections], "findings": [],
                  "checks": [{"rule_id": "task", "status": "met", "reason": "Needs body evidence.", "evidence": [citation]}]}
    assert workspace.execute("submit_semantic_review", submission)["issue"] == "rule_citation_not_in_body"
    other = EvidenceScopedReview(RULES, CONTEXT, BODY, phase="final")
    assert other.execute("submit_semantic_review", submission)["issue"] == "unknown_or_conflicting_citation_id"
    again = workspace.execute("read_source", {"source_id": "source.text", "start": 0, "length": 24})
    assert again["citation_id"] == receipt["citation_id"] and again["reused_local_receipt"]


def test_finding_feedback_locates_original_basis_without_changing_negative_verdict():
    workspace = EvidenceScopedReview(RULES, CONTEXT, BODY, phase="final")
    submission = {"covered_sections": [s["id"] for s in workspace.sections],
                  "checks": [{"rule_id": "task", "status": "unmet", "reason": "A negative fixture.",
                              "evidence": [{"citation_id": workspace.sections[0]["id"]}]}],
                  "findings": [{"id": "f1", "rule_id": "task", "category": "defect", "kind": "fact",
                                "body": {"citation_id": workspace.sections[0]["id"]},
                                "basis": [{"source": "source.text", "quote": "Invented evidence."}],
                                "reason": "A negative fixture.", "impact": "Decision.", "missing_premise": ""}]}
    frozen = deepcopy(submission)
    response = workspace.execute("submit_semantic_review", submission)
    assert response["issue"] == "finding_unbound_evidence"
    assert any(d["path"] == "$.findings[0].basis[0]" for d in response["recovery"]["details"])
    assert submission == frozen and not workspace.snapshot()["passed"]
    hit = workspace.execute("search_sources", {"query": "The rule is conditional."})["hits"][0]
    submission["findings"][0]["basis"][0] = {"citation_id": hit["citation_id"]}
    assert workspace.execute("submit_semantic_review", submission)["ok"]
    assert workspace.snapshot()["material_defects"]
    assert workspace.submission["checks"][0]["status"] == "unmet"


@pytest.mark.parametrize("text,query", [("It is not safe.", "It is safe."), ("Use 100 units.", "Use 10 units."), ("input-first", "inputfirst")])
def test_layout_navigation_never_drops_negation_numbers_or_inline_hyphens(text, query):
    workspace = EvidenceScopedReview(RULES, {"source": {"text": text}}, BODY, phase="final")
    assert workspace.execute("search_sources", {"query": query})["hits"] == []


def test_resolution_requires_exact_frozen_id_and_diagnoses_extra_fields():
    from lib.contexts.workflows.domain.semantic_review import semantic_tools
    legacy = deepcopy(semantic_tools(resolving=True))
    workspace = EvidenceScopedReview(RULES, CONTEXT, BODY, phase="final",
                                    disputes={"findings": [{"id": "independent_check:f1", "kind": "scope"}]})
    value = {"resolutions": [{"finding_id": "f1", "decision": "confirmed_defect", "rule_id": "task",
                             "evidence": [{"source": "reasoning", "quote": BODY["reasoning"]}],
                             "reason": "The original negative finding stands.", "added_premises": []}]}
    frozen = deepcopy(value)
    outcome = workspace.execute("resolve_disputes", value)
    details = outcome["recovery"]["details"]
    assert outcome["issue"] == "invalid_resolution"
    assert any(d["path"] == "$.resolutions[0].finding_id" and 'independent_check:f1' in d['message'] for d in details)
    assert any(d["path"] == "$.resolutions[0]" and 'rule_id' in d['message'] for d in details)
    assert value == frozen and workspace.submission is None
    value["resolutions"][0]["finding_id"] = "independent_check:f1"
    del value["resolutions"][0]["rule_id"]
    assert workspace.execute("resolve_disputes", value)["ok"]
    assert workspace.submission["resolutions"][0]["decision"] == "confirmed_defect"
    assert semantic_tools(resolving=True) == legacy


@pytest.mark.parametrize("negative", [False, True])
def test_completed_role_is_reused_after_other_role_interrupts_without_resampling(negative, monkeypatch):
    from collections import Counter
    from lib.contexts.workflows.application import review_semantic_reasoning as module
    counts = Counter()
    def run(port, key, role, workspace, context, phase):
        label = 'resolution' if workspace.disputes is not None else 'self_check' if role == 'generation' else 'independent_check'
        counts[label] += 1
        if label == 'independent_check' and counts[label] == 1:
            return {'passed':False, 'assessment':{}, 'verification':{}, 'findings':[], 'issue':'transport_interrupted'}
        if label == 'resolution':
            receipt = {'resolutions':[{'finding_id':f['id'], 'decision':'confirmed_defect',
                       'evidence':[{'source':'reasoning','quote':BODY['reasoning']}],
                       'reason':'Keep the original finding.', 'added_premises':[]} for f in workspace.disputes['findings']]}
            assert workspace.execute('resolve_disputes',receipt)['ok']
            return workspace.snapshot()
        checks=[{'rule_id':r['id'],'status':'met','reason':'Protocol fixture.',
                 'evidence':[{'source':'reasoning','quote':BODY['reasoning']}]} for r in workspace.rules]
        findings=[]
        if label=='self_check' and negative:
            checks[0]['status']='unmet'
            findings=[{'id':'f1','rule_id':checks[0]['rule_id'],'category':'defect','kind':'fact',
                       'body':{'source':'reasoning','quote':BODY['reasoning']},
                       'basis':[{'source':'source.text','quote':CONTEXT['source']['text']}],
                       'reason':'A negative protocol fixture.','impact':'Blocks the answer.','missing_premise':''}]
        assert workspace.execute('submit_semantic_review',{'checks':checks,'findings':findings,
                    'covered_sections':[s['id'] for s in workspace.sections]})['ok']
        return workspace.snapshot()
    monkeypatch.setattr(module,'run_reasoning_session',run)
    port=Port(); reviewer=module.ReviewSemanticReasoning(port, CONFIG)
    first=reviewer.execute(['first'],CONTEXT,BODY,phase='final')
    assert first['issue']=='reasoning_agent_evidence_unresolved'
    second=reviewer.execute(['resume'],CONTEXT,BODY,phase='final')
    assert counts['self_check']==1 and counts['independent_check']==2
    assert second['self_check_session']['reused_role_receipt']
    assert second['passed'] is (not negative)
    if negative:
        assert second['issue']=='reasoning_agent_found_gap' and counts['resolution']==1
        assert second['self_check']['checks'][0]['status']=='unmet'
    frozen_counts=counts.copy()
    assert reviewer.execute(['again'],CONTEXT,BODY,phase='final')['reused_review_receipt']
    assert counts==frozen_counts


@pytest.mark.parametrize('corruption', ['missing_assessment', 'lost_findings', 'failed_verification'])
def test_corrupt_complete_role_receipt_is_not_silently_resampled(corruption):
    from lib.contexts.workflows.application.review_semantic_reasoning import ReviewSemanticReasoning, semantic_role_cache_key
    port = Port()
    key = semantic_role_cache_key(CONFIG, CONTEXT, BODY, 'final', 'self_check', port.reasoning_review_identity())
    workspace = EvidenceScopedReview(evidence_rules(RULES), CONTEXT, BODY, phase='final')
    checks = [{'rule_id':r['id'],'status':'met','evidence':[{'source':'reasoning','quote':BODY['reasoning']}],
               'reason':'Protocol fixture.'} for r in workspace.rules]
    assessment = {'checks':checks,'findings':[], 'covered_sections':[s['id'] for s in workspace.sections]}
    assert workspace.execute('submit_semantic_review', assessment)['ok']
    receipt = workspace.snapshot()
    if corruption == 'missing_assessment': receipt['assessment'] = {}
    elif corruption == 'lost_findings': receipt.pop('findings')
    else: receipt['verification']['passed'] = False
    port.receipts[checklist_digest(key)] = receipt
    with pytest.raises(ValueError, match='completed_semantic_role_receipt_invalid'):
        ReviewSemanticReasoning(port, CONFIG).execute(['resume'], CONTEXT, BODY, phase='final')
    assert not port.calls
