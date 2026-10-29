"""Missing-reasoning choices stay in the input node and survive draft reuse."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
from lib.prompts import get, render
from ui_tests.test_workflow_operation_logic import _script, SOURCE


POLICY = "workflow-conversation-reasoning:fixture"
POLICY_VERSION = "workflow-conversation-reasoning-version:fixture"
POLICY_UPGRADE = "workflow-conversation-reasoning-upgrade:fixture"
PROMPT_SELECTION = "workflow-node-prompt-selection:fixture:ingest"


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
    assert ui.session_state["fixture-durable"][POLICY_VERSION] == 3
    assert ui.selectbox(key=PROMPT_SELECTION).options == ["答案提炼为推导提示", "正文 CoT 补写"]
    assert any("待完善（实验性）" in item.value for item in ui.caption)
    assert any("本次调用的上游原生推理保留在请求证据中，不混入补写内容" in item.value for item in ui.caption)
    hints_key = "workflow-node-prompt:fixture:ingest:workflow.conversation_reasoning_hints"
    reasoning_key = "workflow-node-prompt:fixture:ingest:workflow.conversation_reasoning"
    ui.text_area(key=hints_key).set_value("Extract hints without copying the answer.").run()
    ui.selectbox(key=PROMPT_SELECTION).set_value("workflow.conversation_reasoning").run()
    ui.text_area(key=reasoning_key).set_value("Use visible history and hints to derive the reasoning.").run()
    assert not ui.exception
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
    assert saved[hints_key] == "Extract hints without copying the answer."
    assert saved[reasoning_key] == "Use visible history and hints to derive the reasoning."
    fresh = AppTest.from_string(script)
    fresh.session_state["fixture-durable"] = saved
    fresh.session_state["workflow-setup-node:fixture"] = "ingest"
    fresh.run()
    assert not fresh.exception
    assert fresh.selectbox(key=POLICY).value == "exclude"


@pytest.mark.parametrize("mode", ["preserve", "fill_missing", "exclude"])
@pytest.mark.parametrize("version", [1, 2, 3])
def test_reusing_conversation_keeps_policy_and_node_prompt(tmp_path, mode, version):
    prompt = "Explain only what this assistant turn could know."
    templates = {"workflow.conversation_reasoning": prompt}
    if version in {2, 3}:
        templates["workflow.conversation_reasoning_hints"] = "Extract deduction hints from the answer."
    recipe = {"sources": [{"file": "conversation.jsonl", "name": "conversation.jsonl", "sha256": "a" * 64}], "targets": ["sft"], "brief": "",
              "conversation_reasoning": {"version": version, "mode": mode},
              "node_prompt_templates": {"ingest": templates}}
    original = deepcopy(recipe)
    draft = recipe_to_draft(recipe, "Reuse imported conversation", "fixture", [])["values"]
    assert draft[POLICY] == mode
    assert draft[POLICY_VERSION] == version
    prompt_key = "workflow-node-prompt:fixture:ingest:workflow.conversation_reasoning"
    assert (draft.get(prompt_key) == prompt) is (mode == "fill_missing")
    hints_key = "workflow-node-prompt:fixture:ingest:workflow.conversation_reasoning_hints"
    assert (hints_key in draft) is (version in {2, 3} and mode == "fill_missing")
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
    assert saved[POLICY_VERSION] == 3
    fresh = AppTest.from_string(script)
    fresh.session_state["fixture-durable"] = saved
    fresh.session_state["workflow-setup-node:fixture"] = "ingest"
    fresh.run()
    assert not fresh.exception
    assert not fresh.session_state.filtered_state.get("workflow-draft-error:fixture")
    assert fresh.segmented_control(key=format_key).value == "DSH 会话库"
    assert fresh.selectbox(key=POLICY).value == "fill_missing"
    assert fresh.selectbox(key=PROMPT_SELECTION).options == ["答案提炼为推导提示", "正文 CoT 补写"]


def test_saved_v1_choice_changes_protocol_only_after_explicit_selection(tmp_path):
    source = tmp_path / "conversation.jsonl"
    source.write_text('{"messages":[{"role":"user","content":"Hello"},'
                      '{"role":"assistant","content":"Hi"}]}\n', encoding="utf-8")
    ui = AppTest.from_string(_script(source))
    ui.session_state["fixture-durable"] = {SOURCE: "Agent 上下文", POLICY: "fill_missing"}
    ui.session_state["workflow-setup-node:fixture"] = "ingest"
    ui.run()
    assert not ui.exception
    assert ui.selectbox(key=PROMPT_SELECTION).options == ["缺失推理补充"]
    assert POLICY_VERSION not in ui.session_state["fixture-durable"]
    ui.selectbox(key=POLICY).set_value("exclude").run()
    ui.selectbox(key=POLICY).set_value("fill_missing").run()
    assert not ui.exception
    assert ui.session_state["fixture-durable"][POLICY_VERSION] == 3
    assert ui.selectbox(key=PROMPT_SELECTION).options == ["答案提炼为推导提示", "正文 CoT 补写"]


@pytest.mark.parametrize("old_version", ["1.0.0", "1.1.0", "2.0.0"])
@pytest.mark.parametrize("custom", [False, True])
def test_explicit_v3_upgrade_button_replaces_only_old_builtin_completion_text(tmp_path, old_version, custom):
    source = tmp_path / "conversation.jsonl"
    source.write_text('{"messages":[{"role":"user","content":"Hello"},'
                      '{"role":"assistant","content":"Hi"}]}\n', encoding="utf-8")
    prompt_id = "workflow.conversation_reasoning"
    prompt_key = f"workflow-node-prompt:fixture:ingest:{prompt_id}"
    old_body = render(get(prompt_id, version=old_version)) + ("\nKeep my custom rule." if custom else "")
    script = _script(source) + """
