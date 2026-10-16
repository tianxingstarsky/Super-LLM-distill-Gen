"""Second-version schema retries identify stale-turn quotes without text echoes."""
from copy import deepcopy
import json

import pytest

from lib.domain.workflow_node_prompts import snapshot_node_prompts, validate_node_prompt_snapshot
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_review_repair import _claim_format_quote_diagnostic
from lib.prompts import get, render
from tests.test_blind_claim_workflow import OfflineBlindClient, grade_response, worker
from tests.test_blind_claim_format_feedback import diagnostic
from tests.test_claim_review_workflow import SOURCE, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class PreviousReasoningClient(OfflineBlindClient):
    def chat(self, wire, **kwargs):
        value = super().chat(wire, **kwargs)
        data = model_request_data(wire)
        if "target_fields" not in data:
            return value
        raw = grade_response(data)
        for claim in raw["claim_checks"]:
            claim["quote"] = claim["quote"].replace("**", "")
        for check in raw["reference_checks"]:
            check["quote"] = check["quote"].replace("**", "")
        if data["scope"]["message_index"] == 3 and "format_repair" not in data:
            next(claim for claim in raw["claim_checks"] if claim["field"] == "reasoning_content")["quote"] = (
                data["messages"][1]["reasoning_content"].replace("**", ""))
        return json.dumps(raw)


def multiline_row():
    row = sample(True)
    row["messages"][1]["reasoning_content"] = "**EARLIER_REASONING_ONLY_772** is relevant to the first turn."
    row["messages"][3]["content"] = "**Record** the result."
    row["messages"][3]["reasoning_content"] = "CURRENT_REASONING_ONLY_884 explains recording after inspection."
    return row


@pytest.mark.parametrize("format_version", [1, 2])
def test_real_retry_identifies_previous_reasoning_only_in_version_two_and_binds_actual_feedback(tmp_path, format_version):
    client = PreviousReasoningClient()
    flow, row = worker(tmp_path, client), multiline_row()
    assert flow.recipe["claim_review_format_feedback_version"] == 2
    assert flow.recipe["claim_reference_policy_version"] == 1
    flow.recipe["claim_review_format_feedback_version"] = format_version
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    assert accepted(flow._review_claim_score("multiturn", row, evidence, ["sample"]))
    assert len(client.calls) == 5
    retry, = [call for call in client.calls if "format_repair" in call["data"]]
    detail = diagnostic(retry["data"])
    assert detail["error_code"] == "fabricated_claim_review_quote"
    if format_version == 2:
        assert detail["unmatched_candidate_quotes"] == [{"response_path": ["claim_checks", 1, "quote"],
            "field": "reasoning_content", "target_message_index": 3, "actual_previous_message_indices": [1]}]
    else:
        assert "unmatched_candidate_quotes" not in detail
    assert "EARLIER_REASONING_ONLY_772" not in canonical(detail)
    assert "CURRENT_REASONING_ONLY_884" not in canonical(detail)
    assert "Record the result." not in canonical(detail)  # Valid rendered emphasis is not diagnosed as stale.
    failure, = row["claim_review"]["schema_failures"]
    stored = engine.read_json(flow.path / "checkpoints/review" / (failure["checkpoint"]["checkpoint_sha256"] + ".json"))
    assert "EARLIER_REASONING_ONLY_772" in stored["data"]["claim_checks"][1]["quote"]
    final = row["claim_review"]["assessments"][1]
    assert final["checkpoint"]["format_repair"] == retry["data"]["format_repair"]
    assert final["checkpoint"]["request_sha256"] == engine.digest({
        **final["review"]["request"], "format_repair": retry["data"]["format_repair"]})
    assert "source/context/logic" in retry["wire"][0]["content"]
    assert final["review"]["reference_policy_version"] == 1
    before = len(client.calls)
    assert accepted(flow._review_claim_score("multiturn", row, evidence, ["sample"]))
    assert len(client.calls) == before


