"""Schema-only, versioned diagnostics reach the real model request boundary."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.domain.blind_claim_review import blind_review_requests, validate_blind_review
from lib.domain.workflow_quality import accepted, canonical
from lib.domain.workflow_node_prompts import snapshot_node_prompts, validate_node_prompt_snapshot
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_review_repair import _BLIND_REFERENCE_WIRE_PROTOCOL
from tests.test_blind_claim_workflow import OfflineBlindClient, blind_response, grade_response
from tests.test_claim_review_workflow import SOURCE, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def workflow(tmp_path, client, *, enabled=True):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Arrange safe inspection.", targets=["multiturn"],
                               reasoning_required=True, review_repair={"mode": "auto", "max_rounds": 0})
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["claim_review_format_feedback_version"] == 2
    # Freeze the first diagnostic version exercised by this historical file.
    recipe.pop("claim_reference_policy_version")
    recipe.pop("claim_review_experience_scope_version")
    recipe.pop("claim_review_anchor_protocol_version")
    versions = (("workflow.review_score", "1.15.0"), ("workflow.review_reference", "1.0.1"))
    recipe["claim_review_format_feedback_version"] = 1
    if not enabled:
        recipe.pop("claim_review_format_feedback_version")
        recipe.pop("claim_refutation_release_version")
        recipe.pop("claim_review_quote_matching_version")
        versions = (("workflow.review_score", "1.13.0"), ("workflow.review_reference", "1.0.0"))
    from lib.prompts import get, render
    for prompt_id, version in versions:
        frozen = get(prompt_id, version=version)
        recipe["node_prompt_templates"]["review"][prompt_id] = render(frozen)
        recipe["prompts"][prompt_id] = {"version": version, "sha256": engine.digest(frozen.template)}
    engine.atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    engine.atomic_json(path / "state.json", state)
    result = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client)
    result.stage = "review"
    return result


class WrongIndexClient(OfflineBlindClient):
    def chat(self, wire, **kwargs):
        data = model_request_data(wire)
        self.calls.append({"wire": deepcopy(wire), "data": deepcopy(data)})
        self.usage["calls"] += 1
        if data.get("phase") != "blind_reference":
            return json.dumps(grade_response(data))
        value = blind_response(data)
        if data["scope"]["message_index"] == 1:
            if "format_repair" not in data:
                value["scope"]["target"] = data["target"]
        else:
            last_user = max(index for index, message in enumerate(data["messages"]) if message["role"] == "user")
            point = value["reference_points"][0]
            point["point"] = "The actual earlier user reports that inspection has finished."
            point["basis"] = {"kind": "context", "evidence": [{"message_index": (
                last_user if "format_repair" in data else data["scope"]["message_index"]), "field": "content",
                "quote": data["messages"][last_user]["content"]}],
                "derivation": "The quoted earlier user message supplies the completion report for this turn."}
        return json.dumps(value)


def diagnostic(data):
    text = data["format_repair"]["instruction"]
    return json.loads(text.split("Schema-only diagnostic: ", 1)[1])


def test_real_wire_gives_actual_array_indices_exact_error_and_preserves_failed_replies(tmp_path):
    client = WrongIndexClient()
    flow, row = workflow(tmp_path, client), sample(True)
    row["messages"][1]["content"] += " CURRENT_DRAFT_ONLY_887"
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    assert accepted(flow._review_claim_score("multiturn", row, evidence, ["sample"]))
    assert len(client.calls) == 6  # Two bounded blind format retries; two grade calls.
    first, first_retry, later, later_retry = [item for item in client.calls if item["data"].get("phase") == "blind_reference"]
    first_detail, later_detail = diagnostic(first_retry["data"]), diagnostic(later_retry["data"])
    assert first_detail["error_code"] == "stale_blind_review_response"
    assert "target" not in first_detail["scope_field_names"]
    assert later_detail["error_code"] == "invalid_claim_review_context_reference"
    assert later_detail["valid_context_message_indices"] == [0, 1, 2]
    assert [(item["message_index"], item["role"]) for item in later_detail["messages_index_table"]] == [
        (0, "user"), (1, "assistant"), (2, "user")]
    assert later_detail["current_assistant_message_index"] == 3
    assert '"valid_context_message_indices":[0,1,2]' in later["wire"][0]["content"]
    assert [item["provenance"] for item in later_detail["messages_index_table"]] == [
        "user_report_or_request_not_external_fact", "prior_assistant_claim_not_verified", "user_report_or_request_not_external_fact"]
    assert '"provenance":"prior_assistant_claim_not_verified"' in later["wire"][0]["content"]
    assert "CURRENT_DRAFT_ONLY_887" not in first["wire"][0]["content"]
    assert row["messages"][2]["content"] not in canonical(later_detail)
    assert row["messages"][2]["content"] not in later_retry["data"]["format_repair"]["instruction"]
    audit = row["claim_review"]
    assert len(audit["schema_failures"]) == 2
    failures = []
    for failure in audit["schema_failures"]:
        reference = failure["checkpoint"]
        saved = engine.read_json(flow.path / "checkpoints" / reference["stage"] / (reference["checkpoint_sha256"] + ".json"))
        assert saved["sha256"] == reference["response_sha256"] == engine.digest(saved["data"])
        failures.append(saved["data"])
    assert "target" in failures[0]["scope"]
    assert failures[1]["reference_points"][0]["basis"]["evidence"][0]["message_index"] == 3
    assert audit["blind_reviews"][1]["review"]["raw_response"]["reference_points"][0]["basis"]["evidence"][0]["message_index"] == 2
    for call, item in zip((first_retry, later_retry), audit["blind_reviews"]):
        assert item["checkpoint"]["format_repair"] == call["data"]["format_repair"]
        assert item["checkpoint"]["request_sha256"] == engine.digest({
            **item["review"]["request"], "format_repair": call["data"]["format_repair"]})
        assert item["checkpoint"]["key"][-2:] == ["format_repair", 1]
    before = len(client.calls)
    flow._review_claim_score("multiturn", row, evidence, ["sample"])
    assert len(client.calls) == before


def test_untrusted_error_value_never_reaches_actual_retry_or_audit_code(tmp_path):
    client = OfflineBlindClient()
    flow, row = workflow(tmp_path, client), sample()
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    request, = blind_review_requests("sft", flow._review_payload("sft", row), evidence, True)
    calls = 0
    def malicious_error(raw, current):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("invalid_claim_review_context_reference\nSECRET_ERROR_VALUE_426: CURRENT_DRAFT_ONLY")
        return validate_blind_review(raw, current)
    audit = {"schema_failures": [], "status": "pending"}
    receipt, checkpoint = flow._claim_v2_call(request, prompt_id="workflow.review_reference",
        base_key=["blind_reference", "sft", request["scope"]["scope_sha256"]], role="generation",
        validator=malicious_error, audit=audit, phase="blind_reference", instruction=_BLIND_REFERENCE_WIRE_PROTOCOL)
    assert len(client.calls) == 2 and receipt["structure_verified"]
    assert "SECRET_ERROR_VALUE_426" not in canonical(client.calls) and "CURRENT_DRAFT_ONLY" not in canonical(client.calls)
    assert audit["schema_failures"][0]["code"] == "invalid_blind_review_response"
    assert diagnostic(client.calls[1]["data"])["error_code"] == "invalid_blind_review_response"
    assert checkpoint["request_sha256"] == engine.digest({
        **request, "format_repair": client.calls[1]["data"]["format_repair"]})


def test_missing_format_marker_keeps_original_v2_request_and_retry_instruction_bytes(tmp_path):
    client = OfflineBlindClient()
    flow, row = workflow(tmp_path, client, enabled=False), sample()
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    request, = blind_review_requests("sft", flow._review_payload("sft", row), evidence, True)
    count, calls = 0, []
    original_ask = flow.ask
    def capture(*args, **kwargs):
        calls.append((deepcopy(args), deepcopy(kwargs)))
        return original_ask(*args, **kwargs)
    flow.ask = capture
    def once(raw, current):
        nonlocal count
        count += 1
        if count == 1:
            raise ValueError("invalid_claim_review_context_reference")
        return validate_blind_review(raw, current)
    flow._claim_v2_call(request, prompt_id="workflow.review_reference",
        base_key=["blind_reference", "sft", request["scope"]["scope_sha256"]], role="generation",
        validator=once, audit={"schema_failures": [], "status": "pending"}, phase="blind_reference",
        instruction=_BLIND_REFERENCE_WIRE_PROTOCOL)
    original_feedback = {"code": "invalid_blind_review_response",
        "instruction": "Return the complete current protocol contract and copy its scope exactly. Preserve the real evidence and candidate boundaries; do not fabricate missing fields."}
    assert calls[0][0][3] == request
    assert canonical(calls[1][0][3]) == canonical({**request, "format_repair": original_feedback})
    assert all(kwargs["instruction"] == _BLIND_REFERENCE_WIRE_PROTOCOL for _, kwargs in calls)
    assert "messages_index_table" not in canonical(client.calls)


def test_invalid_format_marker_and_exhausted_retry_never_add_more_calls(tmp_path):
    client = WrongIndexClient()
    flow, row = workflow(tmp_path, client), sample()
    flow.recipe["claim_review_format_feedback_version"] = True
    with pytest.raises(ValueError, match="invalid_claim_review_format_feedback_version"):
        flow._review_claim_score("sft", row, {"teacher_evidence": SOURCE}, ["sample"])
    assert not client.calls
    flow.recipe["claim_review_format_feedback_version"] = 1
    request, = blind_review_requests("sft", flow._review_payload("sft", row),
                                     {"teacher_evidence": SOURCE}, True)
    def always_invalid(raw, current):
        raise ValueError("invalid_claim_review_context_reference")
    audit = {"schema_failures": [], "status": "pending"}
    with pytest.raises(ValueError, match="invalid_claim_review_context_reference"):
        flow._claim_v2_call(request, prompt_id="workflow.review_reference",
            base_key=["blind_reference", "sft", request["scope"]["scope_sha256"]], role="generation",
            validator=always_invalid, audit=audit, phase="blind_reference", instruction=_BLIND_REFERENCE_WIRE_PROTOCOL)
    assert len(client.calls) == 2 and audit["status"] == "invalid_after_retry"
    assert len(audit["schema_failures"]) == 2


def test_format_version_selects_new_prompts_without_mutating_frozen_v2_or_v1_catalogs():
    from lib.prompts import get, render
    profiles = [(None, None, "1.11.0", None), (1, None, "1.12.0", None),
                (2, None, "1.13.0", "1.0.0"), (2, 1, "1.14.0", "1.0.1")]
    for protocol, feedback, score, reference in profiles:
        templates = snapshot_node_prompts(None, recipe_version=22, learner_context_review_version=1,
            claim_review_protocol_version=protocol, claim_review_format_feedback_version=feedback)
        assert templates["review"]["workflow.review_score"] == render(get("workflow.review_score", version=score))
        if reference is None:
            assert "workflow.review_reference" not in templates["review"]
        else:
            assert templates["review"]["workflow.review_reference"] == render(get("workflow.review_reference", version=reference))
        assert validate_node_prompt_snapshot(templates, "SYSTEM", recipe_version=22,
            learner_context_review_version=1, claim_review_protocol_version=protocol,
            claim_review_format_feedback_version=feedback) == templates
    with pytest.raises(ValueError, match="invalid_claim_review_format_feedback_version"):
        snapshot_node_prompts(None, recipe_version=22, claim_review_protocol_version=2,
                             claim_review_format_feedback_version=True)
