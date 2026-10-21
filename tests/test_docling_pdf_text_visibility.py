"""Clipped text requires a complete native object/coordinate/source proof."""
from copy import deepcopy
import hashlib

import pytest

from lib.infrastructure import docling_pdf_text_visibility as visibility


def box(l=0, b=0, r=10, t=10):
    return dict(l=l, b=b, r=r, t=t, coord_origin="BOTTOMLEFT")


def rect_segments():
    return [[0, 0, 2, False], [0, 10, 0, False], [10, 10, 0, False],
            [10, 0, 0, False], [0, 0, 0, True]]


def test_rectangle_requires_actual_single_closed_rect_not_bounding_box():
    assert visibility._rectangle(rect_segments()) == (0, 0, 10, 10)
    four = rect_segments()[:-1]
    four[-1][3] = True
    assert visibility._rectangle(four) == (0, 0, 10, 10)
    for bad in [rect_segments()[:-1], [[0, 0, 2, False], [0, 10, 0, False], [10, 0, 0, True]],
                rect_segments()+rect_segments()]:
        assert visibility._rectangle(bad) is None
    curve = rect_segments()
    curve[2][2] = 1
    assert visibility._rectangle(curve) is None
    diagonal = rect_segments()
    diagonal[1], diagonal[2] = diagonal[2], diagonal[1]
    assert visibility._rectangle(diagonal) is None


def test_matrix_composition_handles_rotation_reflection_translation_without_double_transform():
    translate = (1, 0, 0, 1, 40, 50)
    rotate_scale = (0, 2, -2, 0, 0, 0)
    composed = visibility._compose(translate, rotate_scale)
    assert visibility._point(composed, (3, 4)) == (32, 56)
    assert visibility._transformed_box((1, 2, 3, 4), composed) == (32, 52, 36, 56)
    assert visibility._transformed_box((1, 2, 3, 4), (-1, 0, 0, 1, 10, 0)) == (7, 2, 9, 4)
    with pytest.raises(ValueError):
        visibility._compose(translate, (1, 0, 0, 0, 0, 0))


def test_only_strict_rect_separation_proves_invisible_and_complex_paths_hold():
    clip = {"kind": "rectangle", "page_bbox": box()}
    assert visibility._classify((0, 12, 5, 20), [clip])[0] == "fully_clipped"
    for candidate in [(0, 5, 5, 20), (0, 10, 5, 20), (0, 10.005, 5, 20), (2, 2, 5, 5)]:
        assert visibility._classify(candidate, [clip])[0] == "unknown"
    assert visibility._classify((0, 12, 5, 20), [clip, {"kind": "complex_or_unavailable"}])[0] == "unknown"
    assert visibility._classify((0, 12, 5, 20), [])[0] == "unknown"


def proof_page():
    return {"source_sha256": "a"*64, "page_no": 1, "policy": visibility.PDF_TEXT_VISIBILITY_POLICY,
            "status": "complete", "records": [{"record_id": "one", "source_sha256": "a"*64,
            "page_no": 1, "text": "Same title ", "bbox": box(), "visibility": "fully_clipped",
            "fully_clipped": True}]}


def match(page, text="Same title", bbox=None, **kwargs):
    return visibility.match_text_visibility(page, text, bbox or box(),
                source_sha256=kwargs.get("sha", "a"*64), page_no=kwargs.get("page_no", 1))


def test_match_requires_complete_whole_unicode_and_exact_page_source_binding():
    page = proof_page()
    assert match(page)["fully_clipped"]
    assert not match(page, "Same")["fully_clipped"]
    assert not match(page, "same title")["fully_clipped"]
    assert not match(page, "Same title more")["fully_clipped"]
    assert not match(page, sha="b"*64)["fully_clipped"]
    assert not match(page, page_no=2)["fully_clipped"]
    assert not match(page, bbox=box(l=20, r=30))["fully_clipped"]
    page["records"][0]["text"] = "ﬁ"
    assert not match(page, "fi")["fully_clipped"]
    assert match(page, "ﬁ")["fully_clipped"]
    page["status"] = "incomplete"
    assert not match(page, "ﬁ")["fully_clipped"]


def test_duplicate_visible_invisible_overlays_are_ambiguous_never_picked_first():
    page = proof_page()
    visible = deepcopy(page["records"][0])
    visible.update(record_id="two", visibility="unknown", fully_clipped=False)
    page["records"].append(visible)
    assert match(page)["record_ids"] == ["one", "two"]
    assert not match(page)["fully_clipped"]
    # A second occurrence in another physical location is not a duplicate.
    visible["bbox"] = box(l=100, r=110)
    assert match(page)["fully_clipped"]


