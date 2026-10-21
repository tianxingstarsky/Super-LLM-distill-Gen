"""Opt-in, local-only PDF layout parsing through the pinned Docling API.

No default converter is constructed: its automatic model downloads are not an
acceptable side effect of importing a document. Users provision model files
first, then explicitly select this backend. TeX and text retain their parsers.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
from collections import OrderedDict
import re
import threading

from lib.domain.document_parser import validate_document_parser


DOCLING_VERSION = "2.60.1"
DOCLING_CORE_VERSION = "2.48.4"
DOCLING_ADAPTER_VERSION = 2
PAGE_EXPORT_POLICY = "provenance_page_order_figure_structure_v3"
CODE_FORMULA_DIRECTORY = "ds4sd--CodeFormulaV2"
_FORMULA_REQUIRED_FILES = (
    "config.json", "generation_config.json", "model.safetensors", "preprocessor_config.json",
    "processor_config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "chat_template.json",
)
_MAX_ARTIFACT_FILES = 10000
_MAX_PAGE_CHARS = 120000
_ARTIFACT_HASHES = OrderedDict()
_ARTIFACT_HASH_LOCK = threading.Lock()
_MAX_CACHED_ARTIFACTS = 1024
_CONVERTERS = OrderedDict()
_CONVERTERS_LOCK = threading.Lock()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _package_versions(ocr):
    packages = ["docling", "docling-core", "docling-ibm-models", "docling-parse", "pypdfium2", "torch"]
    if ocr:
        packages.append("easyocr")
    try:
        versions = {name: importlib.metadata.version(name) for name in packages}
    except importlib.metadata.PackageNotFoundError as error:
        raise ValueError("document_docling_unavailable") from error
    if versions["docling"] != DOCLING_VERSION or versions["docling-core"] != DOCLING_CORE_VERSION:
        raise ValueError("document_docling_version_mismatch")
    return versions


def _artifact_digest(path):
    path = Path(path)
    if not path.is_dir():
        raise ValueError("document_docling_local_models_required")
    records = []
    for item in path.rglob("*"):
        if item.is_file():
            if len(records) >= _MAX_ARTIFACT_FILES:
                raise ValueError("document_docling_artifact_limit")
            # Weights are large. Within a worker, hash each unchanged local file
            # once; later PDFs inspect file metadata instead of rereading GBs.
            # A new process always hashes again. This is a change detector, not
            # a defense against an actor forging filesystem metadata in-place.
            before = item.stat()
            key = (str(item.resolve()), before.st_size, before.st_mtime_ns,
                   before.st_ctime_ns, before.st_ino)
            with _ARTIFACT_HASH_LOCK:
                digest = _ARTIFACT_HASHES.get(key)
                if digest is not None:
                    _ARTIFACT_HASHES.move_to_end(key)
            if digest is None:
                digest = _file_hash(item)
                after = item.stat()
                if (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino) != key[1:]:
                    raise ValueError("document_docling_configuration_changed")
                with _ARTIFACT_HASH_LOCK:
                    _ARTIFACT_HASHES[key] = digest
                    while len(_ARTIFACT_HASHES) > _MAX_CACHED_ARTIFACTS:
                        _ARTIFACT_HASHES.popitem(last=False)
            records.append((item.relative_to(path).as_posix(), digest))
    if not records:
        raise ValueError("document_docling_local_models_required")
    return _digest(sorted(records))


def formula_models_path(config):
    """Require the pinned model's complete local inference assets before loading.

