"""Source-backed boundaries for mathematical delimiters and PDF citations.

This selects pixels, not a LaTeX answer. Linked bibliography markers remain
ordinary text. Parentheses only lose their ambiguity flag when they surround
math-font content and form a complete, nested pair within the source crop.
"""
from __future__ import annotations

import unicodedata

from .math_regions import is_math_font

INLINE_BOUNDARY_POLICY = "pdf_link_and_balanced_math_boundary_v3"


def _continuous_math_run(left, right):
    """Require actual adjacent PDF characters, not merely nearby ink boxes."""
    size = left["font_size"]
    return (
        right["char_index"] == left["char_index"] + 1
        and abs(left["font_size"] - right["font_size"]) <= .1 * size
        and abs(left["origin"][1] - right["origin"][1]) <= .12 * size
        and -.2 * size <= right["bbox"]["l"] - left["bbox"]["r"] <= .35 * size
    )


def _source_space_gap(left, right, source_by_id):
    """Account for every source character between two same-line math tokens."""
    size = left["font_size"]
    if (abs(left["font_size"] - right["font_size"]) > .1 * size
            or abs(left["origin"][1] - right["origin"][1]) > .12 * size
            or not -.2 * size <= right["bbox"]["l"] - left["bbox"]["r"] <= size):
        return None
    fields = ("text", "font_name", "font_size", "origin", "bbox")
    for glyph in (left, right):
        records = source_by_id.get(glyph["char_index"], [])
        if len(records) != 1 or any(records[0].get(key) != glyph.get(key) for key in fields):
            return None
    spaces = []
    for index in range(left["char_index"] + 1, right["char_index"]):
        records = source_by_id.get(index, [])
        if len(records) != 1:
            return None
        space = records[0]
        if (not space["text"] or any(unicodedata.category(char) != "Zs" for char in space["text"])
                or abs(space["origin"][1] - left["origin"][1]) > .12 * size
                or not left["origin"][0] <= space["origin"][0] <= right["origin"][0]):
            return None
        spaces.append(index)
    return spaces


def _enclosed_function_space_proof(ordered, position, opening, pairs, source_by_id):
    if position < 2 or not source_by_id:
        return None
    variable, operator, name = ordered[position - 2:position + 1]
    if not variable["text"].isalpha() or not is_math_font(variable["font_name"]):
        return None
    function_pair = next((pair for pair in pairs if pair[0] == opening["char_index"]), None)
    if function_pair is None:
        return None
    enclosing = [pair for pair in pairs
                 if pair[0] < variable["char_index"] and function_pair[1] < pair[1]]
    if not enclosing:
        return None
    outer = min(enclosing, key=lambda pair: pair[1] - pair[0])
    # The source must show this variable, operator and entire function call in
    # one parenthesized expression. A separate earlier "(x)" is not evidence.
    by_id = {g["char_index"]: g for g in ordered}
    if by_id[outer[0]]["text"] != "(":
        return None
    if any(left < variable["char_index"] < right < operator["char_index"]
           for left, right in pairs):
        return None
    before = _source_space_gap(variable, operator, source_by_id)
    after = _source_space_gap(operator, name, source_by_id)
    if before is None or after is None or not after:
        return None
    return {
        "method": "enclosed_math_source_whitespace",
        "outer_pair": outer, "function_pair": function_pair,
        "variable_char_index": variable["char_index"],
        "operator_char_index": operator["char_index"],
        "name_char_index": name["char_index"],
        "source_whitespace_char_indices": before + after,
    }


