"""CPT source checks stay on CPT; terminal review belongs to generated dialogues."""

import pytest
from streamlit.testing.v1 import AppTest

from tests.test_workflow_draft import SCRIPT
from tests.test_workflow_arithmetic_ui import _script as no_models_script


TARGET_KEY = "workflow-targets:fixture:自动推荐"
REVIEW_PROMPT = "workflow-node-prompt:fixture:review:workflow.review_score"
JEV_KEY = "workflow-package-review-enabled:fixture"


def _native_cpt(tmp_path, *, review_mode="auto", cleaning_mode="native", english=False):
    source = tmp_path / "source.txt"
    source.write_text("Check the cable before connecting the equipment.", encoding="utf-8")
    code = no_models_script(source)
    if english:
        code = code.replace("import streamlit as st", "import streamlit as st\n"
            "from lib.presentation.streamlit.i18n import install_streamlit_localization\n"
            "install_streamlit_localization()\n"
            "st.session_state['ui_language'] = 'en'", 1)
    ui = AppTest.from_string(code)
    ui.session_state[TARGET_KEY] = ["cpt"]
    ui.session_state["workflow-setup-node:fixture"] = "cpt"
    ui.session_state["workflow-document-parse-mode-draft:fixture"] = "native"
    ui.session_state["workflow-form-draft:fixture"] = {
        "workflow-document-parse-mode:fixture": "native",
        "workflow-cpt-processing-mode:fixture": cleaning_mode,
        "workflow-sources:fixture:文档资料": [str(source)],
        "workflow-review-repair-mode:fixture": review_mode,
        JEV_KEY: True,
        REVIEW_PROMPT: "",  # An incomplete inactive review draft must not block CPT.
    }
    return ui


@pytest.mark.parametrize("review_mode", ["auto", "human"])
def test_cpt_native_submit_needs_no_terminal_model_and_excludes_stale_review_draft(tmp_path, review_mode):
    ui = _native_cpt(tmp_path, review_mode=review_mode).run()
    assert not ui.exception
    assert {node["id"] for node in ui.session_state["fixture-canvas"]["nodes"]} == {
        "ingest", "cpt", "package"}
    assert not any(button.key.startswith("node-model-confirm:") for button in ui.button)
    assert not any(button.key == "fixture-node:review" for button in ui.button)
    assert not ui.button(key="workflow-create:fixture").disabled
    assert not any("全部候选进行 AI 评审" in item.value for item in ui.caption)
    ui.button(key="workflow-create:fixture").click().run()
    assert not ui.exception
    submitted = ui.session_state["fixture-submitted"]
    assert submitted["targets"] == ["cpt"]
    assert submitted["node_models"] == {}
    assert submitted["review_repair"] is None
    assert submitted["package_review"]["enabled"] is False
    assert submitted["cpt_processing"] == {"mode": "native"}
    assert not {"review", "jev", "package"}.intersection(submitted["node_prompts"])
    assert ui.session_state["workflow-form-draft:fixture"][JEV_KEY] is True


def test_cpt_model_cleaning_still_requires_its_cleaner_and_source_checker(tmp_path):
    ui = _native_cpt(tmp_path, cleaning_mode="model").run()
    assert not ui.exception
    assert ui.button(key="workflow-create:fixture").disabled
    cpt = next(node for node in ui.session_state["fixture-canvas"]["nodes"] if node["id"] == "cpt")
    assert "生成模型" in cpt["subtitle"] and "核对模型" in cpt["subtitle"]
    assert not any(button.key == "fixture-node:review" for button in ui.button)
    ui.radio(key="workflow-cpt-processing-mode:fixture").set_value("native").run()
    assert not ui.exception
    assert not ui.button(key="workflow-create:fixture").disabled


def test_switching_to_pure_cpt_preserves_review_settings_for_other_outputs():
    ui = AppTest.from_string(SCRIPT).run()
    ui.button(key="fixture-node:review").click().run()
    ui.toggle(key=JEV_KEY).set_value(True).run()
    ui.text_area(key=REVIEW_PROMPT).set_value("Check every claim against its source.").run()
    ui.pills(key=TARGET_KEY).set_value(["cpt"]).run()
    assert not ui.exception
    assert not any(button.key == "fixture-node:review" for button in ui.button)
    assert not any(widget.key == JEV_KEY for widget in ui.toggle)
    ui.pills(key=TARGET_KEY).set_value(["cpt", "sft"]).run()
    ui.button(key="fixture-node:review").click().run()
    assert not ui.exception
    assert ui.toggle(key=JEV_KEY).value is True
    assert ui.text_area(key=REVIEW_PROMPT).value == "Check every claim against its source."


def test_cpt_bypass_explanation_is_localized(tmp_path):
    ui = _native_cpt(tmp_path, english=True).run()
    assert not ui.exception
    assert any("CPT goes straight to packaging" in item.value for item in ui.caption)
    assert any("Basic checks and your selected cleaning checks still apply" in item.value for item in ui.caption)
    assert not any("CPT 清洗" in item.value for item in ui.caption)
