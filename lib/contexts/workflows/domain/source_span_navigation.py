"""Layout-tolerant navigation; returned quotations are always original spans."""
import re


def layout_pattern(query):
    """Only bridge whitespace and line-end hyphenation, never paraphrase words."""
    parts = []
    index = 0
    while index < len(query):
        char = query[index]
        if char.isspace():
            while index + 1 < len(query) and query[index + 1].isspace():
                index += 1
            parts.append(r"\s+")
        elif char == "-":
            parts.append(r"-\s*")
        else:
            parts.append(re.escape(char))
            if char.isalpha() and index + 1 < len(query) and query[index + 1].isalpha():
                parts.append(r"(?:-\r?\n[ \t]*)?")
        index += 1
    return re.compile("".join(parts), re.I)


def search_original_spans(passages, query):
    pattern = layout_pattern(query)
    hits = []
    for passage in passages:
        text = passage["text"]
        for match in pattern.finditer(text):
            start, end = max(0, match.start() - 160), min(len(text), match.end() + 240)
            hits.append({"source_id": passage["id"], "start": start, "end": end,
                         "text": text[start:end], "match_start": match.start(), "match_end": match.end(),
                         "match_quote": text[match.start():match.end()],
                         "match_kind": "literal" if query.lower() in match[0].lower() else "layout_navigation"})
            if len(hits) > 8:
                return hits[:8], True
    return hits, False
