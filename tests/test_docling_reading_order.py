"""Physical page ordering with the real upstream rule engine, without models."""
from copy import deepcopy

import pytest

pytest.importorskip("docling_core")
rb = pytest.importorskip("docling.models.postprocessing.reading_order_rb")

from docling_core.types.doc import (
    BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, GroupLabel,
    ProvenanceItem, RefItem, Size, TableCell, TableData,
)

from lib.infrastructure.docling_reading_order import project_page_reading_order


def document():
    doc = DoclingDocument(name="page-order-fixture")
    for number in (1, 2, 3):
        doc.add_page(page_no=number, size=Size(width=600, height=800))
    return doc


def prov(page, left=50, bottom=100, right=550, top=120, span=(0, 1)):
    return ProvenanceItem(
        page_no=page, charspan=span,
        bbox=BoundingBox(l=left, b=bottom, r=right, t=top,
                         coord_origin=CoordOrigin.BOTTOMLEFT),
    )


def text(doc, value, *, page=2, bottom=100, top=120, left=50, right=550, parent=None):
    return doc.add_text(
        label=DocItemLabel.TEXT, text=value, parent=parent,
        prov=prov(page, left, bottom, right, top, (0, len(value))),
    )


def refs(doc):
    return [ref.cref for ref in doc.body.children]


def test_projected_continuation_follows_floating_table_without_changing_source():
    doc = document()
    continuation = text(doc, "Page-local continuation.", bottom=350, top=380)
    other_page = text(doc, "Another page.", page=3)
    cell = TableCell(text="Score", row_span=1, col_span=1,
                     start_row_offset_idx=0, end_row_offset_idx=1,
                     start_col_offset_idx=0, end_col_offset_idx=1)
    table = doc.add_table(data=TableData(num_rows=1, num_cols=1, table_cells=[cell]),
                          prov=prov(2, bottom=410, top=660))
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="Source table caption",
                           parent=table, prov=prov(2, bottom=680, top=720))
    table.captions.append(RefItem(cref=caption.self_ref))
    tail = text(doc, "Later paragraph.", bottom=200, top=240)
    before = deepcopy(doc.model_dump(mode="json"))
    view, audit = project_page_reading_order(doc, 2)

    assert audit["status"] == "reordered" and not audit["review_required"]
    assert refs(view) == [table.self_ref, other_page.self_ref, continuation.self_ref, tail.self_ref]
    assert audit["original_page_refs"] == [continuation.self_ref, table.self_ref, tail.self_ref]
    assert audit["actual_page_refs"] == [table.self_ref, continuation.self_ref, tail.self_ref]
    table_box = next(row for row in audit["elements"] if row["ref"] == table.self_ref)
    assert table_box["bbox"]["t"] == 720
    assert table_box["source_bbox_refs"] == [table.self_ref, caption.self_ref]
    assert doc.model_dump(mode="json") == before
    assert view.tables is doc.tables and view.texts is doc.texts
    assert view.tables[0].children == table.children
    assert view.tables[0].data is table.data
    assert view.body is not doc.body


def test_official_predictor_respects_columns_instead_of_global_y_sorting():
    doc = document()
    upper_right = text(doc, "Right top", left=330, right=550, bottom=680, top=720)
    lower_left = text(doc, "Left lower", left=50, right=270, bottom=400, top=440)
    upper_left = text(doc, "Left top", left=50, right=270, bottom=680, top=720)
    lower_right = text(doc, "Right lower", left=330, right=550, bottom=400, top=440)

    view, audit = project_page_reading_order(doc, 2)

    assert not audit["review_required"]
    assert refs(view) == [upper_left.self_ref, lower_left.self_ref,
                          upper_right.self_ref, lower_right.self_ref]


def test_picture_internal_tokens_and_list_members_remain_atomic():
    doc = document()
    tail = text(doc, "After both objects.", bottom=50, top=80)
    picture = doc.add_picture(prov=prov(2, bottom=450, top=700))
    # A rotated diagram's intended child sequence need not follow page y.
    first = text(doc, "token A", bottom=470, top=480, parent=picture)
    second = text(doc, "token B", bottom=650, top=660, parent=picture)
    group = doc.add_group(label=GroupLabel.LIST)
    list_one = doc.add_list_item(text="first", parent=group, prov=prov(2, bottom=300, top=330))
    list_two = doc.add_list_item(text="second", parent=group, prov=prov(2, bottom=350, top=380))
    before = doc.model_dump_json()

    view, audit = project_page_reading_order(doc, 2)

    assert not audit["review_required"]
    assert refs(view) == [picture.self_ref, group.self_ref, tail.self_ref]
    assert [entry.cref for entry in view.pictures[0].children] == [first.self_ref, second.self_ref]
    assert [entry.cref for entry in view.groups[0].children] == [list_one.self_ref, list_two.self_ref]
    assert doc.model_dump_json() == before


