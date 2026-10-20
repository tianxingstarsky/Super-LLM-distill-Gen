"""Evidence-only refinement: no OCR models, provider calls or paper fixtures."""
from collections import Counter
from copy import deepcopy

import pytest

from lib.infrastructure.docling_table_geometry import extract_pdf_rulings, refine_document_table_spans


def cell(text, row, col, x, y, *, colspan=1, rowspan=1):
    return {"text": text, "bbox": {"l":x-3,"b":y-3,"r":x+3,"t":y+3,"coord_origin":"BOTTOMLEFT"},
            "start_row_offset_idx":row,"end_row_offset_idx":row+rowspan,
            "start_col_offset_idx":col,"end_col_offset_idx":col+colspan,
            "row_span":rowspan,"col_span":colspan}


def doc(cells, rows=3, cols=3):
    return {"tables":[{"self_ref":"#/tables/0", "prov":[{"page_no":1,
        "bbox":{"l":0,"b":0,"r":100,"t":100,"coord_origin":"BOTTOMLEFT"}}],
        "data":{"num_rows":rows,"num_cols":cols,"table_cells":cells}}]}


def lines(horizontal=(), vertical=()):
    return {1:{"status":"ok","width":100,"height":100,"horizontal":list(horizontal),"vertical":list(vertical)}}


def grouped():
    cells = [cell("Shared category",1,0,10,55)]
    for row,y in enumerate((85,55,25)):
        cells.extend([cell(f"x{row}",row,1,50,y),cell(str(10+row),row,2,85,y)])
    return doc(cells), lines([(0,0,100,0),(0,100,100,100)],[(30,0,30,100)])


def texts(document):
    return Counter(c["text"] for c in document["tables"][0]["data"]["table_cells"] if c["text"].strip())


def test_open_outer_boundary_is_explicit_and_input_is_immutable():
    original,rulings = grouped()
    before = deepcopy(original)
    fixed,audit = refine_document_table_spans(original,rulings)
    assert original == before
    target = fixed["tables"][0]["data"]["table_cells"][0]
    assert (target["start_row_offset_idx"],target["row_span"]) == (0,3)
    change = audit[0]["changes"][0]
    assert change["sides"]["left"]["source"] == "layout-boundary"
    assert change["sides"]["right"]["source"] == "pdf-ruling"
    assert texts(original) == texts(fixed)
    assert fixed["tables"][0]["data"]["table_cells"][1:] == original["tables"][0]["data"]["table_cells"][1:]
    again,second_audit = refine_document_table_spans(fixed,rulings)
    assert again == fixed and second_audit[0]["changes"] == []


def test_strict_mode_does_not_invent_missing_outer_stroke():
    original,rulings = grouped()
    fixed,audit = refine_document_table_spans(original,rulings,allow_layout_boundary=False)
    assert fixed == original and not audit[0]["changes"]
    rulings[1]["vertical"].append((0,0,0,100))
    fixed,audit = refine_document_table_spans(original,rulings,allow_layout_boundary=False)
    assert fixed != original
    assert all(side["source"] == "pdf-ruling" for side in audit[0]["changes"][0]["sides"].values())


def test_partial_header_rule_supports_a_span_without_text_conditions():
    cells = [cell("Any heading",0,0,10,75)]
    for row,y in enumerate((85,65,25)):
        if row == 2:
            cells.append(cell("Value",row,0,10,y))
        cells.extend([cell(f"L{row}",row,1,50,y),cell(f"R{row}",row,2,85,y)])
    original = doc(cells)
    rulings = lines([(0,0,100,0),(0,50,100,50),(0,100,100,100),(30,75,100,75)])
    fixed,audit = refine_document_table_spans(original,rulings)
    assert fixed["tables"][0]["data"]["table_cells"][0]["row_span"] == 2
    assert audit[0]["changes"][0]["sides"]["right"]["source"] == "partial-rule-boundary"
    assert texts(fixed) == texts(original)


def test_equal_nonempty_values_are_not_collapsed():
    original,rulings = grouped()
    original["tables"][0]["data"]["table_cells"].append(cell("Shared category",0,0,10,85))
    fixed,audit = refine_document_table_spans(original,rulings)
    assert fixed == original and not audit[0]["changes"]


def test_empty_placeholders_can_be_absorbed_but_values_cannot():
    original,rulings = grouped()
    cells = original["tables"][0]["data"]["table_cells"]
    cells.extend([cell("",0,0,10,85),cell("",2,0,10,25)])
    fixed,audit = refine_document_table_spans(original,rulings)
    assert audit[0]["changes"][0]["absorbed_empty_cells"] == 2
    assert texts(fixed) == texts(original)
    cells[-1]["text"] = "existing value"
    assert refine_document_table_spans(original,rulings)[0] == original


