"""In-place human review of the selected workflow's Agent trajectories."""
from __future__ import annotations

import html
import math
import re

import streamlit as st

from lib.application.workflow_service import WorkflowApplication
from lib.presentation.streamlit.i18n import UntranslatedText, translate
from lib.presentation.streamlit.sample_preview import render_sample_preview


_PAGE_SIZES = (1, 3, 5, 10, 20)
_MAX_OFFSET = 100_000  # The persistence adapter rejects larger offsets.
_FILTERS = {"待审核": "pending", "全部": None, "已通过": "approved", "已退回": "rejected"}
_DECISIONS = {"pending": "待审核", "approved": "已通过", "rejected": "已退回"}
_FAILURES = {
    "recorded_tool_error": "来源记录的工具执行失败",
    "tool_observation_mismatch": "工具结果与本地重放不一致",
    "final_answer_mismatch": "最终答案与已验证结果不一致",
    "intermediate_answer_mismatch": "中间回答与已验证结果不一致",
    "terminal_state_mismatch": "终态与隔离重放结果不一致",
    "container_replay_unavailable": "隔离容器不可用",
}
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")

_STYLE = """<style>
.df-agent-review-head {display:flex;align-items:center;justify-content:space-between;gap:12px;
  padding:15px 17px;border:1px solid #dbe9f7;border-radius:13px;
  background:linear-gradient(105deg,#eef7ff,#fff 77%);margin:8px 0 14px}
.df-agent-review-head strong {display:block;color:#183c66;font-size:17px}
.df-agent-review-head small {display:block;color:#6c819b;font-size:12px;margin-top:4px}
.df-agent-review-head b {padding:5px 9px;border-radius:8px;background:#e5f2ff;
  color:#1a6ac9;font-size:12px;white-space:nowrap}
.df-agent-review-facts {display:flex;flex-wrap:wrap;gap:7px;margin:4px 0 12px}
.df-agent-review-facts span {padding:5px 8px;border:1px solid #e3edf8;border-radius:7px;
  background:#f8fbff;color:#506784;font-size:12px;overflow-wrap:anywhere}
.df-agent-review-history {padding:10px 12px;border:1px solid #e2ebf5;border-radius:9px;
  background:#f9fbfe;color:#506782;font-size:12px;overflow-wrap:anywhere}
.df-agent-review-history strong {display:block;color:#264c79;font-size:13px;margin-bottom:4px}
.df-agent-review-history p {margin:5px 0 0;white-space:pre-wrap}
</style>"""


def _copy(template: str, language: str, **values) -> str:
    return str(translate(template, language)).format(**values)


def _label(value: object, limit: int = 74) -> str:
    """Keep source text literal in Streamlit's Markdown-capable choice labels."""
    clean = re.sub(r"\s+", " ", str(value)).strip()
    clean = clean[:limit] + ("…" if len(clean) > limit else "")
    return _MARKDOWN.sub(r"\\\1", clean)


