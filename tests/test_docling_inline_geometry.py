"""Crop repairs must conserve glyphs and account for actual changed pixels."""
from copy import deepcopy
from io import BytesIO

from PIL import Image, ImageDraw
import pytest

from lib.infrastructure.docling_inline_geometry import (
    group_inline_formula_fragments, refine_inline_formula_geometry,
    render_inline_page_evidence,
)


def glyph(index, text, x, y=24, *, width=4, height=8, size=10,
          font="CMMI10", baseline=None):
    return {"char_index": index, "text": text, "font_name": font, "font_size": size,
            "origin": [x, y if baseline is None else baseline], "generated": False,
            "bbox": {"l": x, "b": y, "r": x + width, "t": y + height}}


def page(*glyphs):
    return {"page_no": 2, "width": 240, "height": 120, "glyphs": list(glyphs),
            "source_sha256": "same-source", "status": "ok", "review_required": False}


def raster(data):
    image = Image.new("RGB", (960, 480), "white")
    draw = ImageDraw.Draw(image)
    for g in data["glyphs"]:
        b = g["bbox"]
        draw.rectangle((int(b["l"] * 4) + 1, int((120 - b["t"]) * 4) + 1,
                        int(b["r"] * 4) - 2, int((120 - b["b"]) * 4) - 2), fill="black")
    return image


def refine(data, box, **kwargs):
    image = kwargs.pop("page_image", None)
    return refine_inline_formula_geometry(data, box, (0, 0, 240, 120),
                                          page_image=image or raster(data),
                                          source_ref="#/texts/1", **kwargs)


def assert_retained_original(result):
    assert result["review_required"]
    assert result["bbox"] == result["detector_bbox"]
    assert not result["automatic_text_replacement_allowed"]


@pytest.mark.parametrize("prefix", ["tensor", "alpha", "zz"])
def test_recover_tight_math_identifier_across_connector_font_change(prefix):
    text = prefix + "_index"
    data = page(*(glyph(i, c, 20 + i * 4.5, font="Helvetica" if c == "_" else "CMMI10")
                  for i, c in enumerate(text)))
    original = (20 + len(prefix) * 4.5 + 1, 22, 22 + len(text) * 4.5, 35)
    before = deepcopy(data)
    result = refine(data, original)
    assert not result["review_required"], result["review_reasons"]
    assert result["bbox"]["l"] < original[0]
    assert result["added_char_indices"] == list(range(len(prefix)))
    assert result["char_indices"] == list(range(len(text)))
    pixels = result["pixel_evidence"]
    assert pixels["added_ink_pixels"] > 100
    assert pixels["removed_ink_pixels"] == 0
    assert pixels["unassigned_added_ink_pixels"] == 0
    assert all(count > 0 for count in pixels["added_glyph_ink_pixels"].values())
    assert not any(pixels["proposed_edge_ink_pixels"].values())
    assert data == before


def test_math_identifier_does_not_jump_over_prose_or_large_whitespace():
    data = page(glyph(0, "x", 10), glyph(1, "a", 19, font="Helvetica"),
                glyph(2, "y", 25), glyph(3, "z", 30))
    result = refine(data, (24, 22, 36, 35))
    assert not result["review_required"]
    assert result["char_indices"] == [2, 3]
    assert result["added_char_indices"] == []


def detached_line_page():
    return page(glyph(0, "X", 20),
                glyph(1, "3", 25, y=30, width=3, height=6, size=7, font="CMR7"),
                glyph(2, "p", 23, y=40.5, height=8.5, baseline=43, font="Helvetica"),
                glyph(3, "g", 35, y=40.5, height=8.5, baseline=43, font="Helvetica"))


def test_remove_only_partial_detached_prose_ink_and_keep_superscript():
    data = detached_line_page()
    result = refine(data, (18, 22, 42, 41.5))
    assert not result["review_required"], result["review_reasons"]
    assert result["excluded_char_indices"] == [2, 3]
    assert result["char_indices"] == [0, 1]
    assert result["bbox"]["t"] > data["glyphs"][1]["bbox"]["t"]
    pixels = result["pixel_evidence"]
    assert pixels["removed_ink_pixels"] > 0
    assert pixels["retained_glyph_ink_removed_pixels"] == 0
    assert pixels["unassigned_removed_ink_pixels"] == 0
    assert pixels["original_ink_pixels"] - pixels["proposed_ink_pixels"] == pixels["removed_ink_pixels"]


