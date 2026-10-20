from copy import deepcopy

from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem

from lib.infrastructure.docling_document import page_markdown
from lib.infrastructure.docling_inline_provenance import enrich_provenance_parts


def document(second_page=2, gap=" "):
    doc = DoclingDocument(name="provenanced math")
    first, second = "First x1.", "Then y2."
    text = first + gap + second
    item = doc.add_text(label=DocItemLabel.TEXT, text=text)
    item.prov = [
        ProvenanceItem(page_no=1, charspan=(0, len(first)), bbox=BoundingBox(l=10, b=10, r=200, t=50)),
        ProvenanceItem(page_no=second_page, charspan=(len(first) + len(gap), len(text)),
                       bbox=BoundingBox(l=10, b=60, r=200, t=100)),
    ]
    return doc


def fake_enrich(doc, source, config, reader, *, routes):
    result = {}
    for item in doc.texts:
        original = item.text
        target = "x1" if "x1" in original else "y2"
        start = original.index(target)
        replacement = "$" + target[0] + "_" + target[1] + "$"
        text = original[:start] + replacement + original[start + 2:]
        item.text = text
        item.prov[0] = item.prov[0].model_copy(update={"charspan": (0, len(text))})
        result[item.self_ref] = {
            "kind": "inline_math", "published": True, "status": "transcribed_unverified",
            "assembly": {"backend_text": original, "text": text, "prose_preserved": True,
                         "patches": [{"charspan": [start, start + 2], "backend_text": target,
                                      "replacement": replacement}]},
        }
    return result


def test_cross_page_fragments_keep_source_pages_and_protected_math():
    doc = document()
    proof = enrich_provenance_parts(doc, None, {}, None, {}, fake_enrich)
    item = doc.texts[0]
    assert item.text == "First $x_1$. Then $y_2$."
    assert [p.page_no for p in item.prov] == [1, 2]
    assert item.text[slice(*item.prov[0].charspan)] == "First $x_1$."
    assert item.text[slice(*item.prov[1].charspan)] == "Then $y_2$."
    first, _ = page_markdown(doc, 1, inline_evidence=proof)
    second, _ = page_markdown(doc, 2, inline_evidence=proof)
    assert "$x_1$" in first and "y_2" not in first
    assert "$y_2$" in second and "x_1" not in second


def test_same_page_disjoint_boxes_preserve_original_whitespace():
    doc = document(second_page=1, gap="  \n ")
    proof = enrich_provenance_parts(doc, None, {}, None, {}, fake_enrich)
    assert doc.texts[0].text == "First $x_1$.  \n Then $y_2$."
    output, _ = page_markdown(doc, 1, inline_evidence=proof)
    assert "$x_1$" in output and "$y_2$" in output


def test_one_failed_piece_prevents_partial_parent_publication():
    doc = document()
    before = doc.model_dump(mode="python")
    def fail_second(*args, **kwargs):
        proof = fake_enrich(*args, **kwargs)
        last = next(reversed(proof))
        proof[last].update(status="review_required", published=False)
        return proof
    proof = enrich_provenance_parts(doc, None, {}, None, {}, fail_second)
    assert doc.model_dump(mode="python") == before
    assert proof[doc.texts[0].self_ref]["status"] == "review_required"
    assert not proof[doc.texts[0].self_ref]["published"]


def test_overlapping_source_spans_are_not_split_or_invented():
    doc = document()
    doc.texts[0].prov[1].charspan = (1, len(doc.texts[0].text))
    def inspect(original, *args, **kwargs):
        assert original is doc
        assert len(original.texts) == 1
        return {}
    enrich_provenance_parts(doc, None, {}, None, {}, inspect)
