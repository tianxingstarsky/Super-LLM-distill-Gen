"""New schema guidance reaches the real client without changing old wires."""
from copy import deepcopy
import json

import jsonschema
import pytest

from lib.domain.adversarial_claim_review import (challenge_review_requests, validate_challenge_review,
    adversarial_claim_review_requests, validate_adversarial_claim_review)
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.claim_review_wire_schema import (claim_review_response_schema,
    claim_review_wire_schema_metadata, source_leaf_paths)
from lib.infrastructure.workflow_review_repair import (_CLAIM_CHALLENGE_V4_WIRE_PROTOCOL,
    _CLAIM_ADJUDICATION_V4_WIRE_PROTOCOL, _claim_format_boundaries)
from lib.prompts import get, render
from tests.test_adversarial_claim_workflow import workflow
from tests.test_blind_claim_workflow import OfflineBlindClient, challenge_response, adversarial_grade_response
from tests.test_claim_review_workflow import SOURCE, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def metadata(call):
    return next(json.loads(line) for line in call["data"]["node_task_instructions"].splitlines()
                if line.startswith('{"actual_source_leaf_paths"'))


def check_schema(schema, response):
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(response)


def requests_with_issue():
    request, = challenge_review_requests("sft", sample(), {"teacher_evidence": SOURCE}, reasoning_required=True)
    raw = challenge_response(request, bad=True)
    receipt = validate_challenge_review(raw, request)
    grade, = adversarial_claim_review_requests("sft", sample(), {"teacher_evidence": SOURCE},
        reasoning_required=True, challenge_reviews=[receipt])
    return request, raw, grade, adversarial_grade_response(grade)


def test_complete_schemas_accept_real_domain_fixtures_and_reject_wrong_nested_shapes():
    challenge, challenge_raw, grade, grade_raw = requests_with_issue()
    validate_adversarial_claim_review(grade_raw, grade)
    for request, raw in ((challenge, challenge_raw), (grade, grade_raw)):
        schema = claim_review_response_schema(request)
        check_schema(schema, raw)
        collection = "issues" if request["phase"] == "claim_challenge" else "claim_checks"
        for mutate in (
            lambda value: value[collection][0]["basis"].__setitem__("evidence", {"quote": SOURCE}),
            lambda value: value[collection][0]["basis"]["evidence"][0].__setitem__("source_path", "teacher_evidence"),
            lambda value: value[collection][0].__setitem__("unit_id", "m999u0001"),
            lambda value: value[collection][0]["basis"].__setitem__("unexpected", True),
            lambda value: value.__setitem__("unexpected", True)):
            changed = deepcopy(raw)
            mutate(changed)
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.Draft202012Validator(schema).validate(changed)
    bad = deepcopy(grade_raw)
    bad["challenge_checks"][0]["id"] = "foreign_issue"
    with pytest.raises(jsonschema.ValidationError):
        check_schema(claim_review_response_schema(grade), bad)
    for field in ("scores", "keep", "reason"):
        bad = deepcopy(grade_raw)
        bad["verdict"].pop(field)
        with pytest.raises(jsonschema.ValidationError):
            check_schema(claim_review_response_schema(grade), bad)
    bad = deepcopy(grade_raw)
    bad["verdict"]["scores"]["unexpected"] = 5
    with pytest.raises(jsonschema.ValidationError):
        check_schema(claim_review_response_schema(grade), bad)


