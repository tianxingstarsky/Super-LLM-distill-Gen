"""Agent 工具使用零参考数据管线：任务生成 → 执行循环（act/simulate）→ 轨迹质检。

Instructor + Simulator 双角色：Simulator 扮演环境（搜索返回/代码输出/报错），
模拟观察仅用于合成候选，不能证明工具真实执行或最终回答正确。
输出标记 synthetic_unverified，不能直接并入已重放验证的 Agent 数据流。
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


def prune_redundant(messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
    """仅删除相邻、调用和观察都完全相同的模拟步骤。

    返回 (修剪后 messages, 剪掉的调用数)。不改变首尾结构：user 开头、最终 assistant 结尾。
    """
    pruned: List[Dict[str, Any]] = []
    removed = 0
    previous = None
    index = 0
    while index < len(messages):
        message = messages[index]
        calls = message.get("toolCalls") or []
        if (message.get("role") == "assistant" and isinstance(calls, list) and len(calls) == 1
                and index + 1 < len(messages) and messages[index + 1].get("role") == "tool"
                and isinstance(calls[0], dict)):
            call, observation = calls[0], messages[index + 1]
            call_id = call.get("id")
            if call_id and observation.get("toolCallId") == call_id:
                call_without_id = {key: value for key, value in call.items() if key != "id"}
                observation_without_link = {key: value for key, value in observation.items() if key != "toolCallId"}
                signature = json.dumps([message.get("content"), message.get("reasoning_content"),
                                        call_without_id, observation_without_link], ensure_ascii=False, sort_keys=True)
                later = json.dumps(messages[index + 2:], ensure_ascii=False, sort_keys=True)
                if signature == previous and call_id not in later:
                    removed += 1
                    index += 2
                    continue
                previous = signature
                pruned.extend((message, observation))
                index += 2
                continue
        previous = None
        pruned.append(message)
        index += 1
    return pruned, removed


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
    stats: Dict[str, Any] = {"generated": 0, "kept": 0, "rejected": 0, "pruned_calls": 0}
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
            # 效率守卫：冗余修剪（同名同参的重复调用=绕弯子，直接剪掉）
            pruned_msgs, removed = prune_redundant(traj["messages"])
            traj["messages"] = pruned_msgs
            stats["pruned_calls"] += removed
            stats["generated"] += 1
            if not traj["completed"]:
                stats["rejected"] += 1
                continue
            check = chat_json(client, [{"role": "user", "content": render(
                get("agent.check"), goal=goal, trajectory=_hist_text(traj["messages"]))}], temperature=0.2)
            if check.get("keep") is not True:  # 缺 keep 的响应不得冒充通过（fail closed）
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
                "verification_status": "synthetic_unverified",
                "messages": traj["messages"],
            })
    return {"samples": samples, "stats": stats}
