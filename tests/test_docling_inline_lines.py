"""Native lines are detector context, never inferred equation continuation."""
from copy import deepcopy
import hashlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from lib.infrastructure.docling_inline_lines import (
    read_native_line_pages,
    select_inline_line_views,
)


def box(l, b, r, t):
    return dict(l=l, b=b, r=r, t=t, coord_origin="BOTTOMLEFT")


def line(index, bbox, **kwargs):
    return dict(page_no=1, source_sha256="pdf-hash", line_index=index,
                line_id=f"page-1:line-{index}", bbox=bbox,
                native_text=f"native row {index}", text_direction="left_to_right",
                from_ocr=False, review_required=False, **kwargs)


def glyph(index, text, bbox, *, size=10, origin=None, font="CambriaMath"):
    return dict(char_index=index, text=text, bbox=bbox, font_size=size,
                origin=origin or [bbox["l"], bbox["b"]], font_name=font,
                generated=False)


PARAGRAPH = box(0, 0, 100, 100)


def assert_review(result, reason, paragraph=PARAGRAPH):
    assert len(result) == 1
    assert result[0]["bbox"] == paragraph
    assert result[0]["review_required"]
    assert reason in result[0]["review_reasons"]
    assert not result[0]["automatic_text_replacement_allowed"]


def test_independent_math_rows_remain_physical_records_without_joined_text():
    lines = [line(8, box(10, 60, 40, 70)), line(2, box(10, 20, 40, 30))]
    glyphs = [glyph(0, "x", box(12, 61, 20, 69)),
              glyph(1, "y", box(12, 21, 20, 29))]
    before = deepcopy((lines, glyphs))
    views = select_inline_line_views(lines, PARAGRAPH, glyphs)
    assert len(views) == 2
    assert [v["source_line_indices"] for v in views] == [[8], [2]]
    assert [v["pdfium_char_indices"] for v in views] == [[0], [1]]
    assert [v["source_lines"] for v in views] == [
        [{"line_id": "page-1:line-8", "native_text": "native row 8"}],
        [{"line_id": "page-1:line-2", "native_text": "native row 2"}],
    ]
    for view in views:
        assert view["physical_only"]
        assert not view["continuation_inferred"]
        assert not view["formula_completeness_verified"]
        assert not view["automatic_text_replacement_allowed"]
        assert not view["review_required"]
    assert before == (lines, glyphs)


def test_superscript_outside_native_line_is_closed_by_real_origin_and_size():
    lines = [line(0, box(10, 40, 20, 49))]
    glyphs = [glyph(0, "x", box(11, 40, 18, 48)),
              glyph(1, "2", box(18, 50, 22, 55), size=7, origin=[18, 46])]
    views = select_inline_line_views(lines, PARAGRAPH, glyphs)
    assert len(views) == 1 and not views[0]["review_required"]
    assert views[0]["bbox"] == box(10, 40, 22, 55)
    assert views[0]["pdfium_char_indices"] == [0, 1]


def test_roman_script_label_is_retained_without_math_font_assumption():
    lines = [line(0, box(10, 40, 20, 49))]
    glyphs = [glyph(0, "d", box(11, 40, 18, 48)),
              glyph(1, "m", box(19, 34, 23, 39), size=7, font="Times-Roman"),
              glyph(2, "o", box(23, 34, 27, 39), size=7, font="Times-Roman")]
    views = select_inline_line_views(lines, PARAGRAPH, glyphs)
    assert not views[0]["review_required"]
    assert views[0]["pdfium_char_indices"] == [0, 1, 2]
    assert views[0]["bbox"]["b"] == 34


def test_tall_radical_overlapping_two_native_rows_preserves_full_extent():
    lines = [line(0, box(10, 50, 40, 58)), line(1, box(10, 37, 40, 45))]
    glyphs = [glyph(0, "√", box(10, 37, 18, 58), size=20),
              glyph(1, "a", box(20, 50, 30, 58)),
              glyph(2, "b", box(20, 37, 30, 45))]
    views = select_inline_line_views(lines, PARAGRAPH, glyphs)
    assert len(views) == 1
    assert views[0]["source_line_indices"] == [0, 1]
    assert views[0]["pdfium_char_indices"] == [0, 1, 2]
    assert views[0]["bbox"] == box(10, 37, 40, 58)
    assert views[0]["grouping_evidence"] == ["shared_or_intersecting_glyph_extent"]
    assert not views[0]["continuation_inferred"]


