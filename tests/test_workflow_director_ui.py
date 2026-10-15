"""Node-local director controls must reach the submitted immutable recipe."""
import re

import pytest

from streamlit.testing.v1 import AppTest

from lib.domain.workflow_qa_director import DEFAULT_TYPE_WEIGHTS
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
from tests.test_workflow_draft import SCRIPT


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Director controls use fixture state without any network requests."""
    yield


def _director(ui, *, balanced=False):
    ui.toggle(key="workflow-director-enabled:fixture").set_value(True).run()
    ui.button(key="fixture-node:director").click().run()
    assert not ui.exception
    if balanced:
        ui.selectbox(key="workflow-director-mode:fixture").set_value("balanced").run()


def test_empty_draft_enables_separated_director_but_explicit_disabled_choice_is_preserved():
    code = SCRIPT.replace("st.session_state.setdefault('workflow-director-enabled:fixture', False)", '')
    ui = AppTest.from_string(code).run()
    assert not ui.exception
    assert ui.toggle(key='workflow-director-enabled:fixture').value is True
    assert any(button.key == 'fixture-node:director' for button in ui.button)
    assert ui.selectbox(key='workflow-node-prompt-selection:fixture:sft').value == 'workflow.sft_answer'
    assert set(ui.session_state['workflow-node-bindings:fixture']['director']) == {'generation', 'question'}
    ui.toggle(key='workflow-director-enabled:fixture').set_value(False).run()
    saved = ui.session_state['workflow-form-draft:fixture']
    restarted = AppTest.from_string(code)
    restarted.session_state['workflow-form-draft:fixture'] = saved
    restarted.run()
    assert not restarted.exception
    assert restarted.toggle(key='workflow-director-enabled:fixture').value is False
    assert not any(button.key == 'fixture-node:director' for button in restarted.button)


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
    assert ui.selectbox(key="workflow-node-prompt-selection:fixture:sft").value == "workflow.sft_answer"
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


@pytest.mark.parametrize("context_mode", ["persistent", "batch"])
def test_submission_pins_dispatch_rules_and_node_prompts(tmp_path, context_mode):
    source = tmp_path / "fixture.txt"
    source.write_text("A real source for the submitted workflow.", encoding="utf-8")
    code = SCRIPT.replace("    def task_runs(self): return []", """    def create_run(self, **kwargs):
        st.session_state['fixture-submitted'] = kwargs
        return '0' * 32
    def task_runs(self): return []""").replace("'fixture.txt'", repr(str(source)))
    ui = AppTest.from_string(code).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["sft"]).run()
    ui.multiselect(key="workflow-sources:fixture:文档资料").set_value([str(source)]).run()
    _director(ui)
    ui.text_area(key="workflow-director-answer-rules:fixture").set_value("Clarify missing conditions.").run()
    ui.text_area(key="workflow-node-prompt:fixture:director:workflow.qa_director").set_value("My planner prompt.").run()
    if context_mode == "batch":
        ui.selectbox(key="workflow-director-context-mode:fixture").set_value("batch").run()
    ui.button(key="workflow-create:fixture").click().run()
    assert not ui.exception
    recipe = ui.session_state["fixture-submitted"]
    assert recipe["qa_director"]["enabled"] is True
    assert recipe["qa_director"]["question_separation"] is True
    assert recipe["qa_director"]["planning_mode"] == "adaptive"
    assert recipe["qa_director"]["context_mode"] == context_mode
    assert recipe["qa_director"]["tools_mode"] == ("auto" if context_mode == "persistent" else "disabled")
    assert recipe["qa_director"]["answer_rules"] == "Clarify missing conditions."
    assert recipe["node_prompts"]["director"]["workflow.qa_director"] == "My planner prompt."
    assert recipe["node_models"]["director"]["generation"]["max_output_tokens"] == 32768
    assert recipe["node_models"]["director"]["question"] == recipe["node_models"]["director"]["generation"]
    copied = recipe_to_draft({**recipe, "sources": []}, "Copy", "fixture", [])["values"]
    assert copied["workflow-director-enabled:fixture"] is True
    assert copied["workflow-director-mode:fixture"] == "adaptive"
    assert copied["workflow-director-context-mode:fixture"] == context_mode
    assert copied["workflow-director-tools-mode:fixture"] == ("auto" if context_mode == "persistent" else "disabled")
    assert copied["workflow-director-answer-rules:fixture"] == "Clarify missing conditions."
    assert copied["workflow-node-prompt:fixture:director:workflow.qa_director"] == "My planner prompt."


def test_director_explains_role_separation_and_editable_question_prompt():
    ui = AppTest.from_string(SCRIPT).run()
    _director(ui)
    assert any('无线索提问交给没有资料上下文的独立模型' in row.value for row in ui.caption)
    selection = ui.selectbox(key='workflow-node-prompt-selection:fixture:director')
    assert set(selection.options) == {'对话设计与逐轮指导', '提问简报资料隔离检查',
                                      '无线索提问模板', '问题适配检查'}
    selection.set_value('workflow.question_user').run()
    key = 'workflow-node-prompt:fixture:director:workflow.question_user'
    ui.text_area(key=key).set_value('Use a curious learner voice without private sources.').run()
    ui.button(key='fixture-node:sft').click().run()
    ui.button(key='fixture-node:director').click().run()
    ui.selectbox(key='workflow-node-prompt-selection:fixture:director').set_value('workflow.question_user').run()
    assert ui.text_area(key=key).value == 'Use a curious learner voice without private sources.'
    ui.button(key='workflow-node-prompt-reset:fixture:director:workflow.question_user').click().run()
    assert not ui.exception
    assert ui.text_area(key=key).value != 'Use a curious learner voice without private sources.'


def test_question_brief_isolation_template_is_editable_and_restorable_on_director():
    ui = AppTest.from_string(SCRIPT).run()
    _director(ui)
    selection = 'workflow-node-prompt-selection:fixture:director'
    ui.selectbox(key=selection).set_value('workflow.question_brief_check').run()
    key = 'workflow-node-prompt:fixture:director:workflow.question_brief_check'
    default = ui.text_area(key=key).value
    custom = 'Check whether the brief contains any source facts before the question model receives it.'
    ui.text_area(key=key).set_value(custom).run()
    ui.button(key='fixture-node:sft').click().run()
    ui.button(key='fixture-node:director').click().run()
    ui.selectbox(key=selection).set_value('workflow.question_brief_check').run()
    assert not ui.exception
    assert ui.text_area(key=key).value == custom
    ui.button(key='workflow-node-prompt-reset:fixture:director:workflow.question_brief_check').click().run()
    assert not ui.exception
    assert ui.text_area(key=key).value == default


def test_director_labels_have_english_translations():
    code = SCRIPT.replace("import streamlit as st", """import streamlit as st
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
st.session_state['ui_language'] = 'en' """, 1)
    ui = AppTest.from_string(code).run()
    _director(ui)
    assert ui.toggle(key="workflow-director-enabled:fixture").label == "Add a dialogue director"
    assert ui.selectbox(key="workflow-director-context-mode:fixture").label == "Conversation context"
    assert ui.selectbox(key="workflow-director-context-mode:fixture").options == [
        "Continue with the same knowledge", "Independent batches"]
    assert ui.selectbox(key="workflow-director-tools-mode:fixture").options == [
        "Use tools as needed", "Disable tools"]
    ui.selectbox(key="workflow-director-context-mode:fixture").set_value("batch").run()
    assert ui.selectbox(key="director-tools-effective-disabled:fixture").disabled
    assert ui.selectbox(key="director-tools-effective-disabled:fixture").options == ["Disable tools"]
    for element in (*ui.number_input, *ui.text_area, *ui.caption, *ui.warning, *ui.info, *ui.selectbox):
        text = element.label if hasattr(element, "label") else element.value
        assert not re.search(r"[\u4e00-\u9fff]", text), text
    assert not ui.exception


def test_context_modes_stay_on_node_preserve_tool_preference_and_survive_disk_draft_restore(tmp_path):
    from lib.bootstrap.creation_drafts import creation_draft_application

    script = SCRIPT + """
