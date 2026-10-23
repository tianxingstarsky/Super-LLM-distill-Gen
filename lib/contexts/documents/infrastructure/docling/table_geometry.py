"""Conservative span refinement from PDF rulings and an existing fine grid.

This does not OCR, synthesize, or replace cell text. PDF strokes, layout edges,
and interrupted-rule boundaries have distinct provenance in the audit output.
An inferred span is a candidate supported by geometry, not semantic validation.
"""
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
from itertools import combinations
from math import isfinite
from statistics import median

GEOMETRY_POLICY = "pdf-rulings-layout-spans-v1"


def _merge_lines(lines, *, vertical, tolerance):
    groups = []
    for line in sorted(lines, key=lambda s: s[0] if vertical else s[1]):
        const = line[0] if vertical else line[1]
        lo, hi = (sorted((line[1], line[3])) if vertical else sorted((line[0], line[2])))
        if groups and abs(const - groups[-1][0]) <= tolerance:
            groups[-1][1].append((lo, hi))
        else:
            groups.append((const, [(lo, hi)]))
    result = []
    for const, intervals in groups:
        merged = []
        for lo, hi in sorted(intervals):
            if merged and lo <= merged[-1][1] + tolerance:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        result.extend((const, lo, const, hi) if vertical else (lo, const, hi, const) for lo, hi in merged)
    return result


def _extract_pdf_rulings(path, page_numbers):
    """Read top-level, visible, straight stroked paths using optional PDFium.

Coordinates are PDF bottom-left points. Unsupported rotated/cropped spaces and
pages with Form XObjects are skipped, rather than guessed. Use in the existing
single-process document worker; PDFium itself is not safe for concurrent calls.
Filled shapes, curves and raster lines do not become fabricated ruling evidence.
"""
    import ctypes
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    result = {}
    with closing(pdfium.PdfDocument(path)) as pdf:
        for number in sorted(set(page_numbers)):
            if type(number) is not int or not 1 <= number <= len(pdf):
                raise ValueError("document_docling_invalid_provenance")
            with closing(pdf[number - 1]) as page:
                width, height = page.get_size()
                record = {"width": width, "height": height, "horizontal": [], "vertical": [],
                          "status": "ok", "reader": "pypdfium2-stroked-paths"}
                result[number] = record
                crop = page.get_cropbox()
                if page.get_rotation() != 0 or abs(crop[0]) > 0.01 or abs(crop[1]) > 0.01:
                    record["status"] = "unsupported_coordinate_space"
                    continue
                objects = list(page.get_objects(max_depth=1))
                if len(objects) > 20000 or any(obj.type == raw.FPDF_PAGEOBJ_FORM for obj in objects):
                    record["status"] = "unsupported_nested_or_complex_paths"
                    continue
                for obj in objects:
                    if obj.type != raw.FPDF_PAGEOBJ_PATH:
                        continue
                    fill, stroke = ctypes.c_int(), raw.FPDF_BOOL()
                    if not raw.FPDFPath_GetDrawMode(obj, fill, stroke) or not stroke.value:
                        continue
                    rgba = [ctypes.c_uint() for _ in range(4)]
                    if not raw.FPDFPageObj_GetStrokeColor(obj, *rgba) or rgba[3].value == 0:
                        continue
                    count = raw.FPDFPath_CountSegments(obj)
                    if count < 2 or count > 10000:
                        continue
                    a, b, c, d, e, f = obj.get_matrix().get()
                    points = []
                    for index in range(count):
                        segment = raw.FPDFPath_GetPathSegment(obj, index)
                        x, y = ctypes.c_float(), ctypes.c_float()
                        kind = raw.FPDFPathSegment_GetType(segment)
                        if kind not in (raw.FPDF_SEGMENT_MOVETO, raw.FPDF_SEGMENT_LINETO) or not raw.FPDFPathSegment_GetPoint(segment, x, y):
                            points = []
                            break
                        points.append((kind, (a*x.value+c*y.value+e, b*x.value+d*y.value+f), bool(raw.FPDFPathSegment_GetClose(segment))))
                    previous = start = None
                    for kind, point, close in points:
                        pairs = []
                        if kind == raw.FPDF_SEGMENT_MOVETO:
                            start = point
                        elif previous is not None:
                            pairs.append((previous, point))
                        if close and start is not None:
                            pairs.append((point, start))
                        for first, last in pairs:
                            x0, y0 = first
                            x1, y1 = last
                            if abs(y1-y0) <= 0.05 and abs(x1-x0) > 2:
                                record["horizontal"].append((min(x0,x1), (y0+y1)/2, max(x0,x1), (y0+y1)/2))
                            elif abs(x1-x0) <= 0.05 and abs(y1-y0) > 2:
                                record["vertical"].append(((x0+x1)/2, min(y0,y1), (x0+x1)/2, max(y0,y1)))
                        previous = point
                record["horizontal"] = _merge_lines(record["horizontal"], vertical=False, tolerance=0.5)
                record["vertical"] = _merge_lines(record["vertical"], vertical=True, tolerance=0.5)
    return result


