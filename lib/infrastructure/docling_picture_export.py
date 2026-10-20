"""Serialize existing figure text with Docling's official picture traversal.

The source graph is never rearranged. A picture's content occupies the image
part of the upstream PictureSerializer, so upstream layout caption placement
and satellite deduplication still apply. Figure labels stay in a blockquote;
rotated tokens are neither joined into sentences nor deduplicated.
"""
from __future__ import annotations

from collections import Counter
import hashlib


PICTURE_EXPORT_POLICY = "docling_picture_children_scoped_layout_captions_v1"


def _label(item):
    return getattr(getattr(item, "label", None), "value", getattr(item, "label", None))


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


def _serializer(document, page_number, *, traverse_pictures):
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
    from docling_core.transforms.serializer.common import create_ser_result

    required = {"traverse_pictures", "caption_placement"}
    if not required <= set(MarkdownParams.model_fields):
        if _needs_modern_picture_serializer(document):
            raise ValueError("document_docling_version_mismatch")
        return None, None
    from docling_core.transforms.serializer.markdown import (
        MarkdownMetaSerializer, MarkdownPictureSerializer, _caption_goes_after,
    )
    from docling_core.types.doc import PictureItem, TextItem, TableItem
    from pydantic import PrivateAttr

    class SourceMarkdownDocSerializer(MarkdownDocSerializer):
        _source_events: list = PrivateAttr(default_factory=list)

        def serialize(self, *, item=None, **kwargs):
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

        def _children(self, item, doc_serializer, kwargs):
            parts = doc_serializer.get_parts(item=item, **kwargs)
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
                                       meta_serializer=SourcePictureMetaSerializer()), picture_serializer


def serialize_picture_page(document, page_number):
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
    serializer, picture_serializer = _serializer(document, page_number, traverse_pictures=True)
    if serializer is None:
        return document.export_to_markdown(page_no=page_number, image_placeholder=""), {
            "policy": PICTURE_EXPORT_POLICY, "page_no": page_number,
            "pictures": [], "recovered_refs": [], "legacy_without_picture_children": True,
            **satellite_audit, "duplicate_span_refs": satellite_audit["satellite_duplicate_refs"],
        }
    result = serializer.serialize()
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
    source_events = [event for event in serializer._source_events if event["item_ref"] in required_refs]
    duplicate_emission_refs = [ref for ref, count in Counter(event["item_ref"] for event in source_events).items()
                               if count > 1]
    audit = {"policy": PICTURE_EXPORT_POLICY, "page_no": page_number, "pictures": groups,
             "serialized_span_refs": span_refs,
             "recovered_refs": sorted((emitted - baseline_refs) & child_refs),
             "all_child_refs": sorted(child_refs), "required_source_child_refs": sorted(required_refs),
             "missing_child_refs": sorted(required_refs - emitted),
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
