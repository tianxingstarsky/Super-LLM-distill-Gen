"""Bounded previews of saved stage results, separate from released artifacts."""
from __future__ import annotations

import html

import streamlit as st

from lib.domain.workflow_graph import execution_graph
from lib.domain.workflow_targets import PREFERENCE_TARGETS, training_record
from lib.presentation.streamlit.artifact_preview import render_message_sequence, render_training_sample
from lib.presentation.streamlit.artifact_preview_style import ARTIFACT_PREVIEW_STYLE
from lib.presentation.streamlit.i18n import UntranslatedText, translate
from lib.presentation.streamlit.workflow_preference_details import PREFERENCE_LABELS, preference_stage_label


_STAGE_LABELS = {
    "ingest": "输入解析", "director": "对话指导员", "cpt": "CPT 语料处理",
    "sft": "SFT 生成", "multiturn": "多轮对话", "agent": "Agent 轨迹",
    "preference": "偏好评审", "gsm8k": "模板算术题", "cot": "CoT 推理",
    "trim": "推理链修剪", "review": "评分与修正", "jev": "JEV 评分", "package": "输出打包",
}
_ROW_STATUS = {
    "ready": "可处理输入", "eligible": "节点检查通过", "quarantined": "已隔离",
    "rejected": "已隔离", "pending": "等待处理", "skipped": "已跳过",
}
_PREFERENCE_LABELS = PREFERENCE_LABELS
_DIMENSION_LABELS = {"correctness": "正确性", "reasoning": "推理", "grounding": "依据",
                     "instruction": "指令遵循", "safety": "安全性"}


def _stage_label(key, *, source_cleaning=False, targets=None):
    # Shared preprocessing changes the delivery route, not the CPT stage's
    # identity. Ignore old persisted labels without rewriting run records.
    return preference_stage_label(targets) if key == "preference" and targets is not None else _STAGE_LABELS.get(key, key)


def _sample_target(stage, row, target=None):
    if stage in {"ingest", "cpt"}:
        return "sft" if row.get("messages") else "cpt"
    if stage == "preference":
        return target if target in PREFERENCE_TARGETS else "dpo"
    return row.get("_review_target", row.get("target", "sft")) if stage in {"trim", "review", "jev"} else stage


def _raw_preference_html(row, target="rlaif"):
    """Keep unscored candidates readable without inventing ranks or scores."""
    def conversation(title, value):
        messages = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
        return ('<div class="df-artifact-conversation"><div class="df-artifact-subhead">'
                + title + '</div><div class="bubbles">' + render_message_sequence(messages) + '</div></div>')

    target = target if target in PREFERENCE_TARGETS else "rlaif"
    title = {"dpo": "DPO 偏好候选", "orpo": "ORPO 偏好候选"}.get(target, "原始候选对")
    return (ARTIFACT_PREVIEW_STYLE + '<article class="df-artifact-preview">'
            '<div class="df-artifact-heading"><span class="df-artifact-badge" data-target="'
            + target + '">' + target.upper() + '</span><strong>' + title + '</strong></div>'
            + conversation("共同提示上下文", row.get("prompt"))
            + '<div class="df-artifact-compare"><div class="df-artifact-chosen">'
            + conversation("候选回答 · chosen", row.get("chosen")) + '</div><div class="df-artifact-rejected">'
            + conversation("候选回答 · rejected", row.get("rejected")) + '</div></div></article>')


def _rlaif_html(stage, row, *, awaiting_review=False):
    """Use the export contract to expose only evidence consistent with the pair."""
    native = "responses" in row and not any(key in row for key in ("chosen", "rejected"))
    try:
        if native:
            # Preserve existing native previews only when all displayed fields
            # are explicit. The shared renderer otherwise invents default ranks.
            responses = row["responses"]
            if (row.get("criterion") != "correctness" or not isinstance(responses, list)
                    or len(responses) != 2):
                raise ValueError("incomplete_rlaif_preview")
            for rank, response in enumerate(responses, 1):
                if (not isinstance(response, dict) or type(response.get("preference_rank")) is not int
                        or response["preference_rank"] != rank
                        or not isinstance(response.get("response"), list) or not response["response"]
                        or not isinstance(response.get("feedback"), str) or not response["feedback"].strip()
                        or type(response.get("score")) is not int or not 1 <= response["score"] <= 5
                        or not isinstance(response.get("dimensions"), dict)
                        or any(type(response["dimensions"].get(key)) is not int
                               or not 1 <= response["dimensions"][key] <= 5 for key in _DIMENSION_LABELS)
                        or response["dimensions"]["correctness"] != response["score"]):
                    raise ValueError("incomplete_rlaif_preview")
            if (any(value < 4 for value in responses[0]["dimensions"].values())
                    or responses[0]["score"] - responses[1]["score"] < 2):
                raise ValueError("inconsistent_rlaif_preview")
            payload = row
        else:
            payload = training_record("rlaif", row)
    except (ValueError, TypeError, KeyError, IndexError):
        waiting = (stage == "preference" and awaiting_review and not native
                   and row.get("status") not in {"quarantined", "rejected"}
                   and not any(key in row for key in ("preference", "rlaif")))
        notice = ("RLAIF 候选 · 等待评分与修正。AI 评分、排序与评语将在评分与修正节点生成。" if waiting else
                  "RLAIF 评分证据缺失或不一致，暂时无法展示 AI 反馈；以下保留已保存内容，处理原因见记录说明。")
        return (render_training_sample("unverified_rlaif", row) if native else _raw_preference_html(row)), notice
    dimensions = []
    for response in payload["responses"]:
        facts = ''.join('<span>' + label + ' <b>' + html.escape(str(response["dimensions"][key]))
                        + '/5</b></span>' for key, label in _DIMENSION_LABELS.items())
        dimensions.append('<div class="df-live-dimensions"><strong>'
                          + ("chosen 评分维度" if response["preference_rank"] == 1 else "rejected 评分维度")
                          + '</strong><div>' + facts + '</div></div>')
    return render_training_sample("rlaif", payload) + ''.join(dimensions), ""


