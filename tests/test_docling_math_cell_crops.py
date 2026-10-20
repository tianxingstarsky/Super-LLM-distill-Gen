import copy

from PIL import Image
import pytest

from lib.infrastructure import docling_formula_crops as rendering
from lib.infrastructure.docling_math_cell_crops import render_math_cell_crop


class PDF:
    def __len__(self):
        return 1

    def get_page_size(self, index):
        assert index == 0
        return 100, 100


def box(left, top, right, bottom):
    return dict(l=left, t=top, r=right, b=bottom, coord_origin="TOPLEFT")


@pytest.fixture
def table():
    cells = [{"bbox": box(10+20*c, 10+20*r, 20+20*c, 20+20*r), "text": "math",
              "start_row_offset_idx": r, "end_row_offset_idx": r+1,
              "start_col_offset_idx": c, "end_col_offset_idx": c+1} for r in range(3) for c in range(3)]
    return {"self_ref": "#/tables/0", "prov": [{"page_no": 1, "bbox": box(0, 0, 100, 100)}],
            "data": {"num_rows": 3, "num_cols": 3, "table_cells": cells}}


@pytest.fixture
def capture(monkeypatch):
    calls = []
    def render(pdf, page, bbox, **kwargs):
        calls.append((page, copy.deepcopy(bbox), kwargs))
        return Image.new("RGB", (20, 20), "white"), {"source_bbox": bbox, "clip_bbox": kwargs["clip_bbox"]}
    monkeypatch.setattr(rendering, "render_formula_crop", render)
    return calls


def test_midpoints_clip_all_four_sides_and_preserve_source(table, capture):
    before = copy.deepcopy(table)
    cell = table["data"]["table_cells"][4]
    _, evidence = render_math_cell_crop(PDF(), table, cell)
    assert capture[0][1] == cell["bbox"]
    assert capture[0][2]["padding_points"] == .5
    assert capture[0][2]["clip_bbox"] == box(25, 25, 45, 45)
    assert all(c["basis"] == "neighbor_text_midpoint" for c in evidence["neighbor_constraints"].values())
    assert evidence["source_cell_bbox"] == cell["bbox"]
    assert evidence["recognition_quality"] == "not_evaluated_by_crop_geometry"
    assert table == before


def test_missing_neighbor_does_not_allow_internal_expansion(table, capture):
    cells = table["data"]["table_cells"]
    cell = cells[4]
    cells.remove(cells[1])  # The other row-zero columns do not overlap this text.
    _, evidence = render_math_cell_crop(PDF(), table, cell)
    assert evidence["clip_bbox"]["t"] == cell["bbox"]["t"]
    assert evidence["neighbor_constraints"]["t"]["basis"] == "missing_reliable_neighbor"


def test_outer_cell_is_bounded_by_table(table, capture):
    cell = table["data"]["table_cells"][0]
    _, evidence = render_math_cell_crop(PDF(), table, cell)
    assert evidence["clip_bbox"] == box(0, 0, 25, 25)
    assert evidence["neighbor_constraints"]["t"]["basis"] == "table_boundary"


def test_merged_cell_uses_end_indices_for_neighbors(table, capture):
    cells = table["data"]["table_cells"]
    cell = cells[4]
    cell["bbox"]["r"] = 60
    cell["end_col_offset_idx"] = 3
    cells.remove(cells[5])
    _, evidence = render_math_cell_crop(PDF(), table, cell)
    assert evidence["col_span"] == 2
    assert evidence["clip_bbox"] == box(25, 25, 100, 45)
    assert len(evidence["neighbor_constraints"]["t"]["neighbors"]) == 2


def test_bottom_left_bbox_is_converted_but_not_rewritten(table, capture):
    cell = table["data"]["table_cells"][4]
    cell["bbox"] = dict(l=30, t=70, r=40, b=60, coord_origin="BOTTOMLEFT")
    _, evidence = render_math_cell_crop(PDF(), table, cell)
    assert evidence["clip_bbox"] == box(25, 25, 45, 45)
    assert capture[0][1]["coord_origin"] == "BOTTOMLEFT"


def test_overlapping_neighbor_blocks_expansion_without_erasing_source(table, capture):
    cells = table["data"]["table_cells"]
    cells[1]["bbox"]["b"] = 35
    _, evidence = render_math_cell_crop(PDF(), table, cells[4])
    assert evidence["clip_bbox"]["t"] == 30
    assert evidence["neighbor_constraints"]["t"]["basis"] == "overlapping_neighbor_no_expansion"
    assert "neighbor_text_bounds_overlap" in evidence["geometry_warnings"]


def test_edge_ink_is_a_risk_not_an_output_repair(table, monkeypatch):
    def render(*args, **kwargs):
        image = Image.new("RGB", (20, 20), "white")
        image.putpixel((0, 4), (0, 0, 0))
        return image, {}
    monkeypatch.setattr(rendering, "render_formula_crop", render)
    _, evidence = render_math_cell_crop(PDF(), table, table["data"]["table_cells"][4])
    assert evidence["ink_edges"]["edge_pixels"] == dict(left=1, top=0, right=0, bottom=0)
    assert evidence["geometry_warnings"] == ["ink_touches_crop_edge"]
    assert "transcribed_text" not in evidence


@pytest.mark.parametrize("invalid", ["outside_table", "nan", "multiple_pages", "not_member"])
def test_unreliable_target_provenance_does_not_render(table, capture, invalid):
    cell = table["data"]["table_cells"][4]
    if invalid == "outside_table":
        cell["bbox"]["l"] = -1
    elif invalid == "nan":
        cell["bbox"]["r"] = float("nan")
    elif invalid == "multiple_pages":
        table["prov"] *= 2
    else:
        cell = copy.deepcopy(cell)
        cell["text"] = "not a cell from this table"
    with pytest.raises(ValueError, match="^document_docling_invalid_provenance$"):
        render_math_cell_crop(PDF(), table, cell)
    assert not capture
