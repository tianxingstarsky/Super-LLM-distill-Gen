"""Read a persisted run's report in the same workbench as its execution."""
from __future__ import annotations

import streamlit as st

from lib.presentation.streamlit.i18n import UntranslatedText, translate


VIEW_OPTIONS = ("运行过程", "样本报告")
PAGE_SIZES = (6, 12, 24)
TARGET_LABELS = {"cpt": "CPT", "sft": "SFT", "cot": "CoT", "multiturn": "多轮对话",
                 "dpo": "DPO", "orpo": "ORPO", "rlaif": "RLAIF", "agent": "Agent",
                 "gsm8k": "GSM8K", "agent_negative": "Agent 失败轨迹"}


def _view_key(run_id: str) -> str:
    return f"workflow-result-view:{run_id}"


def _switch_view(run_id: str, *, embedded: bool) -> None:
    """Expand report reading without a new route or changing the selected run."""
    if embedded:
        workspace = st.session_state.get("ws")
        if workspace:
            focus_key = f"task-center-focus:{workspace}"
            prior_key = f"workflow-report-previous-focus:{workspace}:{run_id}"
            if st.session_state.get(_view_key(run_id)) == "样本报告":
                st.session_state.setdefault(prior_key, bool(st.session_state.get(focus_key, False)))
                st.session_state[focus_key] = True
            elif prior_key in st.session_state:
                st.session_state[focus_key] = st.session_state.pop(prior_key)


def _show_process(run_id: str, embedded: bool) -> None:
    st.session_state[_view_key(run_id)] = "运行过程"
    _switch_view(run_id, embedded=embedded)


def render_run_report_choice(application, run_id: str, *, embedded: bool = False) -> bool:
    """Keep old/non-report application ports usable without extra UI calls."""
    if getattr(application, "reports", None) is None:
        return False
    key = _view_key(run_id)
    if key in st.session_state and st.session_state[key] not in VIEW_OPTIONS:
        st.session_state.pop(key)
    mode = st.segmented_control("任务视图", VIEW_OPTIONS, default="运行过程", key=key,
                                label_visibility="collapsed", width="stretch",
                                on_change=_switch_view, args=(run_id,), kwargs={"embedded": embedded})
    return mode == "样本报告"


def _page_value(key: str) -> int:
    value = st.session_state.get(key, 1)
    return max(1, value) if type(value) is int else 1


def _move_page(key: str, value: int) -> None:
    st.session_state[key] = value


def _page_from_editor(page_key: str, editor_key: str) -> None:
    st.session_state[page_key] = _page_value(editor_key)


def _retry_report(reports, run_id: str, target: str) -> None:
    reports.retry(run_id, target)


def _render_preparation(reports, run_id: str, target: str, page: dict, *, embedded: bool) -> None:
    preparation = page.get("preparation") or {}
    if preparation.get("status") == "failed":
        st.error("报告校验或索引准备失败。训练产物未被修改，可重试或查看运行过程。")
        retry, process = st.columns(2)
        retry.button("重新准备报告", key=f"workflow-report-retry:{run_id}:{target}",
                     on_click=_retry_report, args=(reports, run_id, target),
                     disabled=not preparation.get("retryable", False), width="stretch")
        process.button("查看运行过程", key=f"workflow-report-prepare-process:{run_id}",
                       on_click=_show_process, args=(run_id, embedded), width="stretch")
    else:
        st.info("正在准备报告：后台校验并建立分页索引。你可以切换页面，准备完成后会自动显示。")


def render_workflow_report(application, run_id: str, *, embedded: bool = False) -> None:
    """Poll only live output or preparation; a finished reading page is stable."""
    refresh_key = f"workflow-report-refresh:{run_id}"
    refresh = st.session_state.get(refresh_key, True)

    @st.fragment(run_every=2 if refresh else None)
    def report_fragment():
        if st.session_state.get(_view_key(run_id)) != "样本报告":
            st.rerun(scope="app")
        needs_refresh = _render_workflow_report(application, run_id, embedded=embedded)
        if needs_refresh != refresh:
            st.session_state[refresh_key] = needs_refresh
            st.rerun(scope="app")

    report_fragment()


