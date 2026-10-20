"""Figure text remains complete, scoped, paginated, and distinct from metadata."""
from copy import deepcopy

import pytest

from docling_core.transforms.serializer.markdown import MarkdownParams
from docling_core.types.doc import (
    BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, ProvenanceItem, PageItem, Size,
)

from lib.infrastructure.docling_picture_export import (
    PICTURE_EXPORT_POLICY, export_picture_markdown, serialize_picture_page,
)


MODERN = {"traverse_pictures", "caption_placement"} <= set(MarkdownParams.model_fields)
modern_only = pytest.mark.skipif(not MODERN, reason="Requires installed Docling-core 2.102 official picture traversal")


def prov(text="", page=1, *, l=0, b=20, r=100, t=100):
    return ProvenanceItem(page_no=page, charspan=(0, len(text)),
                          bbox=BoundingBox(l=l, b=b, r=r, t=t, coord_origin=CoordOrigin.BOTTOMLEFT))


def source_picture(document=None, *, page=1, caption_child=True):
    document = document or DoclingDocument(name="Source figure text")
    document.pages[page] = PageItem(page_no=page, size=Size(width=100, height=130))
    picture = document.add_picture(prov=prov(page=page))
    caption = document.add_text(label=DocItemLabel.CAPTION, text="Figure 1: Source caption.",
                                prov=prov("Figure 1: Source caption.", page, b=0, t=10),
                                parent=picture if caption_child else None)
    picture.captions.append(caption.get_ref())
    title = document.add_heading(text="Existing figure title", level=1, parent=picture,
                                  prov=prov("Existing figure title", page, b=90, t=99))
    first = document.add_text(label=DocItemLabel.TEXT, text="first_label", parent=picture,
                              prov=prov("first_label", page, b=70, t=80))
    second = document.add_text(label=DocItemLabel.TEXT, text="second_label", parent=picture,
                               prov=prov("second_label", page, b=40, t=50))
    return document, picture, caption, title, first, second


@modern_only
@pytest.mark.parametrize("caption_child", [True, False])
def test_picture_children_are_scoped_and_layout_caption_is_emitted_once_after_them(caption_child):
    doc, picture, caption, title, first, second = source_picture(caption_child=caption_child)
    before = doc.model_dump_json()
    markdown, audit = serialize_picture_page(doc, 1)
    assert markdown.count(caption.text) == 1
    assert "> ## Existing figure title" in markdown
    assert "\n## Existing figure title" not in markdown
    assert "> first\\_label" in markdown and "> second\\_label" in markdown
    assert markdown.index("first\\_label") < markdown.index("second\\_label") < markdown.index(caption.text)
    assert audit["policy"] == PICTURE_EXPORT_POLICY
    assert audit["missing_child_refs"] == audit["duplicate_span_refs"] == []
    assert {title.self_ref, first.self_ref, second.self_ref} <= set(audit["recovered_refs"])
    assert audit["pictures"][0]["caption_refs"] == [caption.self_ref]
    assert doc.model_dump_json() == before


@modern_only
def test_caption_above_picture_stays_above_by_official_layout_policy():
    doc, _, caption, _, _, _ = source_picture()
    caption.prov[0].bbox = BoundingBox(l=0, b=110, r=100, t=120, coord_origin=CoordOrigin.BOTTOMLEFT)
    markdown = export_picture_markdown(doc, 1)
    assert markdown.index(caption.text) < markdown.index("> ## Existing figure title")


@modern_only
def test_repeated_axis_tokens_remain_distinct_and_are_not_reconstructed_as_prose():
    doc, picture, _, _, first, second = source_picture()
    first.text = first.orig = second.text = second.orig = "making"
    # The source child order is meaningful even for rotated text whose ink
    # bbox top depends on the word's length. No geometric sorting is allowed.
    first.prov[0].bbox.t = 50
    first.prov[0].bbox.b = 40
    second.prov[0].bbox.t = 80
    second.prov[0].bbox.b = 70
    markdown, audit = serialize_picture_page(doc, 1)
    assert markdown.count("> making") == 2
    assert "making making" not in markdown
    group = audit["pictures"][0]
    assert group["direct_child_refs"] == [ref.cref for ref in picture.children]
    assert [record["item_ref"] for record in group["children"]][-2:] == [first.self_ref, second.self_ref]
    assert not audit["rotated_text_reordered"] and not audit["token_repetitions_deduplicated"]


