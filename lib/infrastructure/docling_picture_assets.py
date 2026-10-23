"""Keep bounded source figure pixels using Docling's provenance crop API.

The assets live beside parsed text in the durable source-library payload. No
local paths, generated descriptions, or base64 blobs are inserted into training
text. Only one bounded page raster is retained at a time.
"""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
from io import BytesIO
import math


PICTURE_ASSET_POLICY = "docling_source_members_and_intersecting_label_ink_v4"
MAX_PAGE_EDGE = 2400
MAX_PAGE_PIXELS = 6_000_000
MAX_ASSET_BYTES = 2 * 1024 * 1024
MAX_TOTAL_ASSET_BYTES = 8 * 1024 * 1024
MAX_PICTURE_REGIONS = 1000


def _region(document, picture, ordinal):
    prov = picture.prov[ordinal]
    page = document.pages.get(prov.page_no)
    if page is None or page.size is None:
        raise ValueError("document_docling_invalid_provenance")
    width, height = page.size.width, page.size.height
    box = prov.bbox.to_top_left_origin(page_height=height)
    values = [width, height, box.l, box.t, box.r, box.b]
    if (not all(isinstance(x, (int, float)) and math.isfinite(x) for x in values)
            or not 0 <= box.l < box.r <= width
            or not 0 <= box.t < box.b <= height):
        raise ValueError("document_docling_invalid_provenance")
    return prov.page_no, page, {
        "picture_ref": picture.self_ref, "provenance_index": ordinal,
        "source_page": prov.page_no, "source_bbox": prov.bbox.model_dump(mode="json"),
        "page_size": page.size.model_dump(mode="json"),
        "source_role": "original_pdf_picture_pixels",
    }


