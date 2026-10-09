"""Named personal templates reach the actual node editors and persistent drafts."""
import re

from streamlit.testing.v1 import AppTest

from lib.bootstrap.prompt_library import prompt_library_application
from lib.domain.workflow_node_prompts import builtin_node_prompt
from lib.domain.workflow_qa_director import DEFAULT_QUESTION_RULES, DEFAULT_ANSWER_RULES
from tests.test_workflow_draft import SCRIPT


def workbench(tmp_path, *, english=False):
    code = SCRIPT.replace(
        "from lib.presentation.streamlit import workflow_page as page",
        "from lib.presentation.streamlit import workflow_page as page\n"
        "from lib.bootstrap.prompt_library import prompt_library_application\n"
        "from pathlib import Path\n"
        f"library = prompt_library_application(root=Path({tmp_path.as_posix()!r}))",
    ).replace(
        "WorkflowNodeModelsApplication(Inventory()))",
        "WorkflowNodeModelsApplication(Inventory()), prompt_library=library)",
    )
    if english:
        code = code.replace("import streamlit as st", "import streamlit as st\n"
                            "from lib.presentation.streamlit.i18n import install_streamlit_localization\n"
                            "install_streamlit_localization()\n"
                            "st.session_state['ui_language'] = 'en'", 1)
    return AppTest.from_string(code).run()


def library_key(scope, action):
    return f"prompt-library:fixture:{scope}:{action}"


def test_save_restore_and_reuse_prompt_in_fresh_workbench(tmp_path):
    ui = workbench(tmp_path)
    assert not ui.exception
    scope = "node:sft:workflow.sft"
    prompt_key = "workflow-node-prompt:fixture:sft:workflow.sft"
    body = 'Use concise explanations. Keep {literal} and {"answer":"complete"}.\n'
    ui.text_area(key=prompt_key).set_value(body).run()
    ui.text_input(key=library_key(scope, "name")).set_value("My writer").run()
    ui.button(key=library_key(scope, "save")).click().run()
    assert not ui.exception
    library = prompt_library_application(root=tmp_path)
    saved = library.list_templates(scope)[0]
    assert library.get_template(saved["id"], scope)["payload"] == {"text": body}
    ui.button(key="workflow-node-prompt-reset:fixture:sft:workflow.sft").click().run()
    assert ui.text_area(key=prompt_key).value == builtin_node_prompt("workflow.sft")
    assert ui.selectbox(key=library_key(scope, "selection")).value is None
    assert library.get_template(saved["id"], scope)["payload"]["text"] == body
    fresh = workbench(tmp_path)
    fresh.selectbox(key=library_key(scope, "selection")).set_value(saved["id"]).run()
    assert fresh.text_area(key=prompt_key).value == body
    assert fresh.session_state["workflow-form-draft:fixture"][prompt_key] == body
    fresh.text_area(key=prompt_key).set_value("New unsaved wording.").run()
    fresh.run()
    assert fresh.text_area(key=prompt_key).value == "New unsaved wording."
    assert library.get_template(saved["id"], scope)["payload"]["text"] == body


def test_director_rules_can_be_reused_and_restored_without_changing_node_prompt(tmp_path):
    library = prompt_library_application(root=tmp_path)
    scope = "director.rules"
    saved = library.save_template(scope, "Boundary questions", {
        "question_rules": "Cover missing conditions.", "answer_rules": "Ask one clear follow-up."})
    ui = workbench(tmp_path)
    ui.toggle(key="workflow-director-enabled:fixture").set_value(True).run()
    ui.button(key="fixture-node:director").click().run()
    prompt_key = "workflow-node-prompt:fixture:director:workflow.qa_director"
    original = ui.text_area(key=prompt_key).value
    ui.selectbox(key=library_key(scope, "selection")).set_value(saved["id"]).run()
    assert not ui.exception
    assert ui.text_area(key="workflow-director-question-rules:fixture").value == "Cover missing conditions."
    assert ui.text_area(key="workflow-director-answer-rules:fixture").value == "Ask one clear follow-up."
    assert ui.text_area(key=prompt_key).value == original
    ui.button(key=library_key(scope, "restore")).click().run()
    assert ui.text_area(key="workflow-director-question-rules:fixture").value == DEFAULT_QUESTION_RULES
    assert ui.text_area(key="workflow-director-answer-rules:fixture").value == DEFAULT_ANSWER_RULES
    assert library.get_template(saved["id"], scope)["revision"] == 1


