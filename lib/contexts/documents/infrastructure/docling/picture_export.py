"""Serialize existing figure text with Docling's official picture traversal.

The source graph is never rearranged. A picture's content occupies the image
part of the upstream PictureSerializer, so upstream layout caption placement
and satellite deduplication still apply. Figure labels stay in a blockquote;
rotated tokens are neither joined into sentences nor deduplicated.
"""
from __future__ import annotations

from collections import Counter
import hashlib


PICTURE_EXPORT_POLICY = "docling_picture_children_exact_lines_titles_and_labels_v4"


def _project_text(item, structure, page_number, *, page_height=None):
    """Apply only exact, source-bound physical lines; leave unknowns intact."""
    plan = (structure or {}).get("items", {}).get(item.self_ref)
    if not plan or plan.get("review_required") or plan.get("page_no") != page_number:
        return None
    text = item.text
    if (plan.get("item_ref") != item.self_ref or plan.get("source_text") != text or not plan.get("exact_source_partition")
            or plan.get("source_text_sha256") != hashlib.sha256(text.encode()).hexdigest()
            or plan.get("source_provenance") != [p.model_dump(mode="json") for p in item.prov]):
        raise ValueError("document_docling_invalid_output")
    pieces, visible, cursor = [], [], 0
    for segment in plan.get("segments", []):
        start, stop = segment["source_charspan"]
        if (type(start) is not int or type(stop) is not int
                or not cursor <= start < stop <= len(text)
                or segment["text"] != text[start:stop]
                or segment["separator_before"] != text[cursor:start]):
            raise ValueError("document_docling_invalid_output")
        line = segment.get("native_line", {})
        from lib.contexts.documents.infrastructure.docling.picture_structure import _bounds, _contains, _compact
        try:
            # Original provenance may use either origin. Native lines use PDF
            # bottom-left coordinates; a page height is required for conversion.
            source_boxes = [prov.bbox.to_bottom_left_origin(page_height).model_dump(mode="json")
                            if getattr(prov.bbox.coord_origin, "value", prov.bbox.coord_origin) != "BOTTOMLEFT"
                            else prov.bbox.model_dump(mode="json") for prov in item.prov]
            bound = (line.get("source_sha256") == structure.get("source_sha256")
                     and line.get("page_no") == page_number
                     and _compact(line.get("native_text", "")) == _compact(segment["text"])
                     and any(_contains(_bounds(box), _bounds(line["bbox"])) for box in source_boxes))
        except (AttributeError, KeyError, TypeError, ValueError):
            bound = False
        if not bound:
            raise ValueError("document_docling_invalid_output")
        pieces.extend((segment["separator_before"], segment["text"]))
        if segment["display"]:
            visible.append(segment["text"])
        else:
            proof = segment.get("visibility", {})
            from lib.contexts.documents.infrastructure.docling.pdf_text_visibility import validate_clipped_text_binding
            if not validate_clipped_text_binding(proof, line, source_sha256=structure.get("source_sha256"),
                                                  page_no=page_number):
                raise ValueError("document_docling_invalid_output")
        cursor = stop
    pieces.append(plan.get("trailing_separator", ""))
    if not plan.get("segments") or "".join(pieces) != text:
        raise ValueError("document_docling_invalid_output")
    return "\n".join(visible)


def _label(item):
    return getattr(getattr(item, "label", None), "value", getattr(item, "label", None))


def _validate_associated_view(document, source_document, associations):
    """A valid source proof must also describe the actual exported nodes."""
    if not associations or source_document is document:
        return
    from docling_core.types.doc import RefItem
    fields = {"self_ref", "parent", "label", "content_layer", "prov", "children", "captions",
              "references", "footnotes", "text", "orig", "level", "formatting", "hyperlink"}
    try:
        if (document.body.model_dump(mode="json", exclude={"children"}) !=
                source_document.body.model_dump(mode="json", exclude={"children"})
                or Counter(ref.cref for ref in document.body.children) !=
                Counter(ref.cref for ref in source_document.body.children)):
            raise ValueError("association_body_view_not_a_permutation")
        refs = {entry[key] for entry in associations for key in
                ("title_ref", "label_ref", "picture_ref", "anchor_item_ref", "anchor_picture_ref", "original_caption_ref") if key in entry}
        for ref in refs:
            actual = RefItem(cref=ref).resolve(document)
            source = RefItem(cref=ref).resolve(source_document)
            if actual.model_dump(mode="json", include=fields) != source.model_dump(mode="json", include=fields):
                raise ValueError("association_view_node_changed")
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError("document_docling_invalid_output") from error


