"""Command-event monitoring presentation with explicit application inputs."""
import html
import streamlit as st
from lib.application.monitor_service import MonitorApplication
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.i18n import UntranslatedText, translate_label


def render_monitor_page(application: MonitorApplication, workspace_id: str, job_status, *, show_title=True):
    if show_title:
        page_header("运行监控", "跟踪命令任务状态和最近输出，快速定位失败阶段。", "RUN MONITOR")
    kind_key = f"monitor-kind:{workspace_id}"
    language = st.session_state.get("ui_language", "zh")
    try:
        snapshot = application.snapshot(st.session_state.get(kind_key))
        if st.session_state.get(kind_key) not in [None, *snapshot["kinds"]]:
            st.session_state[kind_key] = None
            snapshot = application.snapshot()
    except (OSError, ValueError) as error:
        st.error(f"Cannot read run history: {error}" if language == "en" else f"无法读取运行日志：{error}")
        job_status()
        return
    invalid = snapshot["invalid"]
    if invalid:
        st.warning(f"{invalid} log records could not be read. Only valid events are shown." if language == "en"
                   else f"日志中有 {invalid} 条记录无法读取，以下只展示有效事件。")
    if snapshot["count"]:
        st.html('''<style>
        .df-event-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:0 0 14px; }
        .df-event-summary > div { display:grid; gap:5px; min-height:82px; padding:14px 16px; border:1px solid #DFE8F4; border-radius:11px; background:#fff; }
        .df-event-summary span { color:#71839A; font-size:12px; }
        .df-event-summary strong { overflow:hidden; color:#1C3453; font-size:20px; text-overflow:ellipsis; white-space:nowrap; }
        .df-event-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; margin:10px 0; }
        .df-event-facts > div { min-width:0; padding:10px 12px; border:1px solid #E3EBF6; border-radius:9px; background:#F9FBFF; }
        .df-event-facts span { display:block; color:#71839A; font-size:11px; }
        .df-event-facts strong { display:block; margin-top:4px; overflow-wrap:anywhere; color:#263C57; font-size:13px; }
        @media(max-width:800px) { .df-event-summary,.df-event-facts { grid-template-columns:1fr; } }
        </style>''')
        recent = snapshot["events"]
        kinds = snapshot["kinds"]
        st.html('<div class="df-event-summary">'
                f'<div><span>记录总数</span><strong>{snapshot["count"]:,}</strong></div>'
                f'<div><span>事件类型</span><strong>{len(kinds):,}</strong></div>'
                '<div><span>最近记录</span><strong>'
                + html.escape(str(snapshot.get("latest_at") or "—")[:19].replace("T", " "))
                + '</strong></div></div>')
        left, right = st.columns([1, 2], gap="large")
        with left, st.container(border=True):
            section_heading("事件时间线", "按类型筛选并选择一条记录", "◷")
            kind = st.selectbox("事件类型", [None, *kinds], key=kind_key,
                                format_func=lambda value: "全部类型" if value is None else
                                translate_label("未知类型", language) if value == "" else UntranslatedText(value))
            visible = recent
            st.caption(f"显示最近 {len(visible)} / {snapshot['count']} 条")
            event_key = f"monitor-event:{workspace_id}:{kind}"
            if st.session_state.get(event_key, 0) not in range(len(visible)):
                st.session_state[event_key] = 0
            with st.container(height=510, border=False):
                selected = st.radio(
                    "选择事件", list(range(len(visible))), label_visibility="collapsed",
                    format_func=lambda index: UntranslatedText(
                        f"{visible[index].get('kind') or translate_label('未知类型', language)} · "
                        f"{str(visible[index].get('at') or translate_label('时间未知', language))[:19].replace('T', ' ')}"
                    ), key=event_key,
                )
        with right, st.container(border=True):
            section_heading("事件详情", "显示原始记录中的实际字段", "▤")
            event = visible[selected]
            facts = []
            for key, value in event.items():
                if key in ("kind", "at") or isinstance(value, (dict, list)):
                    continue
                facts.append('<div><span data-user-content>' + html.escape(str(key)) + '</span><strong data-user-content>'
                             + html.escape(str(value)[:200]) + '</strong></div>')
            if facts:
                st.html('<div class="df-event-facts">' + ''.join(facts) + '</div>')
            else:
                st.caption("该事件只有类型和时间，可展开查看原始记录。")
            with st.expander("查看原始事件记录"):
                st.json(event)
    else:
        st.html('<div class="df-empty-state"><span class="df-empty-state-icon">◷</span><strong>本机暂无运行日志</strong><p>任务启动后，这里会显示阶段、耗时与执行结果。</p></div>')
    job_status()

