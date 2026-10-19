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


def test_reused_run_preserves_local_paths_and_ocr_policy(tmp_path):
    config = {"mode": "docling", "artifacts_path": str(tmp_path / "models"), "ocr": True,
              "ocr_models_path": str(tmp_path / "ocr"), "ocr_languages": ["ch_sim", "en"], "formula_enrichment": True}
    values = recipe_to_draft(recipe(document_parser=config), "Reuse", "w", [])["values"]
    validate_creation_draft(values)
    assert values["workflow-document-parse-mode:w"] == "docling"
    assert values["workflow-docling-ocr:w"] is True
    assert values["workflow-docling-formula:w"] is True
    assert values["workflow-docling-languages:w"] == "ch_sim,en"
    assert values["workflow-docling-artifacts:w"] == config["artifacts_path"]


def test_old_run_keeps_formula_disabled(tmp_path):
    config = {"mode": "docling", "artifacts_path": str(tmp_path / "models")}
    values = recipe_to_draft(recipe(document_parser=config), "Reuse", "w", [])["values"]
    assert values["workflow-docling-formula:w"] is False


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
                   "使用本地 CodeFormulaV2 识别公式区域，不调用云端 API。行内公式、符号与复杂排版仍需对照原文核验。",
                   "公式识别未开启：检测到的公式区域可能仅保留未解码标记。",
                   "公式未解码，需对照原 PDF 复核",
                   DOCLING_ERRORS["document_docling_formula_undecoded"],
                   DOCLING_ERRORS["document_docling_page_span_ambiguous"],
                   DOCLING_ERRORS["document_docling_formula_models_required"]):
        assert not re.search(r"[\u4e00-\u9fff]", translate(phrase, "en"))
