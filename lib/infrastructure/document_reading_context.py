"""Small text-model receipts for durable, potentially image-heavy parser audits.

This is a request projection, never a replacement for the stored evidence.
Only named status/provenance fields cross this boundary; source bodies, images,
OCR alternatives, glyphs and visibility proofs remain in the source cache.
"""
from __future__ import annotations

import json
import re


READING_CONTEXT_POLICY = "bounded_document_reading_status_v1"
_LIMIT = 32
_MAX_SUMMARY_CHARS = 12000
_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,127}\Z")
_REF = re.compile(r"#/[a-z_]{1,32}/[0-9]{1,12}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _code(value):
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def _refs(value):
    previous = _mapping(value)
    value = previous.get("refs", value)
    if not isinstance(value, (list, tuple)):
        return {}
    valid = [ref for ref in value if isinstance(ref, str) and _REF.fullmatch(ref)]
    count = max(_count(previous.get("count"), len(valid)), len(valid))
    return {"refs": valid[:_LIMIT], "count": count,
            "truncated": count > len(valid[:_LIMIT])}


def _count(value, default):
    return value if type(value) is int and 0 <= value <= 2**31 - 1 else default


def _status(value):
    value = _mapping(value)
    result = {}
    for name in ("status", "reason", "kind", "publication_block_reason"):
        if (code := _code(value.get(name))) is not None:
            result[name] = code
    for name in ("review_required", "published", "blank"):
        if type(value.get(name)) is bool:
            result[name] = value[name]
    for name in ("reasons", "review_reasons"):
        if isinstance(value.get(name), (list, tuple)):
            codes = [code for entry in value[name] if (code := _code(entry)) is not None]
            result[name] = codes[:8]
            if len(codes) > 8 or value.get(name + "_truncated") is True:
                result[name + "_truncated"] = True
    return result


def _formula(value):
    value = _mapping(value)
    result = _status(value)
    assembly = _status(value.get("assembly"))
    if assembly:
        result["assembly"] = assembly
    cells = value.get("cells")
    if isinstance(cells, list):
        result["cell_count"] = _count(value.get("cell_count"), len(cells))
        summaries = []
        for cell in cells:
            entry = _status(cell)
            for name in ("row", "col", "column", "row_index", "col_index", "page_no"):
                if _count(_mapping(cell).get(name), None) is not None:
                    entry[name] = cell[name]
            summaries.append(entry)
        summaries.sort(key=lambda item: item.get("status") != "review_required")
        result["cells"] = summaries[:8]
        result["cells_truncated"] = value.get("cells_truncated") is True or len(summaries) > 8
    return result


def document_reading_summary(reading):
    """Return a bounded allowlist with risk locations, without source content."""
    if not isinstance(reading, dict) or not reading:
        return {}
    # Request projections can pass through more than one workflow boundary.
    # Rebuild even our own summary, so a forged policy cannot bypass filtering.
    result = {"context_policy": READING_CONTEXT_POLICY}
    for name in ("mode", "evidence_level", "reading_verification"):
        if (code := _code(reading.get(name))) is not None:
            result[name] = code
    for name in ("source_page", "page_no"):
        if _count(reading.get(name), 0) > 0:
            result[name] = reading[name]
    for name in ("source_pdf_sha256", "source_text_sha256", "parsed_text_sha256", "image_sha256"):
        value = reading.get(name)
        if isinstance(value, str) and _HASH.fullmatch(value):
            result[name] = value
    review = _mapping(reading.get("formula_review"))
    if review:
        result["formula_review"] = _status(review)
        refs = review.get("undecoded_formula_refs", {})
        result["formula_review"]["undecoded_formula_refs"] = _refs(refs)
    elements = reading.get("elements")
    if isinstance(elements, list):
        result["element_count"] = _count(reading.get("element_count"), len(elements))
        formulas = []
        for item in elements:
            item = _mapping(item)
            formula = item.get("local_formula_transcription")
            if formula is None and "undecoded_formula" not in item:
                continue
            entry = {"local_formula_transcription": _formula(formula)}
            ref = item.get("item_ref")
            if isinstance(ref, str) and _REF.fullmatch(ref):
                entry["item_ref"] = ref
            if (label := _code(item.get("label"))) is not None:
                entry["label"] = label
            if "undecoded_formula" in item:
                entry["undecoded_formula"] = True
            formulas.append(entry)
        formulas.sort(key=lambda item: not (item.get("undecoded_formula") or
            item["local_formula_transcription"].get("status") == "review_required"))
        result["elements"] = formulas[:_LIMIT]
        result["formula_element_count"] = _count(reading.get("formula_element_count"), len(formulas))
        result["elements_truncated"] = bool(reading.get("elements_truncated")) or len(formulas) > _LIMIT
    export = _mapping(reading.get("page_export"))
    if export:
        result["page_export"] = {}
        for name in ("reading_order", "picture_export"):
            section = _mapping(export.get(name))
            if section:
                summary = _status(section)
                for key in ("missing_child_refs", "duplicate_span_refs"):
                    if key in section:
                        refs = section[key]
                        summary[key] = _refs(refs)
                if summary:
                    result["page_export"][name] = summary
        if not result["page_export"]:
            del result["page_export"]
    for name in ("picture_structure", "figure_structure", "picture_assets"):
        section = _mapping(reading.get(name))
        if not section:
            continue
        summary = _status(section)
        refs = section.get("review_required_refs")
        if refs is not None:
            summary["review_required_refs"] = _refs(refs)
        items = section.get("items")
        if isinstance(items, list):
            summary["item_count"] = len(items)
            held = [item for raw in items if (item := _mapping(raw)) and (
                item.get("review_required") is True or item.get("status") == "review_required"
                or _mapping(item.get("content_coverage")).get("status") == "review_required")]
            summary["review_required_count"] = len(held)
            if name == "picture_assets":
                summary["review_required_refs"] = _refs([item.get("picture_ref") for item in held])
        for key in ("item_count", "review_required_count"):
            if _count(section.get(key), None) is not None:
                summary[key] = section[key]
        if summary:
            result[name] = summary
    while (result.get("elements") and
           len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > _MAX_SUMMARY_CHARS):
        result["elements"].pop()
        result["elements_truncated"] = True
    # A page can have many independent export risks even with no formulas.
    # Bound those lists too, retaining totals and explicit truncation markers.
    def lists(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, list) and child:
                    yield value, key, child
                elif isinstance(child, dict):
                    yield from lists(child)
    while len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > _MAX_SUMMARY_CHARS:
        candidates = list(lists(result))
        if not candidates:
            break
        parent, key, values = max(candidates, key=lambda item: len(json.dumps(item[2])))
        values.pop()
        parent["truncated" if key == "refs" else key + "_truncated"] = True
    return result


def project_reading_context(value):
    """Project parser receipts in ordinary tasks, including nested QA sources.

    Sealed evidence and authenticated context packs have separate text-only
    builders and identity/citation contracts. They are not editable receipts.
    Explicit visual input is also a separate ``ask(image=...)`` channel.
    """
    if isinstance(value, dict):
        return {name: (document_reading_summary(child)
                      if name in {"document_reading", "source_reading"}
                      else child if name in {"source_evidence", "source_context_pack", "teacher_context_pack"}
                      else project_reading_context(child)) for name, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [project_reading_context(child) for child in value]
    return value
