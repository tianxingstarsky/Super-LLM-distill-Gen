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
    ANSWER, Generator, Reviewer, maintenance_reasoning_trace, records, run, model_task_data,
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
        if ("新撰写指定 generation_style 的教学解释" in messages[0]["content"]
                or "新撰写指定 generation_style 的推理文本" in messages[0]["content"]):
            result["answer"] = NEW_ANSWER
            result["reasoning_trace"] = maintenance_reasoning_trace()
        return json.dumps(result, ensure_ascii=False)


def set_version(path, version):
    from lib.domain.workflow_node_prompts import snapshot_node_prompts
    from lib.prompts import get, render
    recipe = engine.read_json(path / "recipe.json")
    recipe["version"] = version
    recipe["node_prompt_templates"] = snapshot_node_prompts(None, recipe_version=version)
    for prompt_id in ("workflow.cot_generate", "workflow.rationale_check"):
        recipe["node_prompt_templates"]["cot"][prompt_id] = render(get(prompt_id, version="1.0.0"))
    for field in ("authored_reasoning_protocol_version", "source_line_protocol_version",
                  "source_memory_protocol_version", "source_context_pack_version",
                  "training_surface_protocol_version", "qa_agent_memory_version"):
        recipe.pop(field, None)
    atomic_json(path / "recipe.json", recipe)
    saved = engine.read_json(path / "state.json")
    saved["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", saved)


def freeze_legacy_document_route(path):
    """Recreate a frozen v20 task where the director was still optional."""
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["version"] == 20 and "native_generation_version" not in recipe
    recipe["qa_director"] = {"enabled": False}
    atomic_json(path / "recipe.json", recipe)
    saved = engine.read_json(path / "state.json")
    saved["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", saved)


def legacy_document_run(tmp_path, **options):
    output, rid, path = run(tmp_path, **options)
    freeze_legacy_document_route(path)
    return output, rid, path


def native(path, target):
    return [json.loads(line) for line in (path / "artifacts" / f"{target}.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("version, targets, linked", [
    (0, ["sft", "cot"], False), (14, ["sft", "cot"], False),
    (15, ["sft", "cot"], True), (15, ["sft"], False), (15, ["cot"], False),
])
def test_route_is_versioned_and_requires_both_outputs(version, targets, linked):
    assert cot_updates_sft({"version": version, "targets": targets}) is linked


@pytest.mark.parametrize("targets,hide_sft", [(["sft", "cot"], False), (["cot"], True), (["sft", "cot"], True)])
def test_native_views_share_trim_review_and_reuse_final_receipts(tmp_path, targets, hide_sft):
    from types import SimpleNamespace
    from lib.contexts.workflows.application.trim_reasoning import TrimReasoning
    from lib.contexts.workflows.infrastructure.generation_adapter import StreamingReasoningAdapter
    from lib.contexts.review.application.review_collections import ReviewGeneratedCollections
    from lib.contexts.review.domain.repair import validate_review_repair
    from lib.contexts.review.domain.candidate_content import candidate_digest as digest
    from lib.contexts.review.infrastructure.automatic_adapter import StreamingReviewCollectionsAdapter
    from lib.contexts.review.infrastructure.automatic_runtime import WorkflowReviewRepair
    recipe = {"version": 42, "targets": targets, "native_generation_version": 1,
              "node_generation": {"sft": {"enabled": True, "answer_mode": "native_joint"}},
              "review_repair": {"mode": "auto"}, "sft_output_style": "drop"}
    assert not cot_updates_sft(recipe)
    source = {"id": "native-one", "source_id": "paper-one", "status": "eligible", "messages": [
        {"role": "user", "content": "Can I compare these results?"},
        {"role": "assistant", "content": "First check the units.", "reasoning_content": "I need comparable quantities."},
        {"role": "user", "content": "Does this prove an improvement?"},
        {"role": "assistant", "content": "It suggests an improvement.", "reasoning_content": "I should check the conditions."}]}
    source_path = tmp_path / "native-source.jsonl"
    write_jsonl(source_path, [source])
    collections = {"sft": WorkflowRows(source_path, 1), "cot": []}
    trim_calls = []

    def trim(rows, *, targets):
        trim_calls.append(targets)
        assert targets == ("sft",)
        rewritten = deepcopy(source)
        rewritten["messages"][-1]["reasoning_content"] = "The comparison still needs controls."
        destination = tmp_path / "trimmed.jsonl"
        write_jsonl(destination, [rewritten])
        rows["sft"] = WorkflowRows(destination, 1)

    delivery = StreamingReasoningAdapter(SimpleNamespace(check_cancel=lambda: None))
    port = SimpleNamespace(apply_reasoning_trim=trim, publish_native_cot=delivery.publish_native_cot)
    TrimReasoning(port, recipe).finalize(collections)
    assert trim_calls == [("sft",)]
    assert list(collections["cot"])[0]["reasoning"] == ["The comparison still needs controls."]
    assert list(collections["cot"])[0]["messages"][1] == source["messages"][1]
    if hide_sft:
        collections["sft"] = []

    class ReviewRuntime:
        _review_payload = WorkflowReviewRepair._review_payload
        _split_review_rows = WorkflowReviewRepair._split_review_rows

        def __init__(self):
            self.path, self.recipe, self.state = tmp_path, recipe, {"id": "native-review"}
            self.calls = []
            (self.path / "stage-results").mkdir()

        def check_cancel(self):
            pass

        def stage_items(self, stage, rows, action):
            assert stage == "review"
            return [output for row in rows for output in action(row)]

        def _review_repair_item(self, item):
            self.calls.append(item["target"])
            # Dropping reasoning from SFT export must not hide it from the
            # canonical review that also certifies the full CoT export.
            assert self._review_payload("sft", item["record"])["messages"][-1]["reasoning_content"]
            record = deepcopy(item["record"])
            record["messages"][-1].update(content="No. A controlled comparison is still needed.",
                reasoning_content="I distinguish a possible comparison from evidence of improvement.")
            record["review_repair"] = {"status": "accepted", "config_sha256": digest(validate_review_repair(recipe["review_repair"])),
                "candidate_sha256": digest(self._review_payload("sft", record))}
            return [{**record, "_review_target": "sft"}]

    runtime = ReviewRuntime()
    review = ReviewGeneratedCollections(StreamingReviewCollectionsAdapter(runtime), recipe)
    reviewed = review.execute(collections)
    cot = list(reviewed["cot"])[0]
    assert runtime.calls == ["sft"]
    assert cot["answer"] == "No. A controlled comparison is still needed."
    assert cot["reasoning"] == [cot["messages"][-1]["reasoning_content"]]
    assert cot["messages"][1] == source["messages"][1]
    assert bool(len(reviewed["sft"])) is (not hide_sft and "sft" in targets)
    if len(reviewed["sft"]):
        assert list(reviewed["sft"])[0]["messages"] == cot["messages"]
    assert cot["review_repair"]["derived_from"] == {"target": "sft", "candidate_id": source["id"]}
    # Production final aggregation reads already-bound receipts for each
    # format. It must not purchase a second review for the same response.
    runtime._production_finalizing = True
    finalized = review.execute({target: list(rows) for target, rows in reviewed.items()})
    assert runtime.calls == ["sft"]
    assert list(finalized["cot"])[0]["answer"] == cot["answer"]
    assert list(WorkflowRows(source_path, 1)) == [source]