def test_partial_text_boundary_and_missing_geometry_leave_table_unchanged():
    original,rulings = grouped()
    original["tables"][0]["data"]["table_cells"][0]["bbox"]["r"] = 40
    assert refine_document_table_spans(original,rulings)[0] == original
    original,rulings = grouped()
    original["tables"][0]["data"]["table_cells"][1].pop("bbox")
    fixed,audit = refine_document_table_spans(original,rulings)
    assert fixed == original and audit[0]["status"] == "missing_nonempty_cell_geometry"


def test_real_interior_separator_blocks_merging_across_it():
    original,rulings = grouped()
    rulings[1]["horizontal"].append((0,70,30,70))
    fixed,_ = refine_document_table_spans(original,rulings)
    target = fixed["tables"][0]["data"]["table_cells"][0]
    assert (target["start_row_offset_idx"],target["row_span"]) == (1,2)


def test_layout_only_rectangle_is_not_sufficient_evidence():
    original,rulings = grouped()
    rulings[1]["vertical"] = []
    assert refine_document_table_spans(original,rulings)[0] == original


def test_missing_grid_row_is_not_guessed_and_rotated_status_is_respected():
    original,rulings = grouped()
    original["tables"][0]["data"]["table_cells"] = [c for c in original["tables"][0]["data"]["table_cells"] if c["start_row_offset_idx"] != 0]
    fixed,audit = refine_document_table_spans(original,rulings)
    assert fixed == original and audit[0]["status"] == "incomplete_fine_grid_anchors"
    rulings[1]["status"] = "unsupported_coordinate_space"
    assert refine_document_table_spans(original,rulings)[1][0]["status"] == "unsupported_coordinate_space"


def test_top_left_cell_coordinates_and_closed_horizontal_span():
    cells = [cell("Existing note",0,0,15,75)]
    cells.extend(cell(str(i),1,i,x,25) for i,x in enumerate((15,50,85)))
    original = doc(cells,rows=2)
    for c in cells:
        box = c["bbox"]
        box.update(t=100-box["t"],b=100-box["b"],coord_origin="TOPLEFT")
    rulings = lines([(0,0,100,0),(0,50,100,50),(0,100,100,100)],[(0,0,0,100),(100,0,100,100)])
    fixed,audit = refine_document_table_spans(original,rulings,allow_layout_boundary=False)
    assert fixed["tables"][0]["data"]["table_cells"][0]["col_span"] == 3
    assert texts(fixed) == texts(original) and len(audit[0]["changes"]) == 1


def test_cross_page_and_overlapping_existing_tables_are_not_rewritten():
    original,rulings = grouped()
    original["tables"][0]["prov"].append(deepcopy(original["tables"][0]["prov"][0]))
    assert refine_document_table_spans(original,rulings)[1][0]["status"] == "ambiguous_table_provenance"
    original,rulings = grouped()
    original["tables"][0]["data"]["table_cells"].append(deepcopy(original["tables"][0]["data"]["table_cells"][0]))
    assert refine_document_table_spans(original,rulings)[1][0]["status"] == "invalid_existing_table_geometry"


def test_pdfium_reads_transformed_real_paths_and_skips_rotated_page(tmp_path):
    pytest.importorskip("pypdfium2")
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, NameObject
    writer = pypdf.PdfWriter()
    for rotated in (False,True):
        page = writer.add_blank_page(width=100,height=100)
        stream = DecodedStreamObject()
        stream.set_data(b"q 1 0 0 1 10 20 cm 1 w 0 0 m 70 0 l S 0 0 m 0 60 l S Q")
        page[NameObject("/Contents")] = writer._add_object(stream)
        if rotated:
            page.rotate(90)
    path = tmp_path / "real-rulings.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    rulings = extract_pdf_rulings(path,[1,2])
    assert rulings[1]["status"] == "ok"
    assert rulings[1]["horizontal"] == [(10.,20.,80.,20.)]
    assert rulings[1]["vertical"] == [(10.,20.,10.,80.)]
    assert rulings[2]["status"] == "unsupported_coordinate_space"
    assert extract_pdf_rulings(path,[0])[0]["status"] == "ruling_extraction_failed"


def test_bad_optional_geometry_preserves_original_instead_of_failing_document():
    original,rulings = grouped()
    rulings[1]["height"] = None
    fixed,audit = refine_document_table_spans(original,rulings)
    assert fixed == original and audit[0]["status"] == "invalid_page_geometry"