def _needs_modern_picture_serializer(document):
    return any(picture.children or picture.annotations or getattr(picture, "meta", None)
               for picture in getattr(document, "pictures", []))


def _has_source_content(item):
    if _label(item) == "picture":
        return False  # The empty image placeholder has no source text to lose.
    text = getattr(item, "text", None)
    if isinstance(text, str):
        return bool(text.strip() or (getattr(item, "orig", "") or "").strip())
    data = getattr(item, "data", None)
    return bool(data and any((getattr(cell, "text", "") or "").strip() or getattr(cell, "ref", None)
                             for cell in getattr(data, "table_cells", [])))


def _satellite_references(document, page_number):
    """Audit raw satellite links before upstream span aggregation hides repeats.

    A single caption being both a child and its owner's caption is normal.
    Repeating a link in captions/footnotes, sharing it across owners, or using
    it in two satellite roles is ambiguous source structure. Do not repair or
    silently normalize that structure while exporting it for review.
    """
    by_ref = {}
    for collection in ("pictures", "tables", "key_value_items", "form_items"):
        for owner in getattr(document, collection, []):
            owner_pages = {p.page_no for p in getattr(owner, "prov", [])}
            for role in ("captions", "footnotes"):
                for index, ref in enumerate(getattr(owner, role, [])):
                    source = ref.resolve(document)
                    source_pages = {p.page_no for p in getattr(source, "prov", [])}
                    event = {"item_ref": ref.cref, "owner_ref": owner.self_ref,
                             "satellite_role": role, "reference_index": index,
                             "owner_pages": sorted(owner_pages), "source_pages": sorted(source_pages),
                             "applies_to_page": page_number in owner_pages | source_pages}
                    by_ref.setdefault(ref.cref, []).append(event)
    duplicates = {ref: events for ref, events in by_ref.items()
                  if len(events) > 1 and any(event["applies_to_page"] for event in events)}
    return ({"satellite_reference_events": [event for events in by_ref.values() for event in events
                                              if event["applies_to_page"]],
             "satellite_duplicate_refs": sorted(duplicates),
             "satellite_duplicate_reference_events": [event for events in duplicates.values() for event in events]})


