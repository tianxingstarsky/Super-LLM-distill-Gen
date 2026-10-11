"""Bounded previews of saved stage results, separate from released artifacts."""
from __future__ import annotations

import html

import streamlit as st

from lib.domain.workflow_graph import execution_graph
from lib.presentation.streamlit.artifact_preview import render_training_sample
from lib.presentation.streamlit.i18n import UntranslatedText, translate


_STAGE_LABELS = {
    "ingest": "输入解析", "director": "对话指导员", "cpt": "CPT 清洗评审",
    "sft": "SFT 生成", "multiturn": "多轮对话", "agent": "Agent 轨迹",
    "preference": "偏好评审", "gsm8k": "算术核验", "cot": "CoT 推理",
    "trim": "推理链修剪", "review": "评分与修正", "jev": "JEV 评分", "package": "输出打包",
}
_ROW_STATUS = {
    "ready": "可处理输入", "eligible": "节点检查通过", "quarantined": "已隔离",
    "rejected": "已隔离", "pending": "等待处理", "skipped": "已跳过",
}


def _stage_label(key, *, source_cleaning=False):
    return "资料清洗" if key == "cpt" and source_cleaning else _STAGE_LABELS.get(key, key)


def _sample_target(stage, row):
    if stage in {"ingest", "cpt"}:
        return "sft" if row.get("messages") else "cpt"
    if stage == "preference":
        return "dpo"
    return row.get("_review_target", row.get("target", "sft")) if stage in {"trim", "review", "jev"} else stage


def _result_html(stage, row):
    """Escape metadata and keep user text in the existing sample renderer."""
    status = _ROW_STATUS.get(row.get("status"), "已保存候选")
    metadata = [f'<span>{html.escape(status)}</span>']
    if row.get("id"):
        metadata.append(f'<code data-user-content>{html.escape(str(row["id"])[:12])}</code>')
    if row.get("location"):
        metadata.append(f'<span data-user-content>{html.escape(str(row["location"]))}</span>')
    if stage in {"ingest", "cpt"} and not row.get("messages"):
        title = "解析后资料" if stage == "ingest" else "清洗后资料"
        text = row.get("text")
        body = (f'<strong class="df-live-source-title">{title}</strong>'
                f'<pre data-user-content>{html.escape(text)}</pre>' if isinstance(text, str) and text
                else '<p class="df-live-source-empty">该记录未保留可预览正文，处理原因见记录说明。</p>')
    else:
        body = render_training_sample(_sample_target(stage, row), row)
    reason = (f'<div class="df-live-result-reason" data-user-content>{html.escape(str(row["reason"]))}</div>'
              if row.get("reason") else "")
    return ('<section class="df-live-result"><div class="df-live-result-meta">'
            + "".join(metadata) + '</div><div class="df-live-result-body">' + body + '</div>' + reason + '</section>')


def render_live_artifacts(application, run_id, *, state, recipe):
    reader = getattr(application, "live_artifacts", None)
    if reader is None:
        return
    selection_key = f"workflow-live-artifact-stage:{run_id}"
    selected = st.session_state.get(selection_key)
    try:
        snapshot = reader(run_id, stage=None if selected in {None, "auto"} else selected, limit=3)
    except (OSError, ValueError, TypeError, KeyError):
        st.caption("暂时无法读取阶段产物，已保存结果仍保留。")
        return
    stages = snapshot.get("stages") or []
    source_cleaning = bool(recipe.get("source_cleaning"))
    scheduled, _ = execution_graph(recipe.get("targets", ()),
        reasoning_trim=bool((recipe.get("reasoning_trim") or {}).get("enabled")),
        qa_director=recipe.get("qa_director"), package_review=recipe.get("package_review"),
        review_repair=recipe.get("review_repair"), source_cleaning=source_cleaning,
        recipe_version=recipe.get("version", 0), repair_only=bool(recipe.get("repair_inputs")))
    stages = [item for item in stages if item["stage"] in scheduled or item.get("written", 0)]
    st.html('''<style>
    .df-live-result{border:1px solid #d9e7f3;border-radius:12px;background:#fff;
      box-shadow:inset 0 1px 0 #fff,0 3px 12px #254c7010;margin:8px 0;overflow:hidden}
    .df-live-result-meta{display:flex;gap:12px;align-items:center;padding:8px 12px;
      background:linear-gradient(110deg,#eef7ff,#f8fbff);font-size:12px;color:#537796}
    .df-live-result-meta code{color:#64849f;font-size:11px}
    .df-live-result-body{padding:5px 12px 12px;max-height:340px;overflow:auto;overscroll-behavior:contain}
    .df-live-source-title{display:block;color:#355b7f;font-size:13px;margin:5px 0 7px}
    .df-live-result-body pre{white-space:pre-wrap;word-break:break-word;font-family:inherit;
      font-size:13px;line-height:1.8;color:#435d76;background:transparent;border:0;padding:0;margin:0}
    .df-live-source-empty{font-size:12px;color:#7890a6;margin:8px 0}
    .df-live-result-reason{padding:7px 12px;background:#fff5ef;color:#9b6643;font-size:12px}
    </style>''')
    running = state.get("status") in {"running", "queued"}
    heading = "实时阶段产物" if running else "已保存阶段产物"
    note = "随完整落盘记录自动更新" if running else "停止或关闭页面后，已保存结果仍可查看"
    st.html('<div class="df-run-section"><div><strong>' + heading + '</strong><small>' + note +
            '</small></div></div>')
    if not stages:
        st.caption("正在等待首条完整结果。节点的 token 输出可在画布中点开查看。")
        return
    language = st.session_state.get("ui_language", "zh")
    def count(item, field):
        value = item.get(field, 0)
        return f"≥ {value:,}" if item.get("indexing") else f"{value:,}"
    table = [{"节点": translate(_stage_label(item["stage"], source_cleaning=source_cleaning), language),
              "处理完成": f"{item.get('processed', 0):,}",
              "已落盘": count(item, "written"), "节点通过": count({**item,
                  "passed": item.get("eligible", 0) + item.get("ready", 0)}, "passed"),
              "评分通过": count(item, "review_accepted"),
              "隔离": count(item, "quarantined")} for item in stages]
    st.dataframe(table, hide_index=True, width="stretch", height="content")
    if any(item.get("indexing") for item in stages):
        st.caption("正在分段读取历史结果，计数随索引进度更新。")
    if state.get("production"):
        st.caption("此处展示当前轮阶段记录；累计产出与轮次见上方交付进度。")
    options = ["auto", *(item["stage"] for item in stages if item.get("written", 0))]
    if selected not in options:
        st.session_state[selection_key] = "auto"
    label = lambda key: translate("跟随最新产物" if key == "auto" else _stage_label(key, source_cleaning=source_cleaning), language)
    st.selectbox("预览节点", options, key=selection_key, format_func=label)
    rows = snapshot.get("rows") or []
    selected_stage = snapshot.get("selected_stage")
    if not rows:
        st.caption("该节点尚无完整落盘样本。")
        return
    st.caption("仅展示最近 3 条完整记录。节点通过不等于最终交付通过，最终结果以评分和打包报告为准。")
    tabs = st.tabs([UntranslatedText(f"#{str(row.get('id') or index + 1)[:10]}")
                    for index, row in enumerate(reversed(rows))])
    for tab, row in zip(tabs, reversed(rows)):
        with tab:
            st.html(_result_html(selected_stage, row))
