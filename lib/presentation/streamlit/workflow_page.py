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
from lib.presentation.streamlit.workflow_stream_view import render_stream_output
from lib.presentation.streamlit.workflow_workbench_style import workbench_style
from lib.domain.workflow_targets import TARGETS
from lib.domain.web_research import MAX_QUERIES, validate_web_research
from lib.domain.workflow_scale import MAX_CANDIDATES, MAX_CONCURRENCY, MAX_BATCH_SIZE, node_roles
from lib.domain.workflow_node_models import missing_bindings
from lib.domain.workflow_node_prompts import active_node_prompt_ids
from lib.presentation.streamlit.workflow_package_review_settings import (
    package_review_snapshot, render_package_review_toggle, render_package_review_settings,
)
from lib.presentation.streamlit.workflow_director_settings import (
    director_snapshot, director_has_issue, render_director_toggle,
    render_director_settings, render_director_rules, TYPE_LABELS,
)
from lib.presentation.streamlit.workflow_canvas import canvas_spec, render_canvas
from lib.presentation.streamlit.workflow_node_settings import node_bindings, render_node_models, snapshot_available_bindings, render_agent_verification, render_document_parser, document_parser_mode
from lib.presentation.streamlit.workflow_generation_settings import (
    STYLE_LABELS, TRIM_LABELS, generation_snapshot, generation_issues, generation_summary,
    render_generation_settings, render_trim_toggle, render_trim_settings, trim_snapshot, trim_has_issue,
)
from lib.presentation.streamlit.workflow_prompt_settings import (
    node_prompt_snapshot, node_prompt_has_issue, render_node_prompts, render_run_node_prompts,
)


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
    "自选目标": (),
}


def _select_setup_node(key: str, node: str) -> None:
    st.session_state[key] = node
    workspace = key.removeprefix("workflow-setup-node:")
    st.session_state[f"canvas-open:setup-canvas:{workspace}"] = True


def _change_source_mode(workspace, key):
    _save_draft_value(workspace, key)
    st.session_state[f"workflow-setup-node:{workspace}"] = "ingest"


def _save_sft_output_style(workspace: str) -> None:
    key = f"workflow-sft-output-style:{workspace}"
    value = st.session_state.get(key)
    if value in {"separated", "drop"}:
        st.session_state[f"workflow-sft-output-style-draft:{workspace}"] = value
        _save_draft_value(workspace, key)


def _workflow_error(error) -> str:
    return {
        "invalid_package_review": "AI 打包评审配置无效，请检查质检打包节点。",
        "invalid_qa_director": "问答指导员配置无效，请检查题型配比与规则。",
        "invalid_qa_director_batch_size": "每批指导任务数应为 1 到 50，请调整指导员节点。",
        "invalid_qa_director_history_limit": "相似历史参考数应为 0 到 20，请调整指导员节点。",
        "invalid_qa_director_type_weights": "题型配比应为 0 到 100，且至少一种大于 0，请调整指导员节点。",
        "invalid_qa_director_rules": "指导员规则不能为空或含无效字符，且每项最多 12,000 字符。",
        "invalid_qa_director_schedule": "指导员无法分配本批题型，请检查题型配比或复制配置后新建任务。",
        "qa_director_requires_qa_target": "请先选择问答或偏好训练目标，再开启问答指导员。",
        "invalid_qa_director_plan": "指导员返回的任务不符合本次规则，请重试当前批次。",
        "invalid_qa_director_batch": "指导员返回的任务数量或结构无效，请重试当前批次。",
        "invalid_qa_director_task": "指导员任务字段无效，请检查节点提示词后重试。",
        "qa_director_type_mismatch": "指导员返回的题型与本批分配不符，请调整指导员提示词后新建任务。",
        "qa_director_id_mismatch": "指导员返回的任务标识与来源不符，请检查指导员提示词并保留任务记录。",
        "qa_director_batch_id_mismatch": "指导员返回了重复或不匹配的任务标识，请调整指导员提示词后新建任务。",
        "invalid_qa_director_answer_policy": "指导员返回了不支持的回答策略，请检查指导员提示词要求的输出字段。",
        "qa_director_closed_book_contract": "无线索任务包含了可见资料，请调整指导员提示词，或选择有线索题型。",
        "qa_director_visible_evidence_required": "本题型缺少必要的可见线索，请补充来源资料或调整指导员提示词。",
        "invalid_qa_director_evidence": "指导员返回的证据格式无效，请检查来源资料与指导员提示词。",
        "qa_director_multiple_evidence_required": "多线索任务需要至少两段不同的来源证据，请补充资料或调整题型配比。",
        "qa_director_recorded_conversation_changed": "已有对话不应交由指导员改写，请保留任务记录并复制配置后新建任务。",
        "invalid_qa_director_judge_schema": "契约评审没有返回有效结果，请检查评审提示词后重试。",
        "qa_director_context_too_small": "指导员上下文不足，请减小每批任务数或精简规则和提示词。",
        "qa_director_visible_context_not_in_source": "可见线索不在本次来源中，已阻止使用虚构资料。",
        "qa_director_evidence_not_in_source": "指导任务的引文无法在来源中找到，请重试当前批次。",
        "qa_director_evidence_not_visible": "所需证据没有加入样本的可见线索，请重试当前批次。",
        "qa_history_question_required": "历史问答缺少有效问题，请检查指导员任务与本机历史记录。",
        "qa_history_question_too_long": "历史问答的问题过长，请精简指导员的问题生成要求。",
        "qa_history_dimension_too_long": "历史问答的题型或策略字段过长，请检查指导员返回格式。",
        "qa_history_sample_id_required": "历史问答缺少有效样本标识，请保留任务记录并重新运行。",
        "qa_history_sample_id_conflict": "样本标识对应的历史问答已发生变化，请保留任务记录并新建任务。",
        "package_review_checkpoint_mismatch": "打包评审检查点与当前样本不一致，请保留任务记录并重新运行。",
        "web_search_not_configured": "网页检索服务未配置。设置检索密钥后可从断点重试。",
        "web_search_provider_error": "网页检索服务暂不可用。可从断点重试本次任务。",
        "web_search_no_safe_results": "没有找到可用的公开检索结果。可重试，或复制配置后调整检索词。",
        "web_search_connection_invalid": "本机检索连接无法读取。请检查本机存储后重新保存连接。",
        "model_stream_interrupted": "模型输出连接中断。可重试当前样本，已完成样本保留断点。",
        "model_stream_incomplete": "模型输出未完整结束。可重试，或复制配置后检查节点输出上限。",
        "model_stream_invalid_event": "模型返回的流式内容无效。请检查模型服务后重试。",
        "model_stream_storage_invalid": "实时输出缓存无法安全读写。请检查本机存储后重试。",
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
        "document_model_vision_not_confirmed": "模型尚未确认图片输入能力，请在输入节点核实并保存确认。",
        "document_vision_model_required": "请在输入节点选择多模态识别模型。",
        "document_image_requires_vision_parser": "图片需要多模态识别，请在输入节点配置模型。",
        "document_vision_requires_document_sources": "多模态识别需要文档或图片来源。",
        "document_pdf_renderer_unavailable": "PDF 页面识别组件不可用，请重新安装项目依赖。",
        "document_vision_page_limit": "单份文档最多识别 200 页或图片，请先拆分文件。",
        "document_image_too_large": "图片尺寸过大，请缩小后重新上传。",
        "invalid_document_image": "图片无法读取，请检查格式后重新上传。",
        "document_pdf_page_invalid": "PDF 页面尺寸无效，请检查文件。",
        "document_docx_expansion_limit": "DOCX 展开内容过大，请先拆分文件。",
        "document_external_image_unsupported": "DOCX 包含外部图片，请将图片嵌入文件后再上传。",
        "invalid_node_generation": "生成风格配置无效，请检查所选节点的附加指令。",
        "invalid_reasoning_trim": "推理链修剪配置无效，请检查修剪节点的模板与指令。",
        "reasoning_trim_requires_reasoning_target": "请先选择 SFT 或 CoT 目标，再开启推理链修剪。",
        "invalid_style_judge_schema": "核验模型未返回有效评分。可从断点重试，或新建任务调整节点模型。",
        "model_visible_output_missing": "模型只返回推理通道，未返回所需的答案正文。请检查模型输出设置后重试。",
        "invalid_node_prompts": "节点提示词配置无效，请检查对应节点的处理步骤。",
        "invalid_node_prompt_id": "节点提示词与处理步骤不匹配，请重新选择或恢复内置提示词。",
        "invalid_node_prompt_text": "节点提示词正文无效，请填写有效文本或恢复内置提示词。",
        "invalid_node_prompt_snapshot": "本次任务的提示词快照无效，请复制配置后新建任务。",
    }.get(str(error), str(error))


