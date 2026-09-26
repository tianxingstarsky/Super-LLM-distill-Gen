"""Human review surface for generated DPO and ORPO preference pairs."""
from __future__ import annotations

import html
import re

import streamlit as st

from lib.application.preference_review_service import PreferenceReviewApplication
from lib.presentation.streamlit.shared import page_header, review_empty_state
from lib.presentation.streamlit.review_queue_controls import PAGE_SIZE, review_queue_controls
from lib.presentation.streamlit.review_release_controls import render_review_release
from lib.render import MESSAGE_CSS, render_message_sequence


_DECISIONS = {"approved": "已通过", "rejected": "已退回", "skipped": "已跳过", "pending": "待审核"}


def _key(value: str, target: str) -> str:
    """Keep persisted DPO widget keys while isolating ORPO page state."""
    return value if target == "dpo" else f"{value}:orpo"


def _answer(messages):
    return "\n\n".join(str(message.get("content", "")) for message in messages)


def _prompt_excerpt(messages: list[dict], limit: int = 50) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        text = re.sub(r"\s+", " ", content).strip().translate(str.maketrans({
            "<": "‹", ">": "›", "[": "［", "]": "］", "!": "！", "`": "′",
        })) if isinstance(content, str) else ""
        if text:
            return text[:limit] + ("…" if len(text) > limit else "")
    return "多轮提示 / 结构化输入"


def _history_html(review: dict) -> str:
    if not review:
        return '<div class="df-review-history"><b>尚无人工处理记录</b><span>当前偏好对等待审核</span></div>'
    status = _DECISIONS.get(review.get("decision"), "已处理")
    reviewer = html.escape(str(review.get("reviewer") or "—"))
    when = html.escape(str(review.get("reviewed_at") or "—"))
    reason = html.escape(str(review.get("reason") or "无补充意见"))
    return (f'<div class="df-review-history"><b>{status}</b><span>{reviewer} · {when}</span>'
            f'<p>{reason}</p></div>')


def _comparison_html(candidate: dict) -> str:
    """Escaped, ordered conversation preview; both answers keep their own scroll area."""
    return (
        '<style>' + MESSAGE_CSS + '</style>'
        '<div class="df-review-compare">'
        '<section class="df-review-compare-card chosen"><header><b>A</b><span><strong>更优回答</strong>'
        '<small>chosen · 推荐保留</small></span></header><div class="bubbles">' +
        render_message_sequence(candidate["chosen"]) + '</div></section>'
        '<section class="df-review-compare-card rejected"><header><b>B</b><span><strong>对照回答</strong>'
        '<small>rejected · 用于偏好训练</small></span></header><div class="bubbles">' +
        render_message_sequence(candidate["rejected"]) + '</div></section>'
        '</div>'
    )


