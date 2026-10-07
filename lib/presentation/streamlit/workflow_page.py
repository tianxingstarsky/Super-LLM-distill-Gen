"""Workflow workbench. All durable state lives in the workspace, not the UI."""
from __future__ import annotations

from contextlib import nullcontext
import html
import json
import math
from pathlib import Path
import tempfile

import streamlit as st
from filelock import Timeout

from lib.application.workflow_service import WorkflowApplication
from lib.application.creation_draft_service import CreationDraftApplication
from lib.domain.workflow_graph import execution_graph
from lib.domain.backend_config import validate_token_prices
from lib.presentation.streamlit.i18n import UntranslatedText, translate, translate_label
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.workflow_run_styles import workflow_run_styles
from lib.presentation.streamlit.workflow_workbench_style import workbench_style
from lib.domain.workflow_targets import TARGETS
from lib.domain.web_research import MAX_QUERIES, validate_web_research
from lib.domain.workflow_scale import MAX_CANDIDATES, MAX_CONCURRENCY, MAX_BATCH_SIZE, node_roles
from lib.domain.workflow_node_models import missing_bindings
from lib.presentation.streamlit.workflow_canvas import canvas_spec, render_canvas
from lib.presentation.streamlit.workflow_node_settings import node_bindings, render_node_models, snapshot_available_bindings, render_agent_verification


LABELS = {"queued": "待启动", "pending": "等待", "running": "执行中", "completed": "完成", "failed": "失败，可重试",
          "cancelled": "已停止", "needs_attention": "已完成，部分目标需处理", "skipped": "未选择"}
TARGET_LABELS = {"cpt": "CPT 预训练语料", "sft": "SFT 指令对话", "agent": "Agent 验证轨迹",
                 "multiturn": "多轮对话 SFT",
                 "dpo": "DPO 偏好对", "rlaif": "RLAIF AI 反馈", "gsm8k": "基础算术（GSM8K 格式）",
                 "cot": "CoT 可见推理", "orpo": "ORPO 偏好对"}
PRESETS = {
    "自动推荐": ("cpt", "sft", "orpo", "dpo"),
    "预训练语料": ("cpt",),
    "多轮对话": ("sft", "multiturn"),
    "Agent 轨迹": ("sft", "agent"),
    "偏好对齐": ("sft", "orpo", "dpo", "rlaif"),
    "ORPO 数据生成": ("orpo",),
    "数学推理": ("sft", "gsm8k", "cot"),
}


def _select_setup_node(key: str, node: str) -> None:
    st.session_state[key] = node


def _save_sft_output_style(workspace: str) -> None:
    key = f"workflow-sft-output-style:{workspace}"
    value = st.session_state.get(key)
    if value in {"separated", "drop"}:
        st.session_state[f"workflow-sft-output-style-draft:{workspace}"] = value
        _save_draft_value(workspace, key)


def _workflow_error(error) -> str:
    return {
        "web_search_not_configured": "网页检索服务未配置。设置检索密钥后可从断点重试。",
        "web_search_provider_error": "网页检索服务暂不可用。可从断点重试本次任务。",
        "web_search_no_safe_results": "没有找到可用的公开检索结果。请新建任务并调整公开检索词。",
        "invalid_task_plan_duplicate_normalized_task": "规划批次包含重复任务，请重试当前批次。",
        "invalid_task_plan_task_too_long": "规划任务过长，请重试当前批次生成简洁任务。",
        "invalid_task_plan_wrong_task_count": "规划批次的任务数量不符，请重试当前批次。",
        "invalid_task_plan_empty_text": "规划批次包含空任务，请重试当前批次。",
        "invalid_task_plan_potential_secret": "规划批次可能包含密钥，已阻止继续生成。",
        "invalid_task_plan_potential_personal_data": "规划批次可能包含个人信息，已阻止继续生成。",
        "invalid_task_plan_invalid_encoding": "规划批次包含无效文本，请重试当前批次。",
        "model_context_window_exceeded": "输入超出节点设置的上下文窗口。请缩小单次输入，或新建任务调整节点限制。",
        "max_output_tokens_exceeds_context_window": "单次输出上限必须小于上下文窗口。请新建任务调整节点限制。",
        "workflow_node_service_not_configured": "节点选择的模型服务已不可用。请在该节点重新选择服务。",
        "workflow_endpoint_url_or_protocol_invalid": "模型服务地址或接口格式无效。请检查服务连接后重试。",
        "workflow_endpoint_models_invalid": "模型服务的模型列表无效。请检查服务连接后重试。",
        "workflow_endpoint_credential_ref_invalid": "模型服务的密钥引用无效。请在服务连接中填写环境变量名。",
        "model_budget_prices_required": "模型服务未配置输入和输出单价。请在节点连接中填写，或明确设为免费服务。",
        "workflow_endpoint_pin_invalid": "本次任务的模型配置快照无效。请新建任务。",
        "workflow_endpoint_changed_create_new_run": "模型服务配置已改变。为避免任务使用不同服务，请新建任务。",
    }.get(str(error), str(error))


def _missing_budget_prices(nodes, source_mode, bindings, endpoints, budget):
    if not (budget.get("max_total_usd") and budget.get("hard_stop", True)):
        return []
    missing = []
    for node in nodes:
        for role in node_roles(node, source_mode):
            binding = bindings.get(node, {}).get(role, {})
            endpoint = endpoints.get(binding.get("backend"))
            if endpoint is None:
                continue
            try:
                validate_token_prices(endpoint.get("prices"))
            except ValueError:
                missing.append((node, role))
    return missing


ICONS = {"pending": "○", "queued": "○", "running": "◉", "completed": "✓", "failed": "!", "cancelled": "■", "skipped": "—"}
STAGE_STATUS = {"pending": "待处理", "queued": "待启动", "running": "执行中", "completed": "已完成",
                "failed": "失败", "cancelled": "已停止", "skipped": "已跳过"}


def _stage_configuration(key, recipe, state):
    targets = recipe.get("targets", [])
    if key == "ingest":
        details = {"来源": [UntranslatedText(row.get("name", row.get("file", "未知来源"))) for row in recipe.get("sources", [])] or ["开放需求"],
                "最大处理单元": recipe.get("max_units"), "分块目标字符数": recipe.get("chunk_chars"),
                "隐私与结构规则": "疑似密钥、无效结构和超长单元进入隔离"}
        binding = recipe.get("node_models", {}).get(key, {}).get("generation")
        if binding:
            details["生成模型"] = UntranslatedText(binding["backend"] + " / " + binding["model"])
            if "context_window_tokens" in binding and "max_output_tokens" in binding:
                details["生成上下文窗口"] = f"{binding['context_window_tokens']:,} tokens"
                details["生成单次输出上限"] = f"{binding['max_output_tokens']:,} tokens"
        return details
    if key in {"cpt", "sft", "multiturn", "agent", "preference", "cot"}:
        details = {"启用目标": [target.upper() for target in targets]}
        mode = "文档资料" if recipe.get("sources") else "开放需求"
        for role in node_roles(key, mode):
            binding = recipe.get("node_models", {}).get(key, {}).get(role, {})
            prefix = "" if role == "generation" else "jev_"
            label = "生成模型" if role == "generation" else "独立质量评审模型"
            fallback = translate("旧版默认配置", st.session_state.get("ui_language", "zh"))
            details[label] = UntranslatedText((binding.get("backend") or recipe.get(prefix + "backend") or fallback)
                              + " / " + (binding.get("model") or recipe.get(prefix + "model") or fallback))
            if "context_window_tokens" in binding and "max_output_tokens" in binding:
                limit_label = "生成" if role == "generation" else "评审"
                details[limit_label + "上下文窗口"] = f"{binding['context_window_tokens']:,} tokens"
                details[limit_label + "单次输出上限"] = f"{binding['max_output_tokens']:,} tokens"
        if node_roles(key, mode):
            details.update({"并发请求上限": recipe.get("concurrency", 1), "每批候选数": recipe.get("batch_size", 100)})
        if key == "cpt":
            details.update({"分块目标字符数": recipe.get("chunk_chars"), "去重": "精确去重与保守近重复检查"})
        elif key == "multiturn":
            details.update({"每段对话轮数": recipe.get("conversation_turns", 3),
                            "质检方式": "逐轮评审 + 全段一致性评审"})
        elif key == "agent":
            details["验证方式"] = "隔离验证" if recipe.get("agent_sandbox_image") else "本地验证"
            details["轨迹剪枝"] = "已核对的相邻重复调用；原始记录保留"
            details["失败样本"] = "单独保存原因和执行证据，不混入训练样本"
        elif key == "preference":
            details["比较目标"] = [target.upper() for target in targets if target in {"dpo", "orpo", "rlaif"}]
        return details
    if key == "gsm8k":
        return {"任务数": recipe.get("tasks"), "生成方式": "本地整数算术模板",
                "验证方式": "受限 AST 逐步计算，不调用模型"}
    if key == "package":
        return {"输出目标": [target.upper() for target in targets],
                "质检证据": "逐条记录、失败原因、来源指纹与 SHA-256 清单",
                "发布状态": "自动检查候选，尚未完成人工审核"}
    return {"启用目标": [target.upper() for target in targets]}


