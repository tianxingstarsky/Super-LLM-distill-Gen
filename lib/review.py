"""审核中心（单进程融合）：df review push/pull。

把预览样本推入 SQLite 审核中心（judge 评分作为建议值），人工标注 keep/reject 后拉回，
通过率达标（≥阈值且 ≥最少条数）自动把 G3 放量闸置为 approved。
协作者远程审核走内置 HTTP API（lib/review_center，无 Redis/ES/Argilla 外部依赖）。
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Dict, List, Optional

DATASET_NAME = "rollout_review"
PASS_THRESHOLD = 0.9
MIN_REVIEWED = 10


def _first_user_content(sample: Dict[str, Any]) -> str:
    for m in sample.get("messages", []):
        if m.get("role") == "user" and m.get("content"):
            return str(m["content"])[:500]
    return ""


def _plain_messages(sample: Dict[str, Any]) -> str:
    """把样本转成便于人工阅读的纯文本（无 JSON 符号）。"""
    lines = []
    for m in sample.get("messages", []):
        role = m.get("role")
        if role == "assistant":
            if m.get("reasoning_content"):
                lines.append(f"【思考】{m['reasoning_content']}")
            if m.get("content"):
                lines.append(f"【回答】{m['content']}")
            for tc in m.get("toolCalls", []):
                args = tc.get("input") or {}
                if isinstance(args, dict):
                    argstr = "，".join(f"{k} = {v}" for k, v in list(args.items())[:6])
                else:
                    argstr = str(args)
                lines.append(f"【工具调用】{tc.get('name')}（{argstr}）")
        elif role == "tool":
            mark = "❌" if m.get("isError") else "✔"
            lines.append(f"【工具结果{mark}】{str(m.get('content', ''))[:800]}")
        else:
            lines.append(f"【{role}】{m.get('content', '')}")
    return "\n\n".join(lines)


def build_records(samples: List[Dict[str, Any]], scores: Dict[str, str]) -> List[Dict[str, Any]]:
    """样本 → 审核中心记录（id=样本 ID；suggestion=judge 评分，供人工参考）。"""
    records = []
    for s in samples:
        instruction = _first_user_content(s) or "（无用户指令，工具型样本）"
        conversation = _plain_messages(s) or "（空对话）"
        rec: Dict[str, Any] = {
            "id": s.get("id", ""),
            "sample_id": s.get("id", ""),
            "instruction": instruction,
            "conversation": conversation,
            "meta": f"model={s.get('model')} finish={s.get('finish_reason')} 错误步骤={s.get('error_tool_steps', 0)}",
        }
        if s.get("id") in scores:
            rec["suggestion"] = "keep" if "true" in str(scores[s["id"]]) else "reject"
        records.append(rec)
    return records


def push_samples(samples: List[Dict[str, Any]], scores: Dict[str, str], client: Any = None,
                 dataset_name: str = DATASET_NAME) -> int:
    """样本入库审核中心（SQLite，单进程融合；同机直连，无外部服务依赖）。"""
    from lib import review_center as rc

    return rc.add_records(dataset_name, build_records(samples, scores))


def pull_decisions(client: Any = None, dataset_name: str = DATASET_NAME) -> List[Dict[str, str]]:
    """拉回全部人工标注（含身份与理由，供 G3 汇总统计）。"""
    from lib import review_center as rc

    return [{"sample_id": r["sample_id"], "decision": r["decision"], "reason": r["reason"],
             "username": r["username"]} for r in rc.responses(dataset_name)]


def decide_gate(decisions: List[Dict[str, str]], threshold: float = PASS_THRESHOLD, minimum: int = MIN_REVIEWED) -> Dict[str, Any]:
    """标注统计 + 是否放行 G3（通过率 ≥ 阈值且条数 ≥ 下限）。"""
    total = len(decisions)
    keeps = sum(1 for d in decisions if d["decision"] == "keep")
    rate = keeps / total if total else 0.0
    return {
        "reviewed": total,
        "keep": keeps,
        "reject": total - keeps,
        "pass_rate": round(rate, 3),
        "release": total >= minimum and rate >= threshold,
    }


def write_review_log(decisions: List[Dict[str, str]], out_path: pathlib.Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in decisions) + "\n", encoding="utf-8"
    )