def extract_pdf_rulings(path, page_numbers):
    """Optional geometry evidence must never invalidate the original parsing.

On missing PDFium or unsupported input, callers get an explicit skipped record
for each requested page. No fallback downloads or alternate parser are invoked.
"""
    numbers = list(page_numbers)
    try:
        return _extract_pdf_rulings(path,numbers)
    except Exception as exc:
        return {number:{"status":"ruling_extraction_failed", "error_type":type(exc).__name__,
                        "policy":GEOMETRY_POLICY,"horizontal":[],"vertical":[]} for number in numbers}


def _box(value, height):
    if not isinstance(value, dict) or any(k not in value for k in ("l", "b", "r", "t")):
        return None
    try:
        l, b, r, t = (float(value[k]) for k in ("l", "b", "r", "t"))
    except (TypeError, ValueError):
        return None
    origin = value.get("coord_origin")
    if origin == "TOPLEFT":
        b, t = height - b, height - t
    elif origin != "BOTTOMLEFT":
        return None
    return (l, b, r, t) if all(isfinite(x) for x in (l,b,r,t)) and l < r and b < t else None


def _contains(rect, box, tolerance):
    return rect[0]-tolerance <= box[0] and rect[1]-tolerance <= box[1] and rect[2]+tolerance >= box[2] and rect[3]+tolerance >= box[3]


def _center_inside(rect, box):
    return rect[0] < (box[0]+box[2])/2 < rect[2] and rect[1] < (box[1]+box[3])/2 < rect[3]


def _anchors(cells, height, axis):
    points = defaultdict(list)
    for cell in cells:
        box = _box(cell.get("bbox"), height)
        if box and cell.get("text", "").strip() and cell[axis+"_span"] == 1:
            points[cell["start_"+axis+"_offset_idx"]].append((box[1]+box[3])/2 if axis == "row" else (box[0]+box[2])/2)
    return {index: median(values) for index, values in points.items()}


def _boundaries(rect, horizontal, vertical, cells, height, tolerance, allow_layout):
    edges = [{"segment": line, "source": "pdf-ruling"} for line in vertical]
    if not allow_layout:
        return edges
    for x in (rect[0], rect[2]):
        if not any(abs(x-v[0]) <= tolerance and v[1] <= rect[1]+tolerance and v[3] >= rect[3]-tolerance for v in vertical):
            edges.append({"segment": (x,rect[1],x,rect[3]), "source": "layout-boundary"})
    full = [h for h in horizontal if h[0] <= rect[0]+tolerance and h[2] >= rect[2]-tolerance]
    for h in horizontal:
        if h in full:
            continue
        lower = [line for line in full if line[1] < h[1]-tolerance]
        upper = [line for line in full if line[1] > h[1]+tolerance]
        if not lower or not upper:
            continue
        bottom, top = max(lower, key=lambda line: line[1]), min(upper, key=lambda line: line[1])
        for x in (h[0],h[2]):
            if not rect[0]+tolerance < x < rect[2]-tolerance:
                continue
            # A partial horizontal rule ending in a text box is not a divider.
            if any(box and bottom[1] < (box[1]+box[3])/2 < top[1] and box[0]+tolerance < x < box[2]-tolerance
                   for box in (_box(cell.get("bbox"),height) for cell in cells if cell.get("text", "").strip())):
                continue
            edges.append({"segment": (x,bottom[1],x,top[1]), "source": "partial-rule-boundary",
                          "supporting_rule": h, "bracketing_rules": [bottom,top]})
    return edges


