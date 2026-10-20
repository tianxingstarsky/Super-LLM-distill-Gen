"""Conservative, local evidence for detector crops; never reconstruct paragraph text.

Coordinates are unrotated PDF points. ``page_image`` must be a full-page raster
of the same PDF page; ``render_inline_page_evidence`` provides that pairing.
The only repairs attempted are completing a contiguous math-font identifier and
removing partial ink from a detached ordinary-text baseline outside the detector.
Fractions, radicals, delimiters and sentence boundaries are not parsed or guessed.

Raster checks establish what pixels changed, not whether a formula is correct.
Even a successful result does not authorize automatic source-text replacement.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path
import statistics
import unicodedata

from .docling_math_regions import _box, _box_dict, extract_pdf_page_glyphs, is_math_font


INLINE_GEOMETRY_POLICY = "pdfium_inline_glyph_raster_owned_boundaries_v3"


def _intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _contains(a, b, tolerance=0):
    return (a[0] - tolerance <= b[0] and a[1] - tolerance <= b[1]
            and a[2] + tolerance >= b[2] and a[3] + tolerance >= b[3])


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _math(glyph):
    return is_math_font(glyph["font_name"]) or any(
        unicodedata.category(c) == "Sm" for c in glyph["text"])


def _identifier(glyph):
    # Connector punctuation bridges font changes in identifiers. No word list,
    # LaTeX answer, recognized string or guessed operator continuation is used.
    return ((glyph["text"].isalpha() and is_math_font(glyph["font_name"]))
            or all(unicodedata.category(c) == "Pc" for c in glyph["text"]))


def _baseline(glyph):
    return glyph["origin"][1]


def _same_baseline(a, b):
    size = max(a["font_size"], b["font_size"])
    return (abs(a["font_size"] - b["font_size"]) <= .1 * size
            and abs(_baseline(a) - _baseline(b)) <= .08 * size)


def _pixel_box(box, page, image_size):
    sx, sy = image_size[0] / page["width"], image_size[1] / page["height"]
    return (max(0, math.floor(box[0] * sx)),
            max(0, math.floor((page["height"] - box[3]) * sy)),
            min(image_size[0], math.ceil(box[2] * sx)),
            min(image_size[1], math.ceil((page["height"] - box[1]) * sy)))


def _pixel_evidence(page, image, original, proposed, retained, excluded, added):
    from PIL import Image, ImageChops, ImageDraw

    if image.width * image.height > 20_000_000:
        raise ValueError("inline_raster_too_large")
    # Rounded raster dimensions may differ by one pixel, but a resized/cropped
    # image is not a substitute for a full-page render.
    if abs(image.width * page["height"] / page["width"] - image.height) > 1.01:
        raise ValueError("inline_raster_dimensions_mismatch")
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        image = white.convert("RGB")
    gray = image.convert("L")
    region = _pixel_box(_union([original, proposed]), page, image.size)
    local = gray.crop(region)
    ink = local.point(lambda value: 255 if value <= 128 else 0)

    def mask(boxes, dilate=0):
        result = Image.new("L", local.size)
        draw = ImageDraw.Draw(result)
        for box in boxes:
            l, t, r, b = _pixel_box(box, page, image.size)
            # PIL rectangles include their last coordinate; crop boxes do not.
            draw.rectangle((l - region[0] - dilate, t - region[1] - dilate,
                            r - region[0] - 1 + dilate, b - region[1] - 1 + dilate), fill=255)
        return result

    def count(value):
        return value.histogram()[255]

    original_mask, proposed_mask = mask([original]), mask([proposed])
    removed = ImageChops.subtract(original_mask, proposed_mask)
    added_mask = ImageChops.subtract(proposed_mask, original_mask)
    # One raster pixel accommodates glyph-box rounding and raster antialiasing.
    retained_mask, excluded_mask = mask([g["_box"] for g in retained], 1), mask(
        [g["_box"] for g in excluded], 1)
    explained = ImageChops.lighter(retained_mask, excluded_mask)
    original_ink = ImageChops.multiply(ink, original_mask)
    removed_ink = ImageChops.multiply(ink, removed)
    added_ink = ImageChops.multiply(ink, added_mask)
    proposed_pixels = _pixel_box(proposed, page, image.size)
    crop = gray.crop(proposed_pixels).point(lambda value: 255 if value <= 128 else 0)
    edges = {
        "left": count(crop.crop((0, 0, 1, crop.height))),
        "right": count(crop.crop((crop.width - 1, 0, crop.width, crop.height))),
        "top": count(crop.crop((0, 0, crop.width, 1))),
        "bottom": count(crop.crop((0, crop.height - 1, crop.width, crop.height))),
    }
    return {
        "threshold": 128, "glyph_mask_dilation_pixels": 1,
        "page_pixel_size": list(image.size), "original_pixel_box": list(_pixel_box(original, page, image.size)),
        "proposed_pixel_box": list(proposed_pixels),
        "raster_sha256": hashlib.sha256(gray.tobytes()).hexdigest(),
        "original_ink_pixels": count(original_ink),
        "proposed_ink_pixels": count(ImageChops.multiply(ink, proposed_mask)),
        "added_ink_pixels": count(added_ink), "removed_ink_pixels": count(removed_ink),
        "unassigned_original_ink_pixels": count(ImageChops.subtract(original_ink, explained)),
        "unassigned_added_ink_pixels": count(ImageChops.subtract(added_ink, retained_mask)),
        "unassigned_removed_ink_pixels": count(ImageChops.subtract(removed_ink, excluded_mask)),
        "retained_glyph_ink_removed_pixels": count(ImageChops.multiply(removed_ink, retained_mask)),
        "proposed_edge_ink_pixels": edges,
        "added_glyph_ink_pixels": {
            str(g["char_index"]): count(ImageChops.multiply(ink, mask([g["_box"]])))
            for g in added
        },
    }


def refine_inline_formula_geometry(page, detector_bbox, paragraph_bbox, *,
                                   page_image=None, source_ref=None, fragment_id=None,
                                   coord_origin="BOTTOMLEFT", allow_math_delimiters=False):
    """Return a proposed crop and auditable evidence, retaining the input on doubt.

    ``page`` is the result of ``extract_pdf_page_glyphs``. All glyphs are examined
    using their ink boxes and baselines, not PDF extraction order. The paragraph
    restricts the search; it is never replaced or combined with another fragment.
    Missing raster evidence, ordinary text mixed into the formula, unsupported
    coordinates or unexplained changed ink require review.
    """
    original = _box(detector_bbox, page["height"], coord_origin)
    paragraph = _box(paragraph_bbox, page["height"], coord_origin)
    result = {
        "policy": INLINE_GEOMETRY_POLICY, "page_no": page["page_no"],
        "source_sha256": page.get("source_sha256"), "source_ref": source_ref,
        "fragment_id": fragment_id, "detector_bbox": _box_dict(original),
        "paragraph_bbox": _box_dict(paragraph), "bbox": _box_dict(original),
        "proposed_bbox": _box_dict(original), "review_required": True,
        "status": "review_required", "review_reasons": [], "char_indices": [],
        "added_char_indices": [], "excluded_char_indices": [], "pixel_evidence": None,
        "automatic_text_replacement_allowed": False,
    }
    reasons = result["review_reasons"]
    coordinate = page.get("coordinate_space", {})
    if (page.get("review_required") or page.get("status") == "unsupported_coordinate_space"
            or coordinate.get("page_rotation", 0) != 0
            or any(abs(v) > .01 for key in ("crop_bbox", "visible_bbox")
                   for v in coordinate.get(key, [0, 0])[:2])):
        reasons.append("unsupported_pdf_coordinate_space")
        return result
    bounds = (0, 0, page["width"], page["height"])
    if not _contains(bounds, original) or not _contains(bounds, paragraph):
        reasons.append("bbox_outside_page")
        return result
    if not _intersects(original, paragraph):
        reasons.append("detector_outside_source_paragraph")
        return result

    glyphs = []
    for source in page["glyphs"]:
        if not source["text"].strip():
            continue
        try:
            box = _box(source["bbox"], page["height"])
        except (KeyError, ValueError, TypeError):
            reasons.append("invalid_pdf_glyph_geometry")
            return result
        if not _intersects(box, paragraph):
            continue
        glyph = dict(source, _box=box)
        if (not glyph.get("origin") or len(glyph["origin"]) != 2
                or not all(math.isfinite(v) for v in glyph["origin"])
                or not math.isfinite(glyph["font_size"]) or glyph["font_size"] <= 0):
            reasons.append("missing_pdf_glyph_baseline")
            return result
        glyphs.append(glyph)
    if len({g["char_index"] for g in glyphs}) != len(glyphs):
        reasons.append("duplicate_pdf_character_ids")
        return result
    selected = [g for g in glyphs if _intersects(g["_box"], original)]
    anchors = [g for g in selected if _math(g)]
    if not anchors:
        result["char_indices"] = sorted(g["char_index"] for g in selected)
        reasons.append("no_pdf_math_anchor")
        return result
    if any(g.get("generated") for g in selected):
        reasons.append("generated_visible_pdf_glyph")

    largest = max(g["font_size"] for g in anchors)
    baseline = statistics.median(_baseline(g) for g in anchors if g["font_size"] >= .9 * largest)
    math_top, math_bottom = max(g["_box"][3] for g in anchors), min(g["_box"][1] for g in anchors)
    excluded = []
    for glyph in selected:
        box = glyph["_box"]
        # Only an edge fragment whose baseline is outside the detector may be
        # excluded. Fully contained ordinary numerators/script labels survive.
        outside_baseline = _baseline(glyph) > original[3] or _baseline(glyph) < original[1]
        overlap_height = min(box[3], original[3]) - max(box[1], original[1])
        detached = box[1] > math_top or box[3] < math_bottom
        if (not _math(glyph) and glyph["text"].isalpha()
                and glyph["font_size"] >= .9 * largest
                and abs(_baseline(glyph) - baseline) > .9 * largest
                and outside_baseline and detached and overlap_height < .25 * (box[3] - box[1])):
            companions = [g for g in glyphs if g["text"].isalpha() and not _math(g)
                          and _same_baseline(glyph, g)
                          and g["_box"][0] < original[2] and g["_box"][2] > original[0]]
            if len(companions) >= 2:
                excluded.append(glyph)
    excluded_ids = {g["char_index"] for g in excluded}
    retained = [g for g in selected if g["char_index"] not in excluded_ids]
    selected_ids = {g["char_index"] for g in selected}

    # Close only a tight same-baseline math-font identifier. This is not a
    # general formula continuation rule, and never crosses a prose character.
    while True:
        retained_ids = {g["char_index"] for g in retained}
        additions = []
        for glyph in glyphs:
            if glyph["char_index"] in retained_ids or not _identifier(glyph):
                continue
            for neighbor in retained:
                if not _identifier(neighbor) or not _same_baseline(glyph, neighbor):
                    continue
                left, right = sorted((glyph, neighbor), key=lambda g: g["_box"][0])
                gap = right["_box"][0] - left["_box"][2]
                if not -.2 * largest <= gap <= .25 * largest:
                    continue
                if any(other["char_index"] not in (glyph["char_index"], neighbor["char_index"])
                       and left["_box"][2] < (other["_box"][0] + other["_box"][2]) / 2 < right["_box"][0]
                       and _same_baseline(other, glyph) for other in glyphs):
                    continue
                additions.append(glyph)
                break
        if not additions:
            break
        retained.extend(additions)
    added = [g for g in retained if g["char_index"] not in selected_ids]
    if any(g.get("generated") for g in retained):
        reasons.append("generated_visible_pdf_glyph")
    retained_ids = {g["char_index"] for g in retained}
    for glyph in glyphs:
        if glyph["char_index"] in retained_ids or glyph["char_index"] in excluded_ids:
            continue
        for base in retained:
            size = base["font_size"]
            offset = abs(_baseline(glyph) - _baseline(base))
            horizontal_gap = max(glyph["_box"][0] - base["_box"][2],
                                 base["_box"][0] - glyph["_box"][2], 0)
            if (_math(base) and glyph["font_size"] < .88 * size
                    and .12 * size < offset < .95 * size
                    and horizontal_gap <= .55 * size):
                # A wholly missed script has no detector support. Do not guess
                # its attachment, or silently approve the apparently clean box.
                reasons.append("nearby_unselected_script_requires_review")
                break

    # Main-baseline roman letters may be prose or a roman mathematical name.
    # Geometry alone cannot distinguish them, including short words.
    boundary_proven = False
    if allow_math_delimiters:
        from .docling_inline_boundaries import mathematical_boundary_proof
        proof = mathematical_boundary_proof(retained, source_glyphs=page["glyphs"])
        result["mathematical_boundary"] = proof
        boundary_proven = proof["proven"]
    if not boundary_proven and any(g["text"].isalpha() and not _math(g) and g["font_size"] >= .88 * largest
           for g in retained):
        reasons.append("ordinary_text_formula_boundary_ambiguous")
    if not boundary_proven and any(any(unicodedata.category(c) in ("Ps", "Pe") for c in g["text"])
           and not is_math_font(g["font_name"]) for g in retained):
        reasons.append("ordinary_delimiter_boundary_ambiguous")

    # Expand intersected glyphs fully; padding is exactly one raster pixel when
    # a render is available. Keep original empty margins except the excluded edge.
    padding = max(page["width"] / page_image.width, page["height"] / page_image.height) if page_image else .25
    ink_box = _union([g["_box"] for g in retained])
    proposed = list(_union([original, (ink_box[0] - padding, ink_box[1] - padding,
                                     ink_box[2] + padding, ink_box[3] + padding)]))
    if excluded:
        if any(g["_box"][1] > math_top for g in excluded):
            proposed[3] = ink_box[3] + padding
        if any(g["_box"][3] < math_bottom for g in excluded):
            proposed[1] = ink_box[1] - padding
    proposed = tuple(proposed)
    if not _contains(bounds, proposed):
        reasons.append("proposed_crop_outside_page")
    # Native layout boxes and PDFium ink boxes differ at font ascenders and
    # descenders. Complete the originally selected glyph only when its center
    # still belongs to this paragraph; never extend ownership to a neighbor.
    owned_overhang = all(
        g["char_index"] in selected_ids
        and paragraph[0] <= (g["_box"][0] + g["_box"][2]) / 2 <= paragraph[2]
        and paragraph[1] <= (g["_box"][1] + g["_box"][3]) / 2 <= paragraph[3]
        for g in retained)
    result["paragraph_glyph_ownership"] = {
        "all_original_glyph_centers_inside": owned_overhang,
        "requires_unique_native_alignment_before_publication": True}
    overhang = not _contains(paragraph, ink_box, tolerance=padding)
    if overhang:
        if not owned_overhang:
            reasons.append("glyph_extension_outside_source_paragraph")
        # Inspect the entire physical page, including characters excluded by the
        # paragraph selector. The exception may complete a glyph, never import
        # ink belonging to another paragraph or a neighboring column.
        for outside in page["glyphs"]:
            if not outside["text"].strip() or outside["char_index"] in retained_ids:
                continue
            box = _box(outside["bbox"], page["height"])
            center_inside = (paragraph[0] <= (box[0] + box[2]) / 2 <= paragraph[2]
                             and paragraph[1] <= (box[1] + box[3]) / 2 <= paragraph[3])
            if not center_inside and _intersects(box, proposed):
                reasons.append("external_glyph_in_paragraph_overhang")
                break
    result.update(proposed_bbox=_box_dict(proposed),
                  char_indices=sorted(g["char_index"] for g in retained),
                  added_char_indices=sorted(g["char_index"] for g in added),
                  excluded_char_indices=sorted(excluded_ids))
    if page_image is None:
        reasons.append("full_page_raster_required")
    elif _contains(bounds, proposed):
        try:
            evidence = _pixel_evidence(page, page_image, original, proposed, retained, excluded, added)
            # Detached prose can leave a few raster rows at the crop edge.
            # Trim only source-attributed excluded ink, with all formula ink
            # protected. Each proposal is remeasured against the original.
            if excluded and not evidence["unassigned_original_ink_pixels"]:
                alternatives = []
                for edge, axis, direction in (("top", 3, -1), ("bottom", 1, 1)):
                    if not evidence["proposed_edge_ink_pixels"][edge]:
                        continue
                    for pixels in range(1, 4):
                        bounded = list(proposed)
                        bounded[axis] += direction * pixels * padding
                        bounded = tuple(bounded)
                        if bounded[1] >= bounded[3]:
                            continue
                        check = _pixel_evidence(
                            page, page_image, original, bounded, retained, excluded, added)
                        if (not any(check["proposed_edge_ink_pixels"].values())
                                and not any(check[key] for key in (
                                    "unassigned_added_ink_pixels", "unassigned_removed_ink_pixels",
                                    "retained_glyph_ink_removed_pixels"))):
                            alternatives.append((pixels, bounded, check))
                            break
                if alternatives:
                    _, proposed, evidence = min(alternatives, key=lambda row: row[0])
                    result["proposed_bbox"] = _box_dict(proposed)
                    result["detached_edge_trim"] = {
                        "method": "source_attributed_raster_rows", "max_rows": 3,
                        "protected_formula_ink_removed": evidence["retained_glyph_ink_removed_pixels"]}
            result["pixel_evidence"] = evidence
            if overhang and evidence["unassigned_original_ink_pixels"]:
                reasons.append("unassigned_ink_prevents_paragraph_overhang")
            if evidence["original_ink_pixels"] == 0:
                reasons.append("empty_detector_raster")
            if evidence["unassigned_added_ink_pixels"]:
                reasons.append("unassigned_ink_in_extension")
            if evidence["unassigned_removed_ink_pixels"] or evidence["retained_glyph_ink_removed_pixels"]:
                reasons.append("unexplained_or_protected_ink_removed")
            if excluded and evidence["unassigned_original_ink_pixels"]:
                reasons.append("unassigned_original_ink_prevents_trimming")
            if any(evidence["proposed_edge_ink_pixels"].values()):
                reasons.append("ink_touches_proposed_boundary")
            if any(count == 0 for count in evidence["added_glyph_ink_pixels"].values()):
                reasons.append("added_glyph_has_no_raster_ink")
        except ValueError as error:
            reasons.append(str(error))
    result["review_reasons"] = list(dict.fromkeys(reasons))
    if not reasons:
        result.update(bbox=_box_dict(proposed), review_required=False,
                      status="geometry_refined" if proposed != original else "geometry_unchanged")
    return result


def render_inline_page_evidence(source, page_no, *, dpi=288):
    """Extract and render the same PDF bytes locally; return (glyph_page, PIL image).

    Caller owns the returned image. Unsupported coordinate frames return no image.
    No OCR/model packages or network operations are involved.
    """
    import pypdfium2 as pdfium

    if not isinstance(dpi, (int, float)) or not math.isfinite(dpi) or dpi <= 0:
        raise ValueError("inline_invalid_render_dpi")
    content = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    glyph_page = extract_pdf_page_glyphs(content, page_no)
    if glyph_page.get("review_required"):
        return glyph_page, None
    if math.ceil(glyph_page["width"] * dpi / 72) * math.ceil(glyph_page["height"] * dpi / 72) > 20_000_000:
        raise ValueError("inline_raster_too_large")
    pdf = pdfium.PdfDocument(content)
    sheet = bitmap = None
    try:
        sheet = pdf[page_no - 1]
        bitmap = sheet.render(scale=dpi / 72)
        return glyph_page, bitmap.to_pil().convert("RGB").copy()
    finally:
        if bitmap is not None:
            bitmap.close()
        if sheet is not None:
            sheet.close()
        pdf.close()


def group_inline_formula_fragments(fragments):
    """Group evidence by physical page and paragraph, preserving every fragment.

    Group membership says nothing about mathematical continuation or reading
    order. Missing paragraph provenance receives its own review group.
    """
    groups = {}
    for index, fragment in enumerate(fragments):
        page_no, source_ref = fragment.get("page_no"), fragment.get("source_ref")
        valid = type(page_no) is int and page_no > 0 and isinstance(source_ref, str) and bool(source_ref)
        key = (page_no, source_ref) if valid else ("unresolved", index)
        group = groups.setdefault(key, {"page_no": page_no, "source_ref": source_ref, "fragments": [],
                                        "review_required": not valid, "continuation_inferred": False,
                                        "automatic_text_replacement_allowed": False})
        group["fragments"].append(deepcopy(fragment))
        group["review_required"] |= bool(fragment.get("review_required", True))
    return list(groups.values())
