"""Open durable unfinished work from the work manager."""
from __future__ import annotations

import html

import streamlit as st
from filelock import Timeout

from lib.application.creation_draft_service import CreationDraftApplication
from lib.presentation.streamlit.i18n import UntranslatedText, translate
from lib.presentation.streamlit.shared import section_heading


def _restore(application, identifier, workspace):
    try:
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
    st.session_state.pop(f"work-drafts-error:{workspace}", None)
    st.session_state["nav"] = "自动工作流"


def _page(key, delta):
    st.session_state[key] = max(0, st.session_state.get(key, 0) + delta)


def render_work_drafts(application: CreationDraftApplication, workspace: str, navigate):
    with st.container(border=True, key="work-drafts"):
        section_heading("未开始的工作", "草稿和上传资料保存在本机，关闭或重启后仍可继续。", "✎")
        try:
            current = application.load()
            page_key = f"work-drafts-page:{workspace}"
            page = st.session_state.get(page_key, 0)
            saved = application.snapshots(limit=21, offset=page * 20)
        except (OSError, ValueError, Timeout):
            st.warning("无法读取工作草稿，请检查本机存储后重试。")
            return
        left, right = st.columns([1, 1.7], gap="large")
        with left:
            name = current.get(f"workflow-name:{workspace}") or translate(
                "尚未命名的工作", st.session_state.get("ui_language", "zh"))
            st.html('<div class="df-section"><div><strong>当前自动保存</strong>'
                    f'<p data-user-content>{html.escape(str(name))}</p></div></div>')
            st.button("继续当前草稿", key=f"work-draft-current:{workspace}",
                      on_click=navigate, args=("自动工作流",), width="stretch")
        with right:
            if saved:
                language = st.session_state.get("ui_language", "zh")
                by_id = {item["id"]: item for item in saved[:20]}

                def label(identifier):
                    item = by_id[identifier]
                    name = item.get("name") or translate("尚未命名的工作", language)
                    moment = str(item.get("created_at", "")).replace("T", " ")[:19]
                    return UntranslatedText(f"{name} · {moment} UTC")

                selected = st.selectbox("已保存的独立草稿", list(by_id), format_func=label,
                                        key=f"work-draft-selected:{workspace}:{page}")
                st.button("打开所选草稿", key=f"work-draft-open:{workspace}",
                          on_click=_restore, args=(application, selected, workspace), width="stretch")
            else:
                st.caption("可在生成页保存独立草稿，分别准备多份工作。")
            if page or len(saved) > 20:
                previous, following = st.columns(2)
                previous.button("上一页草稿", disabled=page == 0, key=page_key + ":prev",
                                on_click=_page, args=(page_key, -1), width="stretch")
                following.button("下一页草稿", disabled=len(saved) <= 20, key=page_key + ":next",
                                 on_click=_page, args=(page_key, 1), width="stretch")
        if st.session_state.get(f"work-drafts-error:{workspace}"):
            st.error("草稿未能打开，现有工作未改变。请检查本机存储后重试。")
