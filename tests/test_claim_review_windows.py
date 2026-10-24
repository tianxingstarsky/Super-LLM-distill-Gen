"""Whole-response preservation, strict wire repair and resumable review windows."""
from copy import deepcopy
import json

import pytest

from lib.contexts.models.contracts import ModelOutputLimitError
from lib.contexts.review.domain.claim_windows import (
    plan_review_windows, assemble_review_windows, validate_review_window_response, project_review_window_hints,
)
from lib.contexts.review.domain.wire_schema import review_wire_diagnostics, validate_review_wire_response
from lib.domain.adversarial_claim_review import challenge_review_requests
from lib.domain.reasoning_candidates import assessment_from_verdict
from lib.domain.workflow_quality import accepted
from lib.infrastructure import training_workflow as engine
from tests.test_blind_claim_workflow import OfflineBlindClient
from tests.test_claim_review_workflow import sample, SOURCE
from tests.test_condition_review_workflow import flow, evidence
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def long_sample():
    row = sample()
    row["messages"][1]["reasoning_content"] = " ".join(
        f"Inspection condition {index} requires power-off before opening the case."
        for index in range(1, 40))
    return row


class WindowClient(OfflineBlindClient):
    def __init__(self, *, wrong_verdict=False, output_limit=False, interrupt_at=None):
        super().__init__()
        self.wrong_verdict, self.output_limit, self.interrupt_at = wrong_verdict, output_limit, interrupt_at
        self.limited = set()

    def chat(self, messages, **kwargs):
        request = model_request_data(messages)
        if self.interrupt_at == len(self.calls) + 1:
            raise RuntimeError("offline_window_interrupt")
        response = json.loads(super().chat(messages, **kwargs))
        window = request.get("review_window")
        if window:
            signature = window["sha256"]
            if self.output_limit and len(window["unit_ids"]) > 6:
                self.limited.add(signature)
                raise ModelOutputLimitError()
            response["scope_sha256"] = signature
            collection = "condition_ledger" if request["phase"] == "claim_challenge" else "claim_checks"
            response[collection] = [item for item in response[collection] if item["unit_id"] in window["unit_ids"]]
            if "checked_unit_ids" in response:
                response["checked_unit_ids"] = deepcopy(window["unit_ids"])
        if self.wrong_verdict and request["phase"] == "claim_adjudication" and "format_repair" not in request:
            # The real failure used turn-verdict fields and flat scores here.
            response["verdict"].pop("grounded")
            scores = response["verdict"].pop("scores")
            response["verdict"].update(instruction=scores["instruction"], critical_error=False)
        self.responses = getattr(self, "responses", []) + [deepcopy(response)]
        return json.dumps(response)


def test_long_reasoning_keeps_whole_prefix_and_every_original_span_with_strict_final_audit(tmp_path):
    client, row = WindowClient(wrong_verdict=True), long_sample()
    workflow = flow(tmp_path, client)
    original = deepcopy(row["messages"])
    check = workflow._review_claim_score("sft", row, evidence(), ["candidate"])
    assert accepted(check)
    assert row["messages"] == original
    audit = row["claim_review"]
    assert audit["version"] == 5 and audit["schema_failures"]
    record, = audit["assessments"]
    assert "raw_response" not in record["review"]
    assert record["review"]["response_origin"] == "program_aggregate_of_review_windows"
    full = record["review"]["request"]
    units = [unit["id"] for unit in full["target_units"]]
    actual = []
    for window in record["windows"]:
        request = window["review"]["request"]
        assert request["messages"] == full["messages"]
        assert request["target_fields"] == full["target_fields"]
        assert request["target_units"] == full["target_units"]
        assert request["source_evidence"] == full["source_evidence"]
        actual += request["review_window"]["unit_ids"]
        assert window["review"]["raw_response"]["verdict"]["scores"]
        feedback = window["checkpoint"]["format_repair"]["instruction"]
        assert '"response_path":["verdict"]' in feedback
        assert '"missing_fields":["grounded","scores"]' in feedback
    assert actual == units
    assert assessment_from_verdict(workflow._review_payload("sft", row), check,
        evidence_ref="offline-window-receipts", method="llm_claim_review", claim_review=audit)["accepted"]
    before = len(client.calls)
    assert accepted(workflow._review_claim_score("sft", row, evidence(), ["candidate"]))
    assert len(client.calls) == before