def render_preference_review(application: PreferenceReviewApplication, *, show_header: bool = True):
    target = getattr(application, "target", "dpo")
    label = target.upper()
    if show_header:
        page_header("偏好审核与模型对齐", "逐对比较模型回答、确认偏好并保留修订历史；已审核版本单独发布。", label)
    else:
        st.subheader(f"{label} 偏好优化")
    runs = application.reviewable_runs()
    if not runs:
        with st.container(border=True):
            review_empty_state(
                "偏好审核队列为空",
                f"生成成对回答并通过质量检查后，{label} 候选会显示在这里供人工比较。",
                f"{label} 偏好对",
            )
            st.info(f"当前工作区没有通过产物校验的 {label} 工作流。先在“自动工作流”生成 {label} 候选，再进入人工审核。")
            if st.button("前往数据生成", type="primary", key=_key("preference-review-empty-workflow", target)):
                st.session_state["nav"] = "自动工作流"
                st.rerun()
        return

    labels = {row["id"]: row for row in runs}
    run_id = st.selectbox(f"{label} 工作流", list(labels), key=_key("preference-review-run", target),
                          format_func=lambda key: f"{labels[key]['name']} · {labels[key]['pair_count']} 对 · {key[:8]}")
    state = application.queue(run_id, limit=1)
    total, counts = state["total"], state["counts"]
    finished = counts["approved"] + counts["rejected"]
    a, b, c, d = st.columns(4)
    a.metric("待处理", counts["pending"] + counts["skipped"])
    b.metric("已通过", counts["approved"])
    c.metric("已退回", counts["rejected"])
    d.metric("候选对", total)
    if total == 0:
        st.warning("本次运行没有通过自动偏好校验的候选对。查看工作流质量报告中的隔离原因。")
        return

    queue_col, content_col, action_col = st.columns([1.05, 2.15, 1.05], gap="medium")
    with queue_col:
        with st.container(border=True, key=f"df-review-queue-{target}"):
            st.html('<div class="df-review-panel-title"><b>▤</b><span><strong>偏好审核队列</strong>'
                    '<small>选择一对回答进行比较</small></span></div>')
            offset, filter_decision, page, pages = review_queue_controls(
                _key(f"preference-review:{run_id}", target), counts, total, widgets=st)
            queue = application.queue(run_id, offset=offset, limit=PAGE_SIZE, decision=filter_decision)
            items = queue["items"]
            st.caption(f"第 {int(page)} / {pages} 页 · {len(items)} 对")
            item_by_id = {item["pair_id"]: item for item in items}
            chosen_id = None
            if item_by_id:
                selection_key = _key(f"preference-review-item:{run_id}:{page}", target)
                if st.session_state.get(selection_key) not in item_by_id:
                    st.session_state[selection_key] = next(iter(item_by_id))
                chosen_id = st.radio(
                    "偏好样本", list(item_by_id), key=selection_key,
                    format_func=lambda key: f"{_DECISIONS.get((item_by_id[key].get('review') or {}).get('decision'), '待审核')} · "
                    f"{_prompt_excerpt(item_by_id[key]['pair']['prompt'])}",
                    label_visibility="collapsed",
                )
            else:
                st.info("此状态下没有偏好对")

    item = item_by_id[chosen_id] if chosen_id else None
    pair = item["pair"] if item else None
    review = (item.get("review") or {}) if item else {}
    candidate = (review.get("candidate") or pair) if item else None
    revised_chosen = _answer(candidate["chosen"]) if candidate else ""
    revised_rejected = _answer(candidate["rejected"]) if candidate else ""

    with content_col:
        with st.container(border=True, key=f"df-review-content-{target}"):
            st.html('<div class="df-review-panel-title"><b>◫</b><span><strong>偏好对比</strong>'
                    '<small>同一提示下比较两种模型回答</small></span></div>')
            if item:
                st.html(f'<div class="df-review-record-meta"><span>{label} 偏好对</span><span>指纹 ' +
                        html.escape(chosen_id[:16]) + '</span><span>提示 ' +
                        str(len(pair["prompt"])) + ' 轮</span></div>')
                st.html('<style>' + MESSAGE_CSS + '</style><div class="df-review-prompt">'
                        '<div class="df-review-prompt-label">原始提示与上下文</div><div class="bubbles">' +
                        render_message_sequence(pair["prompt"]) + '</div></div>')
                st.html(_comparison_html(candidate))
                if pair.get("tools"):
                    with st.expander("锁定的工具定义"):
                        names = []
                        for tool in pair["tools"]:
                            if isinstance(tool, dict):
                                function = tool.get("function")
                                name = function.get("name") if isinstance(function, dict) else None
                                names.append(str(name or tool.get("name") or "未命名工具"))
                        st.html('<div class="df-review-facts"><div><span>工具</span><strong>' +
                                html.escape("、".join(names) or "已记录") + '</strong></div></div>')
                edit_mode = st.session_state.get(_key(f"preference-review-edit:{run_id}:{chosen_id}", target), False)
                if edit_mode:
                    st.html('<div class="df-review-edit-head"><strong>修订偏好回答</strong>'
                            '<small>提示、工具定义和回答角色保持锁定</small></div>')
                    edit_a, edit_b = st.columns(2, gap="medium")
                    with edit_a:
                        revised_chosen = st.text_area("修订更优回答", value=revised_chosen, height=200, max_chars=40000)
                    with edit_b:
                        revised_rejected = st.text_area("修订对照回答", value=revised_rejected, height=200, max_chars=40000)
            else:
                st.caption("从左侧选择一对样本，或切换队列状态。")

    with action_col:
        with st.container(border=True, key=f"df-review-actions-{target}"):
            st.html('<div class="df-review-panel-title"><b>✓</b><span><strong>任务操作</strong>'
                    '<small>确认偏好方向并提交结论</small></span></div>')
            if item:
                st.html(_history_html(review))
                st.toggle("编辑两个回答", value=False, key=_key(f"preference-review-edit:{run_id}:{chosen_id}", target),
                          help="仅修订更优回答和对照回答的正文；提示与工具定义锁定。")
                with st.form(_key(f"preference-review-form:{run_id}:{chosen_id}", target)):
                    reason = st.text_input("审核意见（可选）", value=review.get("reason", ""), max_chars=2000)
                    approve = st.form_submit_button("通过并保存修订", type="primary", width="stretch")
                    swap = st.form_submit_button("交换偏好后通过", width="stretch")
                    reject = st.form_submit_button("退回", width="stretch")
                    skip = st.form_submit_button("跳过", width="stretch")
            else:
                approve = swap = reject = skip = False
                reason = ""
            st.html('<div class="df-review-progress-title"><strong>任务进度</strong><span>' +
                    f'{finished} / {total}</span></div>')
            st.progress(finished / total, text=f"已审核 {finished} 对 · 待处理 {counts['pending'] + counts['skipped']} 对")
            st.html('<div class="df-review-mini-stats"><span>已通过 <b>' + str(counts["approved"]) +
                    '</b></span><span>已退回 <b>' + str(counts["rejected"]) + '</b></span></div>')

    if approve or swap or reject or skip:
        try:
            from lib.review_management import reviewer_identity

            reviewer = reviewer_identity()
            if approve or swap:
                chosen_text, rejected_text = (revised_chosen, revised_rejected)
                if swap:
                    chosen_text, rejected_text = rejected_text, chosen_text
                application.decide(run_id, chosen_id, decision="approved", reviewer=reviewer,
                                   expected_hash=chosen_id, reason=reason,
                                   chosen=chosen_text, rejected=rejected_text)
            else:
                status = "rejected" if reject else "skipped"
                application.decide(run_id, chosen_id, decision=status, reviewer=reviewer,
                                   expected_hash=chosen_id, reason=reason)
            st.session_state.pop(_key(f"preference-release:{run_id}", target), None)
            st.success("审核记录已保存。")
            st.rerun()
        except (OSError, PermissionError, ValueError) as error:
            st.error(f"无法保存审核记录：{error}")

    with action_col:
        with st.container(border=True, key=f"df-review-release-{target}"):
            st.html('<div class="df-review-panel-title"><b>⇩</b><span><strong>人工审核版本</strong>'
                    '<small>全部偏好对处理后开放</small></span></div>')
            st.caption("跳过项仍需处理；原自动候选与评分证据会保留。")
            queue_complete = finished == total
            render_review_release(application, run_id, target=target, complete=queue_complete,
                                  approved=counts["approved"], session_key=_key(f"preference-release:{run_id}", target),
                                  download_key=_key(f"preference-download:{run_id}", target), widgets=st)
