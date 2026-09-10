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
            return str(m["content"])
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
                    argstr = "，".join(f"{k} = {v}" for k, v in args.items())
                else:
                    argstr = str(args)
                lines.append(f"【工具调用】{tc.get('name')}（{argstr}）")
        elif role == "tool":
            mark = "❌" if m.get("isError") else "✔"
            lines.append(f"【工具结果{mark}】{str(m.get('content', ''))}")
        else:
            lines.append(f"【{role}】{m.get('content', '')}")
    return "\n\n".join(lines)


def build_records(samples: List[Dict[str, Any]], scores: Dict[str, str]) -> List[Dict[str, Any]]:
    """样本 → 审核中心记录（id=样本 ID；suggestion=judge 评分，供人工参考）。"""
    from lib.quality import sample_hash
    records = []
    for s in samples:
        instruction = _first_user_content(s) or "（无用户指令，工具型样本）"
        conversation = _plain_messages(s) or "（空对话）"
        rec: Dict[str, Any] = {
            "id": s.get("id", ""),
            "sample_id": s.get("id", ""),
            "sample_hash": sample_hash(s),
            "payload": json.dumps(s.get("messages") or [], ensure_ascii=False),
            "instruction": instruction,
            "conversation": conversation,
            "meta": f"model={s.get('model')} finish={s.get('finish_reason')} 错误步骤={s.get('error_tool_steps', 0)} images={len(s.get('images') or [])}",
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

    return rc.responses(dataset_name)


def decide_gate(decisions: List[Dict[str, str]], threshold: float = PASS_THRESHOLD, minimum: int = MIN_REVIEWED) -> Dict[str, Any]:
    """标注统计 + 是否放行 G3（通过率 ≥ 阈值且条数 ≥ 下限）。"""
    by_sample = {}
    for d in decisions:
        by_sample.setdefault(d["sample_id"], set()).add(d["decision"])
    total = len(by_sample)
    keeps = sum(values == {"keep"} for values in by_sample.values())
    rate = keeps / total if total else 0.0
    return {
        "reviewed": total,
        "responses": len(decisions),
        "conflicts": sum(len(values) > 1 for values in by_sample.values()),
        "keep": keeps,
        "reject": total - keeps,
        "pass_rate": round(rate, 3),
        "release": total >= minimum and rate >= threshold,
    }


def revise_sample(dataset: str, record: Dict[str, Any], messages: List[Dict[str, Any]],
                  reviewer: str = "human") -> str:
    """把人工编辑后的消息保存为**新版本样本**（新 sample_id，原记录不动）。

    与内容哈希绑定一致：编辑产生新内容=新版本，旧审核票不会错误地套用到新内容。
    返回新 sample_id。"""
    from lib import review_center as rc

    base = record["sample_id"]
    existing = rc.sample_ids_like(dataset, base + "-r")
    numbers = [int(m.split("-r")[-1]) for m in existing
               if m.startswith(base + "-r") and m[len(base) + 2:].isdigit()]
    new_id = f"{base}-r{max(numbers, default=0) + 1}"
    sample = {"id": new_id, "messages": messages, "source": "human-edit",
              "model": "human-edit", "finish_reason": "edited"}
    rec = build_records([sample], {})[0]
    rec["meta"] = (f"{record.get('meta', '')} | 修订自 {base}（{reviewer}）").strip(" |")
    rc.add_records(dataset, [rec])
    return new_id


REVISE_SCOPES = {"all": "全部消息", "assistant": "仅助手回答（content）", "thinking": "仅助手思考（reasoning_content）"}


def propose_revision(messages: List[Dict[str, Any]], instruction: str, scope: str,
                     client: Any, max_input_chars: int = 200000) -> List[Dict[str, Any]]:
    """审核台临时修订：LLM 按指令产出候选 messages，**服务端强制 scope**（不信任模型）。

    校验要点：JSON 契约、等长同 role、scope 外消息原样保留；任何不符抛 ValueError。"""
    from lib.llm_client import chat_json
    from lib.prompts import get, render

    if scope not in REVISE_SCOPES:
        raise ValueError(f"未知改动范围：{scope!r}（可用 {sorted(REVISE_SCOPES)}）")
    if not (instruction or "").strip():
        raise ValueError("修改要求不能为空")
    payload = json.dumps(messages, ensure_ascii=False)
    if len(payload) > max_input_chars:
        raise ValueError(f"样本过大（{len(payload)} 字符 > {max_input_chars}），请改用逐条手工编辑")
    out = chat_json(client, [{"role": "user", "content": render(
        get("revise.instruction"), scope=REVISE_SCOPES[scope], instruction=instruction.strip(),
        messages=payload)}], temperature=0.2)
    edited = out.get("messages")
    if not isinstance(edited, list) or len(edited) != len(messages):
        raise ValueError("修订输出与输入消息数不一致")
    fixed: List[Dict[str, Any]] = []
    for src, dst in zip(messages, edited):
        if not isinstance(dst, dict) or dst.get("role") != src.get("role"):
            raise ValueError("修订输出 role 顺序与输入不一致")
        merged = dict(src)
        if scope in ("all", "assistant"):
            merged["content"] = dst.get("content", src.get("content", ""))
        if scope in ("all", "thinking") and src.get("role") == "assistant":
            merged["reasoning_content"] = dst.get("reasoning_content", src.get("reasoning_content", ""))
        fixed.append(merged)
    return fixed


def write_review_log(decisions: List[Dict[str, str]], out_path: pathlib.Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in decisions) + "\n", encoding="utf-8"
    )
