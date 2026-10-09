"""Node-local director controls must reach the submitted immutable recipe."""
import re

from streamlit.testing.v1 import AppTest

from lib.domain.workflow_qa_director import DEFAULT_TYPE_WEIGHTS
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
from tests.test_workflow_draft import SCRIPT


def _director(ui, *, balanced=False):
    ui.toggle(key="workflow-director-enabled:fixture").set_value(True).run()
    ui.button(key="fixture-node:director").click().run()
    assert not ui.exception
    if balanced:
        ui.selectbox(key="workflow-director-mode:fixture").set_value("balanced").run()


def test_director_rules_type_weights_and_prompts_survive_node_changes():
    ui = AppTest.from_string(SCRIPT).run()
    assert not any(button.key == "fixture-node:director" for button in ui.button)
    _director(ui, balanced=True)
    ui.number_input(key="workflow-director-batch:fixture").set_value(7).run()
    ui.number_input(key="workflow-director-history:fixture").set_value(3).run()
    ui.number_input(key="workflow-director-weight:fixture:closed_book").set_value(70).run()
    ui.text_area(key="workflow-director-question-rules:fixture").set_value("Cover boundary cases.").run()
    ui.text_area(key="workflow-director-answer-rules:fixture").set_value("Ask for missing facts.").run()
    prompt = "workflow-node-prompt:fixture:director:workflow.qa_director"
    ui.text_area(key=prompt).set_value("Custom batch planning instructions.").run()
    ui.button(key="fixture-node:sft").click().run()
    assert ui.selectbox(key="workflow-node-prompt-selection:fixture:sft").value == "workflow.sft_directed"
    ui.button(key="fixture-node:director").click().run()
    assert ui.number_input(key="workflow-director-batch:fixture").value == 7
    assert ui.number_input(key="workflow-director-history:fixture").value == 3
    assert ui.number_input(key="workflow-director-weight:fixture:closed_book").value == 70
    assert ui.text_area(key="workflow-director-question-rules:fixture").value == "Cover boundary cases."
    assert ui.text_area(key=prompt).value == "Custom batch planning instructions."
    ui.toggle(key="workflow-director-enabled:fixture").set_value(False).run()
    assert not any(button.key == "fixture-node:director" for button in ui.button)
    _director(ui)
    assert ui.text_area(key=prompt).value == "Custom batch planning instructions."


def test_all_zero_type_weights_prevent_start_and_cpt_does_not_activate_director():
    ui = AppTest.from_string(SCRIPT).run()
    _director(ui, balanced=True)
    for name in DEFAULT_TYPE_WEIGHTS:
        ui.number_input(key=f"workflow-director-weight:fixture:{name}").set_value(0).run()
    assert ui.button(key="workflow-create:fixture").disabled
    assert any("指导员" in warning.value for warning in ui.warning)
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cpt"]).run()
    assert ui.toggle(key="workflow-director-enabled:fixture").disabled
    assert not any(button.key == "fixture-node:director" for button in ui.button)
    assert not ui.exception


def test_submission_pins_dispatch_rules_and_node_prompts():
    code = SCRIPT.replace("    def task_runs(self): return []", """    def create_run(self, **kwargs):
        st.session_state['fixture-submitted'] = kwargs
        return '0' * 32
    def task_runs(self): return []""")
    ui = AppTest.from_string(code).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["sft"]).run()
    ui.multiselect(key="workflow-sources:fixture:文档资料").set_value(["fixture.txt"]).run()
    _director(ui)
    ui.text_area(key="workflow-director-answer-rules:fixture").set_value("Clarify missing conditions.").run()
    ui.text_area(key="workflow-node-prompt:fixture:director:workflow.qa_director").set_value("My planner prompt.").run()
    ui.button(key="workflow-create:fixture").click().run()
    assert not ui.exception
    recipe = ui.session_state["fixture-submitted"]
    assert recipe["qa_director"]["enabled"] is True
    assert recipe["qa_director"]["planning_mode"] == "adaptive"
    assert recipe["qa_director"]["answer_rules"] == "Clarify missing conditions."
    assert recipe["node_prompts"]["director"]["workflow.qa_director"] == "My planner prompt."
    assert recipe["node_models"]["director"]["generation"]["max_output_tokens"] == 32768
    copied = recipe_to_draft({**recipe, "sources": []}, "Copy", "fixture", [])["values"]
    assert copied["workflow-director-enabled:fixture"] is True
    assert copied["workflow-director-mode:fixture"] == "adaptive"
    assert copied["workflow-director-answer-rules:fixture"] == "Clarify missing conditions."
    assert copied["workflow-node-prompt:fixture:director:workflow.qa_director"] == "My planner prompt."


