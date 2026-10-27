"""Node-local style advice is saved explicitly; copied protocols remain strict."""
from streamlit.testing.v1 import AppTest
from lib.presentation.streamlit import reasoning_checklist_controls as controls


def test_new_node_uses_generation_reflection_without_review_controls(monkeypatch):
    monkeypatch.setattr(controls, "render_prompt_library", lambda *args, **kwargs: None)
    app = AppTest.from_string('''
import streamlit as st
from lib.presentation.streamlit.reasoning_checklist_controls import render_reasoning_checklist, checklist_settings
st.session_state.setdefault("stored", {"workflow-cot-checklist-enabled:default": True})
def field(ws, key, default): return st.session_state.stored.get(key, default)
def restore(ws, key, default): st.session_state.setdefault(key, field(ws, key, default))
def save(ws, key): st.session_state.stored[key] = st.session_state[key]
render_reasoning_checklist("default", field=field, restore=restore, save_field=save, prompt_library=None)
st.json(checklist_settings("default", field))
''').run()
    assert not app.exception
    assert not app.selectbox
    assert app.toggle[0].label == "自定义思考要点"
    config = controls.checklist_settings("default", lambda ws, key, default: app.session_state["stored"].get(key, default))
    assert config["version"] == 7 and config["rules"]
    assert "style_admission" not in config and "evidence_policy" not in config


def test_new_node_uses_builtin_reflection_when_custom_guidance_is_off():
    config = controls.checklist_settings("w", lambda ws, key, default: default)
    assert config["version"] == 7 and config["rules"]


def test_editable_default_preserves_user_delivery_phase_without_copying_it_into_prefix():
    from lib.contexts.workflows.contracts import checklist_from_text, default_integrated_reflection
    from lib.contexts.workflows.domain.integrated_reflection import reflection_instruction
    edited = checklist_from_text(controls.reflection_text(), version=7)
    delivery = next(r for r in default_integrated_reflection()["rules"] if r["id"] == "delivery")
    restored = next(r for r in edited["rules"] if r["requirement"] == delivery["requirement"])
    assert restored["phases"] == ["final"]
    assert restored["requirement"] not in reflection_instruction(edited, phase="prefix")
    assert restored["requirement"] in reflection_instruction(edited)


def test_saved_policy_one_keeps_explicit_advisory_choice():
    values = {"workflow-cot-checklist-enabled:w": True, "workflow-cot-checklist-version:w": 6,
              "workflow-cot-evidence-policy:w": 1, "workflow-cot-style-admission:w": "advisory"}
    config = controls.checklist_settings("w", lambda ws, key, default: values.get(key, default))
    assert config["evidence_policy"] == 1 and config["style_admission"] == "advisory"


def test_explicit_saved_versions_do_not_inherit_relaxed_defaults():
    for version in (5, 6):
        values = {"workflow-cot-checklist-enabled:w": True, "workflow-cot-checklist-version:w": version}
        config = controls.checklist_settings("w", lambda ws, key, default: values.get(key, default))
        assert config["version"] == version
        assert config.get("style_admission", "strict") == "strict"


def test_old_draft_can_switch_to_reflection_without_losing_personal_guidance(monkeypatch):
    monkeypatch.setattr(controls, "render_prompt_library", lambda *args, **kwargs: None)
    app = AppTest.from_string('''
import streamlit as st
from lib.presentation.streamlit.reasoning_checklist_controls import render_reasoning_checklist
st.session_state.setdefault("stored", {"workflow-cot-checklist-enabled:w": True,
    "workflow-cot-checklist-version:w": 6, "workflow-cot-checklist-text:w": "保留我的自定义要点"})
def field(ws, key, default): return st.session_state.stored.get(key, default)
def restore(ws, key, default): st.session_state.setdefault(key, field(ws, key, default))
def save(ws, key): st.session_state.stored[key] = st.session_state[key]
render_reasoning_checklist("w", field=field, restore=restore, save_field=save, prompt_library=None)
''').run()
    assert not app.exception and app.selectbox
    app.button(key="workflow-cot-integrate:w").click().run()
    assert not app.exception and not app.selectbox
    assert app.toggle[0].label == "自定义思考要点"
    assert app.session_state["stored"]["workflow-cot-checklist-version:w"] == 7
    assert app.text_area[0].value == "保留我的自定义要点"
