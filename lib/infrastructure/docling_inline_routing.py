"""Choose local layout probes without approving relabeling or mathematical joins.

Native font and spacing evidence can reject a blind whole-box formula reading.
It cannot establish prose semantics, source-code semantics, or continuation.
"""
from __future__ import annotations

import math
import unicodedata

from .docling_math_regions import (
    _box, _box_dict, _ordinary_prose_runs, extract_pdf_page_glyphs, is_math_font,
)


INLINE_ROUTING_POLICY = "native_math_layout_routing_v1"
_INLINE_LABELS = {"text", "list_item", "footnote", "caption"}
_FIXED_PITCH = 1  # PDF font descriptor flags, bit 1 (PDF 1.7 section 5.7.1).


def _value(value):
    return getattr(value, "value", value)


def _multiword_runs(glyphs):
    """Find native word sequences; no word's spelling influences the result."""
    runs = _ordinary_prose_runs(glyphs)
    by_id = {glyph["char_index"]: glyph for glyph in glyphs}
    groups, current = [], []
    for run in runs:
        adjacent = False
        if current:
            previous = current[-1]
            left = by_id[previous["char_indices"][-1]]
            right = by_id[run["char_indices"][0]]
            between = [g for g in glyphs
                       if left["char_index"] < g["char_index"] < right["char_index"]]
            size = max(left["font_size"], right["font_size"])
            adjacent = (
                bool(between) and all(g["text"].isspace() for g in between)
                and abs(left["font_size"] - right["font_size"]) <= .1 * size
                and abs(left["origin"][1] - right["origin"][1]) <= .12 * size
                and 0 <= right["bbox"]["l"] - left["bbox"]["r"] <= size
            )
        if not adjacent:
            if len(current) > 1:
                groups.append(current)
            current = []
        current.append(run)
    if len(current) > 1:
        groups.append(current)
    return [
        {"words": [run["text"] for run in group],
         "char_indices": [index for run in group for index in run["char_indices"]]}
        for group in groups
    ]


def route_inline_math_item(label, glyphs, *, code_language="unknown",
                           provenance_count=1):
    """Return a probe route, never permission to publish or reinterpret code.

    ``glyphs`` use the existing PDFium evidence schema, in source character
    order, including spaces. A multiword ordinary-font sequence is a reason to
    inspect a formula box more finely, not proof that the sequence is prose.
    Known-language and fixed-pitch code remains protected. Unknown-language
    code requires an established math font before a detector probe is useful.
    """
    label = _value(label)
    language = _value(code_language)
    result = {
        "policy": INLINE_ROUTING_POLICY, "original_label": label,
        "code_language": language, "route": "skip", "probe_required": False,
        "allow_whole_formula_ocr": False, "preserve_original_on_failure": True,
        "automatic_relabel_allowed": False,
        "automatic_text_replacement_allowed": False,
        "continuation_inferred": False, "review_required": False, "reasons": [],
        "math_font_char_indices": [], "ordinary_multiword_runs": [],
        "fixed_pitch_char_indices": [],
    }
    if label not in _INLINE_LABELS | {"formula", "code"}:
        return result
    if provenance_count != 1:
        result.update(route="review", review_required=True,
                      reasons=["single_page_provenance_required"])
        return result
    # A declared language takes precedence even when the code prints math.
    if label == "code" and language not in (None, "", "unknown"):
        result.update(route="preserve_code", reasons=["declared_code_language"])
        return result
    try:
        ids = [g["char_index"] for g in glyphs]
        if (any(type(index) is not int or index < 0 for index in ids)
                or len(set(ids)) != len(ids) or ids != sorted(ids)):
            raise ValueError()
        visible = [g for g in glyphs if g["text"].strip()]
        for glyph in visible:
            if (not isinstance(glyph["font_name"], str)
                    or type(glyph["font_flags"]) is not int
                    or glyph["font_flags"] < 0
                    or not math.isfinite(glyph["font_size"]) or glyph["font_size"] <= 0
                    or len(glyph["origin"]) != 2
                    or not all(math.isfinite(value) for value in glyph["origin"])):
                raise ValueError()
            _box(glyph["bbox"], 0)
    except (KeyError, TypeError, ValueError):
        result.update(route="review", review_required=True,
                      reasons=["invalid_native_routing_evidence"])
        return result
    font_math = [g["char_index"] for g in visible if is_math_font(g["font_name"])]
    fixed = [g["char_index"] for g in visible if g["font_flags"] & _FIXED_PITCH]
    has_math = bool(font_math) or any(
        unicodedata.category(char) == "Sm" for g in visible for char in g["text"]
    )
    result.update(math_font_char_indices=font_math, fixed_pitch_char_indices=fixed)
    if label == "code":
        if fixed:
            result.update(route="preserve_code", reasons=["fixed_pitch_code"])
        elif font_math:
            result.update(route="code_math_probe", probe_required=True,
                          review_required=True, reasons=["unknown_code_with_math_font"])
        else:
            result.update(route="preserve_code", reasons=["no_established_math_font"])
    elif label == "formula":
        groups = _multiword_runs(glyphs) if has_math else []
        result["ordinary_multiword_runs"] = groups
        if groups:
            result.update(route="mixed_formula_probe", probe_required=True,
                          review_required=True, reasons=["ordinary_multiword_text_in_formula"])
        else:
            # Preserve the existing display path, including roman named
            # operators. This is not a certificate of a pure mathematical box.
            result.update(route="display", allow_whole_formula_ocr=True)
    elif has_math:
        result.update(route="inline", probe_required=True)
    return result


def build_inline_route_map(document, source_path):
    """Read each relevant physical page once; return formula/code routing evidence.

    Ordinary text labels are left to the existing inline path. The returned
    bounding box is the original source item box, in PDF bottom-left points.
    Glyph intersection includes overhanging scripts, without altering that box.
    No original item is modified, and unresolved probes preserve its label/text.
    """
    pages, routes = {}, {}
    for item in document.texts:
        label = _value(item.label)
        if label not in {"formula", "code"}:
            continue
        provenance = item.prov
        if not provenance:
            raise ValueError("document_docling_invalid_provenance")
        language = getattr(item, "code_language", "unknown")
        if len(provenance) != 1:
            route = route_inline_math_item(
                label, [], code_language=language, provenance_count=len(provenance))
            route.update(source_ref=item.self_ref,
                         page_nos=[prov.page_no for prov in provenance],
                         source_sha256=None, bbox=None, native_char_indices=[])
            routes[item.self_ref] = route
            continue
        prov = provenance[0]
        if prov.page_no not in pages:
            pages[prov.page_no] = extract_pdf_page_glyphs(source_path, prov.page_no)
        page = pages[prov.page_no]
        box = _box(prov.bbox, page["height"])
        glyphs = []
        for glyph in page["glyphs"]:
            value = glyph["bbox"]
            if (min(value["r"], box[2]) > max(value["l"], box[0])
                    and min(value["t"], box[3]) > max(value["b"], box[1])):
                glyphs.append(glyph)
        route = route_inline_math_item(label, glyphs, code_language=language)
        if page.get("review_required"):
            route.update(route="review", probe_required=False,
                         allow_whole_formula_ocr=False, review_required=True,
                         reasons=["unsupported_pdf_coordinate_space"])
        route.update(source_ref=item.self_ref, page_no=prov.page_no,
                     source_sha256=page.get("source_sha256"), bbox=_box_dict(box),
                     coordinate_space=page.get("coordinate_space"),
                     native_char_indices=[g["char_index"] for g in glyphs])
        routes[item.self_ref] = route
    return routes
