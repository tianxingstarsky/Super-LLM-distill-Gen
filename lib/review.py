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


def _content_text(content: Any) -> str:
    """消息 content → 可读文本；多模态 parts 只取文本，图片等结构部分显式标注。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks, others = [], {}
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind in ("text", "reasoning", "input_text", "output_text"):
                chunks.append(str(part.get("text", "")))
            elif kind:
                others[kind] = others.get(kind, 0) + 1
        text = "\n".join(chunk for chunk in chunks if chunk)
        for kind, count in others.items():
            note = f"［{kind} × {count}］"
            text = f"{text}\n{note}" if text else note
        return text
    return "" if content is None else str(content)


def _first_user_content(sample: Dict[str, Any]) -> str:
    for m in sample.get("messages", []):
        if m.get("role") == "user" and m.get("content"):
            text = _content_text(m["content"]).strip()
            if text:
                return text
    return ""


def _judge_suggestion(score: Any) -> Optional[str]:
    """judge 评分 → keep/reject 建议；失败或无法解析时不给建议。

    cmd_distill 对失败调用写 ``error`` 而没有 ``score``（默认空串），旧逻辑
    ``"true" in str(...)`` 会把每个失败样本标成 reject——虚假的负面建议会误导
    人工审核。这里只接受可解析出布尔 keep（或明确的 true/false 文本）。
    """
    if score is None:
        return None
    value = score
    if isinstance(score, str):
        text = score.strip()
        if not text:
            return None
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            # 兼容被截断/片段化的 judge 输出（如 '"keep": true'）；只有以布尔值
            # 结尾才判定，报错文本/空响应不会产生建议。
            lowered = text.lower().rstrip("}").strip().rstrip(",").strip()
            if lowered.endswith("true") or lowered in ("keep", "1"):
                return "keep"
            if lowered.endswith("false") or lowered in ("reject", "0"):
                return "reject"
            return None
    if isinstance(value, dict):
        for key in ("keep", "pass", "valid"):
            if key in value:
                return "keep" if value[key] is True else "reject" if value[key] is False else None
        return None
    if isinstance(value, bool):
        return "keep" if value else "reject"
    return None


def _plain_messages(sample: Dict[str, Any]) -> str:
    """把样本转成便于人工阅读的纯文本（无 JSON 符号；多模态只取文本并标注结构部分）。"""
    lines = []
    for m in sample.get("messages", []):
        role = m.get("role")
        if role == "assistant":
            if m.get("reasoning_content"):
                lines.append(f"【思考】{m['reasoning_content']}")
            if m.get("content"):
                lines.append(f"【回答】{_content_text(m['content'])}")
            for tc in (m.get("toolCalls") or m.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                args = tc.get("input") or tc.get("arguments") or {}
                if isinstance(args, dict):
                    argstr = "，".join(f"{k} = {v}" for k, v in args.items())
                else:
                    argstr = str(args)
                lines.append(f"【工具调用】{tc.get('name')}（{argstr}）")
        elif role == "tool":
            mark = "❌" if m.get("isError") else "✔"
            lines.append(f"【工具结果{mark}】{_content_text(m.get('content', ''))}")
        else:
            lines.append(f"【{role}】{_content_text(m.get('content', ''))}")
    return "\n\n".join(lines)


def build_records(samples: List[Dict[str, Any]], scores: Dict[str, str]) -> List[Dict[str, Any]]:
    """样本 → 审核中心记录（id=样本 ID；payload=完整样本 JSON，修订不丢元数据）。"""
    from lib.quality import sample_hash
    from lib import review_editor as editor
    records = []
    for s in samples:
        instruction = _first_user_content(s) or "（无用户指令，工具型样本）"
        conversation = _plain_messages(s) or "（空对话）"
        rec: Dict[str, Any] = {
            "id": s.get("id", ""),
            "sample_id": s.get("id", ""),
            "sample_hash": sample_hash(s),
            "payload": editor.pack_sample(s),
            "instruction": instruction,
            "conversation": conversation,
            "meta": f"model={s.get('model')} finish={s.get('finish_reason')} 错误步骤={s.get('error_tool_steps', 0)} images={len(s.get('images') or [])}",
        }
        suggestion = _judge_suggestion(scores.get(s.get("id")))
        if suggestion:
            rec["suggestion"] = suggestion
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
    修订基于 unpack_record 还原的完整样本深拷贝：images/tools/其余元数据原样保留，
    只允许改正文与 assistant 思考；role/消息数/元数据不符或试图丢图一律 ValueError。
    序号分配走 review_center.add_revision（单事务），并发修订也绝不覆盖同 id。
    返回新 sample_id。"""
    from lib import review_center as rc
    from lib import review_editor as editor

    original = editor.unpack_record(record)
    revised = editor.validate_edits(original, messages)
    base = record["sample_id"]

    def build(new_id: str) -> Dict[str, Any]:
        revised["id"] = new_id
        revised["source"] = "human-edit"
        revised["model"] = "human-edit"
        revised["finish_reason"] = "edited"
        rec = build_records([revised], {})[0]
        rec["meta"] = (f"{record.get('meta', '')} | 修订自 {base}（{reviewer}）").strip(" |")
        return rec

    return rc.add_revision(dataset, base, build)


REVISE_SCOPES = {"all": "全部消息", "assistant": "仅助手回答（content）", "thinking": "仅助手思考（reasoning_content）"}


def propose_revision(messages: List[Dict[str, Any]], instruction: str, scope: str,
                     client: Any, max_input_chars: int = 200000) -> List[Dict[str, Any]]:
    """审核台临时修订：LLM 按指令产出候选 messages，**服务端强制 scope**（不信任模型）。

    scope 语义（服务端按 role 收紧，模型越权输出直接忽略）：
      - all：各消息 content + assistant reasoning_content；
      - assistant：仅 assistant 的 content（绝不动 user/system/tool 的 content）；
      - thinking：仅 assistant 的 reasoning_content。
    校验要点：JSON 契约、等长同 role、scope 外消息原样保留、多模态结构不可压成文本；
    任何不符抛 ValueError。"""
    from lib.llm_client import chat_json
    from lib.prompts import get, render
    from lib import review_editor as editor

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
        assistant = src.get("role") == "assistant"
        fields: List[str] = []
        if scope == "all" or (scope == "assistant" and assistant):
            fields.append("content")
        if scope in ("all", "thinking") and assistant:
            fields.append("reasoning_content")
        fixed.append(editor.merge_scoped_fields(src, dst, fields))
    return fixed


def write_review_log(decisions: List[Dict[str, str]], out_path: pathlib.Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in decisions) + "\n", encoding="utf-8"
    )
