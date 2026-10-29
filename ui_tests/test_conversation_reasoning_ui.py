"""Missing-reasoning choices stay in the input node and survive draft reuse."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
from ui_tests.test_workflow_operation_logic import _script, SOURCE


POLICY = "workflow-conversation-reasoning:fixture"


def test_input_policy_controls_model_requirement_and_survives_navigation(tmp_path):
    source = tmp_path / "conversation.jsonl"
    source.write_text('{"messages":[{"role":"user","content":"Hello"},'
                      '{"role":"assistant","content":"Hi"}]}\n', encoding="utf-8")
    script = _script(source)
    ui = AppTest.from_string(script).run()
    ui.segmented_control(key=SOURCE).set_value("Agent 上下文").run()
    ui.button(key="fixture-node:ingest").click().run()
    assert not ui.exception
    assert ui.selectbox(key=POLICY).value == "preserve"
    model_key = "node-model:fixture:ingest:generation:backend"
    assert not any(item.key == model_key for item in ui.selectbox)
    ui.selectbox(key=POLICY).set_value("fill_missing").run()
    assert not ui.exception
    assert ui.selectbox(key=model_key)
    assert ui.session_state["workflow-node-scope:fixture"]["source_mode"] == "补全对话推理"
    ui.selectbox(key=model_key).set_value(None).run()
    assert any(button.key == "workflow-config-fix:fixture:model:ingest"
               for button in ui.button)
    ui.selectbox(key=POLICY).set_value("exclude").run()
    assert not ui.exception
    assert not any(item.key == model_key for item in ui.selectbox)
    assert not any(button.key == "workflow-config-fix:fixture:model:ingest"
                   for button in ui.button)
    ui.button(key="fixture-node:sft").click().run()
    ui.button(key="fixture-node:ingest").click().run()
    assert ui.selectbox(key=POLICY).value == "exclude"
    saved = deepcopy(ui.session_state["fixture-durable"])
    assert saved[POLICY] == "exclude"
    fresh = AppTest.from_string(script)
    fresh.session_state["fixture-durable"] = saved
    fresh.session_state["workflow-setup-node:fixture"] = "ingest"
    fresh.run()
    assert not fresh.exception
    assert fresh.selectbox(key=POLICY).value == "exclude"


@pytest.mark.parametrize("mode", ["preserve", "fill_missing", "exclude"])
def test_reusing_conversation_keeps_policy_and_node_prompt(tmp_path, mode):
    prompt = "Explain only what this assistant turn could know."
    recipe = {"sources": [{"file": "conversation.jsonl", "name": "conversation.jsonl", "sha256": "a" * 64}], "targets": ["sft"], "brief": "",
              "conversation_reasoning": {"version": 1, "mode": mode},
              "node_prompt_templates": {"ingest": {"workflow.conversation_reasoning": prompt}}}
    original = deepcopy(recipe)
    draft = recipe_to_draft(recipe, "Reuse imported conversation", "fixture", [])["values"]
    assert draft[POLICY] == mode
    prompt_key = "workflow-node-prompt:fixture:ingest:workflow.conversation_reasoning"
    assert (draft.get(prompt_key) == prompt) is (mode == "fill_missing")
    app = creation_draft_application(tmp_path)
    app.replace(draft)
    assert creation_draft_application(tmp_path).load() == draft
    assert recipe == original


def test_old_conversation_recipe_defaults_to_no_generation():
    recipe = {"sources": [{"file": "conversation.jsonl", "name": "conversation.jsonl", "sha256": "a" * 64}], "targets": ["sft"], "brief": ""}
    draft = recipe_to_draft(recipe, "Old import", "fixture", [])["values"]
    assert draft[POLICY] == "preserve"


def test_production_import_format_and_reasoning_choice_autosave_together(tmp_path):
    source = tmp_path / "conversation.jsonl"
    source.write_text('{"messages":[{"role":"user","content":"Hello"},'
                      '{"role":"assistant","content":"Hi"}]}\n', encoding="utf-8")
    script = _script(source).replace(
        "draft_application=MemoryDraft())",
        "draft_application=MemoryDraft(), conversation_importer=object())")
    ui = AppTest.from_string(script).run()
    ui.segmented_control(key=SOURCE).set_value("Agent 上下文").run()
    assert not ui.exception
    assert not ui.session_state.filtered_state.get("workflow-draft-error:fixture")
    format_key = "workflow-conversation-format:fixture"
    assert ui.session_state["fixture-durable"][format_key] == "标准 JSON / JSONL"
    ui.segmented_control(key=format_key).set_value("DSH 会话库").run()
    ui.button(key="fixture-node:ingest").click().run()
    ui.selectbox(key=POLICY).set_value("fill_missing").run()
    assert not ui.exception
    assert not ui.session_state.filtered_state.get("workflow-draft-error:fixture")
    saved = deepcopy(ui.session_state["fixture-durable"])
    assert saved[format_key] == "DSH 会话库"
    assert saved[POLICY] == "fill_missing"
    fresh = AppTest.from_string(script)
    fresh.session_state["fixture-durable"] = saved
    fresh.session_state["workflow-setup-node:fixture"] = "ingest"
    fresh.run()
    assert not fresh.exception
    assert not fresh.session_state.filtered_state.get("workflow-draft-error:fixture")
    assert fresh.segmented_control(key=format_key).value == "DSH 会话库"
    assert fresh.selectbox(key=POLICY).value == "fill_missing"