STAGE_GLYPHS = {"ingest": "▤", "cpt": "▥", "sft": "✎", "multiturn": "☷", "agent": "◇",
                "preference": "⚖", "gsm8k": "∑", "cot": "◈", "package": "▣"}
EVENT_LABELS = {"stage_started": "节点开始运行", "stage_completed": "节点处理完成",
                "model_started": "模型请求开始", "model_finished": "模型请求完成",
                "web_search_started": "开始查找公开资料", "web_search_completed": "公开资料检索完成",
                "run_finished": "工作流运行结束", "run_failed": "工作流运行失败",
                "run_cancelled": "工作流已停止"}


def _stage_numbers(stage):
    status = stage.get("status", "pending")
    status = status if status in ICONS else "pending"
    done = max(0, int(stage.get("done", 0) or 0))
    total = max(0, int(stage.get("total", 0) or 0))
    percent = min(100, round(done * 100 / total)) if total else (100 if status == "completed" else 0)
    return status, done, total, percent


def _config_html(configuration):
    rows = []
    english = st.session_state.get("ui_language", "zh") == "en"
    for label, value in configuration.items():
        if isinstance(value, list):
            rendered = (", " if english else "、").join(str(item) for item in value[:5]) or "—"
            if len(value) > 5:
                rendered += f", {len(value) - 5} more" if english else f"，另有 {len(value) - 5} 项"
        elif isinstance(value, dict):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = "—" if value is None else str(value)
        marker = (' data-user-content' if (isinstance(value, UntranslatedText)
                   or isinstance(value, list) and any(isinstance(item, UntranslatedText) for item in value)) else '')
        rows.append(f"<div><span>{html.escape(str(label))}</span><strong{marker}>{html.escape(rendered)}</strong></div>")
    return '<div class="df-run-config">' + "".join(rows) + "</div>"


def _events_html(events, *, limit=12):
    if not events:
        return '<div class="df-run-empty">这个节点还没有运行事件。开始执行后会在这里持续更新。</div>'
    rows = []
    for event in reversed(events[-limit:]):
        kind = str(event.get("kind", "event"))
        label = EVENT_LABELS.get(kind, kind.replace("_", " "))
        raw_time = str(event.get("at", ""))
        moment = raw_time.replace("T", " ")[:19]
        if raw_time.endswith(("Z", "+00:00")):
            moment += " UTC"
        stage_key = str(event.get("stage") or "")
        stage_label = GRAPH_LABELS.get(stage_key, "工作流" if not stage_key else stage_key)
        details = " · ".join(f"{key}: {value}" for key, value in event.items()
                             if key not in {"at", "stage", "kind"} and value is not None)
        if len(details) > 180:
            details = details[:177] + "..."
        rows.append(f'<div class="df-run-event" data-kind="{html.escape(kind, quote=True)}">'
                    '<i aria-hidden="true"></i><div class="df-run-event-body">'
                    f'<div class="df-run-event-head"><strong>{html.escape(label)}</strong>'
                    f'<time>{html.escape(moment)}</time></div>'
                    f'<div class="df-run-event-sub"><b>{html.escape(stage_label)}</b>'
                    + (f'<span>{html.escape(details)}</span>' if details else '') + '</div></div></div>')
    return '<div class="df-run-log">' + "".join(rows) + "</div>"


def _quality_html(targets):
    cards = []
    for target, info in targets.items():
        reasons = info.get("reasons", {})
        issue_count = sum(int(count) for count in reasons.values())
        cards.append('<div class="df-run-quality-card">'
                     f'<small>{html.escape(TARGET_LABELS.get(target, target.upper()))}</small>'
                     f'<b>{max(0, int(info.get("eligible", 0)))}</b>'
                     f'<span>通过 / {max(0, int(info.get("total", 0)))} 候选 · {issue_count} 条需处理</span>'
                     '</div>')
    return '<div class="df-run-quality-grid">' + "".join(cards) + "</div>"


GRAPH_LABELS = {"ingest": "输入解析", "cpt": "CPT 语料", "sft": "SFT 生成",
                "multiturn": "多轮对话", "agent": "Agent 轨迹", "gsm8k": "算术核验",
                "preference": "偏好评审", "cot": "CoT 核对", "package": "质检打包"}


def _missing_model_role_summary(issues, language):
    pending_roles = {}
    for node, role in issues:
        pending_roles.setdefault(node, []).append(role)
    role_labels = {"generation": "生成模型", "jev": "独立质量评审模型"}
    descriptions = []
    for node, roles in pending_roles.items():
        node_label = translate_label(GRAPH_LABELS[node], language)
        missing = [translate_label(role_labels[role], language) for role in roles]
        if language == "en":
            descriptions.append(f"{node_label}: missing {', '.join(missing)}")
        else:
            descriptions.append(f"{node_label}：缺少{'、'.join(missing)}")
    return " · ".join(descriptions)


def _planned_flow_html(targets, nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]) -> str:
    """Keep the summary compact; the canvas and dependency list show the nodes."""
    if not targets:
        return ('<div class="df-wb-plan df-wb-plan-empty">'
                '<span>请从上方列表至少选择一类训练目标。</span></div>')
    intermediate = ('<span class="df-wb-plan-intermediate">SFT 中间候选</span>'
                    if "sft" in nodes and "sft" not in targets else '')
    return ('<div class="df-wb-plan">'
            '<strong>运行前流程预览</strong>'
            f'<small>{len(nodes)} 个阶段 · {len(edges)} 条数据依赖</small>'
            + intermediate + '</div>')


def _planned_dependencies_html(edges: tuple[tuple[str, str], ...]) -> str:
    """Render only the data-dependency edges supplied by execution_graph."""
    rows = []
    for origin, destination in edges:
        rows.append(
            f'<div class="df-wb-plan-edge" data-from="{origin}" data-to="{destination}">'
            f'<span>{html.escape(GRAPH_LABELS[origin])}</span>'
            '<i aria-hidden="true">→</i>'
            f'<span>{html.escape(GRAPH_LABELS[destination])}</span></div>')
    return ('<div class="df-wb-plan-detail">'
            '<p>箭头表示本次目标的真实数据依赖；各阶段仍按顺序执行。</p>'
            '<div class="df-wb-plan-edges">' + ''.join(rows) + '</div></div>')