from lib.presentation.streamlit.workflow_director_settings import director_snapshot
st.session_state['fixture-current-director'] = director_snapshot('fixture')
"""
    ui = AppTest.from_string(script).run()
    _director(ui)
    context_key = "workflow-director-context-mode:fixture"
    tools_key = "workflow-director-tools-mode:fixture"
    assert ui.selectbox(key=context_key).value == "persistent"
    assert ui.selectbox(key=tools_key).value == "auto"
    ui.selectbox(key=tools_key).set_value("disabled").run()
    ui.selectbox(key=context_key).set_value("batch").run()
    assert ui.session_state["fixture-current-director"]["tools_mode"] == "disabled"
    assert ui.selectbox(key="director-tools-effective-disabled:fixture").disabled
    ui.selectbox(key=context_key).set_value("persistent").run()
    assert ui.selectbox(key=tools_key).value == "disabled"

    ui.selectbox(key=tools_key).set_value("auto").run()
    ui.selectbox(key=context_key).set_value("batch").run()
    ui.button(key="fixture-node:sft").click().run()
    ui.button(key="fixture-node:director").click().run()
    assert ui.selectbox(key=context_key).value == "batch"
    assert ui.session_state["fixture-current-director"]["tools_mode"] == "disabled"
    saved = dict(ui.session_state["workflow-form-draft:fixture"])
    assert saved[tools_key] == "auto"  # Preference, not the temporary forced mode.
    assert not any(key.startswith("director-tools-effective-disabled:") for key in saved)
    drafts = creation_draft_application(tmp_path)
    drafts.replace(saved)
    identifier = drafts.save_snapshot("Director choices")
    restored = creation_draft_application(tmp_path).restore_snapshot(identifier)
    assert restored == saved

    reopened = AppTest.from_string(script)
    reopened.session_state["workflow-form-draft:fixture"] = restored
    reopened.session_state["workflow-setup-node:fixture"] = "director"
    reopened.run()
    assert not reopened.exception
    assert reopened.selectbox(key=context_key).value == "batch"
    assert reopened.session_state["fixture-current-director"]["tools_mode"] == "disabled"
    reopened.selectbox(key=context_key).set_value("persistent").run()
    assert reopened.selectbox(key=tools_key).value == "auto"
    assert reopened.session_state["fixture-current-director"]["tools_mode"] == "auto"
    assert not reopened.exception


@pytest.mark.parametrize("modes,expected", [
    ({}, ("batch", "disabled")),
    ({"context_mode": "persistent", "tools_mode": "auto"}, ("persistent", "auto")),
    ({"context_mode": "persistent", "tools_mode": "disabled"}, ("persistent", "disabled")),
    ({"context_mode": "batch", "tools_mode": "disabled"}, ("batch", "disabled")),
])
def test_copy_run_restores_explicit_modes_and_preserves_legacy_batch_execution(modes, expected):
    from copy import deepcopy
    from test_workflow_reuse import recipe

    original = recipe(qa_director={"enabled": True, **modes})
    before = deepcopy(original)
    values = recipe_to_draft(original, "Saved task", "fixture", [])["values"]
    assert values["workflow-director-context-mode:fixture"] == expected[0]
    assert values["workflow-director-tools-mode:fixture"] == expected[1]
    assert original == before


def test_new_snapshot_defaults_and_effective_batch_tools_do_not_mutate_saved_preferences(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from lib.presentation.streamlit import workflow_director_settings as settings
    from lib.domain.workflow_qa_director import validate_qa_director

    values = {"workflow-director-enabled:fixture": True}
    state = {"workflow-form-draft:fixture": values}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=state))
    before = deepcopy(state)
    snapshot = settings.director_snapshot("fixture")
    assert snapshot["context_mode"] == "persistent" and snapshot["tools_mode"] == "auto"
    assert validate_qa_director(snapshot)["tools_mode"] == "auto" and state == before
    values.update({"workflow-director-context-mode:fixture": "batch",
                   "workflow-director-tools-mode:fixture": "auto"})
    before = deepcopy(state)
    snapshot = settings.director_snapshot("fixture")
    assert snapshot["context_mode"] == "batch" and snapshot["tools_mode"] == "disabled"
    assert validate_qa_director(snapshot)["tools_mode"] == "disabled" and state == before


@pytest.mark.parametrize("key,bad", [
    ("workflow-director-context-mode:fixture", "unknown"),
    ("workflow-director-tools-mode:fixture", "execute_arbitrary_tools"),
])
def test_context_draft_fields_only_accept_known_modes(key, bad):
    from lib.domain.creation_draft import validate_creation_draft
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({key: bad})


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
        "invalid_question_separation", "invalid_question_brief", "invalid_question_brief_assessment", "question_brief_contains_private_source",
        "private_context_in_question_request", "invalid_visible_dialogue", "invalid_question_output",
        "invalid_question_assessment", "question_not_suitable",
        "duplicate_knowledge_agent_task", "knowledge_agent_tool_limit", "knowledge_context_result_too_large",
        "knowledge_agent_planning_timeout", "knowledge_agent_invalid_assistant", "knowledge_agent_invalid_tool_call",
        "knowledge_agent_context_exhausted",
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
    assert any("角色关系" in item.value and "实际回应逐轮调整" in item.value for item in ui.info)
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


def test_separated_director_canvas_shows_both_models_and_names_the_missing_question_role():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS
    binding = {'backend': 'writer', 'model': 'fixture', 'context_window_tokens': 131072,
               'max_output_tokens': 32768}
    arguments = {'qa_director': {'enabled': True, 'question_separation': True},
                 'recipe_version': 20, 'source_cleaning': True}
    spec = canvas_spec(['sft'], {'director': {'status': 'configuration_required'}}, 'director',
                       GRAPH_LABELS, STAGE_GLYPHS, bindings={'director': {'generation': binding}},
                       **arguments)
    director = next(row for row in spec['nodes'] if row['id'] == 'director')
    assert director['subtitle'] == '缺少：无线索提问模型'
    complete = canvas_spec(['sft'], {}, 'director', GRAPH_LABELS, STAGE_GLYPHS,
                           bindings={'director': {'generation': binding, 'question': binding}}, **arguments)
    director = next(row for row in complete['nodes'] if row['id'] == 'director')
    assert director['models'] == ['指导员: writer · fixture', '无线索提问: writer · fixture']
    assert '无线索提问交给独立模型' in director['description']
    assert '独立问题与资料记忆' in next(row for row in complete['nodes'] if row['id'] == 'sft')['description']
    legacy = canvas_spec(['sft'], {}, 'director', GRAPH_LABELS, STAGE_GLYPHS,
                         bindings={'director': {'generation': binding}}, qa_director={'enabled': True},
                         recipe_version=19)
    assert next(row for row in legacy['nodes'] if row['id'] == 'director')['models'] == ['指导员: writer · fixture']


def test_missing_question_model_has_a_clickable_node_issue_without_changing_prompts():
    ui = AppTest.from_string(SCRIPT).run()
    _director(ui)
    prompt = 'workflow-node-prompt:fixture:director:workflow.qa_director'
    ui.text_area(key=prompt).set_value('Keep my director rules.').run()
    ui.selectbox(key='node-model:fixture:director:question:model:local').set_value(None).run()
    assert not ui.exception
    ui.button(key='fixture-node:sft').click().run()
    issue = ui.button(key='workflow-config-fix:fixture:model:director')
    assert '无线索提问模型' in issue.label
    issue.click().run()
    assert not ui.exception
    assert ui.session_state['workflow-setup-node:fixture'] == 'director'
    assert ui.session_state['workflow-form-draft:fixture'][prompt] == 'Keep my director rules.'
    assert ui.button(key='workflow-create:fixture').disabled


def test_director_run_configuration_uses_the_actual_roles_and_separate_token_limits(monkeypatch):
    from types import SimpleNamespace
    from lib.presentation.streamlit import workflow_page
    monkeypatch.setattr(workflow_page, 'st', SimpleNamespace(session_state={'ui_language': 'zh'}))
    bindings = {'generation': {'backend': 'director', 'model': 'expert',
                               'context_window_tokens': 180_000, 'max_output_tokens': 40_000},
                'question': {'backend': 'questioner', 'model': 'learner',
                             'context_window_tokens': 64_000, 'max_output_tokens': 8192}}
    recipe = {'version': 20, 'targets': ['sft'], 'node_models': {'director': bindings},
              'qa_director': {'enabled': True, 'question_separation': True}}
    config = workflow_page._stage_configuration('director', recipe, {})
    assert config['对话指导员模型'] == 'director / expert'
    assert config['无线索提问模型'] == 'questioner / learner'
    assert config['指导员单次输出上限'] == '40,000 tokens'
    assert config['提问单次输出上限'] == '8,192 tokens'
    old = workflow_page._stage_configuration('director', {**recipe, 'version': 19}, {})
    assert '无线索提问模型' not in old
    assert workflow_page._missing_model_role_summary([('director', 'question')], 'en') == 'Dialogue director: missing Context-free question model'


@pytest.mark.parametrize("targets", [["sft"], ["cpt", "sft"], ["sft", "cot"], ["multiturn"], ["dpo"]])
@pytest.mark.parametrize("live", [False, True])
def test_document_director_canvas_follows_cleaned_sources_before_dialogue_generation(targets, live):
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS

    spec = canvas_spec(targets, {"cpt": {"status": "completed", "done": 3, "total": 3}},
                       "director", GRAPH_LABELS, STAGE_GLYPHS, live=live,
                       qa_director={"enabled": True}, source_cleaning=True, recipe_version=19)
    nodes = {row["id"]: row for row in spec["nodes"]}
    assert nodes["ingest"]["x"] < nodes["cpt"]["x"] < nodes["director"]["x"]
    assert ("ingest", "cpt") in spec["edges"]
    assert ("cpt", "director") in spec["edges"]
    assert ("ingest", "director") not in spec["edges"]
    for stage in ("sft", "multiturn"):
        if stage in nodes:
            assert nodes["director"]["x"] < nodes[stage]["x"]
            assert ("director", stage) in spec["edges"]
            assert ("ingest", stage) not in spec["edges"]
    assert nodes["cpt"]["label"] == "CPT 语料处理"
    assert nodes["cpt"]["intermediate"] is ("cpt" not in targets)
    assert "用户输入" in nodes["director"]["description"]
    if live:
        assert nodes["cpt"]["status"] == "completed"
        assert nodes["cpt"]["percent"] == 100


def test_legacy_and_open_brief_canvases_keep_their_actual_source_dependencies():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS

    for version, source_cleaning in ((18, True), (19, False)):
        spec = canvas_spec(["cpt", "sft"], {}, "director", GRAPH_LABELS, STAGE_GLYPHS,
                           live=True, qa_director={"enabled": True}, source_cleaning=source_cleaning,
                           recipe_version=version)
        assert ("ingest", "cpt") in spec["edges"]
        assert ("ingest", "director") in spec["edges"]
        assert ("cpt", "director") not in spec["edges"]
        assert next(node for node in spec["nodes"] if node["id"] == "cpt")["label"] == "CPT 语料处理"


def test_director_explains_clean_source_route_and_visible_context_without_new_controls():
    ui = AppTest.from_string(SCRIPT).run()
    _director(ui)
    assert any("CPT 语料处理 → 对话指导员" in row.value for row in ui.caption)
    assert any("指导员按行号选完整知识块" in row.value and "系统资料记忆收到所选原文" in row.value
               for row in ui.caption)
    rules = ui.text_area(key="workflow-director-answer-rules:fixture").value
    ui.segmented_control(key="workflow-source-mode:fixture").set_value("开放需求").run()
    ui.button(key="fixture-node:director").click().run()
    assert not ui.exception
    assert not any("CPT 语料处理 → 对话指导员" in row.value for row in ui.caption)
    assert ui.text_area(key="workflow-director-answer-rules:fixture").value == rules


@pytest.mark.parametrize('mode', ['adaptive', 'balanced'])
@pytest.mark.parametrize('custom', [False, True])
def test_saved_director_rules_upgrade_only_exact_adaptive_builtins_on_read(monkeypatch, mode, custom):
    from copy import deepcopy
    from types import SimpleNamespace
    from lib.domain.workflow_qa_director import DEFAULT_QUESTION_RULES, DEFAULT_ANSWER_RULES
    from lib.presentation.streamlit import workflow_director_settings as settings

    question = settings._LEGACY_RULE_DEFAULTS['question']
    answer = settings._LEGACY_RULE_DEFAULTS['answer']
    if custom:
        question += " 保留我的问题设计。"
        answer += " 保留我的回应设计。"
    values = {'workflow-director-enabled:fixture': True, 'workflow-director-mode:fixture': mode,
              'workflow-director-question-rules:fixture': question,
              'workflow-director-answer-rules:fixture': answer}
    state = {'workflow-form-draft:fixture': deepcopy(values)}
    monkeypatch.setattr(settings, 'st', SimpleNamespace(session_state=state))
    before = deepcopy(state)
    snapshot = settings.director_snapshot('fixture')
    assert snapshot['question_rules'] == (DEFAULT_QUESTION_RULES if mode == 'adaptive' and not custom else question)
    assert snapshot['answer_rules'] == (DEFAULT_ANSWER_RULES if mode == 'adaptive' and not custom else answer)
    assert state == before


@pytest.mark.parametrize('mode', ['adaptive', 'balanced'])
def test_director_rule_widgets_and_submission_use_exact_default_upgrade_without_rewriting_saved_draft(mode):
    from copy import deepcopy
    from lib.domain.workflow_qa_director import DEFAULT_QUESTION_RULES, DEFAULT_ANSWER_RULES
    from lib.presentation.streamlit import workflow_director_settings as settings

    script = SCRIPT + """
