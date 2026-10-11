"""Document cleaning is a shared, durable input dependency, not a CPT export prerequisite."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from lib.domain.workflow_graph import execution_graph, uses_shared_source_cleaning
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.structured_source_kind import contains_document_records
from lib.infrastructure.workflow_qa_director import DirectorBatchTooLarge


ORIGINAL = "设备手册\n页码 12\n额定温度为 75 ℃，停机后检查阀门。公式：$E = mc^2$。"
CLEAN = "额定温度为 75 ℃，停机后检查阀门。公式：$E = mc^2$。"


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All model calls below are in-memory doubles; no shared TCP port is used."""
    yield


def verdict(keep=True):
    score = 5 if keep else 1
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": score,
            "scores": {key: score for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "Source facts, numbers, units and formulas agree."}


class Generator:
    model = "offline-clean-source-generator"

    def __init__(self, *, uncertain=False):
        self.uncertain = uncertain
        self.calls, self.clean_calls, self.sft_sources = [], [], []
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        self.usage["calls"] += 1
        self.calls.append(deepcopy(data))
        if "source_text" in data:
            self.clean_calls.append(data)
            return json.dumps({"text": CLEAN, "uncertain": self.uncertain,
                               "removed_noise": ["页码 12"], "reason": "Remove the page number only."})
        source = data["source"]
        self.sft_sources.append(deepcopy(source))
        return json.dumps({"question": f"停机后如何检查？情境 {source['id'][:16]}",
                           "answer": "额定温度为 75 ℃，停机后检查阀门。",
                           "reasoning": "按来源规定，在停机之后检查阀门。", "quotes": [source["text"]]})


class Reviewer:
    model = "offline-clean-source-reviewer"

    def __init__(self):
        self.calls = []
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        result = verdict()
        if "original_source_text" in data:
            result["preservation"] = {key: True for key in (
                "source_faithful", "numbers_and_units_preserved", "formulas_and_code_preserved", "coverage_sufficient")}
        return json.dumps(result)


class DirectedGenerator(Generator):
    """Trace the actual clean → director → SFT calls without a model service."""

    def __init__(self, *, fail_plan=False, **options):
        super().__init__(**options)
        self.director_inputs, self.fail_plan = [], fail_plan

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        if "candidates" in data:
            self.usage["calls"] += 1
            self.calls.append(deepcopy(data))
            self.director_inputs.append(deepcopy(data))
            if self.fail_plan:
                raise RuntimeError("offline interrupted director")
            return json.dumps({"tasks": [{"id": unit["id"], "qa_type": "closed_book",
                "question": f"停机后如何检查？情境 {unit['id'][:16]}",
                "visible_context": "", "answer_policy": "answer",
                "guidance": "回答停机后的检查操作。", "evidence_quotes": [CLEAN]}
                for index, unit in enumerate(data["candidates"])]})
        if "qa_contract" in data and "source_context" in data:
            self.usage["calls"] += 1
            self.calls.append(deepcopy(data))
            self.sft_sources.append(deepcopy(data["source_context"]))
            return json.dumps({"question": data["qa_contract"]["question"],
                "answer": "额定温度为 75 ℃，停机后检查阀门。", "reasoning": "检查需要在停机之后进行。",
                "quotes": [CLEAN]})
        return super().chat(messages, **kwargs)


class DirectedReviewer(Reviewer):
    def chat(self, messages, **kwargs):
        if '"adherence"' in messages[0]["content"]:
            self.calls.append(json.loads(messages[1]["content"]))
            self.usage["calls"] += 1
            return json.dumps({"keep": True, "adherence": 5, "reason": "The visible question is self-contained."})
        return super().chat(messages, **kwargs)


def directed_config():
    return {"enabled": True, "batch_size": 1, "history_limit": 0,
            "type_weights": {"closed_book": 100, "grounded": 0, "partial": 0,
                             "multi_source": 0, "distractor": 0}}


def make_run(tmp_path, *, targets=("sft",), **options):
    source = tmp_path / "manual.txt"
    source.write_text(ORIGINAL, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=targets,
                               cpt_processing={"mode": "model", "review_mode": "text"}, **options)
    return output, run_id, engine.run_path(output, run_id)


