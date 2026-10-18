"""Offline contracts for optional local layout parsing; never fetch weights."""
from copy import deepcopy
import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from lib.domain.document_parser import validate_document_parser
from lib.infrastructure import docling_document as adapter
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.source_library import SourceLibrary, native_parse_signature
from test_document_model_parser import text_pdf, configure, PAGE_ONE, PAGE_TWO


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    artifacts = tmp_path / "models"
    for folder in (artifacts / "ds4sd--docling-layout-heron",
                   artifacts / "ds4sd--docling-models/model_artifacts/tableformer/accurate"):
        folder.mkdir(parents=True)
        (folder / "weights.bin").write_bytes(b"offline-fixture-weights")
    monkeypatch.setattr(adapter, "_package_versions", lambda ocr: {
        "docling": adapter.DOCLING_VERSION, "docling-core": adapter.DOCLING_CORE_VERSION,
        "docling-ibm-models": "fixture", "easyocr": "fixture"} if ocr else {
        "docling": adapter.DOCLING_VERSION, "docling-core": adapter.DOCLING_CORE_VERSION,
        "docling-ibm-models": "fixture"})
    return {"mode": "docling", "artifacts_path": str(artifacts), "ocr": False}


class Provenance:
    def __init__(self, page):
        self.page_no = page

    def model_dump(self, **kwargs):
        assert kwargs == {"mode": "json"}
        return {"page_no": self.page_no, "bbox": {"l": 10, "t": 20, "r": 100, "b": 60},
                "charspan": [0, 80]}


class Document:
    def __init__(self, texts):
        self.texts = texts
        self.pages = {number: {} for number in range(1, len(texts) + 1)}

    def iterate_items(self):
        for page in self.pages:
            yield SimpleNamespace(prov=[Provenance(page)], self_ref=f"#/texts/{page - 1}",
                                  label=SimpleNamespace(value="text")), 0

    def export_to_markdown(self, *, page_no, image_placeholder):
        assert image_placeholder == ""
        return self.texts[page_no - 1]


def conversion(monkeypatch, texts=(PAGE_ONE, PAGE_TWO), *, status="success"):
    adapter._CONVERTERS.clear()
    calls = []
    doc = Document(list(texts))
    class Converter:
        def convert(self, path, *, raises_on_error):
            assert raises_on_error is True
            calls.append(path)
            return SimpleNamespace(status=SimpleNamespace(value=status), document=doc)
    monkeypatch.setattr(adapter, "_make_converter", lambda config: Converter())
    return calls, doc


def test_explicit_local_config_and_defaults(local_config):
    assert validate_document_parser(None) == {"mode": "native"}
    assert validate_document_parser({**local_config, "ocr": False}) == local_config
    for value in ({"mode": "docling"}, {"mode": "docling", "artifacts_path": "https://models.example"},
                  {**local_config, "ocr": "true"}, {**local_config, "binding": {}},
                  {**local_config, "ocr": True}, {**local_config, "artifacts_path": "relative"}):
        with pytest.raises(ValueError):
            validate_document_parser(value)