class WrongSourcePathClient(OfflineBlindClient):
    def chat(self, wire, **kwargs):
        value = super().chat(wire, **kwargs)
        data = model_request_data(wire)
        if "target_fields" in data and "format_repair" not in data:
            raw = grade_response(data)
            raw["claim_checks"][0]["basis"]["evidence"][0]["source_path"] = ["not_a_real_source_path"]
            return json.dumps(raw)
        return value


def test_actual_source_path_retry_exposes_bounded_paths_and_no_document_body(tmp_path):
    client = WrongSourcePathClient()
    flow, row = worker(tmp_path, client), sample()
    assert accepted(flow._review_claim_score("sft", row,
        {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}, ["sample"]))
    retry, = [call for call in client.calls if "format_repair" in call["data"]]
    detail = diagnostic(retry["data"])
    assert detail["error_code"] == "invalid_claim_review_source_path"
    assert ["teacher_evidence"] in detail["actual_source_leaf_paths"]
    assert SOURCE not in canonical(detail)
    assert len(client.calls) == 3


def test_diagnostics_never_echo_invalid_fields_raw_errors_or_source_leaf_values():
    request = {"scope": {"message_index": 1}, "messages": [{"role": "user", "content": "USER_BODY"},
        {"role": "assistant", "content": "CURRENT_BODY"}], "target_fields": {"content": "CURRENT_BODY"},
        "source_evidence": {"teacher_evidence_blocks": ["SOURCE_BODY_" + str(index) for index in range(100)],
                            "bad\nKEY_SENTINEL": "LEAF_BODY_SENTINEL"}}
    raw = {"claim_checks": [{"field": "UNSAFE_FIELD_SENTINEL", "quote": "UNSAFE_QUOTE_SENTINEL"}]}
    detail = _claim_format_quote_diagnostic(raw, request, "claim_review", quote_matching_version=1,
        error_code="invalid_claim_review_source_path")
    assert len(detail["actual_source_leaf_paths"]) == 64
    assert detail["source_leaf_paths_truncated"] is True
    assert all(token not in canonical(detail) for token in ("USER_BODY", "CURRENT_BODY", "SOURCE_BODY_",
        "KEY_SENTINEL", "LEAF_BODY_SENTINEL", "UNSAFE_FIELD_SENTINEL", "UNSAFE_QUOTE_SENTINEL"))


@pytest.mark.parametrize("reference", [None, 1])
def test_new_catalog_keeps_frozen_format_profiles_and_pins_new_reference_policy(reference):
    options = {"recipe_version": 22, "claim_review_protocol_version": 2,
        "claim_review_format_feedback_version": 2, "claim_refutation_release_version": 1,
        "claim_reference_policy_version": reference}
    templates = snapshot_node_prompts(None, **options)
    score = "1.16.0" if reference == 1 else "1.15.0"
    assert templates["review"]["workflow.review_score"] == render(get("workflow.review_score", score))
    assert templates["review"]["workflow.review_reference"] == render(get("workflow.review_reference", "1.0.1"))
    assert validate_node_prompt_snapshot(templates, "system", **options) == templates


@pytest.mark.parametrize("value", [True, False, 0, 2, "1", []])
def test_bad_reference_policy_stops_before_any_model_request(tmp_path, value):
    client = OfflineBlindClient()
    flow, row = worker(tmp_path, client), sample()
    flow.recipe["claim_reference_policy_version"] = value
    with pytest.raises(ValueError, match="invalid_claim_reference_policy_version"):
        flow._review_claim_score("sft", row, {"teacher_evidence": SOURCE}, ["sample"])
    assert not client.calls
    with pytest.raises(ValueError, match="invalid_claim_reference_policy_version"):
        snapshot_node_prompts(None, recipe_version=22, claim_review_protocol_version=2,
            claim_reference_policy_version=value)
