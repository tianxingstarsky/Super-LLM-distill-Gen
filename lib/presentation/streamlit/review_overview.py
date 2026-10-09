"""Compact, workspace-backed review queues shown before choosing a review type."""
from __future__ import annotations

import streamlit as st
from typing import Mapping, Protocol


class ReviewQueue(Protocol):
    def reviewable_runs(self) -> list[dict]: ...


_STYLE = """<style>
[data-testid="stVerticalBlock"][class*="st-key-review-overview-"] {border-color:#dce8f6!important; background:#fff!important;
  box-shadow:0 3px 12px rgba(32,81,146,.035)!important; padding:14px!important;
  position:relative; transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease}
[data-testid="stVerticalBlock"][class*="st-key-review-overview-"]:has(.df-review-overview[data-selected="true"]) {
  border-color:#91B8ED!important; background:#F5F9FF!important}
[class*="st-key-review-overview-"] .df-review-overview {display:grid;grid-template-columns:38px 1fr;
  gap:3px 9px;min-height:75px}
.df-review-overview b {display:grid;grid-row:1/4;place-items:center;width:36px;height:36px;
  border-radius:9px;background:#e7f1ff;color:#1b6fce;font-size:16px}
.df-review-overview[data-kind="dpo"] b,.df-review-overview[data-kind="orpo"] b {
  background:#f0eafb;color:#7955c1}
.df-review-overview[data-kind="cpt"] b {background:#e5f8f0;color:#15916c}
.df-review-overview strong {color:#223852;font-size:14px;line-height:1.4}
.df-review-overview span {color:#1768c8;font-size:20px;font-weight:800;line-height:1.3}
.df-review-overview small {color:#596A80;font-size:12px;line-height:1.5}
[class*="st-key-review-overview-"] button {font-size:12px;min-height:34px}
[data-testid="stVerticalBlock"][class*="st-key-review-overview-"]:hover {
  border-color:#A4C3EB!important; box-shadow:0 6px 18px rgba(32,81,146,.07)!important; transform:translateY(-2px)}
[data-testid="stVerticalBlock"][class*="st-key-review-overview-"]:has(button:focus-visible) {
  outline:2px solid #1769E0;outline-offset:3px}
[class*="st-key-review-overview-open-"] {position:static}
[class*="st-key-review-overview-open-"] button::after {content:"";position:absolute;inset:0;border-radius:14px}
[data-testid="stColumn"]:has(.df-review-overview) {display:flex;flex-direction:column}
[data-testid="stColumn"]:has(.df-review-overview) > [data-testid="stVerticalBlock"],
[data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"][class*="st-key-review-overview-"]),
[data-testid="stVerticalBlock"][class*="st-key-review-overview-"] {flex:1}
[data-testid="stVerticalBlock"][class*="st-key-review-overview-"] > [data-testid="stElementContainer"]:last-child,
[data-testid="stVerticalBlock"][class*="st-key-review-overview-"] > [data-testid="stLayoutWrapper"]:last-child {margin-top:auto}
@media(max-width:1100px) {
  [data-testid="stHorizontalBlock"]:has(.df-review-overview) {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
  [data-testid="stHorizontalBlock"]:has(.df-review-overview) > [data-testid="stColumn"] {width:100%;min-width:0;flex:none}}
@media(max-width:700px) {
  [data-testid="stHorizontalBlock"]:has(.df-review-overview) {grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
  [class*="st-key-review-overview-"] .df-review-overview {grid-template-columns:32px minmax(0,1fr);gap:3px 7px}
  .df-review-overview b {width:30px;height:32px}
  .df-review-overview strong {font-size:13px;overflow-wrap:anywhere}
  [class*="st-key-review-overview-"] button {min-height:44px}}
@media(prefers-reduced-motion:reduce) {
  [data-testid="stVerticalBlock"][class*="st-key-review-overview-"] {transition:none;transform:none!important}}
</style>"""


def _select_mode(key: str, mode: str) -> None:
    st.session_state[key] = mode


def render_review_overview(applications: Mapping[str, ReviewQueue], mode_key: str) -> Mapping[str, ReviewQueue]:
    """Show candidate counts from verified review queues; return the applications.

    A candidate count is not an unreviewed count or a quality score. Detailed
    decisions remain on the selected queue page.
    """
    cards = (
        ("sft", "SFT 数据调整", "✎", "sample_count"),
        ("dpo", "DPO 偏好优化", "♡", "pair_count"),
        ("orpo", "ORPO 偏好优化", "◇", "pair_count"),
        ("rlaif", "RLAIF 反馈审核", "✦", "pair_count"),
        ("cpt", "CPT 语料审核", "▤", "sample_count"),
    )
    st.html(_STYLE)
    for column, (kind, mode, glyph, count_field) in zip(st.columns(len(cards), gap="small"), cards):
        with column, st.container(border=True, key=f"review-overview-{kind}"):
            try:
                runs = applications[mode].reviewable_runs()
                candidates = sum(max(0, int(row.get(count_field, 0))) for row in runs)
                value = str(candidates)
                detail = f"{len(runs)} 个可审任务 · 候选样本"
            except (OSError, ValueError, KeyError, TypeError):
                value, detail = "—", "队列暂不可读取"
            selected = st.session_state.get(mode_key) == mode
            st.html(f'<div class="df-review-overview" data-kind="{kind}" data-selected="{str(selected).lower()}"><b>{glyph}</b>'
                    f'<strong>{mode}</strong><span>{value}</span><small>{detail}</small></div>')
            st.button("打开审核队列 →", key=f"review-overview-open-{kind}",
                      on_click=_select_mode, args=(mode_key, mode), width="stretch",
                      type="primary" if selected else "secondary")
    st.caption("以上是当前可审候选量；通过、退回和待处理状态以具体队列为准。")
    return applications
