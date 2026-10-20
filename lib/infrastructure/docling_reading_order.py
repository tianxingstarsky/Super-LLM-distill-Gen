"""Order a projected PDF page with Docling's official rule-based predictor.

The caller first projects cross-page text by its provenanced character spans.
Only references in a shallow page view's body children are reordered here.
Tables, pictures, lists and their owned descendants remain atomic subtrees;
their current-page boxes, including captions, define the sorting footprint.
No content, provenance, label, reference identity or parent relationship changes.
"""
from __future__ import annotations

from collections import Counter
import math


READING_ORDER_POLICY = "docling_official_projected_page_siblings_v2"


def _finite_positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _page_box(provenance, size):
    box = provenance.bbox.to_bottom_left_origin(size.height)
    values = (box.l, box.b, box.r, box.t)
    if (not all(math.isfinite(value) for value in values)
            or not 0 <= box.l < box.r <= size.width
            or not 0 <= box.b < box.t <= size.height):
        raise ValueError("invalid_page_reading_order_bbox")
    return box


def _satellite_relations(document, children):
    """Prove unique floating associations without altering the source tree."""
    from docling_core.types.doc import FloatingItem

    body_ref = document.body.self_ref
    body_refs = {child.cref for child in children}
    stack = [(child, body_ref) for child in reversed(children)]
    nodes, visited = [], set()
    while stack:
        child, parent_ref = stack.pop()
        target = child.resolve(document)
        if target.self_ref != child.cref or target.parent is None or target.parent.cref != parent_ref:
            raise ValueError("invalid_reading_order_parent_reference")
        if child.cref in visited:
            raise ValueError("duplicate_or_cyclic_reading_order_reference")
        visited.add(child.cref)
        nodes.append(target)
        stack.extend((entry, target.self_ref) for entry in reversed(target.children))
    claims, external, evidence = {}, {}, []
    for owner in nodes:
        if not isinstance(owner, FloatingItem):
            continue
        for field, expected_label in (("captions", "caption"), ("footnotes", "footnote")):
            for satellite in getattr(owner, field, []):
                if satellite.cref in claims:
                    reason = ("duplicate_reading_order_satellite_reference"
                              if claims[satellite.cref] == owner.self_ref
                              else "multiple_reading_order_satellite_owners")
                    raise ValueError(reason)
                claims[satellite.cref] = owner.self_ref
                target = satellite.resolve(document)
                if (target.self_ref != satellite.cref
                        or getattr(target.label, "value", target.label) != expected_label):
                    raise ValueError("invalid_reading_order_satellite_type")
                parent_ref = target.parent.cref if target.parent is not None else None
                if parent_ref == owner.self_ref:
                    continue
                # The official serializer skips a body-level satellite because
                # its floating owner emits it. Its original body slot must
                # therefore stay untouched, not become a second sorting unit.
                if (parent_ref != body_ref or target.self_ref not in body_refs
                        or owner.parent is None or owner.parent.cref != body_ref):
                    raise ValueError("unowned_reading_order_satellite")
                owner_pages = {p.page_no for p in owner.prov}
                target_pages = {p.page_no for p in target.prov}
                if len(owner_pages) != 1 or target_pages != owner_pages:
                    raise ValueError("ambiguous_reading_order_satellite_page")
                external[target.self_ref] = owner.self_ref
                evidence.append({"ref": target.self_ref, "owner_ref": owner.self_ref,
                                 "field": field, "page_no": next(iter(owner_pages)),
                                 "original_body_slot_preserved": True})
    return external, evidence


def _subtree_boxes(document, node, page_number, size, seen, external_satellites):
    """Collect owned page-local geometry, keeping every subtree indivisible."""
    ref = node.self_ref
    if ref in seen:
        raise ValueError("duplicate_or_cyclic_reading_order_reference")
    seen.add(ref)
    provenance = getattr(node, "prov", [])
    own_pages = {prov.page_no for prov in provenance}
    if page_number in own_pages and own_pages != {page_number}:
        raise ValueError("cross_page_item_requires_projection")
    boxes = [_page_box(prov, size) for prov in provenance if prov.page_no == page_number]
    owners = [ref] if boxes else []
    children = list(getattr(node, "children", []))
    child_refs = {child.cref for child in children}
    # A proven body-level association contributes geometry only. The source
    # graph and the serializer's satellite ownership remain unchanged.
    for field in ("captions", "footnotes"):
        for child in getattr(node, field, []):
            if child.cref not in child_refs:
                children.append(child)
                child_refs.add(child.cref)
    for child in children:
        target = child.resolve(document)
        is_external = external_satellites.get(child.cref) == ref
        if (target.self_ref != child.cref or target.parent is None
                or (target.parent.cref != ref and not is_external)):
            raise ValueError("invalid_reading_order_parent_reference")
        child_boxes, child_owners = _subtree_boxes(
            document, target, page_number, size, seen, external_satellites)
        boxes.extend(child_boxes)
        owners.extend(child_owners)
    return boxes, owners


