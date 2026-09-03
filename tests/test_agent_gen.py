"""Agent 零参考管线离线测试（fake client 脚本化完整执行循环）。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _tools():
    from lib.agent_gen import load_tools

    return load_tools(ROOT / "configs" / "agent_tools.yaml")


class FakeClient:
    """任务生成→1 个 goal；act 第 1 步调工具、第 2 步给 final；simulate 给观察。"""

    def __init__(self):
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        content = messages[0]["content"]
        if "agent 任务生成专家" in content:
            return json.dumps({"tasks": [{"goal": "查找今年诺贝尔物理学奖得主"}]}, ensure_ascii=False)
        if "工具执行器模拟器" in content:
            return json.dumps({"observation": "搜索结果：2026 年诺贝尔物理学奖授予 A 与 B，因其在量子模拟领域的贡献。"}, ensure_ascii=False)
        if "agent 轨迹质检员" in content:
            return json.dumps({"valid": True, "issues": [], "keep": True}, ensure_ascii=False)
        if "agent 执行者" in content:
            if "[观察]" in content:  # 历史中出现过工具观察 → 任务已完成
                return json.dumps({"final_answer": "2026 年诺贝尔物理学奖授予 A 与 B。"}, ensure_ascii=False)
            return json.dumps({"thought": "先搜索", "tool_call": {"name": "web_search", "args": {"query": "2026 诺贝尔物理学奖"}}}, ensure_ascii=False)
        raise AssertionError("未知提示词")


def test_run_produces_valid_trajectory():
    from lib.agent_gen import run

    result = run(FakeClient(), _tools()["tools"], ["web"], n_per_scenario=1)
    assert result["stats"]["generated"] == 1
    assert result["stats"]["kept"] == 1
    sample = result["samples"][0]
    assert sample["source"] == "agent" and sample["scenario"] == "web"
    # 轨迹结构：user → assistant(toolCalls) → tool(observation) → assistant(final)
    roles = [m["role"] for m in sample["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert sample["messages"][1]["toolCalls"][0]["name"] == "web_search"
    assert "A 与 B" in sample["messages"][2]["content"]  # 观察被引用进最终回答的依据
    assert "A 与 B" in sample["messages"][3]["content"]


def test_run_rejects_unknown_tool_call():
    from lib.agent_gen import run, tools_desc

    class BadToolClient(FakeClient):
        def chat(self, messages, **kwargs):
            content = messages[0]["content"]
            if "agent 执行者" in content:
                return json.dumps({"thought": "x", "tool_call": {"name": "nonexistent_tool", "args": {}}}, ensure_ascii=False)
            return super().chat(messages, **kwargs)

    result = run(BadToolClient(), _tools()["tools"], ["web"], n_per_scenario=1)
    assert result["stats"]["kept"] == 0
    assert result["stats"]["rejected"] == 1


def test_tools_desc_contains_all_tools():
    from lib.agent_gen import tools_desc

    desc = tools_desc(_tools()["tools"])
    for name in ("web_search", "fetch_page", "read_file", "edit_file", "run_code"):
        assert name in desc
