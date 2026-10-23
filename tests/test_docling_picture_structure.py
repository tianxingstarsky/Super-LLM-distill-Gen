"""Exact native source ranges and physical figure rows, without models."""
from copy import deepcopy
import sys
from types import ModuleType

import pytest

from lib.infrastructure.docling_picture_structure import (
    PICTURE_STRUCTURE_POLICY, associate_external_picture_titles, build_picture_structure,
    group_rotated_token_rows, partition_source_lines, validated_spatial_title_associations,
)


DIGEST = "a" * 64


def box(l=0, b=0, r=100, t=100):
    return dict(l=l, b=b, r=r, t=t, coord_origin="BOTTOMLEFT")


def line(index, text, *, bbox=None, page=1, angle=0, start=None, end=None):
    return dict(line_id=f"page-{page}:line-{index}", line_index=index, page_no=page,
                source_sha256=DIGEST, native_text=text, bbox=bbox or box(), angle=angle,
                coordinate_space_supported=True, from_ocr=False, rendering_mode=0,
                font_key="/F1", font_name="SourceFont", perpendicular_extent=8,
                baseline_axis_start=start or [0, 0], baseline_axis_end=end or [10, 0],
                native_char_indices=[index], native_character_sequence_verified=True)


def partition(text, lines):
    return partition_source_lines(text, box(), lines, page_no=1, source_sha256=DIGEST)


def test_native_lines_preserve_exact_spans_whitespace_and_source_sequence():
    text = "  Main title\t\nPanel label  "
    # Geometry may overlap (an invisible/clipped line can overlap real ink).
    result = partition(text, [line(2, "Panel label"), line(1, "Main title")])
    assert [segment["text"] for segment in result["segments"]] == ["Main title", "Panel label"]
    assert [segment["source_charspan"] for segment in result["segments"]] == [[2, 12], [14, 25]]
    reconstructed = "".join(s["separator_before"] + s["text"] for s in result["segments"])
    assert reconstructed + result["trailing_separator"] == text
    assert all(segment["display"] for segment in result["segments"])
    assert result["exact_source_partition"]


@pytest.mark.parametrize("value,rows,reason", [
    ("abc def", [line(0, "abc")], "missing"),
    ("abc", [line(0, "abd")], "missing"),
    ("abc", [line(0, "abc"), line(1, "abc")], "not_unique"),
    ("aa aa", [line(0, "aa"), line(1, "aa")], "not_unique"),
    ("a−b", [line(0, "a-b")], "missing"),
    ("abc", [line(0, "abc", bbox=box(-1, 0, 100, 100))], "missing"),
    ("abc", [line(0, "abc", page=2)], "missing"),
])
def test_partial_ambiguous_or_unbound_native_lines_never_make_a_projection(value, rows, reason):
    with pytest.raises(ValueError, match=reason):
        partition(value, rows)


@pytest.mark.parametrize("field,value", [
    ("from_ocr", True), ("coordinate_space_supported", False),
    ("source_sha256", "other"), ("rendering_mode", 3),
])
def test_unsupported_line_evidence_cannot_bind_source(field, value):
    row = line(0, "source")
    row[field] = value
    with pytest.raises(ValueError, match="missing"):
        partition("source", [row])


def entry(index, text, *, start, end, angle=90):
    native = line(index, text, start=start, end=end, angle=angle)
    return dict(item_ref=f"#/texts/{index}", label="text", source_text=text,
                review_required=False, segments=[dict(source_charspan=[0, len(text)],
                    text=text, display=True, native_line=native)])


def test_rotated_rows_use_native_start_or_end_alignment_preserving_repeated_tokens():
    entries = [
        entry(0, "one", start=[10, 100], end=[10, 110]),
        entry(1, "same", start=[20, 100.01], end=[20, 120]),
        entry(2, "same", start=[30, 100.02], end=[30, 120]),
        entry(3, "longer", start=[10, 30], end=[10, 50]),
        entry(4, "short", start=[20, 40], end=[20, 50.01]),
    ]
    before = deepcopy(entries)
    rows = group_rotated_token_rows(entries)
    assert [r["source_refs"] for r in rows] == [["#/texts/0", "#/texts/1", "#/texts/2"],
                                               ["#/texts/3", "#/texts/4"]]
    assert rows[0]["source_texts"] == ["one", "same", "same"]
    assert rows[0]["aligned_native_axis_endpoints"] == ["start"]
    assert rows[1]["aligned_native_axis_endpoints"] == ["end"]
    assert all(row["source_order_preserved"] and not row["semantic_relations_inferred"] for row in rows)
    assert entries == before


