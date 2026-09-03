"""语言风格强矫正管线：多轮"改写→风格判定→再改写"直至达标（去 AI 味）。

用户注入规则+示例（示例优先于抽象规则）→ stylefix.polish 改写 →
stylefix.check 判风格符合度 → 不达标继续改写（上限 rounds 轮）→
产出：矫正后样本 + 矫正前后 DPO 对（让模型长期习得风格，而非生成期压着）。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Dict, List

import yaml

from lib.llm_client import chat_json
from lib.prompts import get, render


def load_rules(path: str | pathlib.Path) -> Dict[str, Any]:
    cfg = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    return {
        "rules": cfg.get("rules", []),
        "exemplars": cfg.get("exemplars", []),
    }


def rules_text(rules: List[str]) -> str:
    return "\n".join(f"- {r}" for r in rules) or "（无）"


def exemplars_text(exemplars: List[Dict[str, str]]) -> str:
    if not exemplars:
        return "（无示例）"
    parts = []
    for i, e in enumerate(exemplars, 1):
        parts.append(f"示例 {i} 原文：{e.get('text', '')}\n示例 {i} 矫正：{e.get('corrected', '')}")
    return "\n\n".join(parts)


def polish_round(client: Any, text: str, cfg: Dict[str, Any]) -> str:
    out = chat_json(client, [{"role": "user", "content": render(
        get("stylefix.polish"),
        text=text,
        rules=rules_text(cfg["rules"]),
        exemplars=exemplars_text(cfg["exemplars"]),
    )}], temperature=0.7)
    return str(out.get("corrected", "")).strip()


def check_style(client: Any, text: str, cfg: Dict[str, Any]) -> int:
    out = chat_json(client, [{"role": "user", "content": render(
        get("stylefix.check"), text=text, rules=rules_text(cfg["rules"]))}], temperature=0.2)
    try:
        return int(out.get("adherence", 1))
    except (TypeError, ValueError):
        return 1


def _assistant_texts(sample: Dict[str, Any]) -> List[Dict[str, Any]]:
    """样本中所有 assistant 文本（含 reasoning_content 与 content 合并的完整文本）。"""
    texts = []
    for m in sample.get("messages", []):
        if m.get("role") != "assistant":
            continue
        parts = [m.get("reasoning_content", ""), m.get("content", "")]
        full = "\n".join(p for p in parts if p).strip()
        if full:
            texts.append({"msg": m, "full": full})
    return texts


def run(
    client: Any,
    samples: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    rounds: int = 3,
    threshold: int = 4,
    limit: int = 20,
) -> Dict[str, Any]:
    """多轮矫正：返回矫正后样本 + 矫正前后 DPO 对 + 统计。"""
    out_samples: List[Dict[str, Any]] = []
    dpo_pairs: List[Dict[str, Any]] = []
    stats = {"samples": 0, "texts": 0, "improved": 0, "total_rounds": 0, "dpo_pairs": 0}

    for sample in samples[:limit]:
        stats["samples"] += 1
        new_texts: List[Dict[str, Any]] = []
        dpo_before = len(dpo_pairs)
        for item in _assistant_texts(sample):
            stats["texts"] += 1
            current = item["full"]
            if check_style(client, current, cfg) >= threshold:
                new_texts.append(item["msg"])
                continue
            best, best_score = current, 0
            for _ in range(rounds):
                stats["total_rounds"] += 1
                current = polish_round(client, current, cfg)
                if not current:
                    break
                score = check_style(client, current, cfg)
                if score > best_score:
                    best, best_score = current, score
                if score >= threshold:
                    break
            if best_score > 0 and best != item["full"]:
                stats["improved"] += 1
                msg = dict(item["msg"])
                msg["content"] = best  # 矫正文本放回 content
                new_texts.append(msg)
                dpo_pairs.append({
                    "id": "style-" + hashlib.sha256(best.encode("utf-8")).hexdigest()[:16],
                    "source": "stylefix",
                    "prompt": [m for m in sample["messages"] if m.get("role") == "user"],
                    "chosen": [{"role": "assistant", "content": best}],
                    "rejected": [{"role": "assistant", "content": item["full"]}],
                })
            else:
                new_texts.append(item["msg"])
        if len(dpo_pairs) > dpo_before:
            # 顺序替换样本中的 assistant 文本消息
            idx = 0
            rebuilt = []
            for m in sample["messages"]:
                if m.get("role") == "assistant" and (m.get("content") or m.get("reasoning_content")):
                    rebuilt.append(new_texts[idx])
                    idx += 1
                else:
                    rebuilt.append(m)
            out = dict(sample)
            out["messages"] = rebuilt
            out["source"] = str(sample.get("source", "")) + ":stylefix"
            out_samples.append(out)
    stats["dpo_pairs"] = len(dpo_pairs)
    return {"samples": out_samples, "dpo_pairs": dpo_pairs, "stats": stats}
