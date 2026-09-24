"""Workflow workbench. All durable state lives in the workspace, not the UI."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path
import tempfile

import streamlit as st
from filelock import Timeout

from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_graph import BASE_STAGES, DERIVED_STAGES, execution_graph
from lib.presentation.streamlit.artifact_preview import render_training_sample
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.workflow_run_styles import workflow_run_styles
from lib.presentation.streamlit.workflow_workbench_style import workbench_style
from lib.domain.workflow_targets import TARGETS


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
    "数学推理": ("sft", "gsm8k", "cot"),
}


def _toggle_target_group(key: str, members: frozenset[str], defaults: tuple[str, ...]) -> None:
    """Keep the category card and detailed target picker in one state."""
    current = {target for target in st.session_state.get(key, defaults) if target in TARGETS}
    if members.issubset(current):
        current.difference_update(members)
    else:
        current.update(members)
    st.session_state[key] = [target for target in TARGETS if target in current]
ICONS = {"pending": "○", "queued": "○", "running": "◉", "completed": "✓", "failed": "!", "cancelled": "■", "skipped": "—"}
STAGE_STATUS = {"pending": "待处理", "queued": "待启动", "running": "执行中", "completed": "已完成",
                "failed": "失败", "cancelled": "已停止", "skipped": "已跳过"}


def _stage_configuration(key, recipe, state):
    targets = recipe.get("targets", [])
    if key == "ingest":
        return {"来源": [row.get("name", row.get("file", "未知来源")) for row in recipe.get("sources", [])] or ["开放需求"],
                "最大处理单元": recipe.get("max_units"), "分块目标字符数": recipe.get("chunk_chars"),
                "隐私与结构规则": "疑似密钥、无效结构和超长单元进入隔离"}
    if key in {"cpt", "sft", "multiturn", "agent", "preference", "cot"}:
        details = {"启用目标": [target.upper() for target in targets],
                "生成后端": recipe.get("backend") or "模型配置默认值",
                "生成模型": recipe.get("model") or "模型配置默认值",
                "JEV 后端": recipe.get("jev_backend") or recipe.get("judge_backend") or "JEV 专用配置",
                "JEV 模型": recipe.get("jev_model") or recipe.get("judge_model") or "JEV 专用配置"}
        if key == "cpt":
            details.update({"分块目标字符数": recipe.get("chunk_chars"), "去重": "精确去重与保守近重复检查"})
        elif key == "multiturn":
            details.update({"每段对话轮数": recipe.get("conversation_turns", 3),
                            "质检方式": "逐轮评审 + 全段一致性评审"})
        elif key == "agent":
            details["验证方式"] = "受限 calculator 与录制 JSON 快照重放；其他工具轨迹隔离"
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
    for label, value in configuration.items():
        if isinstance(value, list):
            rendered = "、".join(str(item) for item in value[:5]) or "—"
            if len(value) > 5:
                rendered += f"，另有 {len(value) - 5} 项"
        elif isinstance(value, dict):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = "—" if value is None else str(value)
        rows.append(f"<div><span>{html.escape(str(label))}</span><strong>{html.escape(rendered)}</strong></div>")
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


def _planned_flow_html(targets, nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]) -> str:
    """Summarize the planned nodes without implying an edge between neighbors."""
    if not targets:
        return ('<div class="df-wb-plan df-wb-plan-empty"><strong>运行前流程预览</strong>'
                '<span>选择训练目标后，这里会显示本次的处理阶段与数据依赖。</span></div>')

    stage_markup = []
    for key in nodes:
        intermediate = key == "sft" and "sft" not in targets
        label = GRAPH_LABELS[key] + (" · 中间候选" if intermediate else "")
        stage_markup.append(
            f'<span class="df-wb-plan-node" data-stage="{key}" '
            f'data-intermediate="{str(intermediate).lower()}">'
            f'<i aria-hidden="true">{html.escape(STAGE_GLYPHS[key])}</i>'
            f'<b>{html.escape(label)}</b></span>')
    downstream = [GRAPH_LABELS[destination] for origin, destination in edges
                  if origin == "sft" and destination in {"preference", "cot"}]
    note = (f'<span>SFT 中间步骤会为{html.escape("、".join(downstream))}生成候选；'
            '只有勾选 SFT 才会额外导出 SFT 文件。</span>'
            if "sft" in nodes and "sft" not in targets else '')
    return ('<div class="df-wb-plan">'
            '<div class="df-wb-plan-head"><strong>运行前流程预览</strong>'
            f'<small>{len(nodes)} 个阶段 · {len(edges)} 条数据依赖</small></div>'
            '<div class="df-wb-plan-nodes" aria-label="本次包含的处理阶段">'
            + ''.join(stage_markup) + '</div>'
            '<div class="df-wb-plan-note">本次包含的阶段；节点间的实际连接见下方依赖详情。'
            + note + '</div></div>')


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


def _execution_route_html(targets, stages: dict, selected_stage: str) -> str:
    """Render the persisted dependency graph at the available container width.

    The links describe data lineage, not parallel scheduling.  Every node and
    link comes from ``execution_graph``; the visual state comes from the run.
    """
    nodes, edges = execution_graph(targets)
    base = [key for key in BASE_STAGES if key in nodes]
    derived = [key for key in DERIVED_STAGES if key in nodes]
    height = max(270, 94 + 78 * len(base))
    node_height = 64
    middle = (height - node_height) / 2
    positions = {"ingest": (14, middle, 150),
                 "package": (589, middle, 156)}
    first_base = 57 + (height - 80 - ((len(base) - 1) * 78 + node_height)) / 2
    base_x = 194 if derived else 271
    for index, key in enumerate(base):
        positions[key] = (base_x, first_base + index * 78, 157)
    if derived:
        sft_y = positions["sft"][1]
        first_derived = max(57, min(height - node_height - (len(derived) - 1) * 78 - 16,
                                    sft_y - (len(derived) - 1) * 39))
        for index, key in enumerate(derived):
            positions[key] = (392, first_derived + index * 78, 157)

    lanes = [("来源", 14), ("生成与验证", base_x)]
    if derived:
        lanes.append(("偏好与推理", 392))
    lanes.append(("产物", 589))
    lane_markup = "".join(
        f'<div class="df-dag-lane" style="left:{100*x/760:.3f}%">{label}</div>'
        for label, x in lanes)
    edge_markup: list[str] = []
    for origin, destination in edges:
        x1, y1, w1 = positions[origin]
        x2, y2, _ = positions[destination]
        start_x, end_x = x1 + w1 + 3, x2 - 8
        start_y, end_y = y1 + node_height / 2, y2 + node_height / 2
        source_status = _stage_numbers(stages.get(origin, {}))[0]
        destination_status = _stage_numbers(stages.get(destination, {}))[0]
        if destination_status in {"failed", "cancelled"}:
            edge_status = "attention"
        elif destination_status == "running":
            edge_status = "running"
        elif source_status == destination_status == "completed":
            edge_status = "completed"
        else:
            edge_status = "pending"
        length = math.hypot(end_x - start_x, end_y - start_y)
        angle = math.degrees(math.atan2(end_y - start_y, end_x - start_x))
        edge_markup.append(f'<div class="df-dag-edge" aria-hidden="true" data-from="{origin}" '
                           f'data-to="{destination}" data-status="{edge_status}" '
                           f'style="left:{100*start_x/760:.3f}%;top:{100*start_y/height:.3f}%;'
                           f'width:{100*length/760:.3f}%;transform:rotate({angle:.2f}deg)"></div>')

    node_markup: list[str] = []
    for key in nodes:
        x, y, width = positions[key]
        stage = stages.get(key, {})
        status, done, total, _ = _stage_numbers(stage)
        label = html.escape(GRAPH_LABELS[key])
        glyph = html.escape(STAGE_GLYPHS[key])
        intermediate = key == "sft" and "sft" not in targets
        note = f"{STAGE_STATUS[status]} · {done}/{total} 单元"
        title = html.escape(str(stage.get("label", GRAPH_LABELS[key])) +
                            ("（中间候选）" if intermediate else "") + f"：{note}", quote=True)
        node_markup.append(
            f'<div class="df-dag-node" data-stage="{key}" data-status="{status}" '
            f'data-selected="{str(key == selected_stage).lower()}" '
            f'data-intermediate="{str(intermediate).lower()}" title="{title}" '
            f'style="left:{100*x/760:.3f}%;top:{100*y/height:.3f}%;'
            f'width:{100*width/760:.3f}%;height:{100*node_height/height:.3f}%">'
            f'<span class="df-dag-icon">{glyph}</span><span class="df-dag-copy">'
            f'<strong>{label}</strong><small>{html.escape(note)}</small></span>'
            '<i class="df-dag-status" aria-hidden="true"></i></div>')

    accessible = html.escape("、".join(GRAPH_LABELS[key] for key in nodes), quote=True)
    return ('<div class="df-dag-wrap">'
            f'<div class="df-dag-canvas" role="img" aria-label="本次数据依赖流程：{accessible}" '
            f'style="aspect-ratio:760/{height}">' + lane_markup + "".join(edge_markup) + "".join(node_markup) + '</div>'
            '<div class="df-dag-caption"><span>连线表示本次目标的真实数据依赖；步骤按顺序执行。</span>'
            '<span class="df-dag-legend"><i data-status="completed"></i>完成'
            '<i data-status="running"></i>运行中<i data-status="attention"></i>需处理'
            '<i data-status="pending"></i>等待</span></div></div>')


def _select_stage(selection_key, stage):
    st.session_state[selection_key] = stage


@st.fragment(run_every=2)
def render_run(application, run_id, begin, *, embedded=False):
    st.html(workflow_run_styles())
    state = application.state(run_id)
    recipe = application.recipe(run_id)
    active = application.is_active(run_id)
    st.subheader(state["name"])
    updated = str(state.get("updated_at") or "")[:19].replace("T", " ") or "—"
    st.html('<div class="df-run-meta">'
            f'<span>任务编号 <b>{html.escape(run_id[:8])}</b></span><i>·</i>'
            f'<span>第 {int(state.get("attempt", 0))} 次运行</span><i>·</i>'
            f'<span>更新于 {html.escape(updated)} UTC</span></div>')
    status = state["status"]
    stage_keys = list(state["stages"])
    graph_nodes, _ = execution_graph(recipe["targets"])
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
            f'<span class="df-run-pill">运行尝试 <strong>{int(state.get("attempt", 0))}</strong></span>'
            '</div>')
    if status == "running" and not active:
        st.warning("执行进程已中断。可从已完成的逐条断点继续。")
    elif status == "failed":
        st.error(f"运行失败：{state.get('error', 'unknown')}。已完成的步骤与模型响应已保存。")
    elif status == "completed":
        st.success("所选目标已完成，训练文件与质量报告已生成。")
    elif status == "needs_attention":
        st.warning("运行已结束；有目标没有合格样本，或输入超过本次处理上限。查看下方质量报告。")
    else:
        st.info(LABELS.get(status, status))
    if active:
        if st.button("停止后续步骤", key=f"stop:{run_id}"):
            application.cancel(run_id)
            st.info("已请求停止；当前模型请求返回后，在下一断点停止。")
    elif status in {"queued", "running", "failed", "cancelled"}:
        if st.button("继续执行 / 从断点重试", type="primary", key=f"resume:{run_id}"):
            begin(["workflow", "--action", "resume", "--run-id", run_id])
    if embedded:
        flow, inspector = st.container(), st.container()
    else:
        flow, inspector = st.columns([2.25, 1], gap="large")
    with flow:
        with st.container(border=True):
            st.html('<div class="df-run-section"><div><strong>工作流运行图</strong>'
                    '<small>点击节点卡，检查该步骤的状态、配置与日志。</small></div>'
                    '<span class="df-run-section-tag">实时进度</span></div>')
            st.html(_execution_route_html(recipe["targets"], state["stages"], selected_stage))
            for row_start in range(0, len(stage_keys), 3):
                columns = st.columns(3, gap="small")
                for column, key in zip(columns, stage_keys[row_start:row_start + 3]):
                    stage = state["stages"][key]
                    stage_status, done, total, percent = _stage_numbers(stage)
                    if key not in active_stages:
                        stage_status, done, total, percent = "skipped", 0, 0, 0
                    label = str(stage.get("label", key))
                    display_status = STAGE_STATUS.get(stage_status, stage_status)
                    style_state = "selected" if key == selected_stage else stage_status
                    with column:
                        with st.container(key=f"flow_node_{style_state}_{key}_{run_id[:8]}"):
                            st.button(f"{STAGE_GLYPHS.get(key, '◈')}  **{label}**  \n"
                                      f"{display_status} · {percent}%  \n"
                                      f"{done} / {total} 单元",
                                      key=f"flow-node:{run_id}:{key}", use_container_width=True,
                                      on_click=_select_stage, args=(selection_key, key))
    selected_metrics = state["stages"][selected_stage]
    selected_status, done, total, percent = _stage_numbers(selected_metrics)
    if selected_stage not in active_stages:
        selected_status, done, total, percent = "skipped", 0, 0, 0
    selected_label = str(selected_metrics.get("label", selected_stage))
    if selected_stage == "ingest":
        passed = int(state.get("input_summary", {}).get("ready", 0) or 0)
        quarantined = int(state.get("input_summary", {}).get("quarantined", 0) or 0)
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
                    f'<small>{html.escape(STAGE_STATUS.get(selected_status, selected_status))} · {percent}%</small>'
                    '</div></div>')
            st.progress(percent / 100, text=f"{done} / {total} 单元")
            st.html('<div class="df-run-stat-grid">'
                    f'<div class="df-run-stat"><b>{max(0, passed)}</b><span>{passed_label}</span></div>'
                    f'<div class="df-run-stat"><b>{max(0, quarantined)}</b><span>{quarantined_label}</span></div>'
                    '</div>')
            st.html(_config_html(_stage_configuration(selected_stage, recipe, state)))
            if selected_metrics.get("error"):
                st.error(f"节点错误：{selected_metrics['error']}")
    selected_events = [event for event in state.get("events", []) if event.get("stage") == selected_stage]
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
                st.download_button("下载本次训练数据与质量证据 ZIP", application.bundle(run_id),
                                   file_name=f"training-{run_id[:8]}.zip", mime="application/zip", key=f"zip:{run_id}")
                for target in state["targets"]:
                    with st.expander(f"{TARGET_LABELS.get(target, target.upper())} · 样本预览"):
                        try:
                            preview = application.artifact_preview(run_id, target, 3)
                        except (OSError, ValueError, KeyError):
                            st.error("无法验证或读取该目标的训练文件，请在任务产物中检查完整性。")
                            continue
                        if not preview:
                            st.caption("该目标目前没有通过质量检查的样本。请查看上方隔离原因。")
                        for row in preview:
                            st.html(render_training_sample(target, row))
                negative_count = int(quality.get("targets", {}).get("agent", {}).get("negative", 0))
                if "agent" in state["targets"] and negative_count:
                    with st.expander(f"Agent 失败轨迹 · {negative_count} 条"):
                        st.caption("以下轨迹保留了实际执行失败证据，单独存放，不会混入通过验证的训练样本。")
                        try:
                            negatives = application.artifact_preview(run_id, "agent_negative", 3)
                        except (OSError, ValueError, KeyError):
                            st.error("无法验证或读取失败轨迹文件，请在任务产物中检查完整性。")
                        else:
                            for row in negatives:
                                st.html(render_training_sample("agent_negative", row))
            with st.expander("产物保存位置"):
                st.code(application.artifact_location(run_id))
        else:
            st.caption("质量汇总将在生成与验证步骤结束后出现。进度会自动刷新。")
        rejected = [{"ID": u["id"], "定位": u.get("location", ""), "原因": u.get("reason")}
                    for u in application.quarantined_inputs(run_id)]
        if rejected:
            with st.expander(f"输入隔离记录 · {len(rejected)}"):
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
        st.html(_config_html({"来源文件": [source.get("name", source.get("file", ""))
                                                for source in recipe.get("sources", [])] or ["开放需求"],
                              "开放需求": (recipe.get("brief", "")[:180] + "…" if len(recipe.get("brief", "")) > 180
                                         else recipe.get("brief") or "未填写"),
                              "训练目标": [TARGET_LABELS.get(target, target.upper()) for target in recipe.get("targets", [])],
                              "任务数": recipe.get("tasks"), "处理上限": recipe.get("max_units")}))
        with st.expander("技术详情：完整配方 JSON"):
            st.json(recipe)


def render_workbench(application: WorkflowApplication, begin):
    page_header("数据生成工作台", "上传文档、导入 Agent 上下文，或描述开放需求；系统会自动生成、质检并进入审核。", "DOCS　·　AGENT　·　OPEN BRIEF")
    st.html(workbench_style(st.session_state.get("ui_language", "zh")))
    st.html(
        '<div class="df-wizard-steps">'
        '<div class="df-wizard-step active"><b>1</b><span><strong>添加来源</strong><small>文档、对话或需求</small></span></div>'
        '<i></i><div class="df-wizard-step active"><b>2</b><span><strong>选择目标</strong><small>语料、对话、轨迹或偏好</small></span></div>'
        '<i></i><div class="df-wizard-step"><b>3</b><span><strong>自动生成</strong><small>解析、生成与质检</small></span></div>'
        '<i></i><div class="df-wizard-step"><b>4</b><span><strong>审核导出</strong><small>人工复核后发布</small></span></div>'
        '</div>'
    )
    ws = st.session_state["ws"]
    preset = st.segmented_control(
        "快捷方案", tuple(PRESETS), default="自动推荐", key=f"workflow-preset:{ws}",
        help="选择常用目标组合。下面仍可逐项增删训练目标。",
    ) or "自动推荐"
    st.html('<div class="df-wb-preset-help">快捷方案会预填下方目标；每个目标仍可单独增减。</div>')
    source_mode = st.segmented_control(
        "选择来源类型", ("文档资料", "Agent 上下文", "开放需求"), default="文档资料",
        key="workflow-source-mode",
        help="按来源选择合适的输入；文档或 Agent 记录还可以附加生成要求。",
    ) or "文档资料"
    source_extensions = ({".pdf", ".docx", ".txt", ".md"} if source_mode == "文档资料"
                         else {".json", ".jsonl"} if source_mode == "Agent 上下文" else set())
    files = application.source_files(ws, suffixes=frozenset(source_extensions), limit=501) if source_extensions else []
    file_labels = {row["path"]: row["label"] for row in files}
    with st.container(border=True, key="workbench-targets"):
        section_heading("选择训练目标", "点击分类卡快速启用或清空整组，下方可逐项调整。", "◈")
        target_key = f"workflow-targets:{ws}:{preset}"
        target_defaults = [target for target in PRESETS[preset] if target in TARGETS]
        targets = [target for target in st.session_state.get(target_key, target_defaults) if target in TARGETS]
        target_groups = (
            ("cpt", "预训练语料", "文档清洗、分块与去重", frozenset({"cpt"})),
            ("dialogue", "对话与轨迹", "SFT、多轮与 Agent 轨迹", frozenset({"sft", "multiturn", "agent"})),
            ("preference", "偏好对齐", "ORPO、DPO 与 RLAIF", frozenset({"orpo", "dpo", "rlaif"})),
            ("reasoning", "推理与数学", "CoT 与算术核验", frozenset({"cot", "gsm8k"})),
        )
        for column, (slug, title, description, members) in zip(st.columns(4, gap="small"), target_groups):
            count = len(members.intersection(targets))
            with column:
                st.button(
                    f"{title} · {count} 项已选" if count else f"{title} · 未选",
                    key=f"workbench-target-card-{slug}-{'on' if count else 'off'}",
                    help=f"{description}。点击{'清空' if members.issubset(set(targets)) else '启用'}整组；下方可逐项调整。",
                    on_click=_toggle_target_group,
                    args=(target_key, members, tuple(target_defaults)),
                    use_container_width=True,
                )
        targets = st.multiselect(
            "训练目标", list(TARGETS), default=target_defaults,
            format_func=lambda target: TARGET_LABELS.get(target, target.upper()),
            key=target_key,
            help="可以同时选择多类目标；系统只会导出通过对应质量检查的样本。",
        )
        selected_labels = ''.join(f'<span>{html.escape(TARGET_LABELS.get(target, target.upper()))}</span>'
                                  for target in targets)
        st.html('<div class="df-wb-selected"><b>已选目标 ' + str(len(targets)) + '</b><div>' +
                (selected_labels or '<small>请从上方列表至少选择一类训练目标。</small>') + '</div></div>')
        graph_nodes, graph_edges = execution_graph(targets)
        st.html(_planned_flow_html(targets, graph_nodes, graph_edges))
        if targets:
            with st.expander(f"查看完整数据依赖 · {len(graph_edges)} 条"):
                st.html(_planned_dependencies_html(graph_edges))
        if "agent" in targets:
            st.caption("Agent 正例需要完整的已记录工具轨迹；当前仅能重放受限整数 calculator。")
        if "multiturn" in targets:
            st.caption("多轮目标逐轮及整段评审；合成内容会标记证据等级。")
    with st.container(key=f"workbench-create:{ws}"):
        source_col, setup_col = st.columns([1.12, 1], gap="large")
        with source_col, st.container(border=True, key="workbench-source-panel"):
            section_heading("添加来源", f"本次来源类型：{source_mode}", "▤")
            if source_mode == "开放需求":
                uploaded, selected = [], []
                st.caption("描述任务、领域和使用场景，系统会规划并生成候选。")
                brief = st.text_area("开放性需求", placeholder="例如：为设备维护助手生成中文训练数据，覆盖故障诊断、多轮追问与操作解释。")
            else:
                if source_mode == "Agent 上下文":
                    st.caption("导入完整的 JSON / JSONL 对话记录；工具轨迹需要真实观测。")
                else:
                    st.caption("上传 PDF、DOCX、TXT 或 Markdown；解析时保留来源位置。")
                source_upload, source_library = st.columns(2, gap="small")
                with source_upload:
                    uploaded = st.file_uploader("上传文档 / 上下文记录", type=sorted(e[1:] for e in source_extensions),
                                                accept_multiple_files=True, max_upload_size=50,
                                                key=f"workflow-upload:{ws}:{source_mode}")
                with source_library:
                    selected = st.multiselect("或选择当前文件夹内的来源", list(file_labels),
                                              format_func=lambda path: file_labels[path],
                                              key=f"workflow-sources:{ws}:{source_mode}")
                brief = st.text_area("补充生成要求（可选）", placeholder="例如：重点覆盖故障诊断、证据引用与清晰的分步回答。",
                                     key=f"workflow-source-brief:{ws}:{source_mode}")
                st.caption("单文件最多 50 MiB，本次来源合计最多 200 MiB。")
        with setup_col, st.container(border=True, key="workbench-parameters-panel"):
            section_heading("生成参数设置", "设置运行名称与本次处理范围", "⚙")
            default_run_name = ("Automatic data generation"
                                if st.session_state.get("ui_language") == "en" else "自动数据生成")
            name = st.text_input("运行名称", value=default_run_name)
            a, b = st.columns(2, gap="small")
            maximum = a.number_input("本次最多处理单元", 1, 10000, 100)
            chunk_chars = (b.number_input("文档分块目标字符数", 200, 20000, 2000)
                           if source_mode == "文档资料" else 2000)
            tasks = (b.number_input("开放需求任务数", 1, 100, 10)
                     if source_mode == "开放需求" else 10)
            conversation_turns = (st.number_input("每段对话轮数", 2, 8, 3,
                                                  help="仅用于新生成的多轮对话；导入的完整对话保持原有轮次。")
                                  if "multiturn" in targets else 3)
            evaluation_uploads = []
            if "cpt" in targets:
                with st.expander("预训练评测集去污染（可选）"):
                    st.caption("上传自备 JSON / JSONL 评测参照，每条记录格式为 {\"text\": \"...\"}。"
                               "只在本机对 CPT 候选查重，不作为训练来源，也不发送给模型；未上传时报告会标记未配置。")
                    evaluation_uploads = st.file_uploader(
                        "上传评测集参照", type=["json", "jsonl"], accept_multiple_files=True,
                        max_upload_size=5, key=f"workflow-evaluations:{ws}")
            with st.expander("高级设置：模型与评审器"):
                left, right = st.columns(2)
                with left:
                    backend = st.text_input("生成后端（留空使用模型配置）")
                    model = st.text_input("生成模型（留空使用模型配置）")
                with right:
                    jev_backend = st.text_input("JEV 打分后端（留空使用专用槽位）")
                    jev_model = st.text_input("JEV 打分模型（留空使用专用槽位）")
                st.caption("生成模型与 JEV 评审器可以分开配置；留空时使用系统模型配置。")
        with st.container(border=True, key="workbench-submit"):
            summary_col, action_col = st.columns([3, 1], vertical_alignment="center", gap="large")
            with summary_col:
                st.html('<div class="df-wb-submit-summary"><b>运行配置摘要</b><strong>' +
                        html.escape(name.strip() or "未命名任务") + '</strong><span>' +
                        html.escape(source_mode) + ' · 已选 ' + str(len(targets)) +
                        ' 类目标 · 最多处理 ' + str(int(maximum)) + ' 单元' +
                        (f' · 评测参照 {len(evaluation_uploads)} 份' if evaluation_uploads else '') +
                        '</span></div>')
                st.caption("先解析来源，再生成所选目标并执行质检；结束后可进入人工审核或输出打包。")
            with action_col:
                submitted = st.button("开始自动生成", type="primary", disabled=not targets,
                                      key=f"workflow-create:{ws}", width="stretch")
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
                                        backend=backend or None, model=model or None,
                                        jev_backend=jev_backend or None, jev_model=jev_model or None,
                                        max_units=int(maximum), chunk_chars=int(chunk_chars), tasks=int(tasks),
                                        conversation_turns=int(conversation_turns),
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
                st.error(str(error))
    runs = application.list_runs()
    if not runs:
        st.info("尚无运行记录。创建工作流后，这里会显示实时阶段、质量统计与产物。")
        return
    lookup = {r["id"]: r for r in runs}
    selected_id = st.selectbox("运行记录", list(lookup), key=f"workflow-selected:{ws}",
                              format_func=lambda rid: f"{lookup[rid]['name']} · {LABELS.get(lookup[rid]['status'], lookup[rid]['status'])} · {rid[:8]}")
    render_run(application, selected_id, begin)