def _open_sample_browser(run_id, target):
    st.session_state["workflow-open-preview"] = {
        "workspace": st.session_state["ws"], "run_id": run_id, "target": target}
    st.rerun()


def _open_package(run_id):
    st.session_state[f"package-run:{st.session_state['ws']}"] = run_id
    st.session_state["nav"] = "输出打包"


def _render_research_receipt(application, run_id, recipe, state):
    """Keep the public query and its planning leads visible on the run page."""
    config = recipe.get("web_research")
    if not isinstance(config, dict):
        return
    with st.container(border=True, key=f"web-research-receipt:{run_id}"):
        section_heading("联网资料", "仅用于开放需求的任务规划", "⌕")
        st.caption("公开检索主题")
        st.code(UntranslatedText("\n".join([str(config.get("query", "")),
                                           *[str(query) for query in config.get("more_queries", [])]])),
                language=None)
        try:
            document = application.web_research_results(run_id)
        except (OSError, ValueError, KeyError, TypeError):
            st.error("检索线索读取失败，请检查本次任务文件。")
            return
        if document is not None:
            results = document["results"]
            st.metric("公开线索", len(results))
            st.caption("网页摘要只用于规划任务，不等于事实核验；系统未抓取来源网页正文。")
            topic_times = document.get("topic_retrieved_at")
            if isinstance(topic_times, list) and topic_times:
                st.caption("各主题检索时间")
                st.html('<div class="df-research-topic-times">' + "".join(
                    '<div><strong data-user-content>' + html.escape(str(item["query"]))
                    + '</strong><span>' + html.escape(str(item["retrieved_at"]))
                    + ' UTC</span></div>'
                    for item in topic_times if isinstance(item, dict)
                    and "query" in item and "retrieved_at" in item
                ) + '</div>')
            with st.expander("查看检索线索"):
                for row in results:
                    if row.get("query"):
                        st.caption(UntranslatedText(row["query"]))
                    st.link_button(UntranslatedText(row["title"]), row["url"],
                                   help="在浏览器中打开公开来源")
                    st.caption(UntranslatedText(row["snippet"]))
            return
        research_state = state.get("web_research")
        if isinstance(research_state, dict) and research_state.get("status") == "completed":
            st.error("检索线索读取失败，请检查本次任务文件。")
            return
        if state.get("status") in {"failed", "cancelled"}:
            return
        if any(event.get("kind") == "web_search_started" for event in state.get("events", [])):
            st.info("正在检索公开资料。结果会在这里显示。")
        else:
            st.caption("运行开始后会先检索公开资料，并在此展示规划线索。")


