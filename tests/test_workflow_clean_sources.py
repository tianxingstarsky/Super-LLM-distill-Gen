"""Document cleaning is a shared, durable input dependency, not a CPT export prerequisite."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from lib.domain.workflow_graph import execution_graph, uses_shared_source_cleaning
from lib.infrastructure import training_workflow as engine


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