def test_fraction_rule_or_unmapped_graphics_prevent_vertical_trimming():
    data = detached_line_page()
    image = raster(data)
    ImageDraw.Draw(image).line((76, 324, 165, 324), fill="black", width=2)
    result = refine(data, (18, 22, 42, 41.5), page_image=image)
    assert_retained_original(result)
    assert "unassigned_original_ink_prevents_trimming" in result["review_reasons"]
    assert result["pixel_evidence"]["unassigned_removed_ink_pixels"] > 0


def test_fully_inside_roman_numerator_is_never_removed_as_prose_line():
    data = page(glyph(0, "x", 24, y=20),
                glyph(1, "a", 21, y=38, font="CMR10"),
                glyph(2, "b", 26, y=38, font="CMR10"))
    result = refine(data, (18, 18, 33, 49))
    assert_retained_original(result)
    assert result["excluded_char_indices"] == []
    assert result["char_indices"] == [0, 1, 2]


@pytest.mark.parametrize("small", ["2", "k", "n"])
def test_clipped_superscript_is_expanded_and_preserved(small):
    data = page(glyph(0, "x", 20), glyph(1, small, 25, y=30, size=6, height=7,
                                       font="CMR6"))
    result = refine(data, (18, 22, 30, 34))
    assert not result["review_required"], result["review_reasons"]
    assert result["char_indices"] == [0, 1]
    assert result["bbox"]["t"] > 37
    assert result["pixel_evidence"]["added_ink_pixels"] > 0
    assert result["pixel_evidence"]["removed_ink_pixels"] == 0


def test_entirely_missed_script_is_not_silently_certified_as_complete():
    data = page(glyph(0, "x", 20), glyph(1, "2", 25, y=32, size=6, height=6, font="CMR6"))
    result = refine(data, (18, 22, 24.5, 34))
    assert_retained_original(result)
    assert "nearby_unselected_script_requires_review" in result["review_reasons"]
    assert result["char_indices"] == [0]


def test_nested_radical_and_fraction_ink_remain_intact():
    data = page(glyph(0, "√", 20, y=20, height=25, width=4, font="CMEX10"),
                glyph(1, "√", 27, y=20, height=18, width=4, font="CMEX10"),
                glyph(2, "1", 34, y=31, size=6, height=5, font="CMR6"),
                glyph(3, "x", 34, y=22, size=6, height=5))
    image = raster(data)
    draw = ImageDraw.Draw(image)
    draw.line((88, 302, 170, 302), fill="black", width=2)
    draw.line((116, 330, 166, 330), fill="black", width=2)
    draw.line((132, 363, 160, 363), fill="black", width=2)
    result = refine(data, (18, 18, 44, 47), page_image=image)
    assert not result["review_required"]
    assert result["char_indices"] == [0, 1, 2, 3]
    assert result["excluded_char_indices"] == []
    assert result["pixel_evidence"]["unassigned_original_ink_pixels"] > 0
    assert result["pixel_evidence"]["removed_ink_pixels"] == 0


@pytest.mark.parametrize("prose", ["if", "to", "as", "设"])
def test_same_line_short_prose_and_closing_delimiter_preserve_source_box(prose):
    glyphs = [glyph(i, c, 20 + 5 * i, font="Helvetica") for i, c in enumerate(prose)]
    start = 20 + 5 * len(prose)
    glyphs.extend([glyph(len(glyphs), "−", start, font="CMSY10"),
                   glyph(len(glyphs) + 1, "∞", start + 5, font="CMSY10"),
                   glyph(len(glyphs) + 2, ")", start + 10, font="Helvetica")])
    result = refine(page(*glyphs), (18, 22, start + 16, 35))
    assert_retained_original(result)
    assert "ordinary_text_formula_boundary_ambiguous" in result["review_reasons"]
    assert "ordinary_delimiter_boundary_ambiguous" in result["review_reasons"]
    assert result["excluded_char_indices"] == []


def test_narrow_paragraph_cannot_authorize_extension_into_neighbor():
    data = page(glyph(0, "x", 20), glyph(1, "y", 24.5))
    result = refine_inline_formula_geometry(data, (23, 22, 31, 35), (23, 20, 40, 38),
                                            page_image=raster(data))
    assert_retained_original(result)
    assert "glyph_extension_outside_source_paragraph" in result["review_reasons"]


def test_added_region_with_ink_not_explained_by_glyphs_requires_review():
    data = page(glyph(0, "a", 20), glyph(1, "b", 24.5), glyph(2, "c", 29))
    image = raster(data)
    ImageDraw.Draw(image).rectangle((80, 342, 88, 350), fill="black")
    result = refine(data, (28, 22, 35, 35), page_image=image)
    assert_retained_original(result)
    assert "unassigned_ink_in_extension" in result["review_reasons"]