def project_page_reading_order(document, page_number):
    """Return ``(page_view, audit)`` without mutating the supplied document.

    Only existing top-level sibling slots that contain this page are permuted.
    Other-page slots and all child ordering stay intact. Missing/ambiguous
    source geometry or an invalid predictor result preserves the input view.
    This is physical reading order, not a claim about semantic continuation.
    """
    audit = {
        "policy": READING_ORDER_POLICY,
        "engine": "docling.models.postprocessing.reading_order_rb.ReadingOrderPredictor",
        "page_no": page_number, "scope": "body_page_siblings",
        "status": "review_required", "review_required": True, "reasons": [],
        "changed": False, "source_document_mutated": False, "text_modified": False,
        "original_page_refs": [], "predicted_page_refs": [], "actual_page_refs": [],
        "elements": [], "atomic_subtrees": True, "merge_prediction_used": False,
    }
    try:
        if type(page_number) is not int or page_number < 1:
            raise ValueError("invalid_reading_order_page_number")
        page = document.pages.get(page_number)
        size = page.size if page is not None else None
        if size is None or not _finite_positive(size.width) or not _finite_positive(size.height):
            raise ValueError("missing_reading_order_page_size")
        body = document.body
        children = list(body.children)
        refs = [child.cref for child in children]
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate_reading_order_sibling_reference")
        audit["original_body_refs"] = refs
        external_satellites, satellite_evidence = _satellite_relations(document, children)
        audit["external_satellites"] = satellite_evidence
        candidates, slots, seen = [], [], set()
        for slot, child in enumerate(children):
            if child.cref in external_satellites:
                continue
            item = child.resolve(document)
            if item.self_ref != child.cref or item.parent is None or item.parent.cref != body.self_ref:
                raise ValueError("invalid_reading_order_parent_reference")
            boxes, owners = _subtree_boxes(
                document, item, page_number, size, seen, external_satellites)
            if not boxes:
                continue
            box = {
                "l": min(value.l for value in boxes), "b": min(value.b for value in boxes),
                "r": max(value.r for value in boxes), "t": max(value.t for value in boxes),
                "coord_origin": "BOTTOMLEFT",
            }
            label = getattr(item.label, "value", item.label)
            # Structural groups have no page geometry or DocItemLabel of their
            # own. Their descendant union is one text-bearing sorting unit.
            if not getattr(item, "prov", []):
                label = "text"
            candidates.append((child, item, label, box))
            slots.append(slot)
            audit["elements"].append({
                "ref": child.cref, "label": label, "bbox": box,
                "source_bbox_refs": owners, "body_slot": slot,
            })
        original = [child.cref for child, *_ in candidates]
        audit.update(original_page_refs=original, actual_page_refs=list(original))
        if len(candidates) < 2:
            audit.update(status="unchanged", review_required=False,
                         predicted_page_refs=list(original), actual_body_refs=list(refs))
            return document, audit

        from docling.models.postprocessing.reading_order_rb import PageElement, ReadingOrderPredictor
        elements = [
            PageElement(cid=index, ref=child.model_copy(deep=True), text=getattr(item, "text", ""),
                        page_no=page_number, page_size=size.model_copy(deep=True), label=label, **box)
            for index, (child, item, label, box) in enumerate(candidates)
        ]
        ordered = ReadingOrderPredictor().predict_reading_order(elements)
        predicted = [element.ref.cref for element in ordered]
        audit["predicted_page_refs"] = predicted
        if Counter(predicted) != Counter(original):
            raise ValueError("reading_order_prediction_is_not_a_permutation")
        expected_ids = {child.cref: index for index, (child, *_) in enumerate(candidates)}
        if any(element.cid != expected_ids[element.ref.cref] or element.page_no != page_number
               for element in ordered):
            raise ValueError("reading_order_prediction_identity_changed")
        by_ref = {child.cref: child for child, *_ in candidates}
        output = list(children)
        for slot, ref in zip(slots, predicted):
            output[slot] = by_ref[ref]
        changed = predicted != original
        view = document.model_copy(update={"body": body.model_copy(update={"children": output})}) if changed else document
        audit.update(status="reordered" if changed else "unchanged", review_required=False,
                     changed=changed, actual_page_refs=predicted,
                     actual_body_refs=[child.cref for child in output])
        return view, audit
    except (AttributeError, ImportError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        audit["reasons"].append(str(error) or type(error).__name__)
        audit["error_type"] = type(error).__name__
        return document, audit
