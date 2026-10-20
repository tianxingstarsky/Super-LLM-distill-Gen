"""Local parser selection survives real Streamlit widget cleanup and drafts."""
from pathlib import Path

from lib.domain.creation_draft import validate_creation_draft
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
from test_workflow_node_settings import _document_ui, DOCUMENT_NAVIGATION_SCRIPT
from test_workflow_reuse import recipe


def test_docling_node_shows_explicit_setup_and_retains_settings_when_hidden(tmp_path):
    ui = _document_ui(DOCUMENT_NAVIGATION_SCRIPT)
    ui.radio(key="workflow-document-parse-mode:demo").set_value("docling").run()
    assert not ui.exception
    assert ui.session_state["fixture-parser"]["mode"] == "docling"
    assert not ui.selectbox  # No cloud model configuration for a local parser.
    path = str(tmp_path / "layout-models")
    ui.text_input(key="workflow-docling-artifacts:demo").set_value(path).run()
    runtime_path = str(tmp_path / "parser" / "python.exe")
    ui.text_input(key="workflow-docling-runtime:demo").set_value(runtime_path).run()
    ui.checkbox(key="workflow-docling-ocr:demo").check().run()
    ui.checkbox(key="workflow-docling-formula:demo").check().run()
    ocr_path = str(tmp_path / "ocr-models")
    ui.text_input(key="workflow-docling-ocr-models:demo").set_value(ocr_path).run()
    ui.text_input(key="workflow-docling-languages:demo").set_value("en,ch_sim").run()
    before = dict(ui.session_state["fixture-parser"])
    ui.radio(key="fixture-node").set_value("sft").run()
    assert "workflow-docling-artifacts:demo" not in ui.session_state
    ui.radio(key="fixture-node").set_value("ingest").run()
    assert not ui.exception
    assert ui.session_state["fixture-parser"] == before
    draft = ui.session_state["workflow-form-draft:demo"]
    validate_creation_draft(draft)
    assert draft["workflow-docling-artifacts:demo"] == path
    assert draft["workflow-docling-formula:demo"] is True
    assert before["formula_enrichment"] is True
    assert before["runtime_python"] == runtime_path
    assert draft["workflow-docling-runtime:demo"] == runtime_path


def test_reused_run_preserves_local_paths_and_ocr_policy(tmp_path):
    config = {"mode": "docling", "artifacts_path": str(tmp_path / "models"), "ocr": True,
              "ocr_models_path": str(tmp_path / "ocr"), "ocr_languages": ["ch_sim", "en"], "formula_enrichment": True,
              "runtime_python": str(tmp_path / "parser" / "python.exe")}
    values = recipe_to_draft(recipe(document_parser=config), "Reuse", "w", [])["values"]
    validate_creation_draft(values)
    assert values["workflow-document-parse-mode:w"] == "docling"
    assert values["workflow-docling-ocr:w"] is True
    assert values["workflow-docling-formula:w"] is True
    assert values["workflow-docling-languages:w"] == "ch_sim,en"
    assert values["workflow-docling-artifacts:w"] == config["artifacts_path"]
    assert values["workflow-docling-runtime:w"] == config["runtime_python"]


def test_old_run_keeps_formula_disabled(tmp_path):
    config = {"mode": "docling", "artifacts_path": str(tmp_path / "models")}
    values = recipe_to_draft(recipe(document_parser=config), "Reuse", "w", [])["values"]
    assert values["workflow-docling-formula:w"] is False
    assert values["workflow-docling-runtime:w"] == ""


def test_formula_setup_fails_locally_before_start(tmp_path, monkeypatch):
    from lib.presentation.streamlit import docling_settings as settings
    monkeypatch.setattr(settings, "_package_versions", lambda ocr: {})
    monkeypatch.setattr(settings, "_formula_package_versions", lambda: {})
    config = {"mode": "docling", "artifacts_path": str(tmp_path), "formula_enrichment": True}
    assert settings.docling_setup_issue(config) == "document_docling_formula_models_required"
    assert settings.docling_setup_issue(config, require_dependencies=False) is None