Docling 2.60.1 hard-codes this legacy folder name even though the upstream
repository has moved to docling-project/CodeFormulaV2. Never pass a hub ID.
"""
    path = Path(config["artifacts_path"]) / CODE_FORMULA_DIRECTORY
    try:
        if any(not (path / name).is_file() or (path / name).stat().st_size == 0
               for name in _FORMULA_REQUIRED_FILES):
            raise ValueError("document_docling_formula_models_required")
    except OSError as error:
        raise ValueError("document_docling_formula_models_required") from error
    return path


def _formula_package_versions():
    try:
        versions = {name: importlib.metadata.version(name) for name in ("transformers", "accelerate")}
    except importlib.metadata.PackageNotFoundError as error:
        raise ValueError("document_docling_unavailable") from error
    if not versions["transformers"].startswith("4.") or not versions["accelerate"].startswith("1."):
        raise ValueError("document_docling_version_mismatch")
    return versions


def docling_parse_signature(config):
    """Fingerprint implementation, local weights and OCR policy for cache reuse."""
    config = validate_document_parser(config)
    if config["mode"] != "docling":
        raise ValueError("invalid_document_parser")
    if config.get("runtime_python"):
        from lib.infrastructure.docling_runtime import runtime_signature
        return runtime_signature(config)
    versions = _package_versions(config["ocr"])
    artifacts = Path(config["artifacts_path"])
    # These are the explicit layout/table models selected below. Requiring the
    # documented local layout prevents falling back to a hub download.
    layout = artifacts / "ds4sd--docling-layout-heron"
    table = artifacts / "ds4sd--docling-models" / "model_artifacts" / "tableformer" / "accurate"
    from lib.infrastructure.docling_reading_order import READING_ORDER_POLICY
    from lib.infrastructure.docling_picture_export import PICTURE_EXPORT_POLICY
    from lib.infrastructure.docling_picture_assets import PICTURE_ASSET_POLICY
    signature = {"mode": "docling", "parser_version": DOCLING_ADAPTER_VERSION,
                 "packages": versions, "pdf_backend": "pypdfium2", "layout_model": "docling_layout_heron",
                 "page_export_policy": PAGE_EXPORT_POLICY,
                 "reading_order_policy": READING_ORDER_POLICY,
                 "picture_export_policy": PICTURE_EXPORT_POLICY,
                 "picture_asset_policy": PICTURE_ASSET_POLICY,
                 "picture_structure_policy": "legacy_runtime_source_structure_unchanged",
                 "caption_placement": "layout",
                 "table_mode": "accurate", "ocr": config["ocr"],
                 "formula_enrichment": config["formula_enrichment"],
                 "layout_sha256": _artifact_digest(layout), "table_sha256": _artifact_digest(table)}
    if config["formula_enrichment"]:
        signature.update(formula_model="CodeFormulaV2", formula_packages=_formula_package_versions(),
                         formula_sha256=_artifact_digest(formula_models_path(config)))
    if config["ocr"]:
        signature.update(ocr_engine="easyocr", ocr_languages=config["ocr_languages"],
                         ocr_models_sha256=_artifact_digest(config["ocr_models_path"]))
    return signature


def _make_converter(config):
    """The only third-party constructor; kept lazy for the base installation."""
    config = validate_document_parser(config)
    if config["formula_enrichment"]:
        formula_models_path(config)
    try:
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as error:
        raise ValueError("document_docling_unavailable") from error
    options = PdfPipelineOptions(
        artifacts_path=Path(config["artifacts_path"]), do_ocr=config["ocr"],
        do_table_structure=True, do_code_enrichment=False,
        do_formula_enrichment=config["formula_enrichment"],
        do_picture_description=False, do_picture_classification=False,
        enable_remote_services=False, allow_external_plugins=False,
        generate_page_images=False, generate_picture_images=False,
    )
    if config["ocr"]:
        options.ocr_options = EasyOcrOptions(
            lang=config["ocr_languages"], model_storage_directory=config["ocr_models_path"],
            download_enabled=False, use_gpu=False,
        )
    # Explicit on every platform. The default docling-parse backend cannot
    # locate its glyph assets under this Windows Unicode installation path.
    # This changes PDF text/raster loading only; Docling's layout, table and
    # optional formula models remain the configured pipeline. No silent retry
    # with a different backend is permitted, including on conversion failure.
    return DocumentConverter(allowed_formats=[InputFormat.PDF],
                             format_options={InputFormat.PDF: PdfFormatOption(
                                 backend=PyPdfiumDocumentBackend, pipeline_options=options)})


def _converter(config, signature):
    """Reuse expensive initialized models; serialize use of the shared converter."""
    key = _digest([config, signature])
    with _CONVERTERS_LOCK:
        if key not in _CONVERTERS:
            _CONVERTERS[key] = (_make_converter(config), threading.Lock())
            # One configured parser is normally used by this worker. Keep at
            # most two instances for alternating local OCR configurations.
            while len(_CONVERTERS) > 2:
                _CONVERTERS.popitem(last=False)
        _CONVERTERS.move_to_end(key)
        return _CONVERTERS[key]


def _pdf_page_hints(path):
    """Bounded structural preflight, without extracting text or decoding images.