@st.fragment(run_every=2)
def render_run(application, run_id, begin, *, embedded=False):
    st.html(workflow_run_styles())
    try:
        # Inventory is only a snapshot. Read again inside the fragment so a
        # removed or damaged task file cannot turn a refresh into a traceback.
        state = application.state(run_id)
        recipe = application.recipe(run_id)
        active = application.is_active(run_id)
        if (not isinstance(state, dict) or not isinstance(recipe, dict) or
                not isinstance(state.get("name"), str) or not isinstance(state.get("status"), str) or
                not isinstance(state.get("stages"), dict) or not state["stages"] or
                any(not isinstance(stage, dict) for stage in state["stages"].values()) or
                not isinstance(recipe.get("targets"), list)):
            raise ValueError("unreadable_task_record")
        attempt = int(state.get("attempt", 0))
        graph_nodes, _ = execution_graph(recipe["targets"])
    except (OSError, ValueError, TypeError, KeyError):
        st.warning("历史任务仍已保留，暂时无法读取完整运行记录。请检查任务文件。")
        return
    st.subheader(UntranslatedText(state["name"]))
    updated = str(state.get("updated_at") or "")[:19].replace("T", " ") or "—"
    st.html('<div class="df-run-meta">'
            f'<span>任务编号 <b>{html.escape(run_id[:8])}</b></span><i>·</i>'
            f'<span>第 {attempt} 次运行</span><i>·</i>'
            f'<span>更新于 {html.escape(updated)} UTC</span></div>')
    status = state["status"]
    stage_keys = list(state["stages"])
    active_stages = set(graph_nodes)
    selection_key = f"workflow-stage:{run_id}"
    selected_stage = st.session_state.get(selection_key)
    if selected_stage not in stage_keys:
        selected_stage = (next((key for key in stage_keys if state["stages"][key].get("status") == "running"), None)
                          or next((key for key in stage_keys if state["stages"][key].get("status") == "failed"), None)
                          or ("package" if status in {"completed", "needs_attention"} and "package" in stage_keys else stage_keys[0]))
        st.session_state[selection_key] = selected_stage
    selected_stages = [state["stages"][key] for key in stage_keys if key in active_stages]
    completed_stages = sum(stage.get("status") == "completed" for stage in selected_stages)
    st.html('<div class="df-run-overview">'
            f'<span class="df-run-pill" data-status="{html.escape(status, quote=True)}">运行状态 <strong>{html.escape(LABELS.get(status, status))}</strong></span>'
            f'<span class="df-run-pill">已完成节点 <strong>{completed_stages} / {len(selected_stages)}</strong></span>'
            f'<span class="df-run-pill">本次目标 <strong>{len(state.get("targets", []))}</strong></span>'
            f'<span class="df-run-pill">运行尝试 <strong>{attempt}</strong></span>'
            '</div>')
    if status == "running" and not active:
        st.warning("执行进程已中断。可从已完成的逐条断点继续。")
    elif status == "failed":
        st.error(f"运行失败：{_workflow_error(state.get('error', 'unknown'))}。已完成的步骤与模型响应已保存。")
    elif status == "completed":
        st.success("所选目标已完成，训练文件与质量报告已生成。")
    elif status == "needs_attention":
        st.warning("运行已结束；有目标没有合格样本，或输入超过本次处理上限。查看下方质量报告。")
    else:
        st.info(LABELS.get(status, status))
    console_job = st.session_state.get(f"job:{st.session_state.get('ws', '')}:{run_id}")
    if console_job is not None and status == "queued":
        exit_code, _ = console_job.snapshot()
        if exit_code not in (None, 0):
            st.error("任务进程未能启动。请检查本机运行环境后从断点重试。")
    _render_research_receipt(application, run_id, recipe, state)
    if active:
        if st.button("停止后续步骤", key=f"stop:{run_id}"):
            application.cancel(run_id)
            st.info("已请求停止；当前模型请求返回后，在下一断点停止。")
    elif status in {"queued", "running", "failed", "cancelled"}:
        st.caption("继续执行沿用本次来源快照与节点配方。已保存的逐条断点会校验后复用；修改模型或生成参数，请创建新任务。")
        if st.button("继续执行 / 从断点重试", type="primary", key=f"resume:{run_id}"):
            begin(["workflow", "--action", "resume", "--run-id", run_id])
    flow, inspector = st.columns([2.25, 1], gap="medium")
    with flow:
        with st.container(border=True):
            st.html('<div class="df-run-section"><div><strong>工作流运行图</strong>'
                    '<small>点击节点卡，检查该步骤的状态、配置与日志。</small></div>'
                    '<span class="df-run-section-tag">实时进度</span></div>')
            follow_key = f"workflow-follow:{run_id}"
            if st.session_state.pop(f"canvas-pause:{follow_key}", False):
                st.session_state[follow_key] = False
            follow = st.toggle("跟随执行节点", value=None if follow_key in st.session_state else True, key=follow_key,
                               help="自动定位正在执行、失败或完成后的打包节点；点击节点会暂停跟随。")
            if follow:
                focus_node = (next((key for key in graph_nodes if state["stages"][key].get("status") == "running"), None)
                              or next((key for key in graph_nodes if state["stages"][key].get("status") == "failed"), None)
                              or ("package" if status in {"completed", "needs_attention"} else None))
                if focus_node:
                    selected_stage = focus_node
                    st.session_state[selection_key] = focus_node
            else:
                st.caption("已暂停跟随，可检查所选节点；重新开启后定位当前执行阶段。")
            render_canvas(canvas_spec(recipe["targets"], state["stages"], selected_stage,
                                      GRAPH_LABELS, STAGE_GLYPHS, recipe.get("node_models"),
                                      language=st.session_state.get("ui_language", "zh"), live=True),
                          selection_key, key=f"live-canvas:{run_id}", follow_key=follow_key)
    selected_metrics = state["stages"][selected_stage]
    selected_status, done, total, percent = _stage_numbers(selected_metrics)
    if selected_stage not in active_stages:
        selected_status, done, total, percent = "skipped", 0, 0, 0
    selected_label = str(selected_metrics.get("label", selected_stage))
    if selected_stage == 'ingest' and selected_metrics.get('phase') == 'planning':
        passed, quarantined = done, max(0, total - done)
        passed_label, quarantined_label = '已规划任务', '待规划任务'
    elif selected_stage == "ingest":
        passed = int(state.get("input_summary", {}).get("ready", selected_metrics.get('eligible', 0)) or 0)
        quarantined = int(state.get("input_summary", {}).get("quarantined", selected_metrics.get('quarantined', 0)) or 0)
        passed_label, quarantined_label = "可处理输入", "隔离输入"
    elif selected_stage == "package":
        target_quality = state.get("quality", {}).get("targets", {})
        passed = sum(int(row.get("eligible", 0) or 0) for row in target_quality.values())
        quarantined = sum(sum(int(count) for count in row.get("reasons", {}).values())
                          for row in target_quality.values())
        passed_label, quarantined_label = "导出样本", "需关注记录"
    else:
        passed = int(selected_metrics.get("eligible", 0) or 0)
        quarantined = int(selected_metrics.get("quarantined", 0) or 0)
        passed_label, quarantined_label = "通过质检", "隔离记录"
    with inspector:
        with st.container(border=True):
            st.html('<div class="df-run-section"><div><strong>节点配置</strong>'
                    '<small>所选节点的运行状态与实际配方</small></div></div>')
            st.html('<div class="df-run-inspector-head">'
                    f'<b>{html.escape(STAGE_GLYPHS.get(selected_stage, "◈"))}</b><div>'
                    f'<strong>{html.escape(selected_label)}</strong>'
                    f'<small><span>{html.escape(STAGE_STATUS.get(selected_status, selected_status))}</span> · {percent}%</small>'
                    '</div></div>')
            st.progress(percent / 100, text=f"{done} / {total} 单元")
            if selected_stage == 'ingest' and selected_metrics.get('phase') == 'planning':
                st.caption("开放需求任务规划：按批完成后再进入生成节点。")
            if "cached" in selected_metrics:
                cached = min(done, max(0, int(selected_metrics.get("cached", 0) or 0)))
                reused, processed = st.columns(2)
                reused.metric("已复用断点", f"{cached:,}")
                processed.metric("本次新处理", f"{max(0, done - cached):,}")
                st.caption("处理进度包含已校验并复用的断点；新处理单元也可能复用此前保存的模型响应。")
            if selected_metrics.get("batches_total"):
                st.caption(f"批次 {selected_metrics.get('batches_done', 0)} / {selected_metrics['batches_total']}")
                rate = selected_metrics.get("rate_per_minute")
                st.metric("候选 / 分钟", f"{rate:,.0f}" if rate is not None else "—")
                eta = selected_metrics.get("eta_seconds")
                if selected_status == "running" and eta is not None:
                    st.caption(f"预计剩余 {math.ceil(eta / 60):,} 分钟")
                    st.caption("按本次处理速度估算，不计已复用的单元断点；剩余单元仍可能复用模型响应。")
            st.html('<div class="df-run-stat-grid">'
                    f'<div class="df-run-stat"><b>{max(0, passed)}</b><span>{passed_label}</span></div>'
                    f'<div class="df-run-stat"><b>{max(0, quarantined)}</b><span>{quarantined_label}</span></div>'
                    '</div>')
            st.html(_config_html(_stage_configuration(selected_stage, recipe, state)))
            if selected_metrics.get("error"):
                st.error(f"节点错误：{_workflow_error(selected_metrics['error'])}")
    selected_events = [event for event in state.get("events", []) if event.get("stage") == selected_stage]
    with flow, st.container(border=True):
        st.html('<div class="df-run-section"><div><strong>运行日志</strong>'
                f'<small>当前筛选：{html.escape(selected_label)} · 最近 {min(len(selected_events), 12)} 条事件</small>'
                '</div><span class="df-run-section-tag">所选节点</span></div>')
        st.html(_events_html(selected_events))
    summary = state.get("input_summary", {})
    if summary:
        cols = st.columns(4)
        for col, (label, key) in zip(cols, [("解析单元", "units"), ("可处理", "ready"), ("输入隔离", "quarantined"), ("超出上限", "deferred")]):
            col.metric(label, summary.get(key, 0))
    tabs = st.tabs(["产物与质量", "全部事件", "来源与配方"])
    with tabs[0]:
        quality = state.get("quality")
        if quality:
            st.html('<div class="df-run-section"><div><strong>训练产物与质量</strong>'
                    '<small>每个目标只会导出通过对应检查的样本。</small></div></div>')
            st.html(_quality_html(quality["targets"]))
            st.caption("自动质检候选版本 · 未进行人工审核。开放需求生成的数据依赖模型评审，不能视为已核实的事实。")
            with st.expander("查看未通过原因明细"):
                st.dataframe([{"目标": target.upper(), "通过": info["eligible"], "候选总数": info["total"],
                               "未通过原因": json.dumps(info["reasons"], ensure_ascii=False)}
                              for target, info in quality["targets"].items()], hide_index=True, width="stretch")
            if status in {"completed", "needs_attention"}:
                st.button("查看并打包本次训练数据", on_click=_open_package, args=(run_id,),
                          type="primary", key=f"zip:{run_id}")
                st.caption("按目标进入完整样本浏览，支持序号定位与连续翻阅。")
                for target in state["targets"]:
                    eligible = int(quality["targets"].get(target, {}).get("eligible", 0))
                    st.button(TARGET_LABELS.get(target, target.upper()),
                              disabled=eligible <= 0, on_click=_open_sample_browser,
                              args=(run_id, target), key=f"browse-preview:{run_id}:{target}")
                negative_count = int(quality.get("targets", {}).get("agent", {}).get("negative", 0))
                if "agent" in state["targets"] and negative_count:
                    st.caption("失败轨迹单独保存原因和执行证据，不混入通过验证的训练样本。")
                    st.button("浏览 Agent 失败轨迹", on_click=_open_sample_browser,
                              args=(run_id, "agent_negative"), key=f"browse-negative:{run_id}")
            with st.expander("产物保存位置"):
                st.code(application.artifact_location(run_id))
        else:
            st.caption("质量汇总将在生成与验证步骤结束后出现。进度会自动刷新。")
        if summary.get("quarantined"):
            with st.expander(f"输入隔离记录 · {summary['quarantined']}"):
                if st.toggle("加载隔离记录", key=f"load-inputs:{run_id}"):
                    try:
                        rejected = [{"ID": u["id"], "定位": u.get("location", ""), "原因": u.get("reason")}
                                    for u in application.quarantined_inputs(run_id, limit=100)]
                    except (OSError, ValueError, KeyError):
                        st.error("无法读取输入隔离记录，请检查本次任务文件。")
                    else:
                        st.caption("最多显示前 100 条；完整记录保留在本次产物中。")
                        st.dataframe(rejected, hide_index=True)
    with tabs[1]:
        st.html('<div class="df-run-section"><div><strong>全部运行事件</strong>'
                '<small>按时间倒序展示最近的处理与模型调用事件。</small></div></div>')
        st.html(_events_html(state.get("events", []), limit=60))
        with st.expander("技术详情：原始事件与模型用量"):
            st.dataframe(list(reversed(state.get("events", [])[-100:])), hide_index=True, width="stretch")
            st.json({"模型": state.get("models", {}), "调用与 token": state.get("usage", {})})
    with tabs[2]:
        st.html('<div class="df-run-section"><div><strong>来源与运行配方</strong>'
                '<small>配方和来源快照已固定，可用于核对与重新运行。</small></div></div>')
        st.html(_config_html({"来源文件": [UntranslatedText(source.get("name", source.get("file", "")))
                                                for source in recipe.get("sources", [])] or ["开放需求"],
                              "开放需求": (UntranslatedText(recipe["brief"][:180] + "…" if len(recipe["brief"]) > 180
                                                       else recipe["brief"]) if recipe.get("brief") else "未填写"),
                              "训练目标": [TARGET_LABELS.get(target, target.upper()) for target in recipe.get("targets", [])],
                              "任务数": recipe.get("tasks"), "处理上限": recipe.get("max_units")}))
        with st.expander("技术详情：完整配方 JSON"):
            st.json(recipe)


