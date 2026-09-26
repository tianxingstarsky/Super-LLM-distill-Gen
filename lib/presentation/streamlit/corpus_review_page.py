"""Human review surface for generated CPT corpus rows."""
from __future__ import annotations

import html
import json
import re

import streamlit as st

from lib.application.corpus_review_service import CorpusReviewApplication
from lib.presentation.streamlit.shared import page_header, review_empty_state
from lib.presentation.streamlit.review_queue_controls import PAGE_SIZE, review_queue_controls
from lib.presentation.streamlit.review_release_controls import render_active_release, render_review_release


_DECISIONS = {"approved": "已通过", "rejected": "已退回", "skipped": "已跳过", "pending": "待审核"}
_EVIDENCE_FIELDS = {
    "kind": "来源类型", "source_id": "来源 ID", "location": "来源位置",
    "evidence_level": "证据等级", "judge": "自动质检", "quotes": "原文摘录", "citations": "引用",
}


def _excerpt(text: str, limit: int = 52) -> str:
    """Keep queue labels concise and prevent Markdown or HTML from becoming UI controls."""
    plain = re.sub(r"\s+", " ", text).strip().translate(str.maketrans({
        "<": "‹", ">": "›", "[": "［", "]": "］", "!": "！", "`": "′",
    }))
    return plain[:limit] + ("…" if len(plain) > limit else "")


def _evidence_html(evidence: dict) -> str:
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
        return '<div class="df-review-history"><b>尚无人工处理记录</b><span>当前语料等待审核</span></div>'
    status = _DECISIONS.get(review.get("decision"), "已处理")
    reviewer = html.escape(str(review.get("reviewer") or "—"))
    when = html.escape(str(review.get("reviewed_at") or "—"))
    reason = html.escape(str(review.get("reason") or "无补充意见"))
    return (f'<div class="df-review-history"><b>{status}</b><span>{reviewer} · {when}</span>'
            f'<p>{reason}</p></div>')


def _corpus_html(title: str, text: str, *, original: bool = False) -> str:
    kind = " original" if original else ""
    return (f'<section class="df-review-corpus-card{kind}">'
            f'<header><b>{html.escape(title)}</b><span>{len(text):,} 字符</span></header>'
            f'<div class="df-review-corpus-text">{html.escape(text)}</div></section>')


