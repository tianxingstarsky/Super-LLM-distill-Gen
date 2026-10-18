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


def test_reused_run_preserves_local_paths_and_ocr_policy(tmp_path):
    config = {"mode": "docling", "artifacts_path": str(tmp_path / "models"), "ocr": True,
              "ocr_models_path": str(tmp_path / "ocr"), "ocr_languages": ["ch_sim", "en"]}
    values = recipe_to_draft(recipe(document_parser=config), "Reuse", "w", [])["values"]
    validate_creation_draft(values)
    assert values["workflow-document-parse-mode:w"] == "docling"
    assert values["workflow-docling-ocr:w"] is True
    assert values["workflow-docling-languages:w"] == "ch_sim,en"
    assert values["workflow-docling-artifacts:w"] == config["artifacts_path"]