@pytest.mark.parametrize("targets", [("sft",), ("cot",), ("dpo",), ("orpo",), ("rlaif",), ("multiturn",)])
def test_document_qa_routes_include_shared_cleaning_without_cpt_export(targets):
    assert uses_shared_source_cleaning(targets, ["manual.pdf"])
    nodes, edges = execution_graph(targets, recipe_version=19, source_cleaning=True)
    assert ("ingest", "cpt") in edges
    destination = "multiturn" if targets == ("multiturn",) else "sft"
    assert ("cpt", destination) in edges and ("ingest", destination) not in edges
    assert ("cpt", "package") not in edges and nodes.index("cpt") < nodes.index(destination)


def test_director_reads_shared_cleaning_and_legacy_graphs_stay_unchanged():
    nodes, edges = execution_graph(["sft", "cpt"], qa_director=True, source_cleaning=True,
                                   recipe_version=19, review_repair={"mode": "auto"})
    assert nodes[:3] == ("ingest", "cpt", "director")
    assert ("cpt", "director") in edges and ("director", "sft") in edges
    assert ("cpt", "review") in edges
    for version in (11, 15, 18):
        assert execution_graph(["sft", "cpt"], recipe_version=version, source_cleaning=True) == (
            execution_graph(["sft", "cpt"], recipe_version=version))
    _, old_edges = execution_graph(["sft", "cpt"], recipe_version=18)
    assert ("ingest", "sft") in old_edges and ("cpt", "sft") not in old_edges


@pytest.mark.parametrize("targets,sources,mode", [
    (["sft"], ["conversation.jsonl"], None), (["agent"], ["manual.txt"], None),
    (["sft"], [], "开放需求"), (["sft"], [{"kind": "human_design", "file": "human-seeds.json"}], None),
])
def test_non_document_routes_do_not_invent_cleaning(targets, sources, mode):
    assert not uses_shared_source_cleaning(targets, sources, source_mode=mode)
    _, edges = execution_graph(targets, recipe_version=19, source_cleaning=False)
    assert ("cpt", "sft") not in edges


def test_sft_only_uses_qualified_cleaned_text_and_pins_custom_cleaning_prompt(tmp_path):
    output, run_id, path = make_run(tmp_path, node_prompts={"cpt": {"workflow.cpt_clean": "Use the user's cleaning rules."}})
    generator, reviewer = Generator(), Reviewer()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    assert len(generator.clean_calls) == len(generator.sft_sources) == 1
    assert generator.sft_sources[0]["text"] == CLEAN
    assert generator.sft_sources[0]["source_cleaning"]["status"] == "accepted"
    assert "页码 12" not in generator.sft_sources[0]["text"]
    assert state["input_summary"]["ready_after_cleaning"] == 1
    assert state["stages"]["cpt"]["status"] == "completed"
    assert not (path / "artifacts" / "cpt.jsonl").exists()
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["version"] == 19 and recipe["source_cleaning"] is True
    assert recipe["node_prompt_templates"]["cpt"]["workflow.cpt_clean"] == "Use the user's cleaning rules."
    assert engine.verify_artifacts(path)["counts"] == {"sft": 1}


