"""Protect only proven inline-math insertions through Docling's text serializer."""
import hashlib


INLINE_MARKDOWN_POLICY = "proven_inline_spans_scoped_export_v2"


def export_inline_markdown(document, page_number, evidence, *, export_audit=None):
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
            from .docling_inline_assembly import _inline_latex
            # Match the producer's single-line inline-math contract. Inserting
            # arbitrary newlines after a figure has been quoted could escape
            # its scope or start a Markdown list outside the picture.
            if (not isinstance(raw_math, str) or not raw_math.startswith("$")
                    or not raw_math.endswith("$") or not _inline_latex(raw_math[1:-1])):
                raise ValueError("document_docling_invalid_output")
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
    from .docling_picture_export import serialize_picture_page
    markdown, picture_audit = serialize_picture_page(view, page_number)
    for marker, math in protected.items():
        if markdown.count(marker) != 1:
            raise ValueError("document_docling_invalid_output")
        markdown = markdown.replace(marker, math)
    if export_audit is not None:
        # The serializer saw protected marker text; record the final export hash
        # after restoring proven math, without changing its source span refs.
        export_audit.update(picture_audit)
        export_audit["markdown_sha256"] = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    return markdown
