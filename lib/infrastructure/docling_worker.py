"""Offline Docling subprocess entrypoint. Run with a separately pinned Python."""
from pathlib import Path
import argparse
import importlib.metadata
import json
import os
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
for key, value in {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                   "DO_NOT_TRACK": "1", "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4",
                   "TOKENIZERS_PARALLELISM": "false"}.items():
    os.environ[key] = value


def _deny_network(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.sendto"}:
        raise RuntimeError("document_docling_network_disabled")


def inspect_runtime():
    from lib.infrastructure.docling_runtime import MODERN_PACKAGES, RUNTIME_PROTOCOL
    names = list(MODERN_PACKAGES) + ["pydantic", "transformers", "torch", "pypdfium2", "easyocr",
                                  "onnxruntime", "optimum", "optimum-onnx", "pylatexenc", "pypdf", "fonttools",
                                  "camelot-py"]
    versions = {}
    metadata_files = {}
    for name in names:
        try:
            distribution = importlib.metadata.distribution(name)
            versions[name] = distribution.version
            metadata_files[name] = str(distribution._path / "METADATA")
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"protocol": RUNTIME_PROTOCOL, "packages": versions, "metadata_files": metadata_files,
            "site_packages": [str(Path(entry).resolve()) for entry in sys.path
                              if Path(entry).name in ("site-packages", "dist-packages")]}