This deliberately does not make the existing text extractor a prerequisite for
Docling: bad font encodings are one reason to use a layout/OCR backend. Text
operators are only a conservative hint. They never substitute for conversion.
"""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    from lib.infrastructure.document_vision import MAX_PAGES
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("document_pdf_encrypted")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("document_vision_page_limit")
        hints = []
        for page in reader.pages:
            try:
                contents = page.get_contents()
                raw = contents.get_data() if contents is not None else b""
                has_content = bool(raw.strip())
                has_text = any(re.search(rb"(?:\bTj\b|\bTJ\b|[\x27\x22])", group)
                               for group in re.findall(rb"\bBT\b(.*?)\bET\b", raw, re.DOTALL))
            except Exception:
                # Conversion can recover a damaged stream. Without OCR, an
                # ambiguous page cannot silently be declared complete text.
                has_content, has_text = True, False
            hints.append({"has_content": has_content, "requires_vision": has_content and not has_text})
        return hints
    except PyPdfError as error:
        raise ValueError("document_pdf_parse_failed") from error


def _page_provenance(document, page_numbers):
    """Keep original element references and bounding boxes with each page."""
    result = {number: [] for number in page_numbers}
    undecoded_chars = {number: 0 for number in page_numbers}
    picture_refs = {picture.self_ref for picture in getattr(document, "pictures", [])}
    count = 0
    for item, _ in document.iterate_items(traverse_pictures=True):
        for prov in getattr(item, "prov", []):
            number = getattr(prov, "page_no", None)
            if type(number) is not int or number not in result:
                raise ValueError("document_docling_invalid_provenance")
            count += 1
            if count > 200000:
                raise ValueError("document_docling_provenance_limit")
            record = prov.model_dump(mode="json")
            label = getattr(item, "label", None)
            label = getattr(label, "value", label)
            element = {"item_ref": getattr(item, "self_ref", None), "label": label, "provenance": record}
            parent = getattr(item, "parent", None)
            if parent is not None:
                element["parent_ref"] = parent.cref
                seen = set()
                while parent is not None and parent.cref not in seen:
                    seen.add(parent.cref)
                    if parent.cref in picture_refs:
                        element.update(picture_ref=parent.cref, source_role="picture_child")
                        break
                    parent = getattr(parent.resolve(document), "parent", None)
            decoded = getattr(item, "text", "") or ""
            if label == "formula" and not isinstance(decoded, str):
                raise ValueError("document_docling_invalid_output")
            if label == "formula" and not decoded.strip():
                original = getattr(item, "orig", "") or ""
                if not isinstance(original, str) or not isinstance(decoded, str):
                    raise ValueError("document_docling_invalid_output")
                undecoded_chars[number] += len(original) + len(decoded)
                if undecoded_chars[number] > _MAX_PAGE_CHARS:
                    raise ValueError("document_docling_invalid_output")
                # Preserve, never reconstruct: the PDF text may itself be
                # garbled. It is evidence for review, not a substitute formula.
                element["undecoded_formula"] = {"original_text": original, "decoded_text": decoded}
            result[number].append(element)
    return result


def page_markdown(document, page_number, *, inline_evidence=None, export_audit=None, picture_structure=None):
    """Project cross-page text into a page view without changing the source.

