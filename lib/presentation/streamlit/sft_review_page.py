"""Human review surface for generated SFT conversations."""
from __future__ import annotations

import html
import json
import math
import re
from typing import Callable

import streamlit as st

from lib.application.sft_review_service import SftReviewApplication
from lib.presentation.streamlit.shared import review_empty_state
from lib.render import MESSAGE_CSS, render_message_sequence


_DECISIONS = {"approved": "已通过", "rejected": "已退回", "skipped": "已跳过", "pending": "待审核"}
_EVIDENCE_FIELDS = {
    "kind": "来源类型", "source_id": "来源 ID", "location": "来源位置",
    "evidence_level": "证据等级", "judge": "自动评审", "quotes": "原文摘录", "citations": "引用",
}


def _excerpt(messages: list[dict], limit: int = 50) -> str:
    """Build a compact, text-only queue label without exposing media payloads."""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(block.get("text", "") for block in content
                            if isinstance(block, dict) and isinstance(block.get("text"), str))
        else:
            text = ""
        text = re.sub(r"\s+", " ", text).strip().translate(str.maketrans({
            "<": "‹", ">": "›", "[": "［", "]": "］", "!": "！", "`": "′",
        }))
        if text:
            return text[:limit] + ("…" if len(text) > limit else "")
    return "多轮对话 / 结构化输入"


def _evidence_html(evidence: dict) -> str:
    """Show the provenance fields as bounded, escaped facts instead of raw JSON."""
    rows = []
    for key, label in _EVIDENCE_FIELDS.items():
        if key not in evidence or evidence[key] in (None, "", []):
            continue
        value = evidence[key]
        plain = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        rows.append(f'<div><span>{label}</span><strong>{html.escape(plain[:1200])}</strong></div>')
    return '<div class="df-review-facts">' + "".join(rows) + "</div>"


def _history_html(review: dict) -> str:
    if not review:
        return '<div class="df-review-history"><b>尚无人工处理记录</b><span>当前样本等待审核</span></div>'
    status = _DECISIONS.get(review.get("decision"), "已处理")
    reviewer = html.escape(str(review.get("reviewer") or "—"))
    when = html.escape(str(review.get("reviewed_at") or "—"))
    reason = html.escape(str(review.get("reason") or "无补充意见"))
    return (f'<div class="df-review-history"><b>{status}</b><span>{reviewer} · {when}</span>'
            f'<p>{reason}</p></div>')


