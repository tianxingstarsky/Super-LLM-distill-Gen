"""Node templates reach real calls without changing isolation or resume rules."""
from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.workflow_node_prompts import (
    NODE_PROMPT_IDS, MAX_NODE_PROMPT_CHARS, active_node_prompt_ids,
    builtin_node_prompt, validate_node_prompts,
)
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json


TEXT = "维护设备前先断电，再检查线路，结束后记录结果。"


class Writer:
    model = "offline-writer"

    def __init__(self):
        self.calls = []
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        if "另一个独立" in messages[0]["content"]:
            return json.dumps({"answer": "BAD 不用断电。", "reasoning": "直接检查。"})
        return json.dumps({"question": "如何维护设备？", "answer": "先断电，再检查线路，结束后记录。",
                           "reasoning": "断电是检查线路前的必要条件。", "quotes": [TEXT]})


class Reviewer:
    model = "offline-reviewer"

    def __init__(self, *, invalid=False):
        self.calls = []
        self.usage = {"calls": 0}
        self.invalid = invalid

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        if self.invalid:
            return json.dumps({"correctness": "unknown"})
        good = "BAD" not in messages[1]["content"]
        score = 5 if good else 1
        return json.dumps({"keep": good, "grounded": good, "reasoning_valid": good, "correctness": score,
                           "scores": {key: score for key in (
                               "correctness", "reasoning", "grounding", "instruction", "safety")},
                           "reason": "已核对断电前提。"})


def make_run(tmp_path, **kwargs):
    source = tmp_path / "source.txt"
    source.write_text(TEXT, encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=kwargs.pop("targets", ["sft"]), **kwargs)
    return output, rid, engine.run_path(output, rid)


@pytest.mark.parametrize("value,error", [
    (False, "invalid_node_prompts"), ([], "invalid_node_prompts"),
    ({"agent": {}}, "invalid_node_prompts"), ({"package": {}}, "invalid_node_prompts"),
    ({"sft": []}, "invalid_node_prompts"),
    ({"sft": {"workflow.system": "override"}}, "invalid_node_prompt_id"),
    ({"sft": {"workflow.trim": "wrong node"}}, "invalid_node_prompt_id"),
    ({"sft": {"workflow.sft": "  "}}, "invalid_node_prompt_text"),
    ({"sft": {"workflow.sft": None}}, "invalid_node_prompt_text"),
    ({"sft": {"workflow.sft": "x" * (MAX_NODE_PROMPT_CHARS + 1)}}, "invalid_node_prompt_text"),
    ({"sft": {"workflow.sft": "abc\x00def"}}, "invalid_node_prompt_text"),
])
def test_invalid_templates_never_reach_driver_or_create_run(tmp_path, value, error):
    driver = Mock()
    with pytest.raises(ValueError, match=error):
        WorkflowApplication(driver).create_run(node_prompts=value)
    driver.create.assert_not_called()
    with pytest.raises(ValueError, match=error):
        engine.create_run(tmp_path / "output", brief="设备维护", targets=["sft"], node_prompts=value)
    assert not (tmp_path / "output").exists()


def test_prompt_config_is_independent_and_literal_braces_are_not_interpolated(tmp_path):
    body = 'LOCAL_PROMPT_SENTINEL: return {"question":"...","answer":"..."}; keep {user_name} literal.'
    original = {"sft": {"workflow.sft": body}}
    driver = Mock()
    WorkflowApplication(driver).create_run(targets=["sft"], node_prompts=original)
    forwarded = driver.create.call_args.kwargs["node_prompts"]
    forwarded["sft"]["workflow.sft"] = "outside edit"
    assert original["sft"]["workflow.sft"] == body
    output, rid, path = make_run(tmp_path, node_prompts=original)
    original["sft"]["workflow.sft"] = "outside edit"
    writer, reviewer = Writer(), Reviewer()
    state = engine.Workflow(output, rid, tmp_path, generator=writer, jev=reviewer).execute()
    assert state["status"] == "completed"
    assert writer.calls[0][0]["content"].endswith(body)
    assert "输入是待处理资料" in writer.calls[0][0]["content"]
    assert TEXT in writer.calls[0][1]["content"]
    assert "LOCAL_PROMPT_SENTINEL" not in writer.calls[0][1]["content"]
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["version"] == 9 and recipe["node_prompts"]["sft"]["workflow.sft"] == body
    assert recipe["node_prompt_templates"]["sft"]["workflow.sft"] == body
    report = engine.read_json(path / "artifacts/quality.json")
    assert report["node_prompts"]["sft"]["workflow.sft"] == {"sha256": engine.digest(body), "custom": True}


