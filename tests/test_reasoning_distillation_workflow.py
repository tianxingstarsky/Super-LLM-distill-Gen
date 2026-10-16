"""The existing bounded review loop retains exact assessed reasoning versions."""
from copy import deepcopy

import pytest

from lib.domain.reasoning_candidates import reasoning_candidate_digest
from lib.domain.turn_review import supervised_assistant_turns, turn_review_verdict
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def candidate():
    return {"id": "fresh", "source_id": "paper", "status": "eligible",
        "reasoning_origin": "prompt_styled_generation", "messages": [
            {"role": "user", "content": "Can this check establish the conclusion?"},
            {"role": "assistant", "content": "An unchanged result proves improvement.",
             "reasoning_content": "I assume equality establishes an improvement and keep that conclusion."}]}


def grade(payload, passing):
    result = {"keep": passing, "grounded": True, "reasoning_valid": passing, "correctness": 5,
        "scores": {"correctness": 5, "reasoning": 5 if passing else 2,
                   "grounding": 5, "instruction": 5, "safety": 5},
        "reason": "The conclusion follows." if passing else "Equality does not establish improvement."}
    raw = {**result, "turn_verdicts": [{**result, "message_index": turn["message_index"],
        "content_sha256": turn["content_sha256"], "leakage_free": True,
        "critical_error": not passing} for turn in supervised_assistant_turns(payload)]}
    return turn_review_verdict(raw, supervised_assistant_turns(payload))


class Probe(WorkflowReviewRepair):
    def __init__(self, results, *, active=True, rounds=1):
        self.recipe = {"authored_reasoning_warmup_version": 1,
            "reasoning_distillation_audit_version": 1 if active else 0,
            "node_generation": {"sft": {"enabled": True}},
            "review_repair": {"mode": "auto", "max_rounds": rounds}}
        self.results, self.scores, self.repairs = iter(results), 0, 0

    def _review_evidence(self, row):
        return {"source_kind": "brief", "teacher_evidence": "Improvement requires a positive difference."}

    def _review_score(self, target, row, evidence, key, instruction):
        self.scores += 1
        return grade(self._review_payload(target, row), next(self.results))

    def _repair_content(self, target, row, evidence, key, check, instruction):
        self.repairs += 1
        row = deepcopy(row)
        row["messages"][1].update(content="An unchanged result cannot establish improvement.",
            reasoning_content="The hypothesis requires a positive change. Equality has zero difference, so the hypothesis is unsupported.")
        return row, None


def test_first_passing_revision_retains_rejected_full_body_and_separate_axes():
    original = candidate()
    probe = Probe([False, True])
    result = probe._review_repair_item({"target": "sft", "record": original})[0]
    audit = result["reasoning_distillation"]
    assert result["status"] == "eligible" and audit["selected_index"] == 1
    assert audit["attempted"] == 2 and audit["candidate_limit"] == 2
    first, final = audit["assessments"]
    assert first["payload"]["messages"] == original["messages"]
    assert first["outcome"]["status"] == "pass" and first["process"]["status"] == "fail"
    assert first["method"] == final["method"] == "llm_review"
    assert final["candidate_sha256"] == reasoning_candidate_digest(final["payload"])
    assert final["payload"]["messages"] == result["messages"]
    assert audit["stop_reason"] == "first_passing_candidate"
    assert audit["candidate_policy"] == "bounded_feedback_revisions"
    assert original["messages"][1]["content"] == "An unchanged result proves improvement."
    assert probe.scores == 2 and probe.repairs == 1


def test_passing_first_draft_does_not_spend_the_remaining_candidate_allowance():
    probe = Probe([True], rounds=3)
    result = probe._review_repair_item({"target": "sft", "record": candidate()})[0]
    assert probe.scores == 1 and probe.repairs == 0
    assert result["reasoning_distillation"]["attempted"] == 1


def test_exhausted_candidates_stay_quarantined_with_complete_unmodified_reasoning():
    probe = Probe([False, False])
    result = probe._review_repair_item({"target": "sft", "record": candidate()})[0]
    assert result["status"] == "quarantined"
    assert result["reasoning_distillation"]["status"] == "exhausted"
    assert result["reasoning_distillation"]["selected_index"] is None
    assert len(result["reasoning_distillation"]["assessments"]) == 2
    assert result["messages"][1]["reasoning_content"]


@pytest.mark.parametrize("native", [False, True])
def test_old_recipe_or_native_output_does_not_receive_authored_audit(native):
    original = candidate()
    probe = Probe([True], active=native)
    if native:
        original["reasoning_origin"] = "provider_reasoning"
    result = probe._review_repair_item({"target": "sft", "record": original})[0]
    assert "reasoning_distillation" not in result