@modern_only
@pytest.mark.parametrize("text,escaped", [("<EOS>", "&lt;EOS&gt;"), ("<pad>", "&lt;pad&gt;"),
                                           ("<unfinished", "&lt;unfinished"), ("a < b & c > d", "a &lt; b &amp; c &gt; d")])
def test_literal_special_tokens_and_truncated_angles_use_official_html_escaping(text, escaped):
    doc, _, _, _, first, _ = source_picture()
    first.text = first.orig = text
    markdown = export_picture_markdown(doc, 1)
    assert "> " + escaped in markdown
    assert first.orig == text


@modern_only
def test_nested_pictures_keep_nested_scope_and_each_caption_once():
    doc, picture, outer_caption, _, _, _ = source_picture()
    inner = doc.add_picture(parent=picture, prov=prov(b=30, t=70, l=10, r=90))
    inner_caption = doc.add_text(label=DocItemLabel.CAPTION, text="Figure 2: Inner caption.",
                                 parent=inner, prov=prov("Figure 2: Inner caption.", b=21, t=29))
    inner.captions.append(inner_caption.get_ref())
    child = doc.add_text(label=DocItemLabel.TEXT, text="Inner child", parent=inner,
                         prov=prov("Inner child", b=40, t=60))
    before = doc.model_dump_json()
    markdown, audit = serialize_picture_page(doc, 1)
    assert "> > Inner child" in markdown
    assert markdown.count(inner_caption.text) == markdown.count(outer_caption.text) == 1
    assert markdown.index("Inner child") < markdown.index(inner_caption.text) < markdown.index(outer_caption.text)
    assert child.self_ref in audit["recovered_refs"]
    assert len(audit["pictures"]) == 2
    assert audit["missing_child_refs"] == []
    assert doc.model_dump_json() == before


@modern_only
def test_cross_page_children_use_their_own_page_and_do_not_escape_figure_scope():
    doc, picture, caption, _, _, _ = source_picture()
    later = doc.add_text(label=DocItemLabel.TEXT, text="Second page source child", parent=picture,
                         prov=prov("Second page source child", page=2))
    one, first_audit = serialize_picture_page(doc, 1)
    two, second_audit = serialize_picture_page(doc, 2)
    assert "Second page source child" not in one
    assert "> Second page source child" in two
    assert caption.text not in two
    assert second_audit["pictures"][0]["picture_ref"] == picture.self_ref
    assert second_audit["pictures"][0]["children"][0]["item_ref"] == later.self_ref
    assert all(p["page_no"] == 2 for record in second_audit["pictures"][0]["children"] for p in record["provenance"])
    assert first_audit["missing_child_refs"] == second_audit["missing_child_refs"] == []


@modern_only
def test_unsliced_cross_page_child_cannot_be_silently_filtered_by_first_provenance():
    doc, _, _, _, first, _ = source_picture()
    first.text = "First page. Second page."
    first.prov = [prov("First page.", 1), prov("Second page.", 2)]
    first.prov[0].charspan = (0, 11)
    first.prov[1].charspan = (12, 24)
    for page in (1, 2):
        with pytest.raises(ValueError, match="document_docling_page_span_ambiguous"):
            serialize_picture_page(doc, page)


@modern_only
def test_child_inline_math_placeholder_is_emitted_exactly_once_without_mutation():
    doc, _, _, _, first, _ = source_picture()
    first.text = "Given DFINLINEabcdEND, use the source knowledge."
    before = doc.model_dump_json()
    markdown, audit = serialize_picture_page(doc, 1)
    assert markdown.count("DFINLINEabcdEND") == 1
    restored = markdown.replace("DFINLINEabcdEND", "$x_2$")
    assert "$x_2$" in restored and r"$x\_2$" not in restored
    assert first.self_ref in audit["recovered_refs"]
    assert doc.model_dump_json() == before


@modern_only
def test_model_description_annotations_and_meta_are_not_promoted_to_source_text():
    from docling_core.types.doc import DescriptionAnnotation, DescriptionMetaField, PictureMeta
    doc, picture, _, _, first, _ = source_picture()
    picture.annotations = [DescriptionAnnotation(text="Inferred legacy model description.", provenance="test-model")]
    picture.meta = PictureMeta(description=DescriptionMetaField(text="Inferred model metadata.", created_by="test-model"))
    before = doc.model_dump_json()
    markdown, audit = serialize_picture_page(doc, 1)
    assert "Inferred legacy model description." not in markdown
    assert "Inferred model metadata." not in markdown
    assert "first\\_label" in markdown
    assert not audit["pictures"][0]["metadata_description_is_source_text"]
    assert audit["pictures"][0]["retained_metadata_annotation_count"] == 1
    assert doc.model_dump_json() == before