def test_missing_package_and_version_fail_explicitly(tmp_path, monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(importlib.metadata, "version", missing)
    with pytest.raises(ValueError, match="document_docling_unavailable"):
        adapter.docling_parse_signature({"mode": "docling", "artifacts_path": str(tmp_path)})
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.0")
    with pytest.raises(ValueError, match="document_docling_version_mismatch"):
        adapter.docling_parse_signature({"mode": "docling", "artifacts_path": str(tmp_path)})


def test_real_local_import_is_lazy_and_no_dependency_is_required_for_native():
    # This assertion is valid both on base and Docling installations. The module
    # only accesses distribution metadata; native parsing never constructs it.
    assert "docling.document_converter" not in sys.modules


def test_conversion_retains_original_pages_tables_and_provenance(tmp_path, local_config, monkeypatch):
    source = text_pdf(tmp_path)
    table = "| Item | Limit |\n| --- | --- |\n| Power | 10 W |"
    calls, _ = conversion(monkeypatch, (PAGE_ONE, table))
    signature = adapter.docling_parse_signature(local_config)
    parts = list(adapter.docling_parts(source, local_config, expected_signature=signature))
    assert calls == [source]
    assert [part["location"] for part in parts] == ["page:1", "page:2"]
    assert parts[1]["text"] == table
    reading = parts[1]["document_reading"]
    assert reading["source_page"] == 2 and reading["elements"][0]["item_ref"] == "#/texts/1"
    assert reading["elements"][0]["provenance"]["bbox"]["r"] == 100
    assert reading["source_pdf_sha256"] == engine.file_hash(source)


@pytest.mark.parametrize("status", ["failure", "partial_success", "skipped"])
def test_partial_conversion_cannot_be_reused_as_success(tmp_path, local_config, monkeypatch, status):
    conversion(monkeypatch, status=status)
    with pytest.raises(ValueError, match="document_docling_conversion_incomplete"):
        list(adapter.docling_parts(text_pdf(tmp_path), local_config))


def test_page_mismatch_and_empty_reading_fail_explicitly(tmp_path, local_config, monkeypatch):
    source = text_pdf(tmp_path)
    conversion(monkeypatch, (PAGE_ONE,))
    with pytest.raises(ValueError, match="document_docling_page_mismatch"):
        list(adapter.docling_parts(source, local_config))
    conversion(monkeypatch, (PAGE_ONE, ""))
    parts = list(adapter.docling_parts(source, local_config))
    assert parts[1]["status"] == "quarantined"
    assert parts[1]["reason"] == "document_docling_page_empty"


def test_scanned_page_without_ocr_is_not_published_as_usable(tmp_path, local_config, monkeypatch):
    from PIL import Image
    source = tmp_path / "scanned.pdf"
    Image.new("RGB", (20, 20), "gray").save(source, "PDF")
    conversion(monkeypatch, ("A misleading fragment",))
    part, = adapter.docling_parts(source, local_config)
    assert part["status"] == "quarantined" and part["requires_vision"] is True


def test_cache_key_pins_backend_version_and_local_weights(tmp_path, local_config, monkeypatch):
    signature = adapter.docling_parse_signature(local_config)
    source = {"id": "1" * 64}
    key = SourceLibrary.parse_id(source, signature)
    assert key != SourceLibrary.parse_id(source, native_parse_signature())
    monkeypatch.setattr(adapter, "DOCLING_ADAPTER_VERSION", 2)
    assert key != SourceLibrary.parse_id(source, adapter.docling_parse_signature(local_config))
    monkeypatch.setattr(adapter, "DOCLING_ADAPTER_VERSION", 1)
    weights = Path(local_config["artifacts_path"]) / "ds4sd--docling-layout-heron/weights.bin"
    weights.write_bytes(b"updated-model")
    assert key != SourceLibrary.parse_id(source, adapter.docling_parse_signature(local_config))
    with pytest.raises(ValueError, match="document_docling_configuration_changed"):
        list(adapter.docling_parts(text_pdf(tmp_path), local_config, expected_signature=signature))


def test_weights_are_hashed_once_per_unchanged_worker_snapshot(local_config, monkeypatch):
    original = adapter._file_hash
    calls = []
    def tracked(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(adapter, "_file_hash", tracked)
    adapter.docling_parse_signature(local_config)
    assert len(calls) == 2
    adapter.docling_parse_signature(local_config)
    assert len(calls) == 2
    changed = Path(local_config["artifacts_path"]) / "ds4sd--docling-layout-heron/weights.bin"
    changed.write_bytes(b"changed-length-and-content")
    adapter.docling_parse_signature(local_config)
    assert len(calls) == 3


def test_pdf_is_parsed_once_across_runs_and_metadata_survives(tmp_path, local_config, monkeypatch):
    configure(tmp_path)
    source = text_pdf(tmp_path)
    calls, _ = conversion(monkeypatch)
    output = tmp_path / "output"
    states = []
    for name in ("first", "next"):
        run_id = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                   document_parser=local_config, name=name)
        run = engine.Workflow(output, run_id, tmp_path)
        assert run.execute()["status"] == "completed"
        states.append(run.state)
        rows = engine.read_json(run.path / "artifacts/cpt.records.json")
        assert [row["source_location"]["record"] for row in rows] == ["page:1", "page:2"]
        assert rows[1]["document_reading"]["source_page"] == 2
        engine.verify_artifacts(run.path)
    assert len(calls) == 1
    assert states[1]["source_library"]["hits"] == 1


def test_converter_is_reused_across_distinct_pdfs_and_rebuilt_for_changed_signature(tmp_path, local_config, monkeypatch):
    calls, _ = conversion(monkeypatch)
    build = adapter._make_converter
    constructions = []
    def tracked(config):
        constructions.append(deepcopy(config))
        return build(config)
    monkeypatch.setattr(adapter, "_make_converter", tracked)
    first = text_pdf(tmp_path, name="first.pdf")
    second = text_pdf(tmp_path, name="second.pdf")
    list(adapter.docling_parts(first, local_config))
    list(adapter.docling_parts(second, local_config))
    assert len(constructions) == 1 and calls == [first, second]
    weights = Path(local_config["artifacts_path"]) / "ds4sd--docling-layout-heron/weights.bin"
    weights.write_bytes(b"updated-local-layout")
    list(adapter.docling_parts(second, local_config))
    assert len(constructions) == 2


def test_docling_does_not_require_native_text_or_image_decoding(tmp_path, local_config, monkeypatch):
    from pypdf._page import PageObject
    def broken(*args, **kwargs):
        raise AssertionError("Native text/image extraction must not precede Docling")
    monkeypatch.setattr(PageObject, "extract_text", broken)
    monkeypatch.setattr(PageObject, "images", property(broken))
    conversion(monkeypatch)
    parts = list(adapter.docling_parts(text_pdf(tmp_path), local_config))
    assert len(parts) == 2
    assert all(part.get("status") != "quarantined" for part in parts)


def test_cancellation_is_preserved_and_releases_converter_lock(tmp_path, local_config, monkeypatch):
    calls, _ = conversion(monkeypatch)
    class Cancelled(RuntimeError):
        pass
    checks = []
    def cancelled():
        checks.append(True)
        if len(checks) == 2:
            raise Cancelled("stopped")
    with pytest.raises(Cancelled, match="stopped"):
        list(adapter.docling_parts(text_pdf(tmp_path), local_config, check_cancel=cancelled))
    assert calls == []
    _, lock = adapter._converter(local_config, adapter.docling_parse_signature(local_config))
    assert lock.acquire(blocking=False)
    lock.release()


def test_changed_weights_cannot_silently_reuse_existing_run(tmp_path, local_config, monkeypatch):
    configure(tmp_path)
    run_id = engine.create_run(tmp_path / "output", sources=[text_pdf(tmp_path)], targets=["cpt"],
                               settings_root=tmp_path, document_parser=local_config)
    weights = Path(local_config["artifacts_path"]) / "ds4sd--docling-layout-heron/weights.bin"
    weights.write_bytes(b"new-weights")
    run = engine.Workflow(tmp_path / "output", run_id, tmp_path)
    run.stage = "ingest"
    with pytest.raises(ValueError, match="document_docling_configuration_changed"):
        list(run.iter_source_units(run.recipe["sources"][0]))


def test_text_and_tex_paths_do_not_import_or_require_docling(tmp_path, monkeypatch):
    configure(tmp_path)
    config = {"mode": "docling", "artifacts_path": str(tmp_path / "not-installed")}
    def forbidden(*args, **kwargs):
        raise AssertionError("Plain text and TeX must retain the native parser")
    monkeypatch.setattr(adapter, "docling_parse_signature", forbidden)
    sources = []
    for name, text in (("readme.md", PAGE_ONE), ("theory.tex", r"\section{Power} Power is measured in watts. $P=VI$.")):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        sources.append(path)
    run_id = engine.create_run(tmp_path / "output", sources=sources, targets=["cpt"],
                               settings_root=tmp_path, document_parser=config)
    run = engine.Workflow(tmp_path / "output", run_id, tmp_path)
    assert run.execute()["status"] == "completed"
    rows = engine.read_json(run.path / "artifacts/cpt.records.json")
    assert any("P=VI" in row["text"] for row in rows)


def test_constructor_disables_remote_plugins_and_automatic_ocr_downloads(local_config, tmp_path, monkeypatch):
    capture = {}
    class Options:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    class Converter:
        def __init__(self, **kwargs):
            capture.update(kwargs)
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", SimpleNamespace(
        InputFormat=SimpleNamespace(PDF="pdf")))
    monkeypatch.setitem(sys.modules, "docling.datamodel.pipeline_options", SimpleNamespace(
        PdfPipelineOptions=Options, EasyOcrOptions=Options))
    monkeypatch.setitem(sys.modules, "docling.document_converter", SimpleNamespace(
        DocumentConverter=Converter, PdfFormatOption=Options))
    config = {**local_config, "ocr": True, "ocr_models_path": str(tmp_path), "ocr_languages": ["ch_sim", "en"]}
    adapter._make_converter(config)
    options = capture["format_options"]["pdf"].pipeline_options
    assert capture["allowed_formats"] == ["pdf"]
    assert options.enable_remote_services is False and options.allow_external_plugins is False
    assert options.do_formula_enrichment is False and options.do_picture_description is False
    assert options.artifacts_path == Path(local_config["artifacts_path"])
    assert options.ocr_options.download_enabled is False
    assert options.ocr_options.model_storage_directory == str(tmp_path)


def test_incomplete_source_never_publishes_reusable_parse(tmp_path, local_config, monkeypatch):
    configure(tmp_path)
    conversion(monkeypatch, (PAGE_ONE, ""))
    run_id = engine.create_run(tmp_path / "output", sources=[text_pdf(tmp_path)], targets=["cpt"],
                               settings_root=tmp_path, document_parser=local_config)
    run = engine.Workflow(tmp_path / "output", run_id, tmp_path)
    run.stage = "ingest"
    units = list(run.iter_source_units(run.recipe["sources"][0]))
    assert any(row.get("reason") == "document_docling_page_empty" for row in units)
    assert not list((tmp_path / "output/.source-library/parses").rglob("parse.json"))
