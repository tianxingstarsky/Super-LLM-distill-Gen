"""Current user edits reach every bound review context without changing history."""
import json

import pytest

from lib.domain.workflow_quality import accepted
from tests.test_blind_claim_workflow import OfflineBlindClient
from tests.test_condition_review_workflow import flow, evidence, sourced_sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


INSTRUCTION = "Include a Safety reminder in the answer while preserving the power-off condition."


class EditReviewer(OfflineBlindClient):
    def chat(self, wire, **kwargs):
        response = json.loads(super().chat(wire, **kwargs))
        request = model_request_data(wire)
        if (request.get("phase") == "claim_adjudication"
                and request.get("review_requirements", {}).get("revision_instruction") == INSTRUCTION
                and "Safety reminder" not in request["target_fields"]["content"]):
            response["verdict"]["keep"] = False
            response["verdict"]["scores"]["instruction"] = 1
            response["verdict"]["reason"] = "The current answer still omits the explicitly requested safety reminder."
        return json.dumps(response)


@pytest.mark.parametrize("fulfilled", [False, True])
def test_revision_requirement_is_bound_to_both_contexts_and_blocks_unfulfilled_edit(tmp_path, fulfilled):
    client, row = EditReviewer(), sourced_sample()
    if fulfilled:
        row["messages"][1]["content"] = "Safety reminder: " + row["messages"][1]["content"]
    workflow = flow(tmp_path, client)
    assert workflow.recipe["claim_review_revision_instruction_version"] == 1
    result = workflow._review_score("sft", row, evidence(), ["edit"], INSTRUCTION)
    assert accepted(result) is fulfilled
    assert result["correctness"] == 5
    assert len(client.calls) == 2
    for call in client.calls:
        assert call["data"]["review_requirements"]["revision_instruction"] == INSTRUCTION
        assert "它不是事实证据" in call["wire"][0]["content"]
    audit = row["claim_review"]
    for receipt in audit["challenge_reviews"] + audit["assessments"]:
        assert receipt["review"]["request"]["review_requirements"]["revision_instruction"] == INSTRUCTION
    assert workflow._review_score("sft", row, evidence(), ["edit"], INSTRUCTION) == result
    assert len(client.calls) == 2
    # A changed user edit has a different scope, never an inherited approval.
    workflow._review_score("sft", row, evidence(), ["edit"], INSTRUCTION + " Keep the original wording.")
    assert len(client.calls) == 4
    assert client.calls[0]["data"]["scope"]["scope_sha256"] != client.calls[2]["data"]["scope"]["scope_sha256"]


def test_historical_unmarked_recipe_keeps_old_scope_and_existing_checkpoints(tmp_path):
    client, row = EditReviewer(), sourced_sample()
    workflow = flow(tmp_path, client)
    workflow.recipe.pop("claim_review_revision_instruction_version")
    assert accepted(workflow._review_score("sft", row, evidence(), ["old"], INSTRUCTION))
    assert len(client.calls) == 2
    assert all("revision_instruction" not in call["data"].get("review_requirements", {}) for call in client.calls)
    workflow._review_score("sft", row, evidence(), ["old"], INSTRUCTION + " Changed legacy note.")
    assert len(client.calls) == 2


@pytest.mark.parametrize("value", [None, False, 0, {}, [], " ", "bad\x00note", "x" * 16_001])
def test_invalid_revision_instruction_fails_before_any_model_call(tmp_path, value):
    client = EditReviewer()
    workflow = flow(tmp_path, client)
    with pytest.raises(ValueError, match="invalid_claim_review_revision_instruction"):
        workflow._review_score("sft", sourced_sample(), evidence(), ["invalid"], value)
    assert client.calls == []