def test_every_json_object_schema_forbids_additional_properties():
    for request in (requests_with_issue()[0], requests_with_issue()[2]):
        def visit(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    assert value["additionalProperties"] is False
                    assert set(value["required"]) == set(value["properties"])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(claim_review_response_schema(request))


class MalformedBasisClient(OfflineBlindClient):
    def chat(self, wire, **kwargs):
        result = super().chat(wire, **kwargs)
        request = model_request_data(wire)
        if "format_repair" in request:
            return result
        raw = (challenge_response(request, bad=True) if request["phase"] == "claim_challenge"
               else adversarial_grade_response(request, decision="dismissed"))
        collection = "issues" if request["phase"] == "claim_challenge" else "claim_checks"
        raw[collection][0]["basis"]["evidence"] = {"quote": "UNTRUSTED_RAW_SECRET_992", "source_path": "teacher_evidence"}
        self.failed = getattr(self, "failed", []) + [deepcopy(raw)]
        return json.dumps(raw)


def test_real_client_first_and_retry_get_complete_schema_leaf_paths_without_duplicate_source(tmp_path):
    client, row = MalformedBasisClient(), sample()
    row["messages"][1]["content"] = "Inspection is only allowed with power off."
    flow = workflow(tmp_path, client)
    evidence = {"teacher_evidence": SOURCE, "teacher_evidence_blocks": [SOURCE, "OTHER_SOURCE_TEXT_663"]}
    check = flow._review_claim_score("sft", row, evidence, ["sample"])
    assert accepted(check) and len(client.calls) == 4
    for call in client.calls:
        actual = metadata(call)
        assert actual["claim_review_wire_schema_version"] == 1
        assert actual["actual_source_leaf_paths"] == [["teacher_evidence"], ["teacher_evidence_blocks", 0], ["teacher_evidence_blocks", 1]]
        assert actual["source_leaf_paths_truncated"] is False
        assert actual["response_json_schema"]["additionalProperties"] is False
        assert "not path components" in actual["source_reference_root"]
        assert actual["claim_review_focus"]["units"]
        assert all(item["unit_id"] in {unit["id"] for unit in call["data"]["target_units"]}
                   for item in actual["claim_review_focus"]["units"])
        assert sum("OTHER_SOURCE_TEXT_663" in message["content"] for message in call["wire"]) == 1
        assert "response_json_schema" not in call["data"]
        assert "response_json_schema" not in canonical(call["wire"][:2])
        assert "UNTRUSTED_RAW_SECRET_992" not in canonical(call["wire"])
        if "format_repair" in call["data"]:
            assert "missing_adversarial_evidence" in call["data"]["format_repair"]["instruction"]
    assert metadata(client.calls[0]) == metadata(client.calls[1])
    assert metadata(client.calls[2]) == metadata(client.calls[3])
    assert client.calls[0]["wire"][:2] == client.calls[1]["wire"][:2]
    assert client.calls[2]["wire"][:2] == client.calls[3]["wire"][:2]
    failures = row["claim_review"]["schema_failures"]
    assert len(failures) == 2
    for failure, rejected in zip(failures, client.failed):
        checkpoint = failure["checkpoint"]
        saved = engine.read_json(flow.path / "checkpoints/review" / (checkpoint["checkpoint_sha256"] + ".json"))
        assert saved["data"] == rejected
        assert checkpoint["response_sha256"] == engine.digest(rejected)


def test_different_candidates_reuse_identical_system_and_source_prefix_before_dynamic_schema(tmp_path):
    client, row = OfflineBlindClient(), sample()
    flow = workflow(tmp_path, client)
    evidence = {"teacher_evidence": SOURCE}
    flow._review_claim_score("sft", row, evidence, ["first"])
    changed = sample()
    changed["messages"][1]["content"] = "Power off first.\n\nOnly then inspect.\n\nRecord the findings."
    flow._review_claim_score("sft", changed, evidence, ["second"])
    for phase in ("claim_challenge", "claim_adjudication"):
        first, second = [call for call in client.calls if call["data"]["phase"] == phase]
        assert first["wire"][:2] == second["wire"][:2]
        assert first["wire"][-1] != second["wire"][-1]
        assert metadata(first)["response_json_schema"]["properties"]["scope_sha256"] != metadata(second)["response_json_schema"]["properties"]["scope_sha256"]
        assert [unit["id"] for unit in first["data"]["target_units"]] != [unit["id"] for unit in second["data"]["target_units"]]
        assert "response_json_schema" not in canonical(first["wire"][:2])
        assert '"claim_review_focus":' not in "\n".join(message["content"] for message in first["wire"][:2])


def test_missing_new_markers_preserves_old_v4_first_and_retry_instruction_literal(tmp_path):
    client, row = MalformedBasisClient(), sample()
    flow = workflow(tmp_path, client)
    flow.recipe.pop("claim_review_wire_schema_version")
    flow.recipe.pop("claim_review_focus_version")
    flow.recipe["node_prompt_templates"]["review"]["workflow.review_reference"] = render(get("workflow.review_reference", "2.0.0"))
    flow._review_claim_score("sft", row, {"teacher_evidence": SOURCE}, ["sample"])
    note = ("\n以下仅说明实际messages索引与本次候选单元id；context证据只能使用合法实际索引。"
            "仅复制request.scope.scope_sha256为返回的scope_sha256，不返回整个scope；候选评审只引用本次unit_id，不抄quote。\n")
    for call in client.calls:
        data = {key: value for key, value in call["data"].items() if key not in {"format_repair", "source_memory"}}
        # The real transport moves the source to its single system memory.
        source = json.loads(call["wire"][1]["content"].split("\n", 1)[1].rsplit("\n", 1)[0])["source_evidence"]
        data.pop("source_evidence_ref", None)
        data["source_evidence"] = source
        protocol = (_CLAIM_CHALLENGE_V4_WIRE_PROTOCOL if data["phase"] == "claim_challenge"
                    else _CLAIM_ADJUDICATION_V4_WIRE_PROTOCOL)
        expected = protocol + note + canonical(_claim_format_boundaries(data, data["phase"]))
        assert expected in call["wire"][0]["content"]
        assert "response_json_schema" not in canonical(call["wire"])
        assert "claim_review_focus" not in canonical(call["wire"])


@pytest.mark.parametrize("protocol", [1, 2, 3])
def test_old_protocols_first_and_retry_wire_is_byte_equal_with_new_markers_ignored(tmp_path, protocol):
    class CaptureClient(OfflineBlindClient):
        def chat(self, wire, **kwargs):
            self.calls.append(deepcopy(wire))
            return "{}"

    old_client, new_client = CaptureClient(), CaptureClient()
    old, new = workflow(tmp_path / "old", old_client), workflow(tmp_path / "new", new_client)
    old.recipe.pop("claim_review_wire_schema_version")
    old.recipe.pop("claim_review_focus_version")
    request = {"claim_review_protocol_version": protocol,
        "scope": {"message_index": 1, "scope_sha256": "a" * 64},
        "messages": [{"role": "user", "content": "Can I inspect now?"},
                     {"role": "assistant", "content": "Power off first."}],
        "source_evidence": {"teacher_evidence": SOURCE}, "target_fields": {"content": "Power off first."}}
    if protocol == 3:
        request["target_units"] = [{"id": "m1u0001", "field": "content", "start": 0, "end": 16, "text": "Power off first."}]
    for flow in (old, new):
        attempts = []
        def validator(raw, actual):
            attempts.append(raw)
            if len(attempts) == 1:
                raise ValueError("invalid_claim_review_source_path")
            return raw
        flow._claim_v2_call(request, prompt_id="workflow.review_score", base_key=["legacy"], role="generation",
            validator=validator, audit={"schema_failures": []}, phase="claim_review", instruction="FROZEN_LITERAL_PROTOCOL")
    assert len(old_client.calls) == len(new_client.calls) == 2
    assert canonical(old_client.calls) == canonical(new_client.calls)


@pytest.mark.parametrize("name", ["claim_review_wire_schema_version", "claim_review_focus_version"])
@pytest.mark.parametrize("value", [True, False, 0, 2, "1", []])
def test_new_markers_are_strict_before_any_model_call(tmp_path, name, value):
    client = OfflineBlindClient()
    flow = workflow(tmp_path, client)
    flow.recipe[name] = value
    with pytest.raises(ValueError, match="invalid_" + name):
        flow._review_claim_score("sft", sample(), {"teacher_evidence": SOURCE}, ["sample"])
    assert not client.calls
    with pytest.raises(ValueError, match="invalid_" + name):
        snapshot_node_prompts(None, recipe_version=22, **{name: value})


def test_focus_catalog_is_opt_in_and_preserves_user_custom_prompts():
    args = {"recipe_version": 22, "claim_review_protocol_version": 2, "claim_review_adversarial_protocol_version": 1}
    old = snapshot_node_prompts(None, **args)
    new = snapshot_node_prompts(None, **args, claim_review_focus_version=1, claim_review_wire_schema_version=1)
    assert old["review"]["workflow.review_reference"] == render(get("workflow.review_reference", "2.0.0"))
    assert new["review"]["workflow.review_reference"] == render(get("workflow.review_reference", "2.0.1"))
    assert old["review"]["workflow.review_score"] == new["review"]["workflow.review_score"]
    custom = {"review": {"workflow.review_reference": "USER_DEFINED_FOCUS_STANDARD_663"}}
    assert snapshot_node_prompts(custom, **args, claim_review_focus_version=1)["review"]["workflow.review_reference"] == custom["review"]["workflow.review_reference"]


def test_source_path_enumeration_is_safe_bounded_and_does_not_echo_text():
    value = {"teacher_evidence": ["PRIVATE_SOURCE_TEXT_772"] * 100,
             "evil\nPRIVATE_KEY_998": "secret", "deep": {"child": {"body": "private"}}}
    result = source_leaf_paths(value)
    assert len(result["actual_source_leaf_paths"]) == 64
    assert result["source_leaf_paths_truncated"] is True
    assert "PRIVATE" not in canonical(result)
    request, = challenge_review_requests("sft", sample(), {"teacher_evidence": SOURCE})
    assert claim_review_wire_schema_metadata(request)["actual_source_leaf_paths"] == [["teacher_evidence"]]
