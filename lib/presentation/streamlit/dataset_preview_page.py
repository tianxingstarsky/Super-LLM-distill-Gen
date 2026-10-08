"""Verified workflow-artifact browsing inside the data library."""
from __future__ import annotations

import html
from typing import Any

import streamlit as st

from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_targets import TARGETS
from lib.presentation.streamlit.sample_preview import render_sample_preview
from lib.presentation.streamlit.shared import section_heading
from lib.presentation.streamlit.i18n import UntranslatedText
from lib.presentation.streamlit.review_navigation import review_choices, open_verified_review, open_package


TARGET_LABELS = {
    "cpt": "CPT 预训练语料", "sft": "SFT 指令对话", "dpo": "DPO 偏好对",
    "orpo": "ORPO 偏好对", "rlaif": "RLAIF 反馈排序", "agent": "Agent 工具轨迹",
    "multiturn": "多轮对话", "gsm8k": "基础算术（GSM8K 格式）", "cot": "CoT 可见推理",
}


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _select_sample(key: str, position: int) -> None:
    st.session_state[key] = position


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
    runs = [row for row in application.task_runs()
            if row.get("status") in {"completed", "needs_attention"} and row.get("id")]
    if not runs:
        return False
    by_id = {row["id"]: row for row in runs}
    left, right = st.columns([1, 2.15], gap="large")
    with left, st.container(border=True):
        section_heading("选择工作流产物", "仅预览当前任务中通过完整性校验的真实文件", "▤")
        run_key = f"data-preview-run:{workspace_id}"
        if st.session_state.get(run_key) not in by_id:
            st.session_state.pop(run_key, None)
        run_id = st.selectbox(
            "已完成任务", list(by_id), key=run_key,
            format_func=lambda key: UntranslatedText(f"{by_id[key].get('name', '未命名任务')} · {key[:8]}"),
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
        target_key = f"data-preview-target:{workspace_id}:{run_id}"
        if st.session_state.get(target_key) not in targets:
            st.session_state.pop(target_key, None)
        target = st.selectbox(
            "训练目标", targets, key=target_key,
            format_func=lambda value: "Agent 失败轨迹" if value == "agent_negative"
            else TARGET_LABELS.get(value, value.upper()),
        )
        count = int(((inventory["manifest"].get("negative_counts") or {}).get("agent", 0)
                     if target == "agent_negative"
                     else (inventory["manifest"].get("counts") or {}).get(target, 0)))
        position_key = f"data-preview-position:{workspace_id}:{run_id}:{target}"
        if position_key in st.session_state:
            st.session_state[position_key] = min(count, max(1, int(st.session_state[position_key])))
        position = st.number_input("样本序号", 1, count,
                                   value=None if position_key in st.session_state else 1, key=position_key)
        try:
            rows = application.artifact_preview(run_id, target, limit=1, offset=int(position) - 1)
        except (KeyError, OSError, ValueError, TypeError) as error:
            st.error(f"无法读取已校验样本：{error}")
            return True
        if not rows:
            st.warning("清单记录了合格样本，但当前文件没有可读取的记录。")
            return True
        st.caption(f"本目标共 {count:,} 条 · 当前展示第 {position:,} 条")
        st.html('<div class="df-data-sample-facts">'
                '<div><span>训练目标</span><b>' + _safe(target.upper()) + '</b></div>'
                '<div><span>任务状态</span><b>'
                + ("需检查" if by_id[run_id].get("status") == "needs_attention" else "已完成")
                + '</b></div>'
                '<div><span>文件完整性</span><b>SHA-256 通过</b></div>'
                '<div><span>来源任务</span><b>' + _safe(run_id[:8]) + '</b></div>'
                '</div><p class="df-data-note">预览来自校验后的自动候选；正式训练前仍可进入人工审核。</p>')
        if st.button("查看任务运行过程", key=f"data-preview-workflow:{workspace_id}:{run_id}", width="stretch"):
            st.session_state["workflow-open-run"] = {"workspace": workspace_id, "run_id": run_id}
            st.rerun()
        available_reviews = dict(review_choices(by_id[run_id], inventory["manifest"]))
        if target in available_reviews:
            st.button("进入当前任务的人工审核", on_click=open_verified_review,
                      args=(application, run_id, target), key=f"data-preview-review:{workspace_id}:{run_id}",
                      width="stretch")
        if st.session_state.pop(f"workflow-review-error:{run_id}", False):
            st.error("无法读取或校验任务产物，请检查本次任务文件后重试。")
        st.button("查看并打包本次训练数据", on_click=open_package, args=(run_id, target),
                  key=f"data-preview-package:{workspace_id}:{run_id}", width="stretch")
    with right, st.container(border=True):
        heading, previous, following = st.columns([3, 1, 1], vertical_alignment="center")
        with heading:
            section_heading("真实样本预览", "按训练目标呈现语料、对话、偏好对或工具轨迹", "◉")
        previous.button("上一条样本", disabled=position <= 1, on_click=_select_sample,
                        args=(position_key, int(position) - 1), key=position_key + ":previous", width="stretch")
        following.button("下一条样本", disabled=position >= count, on_click=_select_sample,
                         args=(position_key, int(position) + 1), key=position_key + ":next", width="stretch")
        st.html('<div class="df-data-sample-head"><strong>'
                + _safe("agent.negative.jsonl" if target == "agent_negative" else f"{target}.jsonl")
                + '</strong><span>第 ' + str(position) + ' / ' + str(count) + ' 条</span></div>')
        render_sample_preview(target, rows[0], key=f"artifact-messages:{workspace_id}:{run_id}:{target}:{position}")
    return True
