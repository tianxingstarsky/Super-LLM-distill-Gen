from copy import deepcopy

from PIL import Image
import pytest

from lib.infrastructure import docling_table_mixed_math as mixed
from lib.infrastructure.docling_math_regions import inspect_math_region


def box(l, b, r, t):
    return dict(l=l, b=b, r=r, t=t, coord_origin="BOTTOMLEFT")


@pytest.fixture
def source(monkeypatch):
    glyphs = []
    for i, char in enumerate("amount"):
        glyphs.append(dict(char_index=i, text=char, font_name="Times-Roman", font_size=10,
                           font_weight=400, origin=[20+5*i, 72], bbox=box(20+5*i, 70, 24+5*i, 78)))
    for i, char in enumerate("×106", 6):
        small = char == "6"
        glyphs.append(dict(char_index=i, text=char, font_name="CMSY10" if char == "×" else "Times-Roman",
                           font_size=6 if small else 10, font_weight=400,
                           origin=[20+5*(i-6), 57 if small else 52],
                           bbox=box(20+5*(i-6), 57 if small else 50, 24+5*(i-6), 62 if small else 58)))
    page = dict(status="ok", review_required=False, page_no=1, source_sha256="source-hash",
                width=100, height=100, glyphs=glyphs)
    cell = dict(text="amount ×106", bbox=box(18, 48, 52, 80))
    native = [dict(index=g["char_index"]+100, text=g["text"], bbox=g["bbox"]) for g in glyphs]
    lines = [dict(bbox=box(18, 68, 52, 80), source_sha256="source-hash", page_no=1, line_id="prose"),
             dict(bbox=box(18, 48, 39, 64), source_sha256="source-hash", page_no=1, line_id="math")]
    image = Image.new("RGB", (400, 400), "white")
    calls = []

    def geometry(page, proposal, outer, **kwargs):
        calls.append(proposal)
        return dict(review_required=False, bbox=proposal, char_indices=[6, 7, 8, 9],
                    pixel_evidence={"retained_glyph_ink_removed_pixels": 0})

    monkeypatch.setattr(mixed, "refine_inline_formula_geometry", geometry)
    yield page, cell, native, lines, image, calls
    image.close()


def test_only_independent_math_line_is_planned_and_prose_has_blank_separator(source):
    page, cell, native, lines, image, calls = source
    before = deepcopy(cell)
    result = mixed.plan_mixed_table_math(page, cell, lines, native, image)
    assert result["status"] == "planned" and not result["review_required"]
    assert result["native_prose_char_indices"] == list(range(6))
    assert result["candidate"]["geometry"]["char_indices"] == [6, 7, 8, 9]
    assert result["blank_separators"][0]["dark_pixels"] == 0
    assert len(calls) == 1 and cell == before


@pytest.mark.parametrize("change,reason", [
    ("changed_backend", "table_native_character_inventory_mismatch"),
    ("duplicate_native", "missing_or_ambiguous_table_native_characters"),
    ("changed_source", "unsupported_table_native_line_provenance"),
    ("same_line", "table_math_and_prose_share_native_line"),
    ("missing_line", "table_native_line_coverage_incomplete"),
    ("unsafe_crop", "table_math_line_crop_requires_review"),
    ("ink_in_gap", "table_math_prose_separator_contains_ink"),
])
def test_incomplete_or_ambiguous_evidence_stays_unpublished(source, monkeypatch, change, reason):
    page, cell, native, lines, image, calls = source
    if change == "changed_backend":
        cell["text"] += "9"
    elif change == "duplicate_native":
        native[-1]["index"] = native[-2]["index"]
    elif change == "changed_source":
        lines[1]["source_sha256"] = "different-source"
    elif change == "same_line":
        lines[:] = [dict(lines[0], bbox=cell["bbox"])]
    elif change == "missing_line":
        lines.pop(0)
    elif change == "unsafe_crop":
        monkeypatch.setattr(mixed, "refine_inline_formula_geometry", lambda *a, **kw: {"review_required": True})
    else:
        image.putpixel((100, 130), (0, 0, 0))
    before = deepcopy(cell)
    result = mixed.plan_mixed_table_math(page, cell, lines, native, image)
    assert result["reason"] == reason
    assert result["review_required"] and not result["published"]
    assert cell == before


