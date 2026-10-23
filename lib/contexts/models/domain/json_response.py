"""Decode complete model JSON without corrupting fenced document content."""
from __future__ import annotations
import json
from typing import Any, Dict


def parse_json_robust(output: str) -> Dict[str, Any]:
    """Decode a complete value before considering an external code fence."""
    text = output.strip()
    try:
        # A document or answer can legitimately contain Markdown code fences.
        # Removing those first would corrupt an otherwise valid JSON response.
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = text.find("```")
    first_object = text.find("{")
    if fence >= 0 and (first_object < 0 or fence < first_object):
        # Only unwrap a fence outside the JSON value. Take its closing fence
        # from the end so embedded code blocks remain part of the document.
        end = text.rfind("```")
        if end > fence:
            text = text[fence + 3:end].strip()
        first_line = text.split("\n", 1)[0].strip()
        if first_line and first_line.isalpha() and "\n" in text:
            text = text.split("\n", 1)[1].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 平衡扫描取首个完整对象
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            else:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break
    raise ValueError("JSON 解析失败")