def _save_draft_value(workspace, key):
    draft_key = f"workflow-form-draft:{workspace}"
    previous = st.session_state.get(draft_key, {})
    if st.session_state[key] is None:
        st.session_state[key] = previous[key]
        return
    st.session_state[draft_key] = {
        **previous, key: st.session_state[key]}
    application = st.session_state.get(f"workflow-draft-application:{workspace}")
    if application is not None:
        try:
            # A previous autosave may have failed. Flush the whole validated
            # session form before clearing its error, rather than silently
            # dropping that earlier edit when an unrelated field saves.
            application.replace(st.session_state[draft_key])
            st.session_state.pop(f"workflow-draft-error:{workspace}", None)
        except (OSError, ValueError, Timeout):
            st.session_state[f"workflow-draft-error:{workspace}"] = True


def _select_candidate_count(workspace, count):
    key = f"workflow-count:{workspace}"
    st.session_state[key] = count
    _save_draft_value(workspace, key)


def _draft_number(label, minimum, maximum, default, *, key, **options):
    """Keep inputs when Streamlit removes a conditional or off-page widget."""
    workspace = st.session_state["ws"]
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    if key not in st.session_state:
        st.session_state[key] = draft.get(key, default)
    return st.number_input(label, minimum, maximum, value=None, key=key,
                           on_change=_save_draft_value, args=(workspace, key), **options)


def _draft_name(default, workspace):
    key = f"workflow-name:{workspace}"
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    if key not in st.session_state:
        st.session_state[key] = draft.get(key, default)
    if key not in draft:
        _save_draft_value(workspace, key)
    return st.text_input("运行名称", value=None, key=key, max_chars=100,
                         on_change=_save_draft_value, args=(workspace, key))


def _draft_brief(label, *, key, **options):
    workspace = st.session_state["ws"]
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    if key not in st.session_state:
        st.session_state[key] = draft.get(key, "")
    return st.text_area(label, value=None, key=key, max_chars=20000,
                        on_change=_save_draft_value, args=(workspace, key), **options)


def _draft_web_control(workspace, application: WorkflowApplication, *, configured: bool):
    """Explicit public topics, never an implicit copy of the private brief."""
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    enabled_key = f"workflow-web-research-enabled:{workspace}"
    consent_key = f"workflow-web-research-session-consent:{workspace}"
    if enabled_key not in st.session_state:
        st.session_state[enabled_key] = st.session_state.get(consent_key, False)
    section_heading("联网资料", "输入解析节点的可选规划线索", "⌕")
    status_column, check_column = st.columns([2, 1], vertical_alignment="center", gap="small")
    with status_column:
        if configured:
            st.caption("Brave Search · 已检测到运行环境中的检索密钥")
        else:
            st.caption("Brave Search · 未配置。设置 DATAFORGE_BRAVE_SEARCH_API_KEY 并重启控制台。")
    with check_column:
        check_clicked = st.button("检查 Brave 连接", key=f"workflow-web-check:{workspace}",
                                  use_container_width=True,
                                  help="仅在点击时向 Brave 发送固定公开词 Brave Search；不会发送需求、上传资料或下方检索词。")
    result_key = f"workflow-web-check-result:{workspace}"
    if check_clicked:
        with st.spinner("正在检查 Brave 连接…"):
            st.session_state[result_key] = (configured, application.check_web_research_connection())
    prior = st.session_state.get(result_key)
    if isinstance(prior, tuple) and len(prior) == 2 and prior[0] == configured:
        if prior[1] == "ready":
            st.success("上次检查：Brave Search 连接可用。")
        elif prior[1] == "not_configured":
            st.warning("上次检查：未检测到检索密钥。")
        elif prior[1] == "unavailable":
            st.error("上次检查未通过；请检查密钥、网络或 Brave 服务状态。")
    enabled = st.checkbox("联网查找公开资料", key=enabled_key,
                          on_change=_save_web_consent, args=(workspace, enabled_key),
                          help="仅将你填写的公开检索主题发送给网页检索服务；需求全文和上传资料不会作为检索词发送。检索结果只作规划线索，不代表事实已核验。")
    if not enabled:
        return None, False
    st.caption("仅将你填写的公开检索主题发送给网页检索服务；需求全文和上传资料不会作为检索词发送。检索结果只作规划线索，不代表事实已核验。")
    query_key = f"workflow-web-research-query:{workspace}"
    if query_key not in st.session_state:
        st.session_state[query_key] = draft.get(query_key, "")
    query = st.text_input("公开检索词", value=None, key=query_key, max_chars=160,
                          placeholder="例如：设备维护安全规范",
                          on_change=_save_draft_value, args=(workspace, query_key))
    count_key = f"workflow-web-research-count:{workspace}"
    with st.expander("更多检索设置（可选）"):
        count = _draft_number("检索结果上限", 1, 5, 3, key=count_key)
        more_key = f"workflow-web-research-more:{workspace}"
        if more_key not in st.session_state:
            st.session_state[more_key] = draft.get(more_key, "")
        more_text = st.text_area("补充公开检索主题（每行一个，最多 4 个）", value=None,
                                 key=more_key, max_chars=700,
                                 placeholder="例如：设备检修风险\n维护记录质量规范",
                                 on_change=_save_draft_value, args=(workspace, more_key))
        st.caption("每个主题最多保留所设数量的结果；大任务会轮换线索，不会把需求全文自动发送出去。")
    more_queries = [line.strip() for line in more_text.splitlines() if line.strip()]
    too_many = len(more_queries) >= MAX_QUERIES
    if too_many:
        st.warning("补充公开主题最多 4 个。请合并或删减后再开始。")
    unavailable = not query.strip() or not configured or too_many
    if not configured:
        st.warning("网页检索服务尚未配置，请先在运行环境设置检索密钥。")
    elif not query.strip():
        st.warning("请填写可公开的检索词，不要粘贴需求全文或私有资料。")
    config = {"provider": "brave", "query": query.strip(), "count": int(count)}
    if more_queries:
        config["more_queries"] = more_queries
    return config, unavailable


def _save_web_consent(workspace, key):
    # Consent is deliberately session-only; a new browser session starts offline.
    st.session_state[f"workflow-web-research-session-consent:{workspace}"] = bool(st.session_state[key])


def _restore_selection(workspace, key, default, choices):
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    value = st.session_state.get(key, draft.get(key, default))
    if isinstance(default, list):
        value = [item for item in value if item in choices] if isinstance(value, list) else list(default)
    elif value not in choices:
        value = default
    st.session_state[key] = value
    _save_draft_value(workspace, key)


