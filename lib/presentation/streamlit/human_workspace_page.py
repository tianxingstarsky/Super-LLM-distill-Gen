"""A persistent human working window: design, inspect and revise in one place."""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
import html
import json
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
from lib.domain.human_round_projection import project_feedback_branch
from lib.domain.workflow_targets import TRAINING_FIELDS, training_record


WORKSPACE_STYLE = """<style>
body:has(.st-key-human-working-window) .df-page-hero{min-height:0;padding:15px 184px 15px 22px}
body:has(.st-key-human-working-window) .df-page-copy h1{font-size:27px!important}
body:has(.st-key-human-working-window) .df-page-copy p{margin-bottom:0}
.st-key-human-working-window [data-testid="stVerticalBlock"]{gap:.75rem}
.st-key-human-working-window [data-testid="stVerticalBlockBorderWrapper"]{border-color:#d6e4f1!important;border-radius:18px!important;background:linear-gradient(145deg,#fff,#f8fbff);box-shadow:inset 0 1px 0 #fff,0 4px 12px -5px #365f8e22}
.df-human-assessment{padding:12px 15px;margin-bottom:10px;border:1px solid #d4e5f6;border-radius:12px;background:linear-gradient(130deg,#eff6ff,#fff);color:#345b83;font-size:13px;line-height:1.65}.df-human-assessment strong{display:block;color:#234769;font-size:15px;margin-bottom:4px}.df-human-assessment[data-route="rejected"]{border-color:#eedbc8;background:linear-gradient(130deg,#fff5e9,#fff)}
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
        "invalid_manual_review_record": "候选内容格式无效，请检查对话、答案和推理字段。",
        "invalid_manual_review_score": "人工评分需在 0 到 100 之间。",
        "human_review_below_threshold": "通过结论的评分低于通过分数，请调整评分或改为不通过。",
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
        st.caption("已通过本轮导出检查；可保留或提出修正意见。")
    elif record.get("reason") in {"human_review_required", "manual_review_required"}:
        st.info("此候选等待人工评分和修正，尚未进入训练文件。")
    elif status:
        st.warning("此候选未通过评审，不会混入合格训练数据；可填写意见回流修正。")
        if record.get("reason"):
            reason = str(record["reason"])
            if reason.startswith(("human_", "invalid_human_", "manual_", "review_")):
                st.text(_error(reason))
            else:
                st.text(UntranslatedText(reason))
    language = st.session_state.get("ui_language", "zh")
    if row.get("target") == "cpt" or "text" in record:
        st.html('<article class="df-human-result"><b>' + html.escape(translate_label("语料内容", language)) + '</b>'
            f'<div data-user-content>{html.escape(str(record.get("text", "")))}</div></article>')
        return
    if row.get("target") in {"dpo", "orpo", "rlaif"}:
        for field, label in (("prompt", "问题与上下文"), ("chosen", "优选答案"), ("rejected", "对照答案"),
                             ("responses", "候选回答")):
            if field in record:
                st.caption(translate_label(label, language))
                st.json(record[field])
        return
    messages = row.get("messages") or record.get("messages")
    if not messages:
        messages = [{"role": "user", "content": row.get("question", record.get("question", ""))},
                    {"role": "assistant", "content": row.get("answer", record.get("answer", ""))}]
    for message in messages:
        role = message.get("role", "assistant")
        label = translate_label("问题" if role == "user" else "回答" if role == "assistant" else "上下文", language)
        st.html(f'<article class="df-human-result" data-role="{html.escape(role, quote=True)}"><b>{html.escape(label)}</b>'
                f'<div data-user-content>{html.escape(str(message.get("content", "")))}</div></article>')
        reasoning = message.get("reasoning_content") or (record.get("reasoning") if role == "assistant" else None)
        if reasoning:
            with st.expander("查看推理内容"):
                st.text(UntranslatedText(str(reasoning)))


def _render_manual_review(application, session_id, context, begin, round_id, target,
                          candidate_id, row, projection, *, disabled):
    """One form owns both the human verdict and the exact edited candidate."""
    record = deepcopy(row.get("record") or {})
    messages = deepcopy(row.get("messages") or record.get("messages") or [])
    form_key = f"human-manual-review:{context}:{round_id}:{target}:{candidate_id}"
    review_config = application.session(session_id)["blueprint"].get("review_repair") or {}
    threshold = int(round(float(review_config.get("score_threshold", .8)) * 100))
    with st.form(form_key):
        st.caption("人工评分和修正一起提交。修改后的内容另存为新版本；未通过或未评分的内容不进入训练文件。")
        score, decision = st.columns(2, gap="small")
        with score:
            human_score = st.number_input("人工评分（0–100）", min_value=0, max_value=100,
                value=threshold, step=5, key=form_key + ":score")
        with decision:
            human_decision = st.selectbox("人工结论", ("approve", "reject"),
                format_func=lambda value: translate_label("通过" if value == "approve" else "不通过",
                    st.session_state.get("ui_language", "zh")), key=form_key + ":decision")
        # A complete target payload keeps reasoning, continuous dialogue and
        # preference alternatives visible instead of collapsing them to one QA.
        if target in {"sft", "multiturn"} and len(messages) == 2 and messages[0].get("role") == "user" and messages[1].get("role") == "assistant":
            question = st.text_area("修订问题", value=str(messages[0].get("content", "")),
                max_chars=12000, key=form_key + ":question")
            answer = st.text_area("修订答案", value=str(messages[1].get("content", "")),
                max_chars=32000, key=form_key + ":answer", height=150)
            reasoning = st.text_area("修订推理（可选）", value=str(messages[1].get("reasoning_content", "")),
                max_chars=32000, key=form_key + ":reasoning", height=100)
            payload_text = None
        else:
            fields = TRAINING_FIELDS.get(target, ())
            payload = {key: deepcopy(record[key]) for key in fields if key in record}
            if target == "rlaif":
                try:
                    exported = training_record(target, record)
                    payload = {key: deepcopy(exported[key]) for key in fields if key in exported}
                except (KeyError, ValueError):
                    # Missing AI labels cannot be supplied by a human score.
                    # Keep the two original responses editable without making
                    # up AI dimensions or feedback for a quarantined pair.
                    payload["responses"] = [
                        {"response": deepcopy(record.get(key, [])), "preference_rank": rank}
                        for rank, key in enumerate(("chosen", "rejected"), start=1)]
                    payload["criterion"] = (record.get("rlaif") or {}).get("criterion", "")
            if messages and target in {"sft", "multiturn", "agent"}:
                payload["messages"] = messages
            payload_text = st.text_area("完整候选内容（JSON）", value=json.dumps(payload, ensure_ascii=False, indent=2),
                max_chars=96000, height=240, key=form_key + ":payload",
                help="在这里编辑完整对话、推理字段或偏好对。来源、工具事实和原版本记录由系统保留。")
            question = answer = reasoning = None
        instruction = st.text_area("评分与修正说明", max_chars=6000, height=100,
            value=str(projection.get("reviewer_feedback") or "")[:6000], key=form_key + ":instruction")
        submitted = st.form_submit_button("提交人工评分与修正", type="primary", width="stretch",
            disabled=disabled)
    if not submitted:
        return
    request_key = form_key + ":request"
    request_id = st.session_state.setdefault(request_key, uuid.uuid4().hex)
    try:
        if payload_text is not None:
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError as error:
                raise ValueError("invalid_manual_review_record") from error
            if not isinstance(payload, dict):
                raise ValueError("invalid_manual_review_record")
        else:
            messages[0]["content"], messages[1]["content"] = question, answer
            if reasoning:
                messages[1]["reasoning_content"] = reasoning
            else:
                messages[1].pop("reasoning_content", None)
            payload = {"messages": messages}
        round_row = application.submit_manual_review(session_id, round_id=round_id, target=target,
            candidate_id=candidate_id, request_id=request_id, score=human_score,
            decision=human_decision, instruction=instruction, corrected_record=payload,
            expected_version=st.session_state[f"human-version:{context}"])
        st.session_state[f"human-version:{context}"] = application.session(session_id)["version"]
        st.session_state[f"human-round-pending:{context}"] = round_row["id"]
        st.session_state.pop(request_key, None)
        begin(["workflow", "--action", "resume", "--run-id", round_row["run_id"]])
        st.rerun()
    except (ValueError, OSError, Timeout) as error:
        st.error(_error(error))


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
    result = st.session_state.get(f"human-branch-result:{context}")
    if result and result.get("round_id") != (round_row or {}).get("id"):
        result = None
    target = (result or {}).get("target") or next(iter(recipe["targets"]), "sft")
    pending_round = next((row["id"] for row in session.get("rounds", []) if row.get("active") or row.get("status") in {
        "queued", "prepared", "running", "interrupted", "creating", "cancel_requested"}), None)
    projection = project_feedback_branch(recipe, state,
        {**(round_row or {}), "limits": session.get("limits", {}), "active": active, "pending_round_id": pending_round},
        selected_result=result, target=target)
    spec = canvas_spec(recipe["targets"], state.get("stages", {}), selected, GRAPH_LABELS, STAGE_GLYPHS,
        recipe.get("node_models", {}), language=st.session_state.get("ui_language", "zh"),
        live=bool(run_id), source_mode=("文档资料" if recipe.get("sources") else "开放需求") if session.get("review_only") else "人工设计", reasoning_trim=(recipe.get("reasoning_trim") or {}).get("enabled", False),
        node_generation=recipe.get("node_generation"), qa_director=recipe.get("qa_director"),
        package_review=recipe.get("package_review"), review_repair=recipe.get("review_repair"),
        recipe_version=recipe.get("version", 18), repair_only=bool(recipe.get("repair_inputs")), feedback_branch=projection)
    if selected not in {node["id"] for node in spec["nodes"]}:
        selected = spec["nodes"][0]["id"]
        st.session_state[selection_key] = selected
        spec["selected"] = selected
    spec["viewport_height"] = 320
    spec["feedback_target"] = "human-results-editor"
    canvas_key = f"human-flow:{context}:{run_id or 'setup'}"
    render_canvas(spec, selection_key, key=canvas_key)
    feedback_event = st.session_state.pop(f"canvas-feedback:{canvas_key}", None)
    serial = st.session_state.get(f"canvas-event:{canvas_key}")
    observed_key = f"human-canvas-observed:{canvas_key}"
    stream_key = f"human-canvas-stream:{canvas_key}"
    if serial is not None and serial != st.session_state.get(observed_key):
        st.session_state[observed_key] = serial
        st.session_state[stream_key] = feedback_event is None
    explanation, configuration = st.columns([4, 1], gap="small", vertical_alignment="center")
    with explanation:
        st.caption("评分与修正在同一个节点完成。修正分支只修改当前候选，随后重新评分；不会回到 SFT 生成节点。")
        if feedback_event is not None:
            st.caption("已打开下方修正分支。请选择具体结果，确认意见后提交。")
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
    if run_id and st.session_state.get(stream_key):
        st.session_state[f"canvas-open:live-canvas:{run_id}"] = True
        render_stream_output(workflows, run_id, selected, reader_height=240)


def render_human_workspace(application, workflows, begin, workspace, *, session_id=None, backend_application=None, navigate=None):
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
    review_only = bool(session.get("review_only"))
    page_header("评分与修正工作室" if review_only else "人工问答增强工作室",
        "查看候选、评分和修改内容；修正结果在本节点重新核验。" if review_only else
        "在同一窗口反复设计问答、查看结果，并在评分与修正节点改善候选。", "HUMAN × AI", art_kind="hero")
    st.session_state[f"human-selected:{workspace}"] = session_id
    context = _context(workspace, session_id)
    _load_editor(context, session, application)
    with st.container(key="human-working-window"):
        history, actions, exit_action = st.columns([3.4, 1.1, 1.2], gap="small", vertical_alignment="bottom")
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
        with exit_action:
            if navigate:
                st.button("返回工作流" if review_only else "退出人工增强", key=f"human-exit:{context}", width="stretch",
                    icon=":material/logout:", on_click=navigate, args=("自动工作流",),
                    help="返回数据生成。设计、结果和运行任务保留在工作管理中。")
        st.caption(UntranslatedText(
            f"Up to {session['limits']['max_revision_depth']} revisions per result. Each revision needs your submission." if st.session_state.get("ui_language", "zh") == "en" else
            f"每条结果最多回流 {session['limits']['max_revision_depth']} 次；每次都由人工提交，不会自动反复调用模型。"))
        rounds = session.get("rounds", [])
        current = next((row for row in reversed(rounds) if row["id"] == session.get("current_round_id")), None)
        round_ids = [row["id"] for row in rounds]
        selected_round_key = f"human-round:{context}"
        pending_round = st.session_state.pop(f"human-round-pending:{context}", None)
        if pending_round in round_ids:
            st.session_state[selected_round_key] = pending_round
        if round_ids and st.session_state.get(selected_round_key) not in round_ids:
            st.session_state[selected_round_key] = current["id"] if current else round_ids[-1]
        shown_round = next((row for row in rounds if row["id"] == st.session_state.get(selected_round_key)), current)
        _live_workflow(workflows, backend_application, session, shown_round, context)
        active = any(row.get("active") or row.get("status") in {
            "queued", "prepared", "running", "interrupted", "creating", "cancel_requested"} for row in rounds)
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
        left, right = st.columns([1, 1.25], gap="medium") if not review_only else (nullcontext(), st.container())
        if not review_only:
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
        with right, st.container(border=True, key="human-results-editor"):
            section_heading("评分与修正", "评分、修改内容和重新核验都在同一个节点完成。", "✦")
            if not rounds:
                st.html('<div class="df-human-empty"><strong>从一组问答开始</strong>在左侧写下问题和参考答案，点击生成本轮话术。结果与修正记录会留在这个窗口。</div>')
                return
            language = st.session_state.get("ui_language", "zh")
            round_labels = {row["id"]: (f"Round {index + 1} · " if language == "en" else f"第 {index + 1} 轮 · ") +
                translate_label("人工评分与修正" if row.get("kind") == "manual_review" else
                    "候选修正" if row.get("kind") == "revision" else "问答增强", language) for index, row in enumerate(rounds)}
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
            st.session_state[f"human-branch-result:{context}"] = row
            try:
                projection = application.branch_projection(session_id, round_id=selected_round, target=target, result=row)
            except (ValueError, OSError, Timeout) as error:
                st.error(_error(error))
                return
            selected_result = projection.get("selected_result") or {}
            can_revise = bool(selected_result.get("can_revise"))
            route = selected_result.get("route", "unknown")
            pending_human = row.get("record", {}).get("reason") in {"human_review_required", "manual_review_required"}
            outcome = ("待人工评分与修正" if pending_human else "已通过 · 可保留或继续改善" if route == "accepted" else
                       "未通过 · 进入修正分支" if route == "rejected" else "评审尚未完成")
            st.html(f'<div class="df-human-assessment" data-route="{html.escape(route, quote=True)}"><strong>{outcome}</strong>'
                    '<span>当前候选评分 → 修正分支修改 → 回到评分 → 通过保留 / 未通过待处理</span></div>')
            if selected_result.get("checks"):
                with st.expander("查看本结果的评审依据", expanded=route == "rejected"):
                    for check in selected_result["checks"]:
                        verdict = check.get("verdict", {})
                        check_label = ("人工评分" if check["source"] == "human_review" else
                            "评分与修正" if check["source"] == "review" else
                            ("JEV 评分" if (session["blueprint"].get("package_review") or {}).get("node") == "jev" else "AI 评审")
                            if check["source"] == "package_review" else "生成内质量核对")
                        st.caption(check_label + (" · 通过" if check["status"] == "accepted" else " · 未通过"))
                        if verdict.get("scores"):
                            st.json(verdict["scores"])
                        if check.get("reason"):
                            st.text(UntranslatedText(str(check["reason"])))
            if (session["blueprint"].get("package_review") or {}).get("enabled") and selected_result.get("optional_score_status") == "not_selected":
                st.caption("此结果未抽中 JEV 额外评分；通过状态来自基础质量核对。")
            if selected_result.get("depth_limited"):
                st.warning("这条结果已达到修订上限。请检查设计要求，或重新设计问答。")
            if row.get("lineage"):
                st.caption("此结果来自人工回流；上一个版本仍保留在结果版本列表中。")
            mode_key = f"human-review-mode:{context}:{selected_round}:{target}:{selected_id}"
            if mode_key not in st.session_state:
                st.session_state[mode_key] = (session["blueprint"].get("review_repair") or {}).get("mode", "auto")
            review_mode = st.segmented_control("评分与修正方式", ("auto", "human"), key=mode_key,
                required=True, disabled=active,
                format_func=lambda value: translate_label(
                    "自动评分与修正" if value == "auto" else "人工评分与修正", language))
            if review_mode == "human":
                with st.expander("查看原候选"):
                    _render_record(row)
                _render_manual_review(application, session_id, context, begin, selected_round,
                    target, selected_id, row, projection, disabled=active)
                return
            _render_record(row)
            models = session["blueprint"].get("node_models") or {}
            target_node = "preference" if target in {"dpo", "orpo", "rlaif"} else target
            has_repair_model = bool(models.get("review", {}).get("generation") or
                models.get(target_node, {}).get("generation") or models.get("sft", {}).get("generation"))
            has_score_model = not (session["blueprint"].get("package_review") or {}).get("enabled") or bool(
                models.get("review", {}).get("jev") or models.get("jev", {}).get("jev") or
                models.get("package", {}).get("jev") or models.get(target_node, {}).get("jev") or
                models.get("sft", {}).get("jev"))
            if not has_repair_model:
                st.info("该工作尚未配置修正模型，请返回工作流的评分与修正节点配置后再启动。")
            elif not has_score_model:
                st.info("已开启 JEV，但尚未配置评分模型。请返回评分与修正节点补全模型配置。")
            with st.form(f"human-feedback:{context}:{selected_round}:{target}:{selected_id}"):
                instruction = st.text_area("修正意见", max_chars=6000, height=105,
                    value=projection.get("reviewer_feedback", "")[:6000],
                    help="说明哪里不合适、应如何调整。修正模型只修改当前候选，再交回评分模型核验。原候选不会送回 SFT 生成节点。")
                with st.expander("直接修改问题或参考答案（可选）"):
                    question = st.text_area("修订问题", max_chars=12000)
                    answer = st.text_area("修订参考答案", max_chars=12000)
                revise = st.form_submit_button("修正答案并重新评分",
                    type="primary", disabled=active or not can_revise or not has_repair_model or not has_score_model, width="stretch")
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