def test_rotated_rows_do_not_sort_children_to_make_alignment_work():
    entries = [entry(0, "a", start=[10, 100], end=[10, 110]),
               entry(1, "b", start=[30, 100], end=[30, 110]),
               entry(2, "c", start=[20, 100], end=[20, 110])]
    rows = group_rotated_token_rows(entries)
    assert rows[0]["source_refs"] == ["#/texts/0", "#/texts/1"]
    assert "#/texts/2" not in rows[0]["source_refs"]


def test_matching_centers_do_not_prove_a_common_native_token_row():
    first = entry(0, "a", start=[10, 100], end=[10, 120])
    second = entry(1, "b", start=[20, 90], end=[20, 130])
    assert group_rotated_token_rows([first, second]) == []


@pytest.mark.parametrize("problem", ["horizontal", "font", "page", "source", "missing_chars", "hidden", "heading", "separator"])
def test_rows_do_not_merge_across_unsupported_evidence_or_source_boundaries(problem):
    first = entry(0, "a", start=[10, 100], end=[10, 110])
    second = entry(1, "b", start=[20, 100], end=[20, 110])
    if problem == "horizontal":
        second["segments"][0]["native_line"]["angle"] = 0
    elif problem == "font":
        second["segments"][0]["native_line"]["font_key"] = "/F2"
    elif problem == "page":
        second["segments"][0]["native_line"]["page_no"] = 2
    elif problem == "source":
        second["segments"][0]["native_line"]["source_sha256"] = "other"
    elif problem == "missing_chars":
        second["segments"][0]["native_line"]["native_character_sequence_verified"] = False
    elif problem == "hidden":
        second["segments"][0]["display"] = False
    elif problem == "heading":
        second["label"] = "section_header"
    entries = [first, None, second] if problem == "separator" else [first, second]
    assert group_rotated_token_rows(entries) == []


def test_build_uses_exact_clip_proof_only_and_never_changes_source_document(monkeypatch):
    pytest.importorskip("docling_core")
    import hashlib
    from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size
    import lib.infrastructure.docling_picture_structure as module

    content = b"mock source bytes"
    digest = hashlib.sha256(content).hexdigest()
    doc = DoclingDocument(name="source diagram")
    doc.add_page(page_no=1, size=Size(width=100, height=100))
    provenance = ProvenanceItem(page_no=1, bbox=BoundingBox(**box()), charspan=(0, 24))
    picture = doc.add_picture(prov=provenance)
    title = doc.add_heading(text="Visible title Hidden label", level=2, parent=picture, prov=provenance)
    first, second = line(0, "Visible title"), line(1, "Hidden label")
    for native in (first, second):
        native["source_sha256"] = digest
    monkeypatch.setattr(module, "read_native_picture_lines", lambda *a, **k: {
        1: {"source_sha256": digest, "lines": [first, second]}})
    visibility = ModuleType("lib.contexts.documents.infrastructure.docling.pdf_text_visibility")
    visibility.read_pdf_text_visibility = lambda *a, **k: {1: {"source_sha256": digest}}

    def match(page, text, bbox, **kwargs):
        assert kwargs == {"source_sha256": digest, "page_no": 1}
        return {"fully_clipped": text == "Hidden label",
                "visibility": "fully_clipped" if text == "Hidden label" else "unknown"}

    visibility.match_text_visibility = match
    monkeypatch.setitem(sys.modules, visibility.__name__, visibility)
    before = doc.model_dump_json()
    mapping, audit = build_picture_structure(doc, content)
    record = mapping["items"][title.self_ref]
    assert [s["display"] for s in record["segments"]] == [True, False]
    assert record["original_level"] == 2 and record["status"] == "projected"
    assert record["segments"][1]["source_charspan"] == [14, 26]
    assert record["source_text"] == title.text
    assert not audit["review_required_refs"]
    assert doc.model_dump_json() == before


def panel_fixture():
    pytest.importorskip("docling_core")
    import hashlib
    from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size

    doc = DoclingDocument(name="native panel group")
    doc.add_page(page_no=1, size=Size(width=400, height=400))

    def prov(bounds, text=""):
        return ProvenanceItem(page_no=1, bbox=BoundingBox(**bounds), charspan=(0, len(text)))

    heading = doc.add_heading(text="Left panel label", level=2, prov=prov(box(45, 300, 155, 310), "Left panel label"))
    left = doc.add_picture(prov=prov(box(60, 170, 140, 285)))
    right = doc.add_picture(prov=prov(box(240, 160, 360, 312)))
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="A shared source caption", parent=right,
                           prov=prov(box(30, 120, 370, 140), "A shared source caption"))
    right.captions.append(caption.get_ref())
    anchor = doc.add_heading(text="Other panel label", level=2, parent=right,
                            prov=prov(box(260, 300, 340, 310), "Other panel label"))
    mapping = {"policy": PICTURE_STRUCTURE_POLICY, "source_sha256": DIGEST,
               "items": {}, "pictures": {picture.self_ref: {} for picture in (left, right)}}
    for index, item in enumerate((heading, anchor)):
        bbox = item.prov[0].bbox.model_dump(mode="json")
        native = line(index, item.text, bbox=bbox, start=[bbox["l"], bbox["b"]], end=[bbox["r"], bbox["b"]])
        native["perpendicular_extent"] = 10
        mapping["items"][item.self_ref] = {"item_ref": item.self_ref, "label": "section_header",
            "original_level": item.level, "original_parent_ref": item.parent.cref,
            "source_provenance": [p.model_dump(mode="json") for p in item.prov],
            "source_text": item.text, "source_text_sha256": hashlib.sha256(item.text.encode()).hexdigest(),
            "page_no": 1, "exact_source_partition": True, "review_required": False,
            "trailing_separator": "", "segments": [{"source_charspan": [0, len(item.text)],
                "text": item.text, "separator_before": "", "display": True, "native_line": native}]}
    return doc, mapping, heading, left, right, anchor, caption


