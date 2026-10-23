"""Camelot recovery of collapsed flat grids, independently checked with PDFium."""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.metadata
import math
import unicodedata

TABLE_RECOVERY_POLICY = "camelot_stream_ruled_edges_complete_flat_glyph_grid_v1"


def _compact(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text) if not c.isspace())


def validate_flat_cells(table, page, source_box):
    """Check every cell and all source characters, not a confidence score."""
    rows, cols = table.shape
    if (not 1 <= rows <= 200 or not 1 <= cols <= 100
            or len(table.cells) != rows or len(table.data) != rows
            or any(len(line) != cols for line in [*table.cells, *table.data])):
        raise ValueError("table_recovery_invalid_grid")
    cells, claimed, proofs = [], [], []
    glyphs = [g for g in page["glyphs"] if not g.get("generated") and g["text"].strip()
              and source_box[0] <= (g["bbox"]["l"]+g["bbox"]["r"])/2 <= source_box[2]
              and source_box[1] <= (g["bbox"]["b"]+g["bbox"]["t"])/2 <= source_box[3]]
    if not glyphs:
        raise ValueError("table_recovery_missing_source_glyphs")
    for row, line in enumerate(table.cells):
        for col, cell in enumerate(line):
            if (not all(math.isfinite(v) for v in (cell.x1, cell.y1, cell.x2, cell.y2))
                    or cell.x1 >= cell.x2 or cell.y1 >= cell.y2):
                raise ValueError("table_recovery_invalid_cell")
            chosen = [g for g in glyphs
                      if cell.x1 <= (g["bbox"]["l"]+g["bbox"]["r"])/2 < cell.x2
                      and cell.y1 <= (g["bbox"]["b"]+g["bbox"]["t"])/2 < cell.y2]
            value = table.data[row][col].strip()
            if chosen:
                size = max(g["font_size"] for g in chosen)
                if (any(g.get("origin") is None or g["font_size"] <= 0 for g in chosen)
                        or max(g["origin"][1] for g in chosen)-min(g["origin"][1] for g in chosen) > .1*size
                        or min(g["font_size"] for g in chosen) < .88*size):
                    raise ValueError("table_recovery_nonflat_source")
                chosen.sort(key=lambda g: g["origin"][0])
            if _compact("".join(g["text"] for g in chosen)) != _compact(value):
                raise ValueError("table_recovery_cell_source_disagrees")
            ids = [g["char_index"] for g in chosen]
            claimed.extend(ids)
            box = dict(l=cell.x1, b=cell.y1, r=cell.x2, t=cell.y2, coord_origin="BOTTOMLEFT")
            cells.append(dict(text=value, bbox=box, row_span=1, col_span=1,
                start_row_offset_idx=row, end_row_offset_idx=row+1,
                start_col_offset_idx=col, end_col_offset_idx=col+1,
                column_header=row == 0, row_header=col == 0 and row > 0))
            proofs.append(dict(row=row, column=col, glyph_ids=ids, bbox=box, source_text=value))
    if Counter(claimed) != Counter(g["char_index"] for g in glyphs):
        raise ValueError("table_recovery_incomplete_or_duplicate_source_coverage")
    return cells, proofs


def recover_flat_tables(document, source):
    """Accept only complete flat source grids; leave matrices/scripts intact."""
    audit = []
    try:
        if importlib.metadata.version("camelot-py") != "2.0.0":
            return [{"policy": TABLE_RECOVERY_POLICY, "status": "skipped", "reason": "unsupported_camelot_version"}]
        import camelot
    except (ImportError, importlib.metadata.PackageNotFoundError):
        return [{"policy": TABLE_RECOVERY_POLICY, "status": "skipped", "reason": "camelot_not_installed"}]
    from .math_regions import extract_pdf_page_glyphs, _box
    from docling_core.types.doc import TableData
    pages = {}
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    for item in document.tables:
        if len(item.prov) != 1 or not 3 <= item.data.num_rows <= 200 or not 2 <= item.data.num_cols <= 10:
            continue
        prov = item.prov[0]
        if prov.page_no not in pages:
            pages[prov.page_no] = extract_pdf_page_glyphs(source, prov.page_no)
        page = pages[prov.page_no]
        if page.get("review_required"):
            continue
        rect = _box(prov.bbox, page["height"])
        area = ",".join(map(str, (rect[0], rect[3], rect[2], rect[1])))
        entry = {"policy": TABLE_RECOVERY_POLICY, "table_ref": item.self_ref,
                 "page_no": prov.page_no, "source_sha256": source_sha, "status": "unchanged"}
        try:
            stream = camelot.read_pdf(str(source), pages=str(prov.page_no), flavor="stream",
                                     table_areas=[area], split_text=True, suppress_stdout=True)
            if len(stream) != 1:
                continue
            candidate = stream[0]
            if (not item.data.num_cols*1.5 <= candidate.shape[1] <= 100
                    or candidate.shape[0] < 3 or not all(candidate.data[0])):
                continue
            # Keep visible ruled edges. Subdivide a ruled column only if it
            # contains multiple stream header columns, never by desired count.
            lattice = camelot.read_pdf(str(source), pages=str(prov.page_no), flavor="lattice",
                                      table_areas=[area], line_scale=40, suppress_stdout=True)
            if len(lattice) == 1:
                coarse = lattice[0].cols
                cuts = [c[1] for c in coarse[:-1]]
                for lo, hi in coarse:
                    headers = [c for c in candidate.cols if lo <= (c[0]+c[1])/2 < hi]
                    if len(headers) > 1:
                        cuts.extend(c[1] for c in headers[:-1] if lo < c[1] < hi)
                if cuts:
                    constrained = camelot.read_pdf(str(source), pages=str(prov.page_no), flavor="stream",
                        table_areas=[area], columns=[",".join(map(str, sorted(set(cuts))))],
                        split_text=True, suppress_stdout=True)
                    if len(constrained) != 1:
                        raise ValueError("table_recovery_ambiguous_grid")
                    candidate = constrained[0]
            if (not item.data.num_cols*1.5 <= candidate.shape[1] <= 100
                    or not 3 <= candidate.shape[0] <= 200 or not all(candidate.data[0])):
                raise ValueError("table_recovery_not_a_complete_wider_grid")
            cells, proofs = validate_flat_cells(candidate, page, rect)
            original = item.data.model_dump(mode="json")
            item.data = TableData(num_rows=candidate.shape[0], num_cols=candidate.shape[1], table_cells=cells)
            entry.update(status="recovered", original_data=original,
                         grid_shape=list(candidate.shape), source_cells=proofs,
                         validation="all_source_glyphs_once_ordered_single_baseline_cells")
        except Exception as error:
            entry.update(status="review_required", reason=str(error), error_type=type(error).__name__)
        audit.append(entry)
    return audit
