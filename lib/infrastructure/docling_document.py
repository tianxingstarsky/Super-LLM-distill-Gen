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
DOCLING_ADAPTER_VERSION = 1
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


def docling_parse_signature(config):
    """Fingerprint implementation, local weights and OCR policy for cache reuse."""
    config = validate_document_parser(config)
    if config["mode"] != "docling":
        raise ValueError("invalid_document_parser")
    versions = _package_versions(config["ocr"])
    artifacts = Path(config["artifacts_path"])
    # These are the explicit layout/table models selected below. Requiring the
    # documented local layout prevents falling back to a hub download.
    layout = artifacts / "ds4sd--docling-layout-heron"
    table = artifacts / "ds4sd--docling-models" / "model_artifacts" / "tableformer" / "accurate"
    signature = {"mode": "docling", "parser_version": DOCLING_ADAPTER_VERSION,
                 "packages": versions, "layout_model": "docling_layout_heron",
                 "table_mode": "accurate", "ocr": config["ocr"],
                 "layout_sha256": _artifact_digest(layout), "table_sha256": _artifact_digest(table)}
    if config["ocr"]:
        signature.update(ocr_engine="easyocr", ocr_languages=config["ocr_languages"],
                         ocr_models_sha256=_artifact_digest(config["ocr_models_path"]))
    return signature


def _make_converter(config):
    """The only third-party constructor; kept lazy for the base installation."""
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as error:
        raise ValueError("document_docling_unavailable") from error
    options = PdfPipelineOptions(
        artifacts_path=Path(config["artifacts_path"]), do_ocr=config["ocr"],
        do_table_structure=True, do_code_enrichment=False, do_formula_enrichment=False,
        do_picture_description=False, do_picture_classification=False,
        enable_remote_services=False, allow_external_plugins=False,
        generate_page_images=False, generate_picture_images=False,
    )
    if config["ocr"]:
        options.ocr_options = EasyOcrOptions(
            lang=config["ocr_languages"], model_storage_directory=config["ocr_models_path"],
            download_enabled=False, use_gpu=False,
        )
    return DocumentConverter(allowed_formats=[InputFormat.PDF],
                             format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


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
    count = 0
    for item, _ in document.iterate_items():
        for prov in getattr(item, "prov", []):
            number = getattr(prov, "page_no", None)
            if type(number) is not int or number not in result:
                raise ValueError("document_docling_invalid_provenance")
            count += 1
            if count > 200000:
                raise ValueError("document_docling_provenance_limit")
            record = prov.model_dump(mode="json")
            label = getattr(item, "label", None)
            result[number].append({"item_ref": getattr(item, "self_ref", None),
                                   "label": getattr(label, "value", label), "provenance": record})
    return result


def docling_parts(path, config, *, expected_signature=None, check_cancel=None):
    """Yield whole original PDF pages; reject partial conversions and empty OCR."""
    path = Path(path)
    config = validate_document_parser(config)
    if path.suffix.lower() != ".pdf":
        raise ValueError("document_docling_pdf_required")
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
        text = document.export_to_markdown(page_no=number, image_placeholder="")
        if not isinstance(text, str) or len(text) > _MAX_PAGE_CHARS:
            raise ValueError("document_docling_invalid_output")
        reading = {"mode": "docling", "parser_signature": signature,
                   "source_pdf_sha256": source_sha256, "source_page": number,
                   "elements": provenance[number], "parsed_text_sha256": _digest(text),
                   "evidence_level": "local_layout_ocr_extraction"}
        part = {"location": f"page:{number}", "text": text, "document_reading": reading}
        if original.get("requires_vision") and not config["ocr"]:
            part.update(status="quarantined", reason="document_text_requires_vision", requires_vision=True)
        elif not text.strip():
            if original["has_content"]:
                part.update(status="quarantined", reason="document_docling_page_empty")
            else:
                part.update(status="skipped", reason="empty_document_page")
        parts.append(part)
    if _file_hash(path) != source_sha256:
        raise ValueError("source_snapshot_changed")
    if docling_parse_signature(config) != signature:
        raise ValueError("document_docling_configuration_changed")
    yield from parts
