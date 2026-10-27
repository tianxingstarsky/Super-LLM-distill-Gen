"""Batch receipt scheduling against unchanged strict validators; no model API."""
from copy import deepcopy
import json

import pytest

from lib.contexts.workflows.domain.obligation_review import ObligationReview
from lib.contexts.workflows.domain.review_submission_batch import ReviewSubmissionBatch
from lib.contexts.workflows.domain.reasoning_checklist import validate_reasoning_checklist, checklist_digest
from lib.contexts.workflows.application.reasoning_session import run_reasoning_session
from lib.contexts.workflows.application.check_reasoning import CheckReasoning


RULES = [{"id": name, "requirement": "Check the actual claim and its conditions.", "allow_na": False}
         for name in ("task", "units", "argument")]
BODY = {"reasoning": "A-rated responses increased by 10 percentage points.", "answer": "The increase is 10 percentage points."}
CONTEXT = {"source": {"text": "Percentage rated A: 44.4% before, 54.4% after."}}
CITE = {"source": "reasoning", "quote": BODY["reasoning"]}


def workspace(disputes=None):
    return ReviewSubmissionBatch(ObligationReview(RULES, CONTEXT, BODY, phase="final", disputes=disputes))


def submission(w):
    return {"covered_sections": [s["id"] for s in w.sections], "findings": [],
            "checks": [{"rule_id": r["id"], "status": "met", "evidence": [CITE],
                        "reason": "Subtract the two cited A-rating percentages."} for r in w.rules],
            "risk_checks": [{"id": o["id"], "status": "supported", "reason": "The metric is a percentage.",
                "evidence": [{"source": o["field"], "quote": o["quote"]},
                             {"source": "source.text", "quote": CONTEXT["source"]["text"]}],
                "quantities": [{"operation": "percentage_points", "baseline": "44.4", "current": "54.4", "reported": "10"}]}
                for o in w.obligations]}


def finding():
    return {"id": "f-units", "rule_id": "units", "category": "defect", "kind": "units", "body": CITE,
            "basis": [{"source": "source.text", "quote": CONTEXT["source"]["text"]}],
            "reason": "A negative reviewer allegation retained for separate adjudication.",
            "impact": "Changes the claimed unit.", "missing_premise": ""}


