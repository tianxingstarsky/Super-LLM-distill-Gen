"""导出器：统一样本 JSONL → 各训练框架原生格式（纯格式转换）。

上游格式规范：
  LLaMA-Factory sharegpt：
    https://llamafactory.readthedocs.io/en/latest/getting_started/data_preparation.html
    conversations[{from: human|gpt|function_call|observation, value}]，DPO 用 chosen/rejected
  DeepSeek/Qwen messages（OpenAI 兼容）：
    SFT  {"messages": [{"role","content"[, "reasoning_content"][, "toolCalls"][, "toolCallId"]}]}
    DPO  {"prompt": [...], "chosen": [...], "rejected": [...]}
  minimind（jingyaogong/minimind，dataset/lm_dataset.py 实证）：
    预训练  {"text": "..."}
    SFT     {"conversations": [{"role","content"[,"reasoning_content"][,"tool_calls"(JSON 字符串)][,"tools"]}]}
    DPO     {"chosen": [完整消息列表], "rejected": [完整消息列表]}（含 prompt 前缀）
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
def _strip_internal(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """剥离内部元数据（isError 等），输出标准训练字段。"""
    out = []
    for m in messages:
        item = {k: v for k, v in m.items() if k not in ("isError",)}
        out.append(item)
    return out


def to_chat_sample(sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """统一样本 → {"messages": [...]}（含 reasoning_content 分字段与 toolCalls）。"""
    messages = sample.get("messages") or []
    if not any(m.get("role") == "assistant" for m in messages):
        return None
    out: Dict[str, Any] = {"messages": _strip_internal(messages)}
    if sample.get("images"):
        out["images"] = sample["images"]  # 多模态：图片路径列表（框架侧解析）
    return out


def to_dpo_sample(prompt: List[Dict[str, Any]], chosen: List[Dict[str, Any]], rejected: List[Dict[str, Any]]) -> Dict[str, Any]:
    """DPO 三元组（prompt/chosen/rejected 均为消息列表）。"""
    return {"prompt": prompt, "chosen": chosen, "rejected": rejected}


# ── minimind ─────────────────────────────────────────────────────────────────
def _minimind_msg(m: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """统一消息 → minimind 消息：role/content；assistant 保留 reasoning_content；
    toolCalls → tool_calls（JSON 字符串，minimind 加载侧 json.loads 还原）；
    剥离内部元数据（isError/toolCallId）。"""
    role = m.get("role")
    if role not in ("system", "user", "assistant", "tool"):
        return None
    item: Dict[str, str] = {"role": role, "content": m.get("content", "") or ""}
    if role == "assistant" and m.get("reasoning_content"):
        item["reasoning_content"] = m["reasoning_content"]
    if role == "assistant" and m.get("toolCalls"):
        item["tool_calls"] = json.dumps(
            [{"name": tc.get("name"), "arguments": tc.get("input", {})} for tc in m["toolCalls"]],
            ensure_ascii=False,
        )
    return item


def to_minimind_sft(sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """统一样本 → minimind SFT：{"conversations": [{"role","content",…}]}。"""
    messages = sample.get("messages") or []
    conversations = [c for c in (_minimind_msg(m) for m in messages) if c]
    if not any(c["role"] == "assistant" and c["content"] for c in conversations):
        return None  # 无助手输出，不成样本
    return {"conversations": conversations}


def to_minimind_dpo(pair: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """统一 DPO 对（prompt/chosen/rejected）→ minimind：chosen/rejected 为含 prompt 的完整消息列表。"""
    prompt = [c for c in (_minimind_msg(m) for m in pair.get("prompt", [])) if c]
    chosen = [c for c in (_minimind_msg(m) for m in pair.get("chosen", [])) if c]
    rejected = [c for c in (_minimind_msg(m) for m in pair.get("rejected", [])) if c]
    if not chosen or not rejected:
        return None
    return {"chosen": prompt + chosen, "rejected": prompt + rejected}


def export_minimind(
    samples: Iterable[Dict[str, Any]],
    out_path: str | Path,
    corpus_path: str | Path | None = None,
    dpo_path: str | Path | None = None,
) -> Dict[str, int]:
    """导出 minimind 三件套：sft_t2t.jsonl（SFT）、pretrain_t2t.jsonl（语料，存在才写）、
    dpo.jsonl（偏好对，存在才写）。out_path 只决定输出目录，文件名固定对齐 minimind 习惯。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts: Dict[str, int] = {"sft": 0, "pretrain": 0, "dpo": 0}

    sft_target = out_path.parent / "sft_t2t.jsonl"
    with open(sft_target, "w", encoding="utf-8") as fh:
        for s in samples:
            converted = to_minimind_sft(s)
            if converted:
                fh.write(json.dumps(converted, ensure_ascii=False) + "\n")
                counts["sft"] += 1

    if corpus_path and Path(corpus_path).exists():
        with open(corpus_path, encoding="utf-8") as src, \
             open(out_path.parent / "pretrain_t2t.jsonl", "w", encoding="utf-8") as fh:
            for line in src:
                if not line.strip():
                    continue
                text = json.loads(line).get("text", "")
                if text.strip():
                    fh.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
                    counts["pretrain"] += 1

    if dpo_path and Path(dpo_path).exists():
        with open(dpo_path, encoding="utf-8") as src, \
             open(out_path.parent / "dpo.jsonl", "w", encoding="utf-8") as fh:
            for line in src:
                if not line.strip():
                    continue
                converted = to_minimind_dpo(json.loads(line))
                if converted:
                    fh.write(json.dumps(converted, ensure_ascii=False) + "\n")
                    counts["dpo"] += 1
    return counts


# ── 导出主入口 ──────────────────────────────────────────────────────────────
def export_samples(
    samples: Iterable[Dict[str, Any]],
    fmt: str,
    out_path: str | Path,
    dpo_pairs: Iterable[Dict[str, Any]] = (),
) -> Dict[str, int]:
    """写训练格式 JSONL，返回计数。fmt: llamafactory | chat | all。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    formats = ["llamafactory", "chat"] if fmt == "all" else [fmt]
    counts: Dict[str, int] = {"sft": 0, "dpo": 0}
    samples = list(samples)  # 物化：多格式导出需多次遍历
    for f in formats:
        target = out_path if fmt != "all" else out_path.with_name(out_path.stem + f"_{f}.jsonl")
        n = 0
        with open(target, "w", encoding="utf-8") as fh:
            for s in samples:
                converted = to_sharegpt_sample(s) if f == "llamafactory" else to_chat_sample(s)
                if converted:
                    fh.write(json.dumps(converted, ensure_ascii=False) + "\n")
                    n += 1
        counts[f] = n
    counts["sft"] = max(counts.get("llamafactory", 0), counts.get("chat", 0))
    dpo_count = 0
    if dpo_pairs:
        dpo_path = out_path.with_name(out_path.stem + "_dpo.jsonl")
        with open(dpo_path, "w", encoding="utf-8") as f:
            for pair in dpo_pairs:
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")
                dpo_count += 1
    counts["dpo"] = dpo_count
    return counts
