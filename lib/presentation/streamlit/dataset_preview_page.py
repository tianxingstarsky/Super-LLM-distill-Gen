"""Verified workflow-artifact browsing inside the data library."""
from __future__ import annotations

import html
from typing import Any

import streamlit as st

from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_targets import TARGETS
from lib.presentation.streamlit.artifact_preview import render_training_sample
from lib.presentation.streamlit.shared import section_heading


TARGET_LABELS = {
    "cpt": "CPT 预训练语料", "sft": "SFT 指令对话", "dpo": "DPO 偏好对",
    "orpo": "ORPO 偏好对", "rlaif": "RLAIF 反馈排序", "agent": "Agent 工具轨迹",
    "multiturn": "多轮对话", "gsm8k": "GSM8K 数学推理", "cot": "CoT 可见推理",
}


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _preview_targets(manifest: dict, files: list[dict]) -> list[str]:
    counts = manifest.get("counts") or {}
    names = {row.get("name") for row in files}
    available = list(TARGETS) + ["agent_negative"]
    return [target for target in available
            if int(((manifest.get("negative_counts") or {}).get("agent", 0)
                    if target == "agent_negative" else counts.get(target, 0))) > 0
            and ("agent.negative.jsonl" if target == "agent_negative" else f"{target}.jsonl") in names]


def render_workflow_samples(application: WorkflowApplication, workspace_id: str) -> bool:
    """Return whether the workspace has a completed candidate to browse."""
    runs = [row for row in application.list_runs()
            if row.get("status") in {"completed", "needs_attention"} and row.get("id")]
    if not runs:
        return False
    by_id = {row["id"]: row for row in runs}
    left, right = st.columns([1, 2.15], gap="large")
    with left, st.container(border=True):
        section_heading("选择工作流产物", "仅预览当前任务中通过完整性校验的真实文件", "▤")
        run_id = st.selectbox(
            "已完成任务", list(by_id), key=f"data-preview-run:{workspace_id}",
            format_func=lambda key: f"{by_id[key].get('name', '未命名任务')} · {key[:8]}",
        )
        try:
            inventory = application.package_inventory(run_id)
            targets = _preview_targets(inventory["manifest"], inventory["files"])
        except (KeyError, OSError, ValueError, TypeError) as error:
            st.error(f"产物校验失败：{error}")
            return True
        if not targets:
            st.info("这次任务没有通过质量检查的原生训练样本；请查看工作流质量报告。")
            return True
        target = st.selectbox(
            "训练目标", targets, key=f"data-preview-target:{workspace_id}:{run_id}",
            format_func=lambda value: "Agent 失败轨迹" if value == "agent_negative"
            else TARGET_LABELS.get(value, value.upper()),
        )
        try:
            rows = application.artifact_preview(run_id, target, limit=50)
        except (KeyError, OSError, ValueError, TypeError) as error:
            st.error(f"无法读取已校验样本：{error}")
            return True
        if not rows:
            st.warning("清单记录了合格样本，但当前文件没有可读取的记录。")
            return True
        position = st.number_input("样本序号", 1, len(rows), 1,
                                   key=f"data-preview-position:{workspace_id}:{run_id}:{target}")
        count = int(((inventory["manifest"].get("negative_counts") or {}).get("agent", len(rows))
                     if target == "agent_negative"
                     else (inventory["manifest"].get("counts") or {}).get(target, len(rows))))
        st.caption(f"本目标共 {count:,} 条 · 当前展示前 {len(rows):,} 条中的第 {position:,} 条")
        st.html('<div class="df-data-sample-facts">'
                '<div><span>训练目标</span><b>' + _safe(target.upper()) + '</b></div>'
                '<div><span>任务状态</span><b>'
                + ("需检查" if by_id[run_id].get("status") == "needs_attention" else "已完成")
                + '</b></div>'
                '<div><span>文件完整性</span><b>SHA-256 通过</b></div>'
                '<div><span>来源任务</span><b>' + _safe(run_id[:8]) + '</b></div>'
                '</div><p class="df-data-note">预览来自校验后的自动候选；正式训练前仍可进入人工审核。</p>')
        if st.button("查看任务运行过程", key=f"data-preview-workflow:{workspace_id}:{run_id}", width="stretch"):
            st.session_state[f"workflow-selected:{workspace_id}"] = run_id
            st.session_state[f"task-center-run:{workspace_id}"] = run_id
            st.session_state["nav"] = "任务管理"
            st.rerun()
    with right, st.container(border=True):
        section_heading("真实样本预览", "按训练目标呈现语料、对话、偏好对或工具轨迹", "◉")
        st.html('<div class="df-data-sample-head"><strong>'
                + _safe("agent.negative.jsonl" if target == "agent_negative" else f"{target}.jsonl")
                + '</strong><span>第 ' + str(position) + ' / ' + str(count) + ' 条</span></div>')
        st.html(render_training_sample(target, rows[position - 1]))
    return True
