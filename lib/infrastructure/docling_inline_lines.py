"""Physical detector views from official native lines, never equation grouping.

Docling Parse's ``textline_cells`` carry line rectangles, not character
membership or baselines. PDFium glyphs close those rectangles around visible
scripts. Overlapping or tightly stacked mathematical rows share one detector
view rather than cutting a possible fraction. No strings are joined, and a
view does not certify formula completeness or authorize source replacement.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import statistics
import unicodedata

from .docling_math_regions import is_math_font


INLINE_LINE_POLICY = "docling_native_physical_lines_glyph_closed_v1"
_PARSER_BOX_TOLERANCE = .25


def _bbox(value):
    if not isinstance(value, dict) or value.get("coord_origin", "BOTTOMLEFT") != "BOTTOMLEFT":
        raise ValueError("inline_line_coordinate_space")
    result = tuple(float(value[key]) for key in ("l", "b", "r", "t"))
    if not all(math.isfinite(v) for v in result) or result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError("inline_line_invalid_geometry")
    return result


def _box_dict(box):
    return dict(zip(("l", "b", "r", "t"), box), coord_origin="BOTTOMLEFT")


def _intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _contains(a, b, tolerance=0):
    return (a[0] - tolerance <= b[0] and a[1] - tolerance <= b[1]
            and a[2] + tolerance >= b[2] and a[3] + tolerance >= b[3])


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _math(glyph):
    return is_math_font(glyph.get("font_name", "")) or any(
        unicodedata.category(c) == "Sm" for c in glyph["text"])


def read_native_line_pages(source):
    """Read official line cells without OCR, models, downloads or text rewriting.

    Returns one-based page numbers mapped to line records. Representative native
    line font names are deliberately omitted: mixed-font lines may report just
    one font, which is not evidence that the entire line is mathematics.
    """
    from io import BytesIO
    from docling_parse.pdf_parser import (
        ContentConfig, ContentLevel, DoclingThreadedPdfParser, ThreadedPdfParserConfig,
    )

    content = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    config = ContentConfig(
        char_cells_content_level=ContentLevel.SKIP,
        word_cells_content_level=ContentLevel.SKIP,
        line_cells_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
        shapes_content_level=ContentLevel.SKIP,
        bitmaps_content_level=ContentLevel.SKIP,
    )
    parser = DoclingThreadedPdfParser(parser_config=ThreadedPdfParserConfig(
        loglevel="fatal", threads=1, page_content_config=config, render_config=None))
    key = parser.load(BytesIO(content))
    pages = {}
    try:
        for response in parser.iterate_results():
            page = response.get_page()
            dimension = page.dimension
            crop = dimension.crop_bbox.to_bottom_left_origin(dimension.height)
            unsupported = (abs(dimension.angle) > .01 or abs(crop.l) > .01 or abs(crop.b) > .01)
            records = []
            for cell in page.textline_cells:
                if cell.rendering_mode in (3, 7) or not cell.text.strip():
                    continue
                records.append({
                    "policy": INLINE_LINE_POLICY, "page_no": response.page_number,
                    "line_index": cell.index, "line_id": f"page-{response.page_number}:line-{cell.index}",
                    "bbox": cell.rect.to_bounding_box().to_bottom_left_origin(
                        dimension.height).model_dump(mode="json"),
                    "page_width": dimension.width, "page_height": dimension.height,
                    "source_sha256": digest, "native_text": cell.text,
                    "text_direction": getattr(cell.text_direction, "value", cell.text_direction),
                    "from_ocr": cell.from_ocr, "rendering_mode": cell.rendering_mode,
                    "review_required": bool(unsupported or cell.rect.angle_360 != 0),
                    "reason": "unsupported_native_line_coordinate_space" if unsupported or cell.rect.angle_360 != 0 else None,
                })
            pages[response.page_number] = records
    finally:
        while parser.has_tasks():
            parser.get_task()
        parser.unload(key)
    return pages


def select_inline_line_views(lines, paragraph_bbox, glyphs):
    """Return auditable physical detector views inside an existing paragraph.

    All inputs use bottom-left PDF points. ``glyphs`` are the full page's real
    PDFium glyph records. The caller must pass a supported, unrotated page.
    Only records with ``review_required=False`` are usable as extra detector
    views. Full paragraph/page views and downstream crop/character checks remain
    necessary; native line segmentation cannot prove a mathematical boundary.
    """
    paragraph = _bbox(paragraph_bbox)
    selected = []
    for line in lines:
        box = _bbox(line["bbox"])
        if _intersects(box, paragraph):
            selected.append((line, box))

    def record(box, rows, indices, reasons=(), grouping=()):
        scope = rows[0][0] if rows else {}
        return {
            "policy": INLINE_LINE_POLICY, "bbox": _box_dict(box),
            "paragraph_bbox": _box_dict(paragraph), "page_no": scope.get("page_no"),
            "source_sha256": scope.get("source_sha256"),
            "source_line_ids": [row["line_id"] for row, _ in rows],
            "source_line_indices": [row["line_index"] for row, _ in rows],
            "source_line_bboxes": [dict(row["bbox"]) for row, _ in rows],
            "source_lines": [{"line_id": row["line_id"], "native_text": row.get("native_text", "")}
                             for row, _ in rows],
            "pdfium_char_indices": sorted(indices), "physical_line_count": len(rows),
            "physical_only": True, "continuation_inferred": False,
            "formula_completeness_verified": False, "automatic_text_replacement_allowed": False,
            "paragraph_boundary_tolerance_points": _PARSER_BOX_TOLERANCE,
            "paragraph_boundary_overflow_points": {
                "left": max(paragraph[0] - box[0], 0), "bottom": max(paragraph[1] - box[1], 0),
                "right": max(box[2] - paragraph[2], 0), "top": max(box[3] - paragraph[3], 0),
            },
            "review_required": bool(reasons), "review_reasons": list(reasons),
            "grouping_evidence": sorted(set(grouping)),
        }

    def blocked(reason):
        return [record(paragraph, selected, [], [reason])]

    if not selected:
        return blocked("no_native_lines_for_paragraph")
    if any(type(line.get("page_no")) is not int or line["page_no"] < 1
           or not line.get("source_sha256") for line, _ in selected):
        return blocked("missing_native_line_provenance")
    if len({line["line_id"] for line, _ in selected}) != len(selected):
        return blocked("duplicate_native_line_ids")
    if len({(line.get("page_no"), line.get("source_sha256")) for line, _ in selected}) != 1:
        return blocked("inconsistent_native_line_provenance")
    if any(line.get("review_required") or line.get("from_ocr")
           or line.get("text_direction", "left_to_right") != "left_to_right" for line, _ in selected):
        return blocked("unsupported_native_line_evidence")

    visible = []
    for source in glyphs:
        if not source["text"].strip():
            continue
        box = _bbox(source["bbox"])
        if _intersects(box, paragraph):
            if not _contains(paragraph, box, tolerance=_PARSER_BOX_TOLERANCE):
                return blocked("paragraph_boundary_cuts_pdf_glyph")
            if source.get("generated"):
                return blocked("generated_visible_pdf_glyph")
            visible.append(dict(source, _box=box))
    if not visible:
        return blocked("no_pdf_glyphs_for_native_lines")
    if len({g["char_index"] for g in visible}) != len(visible):
        return blocked("duplicate_pdf_character_ids")

    components = []
    for row in selected:
        b = row[1]
        clipped = (max(b[0], paragraph[0]), max(b[1], paragraph[1]),
                   min(b[2], paragraph[2]), min(b[3], paragraph[3]))
        members = {g["char_index"] for g in visible if _intersects(g["_box"], clipped)}
        if not members:
            return blocked("native_line_without_pdf_glyphs")
        components.append({"box": clipped, "rows": [row], "members": members, "grouping": set()})

    def script_neighbor(glyph, base):
        if not glyph.get("origin") or not base.get("origin"):
            return False
        size, smaller = base.get("font_size", 0), glyph.get("font_size", 0)
        if not (math.isfinite(size) and math.isfinite(smaller) and 0 < smaller < .88 * size):
            return False
        offset = abs(glyph["origin"][1] - base["origin"][1])
        gap = max(glyph["_box"][0] - base["_box"][2], base["_box"][0] - glyph["_box"][2], 0)
        return .12 * size < offset < .95 * size and gap <= .55 * size

    def stacked_math(first, second):
        a, b = first["box"], second["box"]
        gap = max(a[1] - b[3], b[1] - a[3], 0)
        nested_projection = (a[0] <= b[0] and a[2] >= b[2]) or (b[0] <= a[0] and b[2] >= a[2])
        first_math = [g for g in visible if g["char_index"] in first["members"] and _math(g)]
        second_math = [g for g in visible if g["char_index"] in second["members"] and _math(g)]
        sizes = [g.get("font_size", 0) for g in first_math + second_math]
        sizes = [size for size in sizes if math.isfinite(size) and size > 0]
        # Conservative detector context: close, nested mathematical rows may
        # be a fraction. Sharing a view makes no claim that they are one formula.
        return bool(first_math and second_math and sizes and nested_projection
                    and gap <= .5 * statistics.median(sizes))

    changed = True
    while changed:
        changed = False
        for component in components:
            members = [g for g in visible if g["char_index"] in component["members"]]
            additions = {g["char_index"] for g in visible
                         if _intersects(g["_box"], component["box"])
                         or any(script_neighbor(g, base) for base in members)}
            if not additions <= component["members"]:
                component["members"] |= additions
                changed = True
            members = [g for g in visible if g["char_index"] in component["members"]]
            if members:
                expanded = _union([component["box"], *(g["_box"] for g in members)])
                if expanded != component["box"]:
                    component["box"] = expanded
                    changed = True
        merged = False
        for first_index, first in enumerate(components):
            for second_index in range(first_index + 1, len(components)):
                second = components[second_index]
                shared = bool(first["members"] & second["members"])
                overlap = _intersects(first["box"], second["box"])
                stacked = stacked_math(first, second)
                if not (shared or overlap or stacked):
                    continue
                first["box"] = _union([first["box"], second["box"]])
                first["rows"].extend(second["rows"])
                first["members"] |= second["members"]
                first["grouping"] |= second["grouping"] | {
                    "shared_or_intersecting_glyph_extent" if shared or overlap else "close_nested_math_rows"}
                components.pop(second_index)
                changed = merged = True
                break
            if merged:
                break
    covered = set().union(*(component["members"] for component in components))
    if any(g["char_index"] not in covered for g in visible):
        return blocked("native_line_glyph_coverage_incomplete")
    if any(not _contains(paragraph, c["box"], tolerance=_PARSER_BOX_TOLERANCE) for c in components):
        return blocked("line_view_outside_source_paragraph")
    views = []
    for component in components:
        rows = sorted(component["rows"], key=lambda row: (-row[1][3], row[1][0], row[0]["line_index"]))
        views.append(record(component["box"], rows, component["members"], grouping=component["grouping"]))
    return sorted(views, key=lambda view: (-view["bbox"]["t"], view["bbox"]["l"]))
