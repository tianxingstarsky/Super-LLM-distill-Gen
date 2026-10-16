"""A contract-output failure cannot become a review of diagnostic-only text."""
from copy import deepcopy
from pathlib import Path

import pytest

from lib.infrastructure.training_workflow import digest
from lib.infrastructure.workflow_production import _quality_assessment_valid
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def failed_contract():
    messages = [
        {"role": "user", "content": "先给我一个可用的清洗计划。"},
        {"role": "assistant", "content": "先按来源去重，再检查关键条件。", "reasoning_content": "去重对象必须保留原始来源。"},
        {"role": "user", "content": "第二项要保留冲突的版本，调整一下。"},
        {"role": "assistant", "content": "保留冲突版本并分别记录来源。", "reasoning_content": "冲突本身是待核对信息，不能静默丢弃。"},
    ]
    quotes = ["Conflicting versions retain independent provenance."]
    return {"id": "failed-contract", "source_id": "paper", "status": "quarantined",
        "reason": "multiturn_contract_output_invalid", "failed_turn": 2,
        "feedback": "invalid_qa_director_judge_schema", "accepted_prefix": deepcopy(messages[:2]),
        "failed_generation": {"messages": messages, "quotes": quotes,
            "candidate_sha256": digest({"messages": messages, "quotes": quotes}),
            "status": "unaccepted_generation", "attempt": 0, "turn": 2, "training_eligible": False},
        "source_context": {"kind": "document", "text": quotes[0]},
        "turn_reviews": [{"turn": 1, "end_message_index": 1, "reasoning_origin": "prompt_styled_generation"}]}


class Probe(WorkflowReviewRepair):
    def __init__(self, marker=1):
        self.recipe = {"multiturn_terminal_review_version": 1,
            "review_repair": {"mode": "auto", "max_rounds": 2}}
        if marker is not None:
            self.recipe["multiturn_failure_audit_version"] = marker

    def _review_evidence(self, row):
        raise RuntimeError("existing_review_path")

    def ask(self, *args, **kwargs):
        pytest.fail("Diagnostic-only generation must never buy a review or invent a conversation.")


@pytest.mark.parametrize("messages", ["absent", None, []])
def test_contract_failure_preserves_actual_reason_and_full_diagnostics_without_review(messages):
    original = failed_contract()
    if messages != "absent":
        original["messages"] = messages
    frozen = deepcopy(original)
    result, = Probe()._review_repair_item({"target": "multiturn", "record": original})
    assert original == frozen and result["status"] == "quarantined"
    assert all(result[field] == value for field, value in frozen.items())
    receipt = result["review_repair"]
    assert receipt["reason"] == receipt["original_reason"] == "multiturn_contract_output_invalid"
    assert receipt["assessment"] == "not_performed_terminal_generation" and receipt["terminal"] is True
    assert receipt["status"] == "rejected" and receipt["attempts"] == 0 and receipt["score"] is None
    assert receipt["history"] == [] and "verdict" not in receipt
    assert result["failed_generation"]["training_eligible"] is False and not _quality_assessment_valid(result)
    result["failed_generation"]["messages"][1]["reasoning_content"] = "mutated output"
    assert original == frozen


@pytest.mark.parametrize("marker", [None, 0, True, 1.0, "1", 2])
def test_old_and_malformed_failure_markers_keep_the_existing_review_path(marker):
    with pytest.raises(RuntimeError, match="existing_review_path"):
        Probe(marker)._review_repair_item({"target": "multiturn", "record": failed_contract()})


@pytest.mark.parametrize("target,changes", [
    ("sft", {}),
    ("multiturn", {"status": "eligible"}),
    ("multiturn", {"reason": "multiturn_contract_rejected"}),
    ("multiturn", {"messages": [{"role": "user", "content": "现有可编辑任务。"},
                                {"role": "assistant", "content": "现有待审核回答。"}]}),
])
def test_new_skip_does_not_bypass_review_for_complete_or_unrelated_candidates(target, changes):
    original = {**failed_contract(), **deepcopy(changes)}
    with pytest.raises(RuntimeError, match="existing_review_path"):
        Probe()._review_repair_item({"target": target, "record": original})


def test_execute_keeps_the_schema_failure_isolated_and_exports_no_diagnostic_messages(tmp_path, monkeypatch):
    from lib.infrastructure import training_workflow as engine

    source = tmp_path / "paper.txt"
    source.write_text("Conflicting versions retain independent provenance.", encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["multiturn"],
        review_repair={"mode": "auto", "max_rounds": 2},
        production={"version": 2, "goals": {"multiturn": 1}, "max_attempts": 1,
                    "max_rounds": 1, "round_size": 1})
    path = engine.run_path(output, run_id)
    assert engine.read_json(path / "recipe.json")["multiturn_failure_audit_version"] == 1

    class NoCalls:
        model = "offline-contract-failure"
        usage = {}

        def chat(self, *args, **kwargs):
            pytest.fail("A diagnostic body cannot be promoted into another paid workflow.")

    def terminal(workflow, unit):
        return [{**workflow.rejected(unit, "multiturn_contract_output_invalid"),
                 **{key: value for key, value in failed_contract().items() if key not in {"id", "source_id"}}}]

    monkeypatch.setattr(engine.Workflow, "multiturn", terminal)
    root = Path(__file__).resolve().parents[1]
    result = engine.Workflow(output, run_id, root, generator=NoCalls(), jev=NoCalls()).execute()
    assert result["status"] == "needs_attention", result.get("error")
    record, = engine.read_json(path / "artifacts/multiturn.records.json")
    assert record["reason"] == "multiturn_contract_output_invalid" and record["failed_turn"] == 2
    assert record["feedback"] == "invalid_qa_director_judge_schema" and not record.get("messages")
    assert record["failed_generation"] == failed_contract()["failed_generation"]
    assert record["accepted_prefix"] == failed_contract()["accepted_prefix"]
    assert record["review_repair"]["assessment"] == "not_performed_terminal_generation"
    assert not _quality_assessment_valid(record)
    assert (path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8") == ""
    assert engine.verify_artifacts(path)["counts"] == {"multiturn": 0}