def render_corpus_review(application: CorpusReviewApplication, *, show_header: bool = True):
    if show_header:
        page_header("CPT 语料审核", "逐条检查训练语料及其来源证据，修订通过的样本并保留完整审核记录。", "CPT　·　人工质量门")
    else:
        st.subheader("CPT 语料审核")
    runs = application.reviewable_runs()
    if not runs:
        with st.container(border=True):
            review_empty_state(
                "当前没有待审 CPT 语料",
                "生成并验证文档语料后，可在这里逐条检查来源证据、修订结果并发布审核版本。",
                "CPT 连续预训练语料",
            )
            st.info("当前工作区没有通过产物校验的 CPT 工作流。先在“自动工作流”生成 CPT 候选，再进入语料审核。")
            if st.button("前往数据生成", type="primary", key="corpus-review-empty-workflow"):
                st.session_state["nav"] = "自动工作流"
                st.rerun()
        return

    labels = {row["id"]: row for row in runs}
    run_id = st.selectbox("CPT 工作流", list(labels), key="corpus-review-run",
                          format_func=lambda key: f"{labels[key]['name']} · {labels[key]['sample_count']} 条 · {key[:8]}")
    if render_active_release(application, run_id, widgets=st):
        return
    overview = application.queue(run_id, limit=1)
    counts, total = overview["counts"], overview["total"]
    finished = counts["approved"] + counts["rejected"]
    a, b, c, d = st.columns(4)
    a.metric("待处理", counts["pending"] + counts["skipped"])
    b.metric("已通过", counts["approved"])
    c.metric("已退回", counts["rejected"])
    d.metric("候选语料", total)
    if total == 0:
        st.warning("本次运行没有通过自动质量检查的语料。请查看工作流质量报告中的隔离原因。")
        return

    queue_col, content_col, action_col = st.columns([1.05, 2.15, 1.05], gap="medium")
    with queue_col:
        with st.container(border=True, key="df-review-queue-cpt"):
            st.html('<div class="df-review-panel-title"><b>▤</b><span><strong>语料审核队列</strong>'
                    '<small>选择候选语料查看来源</small></span></div>')
            offset, filter_decision, page, pages = review_queue_controls(f"corpus-review:{run_id}", counts, total, widgets=st)
            queue = application.queue(run_id, offset=offset, limit=PAGE_SIZE, decision=filter_decision)
            items = queue["items"]
            st.caption(f"第 {int(page)} / {pages} 页 · {len(items)} 条")
            item_by_id = {item["sample_id"]: item for item in items}
            chosen_id = None
            if item_by_id:
                selection_key = f"corpus-review-item:{run_id}:{page}"
                if st.session_state.get(selection_key) not in item_by_id:
                    st.session_state[selection_key] = next(iter(item_by_id))
                chosen_id = st.radio(
                    "语料样本", list(item_by_id), key=selection_key,
                    format_func=lambda key: f"{_DECISIONS.get((item_by_id[key].get('review') or {}).get('decision'), '待审核')} · "
                    f"{_excerpt(item_by_id[key]['row']['text'])}",
                    label_visibility="collapsed",
                )
            else:
                st.info("此状态下没有语料")

    item = item_by_id[chosen_id] if chosen_id else None
    row = item["row"] if item else None
    review = (item.get("review") or {}) if item else {}
    candidate = (review.get("candidate") or row) if item else None
    revised = candidate["text"] if candidate else ""

    with content_col:
        with st.container(border=True, key="df-review-content-cpt"):
            st.html('<div class="df-review-panel-title"><b>◫</b><span><strong>语料详情</strong>'
                    '<small>正文、来源和自动质检证据</small></span></div>')
            if item:
                st.html('<div class="df-review-record-meta"><span>CPT 语料</span><span>指纹 ' +
                        html.escape(chosen_id[:16]) + '</span><span>原文 ' +
                        f'{len(row["text"]):,}' + ' 字符</span></div>')
                st.html(_corpus_html("原始候选语料", row["text"], original=True))
                if candidate["text"] != row["text"]:
                    st.html(_corpus_html("当前审核修订", candidate["text"]))
                evidence = item.get("evidence") or {}
                if evidence:
                    st.html('<div class="df-review-evidence-title"><strong>来源与质检证据</strong>'
                            '<small>以下字段来自该样本的实际生成记录</small></div>')
                    st.html(_evidence_html(evidence))
                else:
                    st.caption("该样本未附带额外来源证据。")
                edit_mode = st.session_state.get(f"corpus-review-edit:{run_id}:{chosen_id}", False)
                if edit_mode:
                    st.html('<div class="df-review-edit-head"><strong>修订语料正文</strong>'
                            '<small>来源证据与原始候选保持不变</small></div>')
                    revised = st.text_area("修订后的训练语料", value=candidate["text"], height=260, max_chars=500000)
            else:
                st.caption("从左侧选择一条语料，或切换队列状态。")

    with action_col:
        with st.container(border=True, key="df-review-actions-cpt"):
            st.html('<div class="df-review-panel-title"><b>✓</b><span><strong>任务操作</strong>'
                    '<small>审核、退回或暂时跳过</small></span></div>')
            if item:
                st.html(_history_html(review))
                st.toggle("修订语料正文", value=False, key=f"corpus-review-edit:{run_id}:{chosen_id}",
                          help="仅允许修订训练正文；原始候选与来源证据不会被覆盖。")
                with st.form(f"corpus-review-form:{run_id}:{chosen_id}"):
                    reason = st.text_input("审核意见（可选）", value=review.get("reason", ""), max_chars=2000)
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

            reviewer = reviewer_identity()
            decision = "approved" if approve else "rejected" if reject else "skipped"
            application.decide(run_id, chosen_id, decision=decision, reviewer=reviewer,
                               expected_hash=chosen_id, reason=reason,
                               text=revised if approve else None)
            st.session_state.pop(f"corpus-release:{run_id}", None)
            st.success("审核记录已保存。")
            st.rerun()
        except (OSError, PermissionError, ValueError) as error:
            st.error(f"无法保存审核记录：{error}")

    with action_col:
        with st.container(border=True, key="df-review-release-cpt"):
            st.html('<div class="df-review-panel-title"><b>⇩</b><span><strong>人工审核版本</strong>'
                    '<small>全部语料处理后开放</small></span></div>')
            st.caption("跳过项仍需处理；版本只包含通过的语料，并附带审核历史与 SHA-256 清单。")
            queue_complete = finished == total
            render_review_release(application, run_id, target="cpt", complete=queue_complete,
                                  approved=counts["approved"], session_key=f"corpus-release:{run_id}",
                                  download_key=f"corpus-download:{run_id}", widgets=st)
