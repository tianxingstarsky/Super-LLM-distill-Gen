"""Compact, workspace-backed review queues shown before choosing a review type."""
from __future__ import annotations

import streamlit as st

from lib.bootstrap.corpus_reviews import corpus_review_application
from lib.bootstrap.preference_reviews import preference_review_application
from lib.bootstrap.sft_reviews import sft_review_application


_STYLE = """<style>
[class*="st-key-review-overview-"] {border-color:#dce8f6!important;
  box-shadow:0 3px 12px rgba(32,81,146,.045)!important}
[class*="st-key-review-overview-"] .df-review-overview {display:grid;grid-template-columns:38px 1fr;
  gap:3px 9px;min-height:75px}
.df-review-overview b {display:grid;grid-row:1/4;place-items:center;width:36px;height:36px;
  border-radius:9px;background:#e7f1ff;color:#1b6fce;font-size:16px}
.df-review-overview[data-kind="dpo"] b,.df-review-overview[data-kind="orpo"] b {
  background:#f0eafb;color:#7955c1}
.df-review-overview[data-kind="cpt"] b {background:#e5f8f0;color:#15916c}
.df-review-overview strong {color:#223852;font-size:13px;line-height:1.35}
.df-review-overview span {color:#1768c8;font-size:20px;font-weight:800;line-height:1.3}
.df-review-overview small {color:#7b8ca2;font-size:11px}
[class*="st-key-review-overview-"] button {font-size:11px;min-height:31px}
</style>"""


def _select_mode(key: str, mode: str) -> None:
    st.session_state[key] = mode


def render_review_overview(output, mode_key: str) -> dict[str, object]:
    """Show candidate counts from verified review queues; return the applications.

    A candidate count is not an unreviewed count or a quality score. Detailed
    decisions remain on the selected queue page.
    """
    applications = {
        "SFT 数据调整": sft_review_application(output),
        "DPO 偏好优化": preference_review_application(output),
        "ORPO 偏好优化": preference_review_application(output, target="orpo"),
        "CPT 语料审核": corpus_review_application(output),
    }
    cards = (
        ("sft", "SFT 数据调整", "✎", "sample_count"),
        ("dpo", "DPO 偏好优化", "♡", "pair_count"),
        ("orpo", "ORPO 偏好优化", "◇", "pair_count"),
        ("cpt", "CPT 语料审核", "▤", "sample_count"),
    )
    st.html(_STYLE)
    for column, (kind, mode, glyph, count_field) in zip(st.columns(4, gap="small"), cards):
        with column, st.container(border=True, key=f"review-overview-{kind}"):
            try:
                runs = applications[mode].reviewable_runs()
                candidates = sum(max(0, int(row.get(count_field, 0))) for row in runs)
                value = str(candidates)
                detail = f"{len(runs)} 个可审任务 · 候选样本"
            except (OSError, ValueError, KeyError, TypeError):
                value, detail = "—", "队列暂不可读取"
            st.html(f'<div class="df-review-overview" data-kind="{kind}"><b>{glyph}</b>'
                    f'<strong>{mode}</strong><span>{value}</span><small>{detail}</small></div>')
            st.button("打开审核队列 →", key=f"review-overview-open-{kind}",
                      on_click=_select_mode, args=(mode_key, mode), width="stretch")
    st.caption("以上是当前工作区可审候选量；通过、退回和待处理状态以具体队列为准。")
    return applications
