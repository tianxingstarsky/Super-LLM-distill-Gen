import pytest

from lib.infrastructure.docling_inline_formulas import _detector_boxes


def frames():
    inputs = [{"id": "paragraph", "size": [1000, 1000], "image_sha256": "same",
               "offset": [20, 300], "scale": 4, "page_origin": [100, 600],
               "view": "paragraph"}]
    response = {"protocol": 1, "network_disabled": True, "images": [{
        "id": "paragraph", "size": [1000, 1000], "image_sha256": "same",
        "boxes": [{"label": "inline_formula", "coordinate": [40, 320, 120, 360], "score": .9}],
    }]}
    return inputs, response


def test_square_padding_is_reversed_before_pdf_mapping():
    inputs, response = frames()
    bbox = _detector_boxes(response, inputs)["paragraph"][0]["bbox"]
    assert bbox == {"l": 105, "r": 125, "t": 595, "b": 585, "coord_origin": "BOTTOMLEFT"}


def test_white_canvas_padding_never_maps_to_unobserved_pdf_ink():
    inputs, response = frames()
    inputs[0]["content_bbox"] = [20, 300, 140, 380]
    response["images"][0]["boxes"][0]["coordinate"] = [40, 20, 120, 360]
    result = _detector_boxes(response, inputs)["paragraph"][0]
    assert result["bbox"]["t"] == 600
    assert result["raw_pixel_box"] == [40, 20, 120, 360]
    assert result["source_pixel_box"] == [40, 300, 120, 360]
    assert result["white_padding_clipped"]


def test_prediction_entirely_in_white_margin_has_no_source_region():
    inputs, response = frames()
    inputs[0]["content_bbox"] = [20, 300, 140, 380]
    response["images"][0]["boxes"][0]["coordinate"] = [40, 20, 120, 100]
    assert _detector_boxes(response, inputs) == {"paragraph": []}


@pytest.mark.parametrize("key,value", [
    ("id", "other"), ("size", [1, 1]), ("image_sha256", "changed"),
])
def test_different_source_frame_is_rejected(key, value):
    inputs, response = frames()
    response["images"][0][key] = value
    with pytest.raises(ValueError, match="invalid_output"):
        _detector_boxes(response, inputs)


@pytest.mark.parametrize("box", [
    [0, 0, 1, float("nan")], [0, 1, 2, 1], [-1, 0, 1, 2], [0, 0, 1001, 2], [1, 2, 3],
])
def test_invalid_boxes_never_enter_source_geometry(box):
    inputs, response = frames()
    response["images"][0]["boxes"][0]["coordinate"] = box
    with pytest.raises(ValueError, match="invalid_output"):
        _detector_boxes(response, inputs)


def test_non_math_layout_is_not_transcribed():
    inputs, response = frames()
    response["images"][0]["boxes"][0]["label"] = "text"
    assert _detector_boxes(response, inputs) == {"paragraph": []}


def test_radical_font_box_outside_ink_uses_proven_intersection():
    from lib.infrastructure.docling_inline_formulas import _native_views
    bbox = {"l": 0, "b": 0, "r": 20, "t": 10}
    chars = [
        {"index": 1, "text": "√", "bbox": {"l": 0, "b": 8, "r": 5, "t": 18}},
        {"index": 2, "text": "x", "bbox": {"l": 5, "b": 0, "r": 10, "t": 10}},
    ]
    assert _native_views(chars, "√x", bbox) == [[1, 2]]


def test_overhanging_neighbor_font_box_uses_proven_center():
    from lib.infrastructure.docling_inline_formulas import _native_views
    bbox = {"l": 0, "b": 0, "r": 20, "t": 10}
    chars = [
        {"index": 1, "text": "a", "bbox": {"l": 0, "b": 9, "r": 5, "t": 19}},
        {"index": 2, "text": "x", "bbox": {"l": 5, "b": 0, "r": 10, "t": 10}},
    ]
    assert _native_views(chars, "x", bbox) == [[2]]


def test_conflicting_font_mapping_stays_unresolved():
    from lib.infrastructure.docling_inline_formulas import _native_views
    bbox = {"l": 0, "b": 0, "r": 20, "t": 10}
    chars = [{"index": 1, "text": "∑", "bbox": {"l": 0, "b": 0, "r": 5, "t": 10}}]
    assert _native_views(chars, "P", bbox) == []