def test_limit_is_split_without_erasing_reasoning_or_rebuying_the_failed_window_on_resume(tmp_path):
    client, row = WindowClient(output_limit=True), long_sample()
    workflow = flow(tmp_path, client)
    check = workflow._review_claim_score("sft", row, evidence(), ["candidate"])
    assert accepted(check) and client.limited
    assert row["claim_review"]["window_failures"]
    assert assessment_from_verdict(workflow._review_payload("sft", row), check,
        evidence_ref="split-window-receipts", method="llm_claim_review", claim_review=row["claim_review"])["accepted"]
    resumed_client = WindowClient(output_limit=True)
    resumed = engine.Workflow(workflow.path.parent.parent, workflow.state["id"], workflow.root, generator=resumed_client)
    resumed.stage = "review"
    assert accepted(resumed._review_claim_score("sft", row, evidence(), ["candidate"]))
    assert not resumed_client.calls
    assert row["messages"][1]["reasoning_content"] == long_sample()["messages"][1]["reasoning_content"]


def test_interruption_reuses_completed_windows_and_does_not_skip_any_remaining_units(tmp_path):
    first, row = WindowClient(interrupt_at=3), long_sample()
    workflow = flow(tmp_path, first)
    with pytest.raises(RuntimeError, match="offline_window_interrupt"):
        workflow._review_claim_score("sft", row, evidence(), ["candidate"])
    assert len(first.calls) == 2
    resumed_client = WindowClient()
    resumed = engine.Workflow(workflow.path.parent.parent, workflow.state["id"], workflow.root, generator=resumed_client)
    resumed.stage = "review"
    assert accepted(resumed._review_claim_score("sft", row, evidence(), ["candidate"]))
    completed_ids = {call["data"]["review_window"]["sha256"] for call in first.calls}
    assert not completed_ids & {call["data"]["review_window"]["sha256"] for call in resumed_client.calls}


def test_diagnostics_name_nested_required_fields_without_echoing_rejected_values():
    request, = challenge_review_requests("sft", sample(), evidence(), reasoning_required=True,
                                         condition_contract_version=1)
    bad = {"SECRET_FROM_REJECTED_TEXT": "NEVER_ECHO_THIS_SOURCE"}
    diagnostic = json.dumps(review_wire_diagnostics(bad, request))
    assert "condition_ledger" in diagnostic and "unexpected_field_count" in diagnostic
    assert "SECRET_FROM_REJECTED_TEXT" not in diagnostic and "NEVER_ECHO_THIS_SOURCE" not in diagnostic
    with pytest.raises(ValueError, match="invalid_claim_review_wire_response"):
        validate_review_wire_response(bad, request)


def test_source_quote_diagnostics_locate_only_exact_original_text_and_never_rebind_it():
    from lib.contexts.review.domain.wire_schema import source_reference_diagnostics
    from lib.domain.claim_review import _reference
    source = {"teacher_evidence": "The original exact sentence.",
              "teacher_evidence_blocks": ["Another block.", "The original exact sentence."]}
    request = {"source_evidence": source}
    wrong = {"quote": "original exact sentence", "source_path": ["teacher_evidence_blocks", 0]}
    raw = {"claim_checks": [{"basis": {"evidence": [deepcopy(wrong)]}}]}
    before = deepcopy(raw)
    diagnostic = source_reference_diagnostics(raw, request)
    failure, = diagnostic["unmatched_source_references"]
    assert failure == {"response_path": ["claim_checks", 0, "basis", "evidence", 0, "source_path"],
        "code": "quote_at_other_source_path",
        "exact_quote_source_paths": [["teacher_evidence"], ["teacher_evidence_blocks", 1]]}
    assert raw == before
    assert "original exact sentence" not in json.dumps(diagnostic)
    with pytest.raises(ValueError, match="fabricated_claim_review_evidence_quote"):
        _reference(wrong, request, "source")
    raw["claim_checks"][0]["basis"]["evidence"][0]["quote"] = "original  exact sentence"
    failure, = source_reference_diagnostics(raw, request)["unmatched_source_references"]
    assert failure["code"] == "quote_not_in_source" and failure["exact_quote_source_paths"] == []
    assert source_reference_diagnostics({"SECRET_REJECTED_VALUE": before["claim_checks"]}, request) == {}


def test_wrong_source_block_is_retried_by_model_with_exact_path_feedback_and_raw_failure_kept(tmp_path):
    class WrongBlockClient(WindowClient):
        def chat(self, messages, **kwargs):
            request = model_request_data(messages)
            response = json.loads(super().chat(messages, **kwargs))
            if request["phase"] == "claim_adjudication" and "format_repair" not in request:
                for claim in response["claim_checks"]:
                    claim["basis"]["evidence"][0]["source_path"] = ["source_id"]
            return json.dumps(response)

    client, row = WrongBlockClient(), long_sample()
    workflow = flow(tmp_path, client)
    before = deepcopy(row["messages"])
    assert accepted(workflow._review_claim_score("sft", row, evidence(), ["source-location"]))
    assert row["messages"] == before
    for failure in row["claim_review"]["schema_failures"]:
        checkpoint = engine.read_json(workflow.path / "checkpoints/review" /
            (failure["checkpoint"]["checkpoint_sha256"] + ".json"))["data"]
        assert checkpoint["claim_checks"][0]["basis"]["evidence"][0]["source_path"] == ["source_id"]
    retries = [call for call in client.calls if "format_repair" in call["data"]]
    assert retries and all('"exact_quote_source_paths":[["teacher_evidence"]]' in
                          call["data"]["format_repair"]["instruction"] for call in retries)