class Port:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests, self.receipts = [], {}
    def prepare_reasoning_context(self, context): return deepcopy(context)
    def reasoning_review_identity(self): return "frozen-model-and-prompt"
    def checkpoint_has(self, key): return checklist_digest(key) in self.receipts
    def checkpoint(self, key, action):
        identity = checklist_digest(key)
        if identity not in self.receipts:
            self.receipts[identity] = deepcopy(action())
        return deepcopy(self.receipts[identity])
    def check_reasoning_cancel(self): pass
    def reasoning_progress(self, *args): pass
    def reasoning_tool_turn(self, key, role, messages, tools):
        def request():
            self.requests.append((role, deepcopy(messages)))
            reply = next(self.replies)
            if isinstance(reply, Exception): raise reply
            name, arguments = reply
            return {"role": "assistant", "content": None, "tool_calls": [{"id": str(len(self.requests)),
                "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}
        return self.checkpoint(key, request)


def run(w, port):
    return run_reasoning_session(port, [w.binding, "self"], "generation", w, CONTEXT, "final")


def test_one_response_covers_all_rules_risks_and_sections():
    w = workspace(); value = submission(w); original = deepcopy(value)
    p = Port([("submit_semantic_review", value)])
    result = run(w, p)
    assert result["passed"] and len(p.requests) == 1
    assert w.workspace.validate_review(result["assessment"]) is None
    assert value == original and w.candidate == BODY
    assert any(m["role"] == "system" and "ALL applicable" in m["content"] for m in p.requests[0][1])


def test_one_invalid_citation_preserves_siblings_and_requests_only_that_item():
    w = workspace(); complete = submission(w); first = deepcopy(complete)
    first["checks"][1]["evidence"] = [{"citation_id": "invented-section"}]
    p = Port([("submit_semantic_review", first),
              ("submit_semantic_review", {"checks": [complete["checks"][1]]})])
    result = run(w, p)
    assert result["passed"] and len(p.requests) == 2
    feedback = json.loads(next(m["content"] for m in reversed(p.requests[1][1]) if m["role"] == "tool"))
    plan = feedback["environment"]["batch_submission"]
    assert plan["mode"] == "missing_or_invalid_only"
    assert plan["pending"]["rule_ids"] == ["units"]
    assert plan["accepted_ids"]["checks"] == ["task", "argument"]
    assert not plan["pending"]["risk_ids"] and not plan["pending"]["anchor_ids"]
    assert result["assessment"]["risk_checks"] == complete["risk_checks"]


def test_missing_rule_is_a_gap_not_a_failed_semantic_vote():
    w = workspace(); complete = submission(w); first = deepcopy(complete)
    missing = first["checks"].pop()
    receipt = w.execute("submit_semantic_review", first)
    assert receipt["ok"] and not receipt["complete"]
    assert w.pending()["rule_ids"] == [missing["rule_id"]]
    assert w.execute("submit_semantic_review", {"checks": [missing]})["complete"]


def test_missing_finding_can_be_added_without_repeating_negative_check():
    w = workspace(); value = submission(w)
    value["checks"][1]["status"] = "unmet"
    assert not w.execute("submit_semantic_review", value)["complete"]
    assert w.pending()["rule_ids"] == []
    assert "relationships" in w.pending()["invalid_items"]
    assert w.execute("submit_semantic_review", {"findings": [finding()]})["complete"]
    assert w.snapshot()["material_defects"]
    assert w.snapshot()["assessment"]["checks"][1]["status"] == "unmet"


def test_negative_invalid_item_cannot_turn_positive_during_field_repair():
    w = workspace(); value = submission(w)
    value["checks"][1].update(status="unmet", evidence=[{"source": "reasoning", "quote": "Invented."}])
    value["findings"] = [finding()]
    w.execute("submit_semantic_review", value)
    replacement = deepcopy(value["checks"][1]); replacement.update(status="met", evidence=[CITE])
    receipt = w.execute("submit_semantic_review", {"checks": [replacement]})
    assert not receipt["complete"]
    assert receipt["entries"][0]["result"]["issue"] == "review_verdict_cannot_be_replaced"
    replacement["status"] = "unmet"
    assert w.execute("submit_semantic_review", {"checks": [replacement]})["complete"]
    assert w.snapshot()["findings"] == [finding()]


def test_inconsistent_completed_votes_stop_without_another_format_request():
    w = workspace(); value = submission(w); value["findings"] = [finding()]
    p = Port([("submit_semantic_review", value)])
    result = run(w, p)
    assert not result["passed"] and result["blocked"] and len(p.requests) == 1
    assert result["protocol_blocker"] == "committed_review_inconsistent"
    assert result["findings"] == [finding()]


@pytest.mark.parametrize("field", ["checks", "risk_checks"])
def test_duplicate_entries_do_not_get_first_wins_treatment(field):
    w = workspace(); value = submission(w)
    identity = w._identity(field, value[field][0])
    value[field].append(deepcopy(value[field][0]))
    response = w.execute("submit_semantic_review", value)
    assert not response["complete"] and identity not in w.records[field]
    corrected = submission(w)[field][0]
    assert w.execute("submit_semantic_review", {field: [corrected]})["complete"]


def test_committed_negative_is_immutable_and_unknown_identity_cannot_disappear():
    w = workspace(); value = submission(w); value["checks"][1]["status"] = "unmet"
    value["findings"] = [finding()]; value["checks"].pop()
    w.execute("submit_semantic_review", value)
    response = w.execute("submit_semantic_review", {"checks": [submission(w)["checks"][1]]})
    assert response["entries"][0]["result"]["issue"] == "committed_record_conflict"
    assert w.snapshot()["blocked"] and w.records["checks"]["units"]["status"] == "unmet"
    w = workspace(); value = submission(w); value["findings"] = [{"reason": "Unaddressable negative allegation."}]
    w.execute("submit_semantic_review", value)
    assert w.snapshot()["blocked"] and not w.execute("submit_semantic_review", {})["complete"]


def test_quantity_error_cannot_be_fixed_by_changing_quality_standard():
    w = workspace(); value = submission(w)
    value["risk_checks"][0]["quantities"][0]["reported"] = "11"
    receipt = w.execute("submit_semantic_review", value)
    assert not receipt["complete"]
    assert any(e["result"].get("issue") == "reported_quantity_not_in_candidate" for e in receipt["entries"])
    assert len(w.records["checks"]) == len(RULES)
    assert w.execute("submit_semantic_review", {"risk_checks": [submission(w)["risk_checks"][0]]})["complete"]


def test_interrupt_replays_completed_items_without_purchasing_them_again():
    w = workspace(); value = submission(w); missing = value["checks"].pop()
    p = Port([("submit_semantic_review", value), RuntimeError("connection interrupted"),
              ("submit_semantic_review", {"checks": [missing]})])
    with pytest.raises(RuntimeError, match="interrupted"): run(w, p)
    resumed = run(workspace(), p)
    assert resumed["passed"] and len(p.requests) == 3  # 1 success, 1 error, 1 gap fill
    feedback = json.loads(next(m["content"] for m in reversed(p.requests[-1][1]) if m["role"] == "tool"))
    assert feedback["environment"]["batch_submission"]["pending"]["rule_ids"] == [missing["rule_id"]]


def test_dispute_batch_preserves_negative_resolution_and_targets_remaining_finding():
    f1, f2 = finding(), {**finding(), "id": "f2"}
    w = workspace({"findings": [f1, f2], "reviews": {}})
    def decision(identity):
        return {"finding_id": identity, "decision": "confirmed_defect", "evidence": [CITE],
                "reason": "An existing claim is contradicted; retain the original allegation.", "added_premises": []}
    bad = decision("f2"); bad["added_premises"] = ["invent a condition"]
    r = w.execute("resolve_disputes", {"resolutions": [decision(f1["id"]), bad]})
    assert not r["complete"] and w.pending()["finding_ids"] == ["f2"]
    assert w.execute("resolve_disputes", {"resolutions": [decision("f2")]})["complete"]
    assert w.snapshot()["assessment"]["resolutions"][0] == decision(f1["id"])


def test_author_and_independent_reviewer_keep_separate_bulk_sessions():
    config = {"version": 6, "evidence_policy": 2, "batch_submission_version": 1, "rules": RULES}
    from lib.contexts.workflows.domain.review_source_inventory import evidence_rules
    w = ReviewSubmissionBatch(ObligationReview(evidence_rules(RULES), CONTEXT, BODY, phase="final"))
    value = submission(w)
    p = Port([("submit_semantic_review", value), ("submit_semantic_review", value)])
    result = CheckReasoning(p, config).execute(["batch"], CONTEXT, BODY, phase="final")
    assert result["passed"] and [r for r, _ in p.requests] == ["generation", "jev"]
    assert all(m["role"] != "tool" for m in p.requests[1][1])
    assert CheckReasoning(p, config).execute(["other-run"], CONTEXT, BODY, phase="final")["passed"]
    assert len(p.requests) == 2


@pytest.mark.parametrize("flag,policy", [(True, 2), (2, 2), (1, 1)])
def test_schedule_version_is_explicit_and_does_not_reinterpret_old_config(flag, policy):
    with pytest.raises(ValueError, match="invalid_reasoning_checklist"):
        validate_reasoning_checklist({"version": 6, "rules": RULES, "evidence_policy": policy, "batch_submission_version": flag})


def test_legacy_whole_submission_still_rejects_missing_items():
    w = workspace(); value = submission(w); value["checks"].pop()
    assert w.workspace.validate_review(value) == "rule_coverage_missing"
    assert not w.workspace.execute("submit_semantic_review", value)["ok"]


@pytest.mark.parametrize("vote", [None, [], {}, 7])
def test_malformed_status_is_reported_per_item_without_losing_other_results(vote):
    w = workspace(); value = submission(w); value["checks"][0]["status"] = vote
    assert not w.execute("submit_semantic_review", value)["complete"]
    assert set(w.records["checks"]) == {"units", "argument"}
    assert w.execute("submit_semantic_review", {"checks": [submission(w)["checks"][0]]})["complete"]


def test_new_run_freezes_batch_schedule_and_old_config_is_unchanged(tmp_path):
    from lib.infrastructure import training_workflow as engine
    original = {"cot": {"enabled": True, "self_check": {"version": 6, "evidence_policy": 2, "rules": RULES}}}
    before = deepcopy(original)
    run_id = engine.create_run(tmp_path / "runs", brief="Explain a conditional rule.", targets=["cot"],
                               node_generation=original)
    recipe = json.loads((engine.run_path(tmp_path / "runs", run_id) / "recipe.json").read_text("utf-8"))
    assert recipe["version"] == 32 and {31, 32}.issubset(engine.SUPPORTED_RECIPE_VERSIONS)
    assert recipe["node_generation"]["cot"]["self_check"]["batch_submission_version"] == 1
    assert recipe["authored_trace_transport_version"] == 1
    assert original == before
    assert "batch_submission_version" not in validate_reasoning_checklist(original["cot"]["self_check"])
