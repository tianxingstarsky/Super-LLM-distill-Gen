"""翻译管线（M2 第一个提示词驱动新管线）：互译 + 回译校验质量门。

流程：逐行文本 → 语言检测 → 翻译（zh2en/en2zh）→ 回译 → backcheck 评分 →
      忠实度 ≥ 阈值保留（输出平行语料 + 知识桥接字段）。
依据：Bactrian-X 翻译扩展 + MADLAD-400 质量检查惯例。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from lib.llm_client import chat_json
from lib.prompts import get, render

FAITHFUL_THRESHOLD = 4  # backcheck score ≥ 4 保留


def detect_lang(text: str) -> str:
    """按 CJK 占比粗判语言：zh / en。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return "zh" if cjk / max(len(text), 1) > 0.15 else "en"


def translate_one(
    client: Any,
    text: str,
    faithful_threshold: int = FAITHFUL_THRESHOLD,
    temperature: float = 0.3,
) -> Dict[str, Any]:
    """单条互译 + 回译校验（全部走 response_format 强制 JSON）。"""
    lang = detect_lang(text)
    fwd_id = "translation.zh2en" if lang == "zh" else "translation.en2zh"
    back_id = "translation.en2zh" if lang == "zh" else "translation.zh2en"

    fwd = chat_json(client, [{"role": "user", "content": render(get(fwd_id), text=text)}], temperature=temperature)
    target = fwd["translation"]

    back = chat_json(client, [{"role": "user", "content": render(get(back_id), text=target)}], temperature=temperature)

    check = chat_json(client, [{"role": "user", "content": render(get("translation.backcheck"),
        original=text, back_translation=back["translation"])}], temperature=0.2)
    return {
        "source_lang": lang,
        "source": text,
        "target": target,
        "terms": fwd.get("terms", []),
        "backtranslation": back.get("translation", ""),
        "faithful": bool(check.get("faithful")),
        "score": check.get("score", 0),
        "issues": check.get("issues", []),
        "keep": bool(check.get("score", 0) >= faithful_threshold),
    }


def run_translation(
    client: Any,
    lines: List[str],
    limit: int = 5,
    faithful_threshold: int = FAITHFUL_THRESHOLD,
    temperature: float = 0.3,
) -> List[Dict[str, Any]]:
    pairs = []
    for line in lines[:limit]:
        line = line.strip()
        if len(line) < 8:
            continue
        try:
            pairs.append(translate_one(client, line, faithful_threshold, temperature))
        except Exception as e:  # noqa: BLE001 —— 单条失败不阻断
            pairs.append({"source": line[:60], "error": str(e)[:200], "keep": False})
    return pairs
