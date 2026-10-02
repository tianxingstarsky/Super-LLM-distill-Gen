"""Pinned generation settings affect exports without rewriting review evidence."""
import json
from pathlib import Path

import pytest

from lib.infrastructure.training_workflow import (
    Workflow, create_run, preferred_training_record, preference_snapshot, read_json, run_path,
)
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver


SOURCE_PREFERENCES = (Path(__file__).resolve().parents[1] / "configs" / "preferences.yaml").read_text(
    encoding="utf-8")
REASONING = "工具已确认断电。"
ANSWER = "可以检查线路。"


class AcceptingJudge:
    model = "preference-test-judge"

    def __init__(self):
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True,
                           "correctness": 5,
                           "scores": {key: 5 for key in (
                               "correctness", "reasoning", "grounding", "instruction", "safety")},
                           "reason": "来源中的操作顺序与回答一致。"})


def settings(tmp_path, *, style="separated"):
    root = tmp_path / "settings"
    folder = root / "configs"
    folder.mkdir(parents=True)
    text = SOURCE_PREFERENCES.replace("style: separated", f"style: {style}")
    if style == "tags":
        text = text.replace('think_tokens: ["", ""]',
                            'think_tokens: ["<think>", "</think>"]')
    (folder / "preferences.yaml").write_text(text, encoding="utf-8")
    return root


def source_file(tmp_path):
    source = tmp_path / "conversation.jsonl"
    source.write_text(json.dumps({"messages": [
        {"role": "user", "content": "检查电源"},
        {"role": "assistant", "content": ANSWER, "reasoning_content": REASONING},
    ]}, ensure_ascii=False), encoding="utf-8")
    return source


def execute(output, run_id, root):
    state = Workflow(output, run_id, root, jev=AcceptingJudge()).execute()
    assert state["status"] == "completed"
    folder = run_path(output, run_id) / "artifacts"
    row = json.loads((folder / "sft.jsonl").read_text(encoding="utf-8"))
    trl = json.loads((folder / "trl_sft.jsonl").read_text(encoding="utf-8"))
    return folder, row, trl


@pytest.mark.parametrize("style,expected", [
    ("separated", ANSWER),
    ("drop", ANSWER),
])
def test_chat_reasoning_style_is_applied_to_native_and_trainer_exports(
    tmp_path, style, expected,
):
    root = settings(tmp_path, style=style)
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source_file(tmp_path)], targets=["sft"], settings_root=root)
    recipe = read_json(run_path(output, run_id) / "recipe.json")
    assert recipe["generation_preferences"]["values"]["cot_style"] == style

    folder, native, trl = execute(output, run_id, root)
    for row in (native, trl):
        assert row["messages"][-1]["content"] == expected
    assert (native["messages"][-1].get("reasoning_content") == REASONING) is (style == "separated")
    assert (trl["messages"][-1].get("thinking") == REASONING) is (style == "separated")
    evidence = read_json(folder / "sft.records.json")[0]
    assert evidence["messages"][-1]["reasoning_content"] == REASONING
    report = read_json(folder / "quality.json")["generation_preferences"]
    assert report["export_reasoning_style"] == style
    assert report["applied_targets"] == ["sft"]
    assert "preferences.*" in report["unapplied_fields"]


@pytest.mark.parametrize("style", ["plain", "tags"])
def test_styles_that_change_scored_answer_text_are_reported_as_unapplied(tmp_path, style):
    root = settings(tmp_path, style=style)
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source_file(tmp_path)], targets=["sft"], settings_root=root)
    folder, native, trl = execute(output, run_id, root)
    assert native["messages"][-1]["content"] == ANSWER
    assert native["messages"][-1]["reasoning_content"] == REASONING
    assert trl["messages"][-1]["thinking"] == REASONING
    report = read_json(folder / "quality.json")["generation_preferences"]
    assert report["configured_reasoning_style"] == style
    assert report["export_reasoning_style"] == "separated"
    assert report["applied_targets"] == []
    assert "cot.style" in report["unapplied_fields"]


