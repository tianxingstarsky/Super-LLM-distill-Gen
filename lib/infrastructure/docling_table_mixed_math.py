"""Local math inside a table cell with independently bounded native prose.

Docling Parse physical lines propose crops. PDFium glyphs and the existing
raster checks prove ownership; Pix2Text sees only the resulting pixels. Native
characters retain the exact prose and establish an atomic cell-text patch.
No table grid or source document field is reconstructed by this module.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
import unicodedata

from .docling_inline_assembly import assemble_inline_formulas, native_paragraph_chars
from .docling_inline_geometry import refine_inline_formula_geometry
from .docling_math_consistency import character_inventory, validate_math_characters
from .docling_math_regions import _box, _box_dict, inspect_math_region, is_math_font


TABLE_MIXED_MATH_POLICY = "docling_native_line_table_math_leaf_attachments_v3"


def _ordered_symbols(text):
    return "".join(char for char in unicodedata.normalize("NFKC", text) if not char.isspace())


def _source_symbol_order(glyphs, native_chars):
    """Require independent native reading order and strict left-to-right geometry.

    Inventories cannot establish operator order. This narrow independent-line
    route accepts an ordered witness only when both native parsers agree and
    every visible glyph center progresses to the right. Overlapping two-
    dimensional layouts whose order is ambiguous remain review cases.
    """
    result = {"review_required": True, "reasons": [], "native_symbols": None,
              "pdfium_symbols": None, "glyph_center_x": [], "order_inferred": False}
    if not native_chars or not glyphs:
        result["reasons"].append("table_math_native_symbol_order_missing")
        return result
    native_ids = [c["index"] for c in native_chars]
    glyph_ids = [g["char_index"] for g in glyphs]
    if (len(set(native_ids)) != len(native_ids) or len(set(glyph_ids)) != len(glyph_ids)
            or native_ids != sorted(native_ids) or glyph_ids != sorted(glyph_ids)):
        result["reasons"].append("table_math_native_symbol_order_ambiguous")
    native = _ordered_symbols("".join(c["text"] for c in native_chars))
    pdfium = _ordered_symbols("".join(g["text"] for g in glyphs))
    positions, cursor = {}, 0
    for glyph in glyphs:
        symbols = _ordered_symbols(glyph["text"])
        positions[glyph["char_index"]] = list(range(cursor, cursor+len(symbols)))
        cursor += len(symbols)
    result.update(native_symbols=native, pdfium_symbols=pdfium, glyph_symbol_positions=positions)
    if native != pdfium:
        result["reasons"].append("table_math_native_symbol_order_disagrees")
    centers = [(g["bbox"]["l"] + g["bbox"]["r"]) / 2 for g in glyphs if g["text"].strip()]
    result["glyph_center_x"] = centers
    if (not all(math.isfinite(value) for value in centers)
            or any(right <= left for left, right in zip(centers, centers[1:]))):
        result["reasons"].append("table_math_spatial_symbol_order_ambiguous")
    result["review_required"] = bool(result["reasons"])
    return result


def _source_script_attachments(glyphs, order):
    """Prove numeric scripts against specific source glyphs before any OCR.

    The supported route is one left-to-right physical line. Smaller shifted
    glyphs must have exactly one preceding base at the local script distance.
    Non-numeric scripts and ambiguous attachments stay unpublished.
    """
    result = {"review_required": True, "reasons": [], "attachments": [],
              "numeric_only": True, "inferred_missing_characters": False}
    if order["review_required"]:
        result["reasons"].append("table_math_source_symbol_order_requires_review")
        return result
    visible = [g for g in glyphs if g["text"].strip()]
    if not visible:
        result["reasons"].append("table_math_source_script_geometry_missing")
        return result
    for glyph in visible:
        size, origin = glyph.get("font_size"), glyph.get("origin")
        if (type(size) not in (int, float) or not math.isfinite(size) or size <= 0
                or not isinstance(origin, (list, tuple)) or len(origin) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in origin)):
            result["reasons"].append("table_math_source_script_geometry_missing")
            return result
    largest = max(g["font_size"] for g in visible)
    main = [g for g in visible if g["font_size"] >= .88 * largest]
    main_baselines = [g["origin"][1] for g in main]
    if max(main_baselines)-min(main_baselines) > .12 * largest:
        result["reasons"].append("table_math_source_baseline_ambiguous")
        return result
    for index, small in enumerate(visible):
        if small["font_size"] >= .88 * largest:
            continue
        # Same-baseline small capitals/digits are not source script evidence.
        if any(abs(small["origin"][1]-y) <= .12 * largest for y in main_baselines):
            continue
        if not _ordered_symbols(small["text"]).isnumeric():
            result["reasons"].append("table_math_non_numeric_script_requires_review")
            continue
        bases = []
        for base in visible[:index]:
            size = base["font_size"]
            if (small["font_size"] >= .88 * size
                    or not (base["text"].isalnum() or is_math_font(base.get("font_name", "")))):
                continue
            offset = small["origin"][1]-base["origin"][1]
            gap = small["bbox"]["l"]-base["bbox"]["r"]
            if -.25 * size <= gap <= .55 * size and .12 * size < abs(offset) < .95 * size:
                bases.append((base, offset))
        if len(bases) != 1:
            result["reasons"].append("table_math_source_script_attachment_ambiguous")
            continue
        base, offset = bases[0]
        positions = order["glyph_symbol_positions"]
        base_positions, script_positions = positions[base["char_index"]], positions[small["char_index"]]
        if len(base_positions) != 1 or len(script_positions) != 1:
            result["reasons"].append("table_math_source_script_symbol_binding_ambiguous")
            continue
        result["attachments"].append({
            "base_char_index": base["char_index"], "script_char_index": small["char_index"],
            "base_symbol_index": base_positions[0], "script_symbol_index": script_positions[0],
            "base_text": base["text"], "script_text": small["text"], "baseline_offset": offset,
            "relation": "superscript" if offset > 0 else "subscript"})
    result["reasons"] = list(dict.fromkeys(result["reasons"]))
    result["review_required"] = bool(result["reasons"])
    return result


def _intersect(first, second):
    box = (max(first[0], second[0]), max(first[1], second[1]),
           min(first[2], second[2]), min(first[3], second[3]))
    return box if box[0] < box[2] and box[1] < box[3] else None


def plan_mixed_table_math(page, cell, native_lines, native_chars, image):
    """Plan a single independent math line without reading or inventing LaTeX.

    Same-line mixed prose and multiple mathematical rows remain review cases.
    A physical-line proposal alone never authorizes a replacement.
    """
    result = {"policy": TABLE_MIXED_MATH_POLICY, "status": "review_required",
              "review_required": True, "backend_text": cell["text"], "published": False,
              "source_sha256": page.get("source_sha256"), "page_no": page.get("page_no"),
              "line_candidates": [], "reason": None}

    def fail(reason):
        result["reason"] = reason
        return result

    if page.get("review_required") or page.get("status") != "ok" or image is None:
        return fail("unsupported_table_math_source_page")
    outer = _box(cell["bbox"], page["height"])
    bbox = _box_dict(outer)
    native = native_paragraph_chars(native_chars, bbox)
    visible = [g for g in page["glyphs"] if g["text"].strip()
               and outer[0] <= (g["bbox"]["l"] + g["bbox"]["r"]) / 2 <= outer[2]
               and outer[1] <= (g["bbox"]["b"] + g["bbox"]["t"]) / 2 <= outer[3]]
    if (not page.get("source_sha256") or not native or not visible
            or len({c["index"] for c in native}) != len(native)
            or len({g["char_index"] for g in visible}) != len(visible)):
        return fail("missing_or_ambiguous_table_native_characters")
    native_text = "".join(c["text"] for c in native)
    glyph_text = "".join(g["text"] for g in visible)
    if not (character_inventory(cell["text"]) == character_inventory(native_text)
            == character_inventory(glyph_text)):
        return fail("table_native_character_inventory_mismatch")
    result.update(native_chars=deepcopy(native), source_cell_bbox=deepcopy(cell["bbox"]),
                  pdfium_char_indices=[g["char_index"] for g in visible])
    selected, candidates = set(), []
    for line in native_lines:
        intersect = _intersect(_box(line["bbox"], page["height"]), outer)
        if intersect is None:
            continue
        record = {"source_line": deepcopy(line), "clipped_line_bbox": _box_dict(intersect)}
        result["line_candidates"].append(record)
        if (line.get("review_required") or line.get("from_ocr")
                or line.get("text_direction", "left_to_right") != "left_to_right"
                or line.get("page_no") != page["page_no"]
                or line.get("source_sha256") != page["source_sha256"]):
            return fail("unsupported_table_native_line_provenance")
        selection = inspect_math_region(page, record["clipped_line_bbox"])
        record["selection"] = selection
        selected.update(selection["char_indices"])
        if selection.get("review_required"):
            return fail("table_math_and_prose_share_native_line")
        if not selection["contains_math"]:
            continue
        geometry = refine_inline_formula_geometry(
            page, record["clipped_line_bbox"], bbox, page_image=image,
            allow_math_delimiters=True)
        record["geometry"] = geometry
        if geometry["review_required"]:
            return fail("table_math_line_crop_requires_review")
        chosen = set(geometry["char_indices"])
        glyphs = [g for g in visible if g["char_index"] in chosen]
        native_candidates = native_paragraph_chars(native, geometry["bbox"])
        if (not glyphs or not native_candidates
                or character_inventory("".join(c["text"] for c in native_candidates))
                != character_inventory("".join(g["text"] for g in glyphs))):
            return fail("table_math_native_boundary_mismatch")
        candidates.append(dict(record, glyphs=glyphs, native_chars=native_candidates))
    if selected != {g["char_index"] for g in visible}:
        return fail("table_native_line_coverage_incomplete")
    if len(candidates) != 1:
        return fail("table_requires_one_independent_math_line")
    candidate = candidates[0]
    order = _source_symbol_order(candidate["glyphs"], candidate["native_chars"])
    result["source_symbol_order"] = order
    if order["review_required"]:
        return fail("table_math_source_symbol_order_requires_review")
    scripts = _source_script_attachments(candidate["glyphs"], order)
    result["source_script_attachments"] = scripts
    if scripts["review_required"]:
        return fail("table_math_source_script_attachments_require_review")
    math_ids = set(candidate["geometry"]["char_indices"])
    prose = [g for g in visible if g["char_index"] not in math_ids]
    if not prose:
        return fail("table_mixed_cell_has_no_native_prose")
    # Require a physically separate line. The source glyph boxes, including
    # descenders and superscripts, must leave a blank band across this cell.
    math_box = _box(candidate["geometry"]["bbox"], page["height"])
    above = [g for g in prose if g["bbox"]["b"] > math_box[3]]
    below = [g for g in prose if g["bbox"]["t"] < math_box[1]]
    if len(above) + len(below) != len(prose):
        return fail("table_prose_is_not_on_an_independent_line")
    gaps = []
    if above:
        gaps.append((outer[0], math_box[3], outer[2], min(g["bbox"]["b"] for g in above)))
    if below:
        gaps.append((outer[0], max(g["bbox"]["t"] for g in below), outer[2], math_box[1]))
    blank = []
    sx, sy = image.width / page["width"], image.height / page["height"]
    for gap in gaps:
        # Only complete raster pixels within the gap count as blank evidence.
        pixels = (math.ceil(gap[0] * sx), math.ceil((page["height"] - gap[3]) * sy),
                  math.floor(gap[2] * sx), math.floor((page["height"] - gap[1]) * sy))
        if pixels[2] <= pixels[0] or pixels[3] <= pixels[1]:
            return fail("table_math_prose_blank_band_too_small")
        crop = image.crop(pixels).convert("L")
        dark = sum(value <= 128 for value in crop.getdata())
        crop.close()
        blank.append({"bbox": _box_dict(gap), "pixel_box": list(pixels),
                      "dark_pixel_threshold": 128, "dark_pixels": dark})
        if dark:
            return fail("table_math_prose_separator_contains_ink")
    result.update(status="planned", review_required=False, candidate=candidate,
                  blank_separators=blank, native_prose_char_indices=[g["char_index"] for g in prose])
    return result


def validate_table_math_structure(glyphs, latex, selection, *, native_chars=None):
    """Reject changed source symbols and numeric script attachment via LaTeX AST.

    This is an extraction consistency check, not mathematical correctness.
    The shared AST parser is backed by mature pylatexenc, with no regex repair.
    """
    from .docling_inline_continuation import _canonical, _node_tokens, _parse_nodes
    order = _source_symbol_order(glyphs, native_chars)
    result = {"review_required": True, "reasons": list(order["reasons"]), "latex_modified": False,
              "source_symbol_order": order,
              "mathematical_correctness_certified": False}
    try:
        ast = _canonical(_node_tokens(_parse_nodes(latex)))
    except (ValueError, TypeError, KeyError):
        result["reasons"].append("unsupported_table_math_latex_structure")
        return result
    source_scripts = _source_script_attachments(glyphs, order)
    result["source_script_attachments"] = source_scripts
    result["reasons"].extend(source_scripts["reasons"])
    leaves, edges = [], []

    def visit(node):
        if not isinstance(node, tuple):
            return []
        if len(node) == 2 and node[0] == "symbol":
            position = len(leaves)
            leaves.append(node[1])
            return [position]
        elif len(node) == 4 and node[0] == "script":
            _, base, sub, sup = node
            base_positions = visit(base)
            positions = list(base_positions)
            for kind, value in (("subscript", sub), ("superscript", sup)):
                if value:
                    script_positions = []
                    for child in value:
                        script_positions.extend(visit(child))
                    positions.extend(script_positions)
                    if len(base_positions) != 1:
                        result["reasons"].append("unsupported_table_math_script_base")
                    else:
                        edges.extend((base_positions[0], position, kind) for position in script_positions)
            return positions
        else:
            if node and isinstance(node[0], str) and node[0] != "symbol_style":
                # Fraction/radical/group-base order needs a separate 2-D source
                # proof. A flattened inventory cannot authorize that structure.
                result["reasons"].append("unsupported_table_math_ordered_structure")
            positions = []
            for child in node[1:] if node and isinstance(node[0], str) else node:
                if isinstance(child, tuple):
                    positions.extend(visit(child))
            return positions

    visit(ast)
    if character_inventory("".join(g["text"] for g in glyphs)) != character_inventory("".join(leaves)):
        result["reasons"].append("table_math_source_symbols_changed")
    recognized_order = _ordered_symbols("".join(leaves))
    result["recognized_symbol_order"] = recognized_order
    if not order["review_required"] and recognized_order != order["native_symbols"]:
        result["reasons"].append("table_math_source_symbol_order_changed")
    source_edges = [(e["base_symbol_index"], e["script_symbol_index"], e["relation"])
                    for e in source_scripts["attachments"]]
    if Counter(edges) != Counter(source_edges):
        result["reasons"].append("table_math_numeric_script_attachment_changed")
    result.update(source_numeric_script_edges=source_edges, recognized_numeric_script_edges=edges,
                  attachment_binding="concrete_ordered_symbol_positions",
                  review_required=bool(result["reasons"]))
    return result


def transcribe_mixed_table_math(pdf, page, cell, native_lines, native_chars, page_image, reader):
    """Return an atomic proposal and full evidence; leave the caller's cell alone."""
    from .docling_formula_crops import render_formula_crop
    from .docling_math_style import validate_math_style
    try:
        result = plan_mixed_table_math(page, cell, native_lines, native_chars, page_image)
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        return {"policy": TABLE_MIXED_MATH_POLICY, "status": "review_required", "review_required": True,
                "reason": "table_mixed_math_source_evidence_failed", "published": False,
                "source_sha256": page.get("source_sha256"), "page_no": page.get("page_no"),
                "backend_text": cell.get("text"), "error_type": type(error).__name__}
    if result["review_required"]:
        return result
    candidate = result["candidate"]
    bbox = candidate["geometry"]["bbox"]
    try:
        image, crop = render_formula_crop(pdf, page["page_no"], bbox, dpi=480,
                                          padding_points=0, clip_bbox=bbox)
    except (ValueError, RuntimeError, OSError) as error:
        result.update(status="review_required", review_required=True,
                      reason="table_mixed_math_crop_failed", error_type=type(error).__name__)
        return result
    try:
        prediction = reader.read(image)
    finally:
        image.close()
    result.update(crop=crop, prediction=deepcopy(prediction), status="review_required", review_required=True)
    if prediction["status"] != "transcribed_unverified":
        result["reason"] = prediction.get("reason", "table_formula_prediction_requires_review")
        return result
    glyph_text = "".join(g["text"] for g in candidate["glyphs"])
    native_text = "".join(c["text"] for c in candidate["native_chars"])
    consistency = validate_math_characters(native_text, glyph_text, prediction["transcribed_text"])
    selection = inspect_math_region(page, bbox)
    structure = validate_table_math_structure(candidate["glyphs"], prediction["transcribed_text"], selection,
                                               native_chars=candidate["native_chars"])
    style = validate_math_style(candidate["glyphs"], prediction["transcribed_text"])
    result.update(character_consistency=consistency, structure_consistency=structure, style_consistency=style)
    if any(e["review_required"] for e in (consistency, structure, style)):
        result["reason"] = "table_formula_consistency_requires_review"
        return result
    fragment = dict(prediction, fragment_id="independent-native-table-math-line", review_required=False,
                    native_char_indices=[c["index"] for c in candidate["native_chars"]])
    assembly = assemble_inline_formulas(cell["text"], result["native_chars"], [fragment])
    result["assembly"] = assembly
    if assembly["status"] != "transcribed_unverified":
        result["reason"] = "table_formula_atomic_assembly_requires_review"
        return result
    result.update(status="transcribed_unverified", review_required=False, reason=None,
                  text=assembly["text"], published=False)
    return result