def test_new_sft_delivery_uses_cot_final_answer_and_reasoning(tmp_path):
    output, rid, path = legacy_document_run(tmp_path, node_generation={"cot": {"style": "structured"}})
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
    output, rid, path = legacy_document_run(tmp_path, node_generation={"cot": {"style": "structured"}})
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
    output, rid, path = legacy_document_run(tmp_path, sft_output_style="drop",
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
    output, rid, path = legacy_document_run(tmp_path, node_generation={"cot": {"style": "structured"}})
    writer = RewritingGenerator()
    state = engine.Workflow(output, rid, tmp_path, generator=writer, jev=Reviewer(invalid_style=True)).execute()
    assert state["status"] == "failed" and state["error"] == "invalid_style_judge_schema"
    calls = len(writer.calls)
    state = engine.resume(output, rid, tmp_path, generator=writer, jev=Reviewer())
    assert state["status"] == "completed", state.get("error")
    assert len(writer.calls) == calls
    assert native(path, "sft")[0]["messages"][-1]["content"] == NEW_ANSWER


def test_legacy_dual_output_keeps_its_original_independent_answers(tmp_path):
    output, rid, path = legacy_document_run(tmp_path, node_generation={"cot": {"style": "structured"}})
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
    assert list(finalized_sft_rows(iter([rejected, good]), iter([cot_failed]))) == [
        rejected, {**cot_failed, "reasoning_route": "sft_via_cot"}]
    with pytest.raises(ValueError, match="cot_sft_lineage_mismatch"):
        list(finalized_sft_rows([good], []))
    with pytest.raises(ValueError, match="cot_sft_lineage_mismatch"):
        list(finalized_sft_rows([], [cot_failed]))
    with pytest.raises(ValueError, match="cot_sft_lineage_mismatch"):
        list(finalized_sft_rows([good], [{**cot_failed, "id": "wrong"}]))


def test_failed_trim_projection_keeps_cot_dependency_and_is_replayable():
    good = sample_record()
    failed = {"id": good["id"], "source_id": good["source_id"], "status": "quarantined",
              "reason": "reasoning_trim_failed_after_repair"}
    projected = list(finalized_sft_rows([good], [failed]))
    assert projected[0]["reasoning_route"] == "sft_via_cot"
    assert projected[0]["status"] == "quarantined"
    assert "messages" not in projected[0], "Never fall back to the previously accepted SFT answer."
    assert list(finalized_sft_rows(projected, [failed])) == projected


class ProductionCoTWriter(ProductionWriter):
    def __init__(self):
        super().__init__()
        self.cot_calls = 0

    def chat(self, messages, **kwargs):
        if ("新撰写指定 generation_style 的教学解释" in messages[0]["content"]
                or "新撰写指定 generation_style 的推理文本" in messages[0]["content"]):
            data = model_task_data(messages)
            self.calls.append(data)
            self.usage["calls"] += 1
            self.cot_calls += 1
            return json.dumps({"reasoning": "核对断电前提，再安排线路检查。",
                               "answer": "FINAL " + data["reference_answer"],
                               "reasoning_trace": maintenance_reasoning_trace(fault=True)})
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
    freeze_legacy_document_route(path)
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