def test_external_heading_requires_existing_panel_anchor_and_preserves_source_graph():
    doc, mapping, heading, left, right, anchor, caption = panel_fixture()
    before, mapping_before = doc.model_dump_json(), deepcopy(mapping)
    associations, decisions = associate_external_picture_titles(doc, mapping)
    assert len(associations) == 1
    proof = associations[0]
    assert (proof["title_ref"], proof["picture_ref"], proof["anchor_item_ref"], proof["original_caption_ref"]) == (
        heading.self_ref, left.self_ref, anchor.self_ref, caption.self_ref)
    assert proof["proof"]["body_refs"] == [heading.self_ref, left.self_ref, right.self_ref]
    assert proof["source_charspan"] == [0, len(heading.text)]
    assert proof["title_provenance"] == [p.model_dump(mode="json") for p in heading.prov]
    assert not proof["source_graph_modified"] and not proof["caption_ownership_inferred"]
    assert decisions[0]["status"] == "associated"
    assert doc.model_dump_json() == before and mapping == mapping_before
    assert heading.parent.cref == "#/body" and not left.captions and not left.children


@pytest.mark.parametrize("problem", [
    "font", "baseline", "height", "level", "native_source", "native_chars", "hidden", "transformed",
    "center", "too_far", "title_not_covering_panel", "overlapping_panels", "different_row",
    "no_caption", "caption_not_spanning", "caption_above", "caption_duplicate", "caption_multiple_owners",
    "caption_body_parent", "no_owned_anchor", "target_has_title", "body_order_interruption", "corridor_text", "third_picture",
    "native_text", "native_line_id", "native_indices", "source_ref", "segment_text", "separator", "trailing_separator",
    "empty_caption", "caption_changed", "caption_span",
])
def test_nearby_section_headings_cannot_use_incomplete_or_ambiguous_panel_evidence(problem):
    from docling_core.types.doc import BoundingBox, DocItemLabel, ProvenanceItem
    doc, mapping, heading, left, right, anchor, caption = panel_fixture()
    native = mapping["items"][heading.self_ref]["segments"][0]["native_line"]
    if problem == "font":
        native["font_key"] = "/DifferentFont"
    elif problem == "baseline":
        native["baseline_axis_start"][1] += 2
    elif problem == "height":
        native["perpendicular_extent"] = 12
    elif problem == "level":
        heading.level = 3
        mapping["items"][heading.self_ref]["original_level"] = 3
    elif problem == "native_source":
        native["source_sha256"] = "other"
    elif problem == "native_chars":
        native["native_character_sequence_verified"] = False
    elif problem == "native_text":
        native["native_text"] = "Different source characters"
    elif problem == "native_line_id":
        native["line_id"] = "page-1:line-999"
    elif problem == "native_indices":
        native["native_char_indices"] = [5, 5]
    elif problem == "source_ref":
        mapping["items"][heading.self_ref]["item_ref"] = anchor.self_ref
    elif problem == "segment_text":
        mapping["items"][heading.self_ref]["segments"][0]["text"] = "Other text"
    elif problem == "separator":
        mapping["items"][heading.self_ref]["segments"][0]["separator_before"] = "Other text"
    elif problem == "trailing_separator":
        mapping["items"][heading.self_ref]["trailing_separator"] = "Other text"
    elif problem == "hidden":
        mapping["items"][heading.self_ref]["segments"][0]["display"] = False
    elif problem == "transformed":
        heading.text = "Changed by another pass"
    elif problem == "center":
        left.prov[0].bbox.l += 5
        left.prov[0].bbox.r += 5
    elif problem == "too_far":
        left.prov[0].bbox.t = 270
    elif problem == "title_not_covering_panel":
        left.prov[0].bbox.l, left.prov[0].bbox.r = 35, 165
    elif problem == "overlapping_panels":
        right.prov[0].bbox.l = 130
    elif problem == "different_row":
        left.prov[0].bbox.b = 150
    elif problem == "no_caption":
        right.captions.clear()
    elif problem == "caption_not_spanning":
        caption.prov[0].bbox.l = 230
    elif problem == "caption_above":
        caption.prov[0].bbox.b, caption.prov[0].bbox.t = 330, 350
    elif problem == "caption_duplicate":
        right.captions.append(caption.get_ref())
    elif problem == "empty_caption":
        caption.text = caption.orig = ""
    elif problem == "caption_changed":
        caption.text = "Changed source caption"
    elif problem == "caption_span":
        caption.prov[0].charspan = (0, 1)
    elif problem == "caption_multiple_owners":
        left.captions.append(caption.get_ref())
    elif problem == "caption_body_parent":
        caption.parent = doc.body.get_ref()
    elif problem == "no_owned_anchor":
        anchor.parent = doc.body.get_ref()
    elif problem == "target_has_title":
        doc.add_heading(text="Existing title", parent=left)
    elif problem == "body_order_interruption":
        paragraph = doc.add_text(label=DocItemLabel.TEXT, text="Unrelated intervening body paragraph")
        doc.body.children.insert(1, doc.body.children.pop())
    elif problem == "corridor_text":
        doc.add_text(label=DocItemLabel.TEXT, text="Intervening source content",
                     prov=ProvenanceItem(page_no=1, bbox=BoundingBox(**box(170, 180, 220, 200)), charspan=(0, 26)))
    elif problem == "third_picture":
        doc.add_picture(prov=ProvenanceItem(page_no=1, bbox=BoundingBox(**box(175, 190, 220, 270)), charspan=(0, 0)))
    before = doc.model_dump_json()
    associations, _ = associate_external_picture_titles(doc, mapping)
    assert associations == []
    assert doc.model_dump_json() == before