The pinned Markdown serializer filters by an item's first provenance page and
does not slice its text. Docling can merge paragraphs across page boundaries.
Keep the original item indices/references and graph, replacing only those text
items in a shallow document view with their provenanced page fragments.
"""
    texts = getattr(document, "texts", [])
    text_indices = {getattr(item, "self_ref", None): index for index, item in enumerate(texts)
                    if getattr(item, "self_ref", None) is not None}
    replacements = {}
    split_refs = []
    for item, _ in document.iterate_items(traverse_pictures=True):
        provenance = getattr(item, "prov", [])
        pages = {entry.page_no for entry in provenance}
        if len(pages) <= 1 or page_number not in pages:
            continue
        ref = getattr(item, "self_ref", None)
        label = getattr(getattr(item, "label", None), "value", getattr(item, "label", None))
        text = getattr(item, "text", None)
        # Enriched formulas/codes do not necessarily retain the character
        # offsets of the original OCR. Non-text structures need their own
        # pagination policy; never pretend the whole item belongs to one page.
        if ref not in text_indices or label in {"formula", "code"} or not isinstance(text, str):
            raise ValueError("document_docling_page_span_ambiguous")
        spans = []
        for prov in provenance:
            span = getattr(prov, "charspan", None)
            if (not isinstance(span, (list, tuple)) or len(span) != 2
                    or any(type(value) is not int for value in span)
                    or not 0 <= span[0] < span[1] <= len(text)):
                raise ValueError("document_docling_page_span_ambiguous")
            spans.append((span[0], span[1], prov))
        spans.sort(key=lambda entry: entry[:2])
        end = 0
        for start, stop, _ in spans:
            if start < end or text[end:start].strip():
                raise ValueError("document_docling_page_span_ambiguous")
            end = stop
        if text[end:].strip():
            raise ValueError("document_docling_page_span_ambiguous")
        fragments, local_provenance = [], []
        offset = 0
        for start, stop, prov in spans:
            if prov.page_no != page_number:
                continue
            if fragments:
                offset += 1
            fragment = text[start:stop]
            local_provenance.append(prov.model_copy(update={"charspan": (offset, offset + len(fragment))}))
            fragments.append(fragment)
            offset += len(fragment)
        page_text = " ".join(fragments)
        updates = {"text": page_text, "prov": local_provenance}
        if getattr(item, "orig", None) == text:
            updates["orig"] = page_text
        replacements[text_indices[ref]] = item.model_copy(update=updates)
        split_refs.append(ref)
    page_document = document
    if replacements:
        page_document = document.model_copy(update={"texts": [replacements.get(index, item)
                                                             for index, item in enumerate(texts)]})
    from lib.infrastructure.docling_reading_order import project_page_reading_order
    page_document, order_audit = project_page_reading_order(page_document, page_number)
    picture_audit = {}
    if inline_evidence:
        from lib.infrastructure.docling_inline_markdown import export_inline_markdown
        markdown = export_inline_markdown(
            page_document, page_number, inline_evidence, export_audit=picture_audit,
            picture_structure=picture_structure, source_document=document)
    else:
        from lib.infrastructure.docling_picture_export import serialize_picture_page
        markdown, picture_audit = serialize_picture_page(page_document, page_number,
                                                          picture_structure=picture_structure, source_document=document)
    if export_audit is not None:
        export_audit.update(policy=PAGE_EXPORT_POLICY, split_item_refs=split_refs,
                            reading_order=order_audit, picture_export=picture_audit)
    return markdown, split_refs


def docling_parts(path, config, *, expected_signature=None, check_cancel=None):
    """Yield whole original PDF pages; reject partial conversions and empty OCR."""
    path = Path(path)
    config = validate_document_parser(config)
    if path.suffix.lower() != ".pdf":
        raise ValueError("document_docling_pdf_required")
    if config.get("runtime_python"):
        from lib.infrastructure.docling_runtime import runtime_parts
        yield from runtime_parts(path, config, expected_signature=expected_signature, check_cancel=check_cancel)
        return
    signature = docling_parse_signature(config)
    if expected_signature is not None and signature != expected_signature:
        raise ValueError("document_docling_configuration_changed")
    native = _pdf_page_hints(path)
    page_numbers = list(range(1, len(native) + 1))
    if not page_numbers:
        raise ValueError("document_docling_empty_result")
    if check_cancel:
        check_cancel()
    source_sha256 = _file_hash(path)
    try:
        converter, conversion_lock = _converter(config, signature)
    except ValueError as error:
        if str(error).startswith("document_docling_"):
            raise
        raise ValueError("document_docling_conversion_failed") from error
    except Exception as error:
        raise ValueError("document_docling_conversion_failed") from error
    # Waiting for another PDF must not swallow cancellation or block it until
    # that PDF finishes. A running local converter completes before cancellation
    # is checked again; its native work cannot safely be interrupted here.
    while not conversion_lock.acquire(timeout=0.25):
        if check_cancel:
            check_cancel()
    try:
        if check_cancel:
            check_cancel()
        try:
            result = converter.convert(path, raises_on_error=True)
        except Exception as error:
            raise ValueError("document_docling_conversion_failed") from error
        from lib.infrastructure.docling_picture_assets import extract_picture_assets
        picture_assets = (extract_picture_assets(result.document, path, source_sha256=source_sha256)
                          if getattr(result.status, "value", result.status) == "success" else {})
    finally:
        conversion_lock.release()
    if check_cancel:
        check_cancel()
    status = getattr(result.status, "value", result.status)
    if status != "success":
        raise ValueError("document_docling_conversion_incomplete")
    document = result.document
    if set(document.pages) != set(page_numbers):
        raise ValueError("document_docling_page_mismatch")
    provenance = _page_provenance(document, page_numbers)
    # Materialize the bounded page payload before publishing anything, so one
    # bad later page cannot leave a partially successful conversion in a cache.
    parts = []
    for number, original in zip(page_numbers, native):
        if check_cancel:
            check_cancel()
        export_audit = {}
        text, split_refs = page_markdown(document, number, export_audit=export_audit)
        if not isinstance(text, str) or len(text) > _MAX_PAGE_CHARS:
            raise ValueError("document_docling_invalid_output")
        reading = {"mode": "docling", "parser_signature": signature,
                   "source_pdf_sha256": source_sha256, "source_page": number,
                   "page_export": export_audit,
                   "elements": provenance[number], "parsed_text_sha256": _digest(text),
                   "evidence_level": "local_layout_ocr_extraction"}
        if number in picture_assets:
            reading["picture_assets"] = picture_assets[number]
        undecoded_refs = list(dict.fromkeys(item["item_ref"] for item in provenance[number]
                                           if "undecoded_formula" in item))
        has_empty_formula = any("undecoded_formula" in item for item in provenance[number])
        markers = text.count("<!-- formula-not-decoded -->")
        if has_empty_formula or markers:
            reading["formula_review"] = {"status": "required", "undecoded_markers": markers,
                                         "undecoded_formula_refs": undecoded_refs}
        part = {"location": f"page:{number}", "text": text, "document_reading": reading}
        if original.get("requires_vision") and not config["ocr"]:
            part.update(status="quarantined", reason="document_text_requires_vision", requires_vision=True)
        elif export_audit["reading_order"].get("review_required"):
            part.update(status="quarantined", reason="document_docling_reading_order_requires_review")
        elif (export_audit["picture_export"].get("missing_child_refs")
              or export_audit["picture_export"].get("duplicate_span_refs")):
            part.update(status="quarantined", reason="document_docling_picture_export_requires_review")
        elif not text.strip():
            if original["has_content"]:
                part.update(status="quarantined", reason="document_docling_page_empty")
            elif has_empty_formula:
                part.update(status="quarantined", reason="document_docling_formula_undecoded")
            else:
                part.update(status="skipped", reason="empty_document_page")
        elif has_empty_formula or markers:
            part.update(status="quarantined", reason="document_docling_formula_undecoded")
        parts.append(part)
    if _file_hash(path) != source_sha256:
        raise ValueError("source_snapshot_changed")
    if docling_parse_signature(config) != signature:
        raise ValueError("document_docling_configuration_changed")
    yield from parts
