"""OpenCUA cot-generator 输出 → 统一 SFT 样本（纯格式转换适配器）。

上游成品（components/opencua/data/cot-generate，MIT）产出 merged JSONL：
  {task_id, instruction, natural_language_task, alignment_score, efficiency_score,
   task_completed, task_difficulty, traj: [{index, image, value: {code, observation,
   thought, reflection, instruction, …}}]}
映射为与 rollout 蒸馏同构的训练格式：
  user(任务指令) → assistant(思考=observation+thought+reflection，动作=computer_action
  工具调用) → tool(下一步观察) → … → 最终 assistant。
质量门：task_completed=true 才保留（上游 Summarizer 判定）。
"""
from __future__ import annotations

from typing import Any, Dict, List


def merged_to_sample(record: Dict[str, Any]) -> Dict[str, Any]:
    traj = record.get("traj", [])
    instruction = record.get("instruction") or record.get("natural_language_task", "")
    messages: List[Dict[str, Any]] = [{"role": "user", "content": instruction}]

    for i, step in enumerate(traj):
        v = step.get("value", {})
        reasoning_parts = [v.get("observation", ""), v.get("thought", ""), v.get("reflection", "")]
        reasoning = "\n".join(p for p in reasoning_parts if p).strip()
        call_id = f"gui_{step.get('index', i)}"
        messages.append({
            "role": "assistant",
            "content": v.get("instruction", "").strip(),  # 动作的简短指令描述
            "reasoning_content": reasoning,
            "toolCalls": [{
                "id": call_id,
                "name": "computer_action",
                "input": {"code": v.get("code", "")},
            }],
        })
        # 下一步的 observation 是本步动作的结果（OpenCUA 语义：截图是动作前的状态）
        if i + 1 < len(traj):
            messages.append({
                "role": "tool",
                "content": traj[i + 1].get("value", {}).get("observation", ""),
                "toolCallId": call_id,
            })

    return {
        "id": str(record.get("task_id", "")),
        "source": "gui",
        "type": "sft",
        "scenario": record.get("natural_language_task", ""),
        "messages": messages,
        "quality": {
            "task_completed": bool(record.get("task_completed")),
            "alignment_score": record.get("alignment_score"),
            "efficiency_score": record.get("efficiency_score"),
            "task_difficulty": record.get("task_difficulty"),
        },
    }


def merged_to_samples(path: str) -> Dict[str, Any]:
    """merged JSONL → 样本列表 + 统计（task_completed 质量门）。"""
    import json

    samples: List[Dict[str, Any]] = []
    stats = {"tasks": 0, "kept": 0, "rejected_unfinished": 0}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            stats["tasks"] += 1
            sample = merged_to_sample(record)
            if sample["quality"]["task_completed"]:
                samples.append(sample)
                stats["kept"] += 1
            else:
                stats["rejected_unfinished"] += 1
    return {"samples": samples, "stats": stats}
