"""Personal templates apply once, persist drafts, and never silently overwrite."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from lib.bootstrap.prompt_library import prompt_library_application
from lib.presentation.streamlit.prompt_library_controls import (
    PROMPT_LIBRARY_ERRORS, prompt_library_key,
)


SCOPE = "node:sft:workflow.sft"
WORKSPACE = "fixture"
BODY = "workflow-node-prompt:fixture:sft:workflow.sft"
ENABLED = "workflow-node-generation-enabled:fixture:sft"


def _ui(tmp_path: Path, *, application="application", language="zh", prelude=""):
    source = f'''
import streamlit as st
from pathlib import Path
from lib.bootstrap.prompt_library import prompt_library_application
from lib.presentation.streamlit.prompt_library_controls import render_prompt_library
application = prompt_library_application(root=Path({str(tmp_path)!r}), user_key="fixture")
{prelude}
st.session_state["ui_language"] = {language!r}
def save_field(workspace, key):
    draft_key = f"workflow-form-draft:{{workspace}}"
    st.session_state[draft_key] = {{**st.session_state.get(draft_key, {{}}), key: st.session_state[key]}}
draft = st.session_state.get("workflow-form-draft:fixture", {{}})
st.session_state[{BODY!r}] = draft.get({BODY!r}, "Builtin default.")
st.session_state[{ENABLED!r}] = draft.get({ENABLED!r}, False)
render_prompt_library({application}, "fixture", {SCOPE!r}, fields={{"text": {BODY!r}}},
                      defaults={{"text": "Builtin default."}}, save_field=save_field,
                      apply_extras={{{ENABLED!r}: True}}, restore_extras={{{ENABLED!r}: False}})
st.text_area("Body", key={BODY!r}, on_change=save_field, args=("fixture", {BODY!r}))
st.toggle("Enable style", key={ENABLED!r}, on_change=save_field, args=("fixture", {ENABLED!r}))
st.button("Unrelated rerun", key="unrelated")
'''
    return AppTest.from_string(source).run()


def _application(tmp_path):
    return prompt_library_application(root=tmp_path, user_key="fixture")


def _key(name):
    return prompt_library_key(WORKSPACE, SCOPE, name)


def test_selection_applies_immediately_and_persists_all_editor_fields(tmp_path):
    app = _application(tmp_path)
    saved = app.save_template(SCOPE, "My literal prompt", {"text": "  Keep {source} exactly.\n"})
    ui = _ui(tmp_path)
    ui.selectbox(key=_key("selection")).set_value(saved["id"]).run()
    assert not ui.exception
    assert ui.text_area(key=BODY).value == "  Keep {source} exactly.\n"
    assert ui.toggle(key=ENABLED).value is True
    assert ui.session_state["workflow-form-draft:fixture"] == {
        BODY: "  Keep {source} exactly.\n", ENABLED: True,
    }


def test_rerenders_and_none_selection_keep_current_edits(tmp_path):
    app = _application(tmp_path)
    saved = app.save_template(SCOPE, "Reuse", {"text": "Saved version."})
    ui = _ui(tmp_path)
    ui.selectbox(key=_key("selection")).set_value(saved["id"]).run()
    ui.text_area(key=BODY).set_value("My unfinished edit.").run()
    app.update_template(saved["id"], SCOPE, "Reuse", {"text": "A newer library version."}, 1)
    ui.button(key="unrelated").click().run()
    assert ui.text_area(key=BODY).value == "My unfinished edit."
    assert ui.session_state[_key("loaded")]["revision"] == 1
    ui.selectbox(key=_key("selection")).set_value(None).run()
    assert not ui.exception
    assert ui.text_area(key=BODY).value == "My unfinished edit."
    assert ui.button(key=_key("update")).disabled


def test_restore_defaults_changes_draft_without_modifying_personal_template(tmp_path):
    app = _application(tmp_path)
    saved = app.save_template(SCOPE, "Keep forever", {"text": "Saved custom text."})
    ui = _ui(tmp_path)
    ui.selectbox(key=_key("selection")).set_value(saved["id"]).run()
    ui.button(key=_key("restore")).click().run()
    assert not ui.exception
    assert ui.text_area(key=BODY).value == "Builtin default."
    assert ui.toggle(key=ENABLED).value is False
    assert ui.selectbox(key=_key("selection")).value is None
    assert ui.session_state["workflow-form-draft:fixture"][BODY] == "Builtin default."
    assert app.get_template(saved["id"], SCOPE)["payload"] == {"text": "Saved custom text."}
    assert app.get_template(saved["id"], SCOPE)["revision"] == 1


def test_restore_is_available_when_only_extra_toggle_is_custom(tmp_path):
    ui = _ui(tmp_path)
    assert ui.button(key=_key("restore")).disabled
    ui.toggle(key=ENABLED).set_value(True).run()
    assert not ui.button(key=_key("restore")).disabled
    ui.button(key=_key("restore")).click().run()
    assert ui.toggle(key=ENABLED).value is False
    assert not ui.exception


def test_save_reads_authoritative_draft_and_duplicate_name_never_overwrites(tmp_path):
    app = _application(tmp_path)
    ui = _ui(tmp_path)
    ui.text_area(key=BODY).set_value("Saved directly from draft.").run()
    ui.text_input(key=_key("name")).set_value("My template").run()
    ui.button(key=_key("save")).click().run()
    records = app.list_templates(SCOPE)
    assert len(records) == 1
    identifier = records[0]["id"]
    assert ui.selectbox(key=_key("selection")).value == identifier
    assert app.get_template(identifier, SCOPE)["payload"] == {"text": "Saved directly from draft."}
    ui.text_area(key=BODY).set_value("Do not overwrite without permission.").run()
    ui.button(key=_key("save")).click().run()
    assert not ui.exception
    assert len(app.list_templates(SCOPE)) == 1
    assert app.get_template(identifier, SCOPE)["payload"] == {"text": "Saved directly from draft."}
    assert ui.warning[0].value == PROMPT_LIBRARY_ERRORS["prompt_library_name_conflict"]


def test_stale_update_does_not_use_revision_from_refreshed_metadata(tmp_path):
    app = _application(tmp_path)
    saved = app.save_template(SCOPE, "Shared personal template", {"text": "Original."})
    ui = _ui(tmp_path)
    ui.selectbox(key=_key("selection")).set_value(saved["id"]).run()
    app.update_template(saved["id"], SCOPE, saved["name"], {"text": "Another window edit."}, 1)
    ui.run()
    ui.text_area(key=BODY).set_value("Stale editor change.").run()
    ui.button(key=_key("update")).click().run()
    assert not ui.exception
    assert ui.warning[0].value == PROMPT_LIBRARY_ERRORS["prompt_library_revision_conflict"]
    assert app.get_template(saved["id"], SCOPE)["payload"] == {"text": "Another window edit."}
    assert ui.text_area(key=BODY).value == "Stale editor change."
    assert ui.selectbox(key=_key("selection")).value is None
    assert ui.text_input(key=_key("name")).value == saved["name"]
    assert _key("loaded") not in ui.session_state
    ui.selectbox(key=_key("selection")).set_value(saved["id"]).run()
    assert ui.text_area(key=BODY).value == "Another window edit."
    assert ui.session_state[_key("loaded")]["revision"] == 2


def test_explicit_update_changes_selected_template_and_tracks_new_revision(tmp_path):
    app = _application(tmp_path)
    saved = app.save_template(SCOPE, "Editable", {"text": "Original."})
    ui = _ui(tmp_path)
    ui.selectbox(key=_key("selection")).set_value(saved["id"]).run()
    ui.text_area(key=BODY).set_value("Explicit update.").run()
    assert app.get_template(saved["id"], SCOPE)["payload"] == {"text": "Original."}
    ui.button(key=_key("update")).click().run()
    assert not ui.exception
    assert app.get_template(saved["id"], SCOPE)["payload"] == {"text": "Explicit update."}
    assert ui.session_state[_key("loaded")] == {"id": saved["id"], "revision": 2}


def test_absent_library_application_keeps_legacy_editors_usable(tmp_path):
    ui = _ui(tmp_path, application="None")
    assert not ui.exception
    assert len(ui.selectbox) == 0
    assert ui.text_area(key=BODY).value == "Builtin default."


def test_storage_failure_keeps_editor_usable_without_leaking_error_details(tmp_path):
    source = '''
class Unavailable:
    def list_templates(self, scope):
        raise ValueError("private path / token should not escape")
application = Unavailable()
'''
    ui = _ui(tmp_path, prelude=source)
    assert not ui.exception
    assert ui.text_area(key=BODY).value == "Builtin default."
    assert ui.warning[0].value == PROMPT_LIBRARY_ERRORS["prompt_library_unavailable"]


def test_unavailable_library_still_restores_defaults_and_persists_draft(tmp_path):
    source = '''
class Unavailable:
    def list_templates(self, scope):
        raise OSError("Library unavailable")
application = Unavailable()
'''
    ui = _ui(tmp_path, prelude=source)
    ui.text_area(key=BODY).set_value("An unfinished custom prompt.").run()
    ui.toggle(key=ENABLED).set_value(True).run()
    assert not ui.button(key=_key("restore")).disabled
    ui.button(key=_key("restore")).click().run()
    assert not ui.exception
    assert ui.text_area(key=BODY).value == "Builtin default."
    assert ui.toggle(key=ENABLED).value is False
    assert ui.session_state["workflow-form-draft:fixture"] == {
        BODY: "Builtin default.", ENABLED: False,
    }
    assert ui.warning[0].value == PROMPT_LIBRARY_ERRORS["prompt_library_unavailable"]
    assert len(ui.selectbox) == 0
    assert not any(button.key in {_key("save"), _key("update")} for button in ui.button)


def test_user_template_name_remains_literal_in_english_ui(tmp_path):
    app = _application(tmp_path)
    app.save_template(SCOPE, "提示词正文", {"text": "Literal body."})
    ui = _ui(tmp_path, language="en", prelude='''
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
''')
    assert not ui.exception
    assert "提示词正文" in ui.selectbox(key=_key("selection")).options
