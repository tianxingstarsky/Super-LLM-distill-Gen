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
_V2_END = re.compile(
    r"[。！？!?]+[\"'”’」』）)\]]*(?:\.(?=\s|$))?"
    r"|(?<!\.)\.[\"'”’」』）)\]]*(?=\s|$)"
    r"|[;；]"
    r"|,(?=[ \t\r\n]+(?i:and|but|so|therefore|whereas)\b)"
    r"|，(?=[ \t\r\n]*(?:而|但|所以|因此|否则|却))"
)
_ABBREVIATION = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc)\.$", re.I)


def _trim_span(text, start, end, protected=()):
    while start < end and text[start].isspace() and start not in protected:
        start += 1
    while end > start and text[end - 1].isspace() and end - 1 not in protected:
        end -= 1
    return start, end


def _review_protected_positions(text):
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
    return protected


def _paragraph_ranges(text, protected):
    def safe_cut(index):
        return not (index - 1 in protected and index in protected)

    cuts, offset = {0, len(text)}, 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        if not line.strip() or _LIST_LINE.match(line):
            cuts.update(index for index in (offset, end) if safe_cut(index))
        offset = end
    ordered = sorted(cuts)
    return zip(ordered, ordered[1:])


def _spans(text):
    protected = _review_protected_positions(text)
    for start, end in _paragraph_ranges(text, protected):
        chunk_start = start
        for match in _SENTENCE_END.finditer(text, start, end):
            cut = match.end()
            if cut - chunk_start >= 500 and not (cut - 1 in protected and cut in protected):
                yield _trim_span(text, chunk_start, cut, protected)
                chunk_start = cut
        yield _trim_span(text, chunk_start, end, protected)


def _fine_spans(text):
    """Smaller punctuation spans, not claims of semantic independence."""
    protected = _review_protected_positions(text)
    for start, end in _paragraph_ranges(text, protected):
        chunk_start = start
        for match in _V2_END.finditer(text, start, end):
            if any(index in protected for index in range(match.start(), match.end())):
                continue
            if match.group().startswith("."):
                prefix = text[start:match.start() + 1]
                # Preserve list numbering, common abbreviations and initials.
                if (re.fullmatch(r"[ \t]*(?:\d+|[A-Za-z])\.", prefix)
                        or _ABBREVIATION.search(prefix)
                        or re.search(r"\b(?:[A-Za-z]\.){2,}$", prefix)):
                    continue
            yield _trim_span(text, chunk_start, match.end(), protected)
            chunk_start = match.end()
        yield _trim_span(text, chunk_start, end, protected)


def target_units(target_fields: dict[str, str], message_index: int, *, segmentation_version=None) -> list[dict]:
    """Preserve raw spans; optional v2 narrows checks with full-turn context."""
    if segmentation_version is not None and (type(segmentation_version) is not int or segmentation_version != 2):
        raise ValueError("invalid_review_target_segmentation_version")
    if (type(message_index) is not int or message_index < 0
            or not isinstance(target_fields, dict)
            or any(not isinstance(field, str) or not field.strip()
                   or not isinstance(text, str) for field, text in target_fields.items())):
        raise ValueError("invalid_review_target_fields")
    units = []
    for field, text in target_fields.items():
        segment = _fine_spans if segmentation_version == 2 else _spans
        spans = segment(text) if field in _PROSE_FIELDS else [_trim_span(text, 0, len(text))]
        for start, end in spans:
            if start < end and text[start:end].strip():
                units.append({"id": f"m{message_index}u{len(units) + 1:04d}",
                              "field": field, "start": start, "end": end,
                              "text": text[start:end]})
    return units


def validate_target_units(units, target_fields: dict[str, str], message_index: int, *, segmentation_version=None) -> list[dict]:
    """Recompute the exact list; IDs, order, fields, offsets and text must match."""
    expected = target_units(target_fields, message_index, segmentation_version=segmentation_version)
    if (not isinstance(units, list)
            or any(not isinstance(unit, dict) or set(unit) != _FIELDS
                   or any(not isinstance(unit[key], str) for key in ("id", "field", "text"))
                   or any(type(unit[key]) is not int for key in ("start", "end"))
                   for unit in units)
            or units != expected):
        raise ValueError("invalid_review_target_units")
    return expected