@pytest.mark.parametrize("latex,reason", [
    (r"\times 10^{6}", None),
    (r"\times 106", "table_math_numeric_script_attachment_changed"),
    (r"\times 10_{6}", "table_math_numeric_script_attachment_changed"),
    (r"10^{6}", "table_math_source_symbols_changed"),
    (r"\times10^{9}", "table_math_source_symbols_changed"),
    (r"1\times0^{6}", "table_math_source_symbol_order_changed"),
    (r"10^{6}\times", "table_math_source_symbol_order_changed"),
])
def test_latex_ast_preserves_operator_and_numeric_script_attachment(source, latex, reason):
    page, _, native, *_ = source
    glyphs = page["glyphs"][6:]
    selection = inspect_math_region(page, box(18, 48, 39, 64))
    result = mixed.validate_table_math_structure(glyphs, latex, selection, native_chars=native[6:])
    assert result["review_required"] == bool(reason)
    if reason:
        assert reason in result["reasons"]
    assert not result["latex_modified"]


@pytest.mark.parametrize("source_text,latex", [("a+b", "b+a"), ("x-y+z", "z-y+x"),
                                               ("α+β", r"\beta+\alpha")])
def test_symbol_order_guard_rejects_general_operator_or_variable_permutations(source_text, latex):
    glyphs = [dict(char_index=i, text=char, bbox=box(i*5, 10, i*5+4, 18),
                   font_name="CMMI10", font_size=10, origin=[i*5, 10])
              for i, char in enumerate(source_text)]
    native = [dict(index=i+100, text=char) for i, char in enumerate(source_text)]
    result = mixed.validate_table_math_structure(glyphs, latex, {}, native_chars=native)
    assert result["review_required"]
    assert result["source_symbol_order"]["native_symbols"] == source_text
    assert "table_math_source_symbol_order_changed" in result["reasons"]
    correct = mixed.validate_table_math_structure(glyphs, source_text, {}, native_chars=native)
    assert not correct["review_required"]


@pytest.mark.parametrize("latex,valid", [(r"x^{2}+x_{2}", True), (r"x_{2}+x^{2}", False)])
def test_repeated_symbol_scripts_are_bound_to_specific_source_instances(source, monkeypatch, latex, valid):
    from types import SimpleNamespace
    from lib.infrastructure import docling_formula_crops
    page, cell, native, lines, image, _ = source
    glyphs = []
    for i, char in enumerate("x2+x2"):
        y = 57 if i == 1 else 47 if i == 4 else 52
        small = i in (1, 4)
        glyphs.append(dict(char_index=6+i, text=char, font_name="CMMI7" if small else "CMMI10",
                           font_size=6 if small else 10, font_weight=400,
                           origin=[20+5*i, y], bbox=box(20+5*i, y-2, 24+5*i, y+5)))
    page["glyphs"] = page["glyphs"][:6] + glyphs
    cell.update(text="amount x2+x2", bbox=box(18, 42, 52, 80))
    native[:] = native[:6] + [dict(index=g["char_index"]+100, text=g["text"], bbox=g["bbox"]) for g in glyphs]
    lines[1]["bbox"] = box(18, 42, 46, 64)
    monkeypatch.setattr(mixed, "refine_inline_formula_geometry", lambda *a, **kw: dict(
        review_required=False, bbox=lines[1]["bbox"], char_indices=[6, 7, 8, 9, 10]))
    monkeypatch.setattr(docling_formula_crops, "render_formula_crop", lambda *a, **kw: (
        Image.new("RGB", (80, 40), "white"), {}))
    prediction = dict(status="transcribed_unverified", transcribed_text=latex, raw_output=f"<s>{latex}</s>")
    result = mixed.transcribe_mixed_table_math(None, page, cell, lines, native, image,
                                              SimpleNamespace(read=lambda pixels: deepcopy(prediction)))
    assert result["status"] == ("transcribed_unverified" if valid else "review_required")
    proof = result["structure_consistency"]
    assert proof["source_numeric_script_edges"] == [(0, 1, "superscript"), (3, 4, "subscript")]
    assert proof["attachment_binding"] == "concrete_ordered_symbol_positions"
    assert result["prediction"] == prediction
    if not valid:
        assert proof["recognized_numeric_script_edges"] == [(0, 1, "subscript"), (3, 4, "superscript")]
        assert "assembly" not in result and not result["published"]


