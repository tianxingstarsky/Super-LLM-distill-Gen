"""Verified per-target quality evidence for completed training workflows."""
from __future__ import annotations

import html
from typing import Any

import streamlit as st

from lib.application.workflow_service import WorkflowApplication
from lib.presentation.streamlit.dataset_preview_page import TARGET_LABELS
from lib.presentation.streamlit.shared import section_heading


QUALITY_STYLE = """<style>
.df-wq-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;margin:14px 0 18px}
.df-wq-summary>div{display:grid;gap:5px;min-height:100px;padding:15px 17px;border:1px solid #dce8f5;
 border-radius:11px;background:linear-gradient(145deg,#fff,#f7fbff)}
.df-wq-summary span{color:#75869c;font-size:11px}.df-wq-summary strong{color:#1d3859;font-size:24px}
.df-wq-summary small{color:#8798aa;font-size:11px}
.df-wq-targets{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;margin:12px 0}
.df-wq-target{padding:16px;border:1px solid #dce8f5;border-radius:11px;background:#fff}
.df-wq-target>div:first-child{display:flex;justify-content:space-between;gap:12px;align-items:center}
.df-wq-target strong{color:#203b5d;font-size:13px}.df-wq-target b{color:#156bcf;font-size:16px}
.df-wq-target small{display:block;margin:4px 0 10px;color:#74869b;font-size:11px}
.df-wq-track{height:6px;overflow:hidden;border-radius:99px;background:#e8f0f9}
.df-wq-track i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#25b59d,#2178df)}
.df-wq-reason{display:flex;justify-content:space-between;gap:14px;padding:7px 0;border-top:1px solid #eef2f7;
 color:#687b93;font-size:11px}.df-wq-reason b{color:#a85e26}
.df-wq-reason code{margin-left:5px;color:#9aa9b9;font-size:10px}
.df-wq-evidence{display:grid;grid-template-columns:1fr auto;gap:12px;padding:9px 0;border-top:1px solid #e9eef5;
 color:#637993;font-size:12px}.df-wq-evidence strong{color:#284767}
.df-wq-note{padding:11px 13px;border-left:3px solid #3988de;border-radius:0 8px 8px 0;background:#f3f8ff;
 color:#61768e;font-size:11px;line-height:1.55}
.df-wq-reference{display:grid;gap:5px;margin:12px 0;padding:12px 14px;border:1px solid #dce8f5;
 border-radius:9px;background:#f7fbff;color:#5c718c;font-size:11px;line-height:1.5}
.df-wq-reference strong{color:#1d4f89;font-size:12px}
.df-wq-sources{display:grid;gap:9px;margin:10px 0 14px}
.df-wq-source{padding:11px 13px;border:1px solid #dce8f5;border-radius:9px;background:#fff}
.df-wq-source.df-wq-zero{border-color:#f0dfd9;background:#fffafa}
.df-wq-source-head{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.df-wq-source-head b{min-width:0;color:#294360;font-size:12px;overflow-wrap:anywhere}
.df-wq-source-head strong{flex:none;color:#1675d2;font-size:12px}
.df-wq-zero .df-wq-source-head strong{color:#bd5c43}
.df-wq-source-meta{display:flex;flex-wrap:wrap;gap:5px 13px;margin:5px 0 8px;color:#778aa0;font-size:11px}
.df-wq-source-reasons{margin-top:7px;color:#8a6b60;font-size:11px;overflow-wrap:anywhere}
@media(max-width:900px){.df-wq-summary,.df-wq-targets{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:550px){.df-wq-summary,.df-wq-targets{grid-template-columns:1fr}}
</style>"""

