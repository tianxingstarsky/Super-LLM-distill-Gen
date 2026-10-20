"""Local inline math: layout detection, crop evidence, conservative assembly.

The detector receives pixels only. Native characters establish crop/prose
boundaries; they never supply a guessed formula to the recognizer. A proven
continuation is recognized again as one image, with its row evidence retained.
"""
from __future__ import annotations

import hashlib
import math
import unicodedata
from pathlib import Path

from .docling_inline_assembly import (
    assemble_inline_formulas, native_paragraph_chars, read_native_character_pages,
)
from .docling_inline_geometry import (
    refine_inline_formula_geometry, render_inline_page_evidence,
)
from .docling_math_regions import _box, _box_dict, is_math_font


INLINE_LAYOUT_DIRECTORY = "PaddlePaddle--PP-DocLayoutV3_onnx"
INLINE_LAYOUT_POLICY = "pp_doclayout_v3_square480_source_frame_v2"


def _inside(glyph, bbox):
    value = glyph["bbox"]
    return (bbox["l"] <= (value["l"] + value["r"]) / 2 <= bbox["r"]
            and bbox["b"] <= (value["b"] + value["t"]) / 2 <= bbox["t"])


def _intersects(glyph, bbox):
    value = glyph["bbox"]
    return min(value["r"], bbox["r"]) > max(value["l"], bbox["l"]) and min(
        value["t"], bbox["t"]) > max(value["b"], bbox["b"])


def _math(glyph):
    import unicodedata
    return bool(glyph["text"].strip()) and (is_math_font(glyph["font_name"]) or any(
        unicodedata.category(char) == "Sm" for char in glyph["text"]))


def _native_views(native, glyph_text, bbox):
    from .docling_math_consistency import character_inventory
    views = []
    for predicate in (_inside, _intersects):
        chosen = [char for char in native if char["text"].strip() and predicate(char, bbox)]
        if character_inventory("".join(char["text"] for char in chosen)) == character_inventory(glyph_text):
            ids = [char["index"] for char in chosen]
            if ids not in views:
                views.append(ids)
    return views


def _overlap(first, second):
    area = max(0, min(first["r"], second["r"]) - max(first["l"], second["l"])) * max(
        0, min(first["t"], second["t"]) - max(first["b"], second["b"]))
    smaller = min((value["r"]-value["l"])*(value["t"]-value["b"]) for value in (first, second))
    return area / smaller if smaller > 0 else 0


def _punctuation_matches(source, latex):
    from pylatexenc.latex2text import LatexNodes2Text
    # Unicode ellipsis and the standard LaTeX dots macro have the same visible
    # punctuation. This comparison never edits either source or transcription.
    decoded = LatexNodes2Text().latex_to_text(latex).replace("…", "...")
    source = source.replace("…", "...")
    return all(decoded.count(c) == source.count(c) for c in ".,;:!?")


def _detector_boxes(response, prepared):
    """Validate returned pixel frames before mapping them into PDF points."""
    if (response.get("protocol") != 1 or response.get("network_disabled") is not True
            or len(response.get("images", [])) != len(prepared)):
        raise ValueError("document_docling_invalid_output")
    boxes = {}
    for result, source in zip(response["images"], prepared):
        if (result.get("id") != source["id"] or result.get("size") != source["size"]
                or result.get("image_sha256") != source["image_sha256"]):
            raise ValueError("document_docling_invalid_output")
        found = []
        for index, item in enumerate(result["boxes"]):
            if item.get("label") not in {"inline_formula", "display_formula"}:
                continue
            coordinates = item.get("coordinate")
            if (not isinstance(coordinates, list) or len(coordinates) != 4
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in coordinates)):
                raise ValueError("document_docling_invalid_output")
            l, t, r, b = coordinates
            if not (0 <= l < r <= source["size"][0] and 0 <= t < b <= source["size"][1]):
                raise ValueError("document_docling_invalid_output")
            content = source.get("content_bbox", [0, 0, *source["size"]])
            if (not isinstance(content, list) or len(content) != 4
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in content)
                    or not (0 <= content[0] < content[2] <= source["size"][0]
                            and 0 <= content[1] < content[3] <= source["size"][1])):
                raise ValueError("document_docling_invalid_output")
            l, t, r, b = max(l, content[0]), max(t, content[1]), min(r, content[2]), min(b, content[3])
            if l >= r or t >= b:
                continue
            ox, oy = source["offset"]
            scale = source["scale"]
            x, y = source["page_origin"]
            bbox = {"l": x + (l-ox)/scale, "r": x + (r-ox)/scale,
                    "t": y - (t-oy)/scale, "b": y - (b-oy)/scale,
                    "coord_origin": "BOTTOMLEFT"}
            found.append({"bbox": bbox, "score": item["score"], "label": item["label"],
                          "detector_id": f"{source['id']}:{index}", "view": source["view"],
                          "raw_pixel_box": coordinates, "source_pixel_box": [l, t, r, b],
                          "white_padding_clipped": [l, t, r, b] != coordinates})
        boxes[source["id"]] = found
    return boxes