def _serializer(document, page_number, *, traverse_pictures, picture_structure=None, source_document=None):
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
    from docling_core.transforms.serializer.common import create_ser_result

    required = {"traverse_pictures", "caption_placement"}
    if not required <= set(MarkdownParams.model_fields):
        if _needs_modern_picture_serializer(document):
            raise ValueError("document_docling_version_mismatch")
        return None, None
    from docling_core.transforms.serializer.markdown import (
        MarkdownMetaSerializer, MarkdownPictureSerializer, MarkdownTextSerializer, _caption_goes_after,
    )
    from docling_core.types.doc import PictureItem, TextItem, TableItem, RefItem
    from pydantic import PrivateAttr
    associations,label_associations = [],[]
    if picture_structure and traverse_pictures:
        from .picture_structure import validated_spatial_title_associations
        try:
            associations = validated_spatial_title_associations(
                source_document or document, picture_structure, page_no=page_number)
            from .picture_labels import validated_picture_labels
            label_associations=validated_picture_labels(source_document or document,picture_structure,page_no=page_number)
            _validate_associated_view(document, source_document or document, associations+label_associations)
        except ValueError as error:
            raise ValueError("document_docling_invalid_output") from error
    external_titles = {entry["title_ref"]: entry for entry in associations}
    external_labels = {entry["label_ref"]:entry for entry in label_associations}

    class SourceTextSerializer(MarkdownTextSerializer):
        def serialize(self, *, item, **kwargs):
            source_page = document.pages.get(page_number)
            projected = _project_text(item, picture_structure, page_number,
                                       page_height=getattr(getattr(source_page, "size", None), "height", None))
            if projected is not None:
                if not projected:
                    return create_ser_result()
                item = item.model_copy(update={"text": projected})
            return super().serialize(item=item, **kwargs)

    class SourceMarkdownDocSerializer(MarkdownDocSerializer):
        _source_events: list = PrivateAttr(default_factory=list)

        def serialize(self, *, item=None, **kwargs):
            if item is not None and (item.self_ref in external_titles or item.self_ref in external_labels):
                # The source body node stays intact. It is rendered once at
                # the source-proven picture position by the text serializer.
                return create_ser_result()
            result = super().serialize(item=item, **kwargs)
            if (isinstance(item, (TextItem, TableItem)) and _label(item) != "caption"
                    and _has_source_content(item) and result.text):
                self._source_events.append({"item_ref": item.self_ref, "label": _label(item),
                                            "role": "source_item_render_call",
                                            "parent_ref": item.parent.cref if item.parent else None})
            return result

        def serialize_captions(self, item, **kwargs):
            result = super().serialize_captions(item=item, **kwargs)
            emitted = {span.item.self_ref for span in result.spans}
            self._source_events.extend({"item_ref": ref.cref, "label": "caption",
                                        "role": "source_caption_render_call", "owner_ref": item.self_ref}
                                       for ref in item.captions if ref.cref in emitted)
            return result

        def serialize_footnotes(self, item, **kwargs):
            result = super().serialize_footnotes(item=item, **kwargs)
            emitted = {span.item.self_ref for span in result.spans}
            self._source_events.extend({"item_ref": ref.cref, "label": "footnote",
                                        "role": "source_footnote_render_call", "owner_ref": item.self_ref}
                                       for ref in item.footnotes if ref.cref in emitted)
            return result

    class SourcePictureMetaSerializer(MarkdownMetaSerializer):
        def serialize(self, *, item, doc, **kwargs):
            # A model description/classification is metadata, not a native text
            # child. Keep it in the document/audit, outside source Markdown.
            if isinstance(item, PictureItem):
                return create_ser_result()
            return super().serialize(item=item, doc=doc, **kwargs)

    class ScopedPictureSerializer(MarkdownPictureSerializer):
        def __init__(self):
            self.contexts = []
            self.groups = []
            self.row_events = []
            self.title_events = []
            self.label_events = []

        def _children(self, item, doc_serializer, kwargs):
            parts = doc_serializer.get_parts(item=item, **kwargs)
            title_parts = []
            for entry in associations:
                if entry["picture_ref"] != item.self_ref:
                    continue
                source_title = RefItem(cref=entry["title_ref"]).resolve(document)
                options = dict(kwargs, visited=set())
                result = doc_serializer.text_serializer.serialize(
                    item=source_title, doc_serializer=doc_serializer, doc=document, **options)
                if not result.text or [span.item.self_ref for span in result.spans] != [entry["title_ref"]]:
                    raise ValueError("document_docling_invalid_output")
                title_parts.append(result)
                self.title_events.append(dict(entry, render_count=1,
                                              source_role="spatially_associated_body_title"))
            label_parts=[]
            for entry in label_associations:
                if entry["picture_ref"]!=item.self_ref:continue
                source_label=RefItem(cref=entry["label_ref"]).resolve(document)
                result=doc_serializer.text_serializer.serialize(item=source_label,doc_serializer=doc_serializer,
                    doc=document,**dict(kwargs,visited=set()))
                if not result.text or [span.item.self_ref for span in result.spans]!=[entry["label_ref"]]:
                    raise ValueError("document_docling_invalid_output")
                label_parts.append(result)
                self.label_events.append(dict(entry,render_count=1,source_role="spatially_associated_body_label"))
            parts = title_parts + parts + label_parts
            rows = [row for row in (picture_structure or {}).get("pictures", {}).get(
                item.self_ref, {}).get("token_rows", []) if row.get("page_no") == page_number]
            by_first = {row["source_refs"][0]: row for row in rows}
            projected, index, consumed = [], 0, []
            while index < len(parts):
                refs = [span.item.self_ref for span in parts[index].spans]
                row = by_first.get(refs[0]) if len(refs) == 1 else None
                if row is not None:
                    group = parts[index:index + len(row["source_refs"]) ]
                    actual = [[span.item.self_ref for span in part.spans] for part in group]
                    if actual != [[ref] for ref in row["source_refs"]] or not row.get("source_order_preserved"):
                        raise ValueError("document_docling_invalid_output")
                    projected.append(create_ser_result(text=" ".join(part.text for part in group), span_source=group))
                    self.row_events.append({"picture_ref": item.self_ref, "source_refs": row["source_refs"],
                                            "source_order_preserved": True, "repetitions_preserved": True,
                                            "semantic_relations_inferred": False})
                    consumed.append(row["source_refs"])
                    index += len(group)
                else:
                    projected.append(parts[index])
                    index += 1
            if consumed != [row["source_refs"] for row in rows]:
                raise ValueError("document_docling_invalid_output")
            parts = projected
            text = "\n\n".join(part.text for part in parts if part.text)
            quoted = "\n".join("> " + line if line else ">" for line in text.splitlines())
            return create_ser_result(text=quoted, span_source=parts)

        def serialize(self, *, item, doc_serializer, doc, **kwargs):
            options = dict(kwargs, include_annotations=False, enable_chart_tables=False)
            context = {"doc_serializer": doc_serializer, "kwargs": options, "image_part_called": False}
            self.contexts.append(context)
            try:
                result = super().serialize(item=item, doc_serializer=doc_serializer, doc=doc, **options)
                if not context["image_part_called"]:
                    # A parent may be on another page while its source children
                    # belong here. Upstream excludes that parent's image part;
                    # retain its children's original figure scope nonetheless.
                    children = self._children(item, doc_serializer, options)
                    if children.text:
                        params = MarkdownParams(**options)
                        after = _caption_goes_after(item, doc, params, standard_after=False)
                        ordered = [children, result] if after else [result, children]
                        result = create_ser_result(
                            text="\n\n".join(part.text for part in ordered if part.text),
                            span_source=ordered)
                self.groups.append({"picture_ref": item.self_ref,
                                    "span_refs": [span.item.self_ref for span in result.spans]})
                return result
            finally:
                self.contexts.pop()

        def _serialize_image_part(self, item, doc, image_mode, image_placeholder, **kwargs):
            context = self.contexts[-1]
            context["image_part_called"] = True
            image = super()._serialize_image_part(item, doc, image_mode, image_placeholder, **kwargs)
            children = self._children(item, context["doc_serializer"], context["kwargs"])
            return create_ser_result(
                text="\n\n".join(part.text for part in (image, children) if part.text),
                span_source=[image, children])

    picture_serializer = ScopedPictureSerializer()
    params = MarkdownParams(
        pages={page_number}, image_placeholder="", traverse_pictures=traverse_pictures,
        caption_placement="layout")
    return SourceMarkdownDocSerializer(doc=document, params=params, picture_serializer=picture_serializer,
                                       text_serializer=SourceTextSerializer(),
                                       meta_serializer=SourcePictureMetaSerializer()), picture_serializer


