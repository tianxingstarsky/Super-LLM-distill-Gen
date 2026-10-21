"""Spatial display ownership must not rewrite original figure-title sources."""
from copy import deepcopy

import pytest
from docling_core.transforms.serializer.markdown import MarkdownParams
from lib.infrastructure.docling_picture_export import serialize_picture_page
from lib.infrastructure.docling_picture_structure import associate_external_picture_titles
from test_docling_picture_structure import panel_fixture

pytestmark = pytest.mark.skipif("traverse_pictures" not in MarkdownParams.model_fields,
                                reason="Requires modern official figure traversal")


def fixture():
    doc, mapping, heading, left, right, anchor, caption = panel_fixture()
    associations, _ = associate_external_picture_titles(doc, mapping)
    mapping["spatial_title_associations"] = associations
    mapping["pictures"][left.self_ref]["external_title_refs"] = [heading.self_ref]
    return doc, mapping, heading, left, right, anchor, caption


def test_external_body_title_renders_once_inside_its_source_proven_figure():
    doc, mapping, heading, left, right, anchor, caption = fixture()
    before = doc.model_dump_json()
    markdown, audit = serialize_picture_page(doc, 1, picture_structure=mapping)
    assert f"> ### {heading.text}" in markdown and f"> ### {anchor.text}" in markdown
    assert markdown.count(heading.text) == 1
    assert not markdown.startswith("### ") and "\n### " not in markdown
    events = audit["spatial_title_projection_events"]
    assert len(events) == 1 and events[0]["title_ref"] == heading.self_ref
    assert events[0]["picture_ref"] == left.self_ref and events[0]["original_parent_ref"] == "#/body"
    assert not left.children and not left.captions and heading.parent.cref == "#/body"
    assert right.captions[0].cref == caption.self_ref
    assert doc.model_dump_json() == before


def test_page_reading_order_view_uses_original_source_for_association_proof():
    doc, mapping, heading, left, _, _, _ = fixture()
    view = doc.model_copy(update={"body": doc.body.model_copy(update={"children":list(reversed(doc.body.children))})})
    markdown, audit = serialize_picture_page(view, 1, picture_structure=mapping, source_document=doc)
    assert markdown.count(heading.text) == 1 and f"> ### {heading.text}" in markdown
    assert audit["spatial_title_projection_events"][0]["picture_ref"] == left.self_ref


def test_swapped_figure_or_stale_source_cannot_move_a_title():
    doc, mapping, heading, left, right, _, _ = fixture()
    mapping["spatial_title_associations"][0]["picture_ref"] = right.self_ref
    with pytest.raises(ValueError,match="document_docling_invalid_output"):
        serialize_picture_page(doc,1,picture_structure=mapping)


@pytest.mark.parametrize("problem", ["picture_bbox","title_text","anchor_level","caption_text","picture_children","body_member"])
def test_valid_original_proof_cannot_authorize_different_actual_view_nodes(problem):
    from docling_core.types.doc import BoundingBox
    doc,mapping,heading,left,right,anchor,caption = fixture()
    view = doc.model_copy(deep=True)
    if problem == "picture_bbox":
        box = left.prov[0].bbox.model_copy(update={"l":left.prov[0].bbox.l+5})
        view.pictures[0].prov[0].bbox = box
    elif problem == "title_text": view.texts[0].text += " changed"
    elif problem == "anchor_level": view.texts[int(anchor.self_ref.rsplit('/',1)[1])].level += 1
    elif problem == "caption_text": view.texts[int(caption.self_ref.rsplit('/',1)[1])].text += " changed"
    elif problem == "picture_children": view.pictures[0].children.append(anchor.get_ref())
    else: view.body.children = [ref for ref in view.body.children if ref.cref != heading.self_ref]
    with pytest.raises(ValueError,match="document_docling_invalid_output"):
        serialize_picture_page(view,1,picture_structure=mapping,source_document=doc)
