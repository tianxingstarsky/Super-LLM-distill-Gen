"""Process provenanced text pieces without losing their physical page owners."""
from __future__ import annotations

INLINE_PROVENANCE_POLICY = "native_provenance_parts_atomic_reassembly_v1"


def _spans(item):
    result, end = [], 0
    for prov in item.prov:
        pair = prov.charspan
        if (len(pair) != 2 or any(type(value) is not int for value in pair)
                or not 0 <= pair[0] < pair[1] <= len(item.text)):
            return None
        result.append((pair[0], pair[1], prov))
    result.sort(key=lambda row: row[:2])
    for start, stop, _ in result:
        if start < end or item.text[end:start].strip():
            return None
        end = stop
    return result if not item.text[end:].strip() else None


def _assembly(original, parts):
    patches = []
    for offset, proof in parts:
        for patch in proof.get("assembly", {}).get("patches", []):
            start, stop = patch["charspan"]
            patches.append(dict(patch, charspan=[start + offset, stop + offset]))
    pieces, cursor = [], 0
    for patch in patches:
        start, stop = patch["charspan"]
        if not cursor <= start < stop <= len(original) or original[start:stop] != patch["backend_text"]:
            raise ValueError("document_docling_invalid_output")
        pieces.extend((original[cursor:start], patch["replacement"]))
        cursor = stop
    pieces.append(original[cursor:])
    return {"status": "transcribed_unverified", "policy": INLINE_PROVENANCE_POLICY,
            "backend_text": original, "text": "".join(pieces),
            "patches": patches, "prose_preserved": True, "review_reasons": []}


def enrich_provenance_parts(document, source, config, reader, routes, enrich):
    plans, expanded = {}, []
    for item in document.texts:
        label = getattr(item.label, "value", item.label)
        spans = _spans(item) if len(getattr(item, "prov", [])) > 1 and label in {
            "text", "list_item", "caption", "footnote"
        } else None
        if not spans:
            expanded.append(item)
            continue
        parts = []
        for ordinal, (start, stop, prov) in enumerate(spans):
            ref = f"{item.self_ref}:source:{ordinal}"
            text = item.text[start:stop]
            part = item.model_copy(update={
                "self_ref": ref, "text": text, "orig": text,
                "prov": [prov.model_copy(update={"charspan": (0, len(text))})],
            })
            expanded.append(part)
            parts.append((start, stop, prov, part))
        plans[item.self_ref] = (item, parts)
    if not plans:
        return enrich(document, source, config, reader, routes=routes)
    view = document.model_copy(update={"texts": expanded})
    evidence = enrich(view, source, config, reader, routes=routes)
    current = {item.self_ref: item for item in view.texts}
    final = []
    for item in document.texts:
        if item.self_ref not in plans:
            final.append(current[item.self_ref])
            continue
        original, parts = plans[item.self_ref]
        children = []
        for start, stop, prov, part in parts:
            proof = evidence.pop(part.self_ref, None)
            if proof is not None:
                children.append((start, stop, prov, part, proof))
        if not children:
            final.append(original)
            continue
        review = any(proof["status"] == "review_required" for *_, proof in children)
        proof = {"kind": "inline_math", "policy": INLINE_PROVENANCE_POLICY,
                 "backend_text": original.text, "source_parts": [
                     {"source_charspan": [start, stop], "page_no": prov.page_no, "evidence": child}
                     for start, stop, prov, _, child in children
                 ], "published": False,
                 "status": "review_required" if review else "transcribed_unverified",
                 "fragments": [], "continuation_inferred": False}
        evidence[item.self_ref] = proof
        if review:
            final.append(original)
            continue
        assembly = _assembly(original.text, [(start, child) for start, _, _, _, child in children])
        if not assembly["patches"]:
            final.append(original)
            continue
        provenance, delta = [], 0
        for start, stop, prov, part in parts:
            text = current[part.self_ref].text
            provenance.append(prov.model_copy(update={"charspan": (start + delta, start + delta + len(text))}))
            delta += len(text) - (stop - start)
        pages = {}
        for number in sorted({prov.page_no for _, _, prov, _ in parts}):
            if all(prov.page_no == number for _, _, prov, _ in parts):
                pages[str(number)] = assembly
                continue
            page_parts = [(start, stop, prov, part) for start, stop, prov, part in parts if prov.page_no == number]
            original_page = " ".join(original.text[start:stop] for start, stop, _, _ in page_parts)
            offsets, offset = [], 0
            child_by_ref = {part.self_ref: child for _, _, _, part, child in children}
            for start, stop, prov, part in page_parts:
                offsets.append((offset, child_by_ref.get(part.self_ref, {})))
                offset += stop - start + 1
            pages[str(number)] = _assembly(original_page, offsets)
        proof.update(assembly=assembly, page_assemblies=pages, published=True)
        final.append(original.model_copy(update={"text": assembly["text"], "prov": provenance}))
    document.texts = final
    return evidence