def test_already_ordered_page_returns_same_document():
    doc = document()
    text(doc, "Above", bottom=500, top=550)
    text(doc, "Below", bottom=200, top=250)
    view, audit = project_page_reading_order(doc, 2)
    assert view is doc
    assert audit["status"] == "unchanged"
    assert audit["original_page_refs"] == audit["predicted_page_refs"] == audit["actual_page_refs"]


def test_unprojected_cross_page_text_is_preserved_for_review():
    doc = document()
    paragraph = text(doc, "First second", page=1)
    paragraph.prov.append(prov(2, span=(6, 12)))
    text(doc, "Current page")
    before = doc.model_dump_json()
    view, audit = project_page_reading_order(doc, 2)
    assert view is doc and audit["review_required"]
    assert audit["reasons"] == ["cross_page_item_requires_projection"]
    assert doc.model_dump_json() == before


@pytest.mark.parametrize("invalid", ["missing_page", "outside_bbox", "duplicate", "wrong_parent"])
def test_invalid_provenance_or_ownership_never_reorders(invalid):
    doc = document()
    item = text(doc, "Lower")
    text(doc, "Upper", bottom=500, top=550)
    if invalid == "missing_page":
        del doc.pages[2]
    elif invalid == "outside_bbox":
        item.prov[0].bbox.l = -5
    elif invalid == "duplicate":
        doc.body.children.append(doc.body.children[0])
    else:
        item.parent = RefItem(cref="#/furniture")
    before = doc.model_dump_json()
    view, audit = project_page_reading_order(doc, 2)
    assert view is doc and audit["review_required"] and not audit["changed"]
    assert audit["reasons"]
    assert doc.model_dump_json() == before


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "wrong_page", "wrong_cid", "wrong_ref"])
def test_predictor_output_must_preserve_every_identity(monkeypatch, corruption):
    doc = document()
    text(doc, "Lower")
    text(doc, "Upper", bottom=500, top=550)

    def invalid_prediction(self, elements, *args, **kwargs):
        if corruption == "missing":
            return elements[:1]
        if corruption == "duplicate":
            return [elements[0], elements[0]]
        if corruption == "wrong_ref":
            elements[0].ref.cref = "#/texts/100"
            return elements
        field, value = ("page_no", 3) if corruption == "wrong_page" else ("cid", 100)
        return [elements[1].model_copy(update={field: value}), elements[0]]

    monkeypatch.setattr(rb.ReadingOrderPredictor, "predict_reading_order", invalid_prediction)
    before = doc.model_dump_json()
    view, audit = project_page_reading_order(doc, 2)
    assert view is doc and audit["review_required"] and not audit["changed"]
    assert audit["actual_page_refs"] == audit["original_page_refs"]
    assert doc.model_dump_json() == before


def test_predictor_failure_cannot_mutate_shared_references_or_page_size(monkeypatch):
    doc = document()
    text(doc, "Lower")
    text(doc, "Upper", bottom=500, top=550)

    def faulty_prediction(self, elements, *args, **kwargs):
        elements[0].ref.cref = "#/texts/100"
        elements[0].page_size.width = 42
        raise RecursionError("unresolvable_reading_order")

    monkeypatch.setattr(rb.ReadingOrderPredictor, "predict_reading_order", faulty_prediction)
    before = doc.model_dump_json()
    view, audit = project_page_reading_order(doc, 2)
    assert view is doc and audit["review_required"]
    assert audit["error_type"] == "RecursionError"
    assert doc.model_dump_json() == before