def test_direct_api_default_cleaning_is_native_and_does_not_add_model_calls(tmp_path):
    source = tmp_path / "manual.md"
    source.write_text(ORIGINAL, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"])
    generator, reviewer = Generator(), Reviewer()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.clean_calls == [] and len(generator.sft_sources) == 1
    assert len(reviewer.calls) == 1 and "original_source_text" not in reviewer.calls[0]
    assert state["input_summary"]["source_cleaning"] == {"candidates": 1, "accepted": 1, "quarantined": 0}


def test_resume_reuses_successful_cleaning_after_generation_failure(tmp_path):
    output, run_id, _ = make_run(tmp_path)
    generator, reviewer = Generator(), Reviewer()
    class FailFirstGeneration(Generator):
        def chat(self, messages, **kwargs):
            if "source" in json.loads(messages[1]["content"]):
                raise RuntimeError("offline generation interruption")
            return super().chat(messages, **kwargs)
    interrupted = FailFirstGeneration()
    assert engine.Workflow(output, run_id, tmp_path, generator=interrupted, jev=reviewer).execute()["status"] == "failed"
    assert len(interrupted.clean_calls) == 1
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert generator.clean_calls == [] and generator.sft_sources[0]["text"] == CLEAN
    assert sum("original_source_text" in call for call in reviewer.calls) == 1


def test_cpt_and_sft_reuse_one_cleaning_result(tmp_path):
    output, run_id, path = make_run(tmp_path, targets=("cpt", "sft"))
    generator, reviewer = Generator(), Reviewer()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    assert len(generator.clean_calls) == 1
    assert json.loads((path / "artifacts" / "cpt.jsonl").read_text(encoding="utf-8"))["text"] == CLEAN
    assert generator.sft_sources[0]["text"] == CLEAN
    assert sum("original_source_text" in call for call in reviewer.calls) == 1


def test_uncertain_document_never_reaches_sft_or_sample_expansion(tmp_path):
    output, run_id, path = make_run(tmp_path, sample_count=20)
    generator, reviewer = Generator(uncertain=True), Reviewer()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert generator.sft_sources == [] and reviewer.calls == []
    assert state["input_summary"]["generation_candidates"] == 0
    assert state["input_summary"]["source_cleaning"]["quarantined"] == 1
    assert (path / "artifacts" / "sft.jsonl").read_text(encoding="utf-8") == ""
    rows = list(engine.WorkflowRows(path / "stage-results" / "cpt.jsonl", 1))
    assert rows[0]["reason"] == "cpt_clean_uncertain"


def test_production_cleans_once_before_reusing_sources_across_rounds(tmp_path):
    output, run_id, path = make_run(tmp_path, production={"goals": {"sft": 2}, "round_size": 1,
                                                        "min_acceptance_rate": 0})
    generator, reviewer = Generator(), Reviewer()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    assert len(generator.clean_calls) == 1 and len(generator.sft_sources) == 2
    assert all(source["text"] == CLEAN for source in generator.sft_sources)
    assert state["production"]["round"] == 2
    assert engine.verify_artifacts(path)["counts"] == {"sft": 2}


def test_production_retains_failed_cpt_sources_without_using_them_as_sft_inputs(tmp_path):
    output, run_id, path = make_run(tmp_path, targets=("sft", "cpt"),
        production={"goals": {"sft": 2}, "round_size": 1, "min_acceptance_rate": 0})
    generator = Generator(uncertain=True)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert generator.sft_sources == []
    records = engine.read_json(path / "artifacts" / "cpt.records.json")
    assert len(records) == 1 and records[0]["reason"] == "cpt_clean_uncertain"


def test_historical_recipe_resumes_on_the_original_input_path(tmp_path):
    output, run_id, path = make_run(tmp_path)
    recipe = engine.read_json(path / "recipe.json")
    recipe["version"] = 18
    recipe.pop("source_cleaning")
    engine.atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    engine.atomic_json(path / "state.json", state)
    generator = Generator()
    result = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute(resume_run=True)
    assert result["status"] == "completed", result.get("error")
    assert generator.clean_calls == [] and generator.sft_sources[0]["text"] == ORIGINAL


def test_shared_cleaning_passes_recorded_conversations_without_rewriting_them(tmp_path):
    output, run_id, _ = make_run(tmp_path)
    workflow = engine.Workflow(output, run_id, tmp_path)
    original = {"id": "recorded", "source_id": "session", "kind": "conversation", "status": "ready",
                "messages": [{"role": "user", "content": "Help me."}, {"role": "assistant", "content": "Certainly."}]}
    assert workflow.clean_source_unit(original) == [original]


@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
@pytest.mark.parametrize("production", [None, {"goals": {"sft": 2}, "round_size": 1, "min_acceptance_rate": 0}])
def test_structured_document_director_reads_qualified_cleaning_only(tmp_path, suffix, production):
    source = tmp_path / ("manual" + suffix)
    record = {"text": ORIGINAL}
    source.write_text(json.dumps([record] if suffix == ".json" else record), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], qa_director=directed_config(),
        cpt_processing={"mode": "model", "review_mode": "text"}, production=production)
    path = engine.run_path(output, run_id)
    generator = DirectedGenerator()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=DirectedReviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["version"] == 19 and recipe["source_cleaning"] is True
    assert recipe["sources"][0]["kind"] == "document_collection"
    assert len(generator.clean_calls) == 1
    assert len(generator.director_inputs) == (2 if production else 1)
    for plan in generator.director_inputs:
        candidate = plan["candidates"][0]
        assert candidate["teacher_evidence"] == CLEAN
        assert candidate["evidence_origin"] == "qualified_cleaned_source"
        assert candidate["source_cleaning"]["status"] == "accepted"
        assert candidate["source_cleaning"]["cleaned_text_sha256"] == engine.digest(CLEAN)
        assert "页码 12" not in json.dumps(candidate, ensure_ascii=False)
    assert all(row["text"] == CLEAN for row in generator.sft_sources)
    assert state["qa_director"]["evidence_source"] == "qualified_cleaning"
    assert not (path / "artifacts/cpt.jsonl").exists()
    assert engine.verify_artifacts(path)["counts"] == {"sft": 2 if production else 1}


