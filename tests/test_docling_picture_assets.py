"""Source pixels remain faithful, bounded, and separate from training text."""
import base64
import hashlib
from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, PageItem, ProvenanceItem, Size

from lib.infrastructure.docling_picture_assets import extract_picture_assets


def fixture(tmp_path):
    path = tmp_path / "source.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=150)
    stream = DecodedStreamObject()
    stream.set_data(b"q 1 0 0 rg 20 30 40 50 re f Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    doc = DoclingDocument(name="Source figures")
    doc.pages[1] = PageItem(page_no=1, size=Size(width=200, height=150))
    picture = doc.add_picture(prov=ProvenanceItem(page_no=1, charspan=(0, 0),
        bbox=BoundingBox(l=20, r=60, b=30, t=80, coord_origin=CoordOrigin.BOTTOMLEFT)))
    return path, doc, picture


def run(doc, path):
    return extract_picture_assets(doc, path, source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def test_official_crop_retains_actual_color_geometry_source_and_original_document(tmp_path):
    path, doc, picture = fixture(tmp_path)
    before = doc.model_dump_json()
    assets = run(doc, path)
    row = assets[1]["items"][0]
    payload = base64.b64decode(row["image"].split(",", 1)[1])
    with Image.open(BytesIO(payload)) as image:
        assert image.size == (80, 100)
        assert image.getpixel((40, 50)) == (255, 0, 0)
    assert row["image_sha256"] == hashlib.sha256(payload).hexdigest()
    assert row["picture_ref"] == picture.self_ref and row["provenance_index"] == 0
    assert row["source_bbox"] == picture.prov[0].bbox.model_dump(mode="json")
    assert row["status"] == "exported" and not row["source_region_is_blank_white"]
    assert not assets[1]["review_required"]
    assert doc.model_dump_json() == before


def test_blank_native_picture_region_is_identified_without_inventing_content(tmp_path):
    path, doc, picture = fixture(tmp_path)
    picture.prov[0].bbox = BoundingBox(l=100, r=120, b=100, t=120, coord_origin=CoordOrigin.BOTTOMLEFT)
    row = run(doc, path)[1]["items"][0]
    assert row["status"] == "exported" and row["source_region_is_blank_white"]
    assert row["width"] == row["height"] == 40


def test_asset_limit_retains_explicit_review_record_instead_of_a_broken_or_silent_image(tmp_path, monkeypatch):
    path, doc, _ = fixture(tmp_path)
    monkeypatch.setattr("lib.infrastructure.docling_picture_assets.MAX_TOTAL_ASSET_BYTES", 1)
    audit = run(doc, path)[1]
    row = audit["items"][0]
    assert audit["review_required"]
    assert row["status"] == "review_required" and row["reason"] == "picture_asset_size_limit"
    assert "image" not in row and "source_bbox" in row


@pytest.mark.parametrize("box", [(0, 0, 20, 30), (-1, 20, 10, 30), (0, 210, 10, 30)])
def test_invalid_source_regions_are_rejected_before_crop(tmp_path, box):
    path, doc, picture = fixture(tmp_path)
    left, right, bottom, top = box
    picture.prov[0].bbox = BoundingBox(l=left, r=right, b=bottom, t=top, coord_origin=CoordOrigin.BOTTOMLEFT)
    with pytest.raises(ValueError, match="document_docling_invalid_provenance"):
        run(doc, path)


def test_empty_document_never_opens_a_pdf(tmp_path):
    assert extract_picture_assets(DoclingDocument(name="No figure"), tmp_path / "absent.pdf", source_sha256="0"*64) == {}


@pytest.mark.parametrize("valid",[True,False])
def test_complete_source_label_ink_can_extend_crop_without_reparenting(tmp_path,valid):
    from lib.infrastructure.docling_picture_assets import _coverage_region
    _,doc,picture = fixture(tmp_path)
    label = doc.add_text(label=DocItemLabel.TEXT,text="x",prov=ProvenanceItem(
        page_no=1,charspan=(0,1),bbox=BoundingBox(l=30,r=40,b=28,t=34,coord_origin=CoordOrigin.BOTTOMLEFT)))
    glyph = {"text":"x","char_index":5,"generated":False,
             "bbox":{"l":31,"r":39,"b":27.5,"t":33}}
    source_sha = "a"*64
    structure = {"items":{},"source_sha256":source_sha}
    visibility = {1:{"edge_glyphs":{"source_sha256":source_sha if valid else "b"*64,"glyphs":[glyph]}}}
    before = doc.model_dump_json()
    result = _coverage_region(doc,picture,0,structure,[],visibility)
    assert doc.model_dump_json() == before
    if not valid:
        assert result is None
        return
    _,audit = result
    assert audit["crop_bbox"]["b"] == 27.5
    proof = audit["intersecting_body_sources"][0]
    assert proof["item_ref"] == label.self_ref and not proof["ownership_modified"]
    assert proof["source_glyphs"]["char_indices"] == [5]


def test_owned_children_outside_original_picture_add_a_complete_source_crop(tmp_path):
    path, doc, picture = fixture(tmp_path)
    child = doc.add_text(label=DocItemLabel.TEXT, text="Source owned child", parent=picture,
        prov=ProvenanceItem(page_no=1, charspan=(0,18), bbox=BoundingBox(
            l=100, r=120, b=40, t=60, coord_origin=CoordOrigin.BOTTOMLEFT)))
    before = doc.model_dump_json()
    row = run(doc, path)[1]["items"][0]
    assert (row["width"], row["height"]) == (80,100)
    coverage = row["content_coverage"]
    assert (coverage["width"],coverage["height"]) == (200,100)
    assert coverage["crop_bbox"]["r"] == 120
    assert coverage["source_children"][0]["item_ref"] == child.self_ref
    assert coverage["source_order_and_ownership_preserved"] and not coverage["semantic_relations_inferred"]
    payload = base64.b64decode(coverage["image"].split(",",1)[1])
    with Image.open(BytesIO(payload)) as image:
        assert image.getpixel((40,50)) == (255,0,0)
        assert image.getpixel((150,50)) == (255,255,255)
    assert doc.model_dump_json() == before


def test_unbounded_child_geometry_keeps_original_asset_and_records_coverage_review(tmp_path):
    path, doc, picture = fixture(tmp_path)
    doc.add_text(label=DocItemLabel.TEXT, text="outside", parent=picture,
        prov=ProvenanceItem(page_no=1,charspan=(0,7),bbox=BoundingBox(
            l=190,r=210,b=40,t=60,coord_origin=CoordOrigin.BOTTOMLEFT)))
    row = run(doc, path)[1]["items"][0]
    assert row["status"] == "exported" and "image" in row
    assert row["content_coverage"]["status"] == "review_required"
    assert "image" not in row["content_coverage"]


def title_source_geometry():
    from test_docling_picture_structure import panel_fixture
    from lib.infrastructure.docling_picture_structure import associate_external_picture_titles
    doc, mapping, heading, left, _, _, _ = panel_fixture()
    associations, _ = associate_external_picture_titles(doc, mapping)
    bbox = heading.prov[0].bbox.model_dump(mode="json")
    bbox["t"] += .3
    digest = mapping["source_sha256"]
    record = {"record_id":f"{digest}:page-1:object-0","source_sha256":digest,"page_no":1,
              "object_path":[0],"text":heading.text,"bbox":bbox}
    visibility = {1:{"status":"complete","source_sha256":digest,"records":[record]}}
    return doc,mapping,heading,left,associations,visibility


def test_external_title_coverage_uses_actual_source_object_ink_instead_of_native_box_padding():
    from lib.infrastructure.docling_picture_assets import _coverage_region
    doc,mapping,heading,left,associations,visibility = title_source_geometry()
    before = doc.model_dump_json()
    projected,coverage = _coverage_region(doc,left,0,mapping,associations,visibility)
    assert coverage["crop_bbox"]["t"] == 310.3
    assert projected.prov[0].bbox.t == 310.3
    source = coverage["spatial_title_sources"][0]
    assert source["original_parent_ref"] == "#/body"
    assert source["export_picture_ref"] == left.self_ref
    assert source["source_object"]["bbox"]["t"] == 310.3
    assert not coverage["source_children"]
    assert doc.model_dump_json() == before


@pytest.mark.parametrize("problem", ["duplicate","other_source","other_page","other_text","too_far"])
def test_ambiguous_or_unbound_glyph_object_cannot_claim_complete_title_coverage(problem):
    from copy import deepcopy
    from lib.infrastructure.docling_picture_assets import _coverage_region
    doc,mapping,_,left,associations,visibility = title_source_geometry()
    record = visibility[1]["records"][0]
    if problem == "duplicate": visibility[1]["records"].append(deepcopy(record))
    elif problem == "other_source": record["source_sha256"] = "b"*64
    elif problem == "other_page": record["page_no"] = 2
    elif problem == "other_text": record["text"] += " extra"
    else: record["bbox"]["t"] += 20
    with pytest.raises(ValueError,match="figure_title_ink_object_not_uniquely_bound"):
        _coverage_region(doc,left,0,mapping,associations,visibility)