def _cache_source_uploads(cache, workspace, source_mode):
    """Save dropped files once, then select their durable paths for this task."""
    key = f"workflow-upload:{workspace}:{source_mode}"
    error_key = f"workflow-upload-error:{workspace}:{source_mode}"
    uploads = st.session_state.get(key) or []
    st.session_state.pop(error_key, None)
    if not uploads:
        return
    try:
        rows = cache.store((upload.name, upload.getvalue()) for upload in uploads)
    except (ValueError, OSError, Timeout) as error:
        st.session_state[error_key] = str(error)
        return
    sources_key = f"workflow-sources:{workspace}:{source_mode}"
    selected = st.session_state.get(sources_key, [])
    st.session_state[sources_key] = list(dict.fromkeys([*selected, *(row["path"] for row in rows)]))
    _save_draft_value(workspace, sources_key)


def _upload_cache_error(error):
    if error in {"upload_too_large", "upload_batch_too_large"}:
        return "资料未保存：单文件最多 50 MiB，一次上传合计最多 200 MiB。"
    if error in {"empty_upload", "empty_upload_batch"}:
        return "资料未保存：文件内容为空，请选择有内容的文件。"
    if error in {"invalid_upload_name", "unsupported_upload_type", "invalid_upload_content"}:
        return "资料未保存：请检查文件名和格式，支持 PDF、DOCX、TXT、Markdown、JSON 和 JSONL。"
    return "资料未保存：无法写入本机缓存，请检查存储位置后重新上传。"


