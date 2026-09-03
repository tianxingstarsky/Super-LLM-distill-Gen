"""Agent 工具使用零参考数据管线：任务生成 → 执行循环（act/simulate）→ 轨迹质检。

Instructor + Simulator 双角色：Simulator 扮演环境（搜索返回/代码输出/报错），
无需真实环境即可合成自洽的多步 agent 轨迹；轨迹格式与 rollout 蒸馏同构
（assistant toolCalls + tool 观察），可直接并入现有 SFT 数据流。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Dict, List

import yaml

from lib.llm_client import chat_json
from lib.prompts import get, render

MAX_STEPS = 8


def _last_call_repeated(messages: List[Dict[str, Any]]) -> bool:
    """最近两次工具调用完全相同（冗余循环保护）。"""
    calls = [
        (tc["name"], json.dumps(tc.get("args", tc.get("input", {})), ensure_ascii=False, sort_keys=True))
        for m in messages if m.get("role") == "assistant"
        for tc in m.get("toolCalls", [])
    ]
    return len(calls) >= 2 and calls[-1] == calls[-2]


def load_tools(path: str | pathlib.Path) -> Dict[str, Any]:
    return yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))


def tools_desc(tools: Dict[str, Any]) -> str:
    lines = []
    for name, spec in tools.items():
        params = ", ".join(f"{k}: {v}" for k, v in spec.get("parameters", {}).items())
        lines.append(f"- {name}({params}): {spec.get('description', '')}")
    return "\n".join(lines)


def _hist_text(history: List[Dict[str, Any]]) -> str:
    lines = []
    for m in history:
        if m["role"] == "assistant":
            lines.append(f"[思考] {m.get('content', '')}")
            for tc in m.get("toolCalls", []):
                tool_args = tc.get("args", tc.get("input", {}))  # 兼容 args/input 两种字段名
                lines.append(f"[调用] {tc['name']}({json.dumps(tool_args, ensure_ascii=False)})")
        else:
            lines.append(f"[观察] {m.get('content', '')[:300]}")
    return "\n".join(lines) or "（无）"


def gen_tasks(client: Any, tools: Dict[str, Any], scenario: str, n: int, seen: List[str]) -> List[str]:
    out = chat_json(client, [{"role": "user", "content": render(
        get("agent.task_gen"), tools_desc=tools_desc(tools), scenario=scenario, n=n,
        seen="\n".join(f"- {g}" for g in seen) or "（无）")}], temperature=1.0)
    return [str(t.get("goal", "")).strip() for t in out.get("tasks", []) if t.get("goal")]


def run_trajectory(client: Any, tools: Dict[str, Any], goal: str, task_idx: int) -> Dict[str, Any]:
    """单任务执行循环：act → simulate → … → final，返回 messages 与是否完成。"""
    messages: List[Dict[str, Any]] = [{"role": "user", "content": goal}]
    for step in range(MAX_STEPS):
        force_final = (step == MAX_STEPS - 1) or _last_call_repeated(messages)
        history = _hist_text(messages[1:])
        if force_final:
            history += "\n（系统提示：执行步数已达上限或检测到重复调用，请基于已有观察直接输出 final_answer，不得再调用工具）"
        action = chat_json(client, [{"role": "user", "content": render(
            get("agent.act"), tools_desc=tools_desc(tools), goal=goal,
            history=history)}], temperature=0.7)
        if action.get("final_answer"):
            messages.append({"role": "assistant", "content": str(action["final_answer"])})
            return {"messages": messages, "completed": True}
        tc = action.get("tool_call") or {}
        name = str(tc.get("name", ""))
        if name not in tools:
            return {"messages": messages, "completed": False}
        call_id = f"call_{task_idx}_{step}"
        messages.append({
            "role": "assistant",
            "content": str(action.get("thought", "")),
            "toolCalls": [{"id": call_id, "name": name, "input": tc.get("args", {})}],
        })
        obs = chat_json(client, [{"role": "user", "content": render(
            get("agent.simulate"), tools_desc=tools_desc(tools), goal=goal,
            tool_name=name, tool_args=json.dumps(tc.get("args", {}), ensure_ascii=False),
            history=_hist_text(messages[1:-1]))}], temperature=0.8)
        messages.append({"role": "tool", "content": str(obs.get("observation", "")), "toolCallId": call_id})
    return {"messages": messages, "completed": False}


def run(
    client: Any,
    tools: Dict[str, Any],
    scenarios: List[str],
    n_per_scenario: int = 2,
    manifest: set[str] | None = None,
) -> Dict[str, Any]:
    manifest = manifest if manifest is not None else set()
    samples: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {"generated": 0, "kept": 0, "rejected": 0}
    idx = 0
    for scenario in scenarios:
        seen: List[str] = []
        goals = gen_tasks(client, tools, scenario, n_per_scenario, seen)
        for goal in goals:
            if not goal or goal in seen:
                continue
            seen.append(goal)
            idx += 1
            traj = run_trajectory(client, tools, goal, idx)
            stats["generated"] += 1
            if not traj["completed"]:
                stats["rejected"] += 1
                continue
            check = chat_json(client, [{"role": "user", "content": render(
                get("agent.check"), goal=goal, trajectory=_hist_text(traj["messages"]))}], temperature=0.2)
            if not check.get("keep", True):
                stats["rejected"] += 1
                continue
            gid = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16]
            if gid in manifest:
                stats["rejected"] += 1
                continue
            manifest.add(gid)
            stats["kept"] += 1
            samples.append({
                "id": f"agent-{gid}",
                "source": "agent",
                "type": "sft",
                "scenario": scenario,
                "messages": traj["messages"],
            })
    return {"samples": samples, "stats": stats}
