"""Failed canonical reasoning remains one dependency through review and SFT."""
from copy import deepcopy

import pytest

from lib.domain.workflow_reasoning_route import cot_with_sft_context, finalized_sft_rows
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def source(index):
    return {"id": str(index), "source_id": "source", "status": "eligible", "messages": [
        {"role": "user", "content": "Explain task " + str(index)},
        {"role": "assistant", "content": "An earlier answer."}]}


def canonical(row):
    return cot_with_sft_context(row, {"id": row["id"], "source_id": row["source_id"], "status": "eligible",
        "question": deepcopy(row["messages"][:-1]), "answer": "A canonical answer.",
        "reasoning": ["A concrete explanation of this task."]})


@pytest.mark.parametrize("failures", [set(), {"cot"}, {"trim"}, {"review"},
                                     {"cot", "trim"}, {"trim", "review"}, {"cot", "trim", "review"}])
def test_failure_combinations_preserve_alignment_and_review_canonical_once(tmp_path, failures):
    originals = [source(index) for index in range(5)]
    originals[0] = {"id": "0", "source_id": "source", "status": "quarantined", "reason": "upstream_sft_failed"}
    cot = [canonical(original) for original in originals[1:]]
    if "cot" in failures:
        cot[0] = {"id": "1", "source_id": "source", "status": "quarantined",
                  "reason": "cot_generation_failed_after_repair", "reasoning_route": "sft_via_cot"}
    if "trim" in failures:
        # Reproduce old cached trim output whose rejection lost the route.
        cot[1] = {"id": "2", "source_id": "source", "status": "quarantined",
                  "reason": "reasoning_trim_failed_after_repair"}
    sft = list(finalized_sft_rows(originals, cot))
    reviewed_items = []
    class Review(WorkflowReviewRepair):
        recipe = {"version": 15, "targets": ["sft", "cot"], "review_repair": {"mode": "auto", "max_rounds": 0}}
        path = tmp_path
        def check_cancel(self):
            pass
        def stage_items(self, stage, items, action):
            return [row for item in items for row in action(item)]
        def _review_repair_item(self, item):
            reviewed_items.append((item["target"], item["record"]["id"]))
            row = deepcopy(item["record"])
            if "review" in failures and row["id"] == "3":
                # run_item_safely can also return a compact rejection receipt.
                row = {"id": "3", "source_id": "source", "status": "quarantined", "reason": "invalid_review_output"}
            row.update(_review_target=item["target"], review_repair={"status": "accepted" if row["status"] == "eligible" else "rejected"})
            return [row]
    (tmp_path / "stage-results").mkdir()
    result = Review().review_repair_collections({"sft": sft, "cot": cot})
    final_sft, final_cot = list(result["sft"]), list(result["cot"])
    assert reviewed_items == [("sft", "0"), *[("cot", str(index)) for index in range(1, 5)]]
    assert [row["id"] for row in final_sft] == [str(index) for index in range(5)]
    assert [row["id"] for row in final_cot] == [str(index) for index in range(1, 5)]
    assert [row["status"] for row in final_sft[1:]] == [row["status"] for row in final_cot]
    assert all(row["reasoning_route"] == "sft_via_cot" for row in final_sft[1:])
    assert sum(row["status"] == "eligible" for row in final_sft) == 4 - len(failures)
    assert sum(row["status"] == "eligible" for row in final_cot) == 4 - len(failures)
    for sft_row, cot_row in zip(final_sft[1:], final_cot):
        assert sft_row["review_repair"]["derived_from"]["candidate_id"] == cot_row["id"]
        if "messages" not in sft_row:
            assert sft_row["review_repair"]["candidate_sha256"] is None


def test_missing_canonical_output_still_fails_closed(tmp_path):
    original = source(1)
    with pytest.raises(ValueError, match="cot_sft_lineage_mismatch"):
        list(finalized_sft_rows([original], []))


def test_production_counts_only_successful_linked_outputs_after_trim_and_review_failures(tmp_path, monkeypatch):
    from tests.test_review_repair_engine import SOURCE, RIGHT, WRONG, ROOT, generated, Client, NoCalls
    from lib.infrastructure import training_workflow as engine
    document = tmp_path / "source.txt"
    document.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[document], targets=["sft", "cot"],
        review_repair={"mode": "auto", "max_rounds": 0}, reasoning_trim={"enabled": True, "template": "leakage"},
        production={"goals": {"sft": 1, "cot": 1}, "round_size": 1, "max_rounds": 3, "min_acceptance_rate": 0})
    turns = {"cot": 0, "trim": 0}
    def sft(self, unit):
        row = generated(unit)[0]
        row["messages"][0]["content"] += " Scenario " + unit["id"]
        row["messages"][-1]["content"] = RIGHT
        return [row]
    def cot(self, row):
        turns["cot"] += 1
        answer = WRONG if turns["cot"] == 2 else RIGHT
        return [cot_with_sft_context(row, {"id": row["id"], "source_id": row["source_id"],
            "status": "eligible", "question": row["messages"][:-1], "answer": answer,
            "reasoning": ["Power-off is required before equipment inspection."]})]
    def trim(self, item):
        turns["trim"] += 1
        if turns["trim"] == 1:
            return [{**self.rejected(item["sample"], "reasoning_trim_failed_after_repair"), "trim_target": item["target"]}]
        return [{**deepcopy(item["sample"]), "trim_target": item["target"]}]
    monkeypatch.setattr(engine.Workflow, "sft", sft)
    monkeypatch.setattr(engine.Workflow, "cot", cot)
    monkeypatch.setattr(engine.Workflow, "trim_reasoning", trim)
    client = Client()
    state = engine.Workflow(output, run_id, ROOT, generator=client, judge=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["counts"] == {"sft": 1, "cot": 1}
    assert turns == {"cot": 3, "trim": 3}
    assert len(client.calls) == 2, "Only the second and third canonical CoT rows have reviewable content."
    assert all(call["target"] == "cot" for call in client.calls)
    assert engine.verify_artifacts(engine.run_path(output, run_id))["counts"] == {"sft": 1, "cot": 1}