@pytest.mark.parametrize("problem", ["swapped_picture", "changed_page", "duplicate", "stale_geometry", "stale_text", "unbound_refs"])
def test_consumers_recompute_proofs_and_reject_stale_or_swapped_associations(problem):
    doc, mapping, heading, left, right, anchor, caption = panel_fixture()
    associations, _ = associate_external_picture_titles(doc, mapping)
    mapping["spatial_title_associations"] = associations
    mapping["pictures"][left.self_ref]["external_title_refs"] = [heading.self_ref]
    assert validated_spatial_title_associations(doc, mapping, page_no=1) == associations
    assert validated_spatial_title_associations(doc, mapping, page_no=2) == []
    if problem == "swapped_picture":
        associations[0]["picture_ref"] = right.self_ref
    elif problem == "changed_page":
        associations[0]["page_no"] = 2
    elif problem == "duplicate":
        associations.append(deepcopy(associations[0]))
    elif problem == "stale_geometry":
        left.prov[0].bbox.l += .1
    elif problem == "stale_text":
        anchor.text = "Changed native anchor"
    elif problem == "unbound_refs":
        mapping["pictures"][left.self_ref]["external_title_refs"] = [anchor.self_ref]
    with pytest.raises(ValueError, match="figure_title_association_binding_changed"):
        validated_spatial_title_associations(doc, mapping, page_no=1)


def test_builder_exposes_export_association_without_rewriting_source_roles(monkeypatch):
    import hashlib
    import lib.infrastructure.docling_picture_structure as module
    import lib.infrastructure.docling_pdf_text_visibility as visibility
    doc, prior, heading, left, right, anchor, caption = panel_fixture()
    content = b"independent native source fixture"
    digest = hashlib.sha256(content).hexdigest()
    lines = [deepcopy(record["segments"][0]["native_line"]) for record in prior["items"].values()]
    for native in lines:
        native["source_sha256"] = digest
    monkeypatch.setattr(module, "read_native_picture_lines", lambda *a, **k: {
        1: {"source_sha256": digest, "lines": lines}})
    monkeypatch.setattr(visibility, "read_pdf_text_visibility", lambda *a, **k: {})
    monkeypatch.setattr(visibility, "match_text_visibility", lambda *a, **k: {
        "fully_clipped": False, "visibility": "unknown"})
    before = doc.model_dump_json()
    mapping, audit = build_picture_structure(doc, content)
    association = validated_spatial_title_associations(doc, mapping, page_no=1)[0]
    assert association == mapping["spatial_title_associations"][0] == audit["spatial_title_associations"][0]
    assert mapping["pictures"][left.self_ref]["external_title_refs"] == [heading.self_ref]
    record = mapping["items"][heading.self_ref]
    assert record["source_role"] == "body_header" and record["picture_refs"] == []
    assert record["spatial_title_association"] == association
    assert doc.model_dump_json() == before
