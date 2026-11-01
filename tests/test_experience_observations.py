"""Real observation storage, batch ownership and proposal reuse without model APIs."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib.contexts.workflows.application.experience_evolution import EvolveExperience
from lib.contexts.workflows.infrastructure.experience_evolution import ExperienceEvolutionRepository
from lib.contexts.workflows.infrastructure.experience_evolution_model import ExperienceEvolutionModelPort
from lib.contexts.review.infrastructure.experience_observations import record_advisory_review_observation


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
