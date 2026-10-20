"""Crop coordinates and bounded local formula transfer; no model API calls."""
import pytest

from lib.infrastructure.docling_formula_crops import prediction_record, render_formula_crop, numeric_glyphs_match


def test_keeps_formula_syntax_and_internal_whitespace():
    latex = r"l r a t e = d _ { m o d e l } ^ { - 0 . 5 } \cdot s t e p \_ n u m"
    raw = f"<s>{latex}</s>"
    record = prediction_record(latex, raw, [0,13,2], eos_token_id=2)
    assert record['transcribed_text'] == latex
    assert record['raw_output'] == raw
    assert record['status'] == 'transcribed_unverified'


@pytest.mark.parametrize("tokens", [[], [0], [0,13], [0]+[13]*768])
def test_does_not_publish_incomplete_predictions(tokens):
    record = prediction_record('x', '<s>x', tokens, eos_token_id=2)
    assert record['status'] == 'review_required'
    assert record['reason'] == 'document_docling_formula_truncated'
    assert record['raw_output'] == '<s>x'


@pytest.mark.parametrize('text,tokens', [('',[0,2]),('x',[0,13,3,2])])
def test_empty_or_unknown_symbols_need_review(text,tokens):
    record=prediction_record(text,text,tokens,eos_token_id=2,unk_token_id=3)
    assert record['status']=='review_required'
    assert record['reason']=='document_docling_formula_undecoded'


def test_changed_table_numbers_are_rejected_without_patching_the_model_output():
    assert numeric_glyphs_match('3.3 · 10¹⁸',r'3 . 3 \cdot 1 0 ^ { 1 8 }')
    assert not numeric_glyphs_match('3.3 · 10¹⁸',r'3 . 33 \cdot 1 0 ^ { 1 8 }')
    assert not numeric_glyphs_match('3.3 · 10¹⁸',r'3 . 3 \cdot 1 0 ^ { 1 9 }')


def test_real_pdf_crop_has_correct_page_and_clipped_source_coordinates(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    pytest.importorskip("docling")
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=100)
    writer.add_blank_page(width=300, height=150)
    path = tmp_path / "crop.pdf"
    writer.write(path)
    pdf = pdfium.PdfDocument(path)
    try:
        image, evidence = render_formula_crop(pdf, 2, {"l":0,"t":0,"r":40,"b":20,"coord_origin":"TOPLEFT"}, dpi=144)
        assert evidence["page_no"] == 2
        assert evidence["crop_bbox"] == {"l":0.0,"t":150.0,"r":42.0,"b":128.0,"coord_origin":"BOTTOMLEFT"}
        assert image.size == (84, 44)
        assert len(evidence["image_sha256"]) == 64
        with pytest.raises(ValueError, match="invalid_provenance"):
            render_formula_crop(pdf, 3, {"l":0,"t":0,"r":40,"b":20,"coord_origin":"TOPLEFT"})
    finally:
        pdf.close()


@pytest.fixture
def enrichment(monkeypatch):
    from types import SimpleNamespace as NS
    import pypdfium2
    from lib.infrastructure import docling_formula_crops as crops, docling_math_regions as regions
    from lib.infrastructure import docling_math_cell_crops as cells
    box = NS(model_dump=lambda **kw: {"l": 1, "t": 5, "r": 4, "b": 1, "coord_origin": "BOTTOMLEFT"})
    prov = NS(page_no=1, bbox=box)
    formula = NS(label="formula", text="backend formula", prov=[prov], self_ref="#/texts/0")
    cell = NS(text="Accuracy for x", bbox=box, start_row_offset_idx=0, start_col_offset_idx=0)
    table = NS(prov=[prov], self_ref="#/tables/0", data=NS(table_cells=[cell]))
    document = NS(texts=[formula], tables=[table])
    calls = []
    def read(image):
        calls.append(image)
        return prediction_record("x", "<s>x</s>", [0, 13, 2], eos_token_id=2)
    monkeypatch.setattr(crops, "LocalFormulaReader", lambda _: NS(read=read))
    monkeypatch.setattr(pypdfium2, "PdfDocument", lambda _: NS(close=lambda: None))
    monkeypatch.setattr(crops, "render_formula_crop", lambda *a, **kw: ("display", {}))
    monkeypatch.setattr(cells, "render_math_cell_crop", lambda *a, **kw: ("cell", {}))
    monkeypatch.setattr(regions, "extract_pdf_page_glyphs", lambda *a: {})
    monkeypatch.setattr(regions, "inspect_math_region", lambda *a: {
        "contains_math": False, "review_required": True, "reason": "mixed_prose_math"})
    return NS(crops=crops, cells=cells, regions=regions, document=document,
              formula=formula, cell=cell, calls=calls)


