"""Crop table mathematics without borrowing ink from neighboring cells.

Geometry and ink-edge checks describe uncertainty. They never repair text or
certify a recognition result. This module has no model or network dependency.
"""
import math


MATH_CELL_CROP_POLICY = "neighbor-clipped-half-point-v1"


def _mapping(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ValueError("document_docling_invalid_provenance")
    return value


def _bounds(value, page_height):
    value = _mapping(value)
    try:
        coordinates = [value[key] for key in ("l", "t", "r", "b")]
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n)
               for n in coordinates):
            raise ValueError
        left, top, right, bottom = map(float, coordinates)
        origin = getattr(value.get("coord_origin"), "value", value.get("coord_origin"))
        if origin == "BOTTOMLEFT":
            top, bottom = page_height-top, page_height-bottom
        elif origin != "TOPLEFT":
            raise ValueError
        if right <= left or bottom <= top:
            raise ValueError
        return {"l": left, "t": top, "r": right, "b": bottom, "coord_origin": "TOPLEFT"}
    except (KeyError, TypeError, ValueError):
        raise ValueError("document_docling_invalid_provenance") from None


def _indices(cell):
    try:
        values = [cell[name] for name in ("start_row_offset_idx", "end_row_offset_idx",
                                          "start_col_offset_idx", "end_col_offset_idx")]
        if any(type(v) is not int for v in values):
            raise ValueError
        row, end_row, col, end_col = values
        if row < 0 or col < 0 or end_row <= row or end_col <= col:
            raise ValueError
        return row, end_row, col, end_col
    except (KeyError, TypeError, ValueError):
        raise ValueError("document_docling_invalid_provenance") from None


def _ink_edges(image):
    gray = image.convert("L")
    width, height = gray.size
    if not width or not height:
        raise ValueError("document_docling_invalid_output")
    threshold = 128
    edges = {
        "left": sum(gray.getpixel((0, y)) < threshold for y in range(height)),
        "top": sum(gray.getpixel((x, 0)) < threshold for x in range(width)),
        "right": sum(gray.getpixel((width-1, y)) < threshold for y in range(height)),
        "bottom": sum(gray.getpixel((x, height-1)) < threshold for x in range(width)),
    }
    return {"dark_pixel_threshold": threshold, "edge_pixels": edges,
            "touches_edge": any(edges.values())}


def render_math_cell_crop(pdf, table, cell, *, dpi=240, padding_points=0.5):
    """Return a rendered crop and evidence from a single-page Docling table.

    Expand only toward a reliable adjacent logical row/column, or the table's
    outside boundary. Missing/overlapping neighbor geometry cannot authorize
    expansion. Both mapping and Docling model inputs are supported.
    """
    from lib.infrastructure.docling_formula_crops import render_formula_crop

    if (isinstance(padding_points, bool) or not isinstance(padding_points, (int, float))
            or not math.isfinite(padding_points) or padding_points < 0):
        raise ValueError("document_docling_invalid_provenance")
    table, cell = _mapping(table), _mapping(cell)
    prov = table.get("prov")
    if not isinstance(prov, list) or len(prov) != 1:
        raise ValueError("document_docling_invalid_provenance")
    page_number = prov[0].get("page_no")
    if type(page_number) is not int or not 1 <= page_number <= len(pdf):
        raise ValueError("document_docling_invalid_provenance")
    page_width, page_height = pdf.get_page_size(page_number-1)
    outer = _bounds(prov[0].get("bbox"), page_height)
    source = _bounds(cell.get("bbox"), page_height)
    if not (0 <= outer["l"] <= source["l"] < source["r"] <= outer["r"] <= page_width
            and 0 <= outer["t"] <= source["t"] < source["b"] <= outer["b"] <= page_height):
        raise ValueError("document_docling_invalid_provenance")
    data = _mapping(table.get("data"))
    cells = data.get("table_cells")
    rows, cols = data.get("num_rows"), data.get("num_cols")
    if not isinstance(cells, list) or type(rows) is not int or type(cols) is not int:
        raise ValueError("document_docling_invalid_provenance")
    row, end_row, col, end_col = _indices(cell)
    if end_row > rows or end_col > cols or cell not in cells:
        raise ValueError("document_docling_invalid_provenance")
    limits = {"l": source["l"], "t": source["t"], "r": source["r"], "b": source["b"],
              "coord_origin": "TOPLEFT"}
    constraints = {}
    for side, at_edge in (("l", col == 0), ("t", row == 0), ("r", end_col == cols), ("b", end_row == rows)):
        constraints[side] = {"basis": "table_boundary" if at_edge else "missing_reliable_neighbor", "neighbors": []}
        if at_edge:
            limits[side] = outer[side]

    for index, neighbor in enumerate(cells):
        if neighbor == cell or not neighbor.get("bbox") or not str(neighbor.get("text", "")).strip():
            continue
        try:
            nrow, nend_row, ncol, nend_col = _indices(neighbor)
            box = _bounds(neighbor["bbox"], page_height)
        except ValueError:
            continue  # Missing reliable geometry never permits expansion.
        horizontal = box["r"] > source["l"]-padding_points and box["l"] < source["r"]+padding_points
        vertical = box["b"] > source["t"]-padding_points and box["t"] < source["b"]+padding_points
        sides = []
        if nend_row == row and horizontal:
            sides.append(("t", "b", box["b"] <= source["t"]))
        if nrow == end_row and horizontal:
            sides.append(("b", "t", box["t"] >= source["b"]))
        if nend_col == col and vertical:
            sides.append(("l", "r", box["r"] <= source["l"]))
        if ncol == end_col and vertical:
            sides.append(("r", "l", box["l"] >= source["r"]))
        for side, opposite, reliable in sides:
            entry = constraints[side]
            midpoint = (source[side]+box[opposite])/2
            entry["neighbors"].append({"cell_index": index, "bbox": box, "midpoint": midpoint,
                                       "reliable": reliable})

    for side, entry in constraints.items():
        neighbors = entry["neighbors"]
        if not neighbors:
            continue
        if any(not n["reliable"] for n in neighbors):
            limits[side] = source[side]
            entry["basis"] = "overlapping_neighbor_no_expansion"
        else:
            # Intersect every neighboring midpoint and the outer table bbox.
            values = [n["midpoint"] for n in neighbors] + [outer[side]]
            limits[side] = max(values) if side in ("l", "t") else min(values)
            entry["basis"] = "neighbor_text_midpoint"
        entry["limit"] = limits[side]
    image, evidence = render_formula_crop(pdf, page_number, cell["bbox"], dpi=dpi,
                                          padding_points=padding_points, clip_bbox=limits)
    ink = _ink_edges(image)
    warnings = ["ink_touches_crop_edge"] if ink["touches_edge"] else []
    if any(c["basis"] == "overlapping_neighbor_no_expansion" for c in constraints.values()):
        warnings.append("neighbor_text_bounds_overlap")
    return image, {**evidence, "policy": MATH_CELL_CROP_POLICY, "source_cell_bbox": cell["bbox"],
                   "table_bbox": prov[0]["bbox"], "table_ref": table.get("self_ref"),
                   "cell_index": cells.index(cell), "row": row, "column": col,
                   "row_span": end_row-row, "col_span": end_col-col,
                   "neighbor_constraints": constraints, "ink_edges": ink, "geometry_warnings": warnings,
                   "recognition_quality": "not_evaluated_by_crop_geometry"}