def test_close_nested_fraction_rows_share_context_but_not_semantics():
    lines = [line(0, box(20, 50, 30, 58)), line(1, box(10, 38, 40, 46))]
    glyphs = [glyph(0, "a", box(20, 50, 29, 58)),
              glyph(1, "b", box(10, 38, 20, 46)),
              glyph(2, "c", box(30, 38, 40, 46))]
    views = select_inline_line_views(lines, PARAGRAPH, glyphs)
    assert len(views) == 1
    assert views[0]["bbox"] == box(10, 38, 40, 58)
    assert views[0]["grouping_evidence"] == ["close_nested_math_rows"]
    assert not views[0]["formula_completeness_verified"]
    assert views[0]["source_lines"][0]["native_text"] == "native row 0"
    assert len(views[0]["source_lines"]) == 2


def test_close_ordinary_prose_rows_do_not_use_math_fraction_grouping():
    lines = [line(0, box(20, 50, 30, 58)), line(1, box(10, 38, 40, 46))]
    glyphs = [glyph(0, "a", box(20, 50, 29, 58), font="Times-Roman"),
              glyph(1, "b", box(10, 38, 20, 46), font="Times-Roman")]
    views = select_inline_line_views(lines, PARAGRAPH, glyphs)
    assert len(views) == 2
    assert all(not view["review_required"] for view in views)


def test_small_parser_boundary_discrepancy_is_preserved_and_quantified():
    paragraph = box(10, 40, 20, 49)
    glyphs = [glyph(0, "x", box(10, 40, 20, 49.1))]
    views = select_inline_line_views([line(0, paragraph)], paragraph, glyphs)
    assert not views[0]["review_required"]
    assert views[0]["bbox"]["t"] == 49.1
    assert views[0]["paragraph_boundary_overflow_points"]["top"] == pytest.approx(.1)
    assert views[0]["paragraph_boundary_tolerance_points"] == .25


def test_paragraph_boundary_cut_preserves_original_in_review():
    paragraph = box(10, 40, 20, 49)
    glyphs = [glyph(0, "x", box(10, 40, 20, 50))]
    assert_review(select_inline_line_views([line(0, paragraph)], paragraph, glyphs),
                  "paragraph_boundary_cuts_pdf_glyph", paragraph)


def test_visible_glyph_without_line_or_script_proof_blocks_all_line_views():
    lines = [line(0, box(10, 40, 30, 49))]
    glyphs = [glyph(0, "a", box(10, 40, 20, 49)),
              glyph(1, "?", box(70, 80, 80, 89))]
    assert_review(select_inline_line_views(lines, PARAGRAPH, glyphs),
                  "native_line_glyph_coverage_incomplete")


@pytest.mark.parametrize("change,reason", [
    ({"from_ocr": True}, "unsupported_native_line_evidence"),
    ({"review_required": True}, "unsupported_native_line_evidence"),
    ({"text_direction": "right_to_left"}, "unsupported_native_line_evidence"),
    ({"source_sha256": None}, "missing_native_line_provenance"),
    ({"page_no": 0}, "missing_native_line_provenance"),
])
def test_unsupported_or_missing_native_provenance_is_reviewed(change, reason):
    row = line(0, box(10, 40, 30, 49))
    row.update(change)
    assert_review(select_inline_line_views([row], PARAGRAPH,
                  [glyph(0, "x", box(11, 40, 20, 48))]), reason)


def test_different_pages_never_share_a_paragraph_view():
    rows = [line(0, box(10, 40, 30, 49)), line(1, box(10, 20, 30, 29))]
    rows[1]["page_no"] = 2
    assert_review(select_inline_line_views(rows, PARAGRAPH, []),
                  "inconsistent_native_line_provenance")


def test_duplicate_line_ids_are_not_silently_merged():
    row = line(0, box(10, 40, 30, 49))
    assert_review(select_inline_line_views([row, deepcopy(row)], PARAGRAPH, []),
                  "duplicate_native_line_ids")


