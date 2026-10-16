"""Current-turn review units are continuous spans of the untouched candidate.

Offsets are Python string offsets. Segmentation is deliberately conservative:
long code, formulae and unsplittable prose remain whole instead of being cut
to satisfy a length limit. This is review input, never a training-data edit.
"""
import re

from lib.domain.reasoning_fields import REASONING_FIELDS
from lib.domain.review_quote_binding import _protected_positions


_FIELDS = {"id", "field", "start", "end", "text"}
_PROSE_FIELDS = {"content", *REASONING_FIELDS}
_LIST_LINE = re.compile(r"[ \t]*(?:[-+*]|\d+[.)]|[A-Za-z][.)])[ \t]+\S")
_SENTENCE_END = re.compile(r"[。！？!?](?:[\"'”’」』）)\]]*)|(?<![.\d])\.(?=\s|$)")


def _trim_span(text, start, end, protected=()):
    while start < end and text[start].isspace() and start not in protected:
        start += 1
    while end > start and text[end - 1].isspace() and end - 1 not in protected:
        end -= 1
    return start, end


def _spans(text):
    protected = _protected_positions(text)
    # Blank lines inside an indented code block belong to that same block.
    offset, code_start = 0, None
    for line in text.splitlines(keepends=True):
        if line.strip() and line.startswith(("    ", "\t")):
            if code_start is None:
                code_start = offset
        elif line.strip() and code_start is not None:
            protected.update(range(code_start, offset))
            code_start = None
        offset += len(line)
    if code_start is not None:
        protected.update(range(code_start, len(text)))

    def safe_cut(index):
        return not (index - 1 in protected and index in protected)

    cuts, offset = {0, len(text)}, 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        if not line.strip() or _LIST_LINE.match(line):
            cuts.update(index for index in (offset, end) if safe_cut(index))
        offset = end
    ordered = sorted(cuts)
    for start, end in zip(ordered, ordered[1:]):
        chunk_start = start
        for match in _SENTENCE_END.finditer(text, start, end):
            cut = match.end()
            if cut - chunk_start >= 500 and safe_cut(cut):
                yield _trim_span(text, chunk_start, cut, protected)
                chunk_start = cut
        yield _trim_span(text, chunk_start, end, protected)


def target_units(target_fields: dict[str, str], message_index: int) -> list[dict]:
    """Segment only the supplied current fields, preserving their raw spans."""
    if (type(message_index) is not int or message_index < 0
            or not isinstance(target_fields, dict)
            or any(not isinstance(field, str) or not field.strip()
                   or not isinstance(text, str) for field, text in target_fields.items())):
        raise ValueError("invalid_review_target_fields")
    units = []
    for field, text in target_fields.items():
        spans = _spans(text) if field in _PROSE_FIELDS else [_trim_span(text, 0, len(text))]
        for start, end in spans:
            if start < end and text[start:end].strip():
                units.append({"id": f"m{message_index}u{len(units) + 1:04d}",
                              "field": field, "start": start, "end": end,
                              "text": text[start:end]})
    return units


def validate_target_units(units, target_fields: dict[str, str], message_index: int) -> list[dict]:
    """Recompute the exact list; IDs, order, fields, offsets and text must match."""
    expected = target_units(target_fields, message_index)
    if (not isinstance(units, list)
            or any(not isinstance(unit, dict) or set(unit) != _FIELDS
                   or any(not isinstance(unit[key], str) for key in ("id", "field", "text"))
                   or any(type(unit[key]) is not int for key in ("start", "end"))
                   for unit in units)
            or units != expected):
        raise ValueError("invalid_review_target_units")
    return expected