def test_director_labels_have_english_translations():
    code = SCRIPT.replace("import streamlit as st", """import streamlit as st
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
st.session_state['ui_language'] = 'en' """, 1)
    ui = AppTest.from_string(code).run()
    _director(ui)
    assert ui.toggle(key="workflow-director-enabled:fixture").label == "Add a dialogue director"
    for element in (*ui.number_input, *ui.text_area, *ui.caption, *ui.warning, *ui.info, *ui.selectbox):
        text = element.label if hasattr(element, "label") else element.value
        assert not re.search(r"[\u4e00-\u9fff]", text), text
    assert not ui.exception


def test_invalid_director_outputs_have_actionable_localized_errors():
    from lib.presentation.streamlit.i18n import translate_label
    from lib.presentation.streamlit.workflow_page import _workflow_error

    for code in (
        "invalid_qa_director_planning_mode", "invalid_dialogue_design", "invalid_dialogue_step",
        "invalid_qa_director_skip", "dialogue_step_stopped_before_multiturn",
        "qa_history_dialogue_design_invalid",
        "invalid_qa_director_batch_size", "invalid_qa_director_history_limit",
        "invalid_qa_director_type_weights", "invalid_qa_director_rules", "invalid_qa_director_schedule",
        "qa_director_type_mismatch", "qa_director_id_mismatch", "qa_director_batch_id_mismatch",
        "invalid_qa_director_answer_policy", "qa_director_closed_book_contract",
        "qa_director_visible_evidence_required", "invalid_qa_director_evidence",
        "qa_director_multiple_evidence_required", "qa_director_recorded_conversation_changed",
        "qa_history_question_required", "qa_history_question_too_long", "qa_history_dimension_too_long",
        "qa_history_sample_id_required", "qa_history_sample_id_conflict",
    ):
        chinese = _workflow_error(ValueError(code))
        assert chinese != code and re.search(r"[\u4e00-\u9fff]", chinese)
        english = translate_label(chinese, "en")
        assert english != chinese and not re.search(r"[\u4e00-\u9fff]", english)


def test_adaptive_default_hides_inactive_quotas_and_restores_after_node_switch():
    ui = AppTest.from_string(SCRIPT).run()
    _director(ui)
    assert ui.selectbox(key="workflow-director-mode:fixture").value == "adaptive"
    assert not any("workflow-director-weight:" in (item.key or "") for item in ui.number_input)
    assert any("共同解决问题" in item.value for item in ui.info)
    ui.text_area(key="workflow-director-question-rules:fixture").set_value("Plan collaborative revisions.").run()
    ui.button(key="fixture-node:sft").click().run()
    ui.button(key="fixture-node:director").click().run()
    assert ui.selectbox(key="workflow-director-mode:fixture").value == "adaptive"
    assert ui.text_area(key="workflow-director-question-rules:fixture").value == "Plan collaborative revisions."
    ui.selectbox(key="workflow-director-mode:fixture").set_value("balanced").run()
    assert ui.number_input(key="workflow-director-weight:fixture:closed_book").value == 25
    ui.selectbox(key="workflow-director-mode:fixture").set_value("adaptive").run()
    assert not any("workflow-director-weight:" in (item.key or "") for item in ui.number_input)
    assert not ui.exception


def test_director_canvas_adds_a_column_without_overlapping_existing_nodes():
    from lib.domain.workflow_targets import TARGETS
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS

    spec = canvas_spec(TARGETS, {}, "director", GRAPH_LABELS, STAGE_GLYPHS,
                       qa_director={"enabled": True}, reasoning_trim=True)
    nodes = {row["id"]: row for row in spec["nodes"]}
    assert nodes["ingest"]["x"] < nodes["director"]["x"] < nodes["sft"]["x"]
    assert ("director", "sft") in spec["edges"]
    assert ("director", "multiturn") in spec["edges"]
    assert ("ingest", "cpt") in spec["edges"]
    for row in nodes.values():
        assert 0 <= row["x"] <= spec["width"] - 218
        assert 0 <= row["y"] <= spec["height"] - 78
