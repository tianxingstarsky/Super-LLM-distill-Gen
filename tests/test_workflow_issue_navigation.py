"""Configuration warnings open their real node and field group without calls."""
import re

import pytest
from streamlit.testing.v1 import AppTest

from tests.test_workflow_draft import SCRIPT


def _assert_open(ui, node, tab):
    assert not ui.exception
    assert ui.session_state["workflow-setup-node:fixture"] == node
    assert ui.session_state["canvas-open:setup-canvas:fixture"] is True
    if node != "agent":
        assert ui.session_state[f"workflow-node-tabs:fixture:{node}"] == tab


@pytest.mark.parametrize("node", ["sft", "cot"])
def test_style_warning_opens_matching_node_prompt_tab_and_preserves_other_drafts(node):
    ui = AppTest.from_string(SCRIPT).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["sft", "cot"]).run()
    ui.text_input(key="workflow-name:fixture").set_value("Keep this run name").run()
    ui.button(key=f"fixture-node:{node}").click().run()
    ui.toggle(key=f"workflow-generation-enabled:fixture:{node}").set_value(True).run()
    ui.selectbox(key=f"workflow-generation-style:fixture:{node}").set_value("custom").run()
    ui.button(key="fixture-node:ingest").click().run()
    key = f"workflow-config-fix:fixture:style:{node}"
    assert ui.button(key="workflow-create:fixture").disabled
    ui.button(key=key).click().run()
    _assert_open(ui, node, "提示词与风格")
    assert ui.text_input(key="workflow-name:fixture").value == "Keep this run name"
    assert ui.text_area(key=f"workflow-generation-instruction:fixture:{node}").value == ""
    ui.text_area(key=f"workflow-generation-instruction:fixture:{node}").set_value("Check the source first.").run()
    assert not any(button.key == key for button in ui.button)


def test_trim_warning_opens_its_template_without_changing_other_prompts():
    ui = AppTest.from_string(SCRIPT).run()
    source_prompt = "workflow-node-prompt:fixture:sft:workflow.sft"
    ui.text_area(key=source_prompt).set_value("Keep these SFT instructions.").run()
    ui.toggle(key="workflow-trim-enabled:fixture").set_value(True).run()
    ui.button(key="fixture-node:trim").click().run()
    ui.selectbox(key="workflow-trim-template:fixture").set_value("custom").run()
    ui.button(key="fixture-node:sft").click().run()
    ui.button(key="workflow-config-fix:fixture:trim:trim").click().run()
    _assert_open(ui, "trim", "提示词与风格")
    assert ui.selectbox(key="workflow-trim-template:fixture").value == "custom"
    assert ui.session_state["workflow-form-draft:fixture"][source_prompt] == "Keep these SFT instructions."


def test_prompt_warning_selects_the_actual_invalid_template_after_another_node_is_selected():
    ui = AppTest.from_string(SCRIPT).run()
    selection = "workflow-node-prompt-selection:fixture:sft"
    ui.selectbox(key=selection).set_value("workflow.jev_score").run()
    invalid_key = "workflow-node-prompt:fixture:sft:workflow.jev_score"
    ui.text_area(key=invalid_key).set_value(" ").run()
    ui.selectbox(key=selection).set_value("workflow.sft").run()
    ui.button(key="fixture-node:ingest").click().run()
    ui.button(key="workflow-config-fix:fixture:prompt:sft").click().run()
    _assert_open(ui, "sft", "提示词与风格")
    assert ui.selectbox(key=selection).value == "workflow.jev_score"
    assert ui.text_area(key=invalid_key).value == " "
    assert ui.button(key="workflow-create:fixture").disabled


def test_missing_models_are_separate_clickable_node_errors():
    code = SCRIPT.replace("'backends':[{'name':'local','models':['writer','judge']}]", "'backends':[]")
    ui = AppTest.from_string(code).run()
    ui.button(key="fixture-node:ingest").click().run()
    action = ui.button(key="workflow-config-fix:fixture:model:sft")
    assert "生成模型" in action.label and "独立质量评审模型" in action.label
    assert not any(button.key == "workflow-next-config:fixture" for button in ui.button)
    action.click().run()
    _assert_open(ui, "sft", "节点设置")
    assert ui.button(key="workflow-create:fixture").disabled


