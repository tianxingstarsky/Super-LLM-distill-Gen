"""Column boundaries, source scripts and recovered grids must retain evidence."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from lib.infrastructure.docling_native_typography import script_patches, _assemble
from lib.infrastructure.docling_table_recovery import validate_flat_cells
from lib.infrastructure.docling_inline_formulas import _preserved_prose_candidate
from lib.infrastructure.docling_inline_native_preservation import prove_native_preservation
from test_docling_inline_native_preservation import source


@pytest.mark.parametrize("base,small,dy,expected", [
    ("10", "4", 3, "$10^{4}$"),
    ("R", "1", -1.15, "$R_{1}$"),
    ("T", "infer", -1.15, r"$T_{\mathrm{infer}}$"),
])
def test_source_scripts_have_exact_spans_and_keep_other_prose(base, small, dy, expected):
    text = "Use " + base + small + " here."
    start = 4 + len(base)
    gs, ns = source(text, shifts={i: (dy, 7) for i in range(start, start+len(small))})
    before = deepcopy((gs, ns))
    patches = script_patches(gs, ns, text)
    assert len(patches) == 1
    assert _assemble(text, patches)["text"] == "Use " + expected + " here."
    assert patches[0]["backend_text"] == base+small
    assert (gs, ns) == before
    assert not script_patches(gs, ns, text.replace(base+small, "changed"))


def test_kerning_overlap_in_small_label_does_not_drop_suffix():
    text = "Use Tinfer here."
    gs, ns = source(text, shifts={i: (-1.15, 7) for i in range(5,10)})
    # Overlapping ink is normal for 'f'/'e'; source origins stay ordered.
    gs[7]["bbox"]["r"] = gs[8]["bbox"]["l"] + 1
    ns[7]["bbox"]["r"] = ns[8]["bbox"]["l"] + 1
    patches = script_patches(gs, ns, text)
    assert _assemble(text, patches)["text"] == r"Use $T_{\mathrm{infer}}$ here."


@pytest.mark.parametrize("kind", ["flat", "prose_footnote", "reordered"])
def test_ambiguous_source_scripts_are_not_inferred(kind):
    text = "word1" if kind == "prose_footnote" else "104"
    shifts = {} if kind == "flat" else {len(text)-1: (3,7)}
    gs, ns = source(text, shifts=shifts)
    if kind == "reordered":
        gs[-1]["origin"][0] = gs[-2]["origin"][0]
    assert not script_patches(gs, ns, text)


def test_author_symbol_can_link_to_separate_source_footnote_without_email():
    text = "Ada Example†"
    gs, ns = source(text, math_positions=[11], shifts={11: (3,7)})
    markers = [{"kind": "footnote_symbol", "source_text": "†", "glyph_ids": [80], "native_char_ids":[90]}]
    assert not prove_native_preservation(gs, ns, text)["proven_glyph_ids"]
    proven = prove_native_preservation(gs, ns, text, annotation_markers=markers)
    assert 11 in proven["proven_glyph_ids"]


def test_flat_literal_plus_is_preserved_but_shifted_math_is_not():
    text = "Use Python+NumPy here."
    index = text.index("+")
    gs, ns = source(text)
    assert index in prove_native_preservation(gs, ns, text)["proven_glyph_ids"]
    gs, ns = source(text, shifts={index: (3,7)})
    assert index not in prove_native_preservation(gs, ns, text)["proven_glyph_ids"]


def test_each_author_on_a_shared_source_line_can_keep_its_own_annotation():
    text = "Ada Example∗, Bob Example∗, Cora Example∗ †"
    indices = [i for i,c in enumerate(text) if c in "∗†"]
    gs, ns = source(text,math_positions=indices,shifts={i:(3,7) for i in indices})
    markers = [{"kind":"footnote_symbol","source_text":c,"glyph_ids":[80],"native_char_ids":[90]} for c in "∗†"]
    proof = prove_native_preservation(gs,ns,text,annotation_markers=markers)
    assert set(indices) <= set(proof["proven_glyph_ids"])


def test_author_detector_box_does_not_approve_a_stacked_fraction():
    gs, _ = source("Ada Example†", math_positions=[11], shifts={11:(3,7)})
    box = {"geometry": {"review_required":True, "char_indices":list(range(12))}}
    assert _preserved_prose_candidate(box, gs, {11})
    gs[4]["origin"][1] += 4
    assert not _preserved_prose_candidate(box, gs, {11})


def grid():
    gs, _ = source("AB")
    cells = [[NS(x1=-1,y1=90,x2=4.5,y2=110),NS(x1=4.5,y1=90,x2=10,y2=110)]]
    return NS(shape=(1,2), cells=cells, data=[["A","B"]]), {"glyphs":gs}, (-1,90,10,110)


def test_flat_grid_requires_every_source_glyph_once_and_in_correct_cell():
    table, page, box = grid()
    before = deepcopy((table.data, page))
    cells, proofs = validate_flat_cells(table, page, box)
    assert [c["text"] for c in cells] == ["A","B"]
    assert [p["glyph_ids"] for p in proofs] == [[0],[1]]
    assert (table.data,page) == before
    table.data = [["B","A"]]
    with pytest.raises(ValueError,match="cell_source_disagrees"):
        validate_flat_cells(table, page, box)


@pytest.mark.parametrize("corruption", ["script", "missing", "duplicate", "invalid"])
def test_grid_recovery_rejects_loss_and_ambiguous_ownership(corruption):
    table, page, box = grid()
    if corruption == "script":
        page["glyphs"][1]["font_size"] = 6
        table.cells[0][0].x2 = 10
        table.cells[0][1].x1 = 10
        table.cells[0][1].x2 = 20
        table.data = [["AB",""]]
    elif corruption == "missing":
        table.cells[0][1].x1 = 8
        table.data = [["A",""]]
    elif corruption == "duplicate":
        table.cells[0][0].x2 = 10
        table.data = [["AB","B"]]
    else:
        table.cells[0][1].x2 = float("nan")
    with pytest.raises(ValueError,match="table_recovery"):
        validate_flat_cells(table, page, box)


def source_predictor(monkeypatch):
    rb = pytest.importorskip("docling.models.postprocessing.reading_order_rb")
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
    from lib.infrastructure.docling_source_pipeline import source_preserving_pipeline
    monkeypatch.setattr(StandardPdfPipeline,"_init_models",lambda self: setattr(self,"reading_order_model",NS()))
    cls = source_preserving_pipeline()
    instance = cls.__new__(cls)
    instance._init_models()
    return instance.reading_order_model.ro_model, rb


def element(cid,label,box):
    return NS(cid=cid,label=label,page_no=1,l=box[0],b=box[1],r=box[2],t=box[3],ref=NS(cref=f"#/texts/{cid}"))


def test_native_blocks_never_merge_across_columns_or_pages(monkeypatch):
    predictor, rb = source_predictor(monkeypatch)
    elements = [element(0,"text",(30,10,250,30)), element(1,"text",(330,700,550,720))]
    monkeypatch.setattr(rb.ReadingOrderPredictor,"predict_merges",lambda *args:{0:[1]})
    assert predictor.predict_merges(elements) == {}
    assert predictor.source_audit["merge_candidates"][0]["applied"] is False
    another, _ = source_predictor(monkeypatch)
    assert not another.source_audit["merge_candidates"]


def test_caption_geometry_corrects_wrong_owner_without_rewriting(monkeypatch):
    predictor, rb = source_predictor(monkeypatch)
    upper = element(0,"picture",(30,400,250,650))
    lower = element(1,"picture",(30,100,250,350))
    caption = element(2,"caption",(30,370,250,390))
    monkeypatch.setattr(rb.ReadingOrderPredictor,"predict_to_captions",lambda *args:{1:[2]})
    assert predictor.predict_to_captions([upper,lower,caption]) == {0:[2]}
    assert not predictor.source_audit["text_rewritten"]
    # Two equally close owners do not justify changing upstream ownership.
    lower.t = 360
    assert predictor.predict_to_captions([upper,lower,caption]) == {1:[2]}