@modern_only
def test_empty_text_and_empty_nested_picture_are_not_reported_as_missing_source():
    doc, picture, _, _, _, _ = source_picture()
    empty = doc.add_text(label=DocItemLabel.TEXT, text="", parent=picture, prov=prov(""))
    nested = doc.add_picture(parent=picture, prov=prov())
    _, audit = serialize_picture_page(doc, 1)
    assert empty.self_ref in audit["all_child_refs"] and nested.self_ref in audit["all_child_refs"]
    assert empty.self_ref not in audit["required_source_child_refs"]
    assert nested.self_ref not in audit["required_source_child_refs"]
    assert audit["missing_child_refs"] == []


@modern_only
def test_source_child_without_page_provenance_is_rejected_explicitly():
    doc, picture, _, _, _, _ = source_picture()
    doc.add_text(label=DocItemLabel.TEXT, text="Orphan source text", parent=picture)
    with pytest.raises(ValueError, match="document_docling_invalid_provenance"):
        serialize_picture_page(doc, 1)


@modern_only
@pytest.mark.parametrize("cross_owner", [False, True])
def test_duplicate_caption_links_are_audited_before_official_span_deduplication(cross_owner):
    doc, picture, caption, _, _, _ = source_picture()
    owner = doc.add_picture(prov=prov()) if cross_owner else picture
    owner.captions.append(caption.get_ref())
    before = doc.model_dump_json()
    markdown, audit = serialize_picture_page(doc, 1)
    # Preserve the official output and original conflicting links for review;
    # unique aggregate spans alone falsely suggest this repeated text is fine.
    assert markdown.count(caption.text) == 2
    assert audit["serialized_span_refs"].count(caption.self_ref) == 1
    assert caption.self_ref in audit["satellite_duplicate_refs"]
    assert caption.self_ref in audit["duplicate_span_refs"]
    events = [event for event in audit["satellite_duplicate_reference_events"]
              if event["item_ref"] == caption.self_ref]
    assert len(events) == 2
    assert len({event["owner_ref"] for event in events}) == (2 if cross_owner else 1)
    assert doc.model_dump_json() == before


@modern_only
def test_distinct_caption_refs_with_equal_source_text_are_preserved_without_conflict():
    doc, _, caption, _, _, _ = source_picture()
    second = doc.add_picture(prov=prov())
    separate = doc.add_text(label=DocItemLabel.CAPTION, text=caption.text, parent=second,
                            prov=prov(caption.text, b=0, t=10))
    second.captions.append(separate.get_ref())
    markdown, audit = serialize_picture_page(doc, 1)
    assert markdown.count(caption.text) == 2
    assert audit["satellite_duplicate_refs"] == audit["duplicate_source_emission_refs"] == []


@modern_only
@pytest.mark.parametrize("cross_owner", [False, True])
def test_footnote_reference_duplicates_across_floating_items_are_not_missed(cross_owner):
    from docling_core.types.doc import TableData
    doc, picture, _, _, _, _ = source_picture()
    footnote = doc.add_text(label=DocItemLabel.FOOTNOTE, text="Original figure footnote.",
                           parent=picture, prov=prov("Original figure footnote.", b=0, t=5))
    picture.footnotes.append(footnote.get_ref())
    owner = doc.add_table(data=TableData(num_rows=0, num_cols=0), prov=prov()) if cross_owner else picture
    owner.footnotes.append(footnote.get_ref())
    before = doc.model_dump_json()
    _, audit = serialize_picture_page(doc, 1)
    assert footnote.self_ref in audit["satellite_duplicate_refs"]
    assert footnote.self_ref in audit["duplicate_span_refs"]
    events = [event for event in audit["satellite_duplicate_reference_events"]
              if event["item_ref"] == footnote.self_ref]
    assert len(events) == 2 and all(event["satellite_role"] == "footnotes" for event in events)
    assert doc.model_dump_json() == before