def test_blank_font_boxes_do_not_create_false_source_ambiguity():
    from lib.infrastructure.docling_inline_formulas import _native_views
    bbox = {"l": 0, "b": 0, "r": 20, "t": 10}
    chars = [
        {"index": 1, "text": "x", "bbox": {"l": 0, "b": 0, "r": 5, "t": 10}},
        {"index": 2, "text": " ", "bbox": {"l": 5, "b": 9, "r": 10, "t": 19}},
    ]
    assert _native_views(chars, "x", bbox) == [[1]]


def test_orphan_inline_text_cannot_bypass_page_review(monkeypatch):
    from types import SimpleNamespace
    from lib.infrastructure import docling_inline_formulas as module
    monkeypatch.setattr(module, "read_native_character_pages", lambda source: {})
    item = SimpleNamespace(label="text", prov=[], text="x", self_ref="#/texts/0")
    with pytest.raises(ValueError, match="invalid_provenance"):
        module.enrich_inline_formulas(SimpleNamespace(texts=[item]), "unused.pdf", {}, None)


def test_proven_mixed_formula_becomes_text_in_json_and_markdown():
    from docling_core.types.doc import DoclingDocument, BoundingBox, ProvenanceItem, TextItem, DocItemLabel
    from lib.infrastructure.docling_inline_formulas import _publish_inline_item
    document = DoclingDocument(name="misclassified math")
    item = document.add_text(label=DocItemLabel.FORMULA, text="Let A be a matrix.", prov=ProvenanceItem(
        page_no=1, charspan=(0, 18), bbox=BoundingBox(l=0, b=0, r=100, t=20)))
    original_ref = item.self_ref
    assert _publish_inline_item(document, item, {"text": "Let $A$ be a matrix."},
                                {"probe_required": True})
    replaced = document.texts[0]
    assert type(replaced) is TextItem
    assert replaced.self_ref == original_ref
    assert replaced.orig == item.orig
    assert replaced.prov[0].bbox == item.prov[0].bbox
    assert replaced.prov[0].charspan == (0, len(replaced.text))
    assert "```" not in document.export_to_markdown()
    assert "Let $A$ be a matrix." in document.export_to_markdown()


def test_reclassification_cannot_discard_owned_code_captions():
    from docling_core.types.doc import DoclingDocument, BoundingBox, ProvenanceItem, RefItem
    from lib.infrastructure.docling_inline_formulas import _publish_inline_item
    document = DoclingDocument(name="code")
    item = document.add_code(text="x", prov=ProvenanceItem(
        page_no=1, charspan=(0, 1), bbox=BoundingBox(l=0, b=0, r=100, t=20)))
    item.captions = [RefItem(cref="#/texts/1")]
    before = document.model_dump_json()
    assert not _publish_inline_item(document, item, {"text": "$x$"}, {"probe_required": True})
    assert document.model_dump_json() == before


def test_unknown_proportional_code_keeps_its_semantics_even_when_math_matches():
    from docling_core.types.doc import DoclingDocument, BoundingBox, ProvenanceItem
    from lib.infrastructure.docling_inline_formulas import _publish_inline_item
    document = DoclingDocument(name="unknown code")
    item = document.add_code(text="f(θ)", prov=ProvenanceItem(
        page_no=1, charspan=(0, 4), bbox=BoundingBox(l=0, b=0, r=100, t=20)))
    before = document.model_dump_json()
    assert not _publish_inline_item(document, item, {"text": r"$f(\theta)$"},
                                    {"probe_required": True})
    assert document.model_dump_json() == before


def test_unresolved_code_route_is_preserved_as_review_evidence(monkeypatch):
    from types import SimpleNamespace
    from lib.infrastructure import docling_inline_formulas as module
    monkeypatch.setattr(module, "read_native_character_pages", lambda source: {})
    item = SimpleNamespace(label="code", text="ambiguous source", self_ref="#/texts/0")
    proof = module.enrich_inline_formulas(SimpleNamespace(texts=[item]), "unused.pdf", {}, None,
        routes={item.self_ref: {"route": "review", "probe_required": False}})
    assert proof[item.self_ref]["status"] == "review_required"
    assert not proof[item.self_ref]["published"]
    assert item.text == "ambiguous source"