def _render_workflow_report(application, run_id: str, *, embedded: bool = False) -> bool:
    """Display a bounded report page; downloading never starts generation."""
    reports = application.reports
    language = st.session_state.get("ui_language", "zh")
    if language not in {"zh", "en"}:
        language = "zh"
    try:
        overview = reports.overview(run_id)
        targets = [row["target"] for row in overview.get("targets", []) if row.get("target")]
        if not targets:
            st.info("尚无可展示的样本。运行过程和已保存记录仍可查看。")
            return bool(overview.get("active"))
        controls = st.columns([2.2, 1, 1.3, 1.8], gap="small", vertical_alignment="bottom")
        target_key = f"workflow-report-target:{run_id}"
        if target_key in st.session_state and st.session_state[target_key] not in targets:
            st.session_state.pop(target_key)
        with controls[0]:
            target = st.selectbox("报告类型", targets, key=target_key,
                                  format_func=lambda value: translate(TARGET_LABELS.get(value, value.upper()), language))
        with controls[1]:
            limit = st.selectbox("每页样本", PAGE_SIZES, key=f"workflow-report-limit:{run_id}")
        page_key = f"workflow-report-page:{run_id}"
        signature_key = page_key + ":selection"
        signature = (target, limit)
        if st.session_state.get(signature_key) != signature:
            st.session_state[page_key] = 1
            st.session_state[signature_key] = signature
        number = _page_value(page_key)
        page = reports.samples(run_id, target, offset=(number - 1) * limit, limit=limit)
        if page.get("indexing") or (page.get("preparation") or {}).get("status") == "failed":
            _render_preparation(reports, run_id, target, page, embedded=embedded)
            return (page.get("preparation") or {}).get("status") != "failed"
        if page.get("finalized") and not overview.get("finalized"):
            overview = reports.overview(run_id)
        total = max(0, int(page.get("total") or len(page.get("rows", []))))
        pages = max(1, (total + limit - 1) // limit)
        if number > pages:
            number = pages
            page = reports.samples(run_id, target, offset=(number - 1) * limit, limit=limit)
            if page.get("indexing") or (page.get("preparation") or {}).get("status") == "failed":
                st.session_state[page_key] = number
                _render_preparation(reports, run_id, target, page, embedded=embedded)
                return (page.get("preparation") or {}).get("status") != "failed"
        st.session_state[page_key] = number
        # A cold page temporarily omits the editor. Keep its selected cursor in
        # non-widget state so Streamlit's widget cleanup cannot reset the jump.
        editor_key = page_key + ":input"
        st.session_state[editor_key] = number
        with controls[2]:
            st.number_input("报告页码", min_value=1, max_value=pages, step=1, key=editor_key,
                            disabled=pages == 1, on_change=_page_from_editor, args=(page_key, editor_key))
        # A report snapshot includes this page's complete text. It is not an
        # implied whole-dataset export and never reuses a different page body.
        document = reports.html_snapshot(overview, page, language=language)
        with controls[3]:
            st.download_button("下载本页报告", document.encode("utf-8"),
                               file_name=f"{run_id[:8]}-{target}-page-{number}.html",
                               mime="text/html", key=f"workflow-report-download:{run_id}:{target}:{number}",
                               on_click="ignore", width="stretch")
        previous, caption, following = st.columns([1, 4, 1], gap="small", vertical_alignment="center")
        previous.button("上一页", key=page_key + ":previous", disabled=number <= 1,
                        on_click=_move_page, args=(page_key, number - 1), width="stretch")
        with caption:
            if page.get("finalized"):
                st.caption(UntranslatedText(f"Page {number:,} / {pages:,} · {total:,} records" if language == "en"
                                            else f"第 {number:,} / {pages:,} 页 · 共 {total:,} 条记录"))
            else:
                st.caption("未封存结果仅展示最近已保存输出，完整报告在打包后可分页查看。")
        following.button("下一页", key=page_key + ":next", disabled=number >= pages,
                         on_click=_move_page, args=(page_key, number + 1), width="stretch")
        st.html(reports.html_embedded_snapshot(overview, page, language=language))
        return bool(overview.get("active") or (page.get("summary") or {}).get("indexing"))
    except (KeyError, OSError, TypeError, ValueError):
        st.error("报告暂时无法读取，请在运行过程中检查任务产物。")
        st.button("查看运行过程", key=f"workflow-report-error-process:{run_id}",
                  on_click=_show_process, args=(run_id, embedded), width="stretch")
        return False
