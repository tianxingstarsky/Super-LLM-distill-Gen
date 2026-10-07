"""Open durable unfinished work from the work manager."""
from __future__ import annotations

import html

import streamlit as st
from filelock import Timeout

from lib.application.creation_draft_service import CreationDraftApplication
from lib.presentation.streamlit.i18n import UntranslatedText, translate


def _restore(application, identifier, workspace):
    try:
        # Restoring replaces the automatic draft on disk. Preserve unfinished
        # work first, including changes that were never saved as a named draft.
        current = application.load()
        backup = application.save_snapshot() if current else None
        values = application.restore_snapshot(identifier)
    except (OSError, ValueError, Timeout):
        st.session_state[f"work-drafts-error:{workspace}"] = True
        return
    # Widget and node choices from another unfinished task must not bleed into
    # the restored draft. Running jobs and their inspectors stay untouched.
    for key in list(st.session_state):
        if (isinstance(key, str) and f":{workspace}" in key
                and (key.startswith("workflow-") or key.startswith("node-model:"))):
            st.session_state.pop(key, None)
    st.session_state[f"workflow-form-draft:{workspace}"] = values
    st.session_state[f"workflow-draft-loaded:{workspace}"] = True
    if backup:
        st.session_state[f"work-draft-switch-backup:{workspace}"] = backup
    else:
        st.session_state.pop(f"work-draft-switch-backup:{workspace}", None)
    st.session_state.pop(f"work-drafts-error:{workspace}", None)
    st.session_state["nav"] = "自动工作流"


def _page(key, delta):
    st.session_state[key] = max(0, st.session_state.get(key, 0) + delta)


def render_work_drafts(application: CreationDraftApplication, workspace: str, navigate):
    st.html('''<style>
.st-key-work-drafts {padding:9px 0 12px;border-bottom:1px solid #dce6f1;margin-bottom:8px}
.df-draft-current {min-width:0;line-height:1.35}
.df-draft-current small {display:block;color:#7890a7;font-size:10px;margin-bottom:3px}
.df-draft-current strong {display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  color:#284662;font-size:12px;font-weight:650}
.st-key-work-drafts .stButton button {min-height:34px;height:auto;padding:6px 8px;font-size:12px}
.st-key-work-drafts .stButton button [data-testid="stMarkdownContainer"],
.st-key-work-drafts .stButton button p {white-space:normal;overflow:visible;text-overflow:clip;
  font-size:12px;line-height:1.35}
.st-key-work-drafts [data-testid="stHorizontalBlock"]:has([class*="st-key-work-drafts-group-"]) {
  flex-wrap:wrap;row-gap:8px}
.st-key-work-drafts [data-testid="stColumn"]:has(.st-key-work-drafts-group-current) {
  flex:2.4 1 260px;min-width:min(260px,100%)}
.st-key-work-drafts [data-testid="stColumn"]:has(.st-key-work-drafts-group-saved) {
  flex:3.9 1 330px;min-width:min(330px,100%)}
.st-key-work-drafts [data-testid="stColumn"]:has(.st-key-work-drafts-group-return) {
  flex:1.7 1 160px;min-width:min(160px,100%)}
</style>''')
    with st.container(border=False, key="work-drafts"):
        try:
            current = application.load()
            page_key = f"work-drafts-page:{workspace}"
            page = st.session_state.get(page_key, 0)
            saved = application.snapshots(limit=21, offset=page * 20)
        except (OSError, ValueError, Timeout):
            st.warning("无法读取工作草稿，请检查本机存储后重试。")
            return
        language = st.session_state.get("ui_language", "zh")
        name = current.get(f"workflow-name:{workspace}") or translate("尚未命名的工作", language)
        previous = st.session_state.get(f"work-draft-switch-backup:{workspace}")
        columns = st.columns([2.4, 3.9, 1.7] if previous else [2.4, 3.9],
                             gap="small", vertical_alignment="center")
        with columns[0], st.container(key="work-drafts-group-current"):
            title, action = st.columns([1.15, 1.25], gap="small", vertical_alignment="center")
            with title:
                st.html('<div class="df-draft-current"><small>当前自动保存</small>'
                        f'<strong data-user-content title="{html.escape(str(name), quote=True)}">'
                        f'{html.escape(str(name))}</strong></div>')
            with action:
                st.button("继续当前草稿", key=f"work-draft-current:{workspace}",
                          on_click=navigate, args=("自动工作流",), width="stretch",
                          help="草稿和上传资料保存在本机，关闭或重启后仍可继续。")
        by_id = {item["id"]: item for item in saved[:20]}

        def label(identifier):
            item = by_id[identifier]
            draft_name = item.get("name") or translate("尚未命名的工作", language)
            moment = str(item.get("created_at", "")).replace("T", " ")[:16]
            return UntranslatedText(f"{draft_name} · {moment} UTC")

        with columns[1], st.container(key="work-drafts-group-saved"):
            choice, action = st.columns([2.65, 1.25], gap="small", vertical_alignment="center")
            with choice:
                selected = st.selectbox("已保存的独立草稿", list(by_id), format_func=label,
                                        key=f"work-draft-selected:{workspace}:{page}",
                                        label_visibility="collapsed", disabled=not by_id,
                                        placeholder=translate("已保存的独立草稿", language))
            with action:
                st.button("打开所选草稿", key=f"work-draft-open:{workspace}", disabled=not by_id,
                          on_click=_restore, args=(application, selected, workspace), width="stretch",
                          help="切换前会自动保留当前草稿，可在这里找回。")
        if previous:
            with columns[2], st.container(key="work-drafts-group-return"):
                st.button("返回切换前草稿", key=f"work-draft-return:{workspace}",
                          on_click=_restore, args=(application, previous, workspace), width="stretch")
        if page or len(saved) > 20:
            _, previous_page, following_page = st.columns([4, 1, 1], gap="small")
            previous_page.button("上一页草稿", disabled=page == 0, key=page_key + ":prev",
                                 on_click=_page, args=(page_key, -1), width="stretch")
            following_page.button("下一页草稿", disabled=len(saved) <= 20, key=page_key + ":next",
                                  on_click=_page, args=(page_key, 1), width="stretch")
        if st.session_state.get(f"work-drafts-error:{workspace}"):
            st.error("草稿未能打开，现有工作未改变。请检查本机存储后重试。")
