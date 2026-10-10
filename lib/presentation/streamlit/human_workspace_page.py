"""A persistent human working window: design, inspect and revise in one place."""
from __future__ import annotations

from copy import deepcopy
import html
import uuid

import streamlit as st
from filelock import Timeout

from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.i18n import UntranslatedText, translate_label
from lib.presentation.streamlit.human_augmentation_controls import human_snapshot, render_human_designs
from lib.presentation.streamlit.workflow_canvas import canvas_spec, render_canvas
from lib.presentation.streamlit.workflow_stream_view import render_stream_output
from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS, LABELS, _workflow_error, _stage_configuration, _config_html
from lib.presentation.streamlit.model_scheduling_controls import render_run_scheduling


WORKSPACE_STYLE = """<style>
body:has(.st-key-human-working-window) .df-page-hero{min-height:0;padding:15px 184px 15px 22px}
body:has(.st-key-human-working-window) .df-page-copy h1{font-size:27px!important}
body:has(.st-key-human-working-window) .df-page-copy p{margin-bottom:0}
.st-key-human-working-window [data-testid="stVerticalBlock"]{gap:.75rem}
.st-key-human-working-window [data-testid="stVerticalBlockBorderWrapper"]{border-color:#d6e4f1!important;border-radius:18px!important;background:linear-gradient(145deg,#fff,#f8fbff);box-shadow:inset 0 1px 0 #fff,0 4px 12px -5px #365f8e22}
.df-human-loop{display:flex;align-items:center;gap:12px;padding:10px 14px;border-radius:10px;background:#eff6ff;color:#355b86;font-size:13px;border:1px solid #dce9f7}.df-human-loop b{font-size:19px;color:#347ad5}
.df-human-result{padding:14px 16px;border:1px solid #dce6f2;border-radius:13px;background:#fff;box-shadow:0 2px 5px #315f9110;margin-bottom:9px}.df-human-result b{font-size:12px;color:#3772ae}.df-human-result div{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.75;font-size:14px;margin-top:6px;color:#253b55}.df-human-result[data-role="user"]{background:linear-gradient(135deg,#f2f7ff,#fff)}
.df-human-empty{padding:27px 22px;border:1px dashed #cbdcf0;border-radius:14px;background:radial-gradient(ellipse at 92% 15%,#dcecff77,transparent 60%),#f8fbff;color:#58718e;font-size:14px;line-height:1.8}.df-human-empty strong{display:block;color:#244466;font-size:17px;margin-bottom:5px}
</style>"""


def _error(error):
    fallback = _workflow_error(error)
    if fallback == str(error) and str(error).startswith(("human_session_", "invalid_human_session_")):
        fallback = "人工增强记录暂不可读取，请刷新工作状态，或从工作管理重新打开。"
    return {
        "human_session_version_conflict": "工作已在另一个窗口更新。当前输入保留，请刷新工作状态后再提交。",
        "human_session_round_pending": "本轮尚未结束，请继续或停止本轮后再提交下一轮。",
        "human_session_revision_limit": "这条结果已达到修订上限。请检查设计要求，或重新设计问答。",
        "human_session_feedback_required": "请先填写具体修正意见，再提交回流。",
        "human_session_design_required": "请先在左侧填写问题与参考答案，再生成本轮话术。",
        "human_session_result_changed": "结果内容与提交的版本不一致，请重新选择结果后再提交意见。",
        "human_session_candidate_not_found": "这条结果暂不可读取，请刷新工作状态后重新选择。",
        "human_session_configuration_changed": "工作配置校验失败，已停止继续处理。请检查本机工作文件。",
        "human_session_model_configuration_changed": "节点模型连接已变化，请返回数据生成重新配置工作。已有结果保留。",
        "human_session_requires_qa_targets": "人工问答增强支持对话、推理和偏好目标；请在工作台调整训练目标。",
        "human_session_round_limit": "当前工作已达到轮次上限。请保留结果并新建人工增强工作。",
        "invalid_human_session_text": "设计或修正意见无效，请检查文本内容与大小限制。",
        "human_workspace_unavailable": "人工增强工作室暂不可用，请重新打开应用。",
    }.get(str(error), fallback)


def _context(ws, session_id):
    return f"human:{ws}:{session_id}"


