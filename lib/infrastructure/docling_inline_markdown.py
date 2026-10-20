"""Protect only proven inline-math insertions through Docling's text serializer."""
import hashlib


INLINE_MARKDOWN_POLICY = "proven_inline_spans_protected_export_v1"


def export_inline_markdown(document, page_number, evidence):
    protected, replacements = {}, {}
    all_text = "\n".join(item.text for item in document.texts)
    for index, item in enumerate(document.texts):
        proof = evidence.get(item.self_ref, {})
        if proof.get("kind") != "inline_math" or not proof.get("published"):
            continue
        if not item.prov or any(prov.page_no != page_number for prov in item.prov):
            continue
        assembly = proof.get("page_assemblies", {}).get(str(page_number), proof["assembly"])
        if item.text != assembly["text"] or not assembly.get("prose_preserved"):
            raise ValueError("document_docling_invalid_output")
        original, cursor, pieces, expected = assembly["backend_text"], 0, [], []
        for ordinal, patch in enumerate(assembly["patches"]):
            start, stop = patch["charspan"]
            if not cursor <= start < stop <= len(original) or original[start:stop] != patch["backend_text"]:
                raise ValueError("document_docling_invalid_output")
            raw_math = patch["replacement"]
            digest = hashlib.sha256(f"{item.self_ref}:{ordinal}:{raw_math}".encode()).hexdigest()
            marker = f"DFINLINE{digest}END"
            if marker in all_text or marker in protected:
                raise ValueError("document_docling_invalid_output")
            pieces.extend((original[cursor:start], marker))
            expected.extend((original[cursor:start], raw_math))
            protected[marker] = raw_math
            cursor = stop
        pieces.append(original[cursor:])
        expected.append(original[cursor:])
        if "".join(expected) != item.text:
            raise ValueError("document_docling_invalid_output")
        replacements[index] = item.model_copy(update={"text": "".join(pieces)})
    view = document.model_copy(update={"texts": [
        replacements.get(index, item) for index, item in enumerate(document.texts)
    ]}) if replacements else document
    markdown = view.export_to_markdown(page_no=page_number, image_placeholder="")
    for marker, math in protected.items():
        if markdown.count(marker) != 1:
            raise ValueError("document_docling_invalid_output")
        markdown = markdown.replace(marker, math)
    return markdown
