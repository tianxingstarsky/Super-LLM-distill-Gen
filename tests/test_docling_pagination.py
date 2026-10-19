"""Real Docling Core serialization contracts, without models or network calls."""
from copy import deepcopy

import pytest

pytest.importorskip("docling_core")

from docling_core.types.doc import (
    BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, GroupLabel,
    ProvenanceItem, Size, TableCell, TableData,
)

from lib.infrastructure.docling_document import _page_provenance, page_markdown


def provenance(page, start, stop):
    return ProvenanceItem(page_no=page, charspan=(start, stop),
                          bbox=BoundingBox(l=20, t=300, r=500, b=200, coord_origin=CoordOrigin.BOTTOMLEFT))


def document_with_cross_page_text(*, list_item=False):
    document = DoclingDocument(name="cross-page-fixture")
    for number in (1, 2, 3):
        document.add_page(page_no=number, size=Size(width=612, height=792))
    first, second = "First page ends here.", "Second page resumes here."
    text = first + " " + second
    first_prov = provenance(1, 0, len(first))
    if list_item:
        group = document.add_group(label=GroupLabel.LIST)
        item = document.add_list_item(text=text, prov=first_prov, parent=group)
    else:
        item = document.add_text(label=DocItemLabel.TEXT, text=text, prov=first_prov)
    item.prov.append(provenance(2, len(first) + 1, len(text)))
    document.add_text(label=DocItemLabel.TEXT, text="Separate third-page paragraph.", prov=provenance(3, 0, 30))
    return document, item, first, second


@pytest.mark.parametrize("list_item", [False, True])
def test_cross_page_fragments_keep_their_page_graph_and_original(list_item):
    document, item, first, second = document_with_cross_page_text(list_item=list_item)
    before = deepcopy(document.model_dump(mode="json"))
    # Default upstream export keeps the entire item on its first provenance
    # page, dropping the continuation from the second page.
    assert second in document.export_to_markdown(page_no=1)
    assert second not in document.export_to_markdown(page_no=2)
    one, first_refs = page_markdown(document, 1)
    two, second_refs = page_markdown(document, 2)
    three, third_refs = page_markdown(document, 3)
    assert first in one and second not in one
    assert second in two and first not in two
    assert first_refs == second_refs == [item.self_ref] and third_refs == []
    assert "Separate third-page paragraph." in three
    assert first not in three and second not in three
    if list_item:
        assert one.startswith("- ") and two.startswith("- ")
    assert document.model_dump(mode="json") == before
    original_provenance = _page_provenance(document, [1, 2, 3])
    assert original_provenance[2][0]["provenance"]["charspan"] == [len(first) + 1, len(item.text)]


def test_tables_keep_cells_and_references_when_near_split_paragraph():
    document, _, first, second = document_with_cross_page_text()
    cells = []
    for row, values in enumerate((("Method", "Score"), ("Local", "42"))):
        for column, value in enumerate(values):
            cells.append(TableCell(text=value, row_span=1, col_span=1,
                start_row_offset_idx=row, end_row_offset_idx=row + 1,
                start_col_offset_idx=column, end_col_offset_idx=column + 1,
                column_header=row == 0, row_header=False))
    table = document.add_table(data=TableData(num_rows=2, num_cols=2, table_cells=cells),
                                prov=provenance(2, 0, 0))
    before = deepcopy(document.model_dump(mode="json"))
    one, _ = page_markdown(document, 1)
    two, _ = page_markdown(document, 2)
    assert "Method" not in one and first in one
    assert "| Method" in two and "Local" in two and "42" in two
    assert second in two and first not in two
    assert table.self_ref == "#/tables/0"
    assert document.model_dump(mode="json") == before


@pytest.mark.parametrize("spans", [((0, 5), (6, 10)), ((0, 25), (10, 40)), ((0, 20), (21, 999))])
def test_ambiguous_missing_overlapping_or_invalid_spans_are_never_guessed(spans):
    document, item, _, _ = document_with_cross_page_text()
    item.prov = [provenance(number, *span) for number, span in enumerate(spans, 1)]
    with pytest.raises(ValueError, match="document_docling_page_span_ambiguous"):
        page_markdown(document, 1)


def test_multpage_enriched_formula_needs_explicit_review_not_character_guessing():
    document, _, _, _ = document_with_cross_page_text()
    item = document.add_text(label=DocItemLabel.FORMULA, text=r"x^{2}+y^{2}", orig="x2+y2",
                             prov=provenance(1, 0, 2))
    item.prov.append(provenance(2, 3, 5))
    with pytest.raises(ValueError, match="document_docling_page_span_ambiguous"):
        page_markdown(document, 1)
