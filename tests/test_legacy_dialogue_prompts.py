"""Real pre-v9 resumes keep pinned prompt versions, including system text."""
from copy import deepcopy
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from lib.prompts import get, render


IDS = ("workflow.system", "workflow.plan", "workflow.multiturn_user", "workflow.multiturn_assistant",
       "workflow.multiturn_consistency", "workflow.jev_score")


class HistoricalWriter:
    model = "offline-historical-writer"

    def __init__(self, fail_once):
        self.fail_once = fail_once
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        prompt_id = ("workflow.plan" if "count" in data else
                     "workflow.multiturn_user" if "turn" in data else "workflow.multiturn_assistant")
        self.calls.append((prompt_id, deepcopy(messages)))
        self.usage["calls"] += 1
        expected = render(get("workflow.system", version="1.0.0")) + "\n" + render(get(prompt_id, version="1.0.0"))
        assert messages[0]["content"] == expected
        if self.fail_once == prompt_id:
            self.fail_once = None
            raise RuntimeError("offline historical unfinished call")
        if prompt_id == "workflow.plan":
            return json.dumps({"tasks": ["编写设备检查操作卡。"]})
        if prompt_id == "workflow.multiturn_user":
            return json.dumps({"message": "如何检查设备？" if data["turn"] == 1 else "检查完如何记录？"})
        return json.dumps({"answer": "先断电，检查后记录结果。", "quotes": []})


class HistoricalReviewer:
    model = "offline-historical-reviewer"

    def __init__(self):
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        prompt_id = "workflow.multiturn_consistency" if data["context"].get("whole_dialogue") else "workflow.jev_score"
        expected = render(get("workflow.system", version="1.0.0")) + "\n" + render(get(prompt_id, version="1.0.0"))
        assert messages[0]["content"] == expected
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "已核对整段真实对话。"})


def historical_run(tmp_path):
    output = tmp_path / "out"
    run_id = engine.create_run(output, brief="设计安全设备检查教学对话。", targets=["multiturn"], tasks=1, conversation_turns=2)
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    recipe["version"] = 8
    for key in ("node_prompts", "node_prompt_templates", "node_prompt_system"):
        recipe.pop(key)
    recipe["prompts"] = {prompt_id: {"version": "1.0.0", "sha256": engine.digest(get(prompt_id, version="1.0.0").template)}
                         for prompt_id in IDS}
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    return output, run_id, path


@pytest.mark.parametrize("failed_prompt", ["workflow.plan", "workflow.multiturn_user", "workflow.multiturn_assistant"])
def test_unfinished_legacy_plan_and_multiturn_calls_resume_with_original_assets(tmp_path, failed_prompt):
    output, run_id, path = historical_run(tmp_path)
    writer, reviewer = HistoricalWriter(failed_prompt), HistoricalReviewer()
    failed = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=reviewer).execute()
    assert failed["status"] == "failed" and failed["error"] == "RuntimeError"
    resumed = engine.resume(output, run_id, tmp_path, generator=writer, jev=reviewer)
    assert resumed["status"] == "completed", resumed.get("error")
    assert get("workflow.plan").version == "1.1.0"  # the current default differs
    assert get("workflow.multiturn_user").version == "1.1.0"
    assert len([call for call in writer.calls if call[0] == "workflow.plan"]) == (2 if failed_prompt == "workflow.plan" else 1)
    assert engine.read_json(path / "artifacts/multiturn.records.json")[0]["turn_count"] == 2


def test_old_missing_later_pin_uses_v1_fallback_and_mismatched_hash_still_refuses(tmp_path):
    output, run_id, _ = historical_run(tmp_path)
    workflow = engine.Workflow(output, run_id, tmp_path)
    workflow.recipe["prompts"].pop("workflow.multiturn_user")
    assert workflow.prompt_text("workflow.multiturn_user") == render(get("workflow.multiturn_user", version="1.0.0"))
    workflow.recipe["prompts"]["workflow.plan"]["sha256"] = "incorrect"
    with pytest.raises(ValueError, match="prompts_changed_create_new_run"):
        workflow.prompt_text("workflow.plan")


def test_node_snapshot_recipes_do_not_consult_historical_or_latest_assets(tmp_path, monkeypatch):
    output = tmp_path / "out"
    body = "PINNED_PLAN_BODY"
    run_id = engine.create_run(output, brief="教学对话。", targets=["multiturn"],
        node_prompts={"ingest": {"workflow.plan": body}})
    workflow = engine.Workflow(output, run_id, tmp_path)

    def unavailable(*args, **kwargs):
        raise AssertionError("a pinned node snapshot must not resolve the global registry")

    monkeypatch.setattr(engine, "get", unavailable)
    assert workflow.prompt_text("workflow.plan", stage="ingest") == body