def _excerpt(row: dict, language: str = "zh") -> str:
    for message in row.get("messages", []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return _label(content)
        if isinstance(content, list):
            text = " ".join(block.get("text", "") for block in content
                            if isinstance(block, dict) and isinstance(block.get("text"), str))
            if text.strip():
                return _label(text)
    return _copy("无用户文本输入", language)


def _queue_label(item: dict, kind: str, language: str = "zh") -> str:
    row = item["native"] if kind == "positive" else item["negative"]
    status = (_DECISIONS.get(item.get("review_status"), "待审核") if kind == "positive"
              else _FAILURES.get(row.get("failure"), "失败记录"))
    return f"{item['ordinal']:,} · {_copy(status, language)} · {_excerpt(row, language)}"


def _facts(item: dict, kind: str, language: str = "zh") -> str:
    record = item["record"]
    verification = record.get("verification") if isinstance(record.get("verification"), dict) else {}
    facts = [_copy("样本序号 {ordinal}", language, ordinal=f"{item['ordinal']:,}")]
    if kind == "positive":
        facts.append(_copy("可独立重放" if item["independent_replay"] else "仅有记录证据，不能批准",
                           language))
    source = record.get("source_name")
    if isinstance(source, str) and source:
        facts.append(_copy("来源 {value}", language, value=source[:120]))
    location = record.get("source_location")
    if isinstance(location, str) and location:
        facts.append(_copy("位置 {value}", language, value=location[:120]))
    method = verification.get("method")
    if isinstance(method, str) and method:
        facts.append(_copy("校验方法 {value}", language, value=method[:80]))
    calls = verification.get("verified_call_ids")
    if isinstance(calls, list):
        facts.append(_copy("记录核对 {count} 次工具调用", language, count=f"{len(calls):,}"))
    return '<div class="df-agent-review-facts">' + "".join(
        f"<span>{html.escape(fact)}</span>" for fact in facts) + "</div>"


def _history(review: dict | None, language: str = "zh") -> str:
    if not review:
        return ('<div class="df-agent-review-history"><strong>'
                + _copy("尚无人工结论", language) + '</strong>'
                + _copy("当前轨迹等待审查。", language) + '</div>')
    decision = _copy(_DECISIONS.get(review.get("decision"), "已处理"), language)
    reviewer = html.escape(str(review.get("reviewer") or "—")[:128])
    when = html.escape(str(review.get("reviewed_at") or "—")[:64])
    reason = html.escape(str(review.get("reason") or _copy("未填写意见", language))[:2000])
    return (f'<div class="df-agent-review-history"><strong>{decision}</strong>'
            f'{reviewer} · {when}<p>{reason}</p></div>')


def _error_message(error: Exception) -> str:
    if isinstance(error, PermissionError):
        return "审核身份已失效，请在人工审核中重新验证。"
    code = str(error)
    if code == "agent_review_page_too_large_reduce_limit":
        return "当前轨迹过大，请把每页条数调小后重试。"
    if code in {"agent_review_source_changed", "stale_agent_review_source",
                "stale_agent_review_version"}:
        return "产物或审核版本已变化，请刷新任务后重新核对。"
    if code == "agent_review_independent_replay_required":
        return "此轨迹无法从候选内容独立重放，不能批准。"
    if code == "agent_review_busy_retry":
        return "另一位审阅者正在更新该任务，请稍后重试。"
    return "无法读取或保存 Agent 轨迹审核；请检查产物完整性及工作区状态。"


def _load_page(application: WorkflowApplication, run_id: str, *, kind: str,
               decision: str | None, size: int, page_key: str) -> tuple[dict, int, int]:
    page = max(1, int(st.session_state.get(page_key, 1)))
    offset = min((page - 1) * size, _MAX_OFFSET)
    queue = application.agent_review_queue(run_id, kind=kind, offset=offset,
                                           limit=size, decision=decision)
    matched = queue["matched"]
    pages = max(1, min(math.ceil(matched / size), _MAX_OFFSET // size + 1))
    if page > pages:
        page = pages
        st.session_state[page_key] = page
        queue = application.agent_review_queue(run_id, kind=kind, offset=(page - 1) * size,
                                               limit=size, decision=decision)
    return queue, page, pages


def render_agent_review(application: WorkflowApplication, run_id: str, *, workspace_id: str) -> None:
    """Review one verified run in the task detail, without changing pages."""
    language = st.session_state.get("ui_language", "zh")
    st.html(_STYLE)
    st.html('<div class="df-agent-review-head"><div><strong>Agent 轨迹审查</strong>'
            '<small>同一任务内核对工具调用、返回、重放证据与人工结论。</small></div>'
            '<b>来源工件校验</b></div>')
    prefix = f"agent-review:{workspace_id}:{run_id}"
    kind_label = st.segmented_control("轨迹类型", ("训练候选", "失败轨迹"), default="训练候选",
                                      key=f"{prefix}:kind") or "训练候选"
    kind = "positive" if kind_label == "训练候选" else "negative"
    filters, size_col = st.columns([2, 1], gap="small")
    with filters:
        decision_label = (st.selectbox("处理状态", tuple(_FILTERS), key=f"{prefix}:status")
                          if kind == "positive" else None)
    with size_col:
        size = st.selectbox("每页条数", _PAGE_SIZES, index=1, key=f"{prefix}:size")
    decision = _FILTERS[decision_label] if decision_label is not None else None
    page_key = f"{prefix}:page:{kind}:{decision or 'all'}:{size}"
    try:
        with st.spinner("正在校验并准备轨迹索引…"):
            queue, page, pages = _load_page(application, run_id, kind=kind,
                                            decision=decision, size=size, page_key=page_key)
    except (OSError, ValueError, TypeError, KeyError) as error:
        st.error(_error_message(error))
        return

    if kind == "positive":
        counts = queue["counts"]
        pending, approved, rejected = st.columns(3)
        pending.metric("待审核", counts["pending"])
        approved.metric("已通过", counts["approved"])
        rejected.metric("已退回", counts["rejected"])
        st.caption("通过仅代表审阅者确认该训练候选；工具重放不证明外部事实真实。")
    else:
        st.info("失败轨迹保留执行原因和证据供排查，不能作为训练候选通过。")
    st.caption(_copy("匹配 {matched} / 共 {total} 条 · 第 {page} / {pages} 页", language,
                     matched=f"{queue['matched']:,}", total=f"{queue['total']:,}",
                     page=page, pages=pages))
    if queue["matched"] > _MAX_OFFSET + size:
        st.caption("此队列最多定位前 100,000 条；缩小筛选范围以继续审查其余轨迹。")
    if pages > 1:
        jumper, previous, next_page = st.columns([1.2, 1, 1])
        with jumper:
            st.number_input("跳转页码", min_value=1, max_value=pages, value=page,
                            step=1, key=page_key)
        previous.button("上一页轨迹", disabled=page <= 1, key=f"{prefix}:previous",
                        on_click=lambda: st.session_state.__setitem__(page_key, page - 1), width="stretch")
        next_page.button("下一页轨迹", disabled=page >= pages, key=f"{prefix}:next",
                         on_click=lambda: st.session_state.__setitem__(page_key, page + 1), width="stretch")
    items = queue["items"]
    if not items:
        st.info("此条件下没有轨迹。可切换状态或失败轨迹。")
        return

    by_id = {item["candidate_id"]: item for item in items}
    selection_key = f"{prefix}:selected:{kind}:{decision or 'all'}:{size}:{page}"
    if st.session_state.get(selection_key) not in by_id:
        st.session_state[selection_key] = items[0]["candidate_id"]
    queue_col, content_col, action_col = st.columns([1.1, 2.7, 1.05], gap="medium")
    with queue_col, st.container(border=True):
        st.subheader("轨迹列表")
        candidate_id = st.radio("选择轨迹", list(by_id), key=selection_key,
                                format_func=lambda key: UntranslatedText(_queue_label(by_id[key], kind, language)),
                                label_visibility="collapsed")
    item = by_id[candidate_id]
    row = item["native"] if kind == "positive" else item["negative"]
    with content_col, st.container(border=True):
        st.subheader("对话与工具过程")
        st.html(_facts(item, kind, language))
        if kind == "positive":
            record = item["record"]
            preview = {**row, **{field: record[field] for field in (
                "source_name", "source_location", "evidence_level", "verification") if field in record}}
        else:
            preview = row
        render_sample_preview("agent" if kind == "positive" else "agent_negative", preview,
                              key=f"{prefix}:preview:{candidate_id}", widgets=st)
    with action_col, st.container(border=True):
        st.subheader("审核结论" if kind == "positive" else "失败说明")
        if kind == "negative":
            failure = row.get("failure")
            st.warning(_copy(_FAILURES.get(failure, "失败原因见轨迹与执行证据。"), language))
            st.caption("此队列只读；失败记录不会混入正样本。")
            return
        st.html(_history(item.get("review"), language))
        if not item["independent_replay"]:
            st.warning("此候选缺少可独立复核的重放条件。可以退回，暂不能批准。")
        current = item["review_status"]
        with st.form(f"{prefix}:form:{candidate_id}"):
            reason = st.text_area("审核意见", max_chars=2000, height=95,
                                  value=(item.get("review") or {}).get("reason", ""),
                                  help="退回时请说明问题；意见会与来源和证据版本一起保存。")
            approve = st.form_submit_button("通过", type="primary", width="stretch",
                                            key=f"{prefix}:approve:{candidate_id}",
                                            disabled=not item["independent_replay"] or current == "approved")
            reject = st.form_submit_button("退回", width="stretch", key=f"{prefix}:reject:{candidate_id}",
                                           disabled=current == "rejected")
        if approve or reject:
            if reject and not reason.strip():
                st.warning("请填写退回原因，方便定位并修复轨迹。")
                return
            try:
                from lib.review_management import reviewer_identity

                application.agent_review_decide(
                    run_id, candidate_id, source_signature=queue["source_signature"],
                    native_sha256=item["native_sha256"], record_sha256=item["record_sha256"],
                    expected_version=item["review_version"],
                    decision="approved" if approve else "rejected",
                    reviewer=reviewer_identity(), reason=reason,
                )
            except (OSError, ValueError, PermissionError, TypeError, KeyError) as error:
                st.error(_error_message(error))
            else:
                st.session_state.pop(selection_key, None)
                st.rerun()