REASON_LABELS = {
    "empty_text": "空文本", "invalid_encoding": "文本编码异常",
    "potential_secret": "疑似密钥", "potential_personal_data": "疑似个人信息",
    "invalid_json_record": "JSON 记录无效", "oversized_source_block": "来源片段过长",
    "invalid_or_incomplete_conversation": "对话结构不完整",
    "missing_messages": "缺少对话消息", "missing_final_answer": "缺少最终回答",
    "unresolved_tool_error": "未解决的工具错误", "missing_tool_result": "缺少工具返回",
    "orphan_tool_result": "工具返回无法对应调用",
    "multimodal_requires_dedicated_pipeline": "需要多模态专用流程",
    "invalid_generated_corpus": "生成语料不合格", "corpus_judge_rejected": "语料评审未通过",
    "duplicate_training_content": "本批次内容重复",
    "exact_duplicate_corpus": "本批次精确重复", "near_duplicate_corpus": "本批次近重复",
    "released_corpus_exact_duplicate": "与已发布语料精确重复",
    "released_corpus_near_duplicate": "与已发布语料近重复",
    "evaluation_verbatim_overlap": "命中评测参照原文",
    "evaluation_near_overlap": "与评测参照近重复",
    "sft_quality_failed_after_repair": "修订后指令样本仍未通过",
    "multiturn_consistency_rejected": "多轮一致性未通过",
    "recorded_tool_trajectory_required": "缺少真实工具轨迹",
    "invalid_dpo_candidate": "偏好候选结构无效",
    "identical_dpo_answers": "偏好答案相同",
    "insufficient_preference_evidence": "偏好差异证据不足",
    "preference_evidence_missing": "缺少偏好证据",
    "gsm8k_arithmetic_verification_failed": "算术核验失败",
    "cot_reasoning_rejected": "推理解释未通过评审",
}


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _targets_html(quality: dict, manifest: dict) -> str:
    details = quality.get("targets") or {}
    counts = manifest.get("counts") or {}
    cards = []
    for target in dict.fromkeys((*counts, *details)):
        row = details.get(target) or {}
        eligible = int(row.get("eligible", counts.get(target, 0)) or 0)
        total = row.get("total")
        total = int(total) if total is not None else None
        ratio = min(100.0, eligible / total * 100) if total else 0.0
        ratio_text = f"{ratio:.0f}%" if total is not None else "候选数未记录"
        reasons = row.get("reasons") or {}
        if not isinstance(reasons, dict):
            reasons = {}
        reason_rows = ''.join('<div class="df-wq-reason"><span>'
                              + _safe(REASON_LABELS.get(reason, reason))
                              + '<code>' + _safe(reason) + '</code></span><b>'
                              + _safe(value) + '</b></div>'
                              for reason, value in sorted(reasons.items(), key=lambda item: str(item[0])))
        cards.append('<div class="df-wq-target"><div><strong>'
                     + _safe(TARGET_LABELS.get(target, target.upper())) + '</strong><b>'
                     + str(eligible) + ' 条</b></div><small>'
                     + (f"{eligible} / {total} 条候选通过 · {ratio_text}" if total is not None
                        else f"清单记录 {eligible} 条合格样本 · {ratio_text}")
                     + '</small><div class="df-wq-track"><i style="width:' + f"{ratio:.1f}"
                     + '%"></i></div>' + reason_rows + '</div>')
    return '<div class="df-wq-targets">' + ''.join(cards) + '</div>'


def _source_breakdown_html(sources: list[dict]) -> str:
    cards = []
    for row in sources:
        total = max(0, int(row.get("candidates", 0)))
        exported = max(0, int(row.get("exported", 0)))
        duplicates = max(0, int(row.get("duplicates", 0)))
        quarantined = max(0, int(row.get("quarantined", 0)))
        ratio = min(100.0, exported / total * 100) if total else 0.0
        reasons = row.get("reasons") or {}
        if not isinstance(reasons, dict):
            reasons = {}
        top_reasons = sorted(reasons.items(), key=lambda item: (-int(item[1]), str(item[0])))[:2]
        reason_text = " · ".join(f"{REASON_LABELS.get(code, code)} {count}" for code, count in top_reasons)
        cards.append('<div class="df-wq-source' + (' df-wq-zero' if exported == 0 else '')
                     + '"><div class="df-wq-source-head"><b>' + _safe(row.get("source_name", "未知来源"))
                     + '</b><strong>' + f"{exported} / {total} 条 · {ratio:.0f}%"
                     + '</strong></div><div class="df-wq-source-meta"><span>重复 '
                     + str(duplicates) + ' 条</span><span>隔离 ' + str(quarantined)
                     + ' 条</span><span>导出 ' + f"{int(row.get('exported_chars', 0)):,}"
                     + ' 字符</span></div><div class="df-wq-track"><i style="width:'
                     + f"{ratio:.1f}" + '%"></i></div>'
                     + ('<div class="df-wq-source-reasons">主要原因：' + _safe(reason_text)
                        + '</div>' if reason_text else '') + '</div>')
    return '<div class="df-wq-sources">' + ''.join(cards) + '</div>'