def test_mixed_prose_is_retained_and_review_evidence_survives(enrichment):
    e = enrichment
    evidence = e.crops.enrich_formulas(e.document, "local.pdf", "models")
    assert e.formula.text == "x"
    assert e.cell.text == "Accuracy for x"
    assert e.calls == ["display"]
    assert evidence["#/tables/0"]["status"] == "review_required"
    assert evidence["#/tables/0"]["cells"][0]["reason"] == "mixed_prose_math"


def test_mixed_table_expansion_publishes_only_a_proven_atomic_proposal(enrichment, monkeypatch):
    from lib.infrastructure import docling_inline_assembly, docling_inline_lines, docling_inline_geometry
    from lib.infrastructure import docling_table_mixed_math, docling_inline_routing, docling_inline_formulas
    from PIL import Image
    e = enrichment
    original_bbox = e.cell.bbox
    e.cell.model_dump = lambda **kwargs: dict(text=e.cell.text, bbox={}, row_span=1, col_span=1)
    monkeypatch.setattr(e.regions, "inspect_math_region", lambda *a: {
        "contains_math": False, "review_required": True, "status": "mixed_prose_math",
        "reason": "mixed_prose_math_requires_boundary_review"})
    monkeypatch.setattr(docling_inline_assembly, "read_native_character_pages", lambda *a: {1: ["native-chars"]})
    monkeypatch.setattr(docling_inline_lines, "read_native_line_pages", lambda *a: {1: ["native-lines"]})
    monkeypatch.setattr(docling_inline_geometry, "render_inline_page_evidence", lambda *a: (
        dict(source_sha256="source-hash"), Image.new("RGB", (10, 10), "white")))
    observed = []
    def transcribe(pdf, page, cell, lines, chars, image, reader):
        observed.append((page, cell, lines, chars))
        return dict(status="transcribed_unverified", review_required=False, text="Accuracy for $x$", published=False)
    monkeypatch.setattr(docling_table_mixed_math, "transcribe_mixed_table_math", transcribe)
    monkeypatch.setattr(docling_inline_routing, "build_inline_route_map", lambda *a: {})
    monkeypatch.setattr(docling_inline_formulas, "enrich_inline_formulas", lambda *a, **kw: {})
    evidence = e.crops.enrich_formulas(e.document, "local.pdf", "models", inline_config={"enabled": True})
    assert e.cell.text == "Accuracy for $x$" and e.cell.bbox is original_bbox
    assert e.cell.start_row_offset_idx == 0 and e.cell.start_col_offset_idx == 0
    proof = evidence["#/tables/0"]["cells"][0]
    assert proof["backend_text"] == "Accuracy for x"
    assert proof["selection"]["status"] == "mixed_prose_math"
    assert proof["mixed_math"]["published"]
    assert observed[0][0]["source_sha256"] == "source-hash"


def test_mixed_table_raster_cache_closes_previous_page_before_loading_next(enrichment, monkeypatch):
    from types import SimpleNamespace as NS
    from lib.infrastructure import docling_inline_assembly, docling_inline_lines, docling_inline_geometry
    from lib.infrastructure import docling_table_mixed_math, docling_inline_routing, docling_inline_formulas
    e = enrichment
    e.cell.model_dump = lambda **kwargs: dict(text=e.cell.text, bbox={})
    second = NS(text="other mixed math", bbox=e.cell.bbox, start_row_offset_idx=0,
                start_col_offset_idx=0, model_dump=lambda **kwargs: dict(text="other mixed math", bbox={}))
    e.document.tables.append(NS(prov=[NS(page_no=2, bbox=e.document.tables[0].prov[0].bbox)],
                                self_ref="#/tables/1", data=NS(table_cells=[second])))
    monkeypatch.setattr(e.regions, "inspect_math_region", lambda *a: {
        "contains_math": False, "review_required": True, "status": "mixed_prose_math"})
    reads, events = [], []
    def chars(*a):
        reads.append("chars")
        return {1: [], 2: []}
    def lines(*a):
        reads.append("lines")
        return {1: [], 2: []}
    def render(source, page):
        events.append(("render", page))
        return dict(page_no=page), NS(close=lambda: events.append(("close", page)))
    def transcribe(pdf, page, *a):
        events.append(("transcribe", page["page_no"]))
        return dict(status="review_required", review_required=True, reason="review", published=False)
    monkeypatch.setattr(docling_inline_assembly, "read_native_character_pages", chars)
    monkeypatch.setattr(docling_inline_lines, "read_native_line_pages", lines)
    monkeypatch.setattr(docling_inline_geometry, "render_inline_page_evidence", render)
    monkeypatch.setattr(docling_table_mixed_math, "transcribe_mixed_table_math", transcribe)
    monkeypatch.setattr(docling_inline_routing, "build_inline_route_map", lambda *a: {})
    monkeypatch.setattr(docling_inline_formulas, "enrich_inline_formulas", lambda *a, **kw: {})
    e.crops.enrich_formulas(e.document, "local.pdf", "models", inline_config={"enabled": True})
    assert events == [("render", 1), ("transcribe", 1), ("close", 1),
                      ("render", 2), ("transcribe", 2), ("close", 2)]
    assert reads == ["chars", "lines"]


