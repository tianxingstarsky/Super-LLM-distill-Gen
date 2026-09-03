"""rollout_import：ZCode rollout JSONL（model-io-sess_*.jsonl）→ 训练样本。

输入记录结构（实测）：
  {model: {modelId, providerId, role, variant}, requestId, attempt,
   request: {messages: [{role: user|assistant|tool, content: str|[{type: reasoning|text, text}],
                        toolCalls: [{id, name, input}], modelRef, isError, toolCallId, toolName}],
             messageOffset, messageCount, messagesKind: "tail", toolNames},
   response: {finishReason: "stop"|"tool-calls"|..., reasoningText, text, toolCalls, usage},
   error: {...} | 无}

核心设计：
  1. 每条成功记录 = 一个闭环多轮训练样本（历史 tail + 本次完整输出），
     messagesKind="tail" 决定了无法无损重组整段会话——不做有损拼接。
  2. 错误记录（限流/中断/空回复）→ rejected 归档（DPO 负样本候选）。
  3. tool 消息 isError=true 是运行时事实信号 → 统计并标记（Reflector 优先级①）。
  4. 样本级 sha256 manifest 查重（防重复四层之全局清单），增量导入零重复。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


# ── 基础流式读取与分类 ──────────────────────────────────────────────────────
def iter_records(path: str | Path) -> Iterator[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def record_status(rec: Dict[str, Any]) -> str:
    """ok=成功闭环；error=调用失败/中断（限流、断流、空回复等）。"""
    resp = rec.get("response") or {}
    if rec.get("error") or (not resp.get("text") and not resp.get("toolCalls")):
        return "error"
    return "ok"


# ── 消息重建 ────────────────────────────────────────────────────────────────
def _content_to_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("text", "reasoning") and block.get("text"):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def assistant_msg_from_response(rec: Dict[str, Any], cot_style: str = "r1") -> Optional[Dict[str, Any]]:
    """把 response 组装为 assistant 消息（cot_style: r1|qwen3|raw，见配置）。"""
    resp = rec.get("response") or {}
    reasoning = (resp.get("reasoningText") or "").strip()
    text = (resp.get("text") or "").strip()
    tool_calls = resp.get("toolCalls") or []

    if cot_style == "r1":
        content = ""
        if reasoning:
            content += f"<思考>\n{reasoning}\n</思考>"
        if text:
            content += ("\n" if content else "") + text
    elif cot_style == "qwen3":
        content = [
            *([{"type": "reasoning", "text": reasoning}] if reasoning else []),
            *([{"type": "text", "text": text}] if text else []),
        ]
    else:  # raw
        content = text

    msg: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["toolCalls"] = tool_calls
    return msg if (content or tool_calls) else None


def normalize_tool_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """规整为标准 OpenAI messages 字段子集（训练格式），丢弃 modelRef 等内部字段。
    tool 消息暂留 isError 供统计（record_to_sample 计数后剥离）。"""
    out = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant", "tool", "system"):
            continue
        item: Dict[str, Any] = {"role": role}
        if role == "assistant":
            item["content"] = _content_to_str(m.get("content"))
            if m.get("toolCalls"):
                item["toolCalls"] = [
                    {"id": tc.get("id"), "name": tc.get("name"), "input": tc.get("input")}
                    for tc in m["toolCalls"]
                    if isinstance(tc, dict)
                ]
        elif role == "tool":
            item["content"] = _content_to_str(m.get("content"))
            if m.get("toolCallId"):
                item["toolCallId"] = m["toolCallId"]
            if m.get("isError"):
                item["isError"] = True  # 运行时事实信号，导出前剥离
        else:
            item["content"] = _content_to_str(m.get("content"))
        out.append(item)
    return out


def record_to_sample(
    rec: Dict[str, Any],
    cot_style: str = "r1",
    max_messages: int = 0,
) -> Dict[str, Any]:
    """成功记录 → SFT 样本（闭环多轮）。max_messages>0 时只保留尾部 N 条消息。"""
    history = normalize_tool_messages(rec.get("request", {}).get("messages", []))
    if max_messages > 0:
        history = history[-max_messages:]
    final = assistant_msg_from_response(rec, cot_style)
    messages = history + ([final] if final else [])

    error_tool_steps = sum(1 for m in history if m.pop("isError", False))
    return {
        "id": sample_id(messages, "rollout"),
        "source": "rollout",
        "type": "sft",
        "session_id": rec.get("sessionId", ""),
        "request_id": rec.get("requestId", ""),
        "model": (rec.get("model") or {}).get("modelId", ""),
        "finish_reason": (rec.get("response") or {}).get("finishReason", ""),
        "cot_style": cot_style,
        "error_tool_steps": error_tool_steps,
        "usage": (rec.get("response") or {}).get("usage", {}),
        "messages": messages,
    }


# ── 样本级全局查重（manifest） ──────────────────────────────────────────────
def sample_id(messages: List[Dict[str, Any]], source: str) -> str:
    digest = hashlib.sha256(
        json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"{source}-{digest[:16]}"


class ManifestDedup:
    """sha256 全局清单：增量导入零重复；同时给出命中统计。"""

    def __init__(self, manifest_path: str | Path):
        self.path = Path(manifest_path)
        self.ids: set[str] = set()
        if self.path.exists():
            self.ids = {line.strip() for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()}
        self.hits = 0
        self.new = 0

    def seen(self, sample_id_: str) -> bool:
        return sample_id_ in self.ids

    def add(self, sample_id_: str) -> None:
        if sample_id_ in self.ids:
            self.hits += 1
        else:
            self.ids.add(sample_id_)
            self.new += 1

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(sorted(self.ids)) + "\n", encoding="utf-8")