def _save_design(context, key):
    """Save the complete editor before any conditional widget is unmounted."""
    draft_key = f"workflow-form-draft:{context}"
    draft = st.session_state.setdefault(draft_key, {})
    draft[key] = deepcopy(st.session_state[key])
    application = st.session_state[f"human-application:{context}"]
    try:
        result = application.save_draft(st.session_state[f"human-id:{context}"],
            human_snapshot(context), expected_version=st.session_state[f"human-version:{context}"])
        st.session_state[f"human-version:{context}"] = result["version"]
        st.session_state.pop(f"human-save-error:{context}", None)
    except (ValueError, OSError, Timeout) as error:
        st.session_state[f"human-save-error:{context}"] = _error(error)


def _load_editor(context, session, application):
    st.session_state[f"human-application:{context}"] = application
    st.session_state[f"human-id:{context}"] = session["id"]
    if f"human-version:{context}" in st.session_state:
        return
    st.session_state[f"human-version:{context}"] = session["version"]
    draft = session.get("draft") or {"enabled": True, "seeds": []}
    values = {f"workflow-human-enabled:{context}": True,
              f"workflow-human-seeds:{context}": deepcopy(draft.get("seeds", [])),
              f"workflow-human-question-requirements:{context}": draft.get("question_requirements", ""),
              f"workflow-human-answer-requirements:{context}": draft.get("answer_requirements", "")}
    st.session_state[f"workflow-form-draft:{context}"] = values
    st.session_state.update(values)


def _launch(application, session_id, context, begin, *, revise=False, feedback_ids=None, sample_count=6):
    # Keep this key across failed launch/response retries. Only a successfully
    # persisted round consumes it; duplicate submissions resolve to that round.
    request_key = f"human-request:{context}"
    request_id = st.session_state.setdefault(request_key, uuid.uuid4().hex)
    method = application.revise_round if revise else application.generate_round
    options = {"request_id": request_id, "expected_version": st.session_state[f"human-version:{context}"],
               "sample_count": int(sample_count)}
    if revise:
        options["selected_feedback_ids"] = feedback_ids
    round_row = method(session_id, **options)
    fresh = application.session(session_id)
    st.session_state[f"human-version:{context}"] = fresh["version"]
    # A feedback submit happens after the version picker was rendered. Apply
    # the selection before mounting that widget on the next full render.
    st.session_state[f"human-round-pending:{context}"] = round_row["id"]
    st.session_state.pop(request_key, None)
    begin(["workflow", "--action", "resume", "--run-id", round_row["run_id"]])
    return round_row


def _render_record(row):
    record = row.get("record", {})
    status = record.get("status")
    if status == "eligible":
        st.caption("已通过本轮模型与规则评审，仍可提出修正意见。")
    elif status:
        st.warning("此候选未通过评审，不会混入合格训练数据；可填写意见回流修正。")
        if record.get("reason"):
            reason = str(record["reason"])
            if reason.startswith(("human_", "invalid_human_")):
                st.text(_error(reason))
            else:
                st.text(UntranslatedText(reason))
    messages = row.get("messages") or record.get("messages")
    if not messages:
        messages = [{"role": "user", "content": row.get("question", record.get("question", ""))},
                    {"role": "assistant", "content": row.get("answer", record.get("answer", ""))}]
    language = st.session_state.get("ui_language", "zh")
    for message in messages:
        role = message.get("role", "assistant")
        label = translate_label("问题" if role == "user" else "回答" if role == "assistant" else "上下文", language)
        st.html(f'<article class="df-human-result" data-role="{html.escape(role, quote=True)}"><b>{html.escape(label)}</b>'
                f'<div data-user-content>{html.escape(str(message.get("content", "")))}</div></article>')
        reasoning = message.get("reasoning_content") or (record.get("reasoning") if role == "assistant" else None)
        if reasoning:
            with st.expander("查看推理内容"):
                st.text(UntranslatedText(str(reasoning)))