@pytest.mark.parametrize("owner_kind", ["picture", "table"])
@pytest.mark.parametrize("satellite_kind", ["caption", "footnote"])
def test_body_satellite_contributes_only_to_its_unique_owner_footprint(owner_kind, satellite_kind):
    doc = document()
    lower = text(doc, "Lower body.", bottom=100, top=140)
    satellite = doc.add_text(
        label=DocItemLabel(satellite_kind), text="Source satellite.",
        prov=prov(2, bottom=690, top=720),
    )
    if owner_kind == "picture":
        owner = doc.add_picture(prov=prov(2, bottom=400, top=650))
    else:
        owner = doc.add_table(data=TableData(num_rows=0, num_cols=0, table_cells=[]),
                             prov=prov(2, bottom=400, top=650))
    getattr(owner, satellite_kind + "s").append(satellite.get_ref())
    before = doc.model_dump_json()

    view, audit = project_page_reading_order(doc, 2)

    assert not audit["review_required"]
    assert refs(view) == [owner.self_ref, satellite.self_ref, lower.self_ref]
    assert audit["original_page_refs"] == [lower.self_ref, owner.self_ref]
    assert audit["actual_page_refs"] == [owner.self_ref, lower.self_ref]
    footprint = next(row for row in audit["elements"] if row["ref"] == owner.self_ref)
    assert footprint["bbox"]["t"] == 720
    assert footprint["source_bbox_refs"] == [owner.self_ref, satellite.self_ref]
    assert audit["external_satellites"][0]["original_body_slot_preserved"]
    assert satellite.parent.cref == "#/body" and owner.children == []
    assert doc.model_dump_json() == before


def test_body_caption_is_emitted_once_by_page_markdown_after_ordering():
    from lib.infrastructure.docling_document import page_markdown

    doc = document()
    text(doc, "Lower body.", bottom=100, top=140)
    picture = doc.add_picture(prov=prov(2, bottom=400, top=650))
    text(doc, "Diagram label.", bottom=500, top=520, parent=picture)
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="Source figure caption.",
                           prov=prov(2, bottom=350, top=380))
    picture.captions.append(caption.get_ref())
    before = doc.model_dump_json()
    evidence = {}

    markdown, _ = page_markdown(doc, 2, export_audit=evidence)

    assert not evidence["reading_order"]["review_required"]
    assert markdown.count(caption.text) == 1
    assert markdown.index("Diagram label.") < markdown.index(caption.text) < markdown.index("Lower body.")
    assert doc.model_dump_json() == before
    # A valid association elsewhere in the tree must not quarantine a page.
    _, unrelated_audit = project_page_reading_order(doc, 1)
    assert not unrelated_audit["review_required"]


@pytest.mark.parametrize("problem,reason", [
    ("duplicate", "duplicate_reading_order_satellite_reference"),
    ("multiple_owners", "multiple_reading_order_satellite_owners"),
    ("wrong_type", "invalid_reading_order_satellite_type"),
    ("nested_owner", "unowned_reading_order_satellite"),
    ("nested_satellite", "unowned_reading_order_satellite"),
    ("different_page", "ambiguous_reading_order_satellite_page"),
])
def test_ambiguous_satellite_association_preserves_source(problem, reason):
    doc = document()
    group = doc.add_group(label=GroupLabel.LIST)
    picture = doc.add_picture(
        parent=group if problem == "nested_owner" else None,
        prov=prov(2, bottom=400, top=650),
    )
    caption = doc.add_text(
        label=DocItemLabel.TEXT if problem == "wrong_type" else DocItemLabel.CAPTION,
        text="Potential satellite.", prov=prov(3 if problem == "different_page" else 2),
        parent=group if problem == "nested_satellite" else None,
    )
    picture.captions.append(caption.get_ref())
    if problem == "duplicate":
        picture.captions.append(caption.get_ref())
    elif problem == "multiple_owners":
        other = doc.add_picture(prov=prov(2, bottom=200, top=300))
        other.captions.append(caption.get_ref())
    before = doc.model_dump_json()

    view, audit = project_page_reading_order(doc, 2)

    assert view is doc and audit["review_required"]
    assert audit["reasons"] == [reason]
    assert doc.model_dump_json() == before


def test_duplicate_owned_caption_is_not_hidden_by_child_deduplication():
    doc = document()
    picture = doc.add_picture(prov=prov(2, bottom=400, top=650))
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="One caption.", parent=picture,
                           prov=prov(2, bottom=350, top=380))
    picture.captions = [caption.get_ref(), caption.get_ref()]
    before = doc.model_dump_json()
    view, audit = project_page_reading_order(doc, 2)
    assert view is doc and audit["review_required"]
    assert audit["reasons"] == ["duplicate_reading_order_satellite_reference"]
    assert doc.model_dump_json() == before
