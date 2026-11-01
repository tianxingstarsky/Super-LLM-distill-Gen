"""Stopped-run reuse authenticates the exact candidate, sources and paid evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from lib.bootstrap.automatic_review import checkpoint_review_input, reviewed_checkpoint_input
from lib.contexts.review.domain.repair import validate_repair_inputs
from lib.contexts.review.infrastructure.automatic_adapter import CheckpointedReviewAdapter
from lib.contexts.review.infrastructure.checkpoint_parent import (
    verify_checkpoint_review_input, checkpoint_review_hints,
)
from lib.infrastructure.workflow_store import digest, file_hash


def test_high_review_score_cannot_clear_known_generation_issues(monkeypatch):
    from lib.contexts.review.application.review_candidate import ReviewCandidate
    row = json.loads((Path(__file__).parent / "fixtures/native_sft_control_owner.json").read_text("utf-8"))["record"]
    row["native_generation"] = {"issues": ["native_instruction_reference_unresolved",
                                           "native_generation_instruction_leakage"]}
    original = deepcopy(row)
    port = SimpleNamespace(authenticated_revision=True,
        evidence=lambda record: {"text": "Only the original method section."},
        payload=lambda target, record: {"messages": record["messages"]})

    def high_score(self, target, original, candidate, evidence, instruction, item, config, receipt):
        candidate.update(status="eligible")
        candidate.pop("reason", None)
        receipt.update(status="accepted", score=1.0)
        return candidate, receipt

    monkeypatch.setattr(ReviewCandidate, "_review_automatically", high_score)
    reviewer = ReviewCandidate(port, {"review_repair": {"mode": "auto", "max_rounds": 0, "score_threshold": .8}})
    checked = reviewer.execute({"target": "sft", "record": row,
        "checkpoint_context": {"author_control": {}}})[0]
    assert row == original
    assert checked["messages"] == original["messages"]
    assert checked["status"] == "quarantined"
    assert checked["review_repair"]["semantic_status"] == "accepted"
    assert checked["review_repair"]["preserved_generation_issues"] == row["native_generation"]["issues"]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def stopped_parent(tmp_path):
    run_id = "a" * 32
    parent = tmp_path / "workflows" / run_id
    source = parent / "inputs" / "paper.txt"
    source.parent.mkdir(parents=True)
    source.write_text("Only the supplied method section.", encoding="utf-8")
    source_hash = file_hash(source)
    recipe = {"sources": [{"file": "paper.txt", "sha256": source_hash, "kind": "document"}]}
    row = {"id": "sample-one", "status": "quarantined", "reason": "native_instruction_reference_unresolved",
        "source_id": source_hash, "question": "What does this method do?", "answer": "It groups examples.",
        "reasoning": "I need to check the supported scope first.",
        "source_context": {"kind": "document", "text": source.read_text(encoding="utf-8")},
        "messages": [{"role": "user", "content": "What does this method do?"},
            {"role": "assistant", "content": "It groups examples.", "reasoning_content": "I need to check the supported scope first."}]}
    write(parent / "recipe.json", recipe)
    write(parent / "state.json", {"id": run_id, "status": "failed", "recipe_hash": digest(recipe)})
    write(parent / "stage-results/sft.jsonl", row)
    observation = {"phase": "claim_challenge", "condition_ledger": [], "issues": []}
    write(parent / "checkpoints/review/old.json", {"data": observation, "sha256": digest(observation)})
    return tmp_path, parent, row


def test_reuse_is_exact_and_old_review_is_only_an_observation(stopped_parent):
    output, parent, row = stopped_parent
    before = {str(path.relative_to(parent)): path.read_bytes() for path in parent.rglob("*") if path.is_file()}
    item = checkpoint_review_input(output, parent.name, "sft", row["id"])
    assert item["record"] == row
    assert verify_checkpoint_review_input(output, item) == item
    assert validate_repair_inputs([item], {"mode": "auto", "max_rounds": 0, "score_threshold": .8}) == [item]
    hints = checkpoint_review_hints(output, item["checkpoint_context"])
    assert len(hints) == 1
    assert hints[0]["usage"] == "prior_observation_only_not_release_approval"
    assert item["record"]["reason"] == "native_instruction_reference_unresolved"
    assert not (parent / "artifacts/manifest.json").exists()
    for relative, content in before.items():
        assert (parent / relative).read_bytes() == content


@pytest.mark.parametrize("changed", ["record", "stage", "source", "checkpoint", "recipe"])
def test_reuse_rejects_changed_parent_or_candidate(stopped_parent, changed):
    output, parent, row = stopped_parent
    item = checkpoint_review_input(output, parent.name, "sft", row["id"])
    if changed == "record":
        item["record"]["answer"] = "Changed"
    elif changed == "stage":
        write(parent / "stage-results/sft.jsonl", {**row, "reasoning": "changed reasoning"})
    elif changed == "source":
        (parent / "inputs/paper.txt").write_text("different evidence", encoding="utf-8")
    elif changed == "checkpoint":
        value = {"keep": True}
        write(parent / "checkpoints/review/old.json", {"data": value, "sha256": digest(value)})
    else:
        write(parent / "recipe.json", {"sources": []})
    with pytest.raises(ValueError):
        verify_checkpoint_review_input(output, item)


def test_checkpoint_reuse_cannot_authorize_edits_or_repair(stopped_parent):
    output, parent, row = stopped_parent
    item = checkpoint_review_input(output, parent.name, "sft", row["id"])
    for config in ({"mode": "auto", "max_rounds": 1}, {"mode": "human", "max_rounds": 0}):
        with pytest.raises(ValueError, match="unmodified_review_only"):
            validate_repair_inputs([item], config)
    edited = {**deepcopy(item), "answer": "different answer"}
    with pytest.raises(ValueError, match="unmodified_review_only"):
        validate_repair_inputs([edited], {"mode": "auto", "max_rounds": 0})
    class Runtime:
        _authenticated_repair_branch = False
    adapter = CheckpointedReviewAdapter(Runtime())
    with pytest.raises(ValueError, match="not_authenticated"):
        adapter.evidence({"checkpoint_context": item["checkpoint_context"]})
    adapter._runtime._authenticated_repair_branch = True
    assert adapter.evidence({"checkpoint_context": item["checkpoint_context"]}) == item["checkpoint_context"]["evidence"]


def test_sealed_review_stage_uses_a_separate_version_and_verifies_artifacts(stopped_parent):
    output, parent, row = stopped_parent
    state = json.loads((parent / "state.json").read_text(encoding="utf-8"))
    state["status"] = "needs_attention"
    write(parent / "state.json", state)
    reviewed = {**row, "review_repair": {"status": "rejected", "verdict": {"keep": False}}}
    write(parent / "stage-results/review-sft.jsonl", reviewed)
    write(parent / "artifacts/sft.records.json", [reviewed])
    write(parent / "artifacts/manifest.json", {"run_id": parent.name, "status": "complete",
        "recipe_hash": state["recipe_hash"], "sha256": {"sft.records.json": file_hash(parent / "artifacts/sft.records.json")}})
    with pytest.raises(ValueError, match="not_stopped"):
        checkpoint_review_input(output, parent.name, "sft", row["id"])
    item = reviewed_checkpoint_input(output, parent.name, "sft", row["id"])
    assert item["checkpoint_context"]["version"] == 2
    assert item["record"] == reviewed
    assert verify_checkpoint_review_input(output, item) == item
    write(parent / "artifacts/sft.records.json", [{**reviewed, "answer": "changed"}])
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        verify_checkpoint_review_input(output, item)
