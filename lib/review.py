"""Argilla 人工审核：df review push/pull。

把预览样本推送到 Argilla（judge 评分作为建议值），人工标注 keep/reject 后拉回，
通过率达标（≥阈值且 ≥最少条数）自动把 G3 放量闸置为 approved。
无 Argilla 服务时给出明确指引，不阻塞其他命令。
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Optional

ARGILLA_URL = "http://localhost:6900"
ARGILLA_API_KEY = "admin.apikey"  # quickstart 镜像默认
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
    """样本 → Argilla 记录（suggestion=judge 评分，供人工参考）。"""
    records = []
    for s in samples:
        fields: Dict[str, Any] = {
            "sample_id": s.get("id", ""),
            "instruction": _first_user_content(s),
            "conversation": _plain_messages(s),
            "meta": f"model={s.get('model')} finish={s.get('finish_reason')} 错误步骤={s.get('error_tool_steps', 0)}",
        }
        rec: Dict[str, Any] = {"fields": fields}
        if s.get("id") in scores:
            rec["suggestions"] = [{"question_name": "keep_label", "value": "keep" if "true" in str(scores[s["id"]]) else "reject"}]
        records.append(rec)
    return records


def push_samples(samples: List[Dict[str, Any]], scores: Dict[str, str], client: Any = None) -> int:
    import argilla as rg

    if client is None:
        client = rg.Argilla(api_url=ARGILLA_URL, api_key=ARGILLA_API_KEY)

    settings = rg.Settings(
        fields=[
            rg.TextField(name="sample_id", title="样本 ID"),
            rg.TextField(name="instruction", title="任务指令"),
            rg.TextField(name="conversation", title="对话内容（纯文本）", use_markdown=True),
            rg.TextField(name="meta", title="元数据"),
        ],
        questions=[rg.LabelQuestion(name="keep_label", title="保留/驳回", labels=["keep", "reject"])],
    )
    dataset = rg.Dataset(name=DATASET_NAME, settings=settings)
    dataset.create()
    dataset.records.log(records=build_records(samples, scores))
    return len(samples)


def pull_decisions(client: Any = None, dataset_name: str = DATASET_NAME) -> List[Dict[str, str]]:
    import argilla as rg

    if client is None:
        client = rg.Argilla(api_url=ARGILLA_URL, api_key=ARGILLA_API_KEY)
    ds = client.datasets(name=dataset_name)
    decisions = []
    for rec in ds.records:
        if rec.status == "completed" and rec.suggestions:
            value = None
            for resp in rec.suggestions:
                value = resp.value
            decisions.append({"sample_id": rec.fields["sample_id"], "decision": str(value)})
    return decisions


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