def render_sft_review(application: SftReviewApplication, *, legacy_review: Callable[[], None] | None = None):
    st.subheader("SFT 数据调整")
    runs = application.reviewable_runs()
    if not runs:
        with st.container(border=True):
            review_empty_state(
                "当前没有待审 SFT 候选",
                "先生成包含高质量问答的 SFT 工作流。通过自动质量检查的对话会进入这里，审核通过后可单独发布。",
                "SFT 指令与高质量回答",
            )
            st.info("当前工作区没有通过产物校验的 SFT 工作流。先在“自动工作流”生成 SFT 候选，再进入审核。")
            if st.button("前往数据生成", type="primary", key="sft-review-empty-workflow"):
                st.session_state["nav"] = "自动工作流"
                st.rerun()
            if legacy_review:
                with st.expander("打开历史 / 导入样本审核中心"):
                    legacy_review()
        return

    labels = {row["id"]: row for row in runs}
    run_id = st.selectbox("SFT 工作流", list(labels), key="sft-review-run",
                          format_func=lambda key: f"{labels[key]['name']} · {labels[key]['sample_count']} 条 · {key[:8]}")
    overview = application.queue(run_id, limit=1)
    counts, total = overview["counts"], overview["total"]
    finished = counts["approved"] + counts["rejected"]
    a, b, c, d = st.columns(4)
    a.metric("待处理", counts["pending"] + counts["skipped"])
    b.metric("已通过", counts["approved"])
    c.metric("已退回", counts["rejected"])
    d.metric("候选样本", total)
    if total == 0:
        st.warning("本次运行没有通过自动质量检查的 SFT 候选。请查看工作流质量报告中的隔离原因。")
        return

    page_size = 20
    pages = max(1, math.ceil(total / page_size))
    queue_col, content_col, action_col = st.columns([1.05, 2.15, 1.05], gap="medium")
    with queue_col:
        with st.container(border=True, key="df-review-queue-sft"):
            st.html('<div class="df-review-panel-title"><b>▤</b><span><strong>待审核任务</strong>'
                    '<small>选择对话查看完整内容</small></span></div>')
            page = (st.number_input("队列页码", min_value=1, max_value=pages, value=1, step=1,
                                    key=f"sft-review-page:{run_id}") if pages > 1 else 1)
            queue = application.queue(run_id, offset=(int(page) - 1) * page_size, limit=page_size)
            items = queue["items"]
            st.caption(f"第 {int(page)} / {pages} 页 · {len(items)} 条")
            filter_label = st.selectbox("处理状态", ["全部", "待审核", "已通过", "已退回", "已跳过"],
                                        key=f"sft-review-filter:{run_id}:{page}")
            filter_decision = {"全部": None, "待审核": "pending", "已通过": "approved",
                               "已退回": "rejected", "已跳过": "skipped"}[filter_label]
            filtered = [item for item in items if filter_decision is None or
                        (item.get("review") or {}).get("decision", "pending") == filter_decision]
            item_by_id = {item["sample_id"]: item for item in filtered}
            sample_id = None
            if item_by_id:
                selection_key = f"sft-review-item:{run_id}:{page}"
                if st.session_state.get(selection_key) not in item_by_id:
                    st.session_state[selection_key] = next(iter(item_by_id))
                sample_id = st.radio(
                    "SFT 样本", list(item_by_id), key=selection_key,
                    format_func=lambda key: f"{_DECISIONS.get((item_by_id[key].get('review') or {}).get('decision'), '待审核')} · "
                    f"{_excerpt(item_by_id[key]['row']['messages'])}",
                    label_visibility="collapsed",
                )
            else:
                st.info("此状态下没有样本")

    item = item_by_id[sample_id] if sample_id else None
    review = (item.get("review") or {}) if item else {}
    candidate = (review.get("candidate") or item["row"]) if item else None
    revised_messages = [dict(message) for message in candidate["messages"]] if candidate else []

    with content_col:
        with st.container(border=True, key="df-review-content-sft"):
            st.html('<div class="df-review-panel-title"><b>◫</b><span><strong>任务详情</strong>'
                    '<small>按真实轮次呈现输入、回答和工具轨迹</small></span></div>')
            if item:
                st.html('<div class="df-review-record-meta"><span>SFT 对话</span><span>指纹 ' +
                        html.escape(sample_id[:16]) + '</span><span>共 ' +
                        str(len(candidate["messages"])) + ' 条消息</span></div>')
                st.html('<style>' + MESSAGE_CSS + '</style><div class="df-review-dialogue">'
                        '<div class="bubbles">' + render_message_sequence(candidate["messages"]) + '</div></div>')
                evidence = item.get("evidence") or {}
                if evidence:
                    with st.expander("来源与自动质检证据"):
                        st.html(_evidence_html(evidence))
                if candidate.get("tools"):
                    with st.expander("锁定的工具定义"):
                        tools = []
                        for tool in candidate["tools"]:
                            if isinstance(tool, dict):
                                function = tool.get("function")
                                name = function.get("name") if isinstance(function, dict) else None
                                tools.append(str(name or tool.get("name") or "未命名工具"))
                        st.html('<div class="df-review-facts"><div><span>工具</span><strong>' +
                                html.escape("、".join(tools) or "已记录") + '</strong></div></div>')
                edit_mode = st.session_state.get(f"sft-review-edit:{run_id}:{sample_id}", False)
                if edit_mode:
                    st.html('<div class="df-review-edit-head"><strong>修订助手内容</strong>'
                            '<small>用户、系统、工具调用和消息结构保持锁定</small></div>')
                    for index, message in enumerate(candidate["messages"]):
                        if message.get("role") != "assistant":
                            continue
                        content = message.get("content")
                        if isinstance(content, str):
                            revised_messages[index]["content"] = st.text_area(
                                f"第 {index + 1} 条助手回答", value=content,
                                key=f"sft-content:{run_id}:{sample_id}:{index}", height=130, max_chars=500000)
                        reasoning = message.get("reasoning_content")
                        if isinstance(reasoning, str):
                            revised_messages[index]["reasoning_content"] = st.text_area(
                                f"第 {index + 1} 条推理说明", value=reasoning,
                                key=f"sft-reasoning:{run_id}:{sample_id}:{index}", height=90, max_chars=500000)
            else:
                st.caption("从左侧选择一条样本，或切换队列状态。")

    with action_col:
        with st.container(border=True, key="df-review-actions-sft"):
            st.html('<div class="df-review-panel-title"><b>✓</b><span><strong>任务操作</strong>'
                    '<small>确认质量后提交审核结论</small></span></div>')
            if item:
                st.html(_history_html(review))
                st.toggle("编辑助手回答", value=False, key=f"sft-review-edit:{run_id}:{sample_id}",
                          help="仅允许修订助手正文与已有推理说明；其余字段锁定。")
                with st.form(f"sft-review-form:{run_id}:{sample_id}"):
                    reason = st.text_area("审核意见（可选）", value=review.get("reason", ""), max_chars=2000, height=105)
                    approve = st.form_submit_button("通过并保存修订", type="primary", width="stretch")
                    reject = st.form_submit_button("退回", width="stretch")
                    skip = st.form_submit_button("跳过", width="stretch")
            else:
                approve = reject = skip = False
                reason = ""
            st.html('<div class="df-review-progress-title"><strong>任务进度</strong><span>' +
                    f'{finished} / {total}</span></div>')
            st.progress(finished / total, text=f"已审核 {finished} 条 · 待处理 {counts['pending'] + counts['skipped']} 条")
            st.html('<div class="df-review-mini-stats"><span>已通过 <b>' + str(counts["approved"]) +
                    '</b></span><span>已退回 <b>' + str(counts["rejected"]) + '</b></span></div>')

    if approve or reject or skip:
        try:
            from lib.review_management import reviewer_identity

            decision = "approved" if approve else "rejected" if reject else "skipped"
            revised = {key: value for key, value in candidate.items()}
            revised["messages"] = revised_messages
            application.decide(run_id, sample_id, decision=decision, reviewer=reviewer_identity(),
                               expected_hash=sample_id, reason=reason,
                               candidate=revised if approve else None)
            st.session_state.pop(f"sft-release:{run_id}", None)
            st.success("审核记录已保存。")
            st.rerun()
        except (OSError, PermissionError, ValueError) as error:
            st.error(f"无法保存审核记录：{error}")

    with action_col:
        with st.container(border=True, key="df-review-release-sft"):
            st.html('<div class="df-review-panel-title"><b>⇩</b><span><strong>发布版本</strong>'
                    '<small>全部样本通过或退回后开放</small></span></div>')
            st.caption("跳过项仍需处理；自动候选与审核证据会保留。")
            complete = finished == total
            if st.button("生成已审核 SFT 版本", type="primary", disabled=not complete or counts["approved"] == 0):
                try:
                    st.session_state[f"sft-release:{run_id}"] = application.release(run_id)
                except (OSError, PermissionError, ValueError) as error:
                    st.error(f"无法生成审核版本：{error}")
            package = st.session_state.get(f"sft-release:{run_id}")
            if package:
                st.download_button("下载人工审核 SFT ZIP", data=package,
                                   file_name=f"sft-human-reviewed-{run_id[:8]}.zip",
                                   mime="application/zip", key=f"sft-download:{run_id}")
    if legacy_review:
        with st.expander("打开历史 / 导入样本审核中心"):
            legacy_review()