def _result_html(stage, row, target=None, *, awaiting_review=False):
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
        sample_target = _sample_target(stage, row, target)
        if sample_target == "rlaif":
            body, notice = _rlaif_html(stage, row, awaiting_review=awaiting_review)
            if notice:
                body = '<p class="df-live-preference-note">' + notice + '</p>' + body
        elif (sample_target in {"dpo", "orpo"} and stage == "preference" and awaiting_review
              and not row.get("preference")):
            body = ('<p class="df-live-preference-note">偏好候选 · 等待评分与修正。'
                    'chosen / rejected 目前仅表示字段位置，尚未确认优劣。</p>'
                    + _raw_preference_html(row, sample_target))
        else:
            body = render_training_sample(sample_target, row)
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
        node_generation=recipe.get("node_generation"),
        reasoning_trim=bool((recipe.get("reasoning_trim") or {}).get("enabled")),
        qa_director=recipe.get("qa_director"), package_review=recipe.get("package_review"),
        review_repair=recipe.get("review_repair"), source_cleaning=source_cleaning,
        recipe_version=recipe.get("version", 0), repair_only=bool(recipe.get("repair_inputs")),
        arithmetic_seed_protocol_version=recipe.get("arithmetic_seed_protocol_version", 0),
        cpt_review_policy_version=recipe.get("cpt_review_policy_version", 0))
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
    .df-live-preference-note{padding:8px 10px;border-radius:8px;background:#f0f7fd;color:#526f8b;font-size:12px}
    .df-live-dimensions{margin-top:8px;padding:9px 11px;border:1px solid #e1ebf5;border-radius:8px;font-size:12px;color:#526f8b}
    .df-live-dimensions>div{display:flex;flex-wrap:wrap;gap:14px;margin-top:6px}
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
    if source_cleaning and "cpt" in scheduled:
        st.caption(translate("本次导出 CPT 语料，合格正文同时供下游节点复用。"
                             if "cpt" in recipe.get("targets", ()) else
                             "本次不导出 CPT，合格正文仅供下游节点复用。", language))
    def count(item, field):
        value = item.get(field, 0)
        return f"≥ {value:,}" if item.get("indexing") else f"{value:,}"
    table = [{"节点": translate(_stage_label(item["stage"], source_cleaning=source_cleaning, targets=recipe.get("targets")), language),
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
    label = lambda key: translate("跟随最新产物" if key == "auto" else _stage_label(key, source_cleaning=source_cleaning, targets=recipe.get("targets")), language)
    st.selectbox("预览节点", options, key=selection_key, format_func=label)
    rows = snapshot.get("rows") or []
    selected_stage = snapshot.get("selected_stage")
    if not rows:
        st.caption("该节点尚无完整落盘样本。")
        return
    preview_target = None
    if selected_stage == "preference":
        targets = [target for target in ("rlaif", "dpo", "orpo") if target in recipe.get("targets", ())]
        if targets:
            target_key = f"workflow-live-artifact-target:{run_id}"
            if st.session_state.get(target_key) not in targets:
                st.session_state[target_key] = targets[0]
            preview_target = st.selectbox("偏好产物视图", targets, key=target_key,
                format_func=lambda target: translate(_PREFERENCE_LABELS[target], language))
    st.caption("仅展示最近 3 条完整记录。节点通过不等于最终交付通过，最终结果以评分和打包报告为准。")
    tabs = st.tabs([UntranslatedText(f"#{str(row.get('id') or index + 1)[:10]}")
                    for index, row in enumerate(reversed(rows))])
    for tab, row in zip(tabs, reversed(rows)):
        with tab:
            st.html(_result_html(selected_stage, row, preview_target,
                                awaiting_review=recipe.get("review_repair") is not None))