def _publish_inline_item(document, item, assembly, route):
    """Change a mistaken block type only after exact native-prose assembly."""
    if not route.get("probe_required"):
        item.text = assembly["text"]
        item.prov[0] = item.prov[0].model_copy(update={"charspan": (0, len(item.text))})
        return True
    if getattr(item.label, "value", item.label) == "code":
        # Character equality cannot prove that an unknown programming language
        # was mislabeled. Keep recognized math as review evidence in this case.
        return False
    from docling_core.types.doc import TextItem
    original = item.model_dump(mode="python")
    # A code block can own captions/references. Relabeling that structure is not
    # justified by formula crops, even if its own visible text matched.
    extras = set(original) - set(TextItem.model_fields) - {"code_language"}
    if any(original[key] for key in extras):
        return False
    data = {key: value for key, value in original.items() if key in TextItem.model_fields}
    data.update(label="text", text=assembly["text"])
    data["prov"] = [item.prov[0].model_copy(update={"charspan": (0, len(assembly["text"]))})]
    replacement = TextItem.model_validate(data)
    index = next(i for i, value in enumerate(document.texts) if value.self_ref == item.self_ref)
    document.texts[index] = replacement
    return True


def enrich_inline_formulas(document, source, config, reader, *, routes=None):
    from .docling_inline_provenance import enrich_provenance_parts
    return enrich_provenance_parts(
        document, source, config, reader, routes or {}, _enrich_single_source_items)