from lib.presentation.streamlit.workflow_director_settings import director_snapshot
st.session_state['fixture-director-snapshot'] = director_snapshot('fixture')
"""
    values = {'workflow-director-enabled:fixture': True, 'workflow-director-mode:fixture': mode,
              'workflow-director-question-rules:fixture': settings._LEGACY_RULE_DEFAULTS['question'],
              'workflow-director-answer-rules:fixture': settings._LEGACY_RULE_DEFAULTS['answer']}
    ui = AppTest.from_string(script)
    ui.session_state['workflow-form-draft:fixture'] = deepcopy(values)
    ui.session_state['workflow-setup-node:fixture'] = 'director'
    ui.run()
    assert not ui.exception
    for name, current in (('question', DEFAULT_QUESTION_RULES), ('answer', DEFAULT_ANSWER_RULES)):
        key = f'workflow-director-{name}-rules:fixture'
        expected = current if mode == 'adaptive' else values[key]
        assert ui.text_area(key=key).value == expected
        assert ui.session_state['fixture-director-snapshot'][f'{name}_rules'] == expected
        assert ui.session_state['workflow-form-draft:fixture'][key] == values[key]
    # A human edit of an old template remains theirs after node switches.
    key = 'workflow-director-question-rules:fixture'
    custom = values[key] + ' Keep this exact custom instruction.'
    ui.text_area(key=key).set_value(custom).run()
    ui.button(key='fixture-node:sft').click().run()
    ui.button(key='fixture-node:director').click().run()
    assert not ui.exception
    assert ui.text_area(key=key).value == custom
    assert ui.session_state['fixture-director-snapshot']['question_rules'] == custom
