"""Quote evidence with exact source offsets, including PDF whitespace artifacts."""
from __future__ import annotations


def quote_spans(source: str, quotes: list[str], *, pdf_whitespace: bool = False) -> list[dict] | None:
    """Match within one source unit; preserve every non-whitespace character.

    PDF extraction can insert line breaks or omit spaces inside a word. Only
    those whitespace differences are tolerated, and only when the caller has
    established PDF provenance. Offsets always point into the original source;
    model quotes remain separate from the source substrings.
    """
    if (not isinstance(source, str) or not isinstance(quotes, list) or not quotes
            or any(not isinstance(quote, str) or not quote.strip() for quote in quotes)):
        return None
    compact, positions = None, None
    spans = []
    for index, quote in enumerate(quotes):
        start = source.find(quote)
        if start >= 0:
            end, match = start + len(quote), "exact"
        elif pdf_whitespace:
            if compact is None:
                positions = [offset for offset, character in enumerate(source) if not character.isspace()]
                compact = "".join(source[offset] for offset in positions)
            needle = "".join(character for character in quote if not character.isspace())
            found = compact.find(needle)
            if found < 0:
                return None
            start, end = positions[found], positions[found + len(needle) - 1] + 1
            match = "pdf_whitespace"
        else:
            return None
        spans.append({"quote_index": index, "start": start, "end": end,
                      "offset_unit": "unicode_character", "source_quote": source[start:end], "match": match})
    return spans