def _rectangles(horizontal, edges, tolerance):
    found = {}
    for left, right in combinations(sorted(edges, key=lambda edge: edge["segment"][0]), 2):
        vl, vr = left["segment"], right["segment"]
        x0, x1 = vl[0], vr[0]
        if x1-x0 <= tolerance or left["source"] == right["source"] == "layout-boundary":
            continue
        levels = [h for h in horizontal if h[0] <= x0+tolerance and h[2] >= x1-tolerance
                  and max(vl[1],vr[1])-tolerance <= h[1] <= min(vl[3],vr[3])+tolerance]
        levels = sorted(levels, key=lambda h:h[1])
        # Nonadjacent levels always contain a complete horizontal separator.
        for bottom, top in zip(levels,levels[1:]):
            rect = (x0,bottom[1],x1,top[1])
            if rect[3]-rect[1] <= tolerance:
                continue
            if any(rect[1]+tolerance < h[1] < rect[3]-tolerance and h[0] <= x0+tolerance and h[2] >= x1-tolerance for h in horizontal):
                continue
            if any(x0+tolerance < edge["segment"][0] < x1-tolerance and edge["segment"][1] <= rect[1]+tolerance and edge["segment"][3] >= rect[3]-tolerance for edge in edges):
                continue
            found[rect] = {"left": left, "right": right,
                           "bottom": {"segment": bottom, "source": "pdf-ruling"},
                           "top": {"segment": top, "source": "pdf-ruling"}}
            if len(found) > 4096:
                raise ValueError("too_many_candidate_rectangles")
    return found


_SPAN_KEYS = ("start_row_offset_idx", "end_row_offset_idx", "start_col_offset_idx", "end_col_offset_idx", "row_span", "col_span")


def _valid_cells(cells, rows, cols):
    occupied = set()
    for cell in cells:
        if any(type(cell.get(key)) is not int for key in _SPAN_KEYS) or not isinstance(cell.get("text", ""),str):
            return False
        r0,r1,c0,c1,rs,cs = (cell[key] for key in _SPAN_KEYS)
        if not (0 <= r0 < r1 <= rows and 0 <= c0 < c1 <= cols and r1-r0 == rs and c1-c0 == cs):
            return False
        area = {(r,c) for r in range(r0,r1) for c in range(c0,c1)}
        if occupied & area:
            return False
        occupied.update(area)
    return True