def _enrich_single_source_items(document, source, config, reader, *, routes=None):
    """Enrich single-page native paragraphs and retain all unresolved evidence."""
    from PIL import Image, ImageOps
    import pypdfium2 as pdfium
    from .docling_formula_crops import numeric_glyphs_match, render_formula_crop
    from .docling_worker_client import _owned_temporary_directory, run_docling_worker

    native_pages = read_native_character_pages(source)
    from .docling_inline_boundaries import read_pdf_citation_links, separate_linked_citation
    routes = routes or {}
    evidence, paragraphs, page_glyphs = {}, {}, {}
    labels = {"text", "list_item", "footnote", "caption"}
    for item in document.texts:
        route = routes.get(item.self_ref, {})
        if route.get("route") == "review":
            evidence[item.self_ref] = {
                "kind": "inline_math", "policy": INLINE_LAYOUT_POLICY,
                "status": "review_required", "reason": "inline_source_routing_requires_review",
                "backend_text": item.text, "routing": route,
                "fragments": [], "published": False, "continuation_inferred": False,
            }
            continue
        if getattr(item.label, "value", item.label) not in labels and not route.get("probe_required"):
            continue
        if not item.prov:
            raise ValueError("document_docling_invalid_provenance")
        if len(item.prov) != 1:
            has_math = False
            for prov in item.prov:
                if prov.page_no not in page_glyphs:
                    from .docling_math_regions import extract_pdf_page_glyphs
                    page_glyphs[prov.page_no] = extract_pdf_page_glyphs(source, prov.page_no)
                page = page_glyphs[prov.page_no]
                bbox = _box_dict(_box(prov.bbox, page["height"]))
                has_math |= page.get("review_required", False) or any(
                    _inside(glyph, bbox) and _math(glyph) for glyph in page["glyphs"])
            if has_math:
                evidence[item.self_ref] = {
                    "kind": "inline_math", "policy": INLINE_LAYOUT_POLICY,
                    "status": "review_required", "reason": "inline_multiple_provenance_requires_review",
                    "backend_text": item.text, "fragments": [], "continuation_inferred": False,
                }
            continue
        prov = item.prov[0]
        number = prov.page_no
        if number not in page_glyphs:
            from .docling_math_regions import extract_pdf_page_glyphs
            page_glyphs[number] = extract_pdf_page_glyphs(source, number)
        page = page_glyphs[number]
        if page.get("review_required"):
            evidence[item.self_ref] = {
                "kind": "inline_math", "policy": INLINE_LAYOUT_POLICY,
                "status": "review_required", "reason": "unsupported_pdf_coordinate_space",
                "backend_text": item.text, "fragments": [], "page_no": number,
            }
            continue
        bbox = _box_dict(_box(prov.bbox, page["height"]))
        glyphs = [glyph for glyph in page["glyphs"] if _inside(glyph, bbox)]
        if not any(_math(glyph) for glyph in glyphs):
            continue
        paragraphs.setdefault(number, []).append((item, bbox, glyphs))
    if not paragraphs:
        return evidence
    from .docling_inline_lines import read_native_line_pages
    native_lines = read_native_line_pages(source)
    citation_links = read_pdf_citation_links(source)
    from .docling_native_font_evidence import read_native_font_evidence, reconcile_native_font_evidence
    font_proofs = read_native_font_evidence(source, page_numbers=set(paragraphs))
    font_comparisons = {
        number: reconcile_native_font_evidence(
            native_pages.get(number, []), page_glyphs[number]["glyphs"], font_proofs.get(number, {}))
        for number in paragraphs
    }
    from .docling_inline_native_preservation import prove_native_preservation
    native_proofs = {}
    for number, items in paragraphs.items():
        annotations = []
        for item, bbox, glyphs in sorted(
                items, key=lambda entry: getattr(entry[0].label, "value", entry[0].label) != "footnote"):
            route = routes.get(item.self_ref, {})
            source_text = item.text or (item.orig if route.get("probe_required") else "")
            proof = prove_native_preservation(
                glyphs, native_paragraph_chars(native_pages.get(number, []), bbox), source_text,
                label=getattr(item.label, "value", item.label), annotation_markers=annotations)
            native_proofs[item.self_ref] = proof
            annotations.extend(p for p in proof["proofs"] if p["kind"] == "footnote_symbol")

    pdf = pdfium.PdfDocument(source)
    try:
        with _owned_temporary_directory() as folder:
            prepared = []
            line_views = {}
            line_evidence = {}

            def save(image, *, image_id, page_no, view, scale, offset, page_origin, content_size=None):
                path = folder / f"{len(prepared)}.png"
                image.save(path)
                prepared.append({
                    "id": image_id, "path": str(path), "size": list(image.size),
                    "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "page_no": page_no, "view": view, "scale": scale,
                    "offset": list(offset), "page_origin": list(page_origin),
                    "content_bbox": [offset[0], offset[1],
                                     offset[0] + (content_size or image.size)[0],
                                     offset[1] + (content_size or image.size)[1]],
                })

            for number, items in paragraphs.items():
                page, image = render_inline_page_evidence(source, number)
                if image is None:
                    continue
                save(image, image_id=f"page-{number}", page_no=number, view="page",
                     scale=288/72, offset=(0, 0), page_origin=(0, page["height"]))
                image.close()
                for item, bbox, _ in items:
                    crop, metadata = render_formula_crop(pdf, number, bbox, dpi=480, padding_points=2)
                    border = round(2 * 480 / 72)
                    padded = ImageOps.expand(crop, border=border, fill="white")
                    side = max(padded.size)
                    if side * side > 20_000_000:
                        crop.close()
                        padded.close()
                        continue
                    square = Image.new("RGB", (side, side), "white")
                    ox, oy = (side-padded.width)//2, (side-padded.height)//2
                    square.paste(padded, (ox, oy))
                    save(square, image_id=item.self_ref, page_no=number, view="paragraph",
                         scale=480/72, offset=(ox+border, oy+border),
                         page_origin=(metadata["crop_bbox"]["l"], metadata["crop_bbox"]["t"]),
                         content_size=crop.size)
                    square.close()
                    padded.close()
                    crop.close()
                    from .docling_inline_lines import select_inline_line_views
                    views = select_inline_line_views(
                        native_lines.get(number, []), bbox, page_glyphs[number]["glyphs"])
                    line_evidence[item.self_ref] = views
                    if routes.get(item.self_ref, {}).get("probe_required"):
                        for index, view in enumerate(views):
                            if view["review_required"]:
                                continue
                            crop, metadata = render_formula_crop(
                                pdf, number, view["bbox"], dpi=480, padding_points=2)
                            side = max(crop.size) + 2 * border
                            if side * side > 20_000_000:
                                crop.close()
                                continue
                            square = Image.new("RGB", (side, side), "white")
                            ox, oy = (side-crop.width)//2, (side-crop.height)//2
                            square.paste(crop, (ox, oy))
                            identifier = f"{item.self_ref}:physical-line:{index}"
                            save(square, image_id=identifier, page_no=number, view="physical_line",
                                 scale=480/72, offset=(ox, oy),
                                 page_origin=(metadata["crop_bbox"]["l"], metadata["crop_bbox"]["t"]),
                                 content_size=crop.size)
                            line_views.setdefault(item.self_ref, []).append(identifier)
                            square.close()
                            crop.close()
            response = run_docling_worker(config["inline_runtime_python"], {
                "service": "inline_layout", "action": "detect",
                "model_directory": str(Path(config["artifacts_path"]) / INLINE_LAYOUT_DIRECTORY),
                "images": [{"id": item["id"], "path": item["path"]} for item in prepared],
            })
            detections = _detector_boxes(response, prepared)

            for number, items in paragraphs.items():
                page, image = render_inline_page_evidence(source, number)
                try:
                    for item, bbox, glyphs in items:
                        fragments = []
                        native_proof = native_proofs[item.self_ref]
                        preserved_ids = set(native_proof["proven_glyph_ids"])
                        candidates = [dict(box) for box in detections.get(item.self_ref, [])]
                        for identifier in line_views.get(item.self_ref, []):
                            candidates.extend(dict(box) for box in detections.get(identifier, []))
                        candidates.extend(dict(box) for box in detections.get(f"page-{number}", [])
                                          if _overlap(box["bbox"], bbox) > .8)
                        candidates = [separate_linked_citation(candidate, page, citation_links.get(number, []))
                                      for candidate in candidates]
                        for index, candidate in enumerate(candidates):
                            candidate["geometry"] = refine_inline_formula_geometry(
                                page, candidate["bbox"], bbox, page_image=image,
                                source_ref=item.self_ref, fragment_id=f"{item.self_ref}:{index}",
                                allow_math_delimiters=True)
                        # A detector can call an intact scalar or author marker a
                        # formula. Keep the detection as evidence, without forcing
                        # an OCR rewrite of source-aligned flat text.
                        preserved_candidates = [
                            candidate for candidate in candidates
                            if candidate["geometry"]["review_required"]
                            and candidate["geometry"]["char_indices"]
                            and set(candidate["geometry"]["char_indices"]) <= preserved_ids]
                        candidates = [c for c in candidates if c not in preserved_candidates]
                        from .docling_inline_partitions import classify_inline_formula_containers
                        partition = classify_inline_formula_containers(
                            candidates, page, route=routes.get(item.self_ref, {}),
                            physical_line_views=line_evidence.get(item.self_ref, []))
                        partition_candidates = candidates if partition["container_indices"] else []
                        containers = [candidates[index] for index in partition["container_indices"]]
                        candidates = [candidate for index, candidate in enumerate(candidates)
                                      if index not in partition["container_indices"]]
                        from .docling_inline_candidates import select_inline_formula_candidates
                        selection = select_inline_formula_candidates(candidates)
                        selected = [candidates[index] for index in
                                    selection["selected_indices"] + selection["review_indices"]]
                        native = native_paragraph_chars(native_pages.get(number, []), bbox)
                        font_comparison = font_comparisons[number]
                        native_comparison = [
                            dict(char, text=font_comparison["native_comparison_text"].get(char["index"], char["text"]))
                            for char in native
                        ]
                        ready = []
                        for index, candidate in enumerate(selected):
                            geometry = candidate["geometry"]
                            fragment_id = geometry["fragment_id"]
                            fragment = {"fragment_id": fragment_id, "detector": candidate,
                                        "geometry": geometry, "status": "review_required"}
                            is_selected = candidate in [candidates[i] for i in selection["selected_indices"]]
                            if not geometry["review_required"] and is_selected:
                                crop, proof = render_formula_crop(
                                    pdf, number, geometry["bbox"], dpi=480, padding_points=0)
                                try:
                                    prediction = reader.read(crop)
                                finally:
                                    crop.close()
                                original_prediction = dict(prediction)
                                # Native parser font boxes can extend above the
                                # visible ink. Try center and intersection views;
                                # accept only one unambiguous character set that
                                # agrees with the independent PDF ink glyphs.
                                from .docling_math_consistency import validate_math_characters
                                from .docling_math_style import validate_math_style
                                chosen_glyphs = [glyph for glyph in page["glyphs"]
                                                 if glyph["char_index"] in geometry["char_indices"]]
                                glyph_text = "".join(
                                    font_comparison["glyph_comparison_text"].get(glyph["char_index"], glyph["text"])
                                    for glyph in chosen_glyphs)
                                native_views = _native_views(native_comparison, glyph_text, geometry["bbox"])
                                indices = native_views[0] if len(native_views) == 1 else [
                                    char["index"] for char in native if _inside(char, geometry["bbox"])]
                                original = "".join(char["text"] for char in native if char["index"] in indices)
                                comparison_original = "".join(
                                    char["text"] for char in native_comparison if char["index"] in indices)
                                if (prediction["status"] == "transcribed_unverified"
                                        and not numeric_glyphs_match(original, prediction["transcribed_text"])):
                                    prediction.update(status="review_required", reason="numeric_glyphs_changed")
                                consistency = validate_math_characters(
                                    comparison_original, glyph_text, prediction.get("transcribed_text", ""))
                                if not _punctuation_matches(glyph_text, prediction.get("transcribed_text", "")):
                                    consistency["review_required"] = True
                                    consistency["reasons"].append("source_punctuation_changed")
                                if len(native_views) != 1:
                                    consistency["review_required"] = True
                                    consistency["reasons"].append("native_character_selection_not_unique")
                                style_proofs = font_comparison.get("glyph_style_proofs", {})
                                style = validate_math_style(
                                    chosen_glyphs, prediction.get("transcribed_text", ""),
                                    glyph_style_proofs=style_proofs)
                                attempts = []
                                retryable_style_reasons = {
                                    "source_font_weight_does_not_support_bold",
                                    "source_font_program_does_not_support_bold",
                                }
                                # A misread variable cannot bind its bold command
                                # to a source glyph that does not exist (e.g. Z vs z).
                                # Allow another raw reading, not a style exemption.
                                # Repeated/ambiguous existing symbols stay held.
                                unbound_targets = [
                                    target for target in style.get("targets", [])
                                    if target.get("reason") == "bold_source_character_not_unique"]
                                if (consistency["reasons"] == ["formula_variable_or_digit_changed"]
                                        and style["reasons"] == ["bold_source_character_not_unique"]
                                        and unbound_targets
                                        and all(unicodedata.normalize("NFKC", target["text"]) not in
                                                unicodedata.normalize("NFKC", glyph_text)
                                                for target in unbound_targets)):
                                    retryable_style_reasons.add("bold_source_character_not_unique")
                                if (prediction["status"] == "transcribed_unverified"
                                        and (consistency["review_required"] or style["review_required"])
                                        and set(consistency["reasons"]) <= {"formula_variable_or_digit_changed"}
                                        and set(style["reasons"]) <= retryable_style_reasons):
                                    # Retry the identical source region at the mature OCR model's
                                    # lower input scale. White padding adds no neighboring content.
                                    # A new raw prediction must pass source checks; never replace
                                    # a mistaken variable by editing the generated LaTeX.
                                    from io import BytesIO
                                    attempts.append({"prediction": dict(prediction), "crop": proof,
                                                     "character_consistency": consistency,
                                                     "style_consistency": style})
                                    for retry_dpi in (240, 360):
                                        retry_crop, retry_proof = render_formula_crop(
                                            pdf, number, geometry["bbox"], dpi=retry_dpi, padding_points=0)
                                        pad = round(2 * retry_dpi / 72)
                                        padded = ImageOps.expand(retry_crop, border=pad, fill="white")
                                        retry_crop.close()
                                        try:
                                            raw_image = BytesIO()
                                            padded.save(raw_image, format="PNG")
                                            retry_proof.update(
                                                white_padding_pixels=pad,
                                                recognition_input_sha256=hashlib.sha256(raw_image.getvalue()).hexdigest(),
                                                recognition_pixel_size=list(padded.size))
                                            alternative = reader.read(padded)
                                        finally:
                                            padded.close()
                                        alternative_check = validate_math_characters(
                                            comparison_original, glyph_text, alternative.get("transcribed_text", ""))
                                        if not _punctuation_matches(glyph_text, alternative.get("transcribed_text", "")):
                                            alternative_check["review_required"] = True
                                            alternative_check["reasons"].append("source_punctuation_changed")
                                        alternative_style = validate_math_style(
                                            chosen_glyphs, alternative.get("transcribed_text", ""),
                                            glyph_style_proofs=style_proofs)
                                        attempts.append({"prediction": dict(alternative), "crop": retry_proof,
                                                         "character_consistency": alternative_check,
                                                         "style_consistency": alternative_style})
                                        if (alternative["status"] == "transcribed_unverified"
                                                and not alternative_check["review_required"]
                                                and not alternative_style["review_required"]):
                                            prediction, proof, consistency = alternative, retry_proof, alternative_check
                                            style = alternative_style
                                            break
                                if consistency["review_required"] and prediction["status"] == "transcribed_unverified":
                                    prediction.update(status="review_required", reason="formula_character_review")
                                if style["review_required"] and prediction["status"] == "transcribed_unverified":
                                    prediction.update(status="review_required", reason="formula_style_review")
                                fragment.update(**prediction, crop=proof, native_char_indices=indices)
                                fragment["original_prediction"] = original_prediction
                                fragment["character_consistency"] = consistency
                                fragment["style_consistency"] = style
                                fragment["recognition_attempts"] = attempts
                                fragment["font_source_corrections"] = [
                                    correction for correction in font_comparison["corrections"]
                                    if correction["native_index"] in indices
                                ]
                                if fragment["status"] == "transcribed_unverified":
                                    ready.append(fragment)
                            fragments.append(fragment)
                        original_text_slot = source_text = item.text
                        route = routes.get(item.self_ref, {})
                        if route.get("probe_required") and not source_text:
                            source_text = item.orig
                        from .docling_inline_continuation import recognize_inline_formula_continuation
                        continuation = recognize_inline_formula_continuation(
                            source, page, item.self_ref, bbox, ready, native,
                            route or {"source_ref": item.self_ref, "page_no": number,
                                      "source_sha256": page["source_sha256"],
                                      "original_label": getattr(item.label, "value", item.label)}, reader,
                            physical_line_views=line_evidence.get(item.self_ref, []))
                        if continuation["fragment"] is not None:
                            ready = [continuation["fragment"]]
                        assembly = assemble_inline_formulas(source_text, native, ready)
                        uncovered = [glyph["char_index"] for glyph in glyphs if _math(glyph)
                                     and glyph["char_index"] not in preserved_ids
                                     and not any(_inside(glyph, fragment["geometry"]["bbox"])
                                                 for fragment in fragments
                                                 if not fragment["geometry"]["review_required"])]
                        native_only = not ready and not fragments and not uncovered and bool(preserved_ids)
                        if native_only:
                            assembly.update(
                                status="native_preserved", review_reasons=[], prose_preserved=True)
                        continuation_blocked = continuation["status"] == "review_required"
                        needs_review = (continuation_blocked or selection["review_required"]
                                        or assembly["status"] == "review_required" or uncovered
                                        or any(fragment["status"] == "review_required" for fragment in fragments))
                        published = False
                        if assembly["status"] == "transcribed_unverified" and not needs_review:
                            published = _publish_inline_item(document, item, assembly, route)
                            needs_review = not published
                        evidence[item.self_ref] = {
                            "kind": "inline_math", "policy": INLINE_LAYOUT_POLICY,
                            "page_no": number, "model": "PP-DocLayoutV3 + Pix2Text MFR 1.5",
                            "status": ("review_required" if needs_review else
                                       "native_preserved" if native_only else "transcribed_unverified"),
                            "backend_text": assembly["backend_text"], "assembly": assembly,
                            "original_text_slot": original_text_slot,
                            "publication_block_reason": (
                                "whole_formula_continuation_requires_review" if continuation_blocked else
                                "source_code_label_requires_review"
                                if route.get("original_label") == "code" and not published else None),
                            "fragments": fragments, "uncovered_math_char_indices": uncovered,
                            "candidate_selection": selection, "alternatives": candidates,
                            "published": published, "routing": route,
                            "physical_line_views": line_evidence.get(item.self_ref, []),
                            "layout_containers": containers, "partition": partition,
                            "partition_candidates": partition_candidates,
                            "native_preservation": native_proof,
                            "preserved_candidates": preserved_candidates,
                            "continuation": continuation,
                            "continuation_inferred": continuation["fragment"] is not None,
                        }
                finally:
                    if image is not None:
                        image.close()
    finally:
        pdf.close()
    return evidence
