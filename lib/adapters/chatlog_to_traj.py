"""chatlog_to_traj：把"人与 agent 交互上下文"JSONL 转成 OpenCUA 轨迹 JSONL 格式。

上游参照：OpenCUA cot-generator 输入格式（components/opencua/data/cot-generate/README.md）：
  {"task_id", "instruction", "traj": [{"image": "screenshot.png", "value": {"code": "pyautogui.click(...)"}}]}
注意（spike 关键发现）：OpenCUA 原版 Reflector/Generator 深度依赖截图
（前后截图对比 + 红圈标注坐标），仅适合带截图的 GUI 轨迹；纯文本/工具调用日志
走本适配器产出的同构 JSONL，由文本化三角色提示词（distill_prompts.py）蒸馏。

本适配器职责（纯格式转换，无智能）：
  1. 规范化对话轮次（跳过空轮、截断超长内容、去重重复轮）
  2. task_id = sha256(首条用户指令)（确定性、天然幂等）
  3. 文本/工具动作 → value.code 的结构化文本表示；错误步骤显式标记 meta
  4. gui 模式：对已符合 AgentNet/OpenCUA 轨迹格式的输入做透传校验

输入（data/seeds/chatlogs/*.jsonl，每行一个任务）：
  {"task_id": 可选, "turns": [
     {"role": "user"|"assistant"|"tool",
      "content": str,                      # assistant/user 文本
      "name": str, "args": obj,            # tool: 调用名与参数
      "result": {"ok": bool, "output": str, "exit_code": int}}]}

输出：data/output/chatlogs_traj.jsonl（OpenCUA 同构 schema）
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MAX_CONTENT_CHARS = 8000  # 超长内容截断（上下文防爆 L0 的一部分）


def _truncate(text: str, limit: int = MAX_CONTENT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…[truncated]"


def _dedup_turns(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """相邻完全重复的轮次只保留一个。"""
    out: List[Dict[str, Any]] = []
    for turn in turns:
        if out and out[-1] == turn:
            continue
        out.append(turn)
    return out


def _turn_to_code(turn: Dict[str, Any]) -> Dict[str, Any]:
    """把一个轮次映射为 OpenCUA traj 步骤的 value.code（文本模式）。"""
    role = turn.get("role", "assistant")
    meta: Dict[str, Any] = {}
    if role == "user":
        code = f"user_correction({json.dumps(_truncate(turn.get('content', '')), ensure_ascii=False)})"
        meta["feedback"] = True
    elif role == "tool":
        name = turn.get("name", "call_tool")
        args = json.dumps(turn.get("args", {}), ensure_ascii=False)
        code = f"{name}({args})"
        result = turn.get("result") or {}
        if result.get("exit_code") not in (None, 0) or result.get("ok") is False:
            meta["error"] = True
            meta["error_hint"] = _truncate(str(result.get("output", ""))[:500])
        else:
            meta["ok"] = True
    else:  # assistant
        code = f"assistant_answer({json.dumps(_truncate(turn.get('content', '')), ensure_ascii=False)})"
    return code, meta


def turns_to_traj(task: Dict[str, Any]) -> Dict[str, Any]:
    """单任务 dict → OpenCUA 同构轨迹 dict（文本模式，image 置 null）。

    首条用户轮 = 任务指令（instruction），不进入 traj；后续用户轮 = 纠正/追问（feedback）。
    """
    turns = _dedup_turns([t for t in task.get("turns", []) if t.get("content") or t.get("role") == "tool"])
    user_turns = [t for t in turns if t.get("role") == "user"]
    instruction = task.get("instruction") or (user_turns[0].get("content", "") if user_turns else "")
    instruction = _truncate(instruction)

    traj = []
    first_user_seen = False
    for turn in turns:
        if turn.get("role") == "user":
            if not first_user_seen:
                first_user_seen = True
                continue  # 首条用户轮是任务指令，已进入 instruction
        code, meta = _turn_to_code(turn)
        step: Dict[str, Any] = {
            "image": None,  # 纯文本日志无截图；原版 cot-generator 需截图，故本输出走文本化蒸馏
            "value": {"code": code},
        }
        if meta:
            step["meta"] = meta
        traj.append(step)

    task_id = task.get("task_id") or hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:16]
    return {"task_id": task_id, "instruction": instruction, "traj": traj}


def convert_jsonl(input_path: str | Path, output_path: str | Path) -> int:
    """整文件转换，返回成功行数。"""
    input_path, output_path = Path(input_path), Path(output_path)
    count = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(input_path, encoding="utf-8") as fin, open(output_path, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            task = json.loads(line)
            traj = turns_to_traj(task)
            if not traj["traj"]:
                continue
            fout.write(json.dumps(traj, ensure_ascii=False) + "\n")
            count += 1
    return count


def validate_gui_traj_line(line: str) -> Optional[str]:
    """gui 模式透传校验：返回错误信息或 None。用于校验 AgentNet 风格输入。"""
    try:
        rec = json.loads(line)
    except json.JSONDecodeError as e:
        return f"invalid json: {e}"
    if not isinstance(rec.get("task_id"), str) or not isinstance(rec.get("instruction"), str):
        return "missing task_id/instruction"
    for step in rec.get("traj", []):
        if "value" not in step or "code" not in step["value"]:
            return f"bad step in {rec.get('task_id')}: {step}"
    return None