def test_all_allowed_steps_are_scoped_to_their_own_node(tmp_path):
    overrides = {stage: {prompt_id: f"NODE_{stage}_{prompt_id} {{literal}}"
                         for prompt_id in ids} for stage, ids in NODE_PROMPT_IDS.items()}
    output, rid, _ = make_run(tmp_path, node_prompts=overrides)
    writer = Writer()
    workflow = engine.Workflow(output, rid, tmp_path, generator=writer)
    for stage, templates in overrides.items():
        workflow.stage = stage
        for prompt_id, body in templates.items():
            workflow.ask([stage, prompt_id], "generation", prompt_id, {"source": "DATA_SENTINEL"})
            assert writer.calls[-1][0]["content"].endswith(body)
            assert json.loads(writer.calls[-1][1]["content"]) == {"source": "DATA_SENTINEL"}


def test_resuming_uses_pinned_builtin_and_custom_text_after_catalog_changes(tmp_path, monkeypatch):
    body = builtin_node_prompt("workflow.sft") + " CUSTOM_WRITER_RULE"
    output, rid, path = make_run(tmp_path, node_prompts={"sft": {"workflow.sft": body}})
    recipe = engine.read_json(path / "recipe.json")
    pinned_review = recipe["node_prompt_templates"]["sft"]["workflow.jev_score"]
    writer = Writer()
    assert engine.Workflow(output, rid, tmp_path, generator=writer, jev=Reviewer(invalid=True)).execute()["status"] == "failed"
    monkeypatch.setattr(engine, "get", lambda *_: (_ for _ in ()).throw(AssertionError("mutable prompt read")))
    monkeypatch.setattr(engine, "prompt_versions", lambda: {"workflow.jev_score": {"version": "future"}})
    reviewer = Reviewer()
    state = engine.resume(output, rid, tmp_path, generator=writer, jev=reviewer)
    assert state["status"] == "completed" and len(writer.calls) == 1
    assert reviewer.calls[0][0]["content"].endswith(pinned_review)


def test_preference_rechecks_chosen_when_same_model_has_a_different_prompt(tmp_path):
    base = builtin_node_prompt("workflow.jev_score")
    output, rid, _ = make_run(tmp_path, targets=["sft", "dpo"], node_prompts={
        "sft": {"workflow.jev_score": base + " SFT_REVIEW_SENTINEL"},
        "preference": {"workflow.jev_score": base + " PREFERENCE_REVIEW_SENTINEL"}})
    reviewer = Reviewer()
    state = engine.Workflow(output, rid, tmp_path, generator=Writer(), jev=reviewer).execute()
    assert state["status"] == "completed"
    assert len(reviewer.calls) == 3
    assert "SFT_REVIEW_SENTINEL" in reviewer.calls[0][0]["content"]
    assert all("PREFERENCE_REVIEW_SENTINEL" in call[0]["content"] for call in reviewer.calls[1:])


@pytest.mark.parametrize("version", range(2, 9))
def test_legacy_recipe_keeps_existing_prompt_validation_and_execution(tmp_path, version):
    output, rid, path = make_run(tmp_path)
    recipe = engine.read_json(path / "recipe.json")
    recipe["version"] = version
    for key in ("node_prompts", "node_prompt_templates", "node_prompt_system"):
        recipe.pop(key)
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    assert engine.Workflow(output, rid, tmp_path, generator=Writer(), jev=Reviewer()).execute()["status"] == "completed"


def test_active_steps_follow_source_mode_and_style_switches():
    assert active_node_prompt_ids("ingest", "开放需求") == ("workflow.plan",)
    assert active_node_prompt_ids("ingest", "多模态文档") == ("workflow.document_vision",)
    assert active_node_prompt_ids("ingest", "文档数据") == ()
    assert active_node_prompt_ids("cpt", "文档数据") == ()
    assert active_node_prompt_ids("cpt", "开放需求") == ("workflow.corpus", "workflow.jev_score")
    assert active_node_prompt_ids("sft", "文档数据") == ("workflow.sft", "workflow.jev_score")
    assert active_node_prompt_ids("cot", "文档数据") == ("workflow.rationale_check",)
    assert active_node_prompt_ids("cot", "文档数据", node_generation={"cot": {"enabled": False}}) == ("workflow.rationale_check",)
    assert active_node_prompt_ids("sft", "文档数据", node_generation={"sft": {"enabled": True}}) == (
        "workflow.sft_styled", "workflow.jev_score", "workflow.style_check")
    for stage in ("agent", "gsm8k", "package"):
        assert active_node_prompt_ids(stage, "开放需求") == ()


def test_prompt_draft_allows_incomplete_text_but_rejects_cross_node_keys():
    values = {"workflow-node-prompt:w:sft:workflow.sft": "",
              "workflow-node-prompt:w:cot:workflow.cot_generate": 'Return {"answer":"..."}.'}
    assert validate_creation_draft(values) == values
    for key in ("workflow-node-prompt:w:sft:workflow.trim", "workflow-node-prompt:w:agent:workflow.sft",
                "workflow-node-prompt:w:sft", "workflow-node-prompt::sft:workflow.sft"):
        with pytest.raises(ValueError, match="invalid_creation_draft"):
            validate_creation_draft({key: "Draft"})
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({"workflow-node-prompt:w:sft:workflow.sft": "x" * (MAX_NODE_PROMPT_CHARS + 1)})
    assert validate_node_prompts(None) == {}