@pytest.mark.parametrize("record", [
    {"text": ORIGINAL, "messages": [{"role": "user", "content": "检查如何进行？"},
                                    {"role": "assistant", "content": "先断电。"}]},
    {"text": ORIGINAL, "conversations": [{"from": "human", "value": "检查如何进行？"},
                                         {"from": "gpt", "value": "先断电。"}]},
    [{"role": "user", "content": "检查如何进行？"}, {"role": "assistant", "content": "先断电。"}],
])
def test_structured_conversations_do_not_invent_document_cleaning(tmp_path, record):
    source = tmp_path / "conversation.json"
    source.write_text(json.dumps(record), encoding="utf-8")
    assert not contains_document_records(source)
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], qa_director=directed_config())
    recipe = engine.read_json(engine.run_path(output, run_id) / "recipe.json")
    assert recipe.get("source_cleaning") is not True
    assert recipe["sources"][0].get("kind") != "document_collection"


def test_failed_cleaning_never_reaches_director(tmp_path):
    output, run_id, _ = make_run(tmp_path, qa_director=directed_config(), sample_count=10)
    generator = DirectedGenerator(uncertain=True)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=DirectedReviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert generator.director_inputs == generator.sft_sources == []
    assert state["stages"]["director"]["total"] == 0


def test_resume_after_director_failure_reuses_cleaning(tmp_path):
    output, run_id, _ = make_run(tmp_path, qa_director=directed_config())
    interrupted = DirectedGenerator(fail_plan=True)
    state = engine.Workflow(output, run_id, tmp_path, generator=interrupted, jev=DirectedReviewer()).execute()
    assert state["status"] == "failed" and len(interrupted.clean_calls) == 1
    generator = DirectedGenerator()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=DirectedReviewer()).execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert generator.clean_calls == []
    assert generator.director_inputs[0]["candidates"][0]["teacher_evidence"] == CLEAN


@pytest.mark.parametrize("cleaning,status,text", [
    ({}, "ready", CLEAN),
    ({"status": "rejected", "cleaned_text_sha256": engine.digest(CLEAN)}, "ready", CLEAN),
    ({"status": "accepted", "cleaned_text_sha256": engine.digest(CLEAN)}, "quarantined", CLEAN),
    ({"status": "accepted", "cleaned_text_sha256": engine.digest(CLEAN)}, "ready", ORIGINAL),
])
def test_director_rejects_raw_rejected_or_changed_shared_evidence(tmp_path, cleaning, status, text):
    output, run_id, _ = make_run(tmp_path, qa_director=directed_config())
    workflow = engine.Workflow(output, run_id, tmp_path)
    unit = {"kind": "document", "text": text, "status": status, "source_cleaning": cleaning}
    with pytest.raises(ValueError, match="qa_director_clean_source_required"):
        workflow._require_clean_director_evidence(unit)
    # Persisted legacy tasks retain their exact dependency contract.
    workflow.recipe["version"] = 18
    workflow._require_clean_director_evidence(unit)


def test_structured_detection_skips_invalid_records_and_finds_later_document(tmp_path):
    source = tmp_path / "mixed.jsonl"
    source.write_text("invalid\n" + json.dumps({"messages": [{"role": "user", "content": "Hi"}]}) +
                      "\n" + json.dumps({"text": ORIGINAL}) + "\n", encoding="utf-8-sig")
    assert contains_document_records(source)
    assert uses_shared_source_cleaning(["sft"], [{"file": source.name, "kind": "document_collection"}])


def test_mixed_structured_sources_clean_documents_and_preserve_real_conversation(tmp_path):
    conversation = [{"role": "user", "content": "检查前应该做什么？"},
                    {"role": "assistant", "content": "先断电。", "reasoning_content": "断电满足检查的安全前提。"}]
    source = tmp_path / "mixed.jsonl"
    source.write_text(json.dumps({"text": ORIGINAL}) + "\n" + json.dumps({
        "text": "This is a conversation label, not document evidence.", "messages": conversation}), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], qa_director=directed_config(),
        cpt_processing={"mode": "model", "review_mode": "text"})
    generator = DirectedGenerator()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=DirectedReviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert len(generator.clean_calls) == len(generator.director_inputs) == 1
    assert generator.director_inputs[0]["candidates"][0]["teacher_evidence"] == CLEAN
    rows = engine.read_json(engine.run_path(output, run_id) / "artifacts/sft.records.json")
    assert any(row["messages"] == conversation for row in rows)
    assert state["qa_director"]["recorded_conversations_preserved"] == 1