def _missing_budget_prices(nodes, source_mode, bindings, endpoints, budget, *, node_generation=None, package_review=None):
    if not (budget.get("max_total_usd") and budget.get("hard_stop", True)):
        return []
    missing = []
    for node in nodes:
        for role in node_roles(node, source_mode, node_generation=node_generation, package_review=package_review):
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
    if key in {"director", "cpt", "sft", "multiturn", "agent", "preference", "cot", "trim", "package"}:
        details = {"启用目标": [target.upper() for target in targets]}
        mode = "文档资料" if recipe.get("sources") else "开放需求"
        # Old CoT runs only checked an existing explanation and had no writer.
        roles = (("jev",) if key == "cot" and not (recipe.get("node_generation") or {}).get("cot")
                 else node_roles(key, mode, node_generation=recipe.get("node_generation"), package_review=recipe.get("package_review")))
        for role in roles:
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
        if roles:
            details.update({"并发请求上限": recipe.get("concurrency", 1), "每批候选数": recipe.get("batch_size", 100)})
        if key == "director":
            config = recipe.get("qa_director") or {}
            details.update({"每批指导任务数": config.get("batch_size"),
                            "相似历史参考数": config.get("history_limit"),
                            "问答调度指令": UntranslatedText(config.get("question_rules") or "—"),
                            "回答规则": UntranslatedText(config.get("answer_rules") or "—")})
            details.update({TYPE_LABELS.get(name, name): weight for name, weight in
                            config.get("type_weights", {}).items()})
        elif key == "cpt":
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
        if key in {"sft", "cot"}:
            generation = (recipe.get("node_generation") or {}).get(key)
            if generation:
                enabled = generation.get("enabled", True)
                details["生成方式"] = "风格化生成" if enabled else "普通蒸馏"
                if enabled:
                    details["生成风格"] = STYLE_LABELS.get(generation.get("style"), "遵循任务")
                    details["附加风格指令"] = (UntranslatedText(generation["instruction"])
                                                   if generation.get("instruction") else "未填写")
            elif key == "cot":
                details["生成方式"] = "旧版解释核验"
            if key == "sft" and "sft" in targets:
                details["SFT 训练文件格式"] = ("只保留答案" if recipe.get("sft_output_style") == "drop"
                                              else "分字段保留推理")
        elif key == "package":
            review = recipe.get("package_review") or {}
            details.update({"输出目标": [target.upper() for target in targets],
                            "质检证据": "逐条记录、失败原因、来源指纹与 SHA-256 清单",
                            "发布状态": "自动检查候选，尚未完成人工审核"})
            details["打包前 AI 评审"] = "已开启" if review.get("enabled") else "未开启"
            if review.get("enabled"):
                details["评审范围"] = "抽检" if review.get("mode") == "sample" else "全量评审"
                if review.get("mode") == "sample":
                    details["抽检比例（%）"] = review.get("sample_percent")
                    details["每类抽检上限"] = review.get("max_samples_per_target")
            details["失败样本"] = "单独保存原因和执行证据，不混入训练样本"
        elif key == "trim":
            trim = recipe.get("reasoning_trim") or {}
            details["修剪模板"] = TRIM_LABELS.get(trim.get("template"), "提示词与包装清理")
            details["处理范围"] = "仅处理推理，保留最终答案与必要知识"
            if trim.get("instruction"):
                details["附加修剪指令"] = UntranslatedText(trim["instruction"])
        return details
    if key == "gsm8k":
        return {"任务数": recipe.get("tasks"), "生成方式": "本地整数算术模板",
                "验证方式": "受限 AST 逐步计算，不调用模型"}
    return {"启用目标": [target.upper() for target in targets]}


