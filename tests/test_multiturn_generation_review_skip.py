"""Terminal generation diagnostics survive review without inventing a dialogue."""
from copy import deepcopy
from pathlib import Path

import pytest

from lib.infrastructure.workflow_production import _quality_assessment_valid
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def failed_generation():
    # Matches a failed second answer: the accepted first turn has an audit,
    # but generation did not return a complete training messages payload.
    return {"id": "failed-dialogue", "source_id": "paper", "status": "quarantined",
            "reason": "multiturn_turn_failed_after_repair", "failed_turn": 2,
            "feedback": "文档回答缺少可逐字匹配的原文依据",
            "turn_reviews": [{"turn": 1, "keep": True,
                              "reasoning_origin": "prompt_styled_generation"}],
            "qa_contract": {"learning_goal": "knowledge_application", "evidence_mode": "closed_book"}}


class Probe(WorkflowReviewRepair):
    def __init__(self):
        self.recipe = {"multiturn_terminal_review_version": 1,
                       "review_repair": {"mode": "auto", "max_rounds": 2}}

    def _review_evidence(self, _row):
        raise RuntimeError("existing_evidence_path")

    def ask(self, *_args, **_kwargs):
        pytest.fail("A missing generated dialogue must not be invented by a reviewer.")


@pytest.mark.parametrize("messages_value", ["absent", None, []])
def test_failed_turn_without_messages_keeps_every_generation_diagnostic(messages_value):
    original = failed_generation()
    if messages_value != "absent":
        original["messages"] = messages_value
    before = deepcopy(original)
    result, = Probe()._review_repair_item({"target": "multiturn", "record": original})
    assert original == before
    for key, value in before.items():
        assert result[key] == value
    receipt = result["review_repair"]
    assert result["status"] == "quarantined" and result["_review_target"] == "multiturn"
    assert receipt["reason"] == receipt["original_reason"] == original["reason"]
    assert receipt["assessment"] == "not_performed_terminal_generation"
    assert receipt["terminal"] is True and receipt["status"] == "rejected"
    assert receipt["attempts"] == 0 and receipt["score"] is None and receipt["history"] == []
    assert "verdict" not in receipt
    if messages_value == []:
        from lib.infrastructure.training_workflow import digest
        assert receipt["candidate_sha256"] == digest({"messages": []})
    else:
        assert receipt["candidate_sha256"] is None
    assert not _quality_assessment_valid(result)
    result["turn_reviews"][0]["keep"] = False
    assert original == before


@pytest.mark.parametrize("marker", [None, 0])
def test_old_frozen_recipe_retains_its_original_failed_turn_review_path(marker):
    probe = Probe()
    if marker is None:
        probe.recipe.pop("multiturn_terminal_review_version")
    else:
        probe.recipe["multiturn_terminal_review_version"] = marker
    with pytest.raises(RuntimeError, match="existing_evidence_path"):
        probe._review_repair_item({"target": "multiturn", "record": failed_generation()})


@pytest.mark.parametrize("target,changes", [
    ("sft", {}),
    ("multiturn", {"reason": "invalid_multiturn_answer"}),
    ("multiturn", {"status": "eligible"}),
    ("multiturn", {"messages": [{"role": "user", "content": "怎么检查？"},
                                {"role": "assistant", "content": "先断电。"}]}),
])
def test_skip_is_not_a_general_escape_from_review_for_other_or_repairable_candidates(target, changes):
    original = {**failed_generation(), **deepcopy(changes)}
    with pytest.raises(RuntimeError, match="existing_evidence_path"):
        Probe()._review_repair_item({"target": target, "record": original})


def test_execute_keeps_real_failed_turn_shape_isolated_and_exports_no_samples(tmp_path, monkeypatch):
    from lib.infrastructure import training_workflow as engine

    source = tmp_path / "paper.txt"
    source.write_text("Power must be turned off before inspection.", encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["multiturn"],
        conversation_turns=3, review_repair={"mode": "auto", "max_rounds": 2},
        production={"version": 2, "goals": {"multiturn": 1}, "max_attempts": 1,
                    "max_rounds": 1, "round_size": 1})
    path = engine.run_path(output, run_id)
    assert engine.read_json(path / "recipe.json")["multiturn_terminal_review_version"] == 1

    class NoCalls:
        model = "offline-terminal-generation"
        usage = {}
        def chat(self, *_args, **_kwargs):
            pytest.fail("A generation failure without a candidate must not request paid review or repair.")

    def terminal(workflow, unit):
        return [{**workflow.rejected(unit, "multiturn_turn_failed_after_repair"),
                 **{key: value for key, value in failed_generation().items() if key not in {"id", "source_id"}}}]

    monkeypatch.setattr(engine.Workflow, "multiturn", terminal)
    root = Path(__file__).resolve().parents[1]
    result = engine.Workflow(output, run_id, root, generator=NoCalls(), jev=NoCalls()).execute()
    assert result["status"] == "needs_attention", result.get("error")
    records = engine.read_json(path / "artifacts/multiturn.records.json")
    assert len(records) == 1
    record = records[0]
    assert record["reason"] == "multiturn_turn_failed_after_repair"
    assert record["failed_turn"] == 2 and record["feedback"] == failed_generation()["feedback"]
    assert record["turn_reviews"] == failed_generation()["turn_reviews"]
    assert not record.get("messages") and "request_error" not in record
    assert record["review_repair"]["assessment"] == "not_performed_terminal_generation"
    assert not _quality_assessment_valid(record)
    assert (path / "artifacts/multiturn.jsonl").read_text("utf-8") == ""
    assert engine.verify_artifacts(path)["counts"] == {"multiturn": 0}