def test_windows_reject_missing_duplicate_stale_and_fabricated_premise_coverage():
    request, = challenge_review_requests("sft", long_sample(), evidence(), reasoning_required=True,
                                         condition_contract_version=1)
    windows = plan_review_windows(request)
    replies = []
    for window in windows:
        raw = {"claim_review_protocol_version": 4, "phase": "claim_challenge",
               "scope_sha256": window["review_window"]["sha256"], "issues": [], "limitations": [],
               "condition_ledger": [{"unit_id": identifier, "premises": []}
                                    for identifier in window["review_window"]["unit_ids"]]}
        replies.append(validate_review_window_response(raw, window))
    assemble_review_windows(request, replies)
    for changed, code in ((replies[:-1], "incomplete_review_window_coverage"),
                          (replies + replies[:1], "duplicate_review_window_coverage")):
        with pytest.raises(ValueError, match=code):
            assemble_review_windows(request, changed)
    changed = deepcopy(replies)
    changed[0]["raw_response"]["condition_ledger"][0]["premises"] = [
        {"origin": "candidate", "use": "reference_resolution", "unit_id": request["target_units"][-1]["id"],
         "quote": "THIS_QUOTE_WAS_NOT_PRESENT"}]
    with pytest.raises(ValueError, match="fabricated_condition_candidate_quote"):
        assemble_review_windows(request, changed)


def test_historical_recipe_without_transport_marker_keeps_original_unwindowed_contract(tmp_path):
    client, row = WindowClient(), long_sample()
    workflow = flow(tmp_path, client)
    workflow.recipe.pop("claim_review_transport_version")
    assert accepted(workflow._review_claim_score("sft", row, evidence(), ["candidate"]))
    assert row["claim_review"]["version"] == 4 and len(client.calls) == 2
    assert not any("review_window" in call["data"] for call in client.calls)


def test_aggregate_preference_score_uses_its_own_strict_contract_and_preserves_bad_raw_checkpoint(tmp_path):
    class AggregateClient(OfflineBlindClient):
        def chat(self, messages, **kwargs):
            request = model_request_data(messages)
            from tests.test_claim_review_workflow import grade
            self.calls.append({"wire": deepcopy(messages), "data": deepcopy(request)})
            self.usage["calls"] += 1
            response = grade()
            if "format_repair" not in request:
                response.pop("scores")
            return json.dumps(response)
    client = AggregateClient()
    worker = flow(tmp_path, client)
    # A standalone rejected-response score uses aggregate format even though
    # the same node also owns the protocol-four condition reviewer.
    row = {"id": "preference-sample", "prompt": [{"role": "user", "content": "How should I inspect?"}],
           "chosen": [{"role": "assistant", "content": "Power off before inspection."}],
           "rejected": [{"role": "assistant", "content": "Inspect with power on."}]}
    # The helper client grades both high: this fixture tests transport only,
    # not a fabricated preference gap or semantic acceptance.
    worker._review_score("dpo", row, evidence(), ["pair"], "")
    retry = row["aggregate_review_retries"][0]
    assert retry["attempts"] == 2 and retry["schema_failures"]
    first = client.calls[0]
    assert "普通候选/偏好评分" in first["wire"][0]["content"]
    assert not any("review_window" in call["data"] for call in client.calls)
    key = ["pair", "score"]
    saved = engine.read_json(worker.path / "checkpoints/review" / (engine.digest(["call", key]) + ".json"))
    assert "scores" not in saved["data"]


def test_condition_gap_in_one_window_blocks_the_whole_candidate_without_editing_or_inventing_scores(tmp_path):
    class AssumptionClient(WindowClient):
        def chat(self, messages, **kwargs):
            response = json.loads(super().chat(messages, **kwargs))
            request = model_request_data(messages)
            if request.get("phase") == "claim_adjudication":
                response["claim_checks"][0]["premises"] = [{"origin": "assumption", "use": "support",
                    "quote": "A condition not supplied by the original user is assumed here."}]
            return json.dumps(response)
    client, row = AssumptionClient(), long_sample()
    workflow = flow(tmp_path, client)
    original = deepcopy(row["messages"])
    check = workflow._review_claim_score("sft", row, evidence(), ["candidate"])
    assert not accepted(check) and check["keep"] is False
    assert row["messages"] == original
    receipt = row["claim_review"]["assessments"][0]["review"]
    assert receipt["condition_guard"]["needs_review"] is True
    assert all(window["review"]["raw_response"]["verdict"]["keep"]
               for window in row["claim_review"]["assessments"][0]["windows"])
    assert set(check["scores"].values()) == {5}  # Honest raw grades, conservative release veto.
    assert not assessment_from_verdict(workflow._review_payload("sft", row), check,
        evidence_ref="unresolved-window-premise", method="llm_claim_review", claim_review=row["claim_review"])["accepted"]