def parse_document(request):
    from io import BytesIO
    from lib.domain.document_parser import validate_document_parser
    from lib.infrastructure.docling_document import _pdf_page_hints, _file_hash, _page_provenance, page_markdown, _digest, _MAX_PAGE_CHARS
    from lib.infrastructure.docling_runtime import MODERN_PACKAGES, RUNTIME_PROTOCOL
    config = validate_document_parser(request["config"])
    versions = inspect_runtime()["packages"]
    if any(versions.get(n) != v for n, v in MODERN_PACKAGES.items()) or versions != request["signature"]["packages"]:
        raise ValueError("document_docling_version_mismatch")
    source = Path(request["source"])
    hints = _pdf_page_hints(source)
    source_hash = _file_hash(source)
    from docling.datamodel.base_models import InputFormat, DocumentStream
    from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    options = PdfPipelineOptions(artifacts_path=Path(config["artifacts_path"]), do_ocr=config["ocr"],
        do_formula_enrichment=False, do_code_enrichment=False, do_picture_description=False,
        do_picture_classification=False, enable_remote_services=False, allow_external_plugins=False,
        accelerator_options=AcceleratorOptions(device="cpu", num_threads=4))
    if config["ocr"]:
        options.ocr_options = EasyOcrOptions(lang=config["ocr_languages"],
            model_storage_directory=config["ocr_models_path"], download_enabled=False, use_gpu=False)
    from lib.infrastructure.docling_source_pipeline import source_preserving_pipeline
    converter = DocumentConverter(allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options,
                                                       pipeline_cls=source_preserving_pipeline())})
    result = converter.convert(DocumentStream(name=source.name, stream=BytesIO(source.read_bytes())), raises_on_error=True)
    if getattr(result.status, "value", result.status) != "success":
        raise ValueError("document_docling_conversion_incomplete")
    document = result.document
    numbers = list(range(1, len(hints)+1))
    if set(document.pages) != set(numbers):
        raise ValueError("document_docling_page_mismatch")
    from docling_core.types.doc import DoclingDocument
    from lib.infrastructure.docling_table_geometry import extract_pdf_rulings, refine_document_table_spans
    table_pages = {prov.page_no for table in document.tables for prov in table.prov}
    refined, table_audit = refine_document_table_spans(
        document.model_dump(mode="json"), extract_pdf_rulings(source, table_pages))
    document = DoclingDocument.model_validate(refined)
    from lib.infrastructure.docling_table_recovery import recover_flat_tables
    table_audit.extend(recover_flat_tables(document, source))
    formula_evidence = {}
    if config["formula_enrichment"]:
        from lib.infrastructure.docling_formula_crops import enrich_formulas
        formula_evidence = enrich_formulas(
            document, source, config["artifacts_path"],
            inline_config=config if config.get("inline_runtime_python") else None,
            preserved_flat_table_refs={entry["table_ref"] for entry in table_audit
                                       if entry.get("status") == "recovered"
                                       and entry.get("source_sha256") == source_hash})
    from lib.infrastructure.docling_native_typography import restore_native_typography
    typography_audit = restore_native_typography(document, source, formula_evidence)
    from lib.infrastructure.docling_picture_assets import extract_picture_assets
    from lib.infrastructure.docling_picture_structure import build_picture_structure
    try:
        picture_structure, picture_structure_audit = build_picture_structure(document, source)
    except ValueError as error:
        if str(error) == "document_pdfium_visibility_api_unsupported":
            raise ValueError("document_docling_version_mismatch") from error
        raise
    picture_assets = extract_picture_assets(document, source, source_sha256=source_hash,
                                            picture_structure=picture_structure)
    provenance = _page_provenance(document, numbers)
    parts = []
    for number, native in zip(numbers, hints):
        export_audit = {}
        text, split_refs = page_markdown(
            document, number, inline_evidence=formula_evidence, export_audit=export_audit,
            picture_structure=picture_structure, preserve_source_order=True)
        if len(text) > _MAX_PAGE_CHARS:
            raise ValueError("document_docling_invalid_output")
        elements = provenance[number]
        for element in elements:
            if element["item_ref"] in formula_evidence:
                element["local_formula_transcription"] = formula_evidence[element["item_ref"]]
            changes = [entry for entry in table_audit if entry.get("table_ref") == element["item_ref"]]
            if changes:
                element["table_geometry_review"] = changes
        reading = {"mode": "docling", "parser_signature": request["signature"], "source_pdf_sha256": source_hash,
                   "source_page": number, "page_export": export_audit,
                   "elements": elements, "parsed_text_sha256": _digest(text), "evidence_level": "local_layout_ocr_extraction"}
        if typography_audit:
            reading["native_typography"] = [entry for entry in typography_audit if entry["page_no"] == number]
        if number in picture_assets:
            reading["picture_assets"] = picture_assets[number]
        page_structure_items = [item for item in picture_structure_audit["items"]
                                if any(prov["page_no"] == number for prov in item["source_provenance"])]
        if page_structure_items:
            reading["picture_structure"] = {
                "policy": picture_structure_audit["policy"], "source_sha256": source_hash,
                "page_no": number, "source_document_modified": False, "inference_performed": False,
                "items": page_structure_items,
                "physical_token_rows": [row for row in picture_structure_audit["physical_token_rows"]
                                        if row["page_no"] == number],
                "spatial_title_associations": [entry for entry in picture_structure_audit["spatial_title_associations"]
                                                if entry["page_no"] == number],
                "spatial_title_decisions": [entry for entry in picture_structure_audit["spatial_title_decisions"]
                                             if entry.get("page_no") == number],
                "review_required_refs": [item["item_ref"] for item in page_structure_items
                                         if item.get("review_required")],
            }
        part = {"location": f"page:{number}", "text": text, "document_reading": reading}
        undecoded = [e["item_ref"] for e in elements if "undecoded_formula" in e
                     or e.get("local_formula_transcription",{}).get("status") == "review_required"]
        if native["requires_vision"] and not config["ocr"]:
            part.update(status="quarantined", reason="document_text_requires_vision", requires_vision=True)
        elif undecoded or "<!-- formula-not-decoded -->" in text:
            reading["formula_review"] = {"status": "required", "undecoded_formula_refs": undecoded}
            part.update(status="quarantined", reason="document_docling_formula_undecoded")
        elif export_audit["reading_order"].get("review_required"):
            part.update(status="quarantined", reason="document_docling_reading_order_requires_review")
        elif (export_audit["picture_export"].get("missing_child_refs")
              or export_audit["picture_export"].get("duplicate_span_refs")):
            part.update(status="quarantined", reason="document_docling_picture_export_requires_review")
        elif not text.strip():
            part.update(status="quarantined" if native["has_content"] else "skipped",
                        reason="document_docling_page_empty" if native["has_content"] else "empty_document_page")
        parts.append(part)
    if source_hash != _file_hash(source):
        raise ValueError("source_snapshot_changed")
    source_block_audit = [pipeline.reading_order_model.ro_model.source_audit
                          for pipeline in converter.initialized_pipelines.values()]
    return {"protocol": RUNTIME_PROTOCOL, "signature": request["signature"], "source_sha256": source_hash,
            "parts": parts, "document": document.model_dump(mode="json"), "network_disabled": True,
            "table_geometry_audit": table_audit, "picture_structure_audit": picture_structure_audit,
            "source_block_audit": source_block_audit, "native_typography_audit": typography_audit}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()
    sys.addaudithook(_deny_network)
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if request.get("service") == "inline_layout":
        from lib.infrastructure.docling_inline_worker import run_inline_layout
        response = run_inline_layout(request)
    elif request.get("action") == "inspect":
        response = inspect_runtime()
    elif request.get("action") == "parse":
        from lib.infrastructure.docling_runtime import WORKER_ERROR_CODES, RUNTIME_PROTOCOL
        try:
            response = parse_document(request)
        except (ValueError, RuntimeError) as error:
            if str(error) not in WORKER_ERROR_CODES:
                raise
            response = {"protocol": RUNTIME_PROTOCOL, "error": str(error)}
    else:
        raise ValueError("document_docling_invalid_request")
    output = Path(args.response)
    partial = output.with_suffix(".partial")
    partial.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    partial.replace(output)


if __name__ == "__main__":
    main()