def mathematical_boundary_proof(glyphs, *, source_glyphs=None):
    ordered = sorted((g for g in glyphs if g["text"].strip()), key=lambda glyph: glyph["char_index"])
    source_by_id = {}
    if source_glyphs is not None:
        for glyph in source_glyphs:
            source_by_id.setdefault(glyph["char_index"], []).append(glyph)
    ordinary = [g for g in ordered if g["text"].isalpha()
                and not is_math_font(g["font_name"])]
    tokens = "".join(g["text"] for g in ordered)
    stack, pairs = [], []
    closing = {")": "(", "]": "[", "}": "{"}
    for glyph in ordered:
        for char in glyph["text"]:
            if char in closing.values():
                stack.append((char, glyph["char_index"]))
            elif char in closing:
                if not stack or stack[-1][0] != closing[char]:
                    return {"proven": False, "reason": "unbalanced_native_delimiters"}
                left, index = stack.pop()
                pairs.append([index, glyph["char_index"]])
            elif unicodedata.category(char) in ("Ps", "Pe"):
                return {"proven": False, "reason": "unsupported_native_delimiter"}
    if stack:
        return {"proven": False, "reason": "unbalanced_native_delimiters"}
    if not any(is_math_font(g["font_name"]) and g["text"].isalpha() for g in ordered):
        return {"proven": False, "reason": "no_mathematical_variable"}
    permitted, function_contexts = set(), []
    # Nearby letters and parentheses alone cannot distinguish prose such as
    # "where (x)" from a function. Require a source-contiguous operator/name/"("
    # sequence following an existing math variable on the same baseline.
    # Geometry omits spaces, but preserves PDFium character IDs; a missing ID
    # therefore blocks this exception unless the complete source independently
    # proves spacing within an enclosing parenthesized mathematical expression.
    for position, glyph in enumerate(ordered):
        if not glyph["text"].isalpha() or is_math_font(glyph["font_name"]):
            continue
        if position and ordered[position - 1]["char_index"] in permitted:
            continue
        end = position
        while (end < len(ordered) and ordered[end]["text"].isalpha()
               and not is_math_font(ordered[end]["font_name"])):
            if end > position:
                prev, current = ordered[end - 1], ordered[end]
                if not _continuous_math_run(prev, current):
                    break
            end += 1
        following = ordered[end] if end < len(ordered) else None
        preceding = ordered[position - 1] if position else None
        prior_variable = any(
            is_math_font(previous["font_name"]) and previous["text"].isalpha()
            and abs(previous["origin"][1] - glyph["origin"][1]) <= .12 * glyph["font_size"]
            for previous in ordered[:max(0, position - 1)]
        )
        if (following and following["text"] == "("
                and _continuous_math_run(ordered[end - 1], following)
                and preceding and len(preceding["text"]) == 1
                and unicodedata.category(preceding["text"]) == "Sm" and prior_variable):
            context = None
            if not _continuous_math_run(preceding, glyph):
                context = _enclosed_function_space_proof(
                    ordered, position, following, pairs, source_by_id)
            if _continuous_math_run(preceding, glyph) or context is not None:
                permitted.update(g["char_index"] for g in ordered[position:end])
                if context is not None:
                    function_contexts.append(context)
        elif (end == position + 1 and position == 0 and following
              and following["text"] == "="):
            # Bold roman single-variable assignment, e.g. z = (z_1, ..., z_n).
            permitted.add(glyph["char_index"])
    if any(g["char_index"] not in permitted for g in ordinary):
        return {"proven": False, "reason": "unclassified_ordinary_letters"}
    return {"proven": True, "policy": INLINE_BOUNDARY_POLICY,
            "source_char_indices": [g["char_index"] for g in ordered],
            "balanced_pairs": pairs, "roman_math_char_indices": sorted(permitted),
            "roman_function_contexts": function_contexts,
            "source_text": tokens}


def read_pdf_citation_links(source):
    """Use pypdf's PDF annotation objects; never follow a link or load a URL."""
    from pypdf import PdfReader
    result = {}
    reader = PdfReader(source)
    for number, page in enumerate(reader.pages, 1):
        links = []
        for reference in page.get("/Annots", []):
            item = reference.get_object()
            action = item.get("/A")
            if action is not None:
                action = action.get_object()
            if item.get("/Subtype") != "/Link" or not action or action.get("/S") != "/GoTo":
                continue
            destination = str(action.get("/D", ""))
            # Hyperref bibliography destinations explicitly distinguish these
            # links from equation numbers, arbitrary links and math intervals.
            if not destination.startswith("cite."):
                continue
            box = item.get("/Rect")
            if box is None or len(box) != 4:
                continue
            l, b, r, t = map(float, box)
            links.append({"bbox": {"l": l, "b": b, "r": r, "t": t},
                          "destination": destination})
        result[number] = links
    return result


def separate_linked_citation(candidate, page, links):
    """Exclude a trailing linked citation, preserving its source IDs as proof."""
    box = candidate["bbox"]
    chosen = [g for g in page["glyphs"] if g["text"].strip()
              and box["l"] <= (g["bbox"]["l"] + g["bbox"]["r"]) / 2 <= box["r"]
              and box["b"] <= (g["bbox"]["b"] + g["bbox"]["t"]) / 2 <= box["t"]]
    for link in links:
        region = link["bbox"]
        linked = [g for g in chosen
                  if region["l"] <= (g["bbox"]["l"] + g["bbox"]["r"]) / 2 <= region["r"]
                  and region["b"] <= (g["bbox"]["b"] + g["bbox"]["t"]) / 2 <= region["t"]]
        if not linked or not all(g["text"].isdigit() for g in linked):
            continue
        lo, hi = min(g["char_index"] for g in linked), max(g["char_index"] for g in linked)
        ordered = sorted(chosen, key=lambda g: g["char_index"])
        before = next((g for g in reversed(ordered) if g["char_index"] < lo), None)
        after = next((g for g in ordered if g["char_index"] > hi), None)
        if not before or not after or before["text"] != "[" or after["text"] != "]":
            continue
        removed = [before, *linked, after]
        ids = {g["char_index"] for g in removed}
        retained = [g for g in chosen if g["char_index"] not in ids]
        if (not retained or max(g["char_index"] for g in retained) >= before["char_index"]
                or any(is_math_font(g["font_name"]) for g in removed)):
            continue
        edge = min(g["bbox"]["l"] for g in removed)
        right = max(g["bbox"]["r"] for g in retained)
        if right >= edge:
            continue
        result = dict(candidate)
        result["bbox"] = dict(box, r=(right + edge) / 2)
        result["citation_boundary"] = {
            "policy": INLINE_BOUNDARY_POLICY, "original_bbox": box,
            "link": link, "preserved_prose_char_indices": sorted(ids),
        }
        return result
    return candidate