def test_large_condition_ledger_is_a_labelled_hint_view_without_truncating_candidate_or_relaxing_context_limit():
    from lib.domain.adversarial_claim_review import validate_challenge_review, adversarial_claim_review_requests
    from lib.llm_client import ChatClient
    source = SOURCE * 33
    candidate = long_sample()
    inventory, = challenge_review_requests("sft", candidate, {"teacher_evidence": source}, reasoning_required=True,
                                            condition_contract_version=1)
    raw = {"claim_review_protocol_version": 4, "phase": "claim_challenge", "scope_sha256": inventory["scope"]["scope_sha256"],
           "issues": [], "limitations": [], "condition_ledger": [
        {"unit_id": unit["id"], "premises": [{"origin": "source", "use": "task_condition", "quote": source,
                                            "source_path": ["teacher_evidence"]}]} for unit in inventory["target_units"]]}
    full, = adversarial_claim_review_requests("sft", candidate, {"teacher_evidence": source}, reasoning_required=True,
        condition_contract_version=1, challenge_reviews=[validate_challenge_review(raw, inventory)])
    window = plan_review_windows(full)[0]
    before = deepcopy(window)
    projected = project_review_window_hints(window)
    assert window == before
    for field in ("messages", "target_fields", "target_units", "source_evidence", "review_window", "scope"):
        assert projected[field] == window[field]
    assert [entry["unit_id"] for entry in projected["challenge_review"]["condition_ledger"]] == window["review_window"]["unit_ids"]
    assert len(window["challenge_review"]["condition_ledger"]) > len(projected["challenge_review"]["condition_ledger"])
    assert projected["condition_ledger_view"]["original_response_sha256"] == engine.digest(window["challenge_review"])
    retry = project_review_window_hints({**window, "format_repair": {"code": "invalid", "instruction": "Return the full format"}})
    assert retry["format_repair"]["instruction"] == "Return the full format"
    client = ChatClient('http://127.0.0.1:1', 'offline-only', 'offline', context_window_tokens=131072)
    try:
        with pytest.raises(ValueError, match="model_context_window_exceeded"):
            client._check_context([{"role": "user", "content": engine.canonical(window)}], 32768)
        client._check_context([{"role": "user", "content": engine.canonical(projected)}], 32768)
    finally:
        client.client.close()


def test_insufficient_preference_gap_is_explicit_feedback_without_lowering_scores_or_changing_threshold():
    from lib.contexts.review.domain.preference_feedback import preference_release_feedback
    from tests.test_claim_review_workflow import grade
    row = {'preference': {'dimension': 'correctness', 'minimum_gap': 2,
                          'chosen': grade(), 'rejected': grade()}}
    before = deepcopy(row)
    check = preference_release_feedback(row, row['preference']['chosen'])
    assert check['keep'] is False and 'preference_release_guard' in check['reason']
    assert '"chosen_correctness":5' in check['reason'] and '"rejected_correctness":5' in check['reason']
    assert check['correctness'] == 5 and set(check['scores'].values()) == {5}
    assert row == before and row['preference']['minimum_gap'] == 2
    row['preference']['rejected']['correctness'] = 2
    row['preference']['rejected']['scores']['correctness'] = 2
    assert preference_release_feedback(row, row['preference']['chosen']) == row['preference']['chosen']


@pytest.mark.parametrize("side", ["chosen", "rejected"])
def test_preference_repair_cannot_remove_either_existing_reasoning_field(side):
    from lib.contexts.review.domain.preference_feedback import preference_reasoning_issue
    original = {name: [{"role": "assistant", "content": "Answer", "reasoning_content": "Original explanation"}]
                for name in ("chosen", "rejected")}
    changed = deepcopy(original)
    changed[side][0].pop("reasoning_content")
    assert preference_reasoning_issue(original, changed) == "review_repair_reasoning_missing"
    changed[side][0]["reasoning_content"] = "   "
    assert preference_reasoning_issue(original, changed) == "review_repair_reasoning_missing"
    changed[side][0]["reasoning_content"] = "A corrected explanation consistent with this answer"
    assert preference_reasoning_issue(original, changed) is None
    changed[side] = None
    assert preference_reasoning_issue(original, changed) == "review_repair_invalid_schema"
