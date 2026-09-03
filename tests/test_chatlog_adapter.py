"""M0 验证 3 测试：chatlog→traj 适配器（OpenCUA 同构 schema）离线验证。

不调用任何 LLM：纯格式转换 + 错误步骤标记 + 确定性 task_id。
"""
from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "chatlog_sample.jsonl"


def test_convert_chatlog_to_open_cua_schema(tmp_path):
    from lib.adapters.chatlog_to_traj import convert_jsonl

    out = tmp_path / "traj.jsonl"
    count = convert_jsonl(FIXTURE, out)
    assert count == 2

    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    # schema：task_id / instruction / traj[{image, value.code}]（OpenCUA 同构）
    for rec in records:
        assert isinstance(rec["task_id"], str) and rec["task_id"]
        assert isinstance(rec["instruction"], str) and rec["instruction"]
        for step in rec["traj"]:
            assert "image" in step  # 文本日志为 None；GUI 轨迹为路径
            assert "code" in step["value"]

    chat, tool = records[0], records[1]

    # 文本对话：用户纠正轮次标记为 feedback
    feedback_steps = [s for s in chat["traj"] if s.get("meta", {}).get("feedback")]
    assert len(feedback_steps) == 1
    # 最终正确回答在轨迹末尾，且不含错误尝试（切片版本只出现在反馈轮之前的 assistant 轮）
    assert "assistant_answer" in chat["traj"][-1]["value"]["code"]
    assert "切片反转" not in json.dumps(chat["traj"][-1], ensure_ascii=False)

    # 工具日志：运行时事实（exit_code=2）标记为错误；后续成功步骤标记 ok
    error_steps = [s for s in tool["traj"] if s.get("meta", {}).get("error")]
    ok_steps = [s for s in tool["traj"] if s.get("meta", {}).get("ok")]
    assert len(error_steps) == 1
    assert len(ok_steps) == 2
    assert error_steps[0]["value"]["code"].startswith("run_shell")

    # 确定性：task_id 由首条用户指令派生，重复转换幂等
    out2 = tmp_path / "traj2.jsonl"
    assert convert_jsonl(FIXTURE, out2) == 2
    assert out.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")


def test_dedup_and_empty_turns():
    from lib.adapters.chatlog_to_traj import turns_to_traj

    task = {
        "turns": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": ""},  # 空轮跳过
            {"role": "assistant", "content": "在的"},
            {"role": "assistant", "content": "在的"},  # 相邻重复去重
            {"role": "user", "content": ""},  # 空用户轮跳过
        ]
    }
    rec = turns_to_traj(task)
    # 首条用户轮 → instruction；空轮跳过；相邻重复去重 → traj 仅剩 1 个 assistant 轮
    assert len(rec["traj"]) == 1
    assert rec["instruction"] == "你好"


def test_gui_passthrough_validation():
    from lib.adapters.chatlog_to_traj import validate_gui_traj_line

    good = '{"task_id": "t", "instruction": "open app", "traj": [{"image": "a.png", "value": {"code": "pyautogui.click(x=0.5, y=0.3)"}}]}'
    assert validate_gui_traj_line(good) is None
    assert validate_gui_traj_line("{bad json") is not None
    missing = '{"task_id": "t", "instruction": "x", "traj": [{"value": {}}]}'
    assert validate_gui_traj_line(missing) is not None


def test_distill_prompts_constraints_baked_in():
    from lib.adapters.distill_prompts import GENERATOR_TEXT_PROMPT, REFLECTOR_TEXT_PROMPT

    # 反思防呆约束必须出现在生成提示词里（错误只留一句教训、最终只含正确操作）
    assert "一句话教训" in GENERATOR_TEXT_PROMPT
    assert "≤20%" in GENERATOR_TEXT_PROMPT
    assert "只包含正确操作" in GENERATOR_TEXT_PROMPT
    # 正确性信号优先级在 Reflector 提示词里
    assert "exit_code" in REFLECTOR_TEXT_PROMPT
    assert "用户信号" in REFLECTOR_TEXT_PROMPT
