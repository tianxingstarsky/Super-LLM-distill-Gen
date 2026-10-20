import pytest
from docling_core.types.doc import DoclingDocument, DocItemLabel, BoundingBox, ProvenanceItem

from lib.infrastructure.docling_inline_markdown import export_inline_markdown


def fixture():
    document = DoclingDocument(name="inline")
    original = "file_name uses ht and ramp_steps."
    text = r"file_name uses $h_t$ and $ramp\_steps$."
    item = document.add_text(
        label=DocItemLabel.TEXT, text=text,
        prov=ProvenanceItem(page_no=1, charspan=(0, len(text)),
                            bbox=BoundingBox(l=10, b=10, r=200, t=30)),
    )
    patches = [
        {"charspan": [15, 17], "backend_text": "ht", "replacement": "$h_t$"},
        {"charspan": [22, 32], "backend_text": "ramp_steps", "replacement": r"$ramp\_steps$"},
    ]
    return document, {item.self_ref: {
        "kind": "inline_math", "published": True,
        "assembly": {"backend_text": original, "text": text, "patches": patches, "prose_preserved": True},
    }}


def test_protects_math_subscripts_without_unescaping_prose_or_literal_underscore():
    document, evidence = fixture()
    original_json = document.model_dump_json()
    markdown = export_inline_markdown(document, 1, evidence)
    assert r"file\_name" in markdown
    assert "$h_t$" in markdown
    assert r"$ramp\_steps$" in markdown
    assert document.model_dump_json() == original_json


def test_stale_patch_proof_is_rejected():
    document, evidence = fixture()
    document.texts[0].text += " changed"
    with pytest.raises(ValueError, match="invalid_output"):
        export_inline_markdown(document, 1, evidence)


@pytest.mark.parametrize("replacement", ["$x_1\n+ x_2$", "$x_1\n\n+ x_2$", "$x\u2028+y$", "$$x$$", "unwrapped"])
def test_invalid_inline_proof_cannot_inject_new_markdown_structure(replacement):
    document, evidence = fixture()
    item = document.texts[0]
    proof = evidence[item.self_ref]
    proof["assembly"]["patches"][0]["replacement"] = replacement
    item.text = proof["assembly"]["text"] = item.text.replace("$h_t$", replacement)
    before = document.model_dump_json()
    with pytest.raises(ValueError, match="document_docling_invalid_output"):
        export_inline_markdown(document, 1, evidence)
    assert document.model_dump_json() == before
