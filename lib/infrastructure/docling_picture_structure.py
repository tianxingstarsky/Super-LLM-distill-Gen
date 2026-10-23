"""Native physical lines and rotated token rows for source figure text.

Docling Parse supplies line rectangles and character cells. This module binds
those lines to exact source-text ranges, never to guessed words or headings.
The source document stays intact. A separate PDFium proof may mark a complete
line fully clipped; unknown visibility always preserves that line for export.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
from io import BytesIO
import math
from pathlib import Path


PICTURE_STRUCTURE_POLICY = "docling_native_figure_lines_titles_and_labels_v3"
_BOX_TOLERANCE = .05
_MAX_PARTITION_CANDIDATES = 128


def _label(item):
    return getattr(getattr(item, "label", None), "value", None)


def _compact(text):
    return "".join(char for char in text if not char.isspace())


def _bounds(value):
    if value.get("coord_origin") != "BOTTOMLEFT":
        raise ValueError("figure_structure_coordinate_space")
    box = tuple(float(value[key]) for key in ("l", "b", "r", "t"))
    if not all(math.isfinite(v) for v in box) or box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("figure_structure_invalid_bbox")
    return box


def _contains(outer, inner):
    return (outer[0] - _BOX_TOLERANCE <= inner[0]
            and outer[1] - _BOX_TOLERANCE <= inner[1]
            and outer[2] + _BOX_TOLERANCE >= inner[2]
            and outer[3] + _BOX_TOLERANCE >= inner[3])


def read_native_picture_lines(source, *, page_numbers):
    """Read official line/character geometry, including rotation, without OCR."""
    from docling_parse.pdf_parser import (
        ContentConfig, ContentLevel, DoclingThreadedPdfParser, ThreadedPdfParserConfig,
    )

    content = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    parser = DoclingThreadedPdfParser(parser_config=ThreadedPdfParserConfig(
        loglevel="fatal", threads=1, render_config=None,
        page_content_config=ContentConfig(
            char_cells_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
            line_cells_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
            word_cells_content_level=ContentLevel.SKIP,
            shapes_content_level=ContentLevel.SKIP, bitmaps_content_level=ContentLevel.SKIP)))
    key = parser.load(BytesIO(content), page_numbers=sorted(set(page_numbers)))
    result = {}
    try:
        for response in parser.iterate_results():
            page = response.get_page()
            dimension = page.dimension
            crop = dimension.crop_bbox.to_bottom_left_origin(dimension.height)
            supported = abs(dimension.angle) < .01 and abs(crop.l) < .01 and abs(crop.b) < .01
            chars = []
            for char in page.char_cells:
                if char.text.strip() and char.rendering_mode not in (3, 7):
                    chars.append((char, _bounds(char.rect.to_bounding_box().to_bottom_left_origin(
                        dimension.height).model_dump(mode="json"))))
            lines = []
            for line in page.textline_cells:
                if not line.text.strip():
                    continue
                bbox = line.rect.to_bounding_box().to_bottom_left_origin(
                    dimension.height).model_dump(mode="json")
                box = _bounds(bbox)
                angle = float(line.rect.angle_360)
                members = sorted((char for char, bounds in chars
                                  if char.font_key == line.font_key
                                  and abs(char.rect.angle_360 - angle) < .01
                                  and _contains(box, bounds)), key=lambda char: char.index)
                character_proof = _compact("".join(char.text for char in members)) == _compact(line.text)
                rect = line.rect
                lines.append({
                    "line_id": f"page-{response.page_number}:line-{line.index}",
                    "line_index": line.index, "page_no": response.page_number,
                    "source_sha256": digest, "native_text": line.text, "bbox": bbox,
                    "angle": angle, "font_key": line.font_key, "font_name": line.font_name,
                    "baseline_axis_start": [rect.r_x0, rect.r_y0],
                    "baseline_axis_end": [rect.r_x1, rect.r_y1],
                    "perpendicular_extent": math.hypot(rect.r_x3 - rect.r_x0, rect.r_y3 - rect.r_y0),
                    "native_char_indices": [char.index for char in members] if character_proof else [],
                    "native_character_sequence_verified": character_proof,
                    "from_ocr": bool(line.from_ocr), "rendering_mode": int(line.rendering_mode),
                    "coordinate_space_supported": supported,
                })
            result[response.page_number] = {"page_no": response.page_number,
                "source_sha256": digest, "lines": lines, "coordinate_space_supported": supported}
    finally:
        while parser.has_tasks():
            parser.get_task()
        parser.unload(key)
    return result


def partition_source_lines(text, bbox, lines, *, page_no, source_sha256):
    """Return the unique exact non-whitespace partition, with original spans.

    Whitespace remains in source-indexed separators. No Unicode normalization,
    lexical correction, source reordering or partial-string loss is allowed.
    """
    outer = _bounds(bbox)
    offsets = [index for index, char in enumerate(text) if not char.isspace()]
    compact = _compact(text)
    candidates = defaultdict(list)
    used_line_ids = set()
    for line in lines:
        if (line.get("page_no") != page_no or line.get("source_sha256") != source_sha256
                or not line.get("coordinate_space_supported") or line.get("from_ocr")
                or line.get("rendering_mode") in (3, 7)
                or not _contains(outer, _bounds(line["bbox"]))):
            continue
        needle = _compact(line["native_text"])
        if not needle:
            continue
        if line["line_id"] in used_line_ids:
            raise ValueError("duplicate_figure_native_line_id")
        used_line_ids.add(line["line_id"])
        position = compact.find(needle)
        while position >= 0:
            candidates[position].append((position + len(needle), line))
            if sum(map(len, candidates.values())) > _MAX_PARTITION_CANDIDATES:
                raise ValueError("figure_line_partition_limit")
            position = compact.find(needle, position + 1)
    solutions = []
    stack = [(0, [], frozenset())]
    examined = 0
    while stack and len(solutions) < 2:
        examined += 1
        if examined > 4096:
            raise ValueError("figure_line_partition_search_limit")
        position, parts, used = stack.pop()
        if position == len(compact) and parts:
            solutions.append(parts)
            continue
        for end, line in candidates.get(position, []):
            if line["line_id"] not in used:
                stack.append((end, parts + [(position, end, line)], used | {line["line_id"]}))
    if len(solutions) != 1:
        raise ValueError("figure_native_line_partition_not_unique" if solutions else "figure_native_line_partition_missing")
    segments, cursor = [], 0
    for start, stop, line in solutions[0]:
        span = [offsets[start], offsets[stop - 1] + 1]
        segment = {"source_charspan": span, "text": text[span[0]:span[1]],
                   "separator_before": text[cursor:span[0]], "native_line": dict(line),
                   "display": True, "visibility": {"visibility": "unknown", "fully_clipped": False}}
        segments.append(segment)
        cursor = span[1]
    return {"segments": segments, "trailing_separator": text[cursor:],
            "exact_source_partition": True, "source_text": text,
            "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def group_rotated_token_rows(entries):
    """Group adjacent source refs by native baseline-axis start/end alignment.

    Input order is authoritative. Both leading and trailing alignment are
    supported; no token is moved or deduplicated. These are physical rows,
    never sentences or inferred attention relations.
    """
    rows, active = [], []

    def relation(group):
        if not group:
            return None
        lines = [entry["segments"][0]["native_line"] for entry in group]
        first = lines[0]
        angle = first["angle"]
        if abs(angle - 90) > .01 and abs(angle - 270) > .01:
            return None
        extent = first["perpendicular_extent"]
        if extent <= 0 or any(not line["native_character_sequence_verified"]
                or line["page_no"] != first["page_no"]
                or line["source_sha256"] != first["source_sha256"]
                or line["font_key"] != first["font_key"] or abs(line["angle"] - angle) > .01
                or abs(line["perpendicular_extent"] - extent) > .02 * extent for line in lines):
            return None
        # The projected endpoint along the actual text axis is y for vertical
        # text. Its perpendicular progression is x. This is not center sorting.
        tolerance = max(.02, .005 * extent)
        starts = [line["baseline_axis_start"][1] for line in lines]
        ends = [line["baseline_axis_end"][1] for line in lines]
        sides = [kind for kind, values in (("start", starts), ("end", ends))
                 if max(values) - min(values) <= tolerance]
        positions = [line["baseline_axis_start"][0] for line in lines]
        monotone = (all(b > a for a, b in zip(positions, positions[1:]))
                    or all(b < a for a, b in zip(positions, positions[1:])))
        if not sides or not monotone:
            return None
        return {"aligned_native_axis_endpoints": sides, "alignment_tolerance_points": tolerance,
                "angle": angle, "start_spread_points": max(starts) - min(starts),
                "end_spread_points": max(ends) - min(ends)}

    def finish():
        if len(active) > 1:
            native_lines = [entry["segments"][0]["native_line"] for entry in active]
            bounds = [_bounds(line["bbox"]) for line in native_lines]
            rows.append({"source_refs": [entry["item_ref"] for entry in active],
                         "source_charspans": [entry["segments"][0]["source_charspan"] for entry in active],
                         "native_line_ids": [entry["segments"][0]["native_line"]["line_id"] for entry in active],
                         "source_texts": [entry["source_text"] for entry in active],
                         "page_no": native_lines[0]["page_no"],
                         "source_sha256": native_lines[0]["source_sha256"],
                         "bbox": dict(l=min(b[0] for b in bounds), b=min(b[1] for b in bounds),
                                      r=max(b[2] for b in bounds), t=max(b[3] for b in bounds),
                                      coord_origin="BOTTOMLEFT"),
                         "physical_only": True, "source_order_preserved": True,
                         "repetitions_preserved": True, "semantic_relations_inferred": False,
                         **relation(active)})

    for entry in entries:
        eligible = (entry and entry.get("label") == "text" and len(entry.get("segments", [])) == 1
                    and entry["segments"][0].get("display") and not entry.get("review_required"))
        if not eligible or relation([entry]) is None:
            finish()
            active = []
            continue
        if active and relation(active + [entry]) is None:
            finish()
            active = []
        active.append(entry)
    finish()
    return rows


def associate_external_picture_titles(document, mapping):
    """Prove a narrow sibling-panel title relationship for the export view.

    An existing picture-owned title is the anchor, not a nearby body heading.
    A shared baseline/font, uniquely centered adjacent picture, contiguous
    body slots and the anchor's original spanning caption must all agree.
    This does not infer caption ownership, alter labels, or change the graph.
    """
    digest = mapping.get("source_sha256")
    body_refs = [ref.cref for ref in document.body.children]
    items = {item.self_ref: item for item, _ in document.iterate_items(traverse_pictures=True)}
    pictures = {item.self_ref: item for item in document.pictures}
    caption_owners = defaultdict(list)
    satellite_refs = set()
    for item in items.values():
        for name in ("captions", "footnotes"):
            for ref in getattr(item, name, []):
                satellite_refs.add(ref.cref)
                if name == "captions":
                    caption_owners[ref.cref].append(item.self_ref)

    def geometry(item, page_no=None):
        if len(item.prov) != 1:
            raise ValueError("title_association_single_provenance_required")
        p = item.prov[0]
        if page_no is not None and p.page_no != page_no:
            raise ValueError("title_association_page_mismatch")
        size = document.pages[p.page_no].size
        box = _bounds(p.bbox.to_bottom_left_origin(size.height).model_dump(mode="json"))
        if not _contains((0, 0, size.width, size.height), box):
            raise ValueError("title_association_bbox_outside_page")
        return p.page_no, box

    def title(item):
        record = mapping.get("items", {}).get(item.self_ref, {})
        if (_label(item) != "section_header" or item.children or item.self_ref in satellite_refs
                or record.get("item_ref") != item.self_ref or record.get("label") != "section_header"
                or record.get("review_required") or not record.get("exact_source_partition")
                or record.get("source_text") != item.text or item.text != item.orig
                or record.get("source_provenance") != [p.model_dump(mode="json") for p in item.prov]
                or record.get("original_parent_ref") != (item.parent.cref if item.parent else None)
                or record.get("original_level") != getattr(item, "level", None)
                or record.get("source_text_sha256") != hashlib.sha256(item.text.encode()).hexdigest()
                or len(record.get("segments", [])) != 1):
            raise ValueError("title_association_exact_native_heading_required")
        segment = record["segments"][0]
        line = segment["native_line"]
        page, box = geometry(item)
        if (not segment.get("display") or segment.get("source_charspan") != [0, len(item.text)]
                or segment.get("text") != item.text or segment.get("separator_before") != ""
                or record.get("trailing_separator") != ""
                or _compact(line.get("native_text", "")) != _compact(item.text)
                or line.get("page_no") != page or record.get("page_no") != page
                or line.get("source_sha256") != digest or not digest
                or not line.get("coordinate_space_supported") or line.get("from_ocr")
                or not line.get("native_character_sequence_verified")
                or type(line.get("line_index")) is not int or line["line_index"] < 0
                or line.get("line_id") != f"page-{page}:line-{line['line_index']}"
                or not isinstance(line.get("native_char_indices"), list) or not line["native_char_indices"]
                or any(type(index) is not int or index < 0 for index in line["native_char_indices"])
                or line["native_char_indices"] != sorted(set(line["native_char_indices"]))
                or line.get("rendering_mode") in (3, 7) or not line.get("font_key")
                or abs(line["angle"]) > .01 or not _contains(box, _bounds(line["bbox"]))):
            raise ValueError("title_association_native_heading_not_bound")
        native_box = _bounds(line["bbox"])
        height = float(line["perpendicular_extent"])
        if not math.isfinite(height) or height <= 0:
            raise ValueError("title_association_invalid_line_height")
        return record, line, page, native_box, height

    def direct_body(item):
        return item.parent is not None and item.parent.cref == document.body.self_ref and body_refs.count(item.self_ref) == 1

    anchors = []
    for item in document.texts:
        owner = pictures.get(item.parent.cref if item.parent else None)
        if owner is None or not direct_body(owner) or len(owner.captions) != 1:
            continue
        try:
            proof = title(item)
            page, anchor_box = geometry(owner, proof[2])
            cap_ref = owner.captions[0].cref
            caption = items[cap_ref]
            _, caption_box = geometry(caption, page)
            if (_label(caption) != "caption" or caption_owners[cap_ref] != [owner.self_ref]
                    or not isinstance(caption.text, str) or not caption.text.strip() or caption.text != caption.orig
                    or list(caption.prov[0].charspan) != [0, len(caption.text)]
                    or caption.parent is None or caption.parent.cref != owner.self_ref
                    or [ref.cref for ref in owner.children].count(item.self_ref) != 1
                    or [ref.cref for ref in owner.children].count(caption.self_ref) != 1
                    or not _contains(anchor_box, proof[3])
                    or anchor_box[3] - proof[3][3] > proof[4]
                    or abs((proof[3][0] + proof[3][2] - anchor_box[0] - anchor_box[2]) / 2) > .1 * proof[4]):
                continue
            anchors.append((item, owner, proof, anchor_box, caption, caption_box))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue

    associations, decisions = [], []
    for header in document.texts:
        if (_label(header) != "section_header" or not direct_body(header)
                or header.self_ref not in mapping.get("items", {})):
            continue
        proposals = []
        try:
            record, line, page, header_box, height = title(header)
            for anchor, owner, anchor_proof, anchor_box, caption, caption_box in anchors:
                _, other_line, other_page, other_title_box, other_height = anchor_proof
                tolerance = max(_BOX_TOLERANCE, .01 * min(height, other_height))
                if (page != other_page or header.level != anchor.level or line["font_key"] != other_line["font_key"]
                        or abs(height - other_height) > .02 * min(height, other_height)
                        or any(abs(line[key][1] - other_line[key][1]) > tolerance
                               for key in ("baseline_axis_start", "baseline_axis_end"))
                        or abs(header_box[1] - other_title_box[1]) > tolerance
                        or abs(header_box[3] - other_title_box[3]) > tolerance):
                    continue
                for target in document.pictures:
                    if target is owner or not direct_body(target) or target.captions:
                        continue
                    try:
                        _, target_box = geometry(target, page)
                    except (AttributeError, KeyError, TypeError, ValueError):
                        continue
                    # Both panels occupy one physical row; one vertical
                    # projection must contain the other, with disjoint x spans.
                    disjoint = target_box[2] < anchor_box[0] or anchor_box[2] < target_box[0]
                    row = ((target_box[1] >= anchor_box[1] - _BOX_TOLERANCE and target_box[3] <= anchor_box[3] + _BOX_TOLERANCE)
                           or (anchor_box[1] >= target_box[1] - _BOX_TOLERANCE and anchor_box[3] <= target_box[3] + _BOX_TOLERANCE))
                    center_delta = abs((header_box[0] + header_box[2] - target_box[0] - target_box[2]) / 2)
                    gap = header_box[1] - target_box[3]
                    if (not disjoint or not row or center_delta > .1 * height
                            or not -_BOX_TOLERANCE <= gap <= 2 * height
                            or header_box[0] > target_box[0] + _BOX_TOLERANCE
                            or header_box[2] < target_box[2] - _BOX_TOLERANCE
                            or any(_label(child) == "section_header" for child, _ in
                                   document.iterate_items(root=target, traverse_pictures=True))):
                        continue
                    # The original caption must span both panels and titles,
                    # below their complete bodies, at no more than a panel's
                    # height. A nearby unrelated caption cannot authorize it.
                    group_l = min(target_box[0], anchor_box[0], header_box[0], other_title_box[0])
                    group_r = max(target_box[2], anchor_box[2], header_box[2], other_title_box[2])
                    if (caption_box[0] > group_l + _BOX_TOLERANCE or caption_box[2] < group_r - _BOX_TOLERANCE
                            or caption_box[3] >= min(target_box[1], anchor_box[1])
                            or min(target_box[1], anchor_box[1]) - caption_box[3] > max(
                                target_box[3] - target_box[1], anchor_box[3] - anchor_box[1])):
                        continue
                    slots = sorted(body_refs.index(ref) for ref in (header.self_ref, target.self_ref, owner.self_ref))
                    if slots != list(range(slots[0], slots[0] + 3)) or body_refs[slots[0]] != header.self_ref:
                        continue
                    corridor = (caption_box[0], caption_box[3], caption_box[2], max(header_box[3], anchor_box[3]))
                    allowed = {header.self_ref, caption.self_ref}
                    for picture in (target, owner):
                        allowed.update(item.self_ref for item, _ in document.iterate_items(root=picture, traverse_pictures=True))
                    blocked = False
                    for item in items.values():
                        if item.self_ref in allowed:
                            continue
                        for p in getattr(item, "prov", []):
                            if p.page_no != page:
                                continue
                            box = _bounds(p.bbox.to_bottom_left_origin(document.pages[page].size.height).model_dump(mode="json"))
                            if (min(box[2], corridor[2]) - max(box[0], corridor[0]) > _BOX_TOLERANCE
                                    and min(box[3], corridor[3]) - max(box[1], corridor[1]) > _BOX_TOLERANCE):
                                blocked = True
                                break
                        if blocked:
                            break
                    if blocked:
                        continue
                    proposals.append({"item_ref": header.self_ref, "title_ref": header.self_ref,
                        "picture_ref": target.self_ref,
                        "page_no": page, "source_sha256": digest,
                        "source_text_sha256": record["source_text_sha256"], "source_charspan": [0, len(header.text)],
                        "original_parent_ref": header.parent.cref, "original_level": header.level,
                        "title_provenance": [p.model_dump(mode="json") for p in header.prov],
                        "picture_provenance": [p.model_dump(mode="json") for p in target.prov],
                        "anchor_title_provenance": [p.model_dump(mode="json") for p in anchor.prov],
                        "anchor_picture_provenance": [p.model_dump(mode="json") for p in owner.prov],
                        "caption_provenance": [p.model_dump(mode="json") for p in caption.prov],
                        "caption_text_sha256": hashlib.sha256(caption.text.encode()).hexdigest(),
                        "association": "spatial_title_association", "source_graph_modified": False,
                        "caption_ownership_inferred": False, "anchor_item_ref": anchor.self_ref,
                        "anchor_picture_ref": owner.self_ref, "original_caption_ref": caption.self_ref,
                        "proof": {"native_line_ids": [line["line_id"], other_line["line_id"]],
                            "font_key": line["font_key"], "native_line_height": height,
                            "baseline_tolerance_points": tolerance,
                            "baseline_delta_points": abs(line["baseline_axis_start"][1] - other_line["baseline_axis_start"][1]),
                            "horizontal_center_delta_points": center_delta,
                            "horizontal_center_tolerance_points": .1 * height,
                            "title_to_picture_gap_points": gap, "maximum_gap_points": 2 * height,
                            "target_picture_bbox": list(target_box), "anchor_picture_bbox": list(anchor_box),
                            "caption_bbox": list(caption_box), "body_slots": slots,
                            "body_refs": [body_refs[index] for index in slots],
                            "intervening_source_items": []}})
            if len(proposals) == 1:
                associations.extend(proposals)
            decisions.append({"item_ref": header.self_ref, "page_no": page,
                              "status": "associated" if len(proposals) == 1 else "preserved",
                              "candidate_count": len(proposals),
                              "reason": "unique_anchored_panel_title" if len(proposals) == 1 else "no_unique_anchored_panel_title"})
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            decisions.append({"item_ref": header.self_ref, "status": "preserved", "reason": str(error)})
    # Multiple external headings must never compete for the same picture.
    conflicts = {entry["picture_ref"] for entry in associations
                 if sum(other["picture_ref"] == entry["picture_ref"] for other in associations) != 1}
    for decision in decisions:
        if any(entry["item_ref"] == decision["item_ref"] and entry["picture_ref"] in conflicts for entry in associations):
            decision.update(status="preserved", reason="competing_external_picture_titles")
    return [entry for entry in associations if entry["picture_ref"] not in conflicts], decisions


def validated_spatial_title_associations(document, mapping, *, page_no=None):
    """Recompute geometry/source bindings before consuming an export receipt."""
    if not mapping:
        return []
    proposed = mapping.get("spatial_title_associations", [])
    if not isinstance(proposed, list) or any(not isinstance(entry, dict) for entry in proposed):
        raise ValueError("figure_title_association_binding_changed")
    declared = {ref: value.get("external_title_refs", []) for ref, value in mapping.get("pictures", {}).items()
                if value.get("external_title_refs")}
    if not proposed:
        if declared:
            raise ValueError("figure_title_association_binding_changed")
        return []
    if mapping.get("policy") != PICTURE_STRUCTURE_POLICY:
        raise ValueError("figure_title_association_binding_changed")
    expected, _ = associate_external_picture_titles(document, mapping)
    if proposed != expected:
        raise ValueError("figure_title_association_binding_changed")
    expected_refs = defaultdict(list)
    for entry in expected:
        expected_refs[entry["picture_ref"]].append(entry["title_ref"])
    if declared != dict(expected_refs):
        raise ValueError("figure_title_association_binding_changed")
    return [entry for entry in expected if page_no is None or entry["page_no"] == page_no]


def build_picture_structure(document, source_pdf):
    """Build export-only source ranges and physical figure rows; never mutate."""
    from .docling_pdf_text_visibility import read_pdf_text_visibility, match_text_visibility

    content = bytes(source_pdf) if isinstance(source_pdf, (bytes, bytearray)) else Path(source_pdf).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    picture_pages = {p.page_no for item in document.pictures for p in item.prov}
    from .docling_picture_labels import label_candidates,associate_picture_labels
    body_labels={item.self_ref for item,_,_ in label_candidates(document)}
    descendants, owners = {}, {}
    for picture in document.pictures:
        for item, _ in document.iterate_items(root=picture, traverse_pictures=True):
            if item.self_ref == picture.self_ref:
                continue
            descendants[item.self_ref] = item
            owners.setdefault(item.self_ref, []).append(picture.self_ref)
    candidates = [item for item in document.texts
                  if _label(item) in {"text", "section_header"}
                  and (item.self_ref in descendants or item.self_ref in body_labels or (_label(item) == "section_header"
                       and any(p.page_no in picture_pages for p in item.prov)))]
    pages = sorted({p.page_no for item in candidates for p in item.prov})
    mapping = {"policy": PICTURE_STRUCTURE_POLICY, "source_sha256": digest, "items": {}, "pictures": {},
               "spatial_title_associations": [],"spatial_label_associations":[]}
    audit = {"policy": PICTURE_STRUCTURE_POLICY, "source_sha256": digest,
             "source_document_modified": False, "inference_performed": False,
             "items": [], "physical_token_rows": [], "review_required_refs": [],
             "spatial_title_associations": [], "spatial_title_decisions": [],"spatial_label_associations":[]}
    if not candidates:
        return mapping, audit
    native = read_native_picture_lines(content, page_numbers=pages)
    visibility = read_pdf_text_visibility(content, page_numbers=pages)
    for item in candidates:
        record = {"item_ref": item.self_ref, "label": _label(item),
                  "original_level": getattr(item, "level", None),
                  "original_parent_ref": item.parent.cref if item.parent else None,
                  "source_role": "picture_child" if item.self_ref in descendants else "body_figure_label_candidate" if item.self_ref in body_labels else "body_header",
                  "picture_refs": owners.get(item.self_ref, []),
                  "source_provenance": [p.model_dump(mode="json") for p in item.prov],
                  "source_text": item.text, "status": "preserved", "review_required": False,
                  "reasons": [], "segments": []}
        try:
            if item.text != item.orig:
                raise ValueError("figure_source_text_already_transformed")
            if len(item.prov) != 1:
                raise ValueError("figure_structure_requires_single_page_provenance")
            p = item.prov[0]
            size = document.pages[p.page_no].size
            bbox = p.bbox.to_bottom_left_origin(size.height).model_dump(mode="json")
            source_page = native[p.page_no]
            if source_page["source_sha256"] != digest:
                raise ValueError("figure_native_source_hash_mismatch")
            record.update(partition_source_lines(item.text, bbox, source_page["lines"],
                          page_no=p.page_no, source_sha256=digest), page_no=p.page_no)
            for segment in record["segments"]:
                line = segment["native_line"]
                proof = match_text_visibility(visibility.get(p.page_no, {}), line["native_text"], line["bbox"],
                                             source_sha256=digest, page_no=p.page_no)
                segment["visibility"] = proof
                segment["display"] = not bool(proof.get("fully_clipped"))
            record["status"] = ("projected" if len(record["segments"]) > 1
                                or any(not segment["display"] for segment in record["segments"])
                                else "unchanged")
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            record.update(review_required=True, reasons=[str(error)])
            audit["review_required_refs"].append(item.self_ref)
        mapping["items"][item.self_ref] = record
        audit["items"].append(record)
    for picture in document.pictures:
        entries = [mapping["items"].get(ref.cref) for ref in picture.children]
        rows = group_rotated_token_rows(entries)
        mapping["pictures"][picture.self_ref] = {"token_rows": rows,
            "original_child_refs": [ref.cref for ref in picture.children]}
        audit["physical_token_rows"].extend(dict(row, picture_ref=picture.self_ref) for row in rows)
    associations, decisions = associate_external_picture_titles(document, mapping)
    mapping["spatial_title_associations"] = associations
    audit.update(spatial_title_associations=associations, spatial_title_decisions=decisions)
    for association in associations:
        mapping["items"][association["item_ref"]]["spatial_title_association"] = association
        mapping["pictures"][association["picture_ref"]].setdefault("external_title_refs", []).append(association["item_ref"])
    labels=associate_picture_labels(document,mapping)
    mapping["spatial_label_associations"]=labels
    audit["spatial_label_associations"]=labels
    for entry in labels:
        mapping["pictures"][entry["picture_ref"]].setdefault("external_label_refs",[]).append(entry["label_ref"])
    return mapping, audit