def test_run_keeps_creation_time_preferences_after_global_edit(tmp_path):
    root = settings(tmp_path)
    output = tmp_path / "output"
    source = source_file(tmp_path)
    original = create_run(output, sources=[source], targets=["sft"], settings_root=root)
    path = root / "configs" / "preferences.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "style: separated", "style: drop"), encoding="utf-8")
    changed = create_run(output, sources=[source], targets=["sft"], settings_root=root)

    _, first, _ = execute(output, original, root)
    _, second, _ = execute(output, changed, root)
    assert first["messages"][-1]["reasoning_content"] == REASONING
    assert "reasoning_content" not in second["messages"][-1]
    first_recipe = read_json(run_path(output, original) / "recipe.json")
    second_recipe = read_json(run_path(output, changed) / "recipe.json")
    assert first_recipe["generation_preferences"] != second_recipe["generation_preferences"]


def test_application_driver_reads_settings_from_its_project_root(tmp_path):
    root = settings(tmp_path, style="drop")
    output = tmp_path / "output"
    driver = FilesystemWorkflowDriver(root, output)
    run_id = driver.create(sources=[source_file(tmp_path)], targets=["sft"])
    recipe = read_json(run_path(output, run_id) / "recipe.json")
    assert recipe["generation_preferences"]["values"]["cot_style"] == "drop"


def test_invalid_preference_file_rejects_run_before_creating_output(tmp_path):
    root = settings(tmp_path)
    (root / "configs" / "preferences.yaml").write_text("preferences: [invalid]", encoding="utf-8")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="preference_preferences_must_be_mapping"):
        create_run(output, sources=[source_file(tmp_path)], targets=["sft"], settings_root=root)
    assert not output.exists()


def test_agent_and_preference_pairs_never_receive_sft_reasoning_projection(tmp_path):
    preferences = preference_snapshot(settings(tmp_path, style="drop"))
    assistant = {"role": "assistant", "content": ANSWER, "reasoning_content": REASONING}
    agent = {"messages": [{"role": "user", "content": "2+2"}, assistant]}
    pair = {"prompt": [{"role": "user", "content": "2+2"}],
            "chosen": [assistant],
            "rejected": [{"role": "assistant", "content": "其他回答。",
                          "reasoning_content": "另一条推理。"}]}
    assert preferred_training_record("agent", agent, preferences) == agent
    assert preferred_training_record("dpo", pair, preferences) == pair
    assert preferred_training_record("orpo", pair, preferences) == pair
    assert preferred_training_record("agent", agent, preferences)["messages"][-1][
        "reasoning_content"] == REASONING


def test_agent_native_and_negative_sidecar_remain_evidence_bound_with_drop_style(tmp_path):
    root = settings(tmp_path, style="drop")
    source = tmp_path / "traces.jsonl"

    def trace(expression, observed):
        return {"messages": [
            {"role": "user", "content": expression},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "calc", "type": "function",
                "function": {"name": "calculator", "arguments": json.dumps({"expression": expression})}}]},
            {"role": "tool", "tool_call_id": "calc", "content": observed},
            {"role": "assistant", "content": observed, "reasoning_content": REASONING},
        ]}

    source.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in (
        trace("2+2", "4"), trace("3+3", "7"))) + "\n", encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source], targets=["agent"], settings_root=root)
    assert Workflow(output, run_id, root).execute()["status"] == "needs_attention"
    folder = run_path(output, run_id) / "artifacts"
    records = read_json(folder / "agent.records.json")
    native = json.loads((folder / "agent.jsonl").read_text(encoding="utf-8"))
    negative = json.loads((folder / "agent.negative.jsonl").read_text(encoding="utf-8"))
    assert native["messages"] == records[0]["messages"]
    assert native["messages"][-1]["reasoning_content"] == REASONING
    assert negative == records[1]["negative"]
    report = read_json(folder / "quality.json")["generation_preferences"]
    assert report["applied_targets"] == []
    assert "cot.style" in report["unapplied_fields"]