def test_unproven_non_numeric_source_script_is_held_before_render_or_ocr(source, monkeypatch):
    from types import SimpleNamespace
    from lib.infrastructure import docling_formula_crops
    page, cell, native, lines, image, _ = source
    glyphs = [dict(char_index=6+i, text=char, font_name="CMMI7" if i else "CMMI10",
                   font_size=6 if i else 10, font_weight=400,
                   origin=[20+5*i, 47 if i else 52],
                   bbox=box(20+5*i, 45 if i else 50, 24+5*i, 50 if i else 58))
              for i, char in enumerate("xi")]
    page["glyphs"] = page["glyphs"][:6] + glyphs
    cell.update(text="amount xi", bbox=box(18, 42, 52, 80))
    native[:] = native[:6] + [dict(index=g["char_index"]+100, text=g["text"], bbox=g["bbox"]) for g in glyphs]
    lines[1]["bbox"] = box(18, 42, 30, 64)
    monkeypatch.setattr(mixed, "refine_inline_formula_geometry", lambda *a, **kw: dict(
        review_required=False, bbox=lines[1]["bbox"], char_indices=[6, 7]))
    def forbidden(*a, **kw):
        raise AssertionError("unproven non-numeric script must be held before OCR")
    monkeypatch.setattr(docling_formula_crops, "render_formula_crop", forbidden)
    result = mixed.transcribe_mixed_table_math(None, page, cell, lines, native, image,
                                              SimpleNamespace(read=forbidden))
    assert result["status"] == "review_required"
    assert result["reason"] == "table_math_source_script_attachments_require_review"
    assert result["source_script_attachments"]["reasons"] == ["table_math_non_numeric_script_requires_review"]
    assert "prediction" not in result and not result["published"]


def test_same_size_shifted_source_glyph_cannot_be_flattened_to_plain_text(source):
    page, _, native, *_ = source
    glyphs = deepcopy(page["glyphs"][6:8])
    glyphs[0]["text"], glyphs[1]["text"] = "x", "i"
    glyphs[1]["origin"][1] -= 4
    native = [dict(index=100+i, text=g["text"]) for i, g in enumerate(glyphs)]
    result = mixed.validate_table_math_structure(glyphs, "xi", {}, native_chars=native)
    assert result["review_required"]
    assert "table_math_source_baseline_ambiguous" in result["reasons"]


@pytest.mark.parametrize("case,reason", [
    ("reversed_native", "table_math_native_symbol_order_disagrees"),
    ("overlapping_centers", "table_math_spatial_symbol_order_ambiguous"),
    ("missing_native", "table_math_native_symbol_order_missing"),
])
def test_order_is_never_inferred_from_ambiguous_or_missing_source_evidence(source, case, reason):
    page, _, native, *_ = source
    glyphs, native = deepcopy(page["glyphs"][6:]), deepcopy(native[6:])
    if case == "reversed_native":
        native[0]["text"], native[1]["text"] = native[1]["text"], native[0]["text"]
    elif case == "overlapping_centers":
        glyphs[-1]["bbox"] = deepcopy(glyphs[-2]["bbox"])
    else:
        native = None
    result = mixed.validate_table_math_structure(glyphs, r"\times10^{6}", {}, native_chars=native)
    assert result["review_required"] and reason in result["reasons"]


