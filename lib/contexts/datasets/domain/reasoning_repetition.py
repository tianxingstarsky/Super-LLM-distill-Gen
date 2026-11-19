"""Locate exact repetition in assistant targets; observations keep source character offsets."""

from collections import Counter
import re


def repeated_reasoning_lines(text):
    """The frozen 4.6 truncated-draft measurement, including nonadjacent lines."""
    minimum = 24
    counts = Counter(s.strip() for s in text.splitlines() if len(s.strip()) >= minimum)
    if not counts:
        return None
    line, occurrences = counts.most_common(1)[0]
    repeated = sum(len(s) * (n - 1) for s, n in counts.items() if n >= 8)
    if occurrences >= 8 and repeated >= len(text) // 4:
        start = text.index(line)
        return {"line_span": [start, start + len(line)], "count": occurrences,
                "repeated_line_characters": repeated, "minimum_line_characters": minimum}
    return None


_SENTENCE = re.compile(r".+?(?:[。！？]+|[!?]+(?=\s|$)|\.+(?=\s|$)|$)")
_STEP_NUMBER = re.compile(r"^((?:step\s+|步骤\s*)\d+)(?=[:：.、 ]|$)", re.I)
_REPEAT_REQUEST = re.compile(
    r"\b(?:repeat|write|print|output|say|list|copy|return)\b[^\n.!?]{0,120}\b(?:times|verbatim|repeatedly)\b"
    r"|\b(?:lyrics|refrain|chorus|transcribe|transcription)\b"
    r"|(?:重复|复述|反复|抄写)[^\n。！？]{0,45}(?:次|遍|行)|歌词|副歌|逐字(?:抄写|转录|复述)",
    re.I,
)


def target_repetition(text):
    """Hold a sustained recurring sequence of prose. Reusing a fact across changing arguments is fine."""
    if len(text) < 64:
        return None
    units = []
    offset = 0
    fence = None
    for line in text.splitlines(keepends=True):
        plain = line.lstrip()
        mark = re.match(r"^(\x60{3,}|~{3,})", plain)
        is_code = bool(fence or mark)
        if mark:
            if not fence:
                fence = mark[1]
            elif mark[1].startswith(fence):
                fence = None
        if is_code or plain.startswith((">", "|", "$$", "\\[")) or line.startswith(("    ", "\t")):
            units.append((None, offset, offset + len(line)))
        else:
            for m in _SENTENCE.finditer(line):
                unit = m[0].strip()
                if not unit:
                    continue
                start = offset + m.start() + len(m[0]) - len(m[0].lstrip())
                prose = bool(re.search(r"[A-Za-z]{2,}|[\u3400-\u9fff]{2}", unit))
                if "=" in unit and len(re.findall(r"[A-Za-z]{3,}|[\u3400-\u9fff]{2,}", unit)) < 3:
                    prose = False
                # Repeated numbering is presentation. Keep every quantity inside the argument.
                normalized = _STEP_NUMBER.sub("Step", unit) if prose else None
                units.append((normalized, start, start + len(unit)))
        offset += len(line)
    # Consecutive matches at lag p indicate repetition of the same p propositions.
    # Stop at the first evidenced run. Larger periods are examined only when the
    # broad frozen detector has already found substantial repeated full lines.
    max_period = 64 if repeated_reasoning_lines(text) else 8
    for period in range(1, min(max_period, len(units) // 8) + 1):
        matches = 0
        for i in range(period, len(units)):
            unit, _, end = units[i]
            matches = matches + 1 if unit and unit == units[i - period][0] else 0
            if matches < 7 * period:
                continue
            first = i - matches - period + 1
            start = units[first][1]
            if end - start >= max(64, len(text) // 4):
                return {"kind": "repeated_prose_cycle", "span": [start, end], "period": period,
                        "complete_repetitions": (matches + period) // period}
    return None


def assistant_target_repetition(messages):
    """Check only the target turn. Explicitly requested visible repetition remains usable."""
    target = messages[-1]
    evidence = target_repetition(target.get("thinking", ""))
    if evidence:
        return {"field": "thinking", **evidence}
    text = target.get("content")
    if not isinstance(text, str):
        return None
    evidence = target_repetition(text)
    if not evidence:
        return None
    current_user = next((m for m in reversed(messages[:-1]) if m["role"] == "user"), {})
    instructions = [current_user, *(m for m in messages[:-1] if m["role"] == "system")]
    if any(isinstance(m.get("content"), str) and _REPEAT_REQUEST.search(m["content"]) for m in instructions):
        return None
    return {"field": "content", **evidence}
