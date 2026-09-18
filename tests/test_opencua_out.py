"""GUI 管线离线测试：OpenCUA 合并产物 → 统一样本适配器（合成 fixture）。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _merged_record(completed: bool = True) -> dict:
    return {
        "task_id": "gui-test-001",
        "instruction": "切换到深色模式并保存",
        "natural_language_task": "在设置窗口中切换深色模式",
        "task_completed": completed,
        "alignment_score": 9,
        "efficiency_score": 8,
        "task_difficulty": 3,
        "traj": [
            {"index": 0, "image": "shot_0.png", "value": {
                "code": "pyautogui.click(x=0.85, y=0.12)",
                "observation": "白色设置窗口，右上角有深色模式开关（关闭状态）。",
                "thought": "第一步应打开深色模式开关。",
                "reflection": "开关变为蓝色，窗口背景变深，切换成功。",
                "instruction": "点击深色模式开关",
            }},
            {"index": 1, "image": "shot_1.png", "value": {
                "code": "pyautogui.click(x=0.85, y=0.30)",
                "observation": "深色窗口，保存按钮为灰色。",
                "thought": "最后点击保存。",
                "reflection": "按钮变绿显示已保存。",
                "instruction": "点击保存按钮",
            }},
        ],
    }


def test_merged_to_sample_structure():
    from lib.adapters.opencua_out import merged_to_sample

    sample = merged_to_sample(_merged_record())
    assert sample["source"] == "gui" and sample["type"] == "sft"
    roles = [m["role"] for m in sample["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant"]
    # 思考=observation+thought+reflection 拼接；动作=computer_action 工具调用
    first = sample["messages"][1]
    assert "第一步应打开深色模式开关" in first["reasoning_content"]
    assert first["toolCalls"][0]["name"] == "computer_action"
    assert "pyautogui.click" in first["toolCalls"][0]["input"]["code"]
    # 下一步观察是本步结果
    assert sample["messages"][2]["content"] == "深色窗口，保存按钮为灰色。"
    assert sample["messages"][2]["toolCallId"] == "gui_0"
    assert sample["quality"]["task_completed"] is True


def test_merged_to_samples_quality_gate(tmp_path):
    from lib.adapters.opencua_out import merged_to_samples

    path = tmp_path / "merged.jsonl"
    path.write_text(
        json.dumps(_merged_record(True), ensure_ascii=False) + "\n"
        + json.dumps(_merged_record(False), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result = merged_to_samples(str(path))
    assert result["stats"] == {"tasks": 2, "kept": 1, "rejected_unfinished": 1, "rejected_empty_traj": 0}
    assert result["samples"][0]["id"] == "gui-test-001"


def test_quality_gate_rejects_non_bool_and_empty_traj(tmp_path):
    """task_completed 为字符串 "false"/"0" 时不得通过；空轨迹直接拒绝并计数。"""
    from lib.adapters.opencua_out import merged_to_samples

    string_false = _merged_record(completed="false")  # type: ignore[arg-type]
    empty_traj = _merged_record(True)
    empty_traj["traj"] = []

    path = tmp_path / "merged.jsonl"
    path.write_text(
        json.dumps(string_false, ensure_ascii=False) + "\n"
        + json.dumps(empty_traj, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result = merged_to_samples(str(path))
    assert result["samples"] == []
    assert result["stats"]["kept"] == 0
    assert result["stats"]["rejected_unfinished"] == 1  # "false" 字符串不再冒充 True
    assert result["stats"]["rejected_empty_traj"] == 1