def test_mixed_display_probe_cannot_publish_whole_box_latex_before_partition(enrichment, monkeypatch):
    from lib.infrastructure import docling_inline_routing as routing, docling_inline_formulas as inline
    e = enrichment
    route = {"probe_required": True, "allow_whole_formula_ocr": False}
    monkeypatch.setattr(routing, "build_inline_route_map",
                        lambda document, path: {e.formula.self_ref: route})
    received = []
    def probe(document, path, config, model, *, routes):
        received.append((document.texts[0].text, routes))
        return {}
    monkeypatch.setattr(inline, "enrich_inline_formulas", probe)
    evidence = e.crops.enrich_formulas(e.document, "local.pdf", "models", inline_config={"enabled": True})
    assert e.calls == []
    assert received == [("backend formula", {e.formula.self_ref: route})]
    assert e.formula.text == "backend formula"
    assert evidence[e.formula.self_ref]["status"] == "review_required"


def test_bad_crop_keeps_original_and_does_not_lose_other_items(enrichment, monkeypatch):
    e = enrichment
    def fail(*a, **kw):
        raise ValueError("bad crop coordinates")
    monkeypatch.setattr(e.crops, "render_formula_crop", fail)
    monkeypatch.setattr(e.regions, "inspect_math_region", lambda *a: {"contains_math": True})
    evidence = e.crops.enrich_formulas(e.document, "local.pdf", "models")
    assert e.formula.text == "backend formula"
    assert evidence["#/texts/0"]["reason"] == "document_docling_formula_crop_failed"
    assert e.cell.text == "$x$"
    assert e.calls == ["cell"]


def test_failed_table_crop_keeps_text_and_marks_review(enrichment, monkeypatch):
    e = enrichment
    def fail(*a, **kw):
        raise RuntimeError("crop rendering failed")
    monkeypatch.setattr(e.regions, "inspect_math_region", lambda *a: {"contains_math": True})
    monkeypatch.setattr(e.cells, "render_math_cell_crop", fail)
    evidence = e.crops.enrich_formulas(e.document, "local.pdf", "models")
    assert e.cell.text == "Accuracy for x"
    assert evidence["#/tables/0"]["status"] == "review_required"
    assert evidence["#/tables/0"]["cells"][0]["reason"] == "document_docling_formula_crop_failed"


def test_incomplete_formula_keeps_backend_text(enrichment, monkeypatch):
    from types import SimpleNamespace
    e = enrichment
    monkeypatch.setattr(e.crops, "LocalFormulaReader", lambda _: SimpleNamespace(
        read=lambda image: prediction_record("partial", "<s>partial", [0, 13], eos_token_id=2)))
    evidence = e.crops.enrich_formulas(e.document, "local.pdf", "models")
    assert e.formula.text == "backend formula"
    assert evidence["#/texts/0"]["status"] == "review_required"
    assert evidence["#/texts/0"]["raw_output"] == "<s>partial"


def test_model_runtime_failure_still_stops_conversion(enrichment, monkeypatch):
    from types import SimpleNamespace
    e = enrichment
    def fail(image):
        raise RuntimeError("inference backend failed")
    monkeypatch.setattr(e.crops, "LocalFormulaReader", lambda _: SimpleNamespace(read=fail))
    with pytest.raises(RuntimeError, match="inference backend failed"):
        e.crops.enrich_formulas(e.document, "local.pdf", "models")


@pytest.mark.parametrize("orphan", ["formula", "table"])
def test_orphan_item_cannot_silently_disappear_from_page_exports(enrichment, orphan):
    e = enrichment
    item = e.formula if orphan == "formula" else e.document.tables[0]
    item.prov = []
    with pytest.raises(ValueError, match="document_docling_invalid_provenance"):
        e.crops.enrich_formulas(e.document, "local.pdf", "models")
