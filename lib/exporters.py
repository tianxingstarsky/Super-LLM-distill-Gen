"""导出器：统一样本 JSONL → 各训练框架原生格式（纯格式转换）。

上游格式规范：
  LLaMA-Factory sharegpt：
    https://llamafactory.readthedocs.io/en/latest/getting_started/data_preparation.html
    conversations[{from: human|gpt|function_call|observation, value}]，DPO 用 chosen/rejected
  DeepSeek/Qwen messages（OpenAI 兼容）：
    SFT  {"messages": [{"role","content"[, "reasoning_content"][, "toolCalls"][, "toolCallId"]}]}
    DPO  {"prompt": [...], "chosen": [...], "rejected": [...]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# ── LLaMA-Factory sharegpt ──────────────────────────────────────────────────
def to_sharegpt_sample(sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """统一样本 → LLaMA-Factory sharegpt 格式。

    reasoning_content 合并进 gpt 轮（LLaMA-Factory 无逐样本思考字段；
    原生思考 token 由模型家族配置在微调时经 chat template 注入，
    或改用 chat 导出器的 separated 格式保留字段）。
    toolCalls → function_call 轮（value=JSON 字符串 {"name","arguments"}）；
    tool 结果 → observation 轮。
    """
    messages = sample.get("messages") or []
    conversations: List[Dict[str, str]] = []
    system: Optional[str] = None

    for m in messages:
        role = m.get("role")
        if role == "system":
            system = m.get("content", "") or system
            continue
        if role == "user":
            conversations.append({"from": "human", "value": m.get("content", "")})
        elif role == "assistant":
            value = m.get("content", "")
            if m.get("reasoning_content"):
                value = f"{m['reasoning_content']}\n\n{value}".strip()
            if value:
                conversations.append({"from": "gpt", "value": value})
            for tc in m.get("toolCalls", []):
                conversations.append({
                    "from": "function_call",
                    "value": json.dumps({"name": tc.get("name"), "arguments": tc.get("input", {})}, ensure_ascii=False),
                })
        elif role == "tool":
            conversations.append({"from": "observation", "value": m.get("content", "")})

    if not any(c["from"] in ("gpt", "function_call") for c in conversations):
        return None  # 无助手输出，不成样本

    out: Dict[str, Any] = {"conversations": conversations}
    if system:
        out["system"] = system
    if sample.get("images"):
        out["images"] = sample["images"]
    return out


# ── DeepSeek/Qwen messages（OpenAI 兼容） ───────────────────────────────────
def to_chat_sample(sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """统一样本 → {"messages": [...]}（含 reasoning_content 分字段与 toolCalls）。"""
    messages = sample.get("messages") or []
    if not any(m.get("role") == "assistant" for m in messages):
        return None
    return {"messages": messages}


def to_dpo_sample(prompt: List[Dict[str, Any]], chosen: List[Dict[str, Any]], rejected: List[Dict[str, Any]]) -> Dict[str, Any]:
    """DPO 三元组（prompt/chosen/rejected 均为消息列表）。"""
    return {"prompt": prompt, "chosen": chosen, "rejected": rejected}


# ── 导出主入口 ──────────────────────────────────────────────────────────────
def export_samples(
    samples: Iterable[Dict[str, Any]],
    fmt: str,
    out_path: str | Path,
    dpo_pairs: Iterable[Dict[str, Any]] = (),
) -> Dict[str, int]:
    """写训练格式 JSONL，返回计数。fmt: llamafactory | chat。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(out_path, "w", encoding="utf-8") as f:
        if fmt == "llamafactory":
            for s in samples:
                conv = to_sharegpt_sample(s)
                if conv:
                    f.write(json.dumps(conv, ensure_ascii=False) + "\n")
                    count += 1
        else:  # chat
            for s in samples:
                msg = to_chat_sample(s)
                if msg:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    count += 1
    dpo_count = 0
    if dpo_pairs:
        dpo_path = out_path.with_name(out_path.stem + "_dpo.jsonl")
        with open(dpo_path, "w", encoding="utf-8") as f:
            for pair in dpo_pairs:
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")
                dpo_count += 1
    return {"sft": count, "dpo": dpo_count}