def serialize_picture_page(document, page_number, *, picture_structure=None, source_document=None):
    """Return Markdown and a provenance audit without OCR or model inference.

    Cross-page text must first be projected using its exact character spans by
    ``page_markdown``. Silently applying first-provenance filtering to an
    unsliced cross-page figure child is not permitted here.
    """
    if type(page_number) is not int or page_number < 1:
        raise ValueError("document_docling_invalid_provenance")
    satellite_audit = _satellite_references(document, page_number)
    for picture in getattr(document, "pictures", []):
        for child, _ in document.iterate_items(root=picture, traverse_pictures=True):
            if child.self_ref == picture.self_ref:
                continue
            pages = {prov.page_no for prov in getattr(child, "prov", [])}
            if not pages and _has_source_content(child):
                raise ValueError("document_docling_invalid_provenance")
            if len(pages) > 1 and page_number in pages:
                raise ValueError("document_docling_page_span_ambiguous")
    serializer, picture_serializer = _serializer(document, page_number, traverse_pictures=True,
                                                  picture_structure=picture_structure,
                                                  source_document=source_document)
    if serializer is None:
        return document.export_to_markdown(page_no=page_number, image_placeholder=""), {
            "policy": PICTURE_EXPORT_POLICY, "page_no": page_number,
            "pictures": [], "recovered_refs": [], "legacy_without_picture_children": True,
            **satellite_audit, "duplicate_span_refs": satellite_audit["satellite_duplicate_refs"],
        }
    result = serializer.serialize()
    expected_titles = [entry["title_ref"] for entry in (picture_structure or {}).get("spatial_title_associations", [])
                       if entry["page_no"] == page_number]
    actual_titles = [entry["title_ref"] for entry in picture_serializer.title_events]
    if Counter(actual_titles) != Counter(expected_titles) or len(set(actual_titles)) != len(actual_titles):
        raise ValueError("document_docling_invalid_output")
    expected_labels=[e["label_ref"] for e in (picture_structure or {}).get("spatial_label_associations",[]) if e["page_no"]==page_number]
    actual_labels=[e["label_ref"] for e in picture_serializer.label_events]
    if Counter(actual_labels)!=Counter(expected_labels) or len(set(actual_labels))!=len(actual_labels):
        raise ValueError("document_docling_invalid_output")
    baseline, _ = _serializer(document, page_number, traverse_pictures=False)
    baseline_refs = {span.item.self_ref for span in baseline.serialize().spans}
    span_refs = [span.item.self_ref for span in result.spans]
    emitted = set(span_refs)
    groups = []
    for picture in getattr(document, "pictures", []):
        descendants = []
        captions = {ref.cref for ref in picture.captions}
        for child, _ in document.iterate_items(root=picture, traverse_pictures=True):
            if child.self_ref == picture.self_ref:
                continue
            provenance = [p.model_dump(mode="json") for p in getattr(child, "prov", [])
                          if p.page_no == page_number]
            if not provenance:
                continue
            descendants.append({
                "item_ref": child.self_ref, "parent_ref": child.parent.cref if child.parent else None,
                "label": _label(child), "caption": child.self_ref in captions,
                "source_role": "existing_picture_child", "provenance": provenance,
                "original_text": getattr(child, "orig", ""), "serialized": child.self_ref in emitted,
                "required_source_content": _has_source_content(child) and
                    getattr(getattr(child, "content_layer", None), "value", None) == "body",
            })
        if descendants or any(p.page_no == page_number for p in picture.prov):
            groups.append({"picture_ref": picture.self_ref,
                           "parent_ref": picture.parent.cref if picture.parent else None,
                           "provenance": [p.model_dump(mode="json") for p in picture.prov],
                           "direct_child_refs": [ref.cref for ref in picture.children],
                           "caption_refs": [ref.cref for ref in picture.captions],
                           "children": descendants,
                           "retained_metadata_annotation_count": len(picture.annotations),
                           "metadata_description_is_source_text": False})
    child_refs = {child["item_ref"] for group in groups for child in group["children"]}
    required_refs = {child["item_ref"] for group in groups for child in group["children"]
                     if child["required_source_content"]}
    structure_items = [plan for plan in (picture_structure or {}).get("items", {}).values()
                       if plan.get("page_no") == page_number]
    excluded = {plan["item_ref"] for plan in structure_items if not plan.get("review_required")
                and plan.get("segments") and all(not segment["display"] for segment in plan["segments"])}
    source_events = [event for event in serializer._source_events if event["item_ref"] in required_refs]
    duplicate_emission_refs = [ref for ref, count in Counter(event["item_ref"] for event in source_events).items()
                               if count > 1]
    audit = {"policy": PICTURE_EXPORT_POLICY, "page_no": page_number, "pictures": groups,
             "serialized_span_refs": span_refs,
             "recovered_refs": sorted((emitted - baseline_refs) & child_refs),
             "all_child_refs": sorted(child_refs), "required_source_child_refs": sorted(required_refs),
             "missing_child_refs": sorted(required_refs - emitted - excluded),
             "visibility_excluded_refs": sorted(excluded),
             "source_text_structure": structure_items,
             "physical_row_projection_events": picture_serializer.row_events,
             "spatial_title_projection_events": picture_serializer.title_events,
             "spatial_label_projection_events": picture_serializer.label_events,
             "source_serialization_events": source_events,
             "duplicate_source_emission_refs": duplicate_emission_refs,
             # Final Docling spans are de-duplicated by create_ser_result; use
             # actual source render-call events as well, never spans alone.
             "duplicate_span_refs": sorted(set(duplicate_emission_refs) |
                 set(satellite_audit["satellite_duplicate_refs"]) | {
                 ref for ref, count in Counter(span_refs).items() if count > 1}),
             **satellite_audit,
             "figure_serialization_groups": [group for group in picture_serializer.groups if group["span_refs"]],
             "existing_structure_preserved": True,
             "rotated_text_reordered": False, "token_repetitions_deduplicated": False,
             "markdown_sha256": hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
             "inference_performed": False}
    return result.text, audit


def export_picture_markdown(document, page_number):
    return serialize_picture_page(document, page_number)[0]


def audit_picture_export(document, page_number):
    return serialize_picture_page(document, page_number)[1]