def render_workflow_quality(application: WorkflowApplication, workspace_id: str) -> bool:
    """Show a completed run's content checks separately from artifact integrity."""
    runs = [row for row in application.list_runs()
            if row.get("status") in {"completed", "needs_attention"} and row.get("id")]
    if not runs:
        return False
    st.html(QUALITY_STYLE)
    by_id = {row["id"]: row for row in runs}
    run_id = st.selectbox(
        "已完成任务", list(by_id), key=f"workflow-quality-run:{workspace_id}",
        format_func=lambda key: f"{by_id[key].get('name', '未命名任务')} · {key[:8]}",
    )
    try:
        evidence = application.quality_report(run_id)
    except (KeyError, OSError, ValueError, TypeError) as error:
        st.error(f"质量证据校验失败：{error}")
        return True
    manifest = evidence["manifest"]
    quality = evidence["quality"]
    quarantined = quality.get("input_issues") or []
    counts = manifest.get("counts") or {}
    target_details = quality.get("targets") or {}
    total_candidates = sum(int(row.get("total", 0) or 0) for row in target_details.values())
    passed = sum(int(value or 0) for value in counts.values())
    negative = sum(int(value or 0) for value in (manifest.get("negative_counts") or {}).values())
    cards = (
        ("合格目标样本", f"{passed:,}", "逐训练目标计数，非去重人数"),
        ("已记录候选", f"{total_candidates:,}" if target_details else "—", "各目标处理记录合计"),
        ("隔离输入", f"{len(quarantined):,}", "解析阶段保留的处理证据"),
        ("失败轨迹", f"{negative:,}", "单独隔离，未混入正例"),
    )
    st.html('<div class="df-wq-summary">' + ''.join(
        '<div><span>' + _safe(label) + '</span><strong>' + _safe(value)
        + '</strong><small>' + _safe(note) + '</small></div>'
        for label, value, note in cards) + '</div>')
    left, right = st.columns([1.8, 1], gap="large")
    with left, st.container(border=True):
        section_heading("逐目标质量", "合格量来自已校验清单；原因来自本次运行记录", "◉")
        if counts or target_details:
            st.html(_targets_html(quality, manifest))
        else:
            st.info("这次任务没有记录可发布的训练目标。")
        reference = (target_details.get("cpt") or {}).get("decontamination")
        if isinstance(reference, dict):
            if reference.get("status") == "checked":
                description = (f"已核对 {int(reference.get('reference_rows', 0))} 条本次提供的评测参照；"
                               f"原文命中 {int(reference.get('verbatim_overlaps', 0))} 条，"
                               f"近重复 {int(reference.get('near_overlaps', 0))} 条。"
                               "未提供的外部评测集不在检查范围内。")
                title = "CPT 评测集重叠检查 · 已执行"
            else:
                title = "CPT 评测集重叠检查 · 未配置"
                description = "本次未提供评测参照，不能据此推断语料与外部评测集无重叠。"
            st.html('<div class="df-wq-reference"><strong>' + _safe(title) + '</strong><span>'
                    + _safe(description) + '</span></div>')
        source_breakdown = (target_details.get("cpt") or {}).get("source_breakdown") or []
        if isinstance(source_breakdown, list) and source_breakdown:
            section_heading("CPT 来源保留", "按来源查看最终导出、重复和隔离；较低保留率优先显示", "▤")
            ordered_sources = sorted(source_breakdown,
                                     key=lambda item: (int(item.get("exported", 0)) /
                                                       max(1, int(item.get("candidates", 0))),
                                                       str(item.get("source_name", ""))))
            st.html(_source_breakdown_html(ordered_sources[:8]))
            if len(ordered_sources) > 8:
                with st.expander(f"查看其余 {len(ordered_sources) - 8} 个来源"):
                    st.html(_source_breakdown_html(ordered_sources[8:]))
            st.caption("这里只统计进入 CPT 处理阶段的候选；解析阶段隔离的输入另见右侧记录。")
        st.html('<div class="df-wq-note">文件指纹通过，只证明产物与清单一致；内容准确性和适用性仍需按用途审核。</div>')
    with right, st.container(border=True):
        section_heading("完整性与来源", "报告和隔离原因均来自已校验的产物文件", "▤")
        st.progress(1.0, text=f"{len(manifest.get('sha256') or {})} 个产物文件 SHA-256 校验通过")
        st.html('<div class="df-wq-evidence"><span>输入来源</span><strong>'
                + str(len(manifest.get("sources") or [])) + ' 个</strong></div>'
                '<div class="df-wq-evidence"><span>训练目标</span><strong>'
                + str(len(counts)) + ' 类</strong></div>'
                '<div class="df-wq-evidence"><span>任务状态</span><strong>'
                + ("需检查" if by_id[run_id].get("status") == "needs_attention" else "已完成")
                + '</strong></div>')
        if quarantined:
            with st.expander(f"查看隔离输入（{len(quarantined)}）"):
                for row in quarantined[:20]:
                    st.html('<div class="df-wq-evidence"><span>'
                            + _safe(row.get("source", row.get("source_name", "输入单元")))
                            + '</span><strong>' + _safe(row.get("reason", row.get("status", "已隔离")))
                            + '</strong></div>')
                if len(quarantined) > 20:
                    st.caption("仅显示前 20 条；完整记录保留在任务产物中。")
        if st.button("查看任务过程", key=f"workflow-quality-task:{workspace_id}:{run_id}", width="stretch"):
            st.session_state[f"task-center-run:{workspace_id}"] = run_id
            st.session_state["nav"] = "任务管理"
            st.rerun()
        if st.button("查看完整数据包", key=f"workflow-quality-package:{workspace_id}:{run_id}", width="stretch"):
            st.session_state[f"package-run:{workspace_id}"] = run_id
            st.session_state["nav"] = "输出打包"
            st.rerun()
    return True