@pytest.mark.parametrize("invalid_rules", [False, True])
def test_director_issue_opens_settings_or_rules_for_the_actual_failure(invalid_rules):
    ui = AppTest.from_string(SCRIPT).run()
    ui.toggle(key="workflow-director-enabled:fixture").set_value(True).run()
    ui.button(key="fixture-node:director").click().run()
    if invalid_rules:
        ui.text_area(key="workflow-director-question-rules:fixture").set_value(" ").run()
    else:
        ui.selectbox(key="workflow-director-mode:fixture").set_value("balanced").run()
        for name in ("closed_book", "grounded", "partial", "multi_source", "distractor"):
            ui.number_input(key=f"workflow-director-weight:fixture:{name}").set_value(0).run()
    ui.button(key="fixture-node:sft").click().run()
    ui.button(key="workflow-config-fix:fixture:director:director").click().run()
    _assert_open(ui, "director", "提示词与风格" if invalid_rules else "节点设置")
    assert ui.button(key="workflow-create:fixture").disabled


def test_isolated_agent_environment_warning_opens_the_agent_verification_settings():
    code = SCRIPT.replace(
        "def agent_replay_capabilities(self): return {}",
        "def agent_replay_capabilities(self): return {}\n"
        "    def check_agent_sandbox(self): raise AssertionError('No environment checks in navigation tests')",
    )
    ui = AppTest.from_string(code).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["agent", "sft"]).run()
    ui.button(key="fixture-node:agent").click().run()
    ui.selectbox(key="agent-mode-choice:fixture").set_value("isolated").run()
    ui.button(key="fixture-node:sft").click().run()
    ui.button(key="workflow-config-fix:fixture:agent:agent").click().run()
    _assert_open(ui, "agent", "节点设置")
    assert ui.selectbox(key="agent-mode-choice:fixture").value == "isolated"
    assert ui.button(key="workflow-create:fixture").disabled


def test_missing_budget_rates_open_the_affected_node_without_changing_model_selection():
    code = SCRIPT.replace("'roles':{},", "'roles':{},'budget':{'max_total_usd':1,'hard_stop':True},")
    code = code.replace("WorkflowNodeModelsApplication(Inventory()))",
                        "WorkflowNodeModelsApplication(Inventory()), backend_application=Inventory())")
    ui = AppTest.from_string(code).run()
    assert not ui.exception
    chosen = ui.selectbox(key="node-model:fixture:sft:generation:model:local").value
    ui.button(key="fixture-node:ingest").click().run()
    ui.button(key="workflow-config-fix:fixture:prices:sft").click().run()
    _assert_open(ui, "sft", "节点设置")
    assert ui.selectbox(key="node-model:fixture:sft:generation:model:local").value == chosen
    assert ui.button(key="workflow-create:fixture").disabled


def test_image_parser_warning_opens_input_settings_and_keeps_the_selected_image():
    code = SCRIPT.replace("'fixture.txt'", "'fixture.png'")
    ui = AppTest.from_string(code).run()
    sources = "workflow-sources:fixture:文档资料"
    ui.multiselect(key=sources).set_value(["fixture.png"]).run()
    ui.button(key="fixture-node:ingest").click().run()
    ui.radio(key="workflow-document-parse-mode:fixture").set_value("native").run()
    ui.button(key="fixture-node:sft").click().run()
    ui.button(key="workflow-config-fix:fixture:image-parser:ingest").click().run()
    _assert_open(ui, "ingest", "节点设置")
    assert ui.multiselect(key=sources).value == ["fixture.png"]
    assert ui.button(key="workflow-create:fixture").disabled


def test_cpt_image_review_warning_opens_cpt_settings_without_confirming_capabilities():
    ui = AppTest.from_string(SCRIPT).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cpt", "sft"]).run()
    ui.button(key="fixture-node:cpt").click().run()
    ui.radio(key="workflow-cpt-review-mode:fixture").set_value("vision").run()
    ui.button(key="fixture-node:sft").click().run()
    ui.button(key="workflow-config-fix:fixture:cpt-vision:cpt").click().run()
    _assert_open(ui, "cpt", "节点设置")
    assert ui.radio(key="workflow-cpt-review-mode:fixture").value == "vision"
    assert ui.button(key="workflow-create:fixture").disabled


def test_english_issue_and_tab_selection_stay_english_then_follow_language_changes():
    code = SCRIPT.replace("import streamlit as st", """import streamlit as st
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
st.session_state.setdefault('ui_language', 'en')""", 1)
    ui = AppTest.from_string(code).run()
    ui.toggle(key="workflow-generation-enabled:fixture:sft").set_value(True).run()
    ui.selectbox(key="workflow-generation-style:fixture:sft").set_value("custom").run()
    action = ui.button(key="workflow-config-fix:fixture:style:sft")
    assert not re.search(r"[\u4e00-\u9fff]", action.label)
    action.click().run()
    _assert_open(ui, "sft", "Prompts and style")
    ui.session_state["ui_language"] = "zh"
    ui.run()
    _assert_open(ui, "sft", "提示词与风格")