def test_raster_is_required_even_when_font_geometry_is_plausible():
    data = page(glyph(0, "x", 20))
    result = refine_inline_formula_geometry(data, (18, 22, 27, 35), (0, 0, 240, 120))
    assert_retained_original(result)
    assert result["review_reasons"] == ["full_page_raster_required"]


@pytest.mark.parametrize("mutation,reason", [
    ("baseline", "missing_pdf_glyph_baseline"),
    ("generated", "generated_visible_pdf_glyph"),
    ("rotation", "unsupported_pdf_coordinate_space"),
    ("crop_origin", "unsupported_pdf_coordinate_space"),
    ("duplicate", "duplicate_pdf_character_ids"),
])
def test_unsupported_or_unreliable_pdf_evidence_requires_review(mutation, reason):
    data = page(glyph(0, "x", 20))
    if mutation == "baseline":
        data["glyphs"][0]["origin"] = None
    elif mutation == "generated":
        data["glyphs"][0]["generated"] = True
    elif mutation == "rotation":
        data["coordinate_space"] = {"page_rotation": 90}
    elif mutation == "crop_origin":
        data["coordinate_space"] = {"crop_bbox": [5, 0, 240, 120]}
    else:
        data["glyphs"].append(deepcopy(data["glyphs"][0]))
    result = refine(data, (18, 22, 27, 35))
    assert_retained_original(result)
    assert reason in result["review_reasons"]


def test_wrong_raster_dimensions_and_empty_raster_do_not_pass():
    data = page(glyph(0, "x", 20))
    for image, reason in [(Image.new("RGB", (200, 200), "white"), "inline_raster_dimensions_mismatch"),
                          (Image.new("RGB", (960, 480), "white"), "empty_detector_raster")]:
        result = refine(data, (18, 22, 27, 35), page_image=image)
        assert_retained_original(result)
        assert reason in result["review_reasons"]


def test_multiline_fragments_group_without_merging_or_continuation():
    a = {"page_no": 2, "source_ref": "#/texts/1", "fragment_id": "a",
         "bbox": {"l": 10, "b": 30, "r": 20, "t": 40}, "review_required": False}
    b = dict(a, fragment_id="b", bbox={"l": 50, "b": 10, "r": 60, "t": 20})
    c = dict(a, page_no=3, fragment_id="c")
    d = dict(a, source_ref="#/texts/2", fragment_id="d")
    before = deepcopy([a, b, c, d])
    groups = group_inline_formula_fragments([a, b, c, d])
    assert len(groups) == 3
    assert groups[0]["fragments"] == [a, b]
    assert "bbox" not in groups[0] and "text" not in groups[0]
    assert not groups[0]["continuation_inferred"]
    assert not groups[0]["automatic_text_replacement_allowed"]
    groups[0]["fragments"][0]["bbox"]["l"] = 999
    assert [a, b, c, d] == before
    unresolved = group_inline_formula_fragments([{"page_no": 1}, {"page_no": 1}])
    assert len(unresolved) == 2 and all(g["review_required"] for g in unresolved)


def make_pdf(*, rotate=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    sheet = writer.add_blank_page(width=240, height=120)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    sheet[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 40 Td (1=2) Tj /F1 7 Tf 5 Ts (3) Tj ET")
    sheet[NameObject("/Contents")] = writer._add_object(stream)
    if rotate:
        sheet.rotate(90)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_actual_pdfium_glyphs_and_raster_share_source_and_preserve_raised_digit():
    pytest.importorskip("pypdfium2")
    data, image = render_inline_page_evidence(make_pdf(), 1)
    try:
        visible = [g for g in data["glyphs"] if g["text"].strip()]
        assert "".join(g["text"] for g in visible) == "1=23"
        assert visible[-1]["origin"][1] > visible[0]["origin"][1]
        assert len(data["source_sha256"]) == 64
        result = refine_inline_formula_geometry(data, (18, 38, 46, 47),
                                               (10, 30, 60, 60), page_image=image)
        assert not result["review_required"], result["review_reasons"]
        assert result["bbox"]["t"] >= visible[-1]["bbox"]["t"]
        assert result["pixel_evidence"]["added_ink_pixels"] > 0
        assert result["pixel_evidence"]["removed_ink_pixels"] == 0
        assert result["char_indices"] == [g["char_index"] for g in visible]
    finally:
        image.close()


def test_render_rejects_rotated_page_and_invalid_dpi_without_guessing_transform():
    data, image = render_inline_page_evidence(make_pdf(rotate=True), 1)
    assert data["review_required"] and image is None
    for dpi in [0, -1, float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="invalid_render_dpi"):
            render_inline_page_evidence(make_pdf(), 1, dpi=dpi)
