"""Quality presentation composed with explicit applications and workspace inputs."""
import html
import json
from pathlib import Path
import streamlit as st
from lib.application.release_service import ReleaseApplication
from lib.application.workflow_service import WorkflowApplication
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.workflow_quality_page import render_workflow_quality
from lib.render import render_message_sequence
from lib.presentation.streamlit.i18n import translate_label


def render_quality_page(workflow_app: WorkflowApplication, release_app: ReleaseApplication, workspace_id: str,
                        source_folder: Path, dataset_name: str, sample_files, *, show_title=True):
    if show_title:
        page_header("质量报告", "查看格式、内容与审核覆盖情况，定位需要修复或隔离的样本。", "DATA QUALITY")
    verified_runs = [row for row in workflow_app.list_runs()
                     if row.get("status") in {"completed", "needs_attention"} and row.get("id")]
    if verified_runs:
        view = st.segmented_control(
            "质量范围", ("工作流质量", "已有对话质量"), default="工作流质量",
            key=f"quality-source:{workspace_id}", label_visibility="collapsed",
        )
        if view == "工作流质量":
            render_workflow_quality(workflow_app, workspace_id)
            return
    # A quality report must inspect the source rows, including malformed conversation
    # structures that the interactive preview intentionally refuses to normalize.
    files = sample_files()
    if not files:
        st.html('<div class="df-empty-state"><span class="df-empty-state-icon">✓</span><strong>暂无质量报告</strong><p>选取一个样本文件后，系统会统计结构问题、审核覆盖与批量放行条件。</p></div>')
        return
    ws = workspace_id
    source_path = st.selectbox(
        "样本文件", files,
        format_func=lambda path: str(path.relative_to(source_folder)) if path.is_relative_to(source_folder) else path.name,
        key=f"quality-file:{ws}",
    )
    try:
        samples = release_app.raw_preview_samples(source_path)
        data = release_app.quality_report_for_dataset(samples, dataset_name)
    except (OSError, ValueError) as error:
        st.error(f"无法读取样本文件：{error}")
        return
    if not samples:
        st.html('<div class="df-empty-state"><span class="df-empty-state-icon">✓</span><strong>暂无质量报告</strong><p>选取一个样本文件后，系统会统计结构问题、审核覆盖与批量放行条件。</p></div>')
        return
    st.html('''<style>
    .df-quality-summary { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:10px 0 16px; }
    .df-quality-summary > div { display:grid; align-content:center; gap:5px; min-height:93px; padding:15px; border:1px solid #DFE8F4; border-radius:11px; background:linear-gradient(145deg,#fff,#F8FBFF); }
    .df-quality-summary span { color:#7B8BA0; font-size:12px; }
    .df-quality-summary strong { color:#1A2B44; font-size:25px; line-height:1.15; }
    .df-quality-summary small { color:#8A99AA; font-size:11px; }
    .df-quality-summary > div:nth-child(2) strong { color:#D2782F; }
    .df-quality-summary > div:nth-child(3) strong,.df-quality-summary > div:nth-child(4) strong { color:#1769D2; }
    .df-quality-facts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; margin:12px 0; }
    .df-quality-facts > div { display:grid; gap:3px; padding:10px 11px; border:1px solid #E5ECF6; border-radius:9px; background:#FBFDFF; }
    .df-quality-facts span { color:#8191A4; font-size:12px; }
    .df-quality-facts strong { color:#233951; font-size:14px; }
    .df-quality-code-list { display:flex; flex-wrap:wrap; gap:7px; margin:8px 0 14px; }
    .df-quality-code-list span { padding:5px 8px; border:1px solid #DDE8F6; border-radius:6px; background:#F5F9FF; color:#3E658F; font-size:12px; }
    .df-quality-record { display:flex; flex-wrap:wrap; gap:7px; padding:11px 0; }
    .df-quality-record span { padding:5px 8px; border:1px solid #E1EAF5; border-radius:6px; background:#F8FBFF; color:#546E8D; font-size:12px; }
    .df-quality-preview { max-height:460px; overflow:auto; padding:14px; border:1px solid #E2EAF4; border-radius:10px; background:#FCFDFF; }
    @media(max-width:900px) { .df-quality-summary { grid-template-columns:repeat(2,minmax(0,1fr)); } .df-quality-facts { grid-template-columns:1fr; } }
    </style>''')
    st.html(
        '<div class="df-quality-summary">'
        f'<div><span>样本总数</span><strong>{data["samples"]:,}</strong><small>当前选择的文件</small></div>'
        f'<div><span>结构问题</span><strong>{data["issue_count"]:,}</strong><small>逐条检查所记录的问题</small></div>'
        f'<div><span>重复内容</span><strong>{data["duplicate_content"]:,}</strong><small>依据消息等内容指纹</small></div>'
        f'<div><span>重复 ID</span><strong>{data["duplicate_ids"]:,}</strong><small>同名样本额外出现次数</small></div>'
        '</div>'
    )
    st.caption("文件：" + source_path.name + " · 本报告只说明结构与有效审核证据，不代表事实正确性。")
    left, right = st.columns([1.4, 1], gap="large")
    with left, st.container(border=True):
        section_heading("审核覆盖与放量条件", "审核记录仅在绑定当前样本内容时计入覆盖率。", "◉")
        coverage = float(data["review_coverage"])
        st.progress(coverage, text=f"有效审核覆盖 {coverage:.0%}")
        if data["ready_for_bulk"]:
            st.success("达到当前自动放量检查条件；正式放量仍需人工确认 G3。")
        else:
            block_names = {
                "empty_dataset": "空数据集", "structural_errors": "存在结构问题",
                "duplicate_samples": "存在重复样本", "review_coverage_below_90_percent": "有效审核覆盖不足 90%",
                "review_consensus_not_met": "审核共识未达成",
            }
            st.warning("当前未达到放量条件：" + "、".join(
                block_names.get(reason, reason) for reason in data["block_reasons"]))
    with right, st.container(border=True):
        section_heading("数据分布", "语言为字符启发式分类；长度按字符统计。", "▤")
        languages = data["language_heuristic"]
        lengths = data["length_chars"]
        st.html(
            '<div class="df-quality-facts">'
            f'<div><span>中文</span><strong>{int(languages.get("zh", 0)):,} 条</strong></div>'
            f'<div><span>中英混合</span><strong>{int(languages.get("mixed", 0)):,} 条</strong></div>'
            f'<div><span>英文或其他</span><strong>{int(languages.get("en_or_other", 0)):,} 条</strong></div>'
            '</div>'
        )
        st.caption(f"正文长度：最短 {lengths['min']} 字符 · 平均 {lengths['mean']} 字符 · 最长 {lengths['max']} 字符")
    with st.container(border=True):
        section_heading("问题定位", "按问题类型和样本 ID 筛选，查看原始样本及所在行。", "⌕")
        issue_names = {
            "visual_review_required": "图像需人工查看", "messages_missing": "缺少对话消息",
            "message_schema": "消息结构不符", "orphan_context": "上下文起点缺失",
            "incomplete_answer": "回答未完成", "unresolved_tool_error": "未解决的工具错误",
            "invalid_tool_error_flag": "工具错误标记无效",
        }
        language = st.session_state.get("ui_language", "zh")
        issue_names = {code: translate_label(label, language) for code, label in issue_names.items()}
        code_counts = {}
        for issue in data["issues"]:
            code = str(issue.get("code", "未知问题"))
            code_counts[code] = code_counts.get(code, 0) + 1
        if code_counts:
            st.html('<div class="df-quality-code-list">' + ''.join(
                '<span>' + html.escape(issue_names.get(code, code)) + ' · ' + str(count) + '</span>'
                for code, count in sorted(code_counts.items(), key=lambda item: (-item[1], item[0]))) + '</div>')
        else:
            st.success("当前文件没有逐条结构问题。重复统计和审核覆盖仍需单独核对。")
        filter_col, search_col = st.columns([1, 2])
        codes = ["全部问题", *sorted(code_counts)]
        selected_code = filter_col.selectbox("问题类型", codes,
                                             format_func=lambda code: issue_names.get(code, code))
        query = search_col.text_input("搜索样本 ID", placeholder="输入样本 ID 的任意部分…")
        filtered = [issue for issue in data["issues"]
                    if (selected_code == "全部问题" or issue.get("code") == selected_code)
                    and query.casefold() in str(issue.get("sample", "")).casefold()]
        st.caption(f"匹配 {len(filtered)} / {data['issue_count']} 条问题；重复内容与重复 ID 为单独的汇总计数。")
        if filtered:
            selected = st.selectbox(
                "定位问题样本", list(range(len(filtered))),
                format_func=lambda index: (f"{issue_names.get(filtered[index].get('code'), filtered[index].get('code'))}"
                                           f" · {filtered[index].get('sample', '未知样本')}"),
            )
            issue = filtered[selected]
            sample_id = str(issue.get("sample", ""))
            positions = [index for index, row in enumerate(samples)
                         if str(row.get("id") or f"row-{index + 1}") == sample_id]
            if positions:
                position = (st.selectbox("同名样本位置", positions, format_func=lambda index: f"文件第 {index + 1} 条")
                            if len(positions) > 1 else positions[0])
                sample = samples[position]
                row_label = f"File record {position + 1}" if language == "en" else f"文件第 {position + 1} 条"
                st.html('<div class="df-quality-record"><span>' + row_label
                        + '</span><span>' + translate_label("样本 ID：", language) + html.escape(sample_id)
                        + '</span><span>' + translate_label("问题：", language) + html.escape(issue_names.get(str(issue.get('code')), str(issue.get('code'))))
                        + '</span></div>')
                messages = sample.get("messages")
                if isinstance(messages, list) and all(isinstance(message, dict) for message in messages):
                    st.html('<div class="df-quality-preview"><div class="bubbles">'
                            + render_message_sequence(messages) + '</div></div>')
                else:
                    st.code(json.dumps(sample, ensure_ascii=False, indent=2, default=str)[:10000], language="json")
            else:
                st.info("问题记录中的样本 ID 未在当前文件中找到，请重新选择文件后检查。")
        elif code_counts:
            st.info("当前筛选条件下没有问题记录。")
    with st.expander("检查边界与原始问题码"):
        st.caption("本报告只验证结构和当前内容绑定的审核记录；含图像样本还需要具备视觉能力的人工复核。")
        st.json({"问题码计数": code_counts, "放量阻断码": data["block_reasons"]})

