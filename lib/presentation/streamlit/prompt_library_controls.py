"""Small, node-local controls for the user's durable prompt templates."""
from __future__ import annotations

from copy import deepcopy

import streamlit as st

from lib.presentation.streamlit.i18n import UntranslatedText, translate


PROMPT_LIBRARY_ERRORS = {
    "prompt_library_name_required": "请填写模板名称。",
    "prompt_library_invalid_name": "模板名称无效，请使用简短的文字。",
    "prompt_library_name_too_long": "模板名称过长，请精简后保存。",
    "prompt_library_name_exists": "已有同名模板，请换个名称，或选择该模板后更新。",
    "prompt_library_duplicate_name": "已有同名模板，请换个名称，或选择该模板后更新。",
    "prompt_library_name_conflict": "已有同名模板，请换个名称，或选择该模板后更新。",
    "prompt_library_not_found": "模板已不存在，请重新选择或另存为新模板。",
    "prompt_library_revision_conflict": "该模板已在另一处更新。请重新选择以载入新版本，或另存为新模板。",
    "prompt_library_conflict": "该模板已在另一处更新。请重新选择以载入新版本，或另存为新模板。",
    "prompt_library_invalid_payload": "模板内容无效，请检查提示词后重新保存。",
    "prompt_library_invalid_scope": "此模板不适用于当前处理步骤。",
    "prompt_library_busy": "模板库正在被其他操作使用，请稍后重试。",
    "prompt_library_unavailable": "无法读取或保存模板。当前编辑内容仍保留，请稍后重试。",
    "prompt_library_draft_save_failed": "模板已应用，但工作草稿未能保存。当前修改仍保留在会话中。",
}
PROMPT_LIBRARY_NOTICES = {
    "applied": "已套用所选模板，可继续编辑。",
    "saved": "已保存为新模板。",
    "updated": "已更新所选模板。",
    "restored": "已恢复默认模板，已保存的自定义模板保持不变。",
}


def prompt_library_key(workspace: str, scope: str, name: str) -> str:
    return f"prompt-library:{workspace}:{scope}:{name}"


def clear_prompt_template_selection(workspace: str, scope: str) -> None:
    """Detach the current editor from a library selection after an external reset."""
    st.session_state[prompt_library_key(workspace, scope, "selection")] = None
    for name in ("loaded", "error", "notice"):
        st.session_state.pop(prompt_library_key(workspace, scope, name), None)


def _field(workspace, key):
    return st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(
        key, st.session_state.get(key))


def _message(workspace, scope, *, error=None, notice=None):
    error_key = prompt_library_key(workspace, scope, "error")
    notice_key = prompt_library_key(workspace, scope, "notice")
    st.session_state.pop(error_key, None)
    st.session_state.pop(notice_key, None)
    if error:
        st.session_state[error_key] = PROMPT_LIBRARY_ERRORS.get(
            str(error), PROMPT_LIBRARY_ERRORS["prompt_library_unavailable"])
    if notice:
        st.session_state[notice_key] = PROMPT_LIBRARY_NOTICES[notice]


def _write_fields(workspace, scope, values, save_field):
    # Set every field before persisting, so callbacks see one coherent template.
    for key, value in values.items():
        st.session_state[key] = deepcopy(value)
    failed = False
    for key in values:
        try:
            save_field(workspace, key)
        except Exception:
            failed = True
    if failed:
        _message(workspace, scope, error="prompt_library_draft_save_failed")
    return not failed


def _remember_record(workspace, scope, record):
    st.session_state[prompt_library_key(workspace, scope, "selection")] = record["id"]
    st.session_state[prompt_library_key(workspace, scope, "loaded")] = {
        "id": record["id"], "revision": record["revision"],
    }
    st.session_state[prompt_library_key(workspace, scope, "name")] = record["name"]


def _apply(application, workspace, scope, fields, save_field, apply_extras):
    _message(workspace, scope)
    selected = st.session_state.get(prompt_library_key(workspace, scope, "selection"))
    if selected is None:
        st.session_state.pop(prompt_library_key(workspace, scope, "loaded"), None)
        return
    try:
        record = application.get_template(selected, scope)
        payload = record["payload"]
        if not isinstance(payload, dict) or any(name not in payload for name in fields):
            raise ValueError("prompt_library_invalid_payload")
        values = {key: payload[name] for name, key in fields.items()}
        values.update(apply_extras or {})
        # Validate record metadata before touching the user's current editor.
        record["id"], record["revision"], record["name"]
    except Exception as error:
        _message(workspace, scope, error=error)
        return
    _remember_record(workspace, scope, record)
    if _write_fields(workspace, scope, values, save_field):
        _message(workspace, scope, notice="applied")