STAGE_GLYPHS = {"ingest": "▤", "director": "⌘", "cpt": "▥", "sft": "✎", "multiturn": "☷", "agent": "◇",
                "preference": "⚖", "gsm8k": "∑", "cot": "◈", "trim": "✂", "package": "▣"}
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


GRAPH_LABELS = {"ingest": "输入解析", "director": "问答指导员", "cpt": "CPT 语料", "sft": "SFT 生成",
                "multiturn": "多轮对话", "agent": "Agent 轨迹", "gsm8k": "算术核验",
                "preference": "偏好评审", "cot": "CoT 推理生成", "trim": "推理链修剪", "package": "质检打包"}


def _missing_model_role_summary(issues, language):
    pending_roles = {}
    for node, role in issues:
        pending_roles.setdefault(node, []).append(role)
    role_labels = {"generation": "生成模型", "jev": "独立质量评审模型", "vision": "多模态识别模型"}
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
    from lib.presentation.streamlit.review_navigation import request_app_navigation
    st.session_state["workflow-open-preview"] = {
        "workspace": st.session_state["ws"], "run_id": run_id, "target": target}
    request_app_navigation(run_id)


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
def render_run(application, run_id, begin, *, embedded=False, draft_application=None):
    from lib.presentation.streamlit.review_navigation import consume_app_navigation
    consume_app_navigation(run_id)
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
        reasoning_trim_enabled = bool((recipe.get("reasoning_trim") or {}).get("enabled"))
        graph_nodes, _ = execution_graph(recipe["targets"], reasoning_trim=reasoning_trim_enabled,
                                         qa_director=recipe.get("qa_director"))
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
        if any(row.get("package_review", {}).get("rejected", 0)
               for row in state.get("quality", {}).get("targets", {}).values()):
            st.warning("本次 AI 打包评审隔离了部分样本；通过检查的样本仍可导出，评审证据保留在 ZIP 中。")
        else:
            st.warning("运行已结束；有目标没有合格样本，或输入超过本次处理上限。查看下方质量报告。")
    else:
        st.info(LABELS.get(status, status))
    console_job = st.session_state.get(f"job:{st.session_state.get('ws', '')}:{run_id}")
    if console_job is not None and status == "queued":
        exit_code, _ = console_job.snapshot()
        if exit_code not in (None, 0):
            st.error("任务进程未能启动。请检查本机运行环境后从断点重试。")
    _render_research_receipt(application, run_id, recipe, state)
    resumable = not active and status in {"queued", "running", "failed", "cancelled"}
    if resumable:
        st.caption("继续执行沿用本次来源快照与节点配方。已保存的逐条断点会校验后复用；修改模型或生成参数，请创建新任务。")
    if active or resumable:
        action, copy_action = (st.columns(2, gap="small") if draft_application is not None
                               else (st.container(), None))
        with action:
            if active:
                if st.button("停止后续步骤", key=f"stop:{run_id}", width="stretch"):
                    application.cancel(run_id)
                    st.info("已请求停止；当前模型请求返回后，在下一断点停止。")
            elif st.button("继续执行 / 从断点重试", type="primary", key=f"resume:{run_id}", width="stretch"):
                begin(["workflow", "--action", "resume", "--run-id", run_id])
    elif draft_application is not None:
        copy_action = st.container()
    if draft_application is not None:
        from lib.presentation.streamlit.workflow_reuse import reuse_run_as_draft
        workspace = st.session_state["ws"]
        with copy_action:
            if st.button("复制配置并修改", key=f"workflow-reuse:{run_id}", width="stretch",
                         help="保留当前未完成草稿，复制本次配置后调整；不会修改或启动原任务。"):
                if reuse_run_as_draft(application, draft_application, workspace, run_id):
                    st.rerun(scope="app")
        if error := st.session_state.pop(f"workflow-reuse-error:{workspace}", None):
            st.error(error)
    flow, inspector = st.columns([2.25, 1], gap="medium")
    with flow:
        with st.container(border=True, key=f"workflow-run-canvas:{run_id}"):
            st.html('<div class="df-run-section"><div><strong>工作流运行图</strong>'
                    '<small>点击节点，展开模型的实时输出；配置与日志保留在当前页。</small></div>'
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
                                      language=st.session_state.get("ui_language", "zh"), live=True,
                                      reasoning_trim=reasoning_trim_enabled,
                                      node_generation=recipe.get("node_generation"), package_review=recipe.get("package_review"),
                                      qa_director=recipe.get("qa_director")),
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
    elif selected_stage == "director":
        passed, quarantined = done, max(0, total - done)
        passed_label, quarantined_label = "已规划任务", "待规划任务"
    elif selected_stage == "package":
        if selected_status == "running" and selected_metrics.get("phase") in {"ai_review", "writing_artifacts"}:
            passed = int(selected_metrics.get("eligible", 0) or 0)
            quarantined = int(selected_metrics.get("quarantined", 0) or 0)
            passed_label, quarantined_label = "AI 通过", "AI 隔离"
        else:
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
        with st.container(border=True, key=f"workflow-run-inspector:{run_id}"):
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
            if selected_stage == "package" and selected_status == "running":
                phase_label = {"deterministic_checks": "正在核对结构、去重与打包规则。",
                               "ai_review": "正在逐条 AI 评审；通过和隔离数量实时更新。",
                               "writing_artifacts": "AI 评审已结束，正在写入训练文件、报告与清单。"}.get(selected_metrics.get("phase"))
                if phase_label:
                    st.caption(phase_label)
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
            if selected_stage == "director" and state.get("qa_director", {}).get("coverage"):
                language = st.session_state.get("ui_language", "zh")
                coverage = state["qa_director"]["coverage"]
                st.dataframe([
                    {translate_label("问答类型", language): translate_label(TYPE_LABELS.get(kind, kind), language),
                     **{translate_label(label, language): counts.get(field, 0) for field, label in
                        (("planned", "已规划任务"), ("assigned", "已调度"),
                         ("accepted", "生成通过"), ("rejected", "生成隔离"))}}
                    for kind, counts in coverage.items()
                ], hide_index=True, width="stretch")
                st.caption("规划数按任务统计；调度与生成验收数包含 SFT 和多轮目标。最终导出数量另见打包结果。")
            st.html(_config_html(_stage_configuration(selected_stage, recipe, state)))
            render_run_node_prompts(selected_stage, recipe, run_id)
            if selected_metrics.get("error"):
                st.error(f"节点错误：{_workflow_error(selected_metrics['error'])}")
    selected_events = [event for event in state.get("events", []) if event.get("stage") == selected_stage]
    output_open_key = f"canvas-open:live-canvas:{run_id}"
    if st.session_state.get(output_open_key):
        with flow, st.container(border=True, key=f"workflow-node-output:{run_id}:{selected_stage}"):
            heading, close = st.columns([4, 1], vertical_alignment="center")
            with heading:
                st.html('<div class="df-run-section"><div><strong>'
                        + html.escape(translate(GRAPH_LABELS.get(selected_stage, selected_stage),
                                               st.session_state.get("ui_language", "zh")))
                        + ' · ' + html.escape(translate("实时输出", st.session_state.get("ui_language", "zh")))
                        + '</strong></div></div>')
            with close:
                st.button("收起输出", key=f"close-node-output:{run_id}",
                          on_click=st.session_state.pop, args=(output_open_key, None), width="stretch")
            render_stream_output(application, run_id, selected_stage)
    with flow, st.container(border=True, key=f"workflow-run-log:{run_id}"):
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
                from lib.presentation.streamlit.review_navigation import (
                    review_choices, open_verified_review, open_package,
                )
                review_targets = dict(review_choices(state, {
                    "counts": {target: item.get("eligible", 0)
                               for target, item in quality["targets"].items()},
                    "negative_counts": {"agent": quality["targets"].get("agent", {}).get("negative", 0)},
                }))
                package_action, review_action = (st.columns(2, gap="small") if review_targets
                                                  else (st.container(), None))
                with package_action:
                    st.button("查看并打包本次训练数据", on_click=open_package, args=(run_id,),
                              kwargs={"from_fragment": True},
                              type="primary", key=f"zip:{run_id}", width="stretch")
                if review_targets:
                    with review_action:
                        if len(review_targets) == 1:
                            review_target = next(iter(review_targets))
                        else:
                            review_target = st.selectbox(
                                "选择审核目标", list(review_targets),
                                format_func=review_targets.__getitem__, key=f"workflow-review-target:{run_id}")
                        st.button("进入当前任务的人工审核", on_click=open_verified_review,
                                  args=(application, run_id, review_target), key=f"workflow-review:{run_id}",
                                  kwargs={"from_fragment": True},
                                  width="stretch")
                if st.session_state.pop(f"workflow-review-error:{run_id}", False):
                    st.error("无法读取或校验任务产物，请检查本次任务文件后重试。")
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