def test_save_click_includes_the_latest_uncommitted_textarea_edit(tmp_path):
    scope = "node:sft:workflow.sft"
    ui = workbench(tmp_path)
    ui.text_input(key=library_key(scope, "name")).set_value("Latest edit").run()
    latest = "Use the newest text when editing and saving in one widget batch."
    ui.text_area(key="workflow-node-prompt:fixture:sft:workflow.sft").set_value(latest)
    ui.button(key=library_key(scope, "save")).click().run()
    library = prompt_library_application(root=tmp_path)
    saved = library.list_templates(scope)[0]
    assert library.get_template(saved["id"], scope)["payload"]["text"] == latest
    assert not ui.exception


def test_style_and_cleanup_templates_restore_their_own_fields(tmp_path):
    library = prompt_library_application(root=tmp_path)
    style_scope = "generation:sft"
    style = library.save_template(style_scope, "Short analysis", {
        "style": "custom", "instruction": "Explain the key assumption in one sentence."})
    trim_scope = "trim.rules"
    trim = library.save_template(trim_scope, "Keep formulas", {
        "template": "custom", "instruction": "Keep necessary formulas.",
        "custom_prompt": "Remove repeated setup text. Preserve the final answer."})
    ui = workbench(tmp_path)
    ui.selectbox(key=library_key(style_scope, "selection")).set_value(style["id"]).run()
    assert ui.toggle(key="workflow-generation-enabled:fixture:sft").value is True
    assert ui.selectbox(key="workflow-generation-style:fixture:sft").value == "custom"
    assert ui.text_area(key="workflow-generation-instruction:fixture:sft").value == style["payload"]["instruction"]
    ui.button(key=library_key(style_scope, "restore")).click().run()
    assert ui.toggle(key="workflow-generation-enabled:fixture:sft").value is False
    assert ui.selectbox(key="workflow-generation-style:fixture:sft").value == "default"
    ui.toggle(key="workflow-trim-enabled:fixture").set_value(True).run()
    ui.button(key="fixture-node:trim").click().run()
    ui.selectbox(key=library_key(trim_scope, "selection")).set_value(trim["id"]).run()
    assert ui.selectbox(key="workflow-trim-template:fixture").value == "custom"
    assert ui.text_area(key="workflow-trim-prompt:fixture").value == trim["payload"]["custom_prompt"]
    ui.button(key=library_key(trim_scope, "restore")).click().run()
    assert ui.selectbox(key="workflow-trim-template:fixture").value == "leakage"
    assert ui.text_area(key="workflow-trim-instruction:fixture").value == ""
    assert library.get_template(trim["id"], trim_scope)["payload"] == trim["payload"]
    assert not ui.exception


def test_english_controls_keep_user_template_names_literal(tmp_path):
    library = prompt_library_application(root=tmp_path)
    scope = "node:sft:workflow.sft"
    saved = library.save_template(scope, "我的原名", {"text": "Keep my wording."})
    ui = workbench(tmp_path, english=True)
    picker = ui.selectbox(key=library_key(scope, "selection"))
    assert picker.label == "My prompt templates"
    assert "我的原名" in picker.options
    picker.set_value(saved["id"]).run()
    assert ui.text_area(key="workflow-node-prompt:fixture:sft:workflow.sft").value == "Keep my wording."
    assert ui.button(key="workflow-node-prompt-reset:fixture:sft:workflow.sft").label == "Restore default"
    assert not ui.exception


def test_library_status_and_error_messages_have_english_translations():
    from lib.presentation.streamlit.i18n import translate_label
    from lib.presentation.streamlit.prompt_library_controls import (
        PROMPT_LIBRARY_ERRORS, PROMPT_LIBRARY_NOTICES,
    )

    for message in (*PROMPT_LIBRARY_ERRORS.values(), *PROMPT_LIBRARY_NOTICES.values()):
        translated = translate_label(message, "en")
        assert translated != message
        assert not re.search(r"[\u4e00-\u9fff]", translated)