def test_native_row_without_pdfium_glyph_evidence_is_reviewed():
    rows = [line(0, box(10, 40, 30, 49)), line(1, box(10, 20, 30, 29))]
    assert_review(select_inline_line_views(rows, PARAGRAPH,
                  [glyph(0, "x", box(11, 40, 20, 48))]),
                  "native_line_without_pdf_glyphs")


def test_generated_nonspace_glyph_is_not_used_as_geometry_proof():
    g = glyph(0, "x", box(11, 40, 20, 48))
    g["generated"] = True
    assert_review(select_inline_line_views([line(0, box(10, 40, 30, 49))],
                  PARAGRAPH, [g]), "generated_visible_pdf_glyph")


def test_duplicate_pdfium_character_ids_are_rejected():
    g = glyph(0, "x", box(11, 40, 20, 48))
    assert_review(select_inline_line_views([line(0, box(10, 40, 30, 49))],
                  PARAGRAPH, [g, deepcopy(g)]), "duplicate_pdf_character_ids")


def test_missing_native_lines_or_pdfium_glyphs_preserve_source_bbox():
    assert_review(select_inline_line_views([], PARAGRAPH, []),
                  "no_native_lines_for_paragraph")
    assert_review(select_inline_line_views([line(0, box(10, 40, 30, 49))],
                  PARAGRAPH, []), "no_pdf_glyphs_for_native_lines")


def test_non_bottomleft_inputs_are_rejected():
    paragraph = dict(PARAGRAPH, coord_origin="TOPLEFT")
    with pytest.raises(ValueError, match="coordinate_space"):
        select_inline_line_views([], paragraph, [])


@pytest.mark.parametrize("angle,crop_left,line_angle,review", [
    (0, 0, 0, False), (90, 0, 0, True), (0, 2, 0, True), (0, 0, 1, True),
])
def test_official_native_api_materializes_lines_and_retains_provenance(
        monkeypatch, angle, crop_left, line_angle, review):
    class Bbox(SimpleNamespace):
        def to_bottom_left_origin(self, height):
            assert height == 100
            return self

        def model_dump(self, **kwargs):
            return box(self.l, self.b, self.r, self.t)

    class Rect:
        angle_360 = line_angle

        def to_bounding_box(self):
            return Bbox(l=10, b=40, r=30, t=49)

    events = []
    cell = SimpleNamespace(index=3, rect=Rect(), text="x + prose", rendering_mode=0,
                           text_direction=SimpleNamespace(value="left_to_right"), from_ocr=False)
    page = SimpleNamespace(
        textline_cells=[cell, SimpleNamespace(rendering_mode=3, text="hidden")],
        dimension=SimpleNamespace(width=100, height=100, angle=angle,
                                  crop_bbox=Bbox(l=crop_left, b=0, r=100, t=100)))

    class Parser:
        def __init__(self, parser_config):
            content = parser_config.page_content_config
            assert content.line_cells_content_level == "materialize"
            assert content.char_cells_content_level == content.word_cells_content_level == "skip"
            assert parser_config.render_config is None

        def load(self, stream):
            assert stream.read() == b"pdf bytes"
            return "source-key"

        def iterate_results(self):
            yield SimpleNamespace(page_number=1, get_page=lambda: page)

        def has_tasks(self):
            return False

        def unload(self, key):
            events.append(("unload", key))

    module = ModuleType("docling_parse.pdf_parser")
    module.ContentConfig = SimpleNamespace
    module.ThreadedPdfParserConfig = SimpleNamespace
    module.ContentLevel = SimpleNamespace(SKIP="skip", COMPUTE_AND_MATERIALIZE="materialize")
    module.DoclingThreadedPdfParser = Parser
    monkeypatch.setitem(sys.modules, "docling_parse.pdf_parser", module)
    result = read_native_line_pages(b"pdf bytes")
    record = result[1][0]
    assert len(result[1]) == 1
    assert record["source_sha256"] == hashlib.sha256(b"pdf bytes").hexdigest()
    assert record["line_id"] == "page-1:line-3"
    assert record["bbox"] == box(10, 40, 30, 49)
    assert record["native_text"] == "x + prose"
    assert record["review_required"] is review
    assert "font_name" not in record
    assert events == [("unload", "source-key")]
