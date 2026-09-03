"""蒸馏质检：样本分类（运行时事实优先，零 LLM 成本）+ DPO 负样本对提取。

正确性信号优先级（与文本化三角色提示词一致）：
  ① 运行时事实（isError/exit_code/success）② 用户信号（纠正/重试）③ LLM 语义判断兜底。
本模块只做 ① 与结构提取（免费、确定、可离线测试）；LLM 兜底在 df distill --llm-check。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from lib.adapters.rollout_import import (
    _content_to_str,
    assistant_msg_from_response,
    iter_records,
    record_status,
)


def classify_sample(sample: Dict[str, Any]) -> Dict[str, Any]:
    """基于已落盘的运行时事实给样本打标签（零 LLM 调用）。"""
    messages = sample.get("messages") or []
    last = messages[-1] if messages else {}
    tool_errs = int(sample.get("error_tool_steps", 0))
    has_toolcalls = bool(last.get("toolCalls"))
    return {
        "id": sample.get("id", ""),
        "tag": "recovery" if tool_errs > 0 else "clean",
        "finish": "tool-calls" if has_toolcalls else "answer",
        "error_tool_steps": tool_errs,
        "n_messages": len(messages),
        "has_reasoning": bool(last.get("reasoning_content")),
    }


def classify_report(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    tags = {}
    for s in samples:
        c = classify_sample(s)
        key = (c["tag"], c["finish"])
        tags[key] = tags.get(key, 0) + 1
    return {"counts": {f"{t}/{f}": n for (t, f), n in sorted(tags.items())}}


def _norm_assistant(m: Dict[str, Any]) -> Dict[str, Any]:
    item: Dict[str, Any] = {"role": "assistant", "content": _content_to_str(m.get("content"))}
    if m.get("toolCalls"):
        item["toolCalls"] = [
            {"id": tc.get("id"), "name": tc.get("name"), "input": tc.get("input")}
            for tc in m["toolCalls"]
            if isinstance(tc, dict)
        ]
    return item


def extract_dpo_pairs(records_path: str, cot_style: str = "separated") -> List[Dict[str, Any]]:
    """从原始 rollout 记录提取 DPO 负样本对（免费、确定性）。

    构造（LLaVA-DPO"错误 vs 修正"思路，真实数据版）：
      prompt   = 出错工具调用之前的全部历史
      rejected = 发出失败工具调用的 assistant 回合（错误操作）
      chosen   = 该记录最终的 assistant 回合（纠正后的正确操作）
    触发条件：历史中存在 isError=true 的工具结果（运行时事实）。
    """
    pairs: List[Dict[str, Any]] = []
    seen = set()

    def _norm_msgs(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for m in msgs:
            role = m.get("role")
            if role == "user":
                out.append({"role": "user", "content": _content_to_str(m.get("content"))})
            elif role == "assistant":
                out.append(_norm_assistant(m))
            elif role == "tool":
                out.append({"role": "tool", "content": _content_to_str(m.get("content")), "toolCallId": m.get("toolCallId", "")})
        return out

    for rec in iter_records(records_path):
        if record_status(rec) != "ok":
            continue
        raw_msgs = rec.get("request", {}).get("messages", [])
        chosen = assistant_msg_from_response(rec, cot_style)
        if not chosen:
            continue

        for i, m in enumerate(raw_msgs):
            if m.get("role") != "tool" or not m.get("isError"):
                continue
            call_id = m.get("toolCallId")
            bad_idx = -1
            for j in range(i - 1, -1, -1):
                if raw_msgs[j].get("role") == "assistant":
                    tcs = raw_msgs[j].get("toolCalls") or []
                    if any(tc.get("id") == call_id for tc in tcs if isinstance(tc, dict)):
                        bad_idx = j
                        break
            if bad_idx < 0:
                continue
            prompt = _norm_msgs(raw_msgs[:bad_idx])
            rejected = [_norm_assistant(raw_msgs[bad_idx])]
            pair = {"prompt": prompt, "chosen": [chosen], "rejected": rejected}
            digest = json.dumps(pair, ensure_ascii=False, sort_keys=True)
            key = hash(digest)
            if key not in seen:
                seen.add(key)
                pairs.append(pair)
    return pairs