def test_formula_copy_has_english_translations():
    import re
    from lib.presentation.streamlit.docling_settings import DOCLING_ERRORS
    from lib.presentation.streamlit.i18n import translate
    for phrase in ("启用本地公式识别（LaTeX）",
                   "使用本地公式模型识别公式区域，不调用云端 API。行内公式、符号与复杂排版仍需对照原文核验。",
                   "公式识别未开启：检测到的公式区域可能仅保留未解码标记。",
                   "公式未解码，需对照原 PDF 复核",
                   DOCLING_ERRORS["document_docling_formula_undecoded"],
                   DOCLING_ERRORS["document_docling_page_span_ambiguous"],
                   DOCLING_ERRORS["document_docling_formula_models_required"]):
        assert not re.search(r"[\u4e00-\u9fff]", translate(phrase, "en"))


def _modern_assets(tmp_path, *, formula=True):
    from lib.infrastructure import docling_preflight as preflight
    groups = {"docling-project--docling-layout-heron": preflight._LAYOUT_FILES,
              "docling-project--docling-models/model_artifacts/tableformer/accurate": preflight._TABLE_FILES}
    if formula:
        groups["breezedeus--pix2text-mfr-1.5"] = preflight._FORMULA_FILES
    for directory, names in groups.items():
        root = tmp_path / directory
        root.mkdir(parents=True)
        for name in names:
            (root / name).write_bytes(b"offline fixture")
    return {"mode": "docling", "artifacts_path": str(tmp_path), "formula_enrichment": formula, "ocr": False}


def test_modern_preflight_does_not_hash_weights_or_repeat_inspection(tmp_path, monkeypatch):
    from collections import OrderedDict
    from lib.infrastructure import docling_preflight as preflight, docling_runtime as runtime
    from lib.infrastructure import docling_document as adapter, docling_worker_client as client
    from lib.presentation.streamlit import docling_settings as settings
    config = _modern_assets(tmp_path / "models")
    executable = tmp_path / "runtime/Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"python fixture")
    config["runtime_python"] = str(executable)
    venv = executable.parent.parent
    cfg = venv / "pyvenv.cfg"
    cfg.write_text("fixture", encoding="utf-8")
    site = venv / "Lib/site-packages"
    metadata = site / "docling-2.140.0.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("metadata fixture", encoding="utf-8")
    linked = tmp_path / "shared/site-packages"
    linked_metadata = linked / "torch-fixture.dist-info/METADATA"
    linked_metadata.parent.mkdir(parents=True)
    linked_metadata.write_text("linked metadata", encoding="utf-8")
    calls = []
    def inspect(*args, **kwargs):
        calls.append(args)
        return {"protocol": runtime.RUNTIME_PROTOCOL, "packages": {**runtime.MODERN_PACKAGES, **preflight._FORMULA_PACKAGES},
                "metadata_files": {"docling": str(metadata), "torch": str(linked_metadata)},
                "site_packages": [str(site), str(linked)]}
    monkeypatch.setattr(client, "run_docling_worker", inspect)
    monkeypatch.setattr(preflight, "_INSPECTIONS", OrderedDict())
    def no_hash(*args, **kwargs):
        raise AssertionError("UI must never hash weights")
    monkeypatch.setattr(adapter, "_artifact_digest", no_hash)
    assert settings.docling_setup_issue(config) is None
    assert settings.docling_setup_issue(config) is None
    assert len(calls) == 1
    # Package upgrades, .pth edits, linked distributions and interpreter changes
    # all invalidate the cached read-only inspection.
    for index, path in enumerate((metadata, site / "link.pth", linked_metadata, cfg, executable), 2):
        path.write_text(f"changed fixture {index}", encoding="utf-8")
        assert settings.docling_setup_issue(config) is None
        assert len(calls) == index
    new_metadata = site / "new-package.dist-info/METADATA"
    new_metadata.parent.mkdir()
    new_metadata.write_text("new package", encoding="utf-8")
    assert settings.docling_setup_issue(config) is None and len(calls) == 7
    # Callers cannot mutate the retained result.
    info = preflight.inspect_runtime_cached(executable)
    info["packages"]["docling"] = "changed"
    assert preflight.inspect_runtime_cached(executable)["packages"]["docling"] == runtime.MODERN_PACKAGES["docling"]


