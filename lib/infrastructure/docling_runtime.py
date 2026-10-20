"""Versioned local Docling worker, separate from the console's dependencies."""
from pathlib import Path

MODERN_PACKAGES = {"docling": "2.140.0", "docling-core": "2.102.0", "docling-parse": "7.22.3",
                   "docling-ibm-models": "4.0.3"}
RUNTIME_PROTOCOL = 1
TABLE_ENGINE = "tableformer_v1"
WORKER_ERROR_CODES = frozenset({
    "document_docling_version_mismatch", "document_docling_configuration_changed",
    "document_docling_conversion_incomplete", "document_docling_page_mismatch",
    "document_docling_invalid_provenance", "document_docling_provenance_limit",
    "document_docling_page_span_ambiguous", "document_docling_invalid_output",
    "document_docling_formula_truncated", "document_docling_network_disabled",
    "document_pdf_encrypted", "document_vision_page_limit", "document_pdf_parse_failed",
    "source_snapshot_changed",
})


def runtime_signature(config, *, check_cancel=None):
    from lib.infrastructure.docling_document import _artifact_digest
    from lib.infrastructure.docling_worker_client import run_docling_worker
    from lib.infrastructure.docling_table_geometry import GEOMETRY_POLICY
    from lib.infrastructure.docling_preflight import validate_runtime_assets
    executable = Path(config["runtime_python"])
    if not executable.is_file():
        raise ValueError("document_docling_runtime_unavailable")
    response = run_docling_worker(executable, {"action": "inspect"}, timeout_seconds=30, check_cancel=check_cancel)
    versions = response.get("packages", {})
    if response.get("protocol") != RUNTIME_PROTOCOL or any(versions.get(n) != v for n, v in MODERN_PACKAGES.items()):
        raise ValueError("document_docling_version_mismatch")
    validate_runtime_assets(config, runtime_info=response)
    root = Path(config["artifacts_path"])
    signature = {"mode": "docling", "parser_version": 3, "runtime_protocol": RUNTIME_PROTOCOL,
                 "packages": versions, "pdf_backend": "docling_parse_threaded", "table_mode": TABLE_ENGINE,
                 "table_geometry_policy": GEOMETRY_POLICY,
                 "page_export_policy": "provenance_charspan_v1", "ocr": config["ocr"],
                 "formula_enrichment": config["formula_enrichment"],
                 "layout_sha256": _artifact_digest(root / "docling-project--docling-layout-heron"),
                 "table_sha256": _artifact_digest(root / "docling-project--docling-models/model_artifacts/tableformer/accurate")}
    if config["formula_enrichment"]:
        from lib.infrastructure.docling_formula_crops import FORMULA_DIRECTORY, FORMULA_POLICY
        from lib.infrastructure.docling_math_regions import MATH_REGION_POLICY
        from lib.infrastructure.docling_math_cell_crops import MATH_CELL_CROP_POLICY
        signature.update(formula_model="Pix2Text MFR 1.5", formula_policy=FORMULA_POLICY,
                         formula_crop={"dpi": 240, "padding_points": 2},
                         formula_generation={"max_new_tokens":768, "max_time":60, "use_cache":False},
                         table_math={"selection":MATH_REGION_POLICY, "padding_points":.5,
                                     "crop_policy":MATH_CELL_CROP_POLICY, "numeric_glyph_guard":True},
                         formula_sha256=_artifact_digest(root / FORMULA_DIRECTORY))
    if config["ocr"]:
        signature.update(ocr_engine="easyocr", ocr_languages=config["ocr_languages"],
                         ocr_models_sha256=_artifact_digest(config["ocr_models_path"]))
    return signature


def runtime_parts(path, config, *, expected_signature=None, check_cancel=None):
    from lib.infrastructure.docling_document import _pdf_page_hints, _file_hash, _MAX_PAGE_CHARS, _digest
    from lib.infrastructure.docling_worker_client import run_docling_worker
    if check_cancel:
        check_cancel()
    signature = runtime_signature(config, check_cancel=check_cancel)
    if expected_signature is not None and signature != expected_signature:
        raise ValueError("document_docling_configuration_changed")
    native = _pdf_page_hints(path)
    source_hash = _file_hash(path)
    # Serialize local model loading across console/background processes. Reused
    # source-library entries bypass parsing entirely; new PDFs queue here.
    from filelock import FileLock, Timeout
    lock_path = Path(__file__).resolve().parents[2] / ".dataforge" / "local-parser.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_path)
    while True:
        if check_cancel:
            check_cancel()
        try:
            lock.acquire(timeout=0.1)
            break
        except Timeout:
            continue
    try:
        reply = run_docling_worker(config["runtime_python"], {"action": "parse", "source": str(Path(path).resolve()),
                      "config": config, "signature": signature}, check_cancel=check_cancel)
    finally:
        lock.release()
    if reply.get("error") is not None:
        error = reply["error"]
        raise ValueError(error if isinstance(error, str) and error in WORKER_ERROR_CODES
                         else "document_docling_invalid_output")
    if reply.get("protocol") != RUNTIME_PROTOCOL or reply.get("signature") != signature:
        raise ValueError("document_docling_configuration_changed")
    if reply.get("source_sha256") != source_hash or _file_hash(path) != source_hash:
        raise ValueError("source_snapshot_changed")
    parts = reply.get("parts")
    if not isinstance(parts, list) or len(parts) != len(native):
        raise ValueError("document_docling_page_mismatch")
    for page, part in enumerate(parts, 1):
        if not isinstance(part, dict) or part.get("location") != f"page:{page}":
            raise ValueError("document_docling_page_mismatch")
        text = part.get("text")
        reading = part.get("document_reading", {})
        if (not isinstance(text, str) or len(text) > _MAX_PAGE_CHARS or not isinstance(reading, dict)
                or reading.get("source_page") != page or reading.get("source_pdf_sha256") != source_hash
                or reading.get("parser_signature") != signature or reading.get("parsed_text_sha256") != _digest(text)):
            raise ValueError("document_docling_invalid_output")
    if runtime_signature(config, check_cancel=check_cancel) != signature:
        raise ValueError("document_docling_configuration_changed")
    if check_cancel:
        check_cancel()
    yield from parts