def _save(application, workspace, scope, fields, *, update):
    _message(workspace, scope)
    name = st.session_state.get(prompt_library_key(workspace, scope, "name"), "")
    # A save click can arrive with a textarea change in the same widget batch.
    # Streamlit updates widget state before callbacks, while the draft callback
    # may run after this button's callback. Save the newest editor state.
    payload = {name: deepcopy(st.session_state[key] if key in st.session_state
                              else _field(workspace, key)) for name, key in fields.items()}
    try:
        if update:
            selected = st.session_state.get(prompt_library_key(workspace, scope, "selection"))
            loaded = st.session_state.get(prompt_library_key(workspace, scope, "loaded"), {})
            if not selected or loaded.get("id") != selected:
                raise ValueError("prompt_library_not_found")
            record = application.update_template(
                selected, scope, name, payload, expected_revision=loaded["revision"])
        else:
            record = application.save_template(scope, name, payload)
        _remember_record(workspace, scope, record)
    except Exception as error:
        if str(error) == "prompt_library_revision_conflict":
            # Keep the draft and save name, but let selecting the same template
            # again trigger an explicit reload of its newer library version.
            clear_prompt_template_selection(workspace, scope)
        _message(workspace, scope, error=error)
        return
    _message(workspace, scope, notice="updated" if update else "saved")


def _restore(workspace, scope, fields, defaults, save_field, restore_extras):
    values = {key: defaults[name] for name, key in fields.items() if name in defaults}
    values.update(restore_extras or {})
    # Restoring only changes the active draft. No saved template is modified.
    clear_prompt_template_selection(workspace, scope)
    if _write_fields(workspace, scope, values, save_field):
        _message(workspace, scope, notice="restored")


def _render_restore(workspace, scope, fields, defaults, save_field, restore_extras):
    if defaults is None:
        return
    expected = {key: defaults[name] for name, key in fields.items() if name in defaults}
    expected.update(restore_extras or {})
    custom = any(_field(workspace, key) != value for key, value in expected.items())
    st.button("恢复默认模板", key=prompt_library_key(workspace, scope, "restore"),
              disabled=not custom, width="stretch", on_click=_restore,
              args=(workspace, scope, fields, defaults, save_field, restore_extras))


def render_prompt_library(application, workspace: str, scope: str, *, fields: dict,
                          save_field, defaults: dict | None = None,
                          apply_extras: dict | None = None, restore_extras: dict | None = None,
                          label: str = "我的模板", save_label: str = "保存模板") -> None:
    """Render before bound editor widgets; template selection applies immediately.

    ``fields`` maps persisted payload fields to the editor's session-state keys.
    A refreshed list never updates the revision that the user actually loaded.
    """
    if application is None:
        return
    try:
        metadata = application.list_templates(scope)
        by_id = {item["id"]: item for item in metadata}
    except Exception as error:
        _message(workspace, scope, error=error)
        st.warning(st.session_state[prompt_library_key(workspace, scope, "error")])
        _render_restore(workspace, scope, fields, defaults, save_field, restore_extras)
        return
    selection_key = prompt_library_key(workspace, scope, "selection")
    loaded_key = prompt_library_key(workspace, scope, "loaded")
    if st.session_state.get(selection_key) not in by_id:
        st.session_state[selection_key] = None
        st.session_state.pop(loaded_key, None)
    language = st.session_state.get("ui_language", "zh")

    def choice_label(identifier):
        if identifier is None:
            return translate("当前编辑（未套用模板）", language)
        return UntranslatedText(by_id[identifier]["name"])

    picker, actions = st.columns([2.3, 1], gap="small", vertical_alignment="bottom")
    with picker:
        st.selectbox(label, [None, *by_id], key=selection_key, format_func=choice_label,
                     placeholder="选择已保存模板" if by_id else "尚无个人模板", disabled=not by_id,
                     label_visibility="collapsed", help="选择即套用；当前编辑不会自动覆盖已保存模板。",
                     on_change=_apply,
                     args=(application, workspace, scope, fields, save_field, apply_extras))
    with actions, st.popover(save_label, width="stretch"):
        st.text_input("模板名称", key=prompt_library_key(workspace, scope, "name"), max_chars=80)
        st.button("另存为新模板", key=prompt_library_key(workspace, scope, "save"), width="stretch",
                  on_click=_save, args=(application, workspace, scope, fields), kwargs={"update": False})
        selected = st.session_state.get(selection_key)
        loaded = st.session_state.get(loaded_key, {})
        st.button("更新所选模板", key=prompt_library_key(workspace, scope, "update"), width="stretch",
                  disabled=not selected or loaded.get("id") != selected,
                  on_click=_save, args=(application, workspace, scope, fields), kwargs={"update": True},
                  help="仅点击此按钮时覆盖模板，其他使用位置不会自动改变。")
    _render_restore(workspace, scope, fields, defaults, save_field, restore_extras)
    if error := st.session_state.get(prompt_library_key(workspace, scope, "error")):
        st.warning(error)
    elif notice := st.session_state.get(prompt_library_key(workspace, scope, "notice")):
        st.caption(notice)
