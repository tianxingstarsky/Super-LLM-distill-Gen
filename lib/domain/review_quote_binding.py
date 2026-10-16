"""Locate candidate quotes without treating Markdown emphasis as new wording.

This deliberately supports a conservative subset of presentation markup. It
does not normalize whitespace, punctuation, escaping, code, math, or evidence.
Offsets are Python string offsets into the exact original candidate field.
"""
from copy import deepcopy
import re
import unicodedata


def _protected_positions(text):
    protected = set()
    offset, fence = 0, None
    for line in text.splitlines(keepends=True):
        marker = re.match(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if fence is not None:
            protected.update(range(offset, offset + len(line)))
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
            protected.update(range(offset, offset + len(line)))
        elif line.startswith(("    ", "\t")):
            protected.update(range(offset, offset + len(line)))
        offset += len(line)
    for match in re.finditer(r"<(code|pre)\b[^>]*>.*?</\1\s*>", text, re.I | re.S):
        protected.update(range(match.start(), match.end()))
    index = 0
    while index < len(text):
        if index in protected:
            index += 1
            continue
        if text.startswith((r"\(", r"\["), index):
            end_token = r"\)" if text[index + 1] == "(" else r"\]"
            end = text.find(end_token, index + 2)
            end = len(text) if end < 0 else end + 2
            protected.update(range(index, end))
            index = end
            continue
        if text[index] == "\\":
            protected.update(range(index, min(index + 2, len(text))))
            index += 2
            continue
        if text[index] in "`$":
            character = text[index]
            width = 1
            while index + width < len(text) and text[index + width] == character:
                width += 1
            token, search, end = character * width, index + width, -1
            while search < len(text):
                found = text.find(token, search)
                if found < 0:
                    break
                before = text[found - 1] if found else ""
                after = text[found + width:found + width + 1]
                if (found not in protected and before != "\\" and before != character and after != character
                        and (character != "$" or width == 2 or "\n" not in text[index:found])):
                    end = found + width
                    break
                search = found + width
            if end >= 0:
                protected.update(range(index, end))
                index = end
                continue
            # Unmatched backticks are left literal along with the remainder
            # of that line; ambiguity never enables a looser quote match.
            if character == "`":
                end = text.find("\n", index)
                end = len(text) if end < 0 else end
                protected.update(range(index, end))
                index = end
                continue
            index += width
            continue
        index += 1
    return protected


def _boundary(character):
    return not character or character.isspace() or unicodedata.category(character)[0] in {"P", "S"}


def _emphasis_positions(text):
    protected = _protected_positions(text)
    removed, stack = set(), []
    index = 0
    while index < len(text):
        if index in protected or text[index] not in "*_":
            index += 1
            continue
        character, width = text[index], 1
        while index + width < len(text) and text[index + width] == character:
            width += 1
        before, after = text[index - 1:index] if index else "", text[index + width:index + width + 1]
        if width > 2 or any(position in protected for position in range(index, index + width)):
            index += width
            continue
        can_open = bool(after and not after.isspace() and _boundary(before))
        can_close = bool(before and not before.isspace() and _boundary(after))
        if can_close and stack and stack[-1][1:] == (character, width):
            start, _, _ = stack.pop()
            middle = text[start + width:index]
            if middle.strip() and not re.search(r"\n\s*\n", middle):
                removed.update(range(start, start + width))
                removed.update(range(index, index + width))
        elif can_open:
            stack.append((index, character, width))
        index += width
    return removed


def bind_candidate_quote(text, quote, *, field, response_path, version):
    """Return exact source-span provenance; only paired emphasis may differ."""
    if version is not None and (type(version) is not int or version != 1):
        raise ValueError("invalid_claim_review_quote_matching_version")
    if (not isinstance(text, str) or not isinstance(quote, str) or not quote.strip()
            or not isinstance(field, str) or not field
            or not isinstance(response_path, list) or len(response_path) != 3
            or response_path[0] not in ("claim_checks", "reference_checks")
            or type(response_path[1]) is not int or response_path[1] < 0 or response_path[2] != "quote"):
        raise ValueError("fabricated_claim_review_quote")
    start = text.find(quote)
    match = "exact"
    if start >= 0:
        end = start + len(quote)
    elif version == 1:
        removed = _emphasis_positions(text)
        positions = [index for index in range(len(text)) if index not in removed]
        rendered = "".join(text[index] for index in positions)
        rendered_start = rendered.find(quote)
        if rendered_start < 0:
            raise ValueError("fabricated_claim_review_quote")
        start, end = positions[rendered_start], positions[rendered_start + len(quote) - 1] + 1
        # The complete original interval contains no deleted text except
        # parser-recognized emphasis markers. It is never a stitched quote.
        if "".join(text[index] for index in range(start, end) if index not in removed) != quote:
            raise ValueError("fabricated_claim_review_quote")
        match = "markdown_emphasis"
    else:
        raise ValueError("fabricated_claim_review_quote")
    return {"response_path": deepcopy(response_path), "field": field, "quote": quote, "match": match,
            "source_start": start, "source_end": end, "source_text": text[start:end]}