def _refine_document_table_spans(document, page_rulings, *, allow_layout_boundary=True, tolerance=2.0):
    """Return a new Docling document dict and separate per-table evidence.

Only expand one existing nonempty cell into a proven region. Never move text
across real rulings, replace text, collapse equal-valued cells, or shrink spans.
Missing geometry or ambiguous fine-grid anchors leave the table unchanged.
"""
    if not isfinite(tolerance) or not 0 < tolerance <= 3:
        raise ValueError("invalid_geometry_tolerance")
    result = deepcopy(document)
    audit = []
    for table in result.get("tables", []):
        evidence = {"table_ref": table.get("self_ref"), "page_no": None, "changes": [], "candidates": []}
        audit.append(evidence)
        prov = table.get("prov", [])
        if len(prov) != 1:
            evidence["status"] = "ambiguous_table_provenance"
            continue
        page = page_rulings.get(prov[0].get("page_no"), {})
        evidence["page_no"] = prov[0].get("page_no")
        if page.get("status") != "ok":
            evidence["status"] = page.get("status", "missing_pdf_rulings")
            continue
        height = page.get("height")
        if type(height) not in (int, float) or not isfinite(height) or height <= 0:
            evidence["status"] = "invalid_page_geometry"
            continue
        rect = _box(prov[0].get("bbox"), height)
        data = table.get("data", {})
        cells = data.get("table_cells", [])
        rows, cols = data.get("num_rows",0), data.get("num_cols",0)
        if type(rows) is not int or type(cols) is not int or not (0 < rows <= 1000 and 0 < cols <= 250) or len(cells) > 100000:
            evidence["status"] = "unsupported_table_size"
            continue
        if not rect or not _valid_cells(cells, data.get("num_rows",0), data.get("num_cols",0)):
            evidence["status"] = "invalid_existing_table_geometry"
            continue
        if any(c.get("text", "").strip() and _box(c.get("bbox"),height) is None for c in cells):
            evidence["status"] = "missing_nonempty_cell_geometry"
            continue
        horizontal = [tuple(h) for h in page.get("horizontal", []) if rect[1]-tolerance <= h[1] <= rect[3]+tolerance and h[0] < rect[2] and h[2] > rect[0]]
        vertical = [tuple(v) for v in page.get("vertical", []) if rect[0]-tolerance <= v[0] <= rect[2]+tolerance and v[1] < rect[3] and v[3] > rect[1]]
        # Highly complex diagrams are not treated as tables by this pass.
        if len(horizontal) > 200 or len(vertical) > 100:
            evidence["status"] = "too_many_rulings"
            continue
        row_anchors, col_anchors = _anchors(cells,height,"row"), _anchors(cells,height,"col")
        evidence.update(row_anchors=row_anchors,col_anchors=col_anchors,layout_bbox_pdf=rect)
        if len(row_anchors) != rows or len(col_anchors) != cols:
            evidence["status"] = "incomplete_fine_grid_anchors"
            continue
        edges = _boundaries(rect,horizontal,vertical,cells,height,tolerance,allow_layout_boundary)
        if len(edges) > 128:
            evidence["status"] = "too_many_candidate_boundaries"
            continue
        before_text = Counter(c.get("text", "") for c in cells if c.get("text", "").strip())
        for candidate, sides in _rectangles(horizontal,edges,tolerance).items():
            if not _contains(rect,candidate,tolerance):
                continue
            info = {"rectangle_pdf": candidate, "sides": sides}
            evidence["candidates"].append(info)
            rs = sorted(i for i,y in row_anchors.items() if candidate[1] < y < candidate[3])
            cs = sorted(i for i,x in col_anchors.items() if candidate[0] < x < candidate[2])
            if not rs or not cs or rs != list(range(rs[0],rs[-1]+1)) or cs != list(range(cs[0],cs[-1]+1)):
                info["reason"] = "missing_or_noncontiguous_grid_anchors"
                continue
            inside = [c for c in cells if c.get("text", "").strip() and _center_inside(candidate,_box(c["bbox"],height))]
            if len(inside) != 1:
                info.update(reason="requires_one_nonempty_cell",nonempty_count=len(inside))
                continue
            cell = inside[0]
            if not _contains(candidate,_box(cell["bbox"],height),tolerance):
                info["reason"] = "text_crosses_boundary"
                continue
            r0,r1,c0,c1 = rs[0],rs[-1]+1,cs[0],cs[-1]+1
            old = {key:cell[key] for key in _SPAN_KEYS}
            new = dict(zip(_SPAN_KEYS,(r0,r1,c0,c1,r1-r0,c1-c0)))
            if not (r0 <= old["start_row_offset_idx"] and r1 >= old["end_row_offset_idx"] and c0 <= old["start_col_offset_idx"] and c1 >= old["end_col_offset_idx"]):
                info["reason"] = "would_shrink_or_shift_cell"
                continue
            if old == new:
                info["reason"] = "already_matches"
                continue
            conflicts = [c for c in cells if c is not cell and c["start_row_offset_idx"] < r1 and c["end_row_offset_idx"] > r0 and c["start_col_offset_idx"] < c1 and c["end_col_offset_idx"] > c0]
            if any(c.get("text", "").strip() for c in conflicts):
                info["reason"] = "other_nonempty_cell_in_fine_grid"
                continue
            if any(not (r0 <= c["start_row_offset_idx"] and c["end_row_offset_idx"] <= r1 and c0 <= c["start_col_offset_idx"] and c["end_col_offset_idx"] <= c1) for c in conflicts):
                info["reason"] = "would_cut_empty_span"
                continue
            for conflict in conflicts:
                cells.remove(conflict)
            cell.update(new)
            info["reason"] = "accepted_ruling_supported_span"
            evidence["changes"].append({"before":old,"after":new,"text_unchanged":cell["text"],
                "rectangle_pdf":candidate,"sides":sides,"absorbed_empty_cells":len(conflicts)})
        if before_text != Counter(c.get("text", "") for c in cells if c.get("text", "").strip()) or not _valid_cells(cells,data["num_rows"],data["num_cols"]):
            raise ValueError("document_docling_geometry_preservation_failed")
        evidence.update(status="refined" if evidence["changes"] else "unchanged",nonempty_text_multiset_preserved=True)
    return result, audit


def refine_document_table_spans(document, page_rulings, *, allow_layout_boundary=True, tolerance=2.0):
    """Return ``(new_document_dict, audit_list)`` without changing the input.

Each audit record has policy, table_ref, page_no, status, changes, and candidates.
Changes record old/new index spans, unchanged text, rectangle and named sides;
side sources are pdf-ruling, layout-boundary, or partial-rule-boundary. A failed
optional refinement preserves that entire original table and reports its reason.
"""
    result = deepcopy(document)
    audit = []
    for index,table in enumerate(document.get("tables",[])):
        try:
            refined,items = _refine_document_table_spans({"tables":[table]},page_rulings,
                allow_layout_boundary=allow_layout_boundary,tolerance=tolerance)
            result["tables"][index] = refined["tables"][0]
            audit.extend(dict(item,policy=GEOMETRY_POLICY) for item in items)
        except (ValueError,TypeError,KeyError,IndexError,OverflowError) as exc:
            audit.append({"policy":GEOMETRY_POLICY,"table_ref":table.get("self_ref"),"page_no":None,
                "status":"geometry_refinement_skipped","error_type":type(exc).__name__,
                "reason":str(exc) if isinstance(exc,ValueError) else "invalid_geometry_input",
                "changes":[],"candidates":[]})
    return result,audit
