"""Real observation storage, batch ownership and proposal reuse without model APIs."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib.contexts.workflows.application.experience_evolution import EvolveExperience
from lib.contexts.workflows.domain.experience_evolution import digest
from lib.contexts.workflows.infrastructure.experience_evolution import ExperienceEvolutionRepository
from lib.contexts.workflows.infrastructure.experience_evolution_model import ExperienceEvolutionModelPort
from lib.contexts.review.infrastructure.experience_observations import record_advisory_review_observation
from lib.io_utils import atomic_json


class ProposalPort(ExperienceEvolutionModelPort):
    calls = 0

    def propose(self, payload):
        self.calls += 1
        self.last_payload = deepcopy(payload)
        return {"summary": "Retain confirmed constraints; investigate uncertain outcomes.", "rules": []}


def application(tmp_path):
    repository = ExperienceEvolutionRepository(tmp_path)
    port = ProposalPort(repository, tmp_path)
    return EvolveExperience(port), port


def episode(identifier="one", reasoning=True):
    assistant = {"role": "assistant", "content": "A conditional answer."}
    if reasoning:
        assistant["reasoning_content"] = "I need to check this condition, not infer the missing premise."
    return {"id": identifier, "messages": [{"role": "user", "content": "Does it apply here?"}, assistant],
        "review": {"reason": "The required premise was not supplied.", "keep": False}}


def test_recording_is_idempotent_and_claims_are_disjoint(tmp_path):
    app, port = application(tmp_path)
    first = app.record_observation("reasoning", episode(), run_id="a" * 32, outcome="unknown")
    assert app.record_observation("reasoning", episode(), run_id="a" * 32, outcome="unknown")["recorded"] is False
    changed = app.record_observation("reasoning", episode(), run_id="a" * 32,
                                     outcome="failure", evidence_status="human_confirmed")
    assert first["id"] != changed["id"]
    batch = app.claim_observation_batch("reasoning", max_records=1)
    other = app.claim_observation_batch("reasoning", max_records=1)
    assert batch["records"][0]["id"] != other["records"][0]["id"]
    assert batch["records"][0]["observed_outcome"] == "unknown"
    assert port.calls == 0
    app.release_observation_batch(other["id"])
    assert app.claim_observation_batch("reasoning")["records"][0]["id"] == changed["id"]


def test_explicit_batch_proposal_reuses_saved_edition_without_another_call(tmp_path):
    app, port = application(tmp_path)
    app.record_observation("reasoning", episode(), run_id="a" * 32, outcome="advisory_release", evidence_status="model_assessed")
    app.record_observation("reasoning", episode("two"), run_id="a" * 32, outcome="unknown")
    batch = app.claim_observation_batch("reasoning")
    assert port.calls == 0
    edition = app.propose_observation_batch(batch["id"], "Check actual constraints.")
    assert port.calls == 1
    assert len(edition["episodes"]) == 2
    assert edition["quality"] == "model_proposed_not_verified"
    assert edition["status"] == "candidate"
    assert app.propose_observation_batch(batch["id"], "Check actual constraints.") == edition
    assert port.calls == 1
    assert app.get_observation_batch(batch["id"])["status"] == "proposed"
    assert app.claim_observation_batch("reasoning")["status"] == "empty"
    with pytest.raises(ValueError, match="request_changed"):
        app.propose_observation_batch(batch["id"], "Different instructions.")


def test_review_projection_preserves_raw_record_and_never_fabricates_reasoning(tmp_path):
    app, port = application(tmp_path)
    row = {"id": "candidate", "status": "eligible", "messages": episode(reasoning=False)["messages"],
        "review_repair": {"status": "advisory", "verdict": {"keep": False, "reason": "The premise is not established."}},
        "claim_review": {"large_original_audit": "Complete original conclusions and quotations."}}
    runtime = SimpleNamespace(recipe={"review_advisory_policy_version": 1}, state={"id": "a" * 32, "recipe_hash": "f" * 64})
    receipt = record_advisory_review_observation(runtime, "sft", row, app)
    stored = app.get_observation(receipt["id"])
    assert stored["source_record"] == row
    assert stored["outcome"] == "advisory_release"
    assert stored["evidence_status"] == "model_assessed"
    assert "reasoning_content" not in stored["episode"]["conversation"]["messages"][-1]
    assert "reasoning" not in stored["episode"]["conversation"]
    assert port.calls == 0
    replay = {**deepcopy(row), "experience_observation": receipt}
    assert record_advisory_review_observation(runtime, "sft", replay, app)["recorded"] is False
    runtime.recipe = {}
    assert record_advisory_review_observation(runtime, "sft", row, app) is None


def test_oversized_episode_is_preserved_not_silently_clipped_or_consumed(tmp_path):
    app, _ = application(tmp_path)
    record = app.record_observation("reasoning", episode(), run_id="a" * 32)
    result = app.claim_observation_batch("reasoning", max_chars=10)
    assert result["status"] == "needs_capacity"
    assert result["blocked_observation_id"] == record["id"]
    assert app.get_observation(record["id"])["collection_state"] == "pending"
    assert app.claim_observation_batch("reasoning")["records"][0]["messages"] == episode()["messages"]


def test_official_edition_completes_its_exact_observation_without_a_batch_id_in_edition(tmp_path):
    app, port = application(tmp_path)
    observation = app.record_observation("reasoning", episode(), run_id="a" * 32)
    batch = app.claim_observation_batch("reasoning", max_records=1)
    edition = app.propose("reasoning", "Check actual conditions.", batch["records"],
        source_receipt={"observation_sha256": digest(batch["records"][0])})
    assert "batch_id" not in edition["source_receipt"]
    app.complete_official_observation_batch(batch["id"], edition["id"])
    app.complete_official_observation_batch(batch["id"], edition["id"])
    stored = app.get_observation_batch(batch["id"])
    assert stored["status"] == "proposed" and stored["edition_id"] == edition["id"]
    assert app.get_observation(observation["id"])["collection_state"] == "proposed"
    assert app.claim_observation_batch("reasoning")["status"] == "empty"
    assert port.calls == 1


def no_update_outcome(batch, *, parent_id=None):
    return {"status": "no_update", "scope": batch["scope"], "parent_id": parent_id,
        "source_sha256": "b" * 64,
        "source_receipt": {"observation_sha256": digest(batch["records"][0])},
        "upstream_result": {"memory": {"memory_items": []}},
        "model_request_receipt": [{"request_id": "protocol-fixture", "response_details": {
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}}}]}


def persist_no_update(repository, outcome):
    receipt = {**outcome, "result_sha256": digest(outcome)}
    atomic_json(repository.root / "official-editions" / (outcome["source_sha256"] + ".json"), receipt)
    return receipt


def test_official_no_update_consumes_once_and_preserves_parent_and_request_receipt(tmp_path):
    app, port = application(tmp_path)
    parent = app.save_version("reasoning", "Keep the original experience.")
    observation = app.record_observation("reasoning", episode(), run_id="a" * 32)
    batch = app.claim_observation_batch("reasoning", max_records=1)
    outcome = no_update_outcome(batch, parent_id=parent["id"])
    with pytest.raises(ValueError, match="official_receipt_changed"):
        app.complete_official_observation_batch(batch["id"], outcome)
    assert app.get_observation_batch(batch["id"])["status"] == "claimed"
    receipt = persist_no_update(port.repository, outcome)
    app.complete_official_observation_batch(batch["id"], outcome)
    app.complete_official_observation_batch(batch["id"], receipt)
    restored, _ = application(tmp_path)
    stored = restored.get_observation_batch(batch["id"])
    assert stored["status"] == "no_update" and "edition_id" not in stored
    assert stored["official_result_receipt"] == receipt
    assert restored.get_observation(observation["id"])["collection_state"] == "no_update"
    assert restored.claim_observation_batch("reasoning")["status"] == "empty"
    assert restored.get_version(parent["id"]) == parent
    assert [version["id"] for version in restored.list_versions("reasoning")] == [parent["id"]]
    assert port.calls == 0
    with pytest.raises(ValueError, match="not_releasable"):
        restored.release_observation_batch(batch["id"])


@pytest.mark.parametrize("mismatch", ["scope", "observation", "receipt"])
def test_official_no_update_rejects_other_source_or_changed_receipt(tmp_path, mismatch):
    app, port = application(tmp_path)
    observation = app.record_observation("reasoning", episode(), run_id="a" * 32)
    batch = app.claim_observation_batch("reasoning", max_records=1)
    outcome = no_update_outcome(batch)
    if mismatch == "scope":
        outcome["scope"] = "answer"
    elif mismatch == "observation":
        outcome["source_receipt"]["observation_sha256"] = "f" * 64
    persist_no_update(port.repository, outcome)
    if mismatch == "receipt":
        outcome["model_request_receipt"][0]["response_details"]["usage"]["completion_tokens"] += 1
    with pytest.raises(ValueError, match="official_(batch_mismatch|receipt_changed)"):
        app.complete_official_observation_batch(batch["id"], outcome)
    assert app.get_observation_batch(batch["id"])["status"] == "claimed"
    assert app.get_observation(observation["id"])["collection_state"] == "claimed"
    assert port.calls == 0