def test_modern_assets_require_all_nonempty_files_without_hashing(tmp_path):
    import pytest
    from lib.infrastructure import docling_preflight as preflight
    config = _modern_assets(tmp_path)
    info = {"packages": dict(preflight._FORMULA_PACKAGES)}
    assert preflight.validate_runtime_assets(config, runtime_info=info) is None
    for path in tmp_path.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_bytes()
        path.write_bytes(b"")
        error = ("runtime_formula_models_required" if "pix2text" in str(path) else "runtime_models_required")
        with pytest.raises(ValueError, match=error):
            preflight.validate_runtime_assets(config, runtime_info=info)
        path.write_bytes(content)
    formula = tmp_path / "breezedeus--pix2text-mfr-1.5/decoder_model.onnx"
    formula.unlink()
    config["formula_enrichment"] = False
    assert preflight.validate_runtime_assets(config) is None


def test_formula_preflight_requires_each_pinned_runtime_dependency(tmp_path):
    import pytest
    from lib.infrastructure import docling_preflight as preflight
    config = _modern_assets(tmp_path)
    for package in preflight._FORMULA_PACKAGES:
        for invalid in (None, "0.0.0"):
            info = {"packages": {**preflight._FORMULA_PACKAGES, package: invalid}}
            with pytest.raises(ValueError, match="document_docling_version_mismatch"):
                preflight.validate_runtime_assets(config, runtime_info=info)


def test_ocr_preflight_uses_upstream_language_files_without_importing_models(tmp_path):
    import pytest
    from lib.infrastructure import docling_preflight as preflight
    config = _modern_assets(tmp_path / "models", formula=False)
    site = tmp_path / "runtime/Lib/site-packages"
    package = site / "easyocr"
    package.mkdir(parents=True)
    values = {"detection_models": {"craft": {"filename": "craft_mlt_25k.pth"}},
              "recognition_models": {"gen2": {"english_g2": {"filename": "english_g2.pth"},
                                                "zh_sim_g2": {"filename": "zh_sim_g2.pth"}},
                                     "gen1": {"zh_tra_g1": {"filename": "chinese.pth"}}},
              "latin_lang_list": ["en", "fr"], "other_lang_list": ["ch_sim", "ch_tra"],
              "arabic_lang_list": [], "bengali_lang_list": [], "cyrillic_lang_list": [], "devanagari_lang_list": []}
    source = "\n".join(f"{name} = {value!r}" for name, value in values.items())
    # Upstream character tables contain expressions; availability checks only
    # read model filenames and must never execute unrelated configuration code.
    source = source.replace("'filename': 'english_g2.pth'", "'filename': 'english_g2.pth', 'characters': forbidden_call()")
    (package / "config.py").write_text(source, encoding="utf-8")
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    for name in ("craft_mlt_25k.pth", "english_g2.pth"):
        (ocr / name).write_bytes(b"fixture")
    config.update(ocr=True, ocr_models_path=str(ocr), ocr_languages=["en"])
    info = {"packages": {"easyocr": "1.7.2"},
            "metadata_files": {"easyocr": str(site / "easyocr-1.7.2.dist-info/METADATA")}}
    assert preflight.validate_runtime_assets(config, runtime_info=info) is None
    config["ocr_languages"] = ["ch_sim", "en"]
    with pytest.raises(ValueError, match="ocr_models_required"):
        preflight.validate_runtime_assets(config, runtime_info=info)
    (ocr / "zh_sim_g2.pth").write_bytes(b"fixture")
    assert preflight.validate_runtime_assets(config, runtime_info=info) is None
    for invalid in (["ch_sim", "ch_tra"], ["unknown"]):
        config["ocr_languages"] = invalid
        with pytest.raises(ValueError, match="invalid_document_docling_ocr_languages"):
            preflight.validate_runtime_assets(config, runtime_info=info)