@st.fragment(run_every=2)
def _live_workflow(workflows, backends, session, round_row, context):
    recipe = session["blueprint"]
    run_id = round_row.get("run_id") if round_row else None
    try:
        state = workflows.state(run_id) if run_id else {"stages": {}}
        active = workflows.is_active(run_id) if run_id else False
        if run_id:
            recipe = workflows.recipe(run_id)
    except (ValueError, OSError):
        st.warning("本轮工作记录暂不可读取，已保留设计与历史结果。")
        return
    if run_id:
        status_key = f"human-observed-state:{context}:{run_id}"
        previous = st.session_state.get(status_key)
        observed = (state.get("status"), active)
        st.session_state[status_key] = observed
        if previous is not None and previous != observed:
            st.rerun(scope="app")
    selection_key = f"workflow-stage:{run_id}" if run_id else f"human-stage:{context}"
    selected = st.session_state.get(selection_key, "sft")
    spec = canvas_spec(recipe["targets"], state.get("stages", {}), selected, GRAPH_LABELS, STAGE_GLYPHS,
        recipe.get("node_models", {}), language=st.session_state.get("ui_language", "zh"),
        live=bool(run_id), source_mode="人工设计", reasoning_trim=(recipe.get("reasoning_trim") or {}).get("enabled", False),
        node_generation=recipe.get("node_generation"), qa_director=recipe.get("qa_director"),
        package_review=recipe.get("package_review"), recipe_version=recipe.get("version", 17))
    if selected not in {node["id"] for node in spec["nodes"]}:
        selected = spec["nodes"][0]["id"]
        st.session_state[selection_key] = selected
        spec["selected"] = selected
    spec["viewport_height"] = 180
    spec["labels"]["lineage"] = translate_label("回流使用新版本，已完成结果和评审记录保留。", st.session_state.get("ui_language", "zh"))
    render_canvas(spec, selection_key, key=f"human-flow:{context}:{run_id or 'setup'}")
    loop, configuration = st.columns([4, 1], gap="small", vertical_alignment="center")
    with loop:
        st.html('<div class="df-human-loop"><b>↺</b><span>选择结果 → 人工修正意见 → 重新生成与评审 → 查看新版本</span></div>')
    with configuration, st.popover("查看所选节点配置", width="stretch"):
        st.html(_config_html(_stage_configuration(st.session_state.get(selection_key, selected), recipe, state)))
        render_run_scheduling(backends, recipe.get("node_models", {}).get(st.session_state.get(selection_key, selected), {}))
    if run_id:
        st.caption(translate_label(LABELS.get(state.get("status", "queued"), "等待"), st.session_state.get("ui_language", "zh")))
        if active:
            st.caption("生成期间可以继续编辑下一轮设计；当前轮使用已保存的快照。")
    # Selecting a node opens its real stream. Do not reuse a stale stage while
    # the fragment's canvas is committing a new selection.
    selected = st.session_state.get(selection_key, selected)
    if run_id and st.session_state.get(f"canvas-event:human-flow:{context}:{run_id}"):
        st.session_state[f"canvas-open:live-canvas:{run_id}"] = True
        render_stream_output(workflows, run_id, selected, reader_height=240)