def _coverage_region(document, picture, ordinal, structure, associations, visibility):
    """Enclose source-owned displayed children without inventing panel owners."""
    from docling_core.types.doc import BoundingBox, CoordOrigin
    prov = picture.prov[ordinal]
    page = document.pages[prov.page_no]
    original = prov.bbox.to_bottom_left_origin(page.size.height)
    boxes, sources, title_sources, edge_sources = [original], [], [], []
    for child, _ in document.iterate_items(root=picture, traverse_pictures=True):
        label = getattr(getattr(child, "label", None), "value", None)
        if child.self_ref == picture.self_ref or label in {"caption", "footnote", "picture"}:
            continue
        plan = (structure or {}).get("items", {}).get(child.self_ref)
        if plan and not plan.get("review_required") and plan.get("page_no") == prov.page_no:
            from .docling_picture_export import _project_text
            _project_text(child, structure, prov.page_no, page_height=page.size.height)
            segments = [(segment["native_line"]["bbox"], segment["source_charspan"],
                         segment["native_line"]["line_id"]) for segment in plan["segments"] if segment["display"]]
        else:
            segments = [(p.bbox.model_dump(mode="json"), list(p.charspan), None)
                        for p in getattr(child, "prov", []) if p.page_no == prov.page_no]
        for value, charspan, line_id in segments:
            box = BoundingBox.model_validate(value).to_bottom_left_origin(page.size.height)
            boxes.append(box)
            sources.append({"item_ref": child.self_ref, "source_charspan": charspan,
                            "native_line_id": line_id, "bbox": box.model_dump(mode="json")})
    from .docling_picture_structure import _compact, _bounds
    for association in associations:
        if association["picture_ref"] != picture.self_ref or association["page_no"] != prov.page_no:
            continue
        plan = structure["items"][association["title_ref"]]
        for segment in plan["segments"]:
            if not segment["display"]:
                continue
            line = segment["native_line"]
            native = BoundingBox.model_validate(line["bbox"]).to_bottom_left_origin(page.size.height)
            boxes.append(native)
            # PDFium object ink may extend fractionally beyond a native
            # typographic line box. Match a complete unique text object near
            # that exact line, then include its actual bounds, not padding.
            page_record = visibility.get(prov.page_no, {})
            matches = []
            if (page_record.get("status") == "complete" and page_record.get("source_sha256") == structure["source_sha256"]):
                for record in page_record.get("records", []):
                    if (record.get("page_no") != prov.page_no or record.get("source_sha256") != structure["source_sha256"]
                            or _compact(record.get("text", "")) != _compact(line["native_text"])):
                        continue
                    box = _bounds(record["bbox"])
                    if native.l-.5 <= box[0] and native.b-.5 <= box[1] and native.r+.5 >= box[2] and native.t+.5 >= box[3]:
                        matches.append(record)
            if len(matches) != 1:
                raise ValueError("figure_title_ink_object_not_uniquely_bound")
            record = matches[0]
            ink = BoundingBox.model_validate(record["bbox"]).to_bottom_left_origin(page.size.height)
            boxes.append(ink)
            title_sources.append({"item_ref": association["title_ref"], "source_charspan": segment["source_charspan"],
                "native_line_id": line["line_id"], "bbox": native.model_dump(mode="json"),
                "original_parent_ref": association["original_parent_ref"], "export_picture_ref": picture.self_ref,
                "source_role": "spatially_associated_body_title", "object_binding_tolerance_points": .5,
                "source_object": {key: record[key] for key in ("record_id", "source_sha256", "page_no", "object_path", "text", "bbox")}})
    # Some native figure labels remain body text. If an original one-line box
    # is predominantly inside exactly one picture but its ink crosses an edge,
    # keep a separate complete crop. Do not reparent it or duplicate its text.
    if structure:
        for item in document.texts:
            if (getattr(item.label, "value", item.label) != "text" or len(item.prov) != 1
                    or item.prov[0].page_no != prov.page_no or item.parent is None
                    or item.parent.cref != document.body.self_ref):
                continue
            box = item.prov[0].bbox.to_bottom_left_origin(page.size.height)
            height = box.t-box.b
            if not 0 < height <= 24:
                continue
            hit = []
            for owner in document.pictures:
                for p in owner.prov:
                    if p.page_no != prov.page_no:
                        continue
                    region = p.bbox.to_bottom_left_origin(page.size.height)
                    overlap = max(0, min(region.r,box.r)-max(region.l,box.l))*max(0,min(region.t,box.t)-max(region.b,box.b))
                    if (region.l <= (box.l+box.r)/2 <= region.r and region.b <= (box.b+box.t)/2 <= region.t
                            and overlap >= .5*(box.r-box.l)*height):
                        hit.append(owner.self_ref)
            overflow = max(original.l-box.l,box.r-original.r,original.b-box.b,box.t-original.t)
            if hit != [picture.self_ref] or not .25 < overflow <= height/2:
                continue
            records = [r for r in visibility.get(prov.page_no, {}).get("records", [])
                       if r.get("source_sha256") == structure["source_sha256"]
                       and _compact(r.get("text", "")) == _compact(item.text)
                       and box.l-.5 <= r["bbox"]["l"] and box.r+.5 >= r["bbox"]["r"]
                       and box.b-.5 <= r["bbox"]["b"] and box.t+.5 >= r["bbox"]["t"]]
            if len(records) == 1:
                record = records[0]
                ink = BoundingBox.model_validate(record["bbox"]).to_bottom_left_origin(page.size.height)
                binding = {"source_object": {key: record[key] for key in
                           ("record_id", "source_sha256", "page_no", "object_path", "text", "bbox")}}
            else:
                glyph_page = visibility.get(prov.page_no, {}).get("edge_glyphs", {})
                glyphs = [g for g in glyph_page.get("glyphs", []) if g["text"].strip() and not g.get("generated")
                          and box.l <= (g["bbox"]["l"]+g["bbox"]["r"])/2 <= box.r
                          and box.b <= (g["bbox"]["b"]+g["bbox"]["t"])/2 <= box.t]
                if (glyph_page.get("review_required") or glyph_page.get("source_sha256") != structure["source_sha256"]
                        or not glyphs or _compact("".join(g["text"] for g in glyphs)) != _compact(item.text)):
                    continue
                ink = BoundingBox(l=min(g["bbox"]["l"] for g in glyphs), b=min(g["bbox"]["b"] for g in glyphs),
                                  r=max(g["bbox"]["r"] for g in glyphs), t=max(g["bbox"]["t"] for g in glyphs),
                                  coord_origin=CoordOrigin.BOTTOMLEFT)
                binding = {"source_glyphs": {"char_indices": [g["char_index"] for g in glyphs],
                           "source_sha256": glyph_page["source_sha256"], "page_no": prov.page_no,
                           "text": item.text, "ink_bbox": ink.model_dump(mode="json")}}
            boxes.extend([box, ink])
            edge_sources.append({"item_ref": item.self_ref, "original_parent_ref": item.parent.cref,
                "source_role": "source_body_text_intersecting_unique_picture_edge", "ownership_modified": False,
                "bbox": box.model_dump(mode="json"), **binding})
    union = BoundingBox(l=min(box.l for box in boxes), r=max(box.r for box in boxes),
                        b=min(box.b for box in boxes), t=max(box.t for box in boxes),
                        coord_origin=CoordOrigin.BOTTOMLEFT)
    if max(original.l-union.l, union.r-original.r, original.b-union.b, union.t-original.t) <= .25:
        return None
    projected = picture.model_copy(update={"image": None, "prov": [
        p.model_copy(update={"bbox": union}) if index == ordinal else p for index, p in enumerate(picture.prov)]})
    _region(document, projected, ordinal)  # Reject invalid geometry; never pad/clamp missing source.
    return projected, {"crop_bbox": union.model_dump(mode="json"), "source_children": sources,
                       "spatial_title_sources": title_sources,
                       "intersecting_body_sources": edge_sources,
                       "source_order_and_ownership_preserved": True,
                       "semantic_relations_inferred": False,
                       "source_role": "original_pdf_picture_with_displayed_source_members"}