def native_pdf(*, clip="0 0 90 30 re W n", form_matrix="2 0 0 2 40 50", rotate=0, render_mode=0):
    """A small, real PDF with same-string visible and nested clipped objects."""
    def stream(header, data):
        return (header+f" /Length {len(data)} >>\nstream\n").encode()+data+b"\nendstream"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Rotate {rotate} "
         "/Resources << /Font << /F1 4 0 R >> /XObject << /Outer 6 0 R >> >> /Contents 5 0 R >>").encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        stream("<<", b"BT /F1 12 Tf 10 250 Td (Same) Tj ET /Outer Do"),
        stream("<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] /Matrix ["+form_matrix+"] "
               "/Resources << /XObject << /Inner 7 0 R >> >>", (f"q {clip} /Inner Do Q").encode()),
        stream("<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] "
               "/Resources << /Font << /F1 4 0 R >> >>", (f"BT /F1 12 Tf {render_mode} Tr 5 60 Td (Same) Tj ET").encode())]
    content = bytearray(b"%PDF-1.7\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{i} 0 obj\n".encode()+obj+b"\nendobj\n")
    xref = len(content)
    content.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode())
    content.extend(f"trailer\n<< /Root 1 0 R /Size {len(offsets)} >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(content)


def test_official_pdfium_nested_form_proof_is_source_bound_and_object_specific():
    pdf = native_pdf()
    before = bytes(pdf)
    page = visibility.read_pdf_text_visibility(pdf, page_numbers=[1])[1]
    assert pdf == before and page["status"] == "complete"
    assert page["source_sha256"] == hashlib.sha256(pdf).hexdigest()
    assert len(page["records"]) == 2
    visible, hidden = page["records"]
    assert visible["text"] == hidden["text"] == "Same"
    assert not visible["fully_clipped"] and hidden["fully_clipped"]
    assert hidden["object_path"] == [1, 0, 0]
    assert hidden["proof"]["parent_to_page_matrix"] == [2, 0, 0, 2, 40, 50]
    assert hidden["bbox"]["b"] > 169 and hidden["bbox"]["t"] < 190
    assert hidden["proof"]["excluding_clip"]["page_bbox"]["t"] == 110
    assert hidden["proof"]["excluding_clip"]["owner_object_path"] != hidden["object_path"]
    assert visibility.match_text_visibility(page, "Same", hidden["bbox"],
        source_sha256=page["source_sha256"], page_no=1)["fully_clipped"]


@pytest.mark.parametrize("clip", ["0 50 90 20 re W n", "0 0 m 90 0 l 45 30 l h W n"])
def test_official_pdfium_partial_or_nonrectangular_clip_remains_unknown(clip):
    page = visibility.read_pdf_text_visibility(native_pdf(clip=clip), page_numbers=[1])[1]
    assert page["status"] == "complete"
    assert not any(record["fully_clipped"] for record in page["records"])


def test_official_pdfium_rotated_form_composes_clip_and_text_same_way():
    page = visibility.read_pdf_text_visibility(native_pdf(form_matrix="0 2 -2 0 220 30"), page_numbers=[1])[1]
    assert page["status"] == "complete"
    hidden = page["records"][1]
    assert hidden["fully_clipped"]
    assert hidden["bbox"]["r"] < hidden["proof"]["excluding_clip"]["page_bbox"]["l"]


def test_page_rotation_and_stroked_text_are_not_certified():
    assert visibility.read_pdf_text_visibility(native_pdf(rotate=90), page_numbers=[1])[1]["status"] == "unsupported_coordinate_space"
    page = visibility.read_pdf_text_visibility(native_pdf(render_mode=1), page_numbers=[1])[1]
    assert not any(record["fully_clipped"] for record in page["records"])


def test_resource_limit_revokes_partial_inventory_and_old_api_fails_explicitly(monkeypatch):
    monkeypatch.setattr(visibility, "_MAX_OBJECTS", 1)
    page = visibility.read_pdf_text_visibility(native_pdf(), page_numbers=[1])[1]
    assert page["status"] == "incomplete" and page["review_required"]
    assert not any(record["fully_clipped"] for record in page["records"])
    import pypdfium2.raw as raw
    monkeypatch.setattr(raw, "FPDFPageObj_GetClipPath", None)
    with pytest.raises(ValueError, match="api_unsupported"):
        visibility.read_pdf_text_visibility(native_pdf(), page_numbers=[1])


@pytest.mark.parametrize("pages", [[0], [True], [2], [1, 1]])
def test_page_selection_never_silently_uses_different_physical_page(pages):
    with pytest.raises(ValueError, match="invalid_pages"):
        visibility.read_pdf_text_visibility(native_pdf(), page_numbers=pages)


def complete_native_binding():
    page = visibility.read_pdf_text_visibility(native_pdf(), page_numbers=[1])[1]
    hidden = page["records"][1]
    line = {"line_index": 1, "line_id": "page-1:line-1", "page_no": 1,
            "source_sha256": page["source_sha256"], "native_text": hidden["text"],
            "bbox": hidden["bbox"], "coordinate_space_supported": True,
            "from_ocr": False, "rendering_mode": -1}
    proof = visibility.match_text_visibility(page, line["native_text"], line["bbox"],
                                             source_sha256=page["source_sha256"], page_no=1)
    return page, line, proof


def validate_binding(page, line, proof):
    return visibility.validate_clipped_text_binding(proof, line, source_sha256=page["source_sha256"], page_no=1)


def test_export_validator_accepts_real_complete_unique_binding_and_rejects_same_text_other_location():
    page, line, proof = complete_native_binding()
    assert validate_binding(page, line, proof)
    visible_line = dict(line, bbox=page["records"][0]["bbox"], line_index=0, line_id="page-1:line-0")
    assert visible_line["native_text"] == line["native_text"]
    assert not validate_binding(page, visible_line, proof)
    # Changing the declared match bbox does not change the actual object's bbox.
    swapped = deepcopy(proof)
    swapped["native_bbox"] = visible_line["bbox"]
    assert not validate_binding(page, visible_line, swapped)


@pytest.mark.parametrize("change", ["missing_contract", "incomplete", "ambiguous", "reason",
    "record_text", "record_page", "record_source", "record_id", "record_path", "record_ids",
    "record_bbox", "parent_transform", "ancestor_transform", "clip_owner", "clip_local",
    "clip_page", "clip_segments", "clip_not_disjoint", "rendering_mode", "native_text",
    "native_source", "native_page", "native_ocr", "native_id"])
def test_export_validator_rejects_swapped_stale_and_inconsistent_proof(change):
    page, line, proof = complete_native_binding()
    record, evidence = proof["record"], proof["record"]["proof"]
    clip = evidence["effective_clips"][0]
    if change == "missing_contract": proof.pop("binding_policy")
    elif change == "incomplete": proof["page_inventory_status"] = "incomplete"
    elif change == "ambiguous": proof["matching_object_count"] = 2
    elif change == "reason": proof["reason"] = "visibility_not_proven"
    elif change == "record_text": record["text"] = "Other"
    elif change == "record_page": record["page_no"] = 2
    elif change == "record_source": record["source_sha256"] = "b"*64
    elif change == "record_id": record["record_id"] += "0"
    elif change == "record_path": record["object_path"][-1] += 1
    elif change == "record_ids": proof["record_ids"].append("duplicate")
    elif change == "record_bbox": record["bbox"]["t"] += 1
    elif change == "parent_transform": evidence["parent_to_page_matrix"][4] += 10
    elif change == "ancestor_transform": evidence["ancestor_forms"][0]["matrix"][4] += 10
    elif change == "clip_owner": clip["owner_object_path"] = [999]
    elif change == "clip_local": clip["local_bbox"]["t"] += 1
    elif change == "clip_page": clip["page_bbox"]["t"] += 1
    elif change == "clip_segments": clip["segments"][1][2] = 1
    elif change == "clip_not_disjoint":
        # Make every recorded clip coordinate internally consistent but place
        # it across the text: the boolean fully_clipped is not evidence.
        for segment in clip["segments"]: segment[1] += 50
        clip["local_bbox"] = box(l=0, b=50, r=90, t=80)
        clip["page_bbox"] = box(l=40, b=150, r=220, t=210)
    elif change == "rendering_mode": evidence["rendering_mode"] = 2
    elif change == "native_text": line["native_text"] += " more"
    elif change == "native_source": line["source_sha256"] = "b"*64
    elif change == "native_page": line["page_no"] = 2
    elif change == "native_ocr": line["from_ocr"] = True
    elif change == "native_id": line["line_id"] = "page-2:line-1"
    assert not validate_binding(page, line, proof)


def test_export_validator_does_not_accept_flags_without_object_evidence():
    page, line, _ = complete_native_binding()
    thin = {"fully_clipped": True, "record": {"visibility": "fully_clipped", "fully_clipped": True,
            "source_sha256": page["source_sha256"], "page_no": 1,
            "proof": {"excluding_clip": {"kind": "verified_ancestor_rectangle"}}}}
    assert not validate_binding(page, line, thin)
