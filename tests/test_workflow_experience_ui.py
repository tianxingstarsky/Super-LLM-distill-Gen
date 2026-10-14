"""Node-local experiences survive edit, restore, save, restart and role changes."""
from streamlit.testing.v1 import AppTest

from lib.bootstrap.prompt_library import prompt_library_application
from lib.domain.agent_experience import default_experience_text
from tests.test_workflow_prompt_library import workbench, library_key


def test_experience_can_be_saved_restored_and_applied_after_restart(tmp_path):
    ui = workbench(tmp_path)
    scope = "experience:answer"
    text_key = "workflow-agent-experience-text:fixture:answer"
    toggle_key = "workflow-agent-experience-enabled:fixture:answer"
    body = "Respond to the actual user. Explain the source conditions without quoting internal labels. {literal}"
    ui.text_area(key=text_key).set_value(body).run()
    ui.toggle(key=toggle_key).set_value(False).run()
    ui.text_input(key=library_key(scope, "name")).set_value("Domain writer").run()
    ui.button(key=library_key(scope, "save")).click().run()
    assert not ui.exception
    library = prompt_library_application(root=tmp_path)
    saved = library.list_templates(scope)[0]
    assert library.get_template(saved["id"], scope)["payload"] == {"enabled": False, "text": body}
    ui.button(key=library_key(scope, "restore")).click().run()
    assert ui.text_area(key=text_key).value == default_experience_text("answer")
    assert ui.toggle(key=toggle_key).value is True
    fresh = workbench(tmp_path)
    fresh.selectbox(key=library_key(scope, "selection")).set_value(saved["id"]).run()
    assert fresh.text_area(key=text_key).value == body
    assert fresh.toggle(key=toggle_key).value is False
    assert fresh.session_state["workflow-form-draft:fixture"][text_key] == body
    assert not fresh.exception


def test_changing_processing_role_does_not_lose_experience_edits(tmp_path):
    ui = workbench(tmp_path)
    key = "workflow-agent-experience-text:fixture:answer"
    ui.text_area(key=key).set_value("Keep the user's actual uncertainty.").run()
    ui.selectbox(key="workflow-node-prompt-selection:fixture:sft").set_value("workflow.jev_score").run()
    assert ui.text_area(key="workflow-agent-experience-text:fixture:review").value == default_experience_text("review")
    ui.selectbox(key="workflow-node-prompt-selection:fixture:sft").set_value("workflow.sft").run()
    assert ui.text_area(key=key).value == "Keep the user's actual uncertainty."
    assert not ui.exception


def test_experience_controls_have_english_labels(tmp_path):
    ui = workbench(tmp_path, english=True)
    assert not ui.exception
    assert ui.toggle(key="workflow-agent-experience-enabled:fixture:answer").label == "Use agent experience"
    assert ui.text_area(key="workflow-agent-experience-text:fixture:answer").label == "Experience text"
    assert any("Agent experience is on" in caption.value for caption in ui.caption)
    assert any("Steps with the same role share this experience" in caption.value for caption in ui.caption)


def test_invalid_experience_and_prompt_have_distinct_recovery_links(tmp_path):
    ui = workbench(tmp_path)
    experience_key = "workflow-agent-experience-text:fixture:answer"
    prompt_key = "workflow-node-prompt:fixture:sft:workflow.sft"
    ui.text_area(key=experience_key).set_value("").run()
    ui.text_area(key=prompt_key).set_value("").run()
    assert not ui.exception
    ui.button(key="fixture-node:cpt").click().run()
    ui.button(key="workflow-config-fix:fixture:experience:answer:sft").click().run()
    assert not ui.exception
    assert ui.session_state["workflow-setup-node:fixture"] == "sft"
    assert ui.selectbox(key="workflow-node-prompt-selection:fixture:sft").value == "workflow.sft"
    assert ui.text_area(key=experience_key).value == ""
    assert ui.text_area(key=prompt_key).value == ""


def test_invalid_inactive_experience_can_be_recovered_without_losing_other_edits(tmp_path):
    ui = workbench(tmp_path, english=True)
    answer_key = "workflow-agent-experience-text:fixture:answer"
    cpt_key = "workflow-agent-experience-text:fixture:cpt"
    ui.text_area(key=answer_key).set_value("").run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cpt"]).run()
    assert not ui.exception
    assert ui.button(key="workflow-experience-recover:fixture:answer").label == "Restore this experience"
    ui.text_area(key=cpt_key).set_value("Preserve the original meaning.").run()
    ui.button(key="workflow-experience-recover:fixture:answer").click().run()
    assert not ui.exception
    assert ui.session_state["workflow-form-draft:fixture"][answer_key] == default_experience_text("answer")
    assert ui.text_area(key=cpt_key).value == "Preserve the original meaning."