def render_human_workspace(application, workflows, begin, workspace, *, session_id=None, backend_application=None, navigate=None):
    page_header("人工问答增强工作室", "在同一窗口反复设计问答、查看生成结果，再用人工意见回流修正。", "HUMAN × AI", art_kind="hero")
    st.html(WORKSPACE_STYLE)
    try:
        rows = application.list_sessions()
    except (ValueError, OSError, Timeout) as error:
        st.error(_error(error))
        return
    session_id = session_id or st.session_state.get(f"human-selected:{workspace}")
    if session_id is None and rows:
        session_id = rows[0]["id"]
    if not session_id:
        st.info("还没有人工增强工作。请在数据生成中选择人工问答增强，再点击启动。")
        if navigate and st.button("配置人工增强工作", type="primary"):
            navigate("自动工作流")
            st.rerun()
        return
    try:
        session = application.session(session_id)
    except (ValueError, OSError, Timeout) as error:
        st.error(_error(error))
        if navigate and st.button("配置人工增强工作", type="primary"):
            navigate("自动工作流")
            st.rerun()
        return
    st.session_state[f"human-selected:{workspace}"] = session_id
    context = _context(workspace, session_id)
    _load_editor(context, session, application)
    with st.container(key="human-working-window"):
        history, actions = st.columns([3, 2], gap="medium", vertical_alignment="bottom")
        with history:
            choices = [row["id"] for row in rows]
            if session_id not in choices:
                choices.insert(0, session_id)
            names = {row["id"]: row.get("name", row["id"][:8]) for row in rows}
            selected_session = st.selectbox("历史工作", choices, index=choices.index(session_id),
                format_func=lambda value: UntranslatedText(names.get(value, session.get("name", ""))), key=f"human-history:{session_id}")
            if selected_session != session_id:
                st.session_state[f"human-selected:{workspace}"] = selected_session
                st.query_params["human"] = selected_session
                st.rerun()
        with actions:
            if st.button("刷新工作状态", key=f"human-refresh:{context}", width="stretch"):
                # Preserve local editor text after a failed CAS save. Reload
                # only its version; the next explicit save retries the draft.
                st.session_state[f"human-version:{context}"] = session["version"]
                st.session_state.pop(f"human-save-error:{context}", None)
                st.rerun()
        st.caption(UntranslatedText(
            f"Up to {session['limits']['max_revision_depth']} revisions per result. Each revision needs your submission." if st.session_state.get("ui_language", "zh") == "en" else
            f"每条结果最多回流 {session['limits']['max_revision_depth']} 次；每次都由人工提交，不会自动反复调用模型。"))
        rounds = session.get("rounds", [])
        current = next((row for row in reversed(rounds) if row["id"] == session.get("current_round_id")), None)
        _live_workflow(workflows, backend_application, session, current, context)
        left, right = st.columns([1, 1.25], gap="medium")
        with left, st.container(border=True, key="human-design-editor"):
            section_heading("问答设计", "修改设计后生成下一轮；历史结果始终保留。", "✎")
            render_human_designs(context, save_field=_save_design)
            if st.session_state.get(f"human-save-error:{context}"):
                st.error(st.session_state[f"human-save-error:{context}"])
            size, action = st.columns([1, 1.4], vertical_alignment="bottom")
            with size:
                sample_count = st.number_input("本轮候选上限", min_value=1, max_value=1000, value=6,
                    key=f"human-count:{context}", help="这是上限，不要求凑满。资料和有效变化不足时允许少产出。")
            active = any(row.get("active") or row.get("status") in {
                "queued", "prepared", "running", "interrupted", "creating", "cancel_requested"} for row in rounds)
            with action:
                if st.button("生成本轮话术", type="primary", key=f"human-generate:{context}",
                             disabled=active or bool(st.session_state.get(f"human-save-error:{context}")), width="stretch"):
                    try:
                        _launch(application, session_id, context, begin, sample_count=sample_count)
                        st.rerun()
                    except (ValueError, OSError, Timeout) as error:
                        st.error(_error(error))
            if current and current.get("launchable") and not current.get("active"):
                if st.button("继续本轮", key=f"human-resume:{context}", width="stretch"):
                    try:
                        row = application.resume_round(session_id, current["id"])
                        st.session_state[f"human-version:{context}"] = application.session(session_id)["version"]
                        begin(["workflow", "--action", "resume", "--run-id", row["run_id"]])
                        st.rerun()
                    except (ValueError, OSError, Timeout) as error:
                        st.error(_error(error))
            if current and active and st.button("停止本轮", key=f"human-stop:{context}"):
                try:
                    application.cancel_round(session_id, current["id"])
                    st.session_state[f"human-version:{context}"] = application.session(session_id)["version"]
                    st.rerun()
                except (ValueError, OSError, Timeout) as error:
                    st.error(_error(error))
        with right, st.container(border=True, key="human-results-editor"):
            section_heading("结果与回流修正", "选择结果，填写意见，再查看重新生成与评审后的版本。", "↺")
            if not rounds:
                st.html('<div class="df-human-empty"><strong>从一组问答开始</strong>在左侧写下问题和参考答案，点击生成本轮话术。结果与修正记录会留在这个窗口。</div>')
                return
            round_ids = [row["id"] for row in rounds]
            selected_round_key = f"human-round:{context}"
            pending_round = st.session_state.pop(f"human-round-pending:{context}", None)
            if pending_round in round_ids:
                st.session_state[selected_round_key] = pending_round
            if st.session_state.get(selected_round_key) not in round_ids:
                st.session_state[selected_round_key] = current["id"] if current else round_ids[-1]
            language = st.session_state.get("ui_language", "zh")
            round_labels = {row["id"]: (f"Round {index + 1} · " if language == "en" else f"第 {index + 1} 轮 · ") +
                translate_label("人工回流" if row.get("kind") == "revision" else "问答增强", language) for index, row in enumerate(rounds)}
            selected_round = st.selectbox("结果版本", round_ids, format_func=round_labels.__getitem__, key=selected_round_key)
            targets = list(session["blueprint"]["targets"])
            target = st.segmented_control("结果类型", targets, default=targets[0], key=f"human-target:{context}:{selected_round}") or targets[0]
            offset_key = f"human-offset:{context}:{selected_round}:{target}"
            offset = st.session_state.get(offset_key, 0)
            try:
                result = application.results(session_id, round_id=selected_round, target=target, offset=offset, limit=10)
            except (ValueError, OSError, Timeout) as error:
                st.error(_error(error))
                return
            candidates = result["rows"]
            if not candidates:
                st.info("本轮还没有可用结果。可点击工作流节点看实时输出；完成后刷新工作状态。")
                return
            st.caption(f"{offset + 1}–{offset + len(candidates)} / {result['total']}")
            previous, following = st.columns(2)
            with previous:
                st.button("上一组结果", key=f"human-prev:{context}:{selected_round}:{target}", disabled=offset == 0,
                    on_click=st.session_state.update, args=({offset_key: max(0, offset - 10)},), width="stretch")
            with following:
                st.button("下一组结果", key=f"human-next:{context}:{selected_round}:{target}", disabled=not result["has_more"],
                    on_click=st.session_state.update, args=({offset_key: offset + 10},), width="stretch")
            by_id = {row["candidate_id"]: row for row in candidates}
            selected_id = st.selectbox("选择结果", list(by_id), key=f"human-result:{context}:{selected_round}:{target}:{offset}",
                format_func=lambda value: UntranslatedText(str(by_id[value].get("question", value))[:90]))
            row = by_id[selected_id]
            _render_record(row)
            if row.get("lineage"):
                st.caption("此结果来自人工回流；上一个版本仍保留在结果版本列表中。")
            with st.form(f"human-feedback:{context}:{selected_round}:{target}:{selected_id}"):
                instruction = st.text_area("修正意见", max_chars=6000, height=105,
                    help="说明哪里不合适、应如何调整。意见会进入新一轮生成与评审，不会被当作训练答案直接导出。")
                with st.expander("直接修改问题或参考答案（可选）"):
                    question = st.text_area("修订问题", max_chars=12000)
                    answer = st.text_area("修订参考答案", max_chars=12000)
                revise = st.form_submit_button("提交意见并回流修正", type="primary", disabled=active, width="stretch")
            if revise:
                try:
                    feedback = application.save_feedback(session_id, round_id=selected_round, target=target, candidate_id=selected_id, instruction=instruction,
                        question=question.strip() or None, answer=answer.strip() or None,
                        expected_version=st.session_state[f"human-version:{context}"])
                    st.session_state[f"human-version:{context}"] = application.session(session_id)["version"]
                    _launch(application, session_id, context, begin, revise=True,
                            feedback_ids=[feedback["feedback_id"]], sample_count=1)
                    st.rerun()
                except (ValueError, OSError, Timeout) as error:
                    st.error(_error(error))
            if row.get("feedback"):
                with st.expander("此结果的人工意见记录"):
                    st.json(row["feedback"])


def render_human_history(application, workspace):
    try:
        rows = application.list_sessions()
    except (ValueError, OSError, Timeout) as error:
        st.error(_error(error))
        return
    if not rows:
        st.info("还没有人工增强工作。启动后会自动保存在这里。")
    for row in rows:
        with st.container(border=True):
            name, action = st.columns([4, 1], vertical_alignment="center")
            with name:
                st.text(UntranslatedText(str(row.get("name", row["id"]))))
                st.caption(translate_label("人工问答增强", st.session_state.get("ui_language", "zh")))
            with action:
                if st.button("继续设计", key=f"human-history-open:{row['id']}", width="stretch"):
                    st.session_state["human-open-session"] = {"workspace": workspace, "session_id": row["id"]}
                    st.rerun()