def render_workbench(application: WorkflowApplication, begin, model_application, *,
                     draft_application: CreationDraftApplication | None = None,
                     backend_application=None, input_cache=None):
    page_header("数据生成工作台", "上传文档、导入 Agent 上下文，或描述开放需求；系统会自动生成、质检并进入审核。", "DOCS　·　AGENT　·　OPEN BRIEF")
    st.html(workbench_style(st.session_state.get("ui_language", "zh")))
    st.html(
        '<div class="df-wizard-steps">'
        '<div class="df-wizard-step active"><b>1</b><span><strong>配置本次任务</strong><small>来源、目标与节点模型</small></span></div>'
        '<i></i><div class="df-wizard-step"><b>2</b><span><strong>自动生成与质检</strong><small>实时查看阶段与结果</small></span></div>'
        '<i></i><div class="df-wizard-step"><b>3</b><span><strong>审核与导出</strong><small>核对后生成训练包</small></span></div>'
        '</div>'
    )
    ws = st.session_state["ws"]
    if draft_application is not None:
        st.session_state[f"workflow-draft-application:{ws}"] = draft_application
        draft_key = f"workflow-form-draft:{ws}"
        if not st.session_state.get(f"workflow-draft-loaded:{ws}"):
            try:
                saved = draft_application.load()
                st.session_state[draft_key] = {**saved, **st.session_state.get(draft_key, {})}
                st.session_state[f"workflow-draft-loaded:{ws}"] = True
            except (OSError, ValueError):
                st.session_state[f"workflow-draft-error:{ws}"] = True
        if st.session_state.get(f"workflow-draft-error:{ws}"):
            st.warning("配置草稿未能保存或恢复。当前修改仍保留在会话中。")
        else:
            st.caption("参数、目标、需求和已选资料保存在本机缓存；节点模型和联网检索需重新确认。")
    preset_key = f"workflow-preset:{ws}"
    _restore_selection(ws, preset_key, "自动推荐", PRESETS)
    preset = st.segmented_control(
        "快捷方案", tuple(PRESETS), default=None, key=preset_key,
        on_change=_save_draft_value, args=(ws, preset_key),
        help="选择常用目标组合。下面仍可逐项增删训练目标。",
    ) or "自动推荐"
    source_key = f"workflow-source-mode:{ws}"
    _restore_selection(ws, source_key, "文档资料", ("文档资料", "Agent 上下文", "开放需求"))
    source_mode = st.segmented_control(
        "选择来源类型", ("文档资料", "Agent 上下文", "开放需求"),
        default=None, key=source_key, on_change=_save_draft_value, args=(ws, source_key),
        help="按来源选择合适的输入；文档或 Agent 记录还可以附加生成要求。",
    ) or "文档资料"
    source_extensions = ({".pdf", ".docx", ".txt", ".md"} if source_mode == "文档资料"
                         else {".json", ".jsonl"} if source_mode == "Agent 上下文" else set())
    files = application.source_files(ws, suffixes=frozenset(source_extensions), limit=501) if source_extensions else []
    file_labels = {row["path"]: row["label"] for row in files}
    with st.container(border=True, key="workbench-targets"):
        section_heading("选择训练目标", icon="◈")
        target_key = f"workflow-targets:{ws}:{preset}"
        target_defaults = [target for target in PRESETS[preset] if target in TARGETS]
        _restore_selection(ws, target_key, target_defaults, TARGETS)
        targets = st.multiselect(
            "训练目标", list(TARGETS), default=None,
            format_func=lambda target: TARGET_LABELS.get(target, target.upper()),
            key=target_key,
            on_change=_save_draft_value, args=(ws, target_key),
            help="可以同时选择多类目标；系统只会导出通过对应质量检查的样本。",
        )
        graph_nodes, graph_edges = execution_graph(targets)
        st.html(_planned_flow_html(targets, graph_nodes, graph_edges))
        if targets and graph_edges:
            with st.expander(f"查看完整数据依赖 · {len(graph_edges)} 条"):
                st.html(_planned_dependencies_html(graph_edges))
                if "sft" in graph_nodes and "sft" not in targets:
                    st.caption("仅用于下游目标的 SFT 中间候选不会单独导出。")
        if "agent" in targets:
            st.caption("Agent 正例需要完整的已记录工具轨迹；请在 Agent 节点选择验证方式并查看支持范围。")
        if "multiturn" in targets:
            st.caption("多轮目标逐轮及整段评审；合成内容会标记证据等级。")
    model_issues = []
    pricing_issues = []
    sft_output_style = None
    if "sft" in targets:
        style_key = f"workflow-sft-output-style:{ws}"
        draft_key = f"workflow-sft-output-style-draft:{ws}"
        if draft_key not in st.session_state:
            saved = st.session_state.get(f"workflow-form-draft:{ws}", {})
            st.session_state[draft_key] = saved.get(style_key, application.default_sft_output_style())
        if style_key not in st.session_state:
            st.session_state[style_key] = st.session_state[draft_key]
        sft_output_style = st.session_state[style_key]
    agent_capabilities = application.agent_replay_capabilities() if "agent" in targets else {}
    if targets:
        selection_key = f"workflow-setup-node:{ws}"
        selected_node = st.session_state.get(selection_key, "sft" if "sft" in graph_nodes else "ingest")
        if selected_node not in graph_nodes:
            selected_node = graph_nodes[0]
        st.session_state[selection_key] = selected_node
        bindings, endpoints = node_bindings(model_application, graph_nodes, source_mode, ws)
        model_issues = missing_bindings(graph_nodes, source_mode, bindings, endpoints)
        if backend_application is not None:
            pricing_issues = _missing_budget_prices(
                graph_nodes, source_mode, bindings, endpoints,
                backend_application.list_backends().get("budget") or {},
            )
        if model_issues:
            st.warning("部分节点尚未选择可用模型，请点击这些节点完成配置。")
            pending_nodes = list(dict.fromkeys(node for node, _ in model_issues))
            next_node = next((node for node in graph_nodes[graph_nodes.index(selected_node) + 1:]
                              if node in pending_nodes), pending_nodes[0])
            st.button("配置下一个待完善节点", on_click=_select_setup_node,
                      args=(selection_key, next_node), key=f"workflow-next-config:{ws}")
            st.caption(_missing_model_role_summary(model_issues, st.session_state.get("ui_language", "zh")))
        if pricing_issues:
            st.warning("部分节点的模型服务缺少预算单价。请点击节点，在连接表单中更新输入和输出单价。")
    # Keep each side as a continuous stack. A long model form must not push
    # the source inputs down to the bottom of an unrelated, shared-height row.
    with st.container(key="workbench-layout"):
        if targets:
            source_col, setup_col = st.columns([1.65, 1], gap="medium")
        else:
            source_col, setup_col = st.container(), None
    if targets:
        with source_col, st.container(border=True, key="workbench-canvas-panel"):
            section_heading("工作流节点配置", "点击节点查看步骤；需要模型的节点可在右侧选择。", "◇")
            render_canvas(canvas_spec(targets, {node: {"status": "configuration_required"} for node, _ in model_issues}, selected_node, GRAPH_LABELS, STAGE_GLYPHS,
                                      snapshot_available_bindings(graph_nodes, source_mode, bindings, endpoints),
                                      language=st.session_state.get("ui_language", "zh"), source_mode=source_mode),
                          selection_key, key=f"setup-canvas:{ws}")
        with setup_col, st.container(border=True, key="workbench-node-panel"):
            section_heading(GRAPH_LABELS[selected_node], "所选节点", STAGE_GLYPHS[selected_node])
            render_node_models(selected_node, source_mode, ws, bindings, endpoints,
                               backend_application=backend_application)
            if selected_node == "sft":
                sft_output_style = st.selectbox(
                    "SFT 推理内容输出", ("separated", "drop"),
                    key=f"workflow-sft-output-style:{ws}",
                    on_change=_save_sft_output_style, args=(ws,),
                    format_func=lambda value: translate_label(
                        "分字段保留推理" if value == "separated" else "只保留答案",
                        st.session_state.get("ui_language", "zh")),
                    help="仅影响本次任务的 SFT 训练文件，不改写审核证据或其他目标。",
                )
                st.caption("当前 SFT 节点独立设置；不会改变其他任务的输出方式。")
            if selected_node == "agent":
                render_agent_verification(ws, agent_capabilities, application.check_agent_sandbox)
            if selected_node == "ingest":
                st.caption("输入解析保留来源位置；开放需求按每批最多 50 个任务规划。")
            elif selected_node == "package":
                st.caption("只打包通过质量检查的记录，并附带来源与审核证据。")
    # A short, informational node leaves most of the inspector column unused.
    # Put common controls there, while keeping long model/verification forms
    # separate from the full-width controls below the workbench.
    compact_parameters = bool(targets and selected_node != "agent"
                              and not node_roles(selected_node, source_mode))
    with st.container(key=f"workbench-create:{ws}"):
        web_research, web_unavailable = None, False
        with source_col, st.container(border=True, key="workbench-source-panel"):
            section_heading("添加来源", f"本次来源类型：{source_mode}", "▤")
            if source_mode == "开放需求":
                uploaded, selected = [], []
                st.caption("描述任务、领域和使用场景，系统会规划并生成候选。")
                brief = _draft_brief("开放性需求", key=f"workflow-open-brief:{ws}", placeholder="例如：为设备维护助手生成中文训练数据，覆盖故障诊断、多轮追问与操作解释。")
                with st.container(border=True, key=f"workbench-web-research:{ws}"):
                    web_research, web_unavailable = _draft_web_control(
                        ws, application,
                        configured=bool(application.web_research_capabilities().get("brave_configured")))
                    if web_research and web_research["query"]:
                        try:
                            validate_web_research(web_research, brief=brief, targets=targets)
                        except ValueError as error:
                            messages = {
                                "web_research_requires_open_brief": "请先填写开放性需求，再开启联网检索。",
                                "web_research_requires_planning_target": "联网检索只为开放任务规划提供线索；仅选 Agent 轨迹等目标时，请先选择可规划的训练目标。",
                                "web_research_query_private_or_invalid": "检索词疑似包含私有信息或超过长度限制，请改用可公开的简短主题。",
                            }
                            st.warning(messages.get(str(error), "联网检索配置无效，请检查公开检索词与目标。"))
                            web_unavailable = True
            else:
                if source_mode == "Agent 上下文":
                    st.caption("导入完整的 JSON / JSONL 对话记录；工具轨迹需要真实观测。")
                else:
                    st.caption("上传 PDF、DOCX、TXT 或 Markdown；解析时保留来源位置。")
                upload_options = ({"on_change": _cache_source_uploads, "args": (input_cache, ws, source_mode)}
                                  if input_cache is not None else {})
                uploaded = st.file_uploader("上传文档 / 上下文记录", type=sorted(e[1:] for e in source_extensions),
                                            accept_multiple_files=True, max_upload_size=50,
                                            key=f"workflow-upload:{ws}:{source_mode}", **upload_options)
                if input_cache is not None:
                    st.caption("拖入文件即保存并选中，关闭或重启后仍可使用。")
                    # The callback already persisted and selected these uploads.
                    uploaded = []
                sources_key = f"workflow-sources:{ws}:{source_mode}"
                _restore_selection(ws, sources_key, [], file_labels)
                if file_labels:
                    selected = st.multiselect("本次使用的资料", list(file_labels),
                                              format_func=lambda path: file_labels[path],
                                              key=sources_key, on_change=_save_draft_value, args=(ws, sources_key))
                else:
                    selected = []
                upload_error = st.session_state.get(f"workflow-upload-error:{ws}:{source_mode}")
                if upload_error:
                    st.error(_upload_cache_error(upload_error))
                brief = _draft_brief("补充生成要求（可选）",
                                     placeholder="例如：重点覆盖故障诊断、证据引用与清晰的分步回答。",
                                     key=f"workflow-source-brief:{ws}:{source_mode}")
                st.caption("单文件最多 50 MiB，本次来源合计最多 200 MiB。")
        with (setup_col if compact_parameters else nullcontext()), st.container(
                border=True, key="workbench-parameters-panel"):
            section_heading("生成参数设置", "设置运行名称与本次处理范围", "⚙")
            scale_col, batch_col = ((st.container(), st.container()) if compact_parameters
                                    else st.columns(2, gap="medium"))
            with scale_col:
                default_run_name = ("Automatic data generation"
                                    if st.session_state.get("ui_language") == "en" else "自动数据生成")
                name = _draft_name(default_run_name, ws)
                st.caption("快捷规模 · 仍可输入自定义数量")
                for size_column, count in zip(st.columns(3, gap="small"), (1000, 10000, 50000)):
                    with size_column:
                        st.button(f"{count:,}", key=f"workflow-count-preset:{ws}:{count}",
                                  use_container_width=True, on_click=_select_candidate_count,
                                  args=(ws, count))
                sample_count = _draft_number("候选样本规模", 1, MAX_CANDIDATES, 1000, step=100, key=f"workflow-count:{ws}",
                                               help="设置单个生成目标的候选规模。质检后的实际导出数量可能较少；导入轨迹与 CPT 文档不会重复凑数。")
                tasks = sample_count
                conversation_turns = (_draft_number("每段对话轮数", 2, 8, 3, key=f"workflow-turns:{ws}",
                                                      help="仅用于新生成的多轮对话；导入的完整对话保持原有轮次。")
                                      if "multiturn" in targets else 3)
            with batch_col:
                st.caption("分批生成 · 失败后可从逐条断点继续")
                a, b = st.columns(2, gap="small")
                with a:
                    concurrency = _draft_number("并发请求上限", 1, MAX_CONCURRENCY, 4, key=f"workflow-concurrency:{ws}",
                                              help="同一节点内同时处理的样本数。可按模型服务的限流调低；阶段仍按数据依赖顺序执行。")
                with b:
                    batch_size = _draft_number("每批候选数", 1, MAX_BATCH_SIZE, 100, key=f"workflow-batch-size:{ws}",
                                             help="只将当前批次送入执行队列，完成后再读取下一批；每条结果单独保存断点。")
                with st.expander("输入范围与文档分块（可选）"):
                    range_col, chunk_col = (st.columns(2, gap="small") if source_mode == "文档资料"
                                            else (nullcontext(), nullcontext()))
                    with range_col:
                        maximum = _draft_number("本次最多处理单元", 1, MAX_CANDIDATES, MAX_CANDIDATES, key=f"workflow-max-units:{ws}",
                                              help="限制来源解析后的处理范围。开放需求规划也受此上限约束。")
                    with chunk_col:
                        chunk_chars = (_draft_number("文档分块目标字符数", 200, 20000, 2000, key=f"workflow-chunk-chars:{ws}")
                                   if source_mode == "文档资料" else 2000)
                planning_count = min(int(sample_count), int(maximum)) if source_mode == "开放需求" else int(sample_count)
                if source_mode == "开放需求" and maximum < sample_count:
                    st.warning("处理上限低于候选规模。本次开放需求只规划到处理上限；其余候选不会在本次运行中生成。")
                batch_summary, request_summary = st.columns(2, gap="small")
                batch_summary.metric("每个生成目标的候选批次", f"{(planning_count + int(batch_size) - 1) // int(batch_size):,}")
                request_summary.metric("同时处理的样本上限", f"{min(int(concurrency), int(batch_size)):,}")
                st.caption("批次数按候选规模估算；不代表模型调用次数或合格数量。CPT 与导入轨迹按实际来源处理。")
                evaluation_uploads = []
                if "cpt" in targets:
                    with st.expander("预训练评测集去污染（可选）"):
                        st.caption("上传自备 JSON / JSONL 评测参照，每条记录格式为 {\"text\": \"...\"}。"
                                   "只在本机对 CPT 候选查重，不作为训练来源，也不发送给模型；未上传时报告会标记未配置。")
                        evaluation_uploads = st.file_uploader(
                            "上传评测集参照", type=["json", "jsonl"], accept_multiple_files=True,
                            max_upload_size=5, key=f"workflow-evaluations:{ws}")
        with st.container(border=True, key="workbench-submit"):
            agent_mode = st.session_state.get(f"workflow-agent-mode:{ws}", "local")
            agent_unavailable = ("agent" in targets and agent_mode == "isolated"
                                 and not agent_capabilities.get("isolated_configured"))
            if agent_unavailable:
                st.warning("Agent 节点的隔离验证环境未配置，请检查该节点。")
            style_summary = (translate("分字段保留推理" if sft_output_style == "separated"
                                       else "只保留答案",
                                       st.session_state.get("ui_language", "zh"))
                             if "sft" in targets else "")
            language = st.session_state.get("ui_language", "zh")
            run_summary = translate(
                f"{source_mode} · 已选 {len(targets)} 类目标 · 候选规模 {int(sample_count):,}"
                f" · 并发 {int(concurrency)} · 每批 {int(batch_size)}",
                language,
            )
            if "sft" in targets:
                run_summary += f" · SFT {style_summary}"
            if evaluation_uploads:
                run_summary += (f" · {len(evaluation_uploads)} evaluation references" if language == "en"
                                else f" · 评测参照 {len(evaluation_uploads)} 份")
            summary_col, action_col = st.columns([3, 1], vertical_alignment="center", gap="large")
            with summary_col:
                st.html('<div class="df-wb-submit-summary"><b>运行配置摘要</b><strong>' +
                        html.escape(translate(name.strip() or "未命名任务", language)) +
                        '</strong><span>' + html.escape(run_summary) + '</span></div>')
                st.caption("先解析来源，再生成所选目标并执行质检；结束后可进入人工审核或输出打包。")
            with action_col:
                submitted = st.button("开始自动生成", type="primary", disabled=not targets or bool(model_issues)
                                      or bool(pricing_issues) or agent_unavailable or web_unavailable
                                      or bool(st.session_state.get(f"workflow-upload-error:{ws}:{source_mode}")),
                                      key=f"workflow-create:{ws}", width="stretch")
                if draft_application is not None:
                    if st.button("保存为独立草稿", key=f"workflow-save-draft:{ws}", width="stretch",
                                 help="保留这份配置，稍后可从工作管理打开。不会开始生成。"):
                        try:
                            draft_application.save_snapshot(
                                name, values=st.session_state.get(f"workflow-form-draft:{ws}", {}))
                            st.success("独立草稿已保存，可从工作管理继续。")
                        except (ValueError, OSError, Timeout):
                            st.error("草稿未能保存，当前配置仍在。请检查本机存储后重试。")
        if submitted:
            try:
                # Upload filenames never become filesystem paths; preserve only their extension.
                with tempfile.TemporaryDirectory(prefix="dataforge-upload-") as temporary:
                    sources = [Path(path) for path in selected]
                    source_names = {}
                    for index, upload in enumerate(uploaded or []):
                        path = Path(temporary) / f"upload-{index}{Path(upload.name).suffix.lower()}"
                        path.write_bytes(upload.getvalue())
                        sources.append(path)
                        source_names[str(path.resolve())] = upload.name
                    evaluation_sources = []
                    evaluation_source_names = {}
                    for index, upload in enumerate(evaluation_uploads or []):
                        path = Path(temporary) / f"evaluation-{index}{Path(upload.name).suffix.lower()}"
                        path.write_bytes(upload.getvalue())
                        evaluation_sources.append(path)
                        evaluation_source_names[str(path.resolve())] = upload.name
                    if source_mode != "开放需求" and not sources:
                        raise ValueError(f"请先上传或选择{source_mode}来源。")
                    if source_mode == "开放需求" and not brief.strip():
                        raise ValueError("请描述开放性需求。")
                    run_id = application.create_run(sources=sources, brief=brief, name=name, targets=targets,
                                        node_models=model_application.snapshot(graph_nodes, source_mode, bindings),
                                        sample_count=int(sample_count), concurrency=int(concurrency), batch_size=int(batch_size),
                                        max_units=int(maximum), chunk_chars=int(chunk_chars), tasks=int(tasks),
                                        conversation_turns=int(conversation_turns),
                                        agent_replay_mode=agent_mode,
                                        sft_output_style=sft_output_style,
                                        web_research=web_research,
                                        source_names=source_names,
                                        evaluation_sources=evaluation_sources,
                                        evaluation_source_names=evaluation_source_names)
                st.session_state[f"workflow-selected:{ws}"] = run_id
                begin(["workflow", "--action", "resume", "--run-id", run_id])
                # Show the actual running graph immediately instead of leaving the
                # operator above the long creation form.  The app consumes this
                # handoff before its sidebar navigation widget is instantiated.
                st.session_state["workflow-open-run"] = {"workspace": ws, "run_id": run_id}
                st.rerun()
            except (ValueError, OSError, Timeout) as error:
                st.error(_workflow_error(error))
    runs = application.task_runs()
    if not runs:
        st.info("尚无运行记录。创建工作流后，这里会显示实时阶段、质量统计与产物。")
        return
    lookup = {r["id"]: r for r in runs}
    history_language = st.session_state.get("ui_language", "zh")
    selected_id = st.selectbox("运行记录", list(lookup), key=f"workflow-selected:{ws}",
                              format_func=lambda rid: UntranslatedText(
                                  f"{lookup[rid]['name']} · "
                                  + translate(LABELS.get(lookup[rid]['status'], lookup[rid]['status']),
                                              history_language)
                                  + f" · {rid[:8]}"))
    if st.button("查看所选任务", key=f"workflow-open-history:{ws}"):
        st.session_state["workflow-open-run"] = {"workspace": ws, "run_id": selected_id}
        st.rerun()
