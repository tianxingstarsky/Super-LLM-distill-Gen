"""CPT models, cleaning prompts and source review stay on their node."""
from streamlit.testing.v1 import AppTest

from lib.domain.workflow_node_prompts import builtin_node_prompt
from tests.test_workflow_draft import SCRIPT


def cpt_ui():
    ui = AppTest.from_string(SCRIPT).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cpt"]).run()
    ui.button(key="fixture-node:cpt").click().run()
    assert not ui.exception
    return ui


def test_cpt_default_has_independent_models_and_prompts_survive_local_mode():
    ui = cpt_ui()
    assert ui.radio(key="workflow-cpt-processing-mode:fixture").value == "model"
    assert ui.radio(key="workflow-cpt-review-mode:fixture").value == "text"
    assert ui.selectbox(key="node-model:fixture:cpt:generation:backend").value == "local"
    assert ui.selectbox(key="node-model:fixture:cpt:jev:backend").value == "local"
    key = "workflow-node-prompt:fixture:cpt:workflow.cpt_clean"
    instruction = "Keep formulas and table relationships unchanged. Return the required cleaning fields."
    ui.text_area(key=key).set_value(instruction).run()
    ui.radio(key="workflow-cpt-processing-mode:fixture").set_value("native").run()
    assert not any(row.key.startswith("node-model:fixture:cpt:") for row in ui.selectbox)
    assert not any(row.key.startswith("workflow-node-prompt:fixture:cpt:") for row in ui.text_area)
    ui.radio(key="workflow-cpt-processing-mode:fixture").set_value("model").run()
    assert ui.text_area(key=key).value == instruction
    ui.button(key="workflow-node-prompt-reset:fixture:cpt:workflow.cpt_clean").click().run()
    assert ui.text_area(key=key).value == builtin_node_prompt("workflow.cpt_clean")
    assert not ui.exception


def test_visual_review_requires_explicit_review_model_capability_without_losing_settings():
    ui = cpt_ui()
    ui.radio(key="workflow-cpt-review-mode:fixture").set_value("vision").run()
    assert any(box.label == "我已核实评审模型支持图片输入" for box in ui.checkbox)
    assert any("评审模型尚未确认图片输入能力" in warning.value for warning in ui.warning)
    assert ui.button(key="cpt-vision-save:fixture").disabled
    ui.button(key="fixture-node:ingest").click().run()
    ui.button(key="fixture-node:cpt").click().run()
    assert ui.radio(key="workflow-cpt-review-mode:fixture").value == "vision"
    assert ui.session_state["workflow-form-draft:fixture"]["workflow-cpt-review-mode:fixture"] == "vision"
    assert not ui.exception
