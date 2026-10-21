"""Published figure structure must remain source-bound through serialization."""
from copy import deepcopy
import hashlib
from functools import lru_cache

import pytest
from docling_core.transforms.serializer.markdown import MarkdownParams
from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, PageItem, ProvenanceItem, Size

from lib.infrastructure.docling_picture_export import serialize_picture_page
from test_docling_pdf_text_visibility import native_pdf, complete_native_binding

pytestmark = pytest.mark.skipif("traverse_pictures" not in MarkdownParams.model_fields,
                                reason="Requires modern official picture traversal")
DIGEST = hashlib.sha256(native_pdf()).hexdigest()


@lru_cache(maxsize=1)
def binding():
    return complete_native_binding()


def source():
    doc = DoclingDocument(name="Figure structure publication")
    doc.pages[1] = PageItem(page_no=1, size=Size(width=200, height=200))
    def prov(text, y):
        return ProvenanceItem(page_no=1, charspan=(0, len(text)),
            bbox=BoundingBox(l=20, r=180, b=y, t=y+10, coord_origin=CoordOrigin.BOTTOMLEFT))
    picture = doc.add_picture(prov=prov("", 10))
    heading = doc.add_heading(text="visible Same", parent=picture, prov=prov("visible Same", 170))
    heading.prov[0].bbox = BoundingBox(l=0, r=200, b=0, t=200, coord_origin=CoordOrigin.BOTTOMLEFT)
    children = [doc.add_text(label=DocItemLabel.TEXT, text=text, parent=picture, prov=prov(text, y))
                for text, y in [("a", 110), ("a", 90), ("<pad>", 70), ("Same", 50)]]
    children[-1].prov[0].bbox = deepcopy(heading.prov[0].bbox)
    return doc, picture, heading, children


def plan(item, ranges):
    segments, cursor = [], 0
    for start, stop, display in ranges:
        page, native_line, real_proof = binding()
        proof = deepcopy(real_proof) if not display else {"visibility":"unknown", "fully_clipped":False}
        line = deepcopy(native_line)
        if display:
            line.update(native_text=item.text[start:stop], bbox={"l":20,"b":170,"r":180,"t":180,"coord_origin":"BOTTOMLEFT"})
        segments.append({"source_charspan":[start,stop],"text":item.text[start:stop],
                         "separator_before":item.text[cursor:start],"display":display,"visibility":proof,
                         "native_line":line})
        cursor=stop
    return {"item_ref":item.self_ref,"source_text":item.text,"exact_source_partition":True,
            "source_text_sha256":hashlib.sha256(item.text.encode()).hexdigest(),
            "source_provenance":[p.model_dump(mode="json") for p in item.prov],
            "page_no":1,"segments":segments,"trailing_separator":item.text[cursor:],"review_required":False}


def structure(heading, hidden):
    return {"source_sha256":DIGEST,"items":{heading.self_ref:plan(heading, [(0,7,True),(8,12,False)]),
            hidden.self_ref:plan(hidden,[(0,4,False)])},"pictures":{}}


def test_clipped_spans_remain_in_source_but_only_actual_title_is_published():
    doc, _, heading, children = source()
    before = doc.model_dump_json()
    mapping = structure(heading, children[-1])
    text, audit = serialize_picture_page(doc, 1, picture_structure=mapping)
    assert "> ## visible" in text and "Same" not in text
    assert children[-1].self_ref in audit["visibility_excluded_refs"]
    assert not audit["missing_child_refs"]
    assert doc.model_dump_json() == before
    assert audit["source_text_structure"][0]["source_text"] == "visible Same"


def test_repeated_rotated_tokens_publish_as_one_physical_row_with_distinct_spans():
    doc, picture, heading, children = source()
    mapping = structure(heading, children[-1])
    refs = [item.self_ref for item in children[:3]]
    mapping["pictures"][picture.self_ref] = {"token_rows":[{"page_no":1,"source_refs":refs,
                                                         "source_order_preserved":True}]}
    text, audit = serialize_picture_page(doc, 1, picture_structure=mapping)
    assert "> a a &lt;pad&gt;" in text
    assert audit["physical_row_projection_events"][0]["source_refs"] == refs
    assert not audit["missing_child_refs"] and not audit["duplicate_span_refs"]


@pytest.mark.parametrize("tamper", ["text", "source", "provenance", "visibility", "separator"])
def test_stale_or_unproven_projection_is_rejected(tamper):
    doc, _, heading, children = source()
    mapping = structure(heading, children[-1])
    p = mapping["items"][heading.self_ref]
    if tamper == "text": p["source_text"] = "different text"
    elif tamper == "source": mapping["source_sha256"] = "b" * 64
    elif tamper == "provenance": p["source_provenance"][0]["charspan"] = [0, 4]
    elif tamper == "visibility": p["segments"][1]["visibility"]["fully_clipped"] = False
    else: p["segments"][1]["separator_before"] = "--"
    with pytest.raises(ValueError, match="document_docling_invalid_output"):
        serialize_picture_page(doc, 1, picture_structure=mapping)


def test_unknown_structure_keeps_original_text_and_does_not_claim_exclusion():
    doc, _, heading, children = source()
    mapping = structure(heading, children[-1])
    for item in mapping["items"].values(): item["review_required"] = True
    text, audit = serialize_picture_page(doc, 1, picture_structure=mapping)
    assert "visible Same" in text and "> Same" in text
    assert not audit["visibility_excluded_refs"]


def test_unsupported_row_order_cannot_silently_group_different_source_tokens():
    doc, picture, heading, children = source()
    mapping = structure(heading, children[-1])
    mapping["pictures"][picture.self_ref] = {"token_rows":[{"page_no":1,"source_refs":[
        children[0].self_ref, children[2].self_ref], "source_order_preserved":True}]}
    with pytest.raises(ValueError, match="document_docling_invalid_output"):
        serialize_picture_page(doc, 1, picture_structure=mapping)