def test_director_context_batch_splits_preserve_offsets_results_and_progress(tmp_path, monkeypatch):
    output, run_id, path = make_run(tmp_path, qa_director={**directed_config(), "batch_size": 4}, sample_count=4)
    original = engine.Workflow._director_batch_plan

    def limited(self, batch, offset, *args):
        if len(batch) > 1:
            raise DirectorBatchTooLarge("qa_director_batch_context_too_large")
        return original(self, batch, offset, *args)

    monkeypatch.setattr(engine.Workflow, "_director_batch_plan", limited)
    generator = DirectedGenerator()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=DirectedReviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert [plan["offset"] for plan in generator.director_inputs] == [0, 1, 2, 3]
    assert state["stages"]["director"]["batches_total"] == state["stages"]["director"]["batches_done"] == 4
    assert state["stages"]["sft"]["batches_total"] == state["stages"]["sft"]["batches_done"] == 4
    rows = list(engine.WorkflowRows(path / "stage-results/director.jsonl", 4))
    assert len({row["id"] for row in rows}) == 4
    assert all(row["source_cleaning"]["status"] == "accepted" for row in rows)
    assert engine.verify_artifacts(path)["counts"] == {"sft": 4}


def test_director_can_plan_from_clean_neighbor_material_and_sft_receives_same_pack(tmp_path):
    preparation = ("巡检准备阶段先确认设备已停机，核查维护记录和工单，佩戴防护用具。"
                   "随后检查现场照明、通道与警示标记，确认作业负责人和设备编号一致。"
                   "将检查结果填入工单；发现编号不一致时先核对身份，不进入后续操作。"
                   "准备阶段的人员负责记录时间和环境条件，不负责完成复位。")
    completion = ("巡检完成阶段先核对阀门与连接件的状态，确认无遗留工具后签署检查记录。"
                  "最后复位时先关闭隔离开关，再由作业负责人确认启动许可。"
                  "若许可尚未确认，保持设备停止并联系负责人，不得自行启动。"
                  "完成复位后观察设备运行状态，记录温度和异常声音，发现异常应停机报告。")
    quote = "最后复位时先关闭隔离开关，再由作业负责人确认启动许可。"
    source = tmp_path / "manual.txt"
    source.write_text(preparation + "\n\n" + completion, encoding="utf-8")
    output = tmp_path / "output"
    options = directed_config()
    options["type_weights"] = {key: int(key == "grounded") for key in options["type_weights"]}
    run_id = engine.create_run(output, sources=[source], targets=["sft"], qa_director=options,
                               chunk_chars=200, sample_count=1)
    class NeighborWriter:
        model = "offline-neighbor-source-director"
        usage = {}
        def __init__(self):
            self.calls = []
        def chat(self, messages, **kwargs):
            data = json.loads(messages[1]["content"])
            self.calls.append(deepcopy(data))
            if "candidates" in data:
                return json.dumps({"tasks": [{"id": candidate["id"], "qa_type": "grounded",
                    "question": "巡检结束后，复位和启动许可如何处理？", "visible_context": quote,
                    "answer_policy": "answer", "guidance": "依据完成阶段说明复位顺序。",
                    "evidence_quotes": [quote]} for candidate in data["candidates"]]})
            if "qa_contract" in data:
                return json.dumps({"question": data["qa_contract"]["question"],
                    "answer": "先关闭隔离开关，再请作业负责人确认启动许可。",
                    "reasoning": "复位操作与启动许可必须按照既定顺序完成。", "quotes": [quote]})
            raise AssertionError("unexpected request")
    generator = NeighborWriter()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=DirectedReviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    plan, sft = generator.calls
    assert quote not in plan["candidates"][0]["teacher_evidence"]
    assert plan["teacher_context_pack"] == sft["teacher_context_pack"]
    materials = plan["teacher_context_pack"][0]["materials"]
    assert len(materials) >= 2 and all(material["cleaning_status"] == "accepted" for material in materials)
    assert any(quote in material["text"] for material in materials)
    row = engine.read_json(engine.run_path(output, run_id) / "artifacts/sft.records.json")[0]
    assert row["status"] == "eligible" and quote in row["messages"][0]["content"]