def _save_web_connection(workspace, application):
    key = f"workflow-web-api-key:{workspace}"
    value = st.session_state.get(key, "").strip()
    if not value:
        st.session_state[f"workflow-web-save-result:{workspace}"] = "empty"
        return
    try:
        application.save_web_research_connection(value)
    except (OSError, ValueError, Timeout):
        st.session_state[f"workflow-web-save-result:{workspace}"] = "error"
    else:
        st.session_state[f"workflow-web-save-result:{workspace}"] = "saved"
        st.session_state.pop(f"workflow-web-check-result:{workspace}", None)
    finally:
        # The widget is a temporary entry field, not a second credential store.
        st.session_state[key] = ""


def _draft_web_control(workspace, application: WorkflowApplication, *, configured: bool,
                       connection: dict | None = None):
    """Explicit public topics, never an implicit copy of the private brief."""
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    enabled_key = f"workflow-web-research-enabled:{workspace}"
    consent_key = f"workflow-web-research-session-consent:{workspace}"
    if enabled_key not in st.session_state:
        st.session_state[enabled_key] = st.session_state.get(consent_key, False)
    connection = connection or {}
    revision = connection.get("connection_revision", configured)
    section_heading("联网资料", "输入解析节点的可选规划线索", "⌕")
    status_column, check_column = st.columns([2, 1], vertical_alignment="center", gap="small")
    with status_column:
        if configured:
            st.caption("Brave Search · 已保存本机检索连接" if connection.get("key_source") == "local"
                       else "Brave Search · 已检测到运行环境中的检索密钥")
        else:
            st.caption("Brave Search · 未配置，可在下方保存检索连接。")
    with check_column:
        check_clicked = st.button("检查 Brave 连接", key=f"workflow-web-check:{workspace}",
                                  use_container_width=True,
                                  disabled=not configured,
                                  help="仅在点击时向 Brave 发送固定公开词 Brave Search；不会发送需求、上传资料或下方检索词。")
    with st.expander("配置检索连接", expanded=bool(st.session_state[enabled_key] and not configured)):
        secret_key = f"workflow-web-api-key:{workspace}"
        st.text_input("Brave Search API 密钥", type="password", key=secret_key,
                      help="仅保存在本机连接中，不进入工作草稿、任务配方或导出文件。")
        st.button("保存检索连接", key=f"workflow-web-save:{workspace}",
                  on_click=_save_web_connection, args=(workspace, application), width="stretch")
        st.caption("保存后立即生效，无需重启。保存不会发起检索。")
    save_result = st.session_state.pop(f"workflow-web-save-result:{workspace}", None)
    if save_result == "saved":
        st.success("检索连接已保存，可检查连接或继续配置任务。")
    elif save_result == "error":
        st.error("检索连接未保存。请检查密钥与本机存储后重试。")
    elif save_result == "empty":
        st.warning("请先填写 Brave Search API 密钥。")
    if connection.get("connection_error"):
        st.warning("本机检索连接无法读取，请检查本机存储后重试。")
    result_key = f"workflow-web-check-result:{workspace}"
    if check_clicked:
        with st.spinner("正在检查 Brave 连接…"):
            st.session_state[result_key] = (revision, application.check_web_research_connection())
    prior = st.session_state.get(result_key)
    if isinstance(prior, tuple) and len(prior) == 2 and prior[0] == revision:
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
        st.warning("请先展开配置检索连接，保存 Brave Search 密钥。")
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
                     backend_application=None, input_cache=None, manual_application=None, document_preview=None,
                     knowledge_application=None):
    page_header("数据生成工作台", "导入文档或上下文，自动生成训练数据；也可以人工制作图片与文字问答。", "CPT　·　SFT　·　DPO　·　MULTIMODAL")
    st.html(workbench_style(st.session_state.get("ui_language", "zh")))
    ws = st.session_state["ws"]
    if manual_application is not None:
        creation_key = f"workflow-creation-mode:{ws}"
        if st.session_state.get(creation_key) not in {"自动生成", "人工制作图文"}:
            st.session_state[creation_key] = "自动生成"
        creation_mode = st.segmented_control(
            "制作方式", ("自动生成", "人工制作图文"), default=None, key=creation_key,
        )
        if creation_mode == "人工制作图文":
            from lib.presentation.streamlit.manual_dataset_page import render_manual_datasets
            render_manual_datasets(manual_application, ws, show_title=False)
            return
    st.html(
        '<div class="df-wizard-steps">'
        '<div class="df-wizard-step active"><b>1</b><span><strong>配置本次任务</strong><small>来源、目标与节点模型</small></span></div>'
        '<i></i><div class="df-wizard-step"><b>2</b><span><strong>自动生成与质检</strong><small>实时查看阶段与结果</small></span></div>'
        '<i></i><div class="df-wizard-step"><b>3</b><span><strong>审核与导出</strong><small>核对后生成训练包</small></span></div>'
        '</div>'
    )
    if notice := st.session_state.pop(f"workflow-reuse-notice:{ws}", None):
        st.success("已复制到新草稿，可直接修改后运行。联网检索需重新开启。")
        if notice.get("missing_sources"):
            st.warning("部分来源无法匹配本机文件，请重新选择或上传。")
            st.caption(UntranslatedText("、".join(notice["missing_sources"][:5])))
        if notice.get("evaluation_references_omitted"):
            st.info("评测参照未复制，请在需要时重新添加。")
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
    entry_target = st.session_state.pop(f"workflow-entry-target:{ws}", None)
    if entry_target in TARGETS:
        # A named entry chooses that exact output. Old edits to a quick plan
        # must not silently override the goal the user has just selected.
        entry_values = {f"workflow-preset:{ws}": "自选目标",
                        f"workflow-targets:{ws}:自选目标": [entry_target]}
        if entry_target in {"cpt", "agent"}:
            entry_values[f"workflow-source-mode:{ws}"] = "文档资料" if entry_target == "cpt" else "Agent 上下文"
        for entry_key, entry_value in entry_values.items():
            st.session_state[entry_key] = entry_value
            _save_draft_value(ws, entry_key)
    preset_key = f"workflow-preset:{ws}"
    _restore_selection(ws, preset_key, "自动推荐", PRESETS)
    source_key = f"workflow-source-mode:{ws}"
    source_modes = ("文档资料", "Agent 上下文", "知识库检索", "开放需求")
    _restore_selection(ws, source_key, "文档资料", source_modes)
    source_mode = st.segmented_control(
        "选择来源类型", source_modes,
        default=None, key=source_key, on_change=_change_source_mode, args=(ws, source_key),
        help="按来源选择合适的输入；文档或 Agent 记录还可以附加生成要求。",
    ) or "文档资料"
    source_extensions = ({".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp"} if source_mode == "文档资料"
                         else {".json", ".jsonl"} if source_mode == "Agent 上下文" else set())
    files = application.source_files(ws, suffixes=frozenset(source_extensions), limit=5000) if source_extensions else []
    file_labels = {row["path"]: row["label"] for row in files}
    sources_key = f"workflow-sources:{ws}:{source_mode}"
    saved_sources = st.session_state.get(sources_key, st.session_state.get(f"workflow-form-draft:{ws}", {}).get(sources_key, []))
    unavailable_sources = 0
    # The bounded inventory must not silently erase valid selections from a
    # saved draft or a library handoff beyond its first 5,000 files.
    for saved_source in saved_sources if isinstance(saved_sources, list) and source_extensions and document_preview is not None else []:
        if isinstance(saved_source, str) and saved_source not in file_labels:
            try:
                safe_source = document_preview.describe(saved_source, frozenset(source_extensions))
                file_labels[saved_source] = safe_source["label"]
            except (OSError, ValueError):
                unavailable_sources += 1
    with st.container(border=True, key="workbench-targets"):
        heading_col, preset_col = st.columns([2, 1], vertical_alignment="center")
        with heading_col:
            section_heading("选择训练目标", icon="◈")
        with preset_col:
            preset = st.selectbox("快捷方案", tuple(PRESETS), key=preset_key,
                on_change=_save_draft_value, args=(ws, preset_key),
                help="选择常用目标组合。下面仍可逐项增删训练目标。")
        target_key = f"workflow-targets:{ws}:{preset}"
        target_defaults = [target for target in PRESETS[preset] if target in TARGETS]
        _restore_selection(ws, target_key, target_defaults, TARGETS)
        targets = st.pills(
            "训练目标", list(TARGETS), default=None,
            format_func=lambda target: TARGET_LABELS.get(target, target.upper()),
            key=target_key, selection_mode="multi", wrap=True,
            on_change=_save_draft_value, args=(ws, target_key),
            help="点击即可选中或取消，可以同时选择多类训练数据。",
        ) or []
        reasoning_trim = render_trim_toggle(ws, targets, save_field=_save_draft_value)
        qa_director = render_director_toggle(ws, targets, save_field=_save_draft_value)
        graph_nodes, graph_edges = execution_graph(targets, reasoning_trim=reasoning_trim["enabled"],
                                                   qa_director=qa_director)
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
        if qa_director["enabled"] and source_mode == "Agent 上下文":
            st.caption("已有完整对话保留原文并绕过指导员；指导员只安排文档与开放需求生成的问答。")
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
    document_parser = {"mode": "native"}
    if source_mode == "文档资料" and document_parser_mode(ws) == "vision":
        document_parser = {"mode": "vision"}
        saved_binding = st.session_state.get(f"workflow-node-bindings:{ws}", {}).get("ingest", {}).get("vision")
        if saved_binding:
            document_parser["binding"] = saved_binding
    package_review = package_review_snapshot(ws)
    if targets:
        node_generation = generation_snapshot(ws, graph_nodes)
        selection_key = f"workflow-setup-node:{ws}"
        selected_node = st.session_state.get(selection_key, "sft" if "sft" in graph_nodes else "ingest")
        if selected_node not in graph_nodes:
            selected_node = graph_nodes[0]
        st.session_state[selection_key] = selected_node
        model_source_mode = "多模态文档" if document_parser.get("mode") == "vision" else source_mode
        bindings, endpoints = node_bindings(model_application, graph_nodes, model_source_mode, ws,
                                           node_generation=node_generation, package_review=package_review)
        if document_parser.get("mode") == "vision" and document_parser.get("binding"):
            from lib.domain.document_parser import supports_vision
            vision_binding = document_parser["binding"]
            if not supports_vision(endpoints.get(vision_binding["backend"], {}), vision_binding["model"]):
                document_parser["unconfirmed"] = True
        model_issues = missing_bindings(graph_nodes, model_source_mode, bindings, endpoints,
                                        node_generation=node_generation, package_review=package_review)
        if backend_application is not None:
            pricing_issues = _missing_budget_prices(
                graph_nodes, model_source_mode, bindings, endpoints,
                backend_application.list_backends().get("budget") or {},
                node_generation=node_generation, package_review=package_review,
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
    workbench = st.container(key="workbench-layout")
    if targets:
        with workbench, st.container(border=True, key="workbench-canvas-panel"):
            section_heading("工作流节点配置", "点击节点，就近配置模型、提示词与处理方式。", "◇")
            render_canvas(canvas_spec(targets, {node: {"status": "configuration_required"} for node, _ in model_issues}, selected_node, GRAPH_LABELS, STAGE_GLYPHS,
                                      snapshot_available_bindings(graph_nodes, model_source_mode, bindings, endpoints,
                                                                  node_generation=node_generation, package_review=package_review),
                                      language=st.session_state.get("ui_language", "zh"), source_mode=model_source_mode,
                                      reasoning_trim=reasoning_trim["enabled"], node_generation=node_generation, package_review=package_review,
                                      qa_director=qa_director),
                          selection_key, key=f"setup-canvas:{ws}",
                          inspector_key="workbench-node-panel", expanded=True)
        with workbench, st.container(border=True, key="workbench-node-panel"):
            with st.container(key="workbench-node-header"):
                title, close = st.columns([6, 1], gap="small", vertical_alignment="center")
                with title:
                    section_heading(GRAPH_LABELS[selected_node], "节点配置 · 自动保存", STAGE_GLYPHS[selected_node])
                with close:
                    st.button("✕", key=f"workflow-close-config:{ws}", help="收起节点配置",
                              on_click=st.session_state.update,
                              args=({f"canvas-open:setup-canvas:{ws}": False},), width="stretch")
            if selected_node == "package":
                render_package_review_toggle(ws, save_field=_save_draft_value)
            has_prompts = bool(active_node_prompt_ids(selected_node, model_source_mode,
                                                      node_generation=node_generation, package_review=package_review,
                                                      qa_director=qa_director))
            settings_tab, prompts_tab = (st.tabs(["节点设置", "提示词与风格"]) if has_prompts
                                        else (nullcontext(), nullcontext()))
            with settings_tab:
                if selected_node == "package":
                    render_package_review_settings(ws, save_field=_save_draft_value)
                elif selected_node == "director":
                    render_director_settings(ws, save_field=_save_draft_value)
                if selected_node == "ingest" and source_mode == "文档资料":
                    document_parser = render_document_parser(ws, bindings, endpoints,
                        backend_application=backend_application)
                    model_source_mode = "多模态文档" if document_parser.get("mode") == "vision" else source_mode
                elif selected_node == "ingest" and source_mode == "知识库检索" and knowledge_application is not None:
                    from lib.presentation.streamlit.knowledge_page import render_knowledge_settings
                    render_knowledge_settings(knowledge_application, ws)
                else:
                    render_node_models(selected_node, source_mode, ws, bindings, endpoints,
                                       backend_application=backend_application, node_generation=node_generation, package_review=package_review)
                if selected_node == "sft" and "sft" in targets:
                    sft_output_style = st.selectbox(
                        "SFT 训练文件格式", ("separated", "drop"),
                        key=f"workflow-sft-output-style:{ws}",
                        on_change=_save_sft_output_style, args=(ws,),
                        format_func=lambda value: translate_label(
                            "分字段保留推理" if value == "separated" else "只保留答案",
                            st.session_state.get("ui_language", "zh")),
                        help="仅影响本次任务的 SFT 训练文件，不改写审核证据或其他目标。",
                    )
                    st.caption("只控制训练文件是否包含推理字段；生成风格与审核证据独立保留。")
                if selected_node == "agent":
                    render_agent_verification(ws, agent_capabilities, application.check_agent_sandbox)
                if selected_node == "ingest" and source_mode != "知识库检索":
                    st.caption("输入解析保留来源位置；开放需求按每批最多 50 个任务规划。")
                elif selected_node == "package":
                    st.caption("只打包通过质量检查的记录，并附带来源与审核证据。")
            with prompts_tab:
                if selected_node in {"sft", "cot"}:
                    render_generation_settings(selected_node, ws, save_field=_save_draft_value)
                elif selected_node == "trim":
                    render_trim_settings(ws, save_field=_save_draft_value)
                elif selected_node == "director":
                    render_director_rules(ws, save_field=_save_draft_value)
                render_node_prompts(selected_node, model_source_mode, ws,
                                    save_field=_save_draft_value, node_generation=node_generation, package_review=package_review,
                                    qa_director=qa_director)
    # Source previews and common run controls stay in normal document flow.
    # Only the node-local form follows the selected graph node.
    with workbench:
        source_col, setup_col = st.columns([1.2, 1], gap="medium") if targets else (st.container(), None)
    node_generation = generation_snapshot(ws, graph_nodes) if targets else {}
    generation_invalid = generation_issues(node_generation)
    reasoning_trim = trim_snapshot(ws, eligible=bool(set(targets).intersection({"sft", "cot"})))
    trim_invalid = trim_has_issue(reasoning_trim)
    director_invalid = director_has_issue(qa_director)
    node_prompts = node_prompt_snapshot(ws, graph_nodes, model_source_mode,
                                       node_generation=node_generation, package_review=package_review,
                                       qa_director=qa_director) if targets else {}
    prompts_invalid = node_prompt_has_issue(node_prompts)
    # Pair source input with either its preview or the common run settings.
    # Node forms have no influence on the height of these normal-flow columns.
    preview_in_input = bool(targets and selected_node == "ingest" and source_mode == "文档资料"
                            and document_parser.get("mode") == "native" and document_preview is not None)
    compact_parameters = bool(targets and not preview_in_input and source_mode != "知识库检索")
    with st.container(key=f"workbench-create:{ws}"):
        web_research, web_unavailable = None, False
        knowledge_source_names = {}
        with source_col, st.container(border=True, key="workbench-source-panel"):
            section_heading("添加来源", f"本次来源类型：{source_mode}", "▤")
            if source_mode == "开放需求":
                uploaded, selected = [], []
                st.caption("描述任务、领域和使用场景，系统会规划并生成候选。")
                brief = _draft_brief("开放性需求", key=f"workflow-open-brief:{ws}", placeholder="例如：为设备维护助手生成中文训练数据，覆盖故障诊断、多轮追问与操作解释。")
                with st.container(border=True, key=f"workbench-web-research:{ws}"):
                    search_connection = application.web_research_capabilities()
                    web_research, web_unavailable = _draft_web_control(
                        ws, application,
                        configured=bool(search_connection.get("brave_configured")),
                        connection=search_connection)
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
            elif source_mode == "知识库检索":
                uploaded = []
                if knowledge_application is None:
                    st.error("知识库服务未加载，请重新打开应用。")
                    selected = []
                else:
                    from lib.presentation.streamlit.knowledge_page import render_knowledge_source
                    knowledge_files = application.source_files(ws, suffixes=frozenset({".md", ".txt", ".pdf", ".docx"}), limit=5000)
                    selected, knowledge_source_names = render_knowledge_source(knowledge_application, ws,
                        knowledge_files, input_cache=input_cache, save_field=_save_draft_value)
                brief = _draft_brief("补充生成要求（可选）", key=f"workflow-source-brief:{ws}:{source_mode}")
            else:
                if source_mode == "Agent 上下文":
                    st.caption("导入完整的 JSON / JSONL 对话记录；工具轨迹需要真实观测。")
                else:
                    st.caption("上传文档或图片；扫描件可在输入节点选择多模态识别。")
                upload_options = ({"on_change": _cache_source_uploads, "args": (input_cache, ws, source_mode)}
                                  if input_cache is not None else {})
                upload_types = sorted(e[1:] for e in source_extensions)
                upload_label = ("导入文档或图片" if source_mode == "文档资料"
                                else "导入 JSON / JSONL 上下文记录")
                uploaded = st.file_uploader(upload_label, type=upload_types,
                                            accept_multiple_files=True, max_upload_size=50,
                                            key=f"workflow-upload:{ws}:{source_mode}", **upload_options)
                if input_cache is not None:
                    st.caption("拖入文件即保存并选中，关闭或重启后仍可使用。")
                    # The callback already persisted and selected these uploads.
                    uploaded = []
                sources_key = f"workflow-sources:{ws}:{source_mode}"
                if len(files) >= 5000:
                    st.caption("仅列出前 5,000 份来源，已选资料会保留；更多文件可从资料库搜索后添加。")
                if unavailable_sources:
                    st.warning(f"有 {unavailable_sources} 份已选资料已失联、超限或不属于本机来源目录，已移出本次选择；请重新添加。")
                _restore_selection(ws, sources_key, [], file_labels)
                if file_labels:
                    selected = st.multiselect("本次使用的资料", list(file_labels),
                                              format_func=lambda path: file_labels[path],
                                              key=sources_key, on_change=_save_draft_value, args=(ws, sources_key))
                else:
                    selected = []
                if (source_mode == "文档资料" and document_parser.get("mode") == "native"
                        and any(Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} for path in selected)):
                    st.warning("图片需要多模态识别，请在输入解析节点选择并确认图片输入模型。")
                upload_error = st.session_state.get(f"workflow-upload-error:{ws}:{source_mode}")
                if upload_error:
                    st.error(_upload_cache_error(upload_error))
                brief = _draft_brief("补充生成要求（可选）",
                                     placeholder="例如：重点覆盖故障诊断、证据引用与清晰的分步回答。",
                                     key=f"workflow-source-brief:{ws}:{source_mode}")
                st.caption("单文件最多 50 MiB，本次来源合计最多 200 MiB。")
        if preview_in_input:
            from lib.presentation.streamlit.document_preview import render_document_preview
            with setup_col, st.container(border=True, key="workbench-source-preview"):
                chunk_chars = _draft_number(
                    "文档分块目标字符数", 200, 20000, 2000,
                    key=f"workflow-chunk-chars:{ws}")
                native_sources = [path for path in selected if Path(path).suffix.lower() in {".md", ".txt", ".pdf", ".docx"}]
                if document_parser.get("mode") == "vision":
                    st.caption("此处展示本地提取文字；多模态识别结果在运行时的输入节点实时展示。")
                render_document_preview(document_preview, ws, native_sources, file_labels, int(chunk_chars))
        if targets and source_mode == "知识库检索":
            from lib.presentation.streamlit.knowledge_page import render_knowledge_preview
            with setup_col, st.container(border=True, key="workbench-knowledge-preview"):
                render_knowledge_preview(ws)
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
                with st.expander("输入范围（可选）" if preview_in_input else "输入范围与文档分块（可选）"):
                    range_col, chunk_col = (st.columns(2, gap="small") if source_mode == "文档资料" and not preview_in_input
                                            else (nullcontext(), nullcontext()))
                    with range_col:
                        maximum = _draft_number("本次最多处理单元", 1, MAX_CANDIDATES, MAX_CANDIDATES, key=f"workflow-max-units:{ws}",
                                              help="限制来源解析后的处理范围。开放需求规划也受此上限约束。")
                    if not preview_in_input:
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
            if generation_invalid:
                st.warning("风格配置尚未完成，请在对应节点填写有效的自定义指令。")
            if trim_invalid:
                st.warning("修剪配置尚未完成，请在修剪节点填写有效的自定义模板。")
            if director_invalid:
                st.warning("请至少保留一种问答类型，并检查指导员规则。")
            if prompts_invalid:
                st.warning("节点提示词尚未完成，请在对应节点填写有效正文或恢复内置提示词。")
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
            if node_generation:
                run_summary += " · " + generation_summary(node_generation, language)
            if reasoning_trim["enabled"]:
                run_summary += " · " + translate("推理链修剪", language)
            if qa_director["enabled"]:
                run_summary += " · " + translate("问答指导员", language)
            if package_review["enabled"]:
                run_summary += " · " + translate("AI 抽检" if package_review["mode"] == "sample" else "AI 全量评审", language)
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
                parser_unavailable = (document_parser.get("mode") == "vision" and
                    (not document_parser.get("binding") or document_parser.get("unconfirmed")))
                parser_unavailable = parser_unavailable or (source_mode == "文档资料" and document_parser.get("mode") == "native"
                    and any(Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} for path in selected))
                submitted = st.button("开始自动生成", type="primary", disabled=not targets or bool(model_issues)
                                      or bool(pricing_issues) or agent_unavailable or web_unavailable
                                      or bool(generation_invalid) or trim_invalid or prompts_invalid or director_invalid
                                      or parser_unavailable or source_mode == "知识库检索" and not selected
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
                    source_names = dict(knowledge_source_names)
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
                                        node_models=model_application.snapshot(graph_nodes, source_mode, bindings,
                                                                               node_generation=node_generation, package_review=package_review),
                                        sample_count=int(sample_count), concurrency=int(concurrency), batch_size=int(batch_size),
                                        max_units=int(maximum), chunk_chars=int(chunk_chars), tasks=int(tasks),
                                        conversation_turns=int(conversation_turns),
                                        agent_replay_mode=agent_mode,
                                        sft_output_style=sft_output_style,
                                        node_generation=node_generation,
                                        qa_director=qa_director,
                                        package_review=package_review,
                                        node_prompts=node_prompts,
                                        reasoning_trim=reasoning_trim,
                                        web_research=web_research,
                                        document_parser=document_parser,
                                        knowledge_retrieval=(st.session_state.get(f"knowledge-result:{ws}", (None, None))[1]
                                                             if source_mode == "知识库检索" else None),
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
