"""CoT is a durable downstream SFT step, including partial production rounds."""
from __future__ import annotations

from copy import deepcopy
import json
import pytest

from lib.domain.workflow_reasoning_route import (
    cot_updates_sft, cot_with_sft_context, finalized_cot_sft_row, finalized_sft_rows,
)
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_production import WorkflowProduction
from lib.infrastructure.workflow_rows import WorkflowRows, write_jsonl
from lib.io_utils import atomic_json
from tests.test_workflow_styled_generation import (
    ANSWER, Generator, Reviewer, records, run,
)
from tests.test_workflow_production import Generator as ProductionWriter, TEXT


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All model responses in this module are injected offline."""
    yield


NEW_ANSWER = "先切断设备电源，再检查线路，完成维护后记录结果。"


class RewritingGenerator(Generator):
    def chat(self, messages, **kwargs):
        result = json.loads(super().chat(messages, **kwargs))
        if "已有合格 reference_answer" in messages[0]["content"]:
            result["answer"] = NEW_ANSWER
        return json.dumps(result, ensure_ascii=False)


def set_version(path, version):
    recipe = engine.read_json(path / "recipe.json")
    recipe["version"] = version
    atomic_json(path / "recipe.json", recipe)
    saved = engine.read_json(path / "state.json")
    saved["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", saved)


def native(path, target):
    return [json.loads(line) for line in (path / "artifacts" / f"{target}.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("version, targets, linked", [
    (0, ["sft", "cot"], False), (14, ["sft", "cot"], False),
    (15, ["sft", "cot"], True), (15, ["sft"], False), (15, ["cot"], False),
])
def test_route_is_versioned_and_requires_both_outputs(version, targets, linked):
    assert cot_updates_sft({"version": version, "targets": targets}) is linked


def test_new_sft_delivery_uses_cot_final_answer_and_reasoning(tmp_path):
    output, rid, path = run(tmp_path, node_generation={"cot": {"style": "structured"}})
    assert engine.read_json(path / "recipe.json")["version"] >= 15
    writer = RewritingGenerator()
    state = engine.Workflow(output, rid, tmp_path, generator=writer, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    sft, cot = native(path, "sft")[0], native(path, "cot")[0]
    assert sft["messages"][-1] == {
        "role": "assistant", "content": NEW_ANSWER,
        "reasoning_content": "\n".join(cot["reasoning"]),
    }
    assert cot["answer"] == NEW_ANSWER and cot["question"] == sft["messages"][:-1]
    upstream = json.loads((path / "stage-results/sft.jsonl").read_text(encoding="utf-8"))
    assert upstream["messages"][-1]["content"] == ANSWER
    assert records(path, "sft")[0]["reasoning_route"] == "sft_via_cot"
    assert engine.verify_artifacts(path)["counts"] == {"sft": 1, "cot": 1}


def test_rejected_cot_cannot_fall_back_to_previously_accepted_sft(tmp_path):
    output, rid, path = run(tmp_path, node_generation={"cot": {"style": "structured"}})
    state = engine.Workflow(output, rid, tmp_path, generator=Generator(), jev=Reviewer(reject_style=2)).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["stages"]["sft"]["eligible"] == 1
    assert native(path, "sft") == native(path, "cot") == []
    assert records(path, "sft")[0]["reason"] == "cot_generation_failed_after_repair"


def test_terminal_trim_runs_once_and_both_formats_use_its_result(tmp_path):
    output, rid, path = run(tmp_path, conversation=True,
        node_generation={"cot": {"style": "structured"}},
        reasoning_trim={"enabled": True, "template": "leakage"})
    writer = RewritingGenerator()
    state = engine.Workflow(output, rid, tmp_path, generator=writer, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["stages"]["trim"]["done"] == 1
    assert sum("只生成 replacement reasoning" in call[0]["content"] for call in writer.calls) == 1
    sft, cot = native(path, "sft")[0], native(path, "cot")[0]
    assert sft["messages"][-1]["content"] == cot["answer"] == NEW_ANSWER
    assert sft["messages"][-1]["reasoning_content"] == "\n".join(cot["reasoning"])
    for target in ("sft", "cot"):
        assert "SOURCE_CHAIN_SENTINEL" not in json.dumps(records(path, target), ensure_ascii=False)
        assert records(path, target)[0]["reasoning_trim"]["status"] == "applied"


def test_terminal_trim_failure_quarantines_both_formats(tmp_path):
    output, rid, path = run(tmp_path, conversation=True,
        reasoning_trim={"enabled": True, "template": "leakage"})
    state = engine.Workflow(output, rid, tmp_path, generator=Generator(), jev=Reviewer(reject_meaning=2)).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert native(path, "sft") == native(path, "cot") == []
    assert records(path, "sft")[0]["reason"] == "reasoning_trim_failed_after_repair"


def test_drop_format_removes_only_final_sft_reasoning_field(tmp_path):
    output, rid, path = run(tmp_path, sft_output_style="drop",
                            node_generation={"cot": {"style": "structured"}})
    state = engine.Workflow(output, rid, tmp_path, generator=RewritingGenerator(), jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert native(path, "sft")[0]["messages"][-1] == {"role": "assistant", "content": NEW_ANSWER}
    assert records(path, "sft")[0]["messages"][-1]["reasoning_content"]
    assert native(path, "cot")[0]["reasoning"]


def test_dedup_uses_final_cot_payload_instead_of_distinct_upstream_answers(tmp_path):
    source = tmp_path / "source.jsonl"
    original_answers = [ANSWER, "先关闭设备电源，然后检查线路，最后登记维护结果。"]
    source.write_text("".join(json.dumps({"messages": [
        {"role": "user", "content": "设备应如何维护？"},
        {"role": "assistant", "content": answer,
         "reasoning_content": f"来源表述 {index}：先核对断电条件，再检查线路并记录。"}
    ]}, ensure_ascii=False) + "\n" for index, answer in enumerate(original_answers)), encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=["sft", "cot"],
                            node_generation={"cot": {"style": "structured"}})
    path = engine.run_path(output, rid)
    state = engine.Workflow(output, rid, tmp_path, generator=RewritingGenerator(), jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    upstream = [json.loads(line) for line in (path / "stage-results/sft.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["messages"][-1]["content"] for row in upstream} == set(original_answers)
    assert engine.verify_artifacts(path)["counts"] == {"sft": 1, "cot": 1}
    for target in ("sft", "cot"):
        rows = records(path, target)
        assert sorted(row["status"] for row in rows) == ["duplicate", "eligible"]
        assert next(row for row in rows if row["status"] == "duplicate")["reason"] == "duplicate_training_content"
    assert native(path, "sft")[0]["messages"][-1]["content"] == NEW_ANSWER


def test_resuming_failed_cot_review_reuses_generation_checkpoints(tmp_path):
    output, rid, path = run(tmp_path, node_generation={"cot": {"style": "structured"}})
    writer = RewritingGenerator()
    state = engine.Workflow(output, rid, tmp_path, generator=writer, jev=Reviewer(invalid_style=True)).execute()
    assert state["status"] == "failed" and state["error"] == "invalid_style_judge_schema"
    calls = len(writer.calls)
    state = engine.resume(output, rid, tmp_path, generator=writer, jev=Reviewer())
    assert state["status"] == "completed", state.get("error")
    assert len(writer.calls) == calls
    assert native(path, "sft")[0]["messages"][-1]["content"] == NEW_ANSWER


def test_legacy_dual_output_keeps_its_original_independent_answers(tmp_path):
    output, rid, path = run(tmp_path, node_generation={"cot": {"style": "structured"}})
    set_version(path, 14)
    state = engine.Workflow(output, rid, tmp_path, generator=RewritingGenerator(), jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert native(path, "sft")[0]["messages"][-1]["content"] == ANSWER
    assert native(path, "cot")[0]["answer"] == NEW_ANSWER


def sample_record():
    return {"id": "sample", "source_id": "source", "status": "eligible", "source_name": "trace.jsonl",
        "location": "row 1", "source_context": {"kind": "conversation", "origin": "recorded"},
        "qa_contract": {"mode": "evidence"}, "messages": [
            {"role": "user", "content": "Calculate 2 + 2."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function",
                "function": {"name": "calculator", "arguments": '{"expression":"2+2"}'}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "4"},
            {"role": "assistant", "content": "4", "reasoning_content": "The tool returned 4."}],
        "tools": [{"type": "function", "function": {"name": "calculator", "parameters": {"type": "object"}}}]}


def test_projection_retains_tool_conversation_and_source_metadata():
    source = sample_record()
    saved = deepcopy(source)
    derived = cot_with_sft_context(source, {"id": "sample", "source_id": "source", "status": "eligible",
        "question": source["messages"][:-1], "reasoning": ["Adding two and two gives four."], "answer": "The result is 4."})
    final = finalized_cot_sft_row(derived)
    assert source == saved
    for field in ("tools", "source_name", "location", "source_context", "qa_contract"):
        assert final[field] == source[field]
    assert final["messages"][:-1] == source["messages"][:-1]
    assert final["messages"][-1]["content"] == "The result is 4."


@pytest.mark.parametrize("aliases", [
    {"thinking": "Earlier explicit reasoning."},
    {"thinking": "Earlier explicit reasoning.", "reasoning_content": "Earlier explicit reasoning."},
])
def test_earlier_assistant_reasoning_aliases_are_canonicalized_before_trim(aliases):
    source = sample_record()
    source["messages"][1].update(aliases)
    derived = cot_with_sft_context(source, {"id": "sample", "source_id": "source", "status": "eligible",
        "reasoning": ["The tool returned the correct result."], "answer": "4"})
    earlier = derived["messages"][1]
    assert earlier["reasoning_content"] == "Earlier explicit reasoning."
    assert "thinking" not in earlier and "reasoning" not in earlier
    assert derived["question"] == derived["messages"][:-1]


def test_conflicting_earlier_reasoning_is_quarantined_instead_of_silently_picked():
    source = sample_record()
    source["messages"][1].update(reasoning_content="One chain.", thinking="A different chain.")
    derived = cot_with_sft_context(source, {"id": "sample", "source_id": "source", "status": "eligible",
        "reasoning": ["The tool returned the correct result."], "answer": "4"})
    assert derived["status"] == "quarantined"
    assert derived["reason"] == "cot_sft_conflicting_reasoning_fields"
    assert list(finalized_sft_rows([source], [derived]))[0]["status"] == "quarantined"


def test_linked_trim_cleans_earlier_aliases_without_copying_original_chain(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps({"messages": [
        {"role": "user", "content": "维护时有什么步骤？"},
        {"role": "assistant", "content": ANSWER, "thinking": "EARLY_CHAIN_SENTINEL 先断电再检查。"},
        {"role": "user", "content": "现在请再总结一次。"},
        {"role": "assistant", "content": ANSWER, "reasoning_content": "FINAL_CHAIN_SENTINEL 先断电再检查。"}
    ]}, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=["sft", "cot"],
                            reasoning_trim={"enabled": True, "template": "leakage"})
    path = engine.run_path(output, rid)
    writer = Generator()
    state = engine.Workflow(output, rid, tmp_path, generator=writer, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["stages"]["trim"]["done"] == 1
    assert sum("只生成 replacement reasoning" in call[0]["content"] for call in writer.calls) == 2
    for target in ("sft", "cot"):
        text = json.dumps(records(path, target), ensure_ascii=False)
        assert "EARLY_CHAIN_SENTINEL" not in text and "FINAL_CHAIN_SENTINEL" not in text
    assert "thinking" not in native(path, "sft")[0]["messages"][1]


def test_ordered_merge_retains_upstream_rejections_and_fails_closed_on_missing_output():
    good = sample_record()
    rejected = {"id": "rejected", "status": "quarantined", "reason": "sft_quality_failed_after_repair"}
    cot_failed = {"id": good["id"], "status": "quarantined", "reason": "cot_reasoning_rejected"}
    assert list(finalized_sft_rows(iter([rejected, good]), iter([cot_failed]))) == [rejected, cot_failed]
    with pytest.raises(ValueError, match="cot_sft_lineage_mismatch"):
        list(finalized_sft_rows([good], []))
    with pytest.raises(ValueError, match="cot_sft_lineage_mismatch"):
        list(finalized_sft_rows([], [cot_failed]))
    with pytest.raises(ValueError, match="cot_sft_lineage_mismatch"):
        list(finalized_sft_rows([good], [{**cot_failed, "id": "wrong"}]))


class ProductionCoTWriter(ProductionWriter):
    def __init__(self):
        super().__init__()
        self.cot_calls = 0

    def chat(self, messages, **kwargs):
        if "已有合格 reference_answer" in messages[0]["content"]:
            data = json.loads(messages[1]["content"])
            self.calls.append(data)
            self.usage["calls"] += 1
            self.cot_calls += 1
            return json.dumps({"reasoning": "核对断电前提，再安排线路检查。",
                               "answer": "FINAL " + data["reference_answer"]})
        return super().chat(messages, **kwargs)


@pytest.mark.parametrize("goals", [{"sft": 3, "cot": 1}, {"sft": 1, "cot": 3}])
def test_partial_production_targets_keep_cot_dependency_without_extra_delivery(tmp_path, goals):
    source = tmp_path / "source.txt"
    source.write_text(TEXT, encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=["sft", "cot"],
        node_generation={"cot": {"style": "structured"}},
        production={"goals": goals, "round_size": 1, "min_acceptance_rate": 0})
    path = engine.run_path(output, rid)
    writer = ProductionCoTWriter()
    state = engine.Workflow(output, rid, tmp_path, generator=writer, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert writer.cot_calls == 3
    assert engine.verify_artifacts(path)["counts"] == goals
    assert all(row["messages"][-1]["content"].startswith("FINAL ") for row in native(path, "sft"))
    assert state["production"]["counts"] == goals


@pytest.mark.parametrize("active", [{"cot"}, {"dpo"}])
def test_legacy_partial_production_does_not_trim_unrequested_sft(tmp_path, active):
    path = tmp_path / "sft.jsonl"
    source = sample_record()
    write_jsonl(path, [source])
    rows = WorkflowRows(path, 1)
    seen = []
    class LegacyRound(WorkflowProduction):
        recipe = {"version": 14, "targets": ["sft", "cot", "dpo"], "sources": [], "qa_director": {}}
        state = {"stages": {"sft": {"eligible": 1}}}
        sft = cot = preference = None
        def stage_items(self, stage, values, action):
            return rows
        def apply_reasoning_trim(self, collections):
            seen.append({key: len(collections[key]) for key in ("sft", "cot", "dpo")})
    result = LegacyRound()._production_round([source], [], active)
    assert seen == [{"sft": 0, "cot": int("cot" in active), "dpo": int("dpo" in active)}]
    assert result["sft"] == []