def _encode(crop, total):
    if crop is None or crop.width < 1 or crop.height < 1:
        return {"status": "review_required", "reason": "empty_source_crop"}, total
    output = BytesIO()
    crop.save(output, format="PNG")
    payload = output.getvalue()
    if len(payload) > MAX_ASSET_BYTES or total + len(payload) > MAX_TOTAL_ASSET_BYTES:
        return {"status": "review_required", "reason": "picture_asset_size_limit"}, total
    return {"status": "exported", "image_sha256": hashlib.sha256(payload).hexdigest(),
            "width": crop.width, "height": crop.height, "bytes": len(payload), "mime_type": "image/png",
            "source_region_is_blank_white": all(bounds == (255, 255) for bounds in crop.getextrema()),
            "image": "data:image/png;base64," + base64.b64encode(payload).decode("ascii")}, total + len(payload)


def extract_picture_assets(document, source, *, source_sha256, picture_structure=None):
    """Return per-page PNG payloads and explicit failures, without model calls.

    Uses official ``DocItem.get_image`` on a shallow view with a locally
    rendered page. Existing picture image URIs are deliberately ignored: the
    supplied original PDF and its provenance are the only image source.
    """
    pictures = list(getattr(document, "pictures", []))
    if not pictures:
        return {}
    if picture_structure is not None and picture_structure.get("source_sha256") != source_sha256:
        raise ValueError("document_docling_invalid_output")
    associations, visibility = [], {}
    if picture_structure:
        from .docling_picture_structure import validated_spatial_title_associations
        from .docling_pdf_text_visibility import read_pdf_text_visibility
        associations = validated_spatial_title_associations(document, picture_structure)
        visibility = read_pdf_text_visibility(source, page_numbers=sorted({p.page_no for item in pictures for p in item.prov}))
        from .docling_math_regions import extract_pdf_page_glyphs
        for number in visibility:
            visibility[number]["edge_glyphs"] = extract_pdf_page_glyphs(source, number)
    regions = {}
    count = sum(len(picture.prov) for picture in pictures)
    if count > MAX_PICTURE_REGIONS:
        raise ValueError("document_docling_provenance_limit")
    for picture in pictures:
        if not picture.prov:
            raise ValueError("document_docling_invalid_provenance")
        for ordinal in range(len(picture.prov)):
            number, page, record = _region(document, picture, ordinal)
            regions.setdefault(number, []).append((picture, ordinal, page, record))

    import pypdfium2 as pdfium
    from docling_core.types.doc import ImageRef

    outputs, total = {}, 0
    with closing(pdfium.PdfDocument(str(source))) as pdf:
        for number, items in sorted(regions.items()):
            if not 1 <= number <= len(pdf):
                raise ValueError("document_docling_invalid_provenance")
            audit = {"policy": PICTURE_ASSET_POLICY, "page_no": number,
                     "source_pdf_sha256": source_sha256, "items": [],
                     "review_required": False, "inference_performed": False}
            outputs[number] = audit
            page = pdf[number - 1]
            bitmap = page_image = None
            try:
                width, height = page.get_size()
                declared = items[0][2].size
                if (not all(math.isfinite(x) and 0 < x <= 100_000 for x in (width, height))
                        or abs(width - declared.width) > .5 or abs(height - declared.height) > .5):
                    raise ValueError("document_docling_invalid_provenance")
                scale = min(2., MAX_PAGE_EDGE / max(width, height),
                            math.sqrt(MAX_PAGE_PIXELS / (width * height)))
                # Rounding to integer bitmap dimensions is handled by PDFium;
                # Docling then maps source coordinates to those exact pixels.
                bitmap = page.render(scale=scale)
                page_image = bitmap.to_pil().convert("RGB")
                image_ref = ImageRef.from_pil(page_image, dpi=max(1, round(72 * scale)))
                pages = dict(document.pages)
                pages[number] = items[0][2].model_copy(update={"image": image_ref})
                view = document.model_copy(update={"pages": pages})
                for picture, ordinal, _, record in items:
                    crop = None
                    try:
                        # FloatingItem.get_image would prefer an existing URI.
                        # Force the official source-provenance crop instead.
                        source_picture = picture.model_copy(update={"image": None})
                        crop = source_picture.get_image(view, prov_index=ordinal)
                        encoded, total = _encode(crop, total)
                        record.update(encoded, dpi=image_ref.dpi)
                    finally:
                        if crop is not None:
                            crop.close()
                    if record["status"] != "exported":
                        audit["review_required"] = True
                    try:
                        expanded = _coverage_region(document, picture, ordinal, picture_structure, associations, visibility)
                    except ValueError:
                        expanded = None
                        record["content_coverage"] = {"status": "review_required", "reason": "invalid_child_coverage_geometry"}
                        audit["review_required"] = True
                    if expanded is not None:
                        projected, coverage = expanded
                        crop = projected.get_image(view, prov_index=ordinal)
                        try:
                            encoded, total = _encode(crop, total)
                            coverage.update(encoded, dpi=image_ref.dpi)
                            record["content_coverage"] = coverage
                            if encoded["status"] != "exported":
                                audit["review_required"] = True
                        finally:
                            if crop is not None:
                                crop.close()
                    audit["items"].append(record)
                # Release the view's PIL cache before rendering the next page.
                del view, pages, image_ref
            finally:
                if page_image is not None:
                    page_image.close()
                if bitmap is not None:
                    bitmap.close()
                page.close()
    return outputs