from lib.presentation.streamlit.workflow_prompt_settings import node_prompt_snapshot
st.session_state['fixture-submitted-prompts'] = node_prompt_snapshot('fixture', ['ingest'], '补全对话推理')
"""
    ui = AppTest.from_string(script)
    old_policy_version = 2 if old_version == "2.0.0" else 1
    saved = {SOURCE: "Agent 上下文", POLICY: "fill_missing", POLICY_VERSION: old_policy_version, prompt_key: old_body}
    ui.session_state["fixture-durable"] = deepcopy(saved)
    ui.session_state["workflow-setup-node:fixture"] = "ingest"
    ui.run()
    ui.selectbox(key=PROMPT_SELECTION).set_value(prompt_id).run()
    assert not ui.exception
    assert ui.text_area(key=prompt_key).value == old_body
    assert ui.session_state["fixture-durable"][POLICY_VERSION] == old_policy_version
    ui.run()
    assert ui.text_area(key=prompt_key).value == old_body
    assert ui.session_state["fixture-durable"][prompt_key] == old_body
    assert ui.button(key=POLICY_UPGRADE).label == "使用新版正文 CoT"
    ui.button(key=POLICY_UPGRADE).click().run()
    ui.selectbox(key=PROMPT_SELECTION).set_value(prompt_id).run()
    assert not ui.exception
    assert ui.selectbox(key=POLICY).value == "fill_missing"
    assert not any(button.key == POLICY_UPGRADE for button in ui.button)
    expected = old_body if custom else render(get(prompt_id, version="2.1.0"))
    assert ui.session_state["fixture-durable"][POLICY_VERSION] == 3
    assert ui.text_area(key=prompt_key).value == expected
    assert ui.session_state["fixture-durable"][prompt_key] == expected
    submitted = ui.session_state["fixture-submitted-prompts"].get("ingest", {})
    assert submitted == ({prompt_id: old_body} if custom else {})
    notice = "自定义补写模板已保留；请使用 history、reasoning_hints 与 response_kind 输入并将 CoT 写在回复正文中，不再依赖原答案 assistant_response。"
    assert any(item.value == notice for item in ui.caption) is custom


@pytest.mark.parametrize("custom", [False, True])
def test_saved_v2_hints_keep_their_body_until_explicit_upgrade(tmp_path, custom):
    source = tmp_path / "conversation.jsonl"
    source.write_text('{"messages":[{"role":"user","content":"Hello"},'
                      '{"role":"assistant","content":"Hi"}]}\n', encoding="utf-8")
    hints_id = "workflow.conversation_reasoning_hints"
    hints_key = f"workflow-node-prompt:fixture:ingest:{hints_id}"
    reasoning_id = "workflow.conversation_reasoning"
    reasoning_key = f"workflow-node-prompt:fixture:ingest:{reasoning_id}"
    old_body = render(get(hints_id, version="1.0.0")) + ("\nKeep my hint rule." if custom else "")
    ui = AppTest.from_string(_script(source))
    ui.session_state["fixture-durable"] = {
        SOURCE: "Agent 上下文", POLICY: "fill_missing", POLICY_VERSION: 2, hints_key: old_body,
    }
    ui.session_state["workflow-setup-node:fixture"] = "ingest"
    ui.run()
    assert not ui.exception
    assert ui.selectbox(key=PROMPT_SELECTION).options == ["答案提炼为推导提示", "正文 CoT 补写"]
    assert ui.text_area(key=hints_key).value == old_body
    ui.selectbox(key=PROMPT_SELECTION).set_value(reasoning_id).run()
    assert ui.text_area(key=reasoning_key).value == render(get(reasoning_id, version="2.0.0"))
    assert ui.session_state["fixture-durable"][POLICY_VERSION] == 2
    ui.selectbox(key=POLICY).set_value("exclude").run()
    ui.selectbox(key=POLICY).set_value("fill_missing").run()
    ui.selectbox(key=PROMPT_SELECTION).set_value(hints_id).run()
    assert not ui.exception
    assert ui.session_state["fixture-durable"][POLICY_VERSION] == 3
    expected = old_body if custom else render(get(hints_id, version="1.1.0"))
    assert ui.text_area(key=hints_key).value == expected
    assert ui.session_state["fixture-durable"][hints_key] == expected