@pytest.mark.parametrize("raw,status,reason", [
    (r"\times10^{6}", "transcribed_unverified", None),
    (r"\times106", "review_required", "table_formula_consistency_requires_review"),
])
def test_recognition_changes_only_math_span_and_keeps_raw_prediction(source, monkeypatch, raw, status, reason):
    from types import SimpleNamespace
    from lib.infrastructure import docling_formula_crops
    page, cell, native, lines, image, _ = source
    before = deepcopy(cell)
    monkeypatch.setattr(docling_formula_crops, "render_formula_crop", lambda *a, **kw: (
        Image.new("RGB", (80, 40), "white"), dict(page_no=1, image_sha256="crop-hash")))
    prediction = dict(status="transcribed_unverified", transcribed_text=raw, raw_output=f"<s>{raw}</s>")
    reader = SimpleNamespace(read=lambda pixels: deepcopy(prediction))
    result = mixed.transcribe_mixed_table_math(None, page, cell, lines, native, image, reader)
    assert result["status"] == status and result["reason"] == reason
    assert result["prediction"] == prediction
    if status == "transcribed_unverified":
        assert result["text"] == "amount $" + raw + "$"
        assert len(result["assembly"]["patches"]) == 1
        assert result["assembly"]["prose_preserved"]
    assert cell == before and not result["published"]


def test_truncated_prediction_keeps_full_raw_without_assembling(source, monkeypatch):
    from types import SimpleNamespace
    from lib.infrastructure import docling_formula_crops
    page, cell, native, lines, image, _ = source
    monkeypatch.setattr(docling_formula_crops, "render_formula_crop", lambda *a, **kw: (
        Image.new("RGB", (80, 40), "white"), dict(page_no=1, image_sha256="crop-hash")))
    prediction = dict(status="review_required", transcribed_text=r"\times10",
                      raw_output=r"<s>\times10", reason="document_docling_formula_truncated")
    result = mixed.transcribe_mixed_table_math(None, page, cell, lines, native, image,
                                              SimpleNamespace(read=lambda pixels: prediction))
    assert result["reason"] == "document_docling_formula_truncated"
    assert result["prediction"] == prediction and "assembly" not in result
    assert not result["published"]


def test_model_failure_is_not_disguised_as_bad_source_evidence(source, monkeypatch):
    from types import SimpleNamespace
    from lib.infrastructure import docling_formula_crops
    page, cell, native, lines, image, _ = source
    monkeypatch.setattr(docling_formula_crops, "render_formula_crop", lambda *a, **kw: (
        Image.new("RGB", (80, 40), "white"), {}))
    def failed(pixels):
        raise ValueError("inference failure")
    with pytest.raises(ValueError, match="inference failure"):
        mixed.transcribe_mixed_table_math(None, page, cell, lines, native, image, SimpleNamespace(read=failed))


def test_existing_docling_table_serializer_keeps_formula_in_one_header_cell():
    pytest.importorskip("docling_core")
    from docling_core.types.doc import DoclingDocument, TableData, TableCell
    document = DoclingDocument(name="table-export")
    text = r"amount $\times 1 0 ^ { 6 }$"
    table = document.add_table(data=TableData(num_rows=2, num_cols=1, table_cells=[
        TableCell(text=text, start_row_offset_idx=0, end_row_offset_idx=1,
                  start_col_offset_idx=0, end_col_offset_idx=1, column_header=True),
        TableCell(text="31", start_row_offset_idx=1, end_row_offset_idx=2,
                  start_col_offset_idx=0, end_col_offset_idx=1),
    ]))
    markdown = table.export_to_markdown(doc=document)
    html = table.export_to_html(doc=document)
    assert text in markdown and text in html
    assert len(markdown.splitlines()) == 3
    assert html.count("<th>") == 1 and html.count("<td>") == 1