@modern_only
def test_satellite_used_as_both_caption_and_footnote_requires_review():
    doc, picture, caption, _, _, _ = source_picture()
    picture.footnotes.append(caption.get_ref())
    _, audit = serialize_picture_page(doc, 1)
    assert caption.self_ref in audit["duplicate_span_refs"]
    assert {event["satellite_role"] for event in audit["satellite_duplicate_reference_events"]} == {"captions", "footnotes"}


@modern_only
def test_unrelated_page_satellite_conflict_does_not_quarantine_this_page():
    doc, _, _, _, _, _ = source_picture()
    second = doc.add_picture(prov=prov(page=2))
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="Second page caption.", parent=second,
                           prov=prov("Second page caption.", page=2))
    second.captions = [caption.get_ref(), caption.get_ref()]
    _, first_audit = serialize_picture_page(doc, 1)
    _, second_audit = serialize_picture_page(doc, 2)
    assert first_audit["satellite_duplicate_refs"] == []
    assert second_audit["satellite_duplicate_refs"] == [caption.self_ref]


@modern_only
def test_nested_source_events_count_leaf_emissions_not_parent_aggregate_spans():
    doc, picture, _, _, first, _ = source_picture()
    inner = doc.add_picture(parent=picture, prov=prov())
    child = doc.add_text(label=DocItemLabel.TEXT, text="Nested unique source", parent=inner,
                         prov=prov("Nested unique source"))
    markdown, audit = serialize_picture_page(doc, 1)
    assert markdown.count("Nested unique source") == 1
    assert len([event for event in audit["source_serialization_events"] if event["item_ref"] == child.self_ref]) == 1
    assert len([event for event in audit["source_serialization_events"] if event["item_ref"] == first.self_ref]) == 1
    assert audit["duplicate_source_emission_refs"] == []


@modern_only
def test_inline_math_protection_runs_through_the_real_picture_export_path():
    from lib.infrastructure.docling_inline_markdown import export_inline_markdown
    doc, _, _, _, child, _ = source_picture()
    original = "Given x2, retain the context."
    replacement = "$x_2$"
    start = original.index("x2")
    child.orig = original
    child.text = original[:start] + replacement + original[start + 2:]
    child.prov[0].charspan = (0, len(child.text))
    evidence = {child.self_ref: {
        "kind": "inline_math", "published": True,
        "assembly": {"backend_text": original, "text": child.text, "prose_preserved": True,
                     "patches": [{"charspan": (start, start + 2), "backend_text": "x2", "replacement": replacement}]},
    }}
    before = doc.model_dump_json()
    audit = {}
    markdown = export_inline_markdown(doc, 1, evidence, export_audit=audit)
    assert markdown.count(replacement) == 1 and r"$x\_2$" not in markdown
    assert "DFINLINE" not in markdown
    assert child.self_ref in audit["recovered_refs"]
    assert audit["missing_child_refs"] == []
    assert doc.model_dump_json() == before


@modern_only
def test_pagination_projects_cross_page_figure_child_before_serializing():
    from lib.infrastructure.docling_document import page_markdown
    doc, _, _, _, child, _ = source_picture()
    first, second = "First page source.", "Second page source."
    child.text = child.orig = first + " " + second
    child.prov = [prov(first, 1), prov(second, 2)]
    child.prov[0].charspan = (0, len(first))
    child.prov[1].charspan = (len(first) + 1, len(child.text))
    doc.pages[2] = PageItem(page_no=2, size=Size(width=100, height=130))
    before = doc.model_dump_json()
    one, first_refs = page_markdown(doc, 1)
    two, second_refs = page_markdown(doc, 2)
    assert "> First page source." in one and second not in one
    assert "> Second page source." in two and first not in two
    assert child.self_ref in first_refs and child.self_ref in second_refs
    assert doc.model_dump_json() == before


def test_plain_text_legacy_export_remains_usable_without_pictures():
    doc = DoclingDocument(name="Plain source")
    doc.add_text(label=DocItemLabel.TEXT, text="Unchanged source.", prov=prov("Unchanged source."))
    assert export_picture_markdown(doc, 1) == "Unchanged source."


def test_legacy_runtime_cannot_silently_drop_children(monkeypatch):
    doc, _, _, _, _, _ = source_picture()
    if MODERN:
        fields = dict(MarkdownParams.model_fields)
        fields.pop("traverse_pictures", None)
        monkeypatch.setattr(MarkdownParams, "__pydantic_fields__", fields)
    with pytest.raises(ValueError, match="document_docling_version_mismatch"):
        serialize_picture_page(doc, 1)
