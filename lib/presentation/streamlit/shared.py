"""Shared page-level presentation primitives for the DataForge console."""
from __future__ import annotations

import html

import streamlit as st


_HERO_CUBES = '<i><b></b><em></em><span></span></i>' * 3


def page_header(title: str, description: str, tag: str = "DATAFORGE WORKSPACE") -> None:
    """Render the common page masthead used across the primary workspaces."""
    # Keep a semantic Streamlit title for navigation and screen-reader/test support;
    # console_theme hides this framework heading and shows the richer masthead below.
    st.title(title)
    st.html(
        '<section class="df-page-hero">'
        '<div class="df-page-copy">'
        '<span class="df-page-eyebrow">AI DATA WORKSPACE</span>'
        f'<h1>{html.escape(title)}</h1>'
        f'<p>{html.escape(description)}</p>'
        '</div>'
        f'<div class="df-page-tag">{html.escape(tag)}</div>'
        '<div class="df-hero-art" aria-hidden="true">' + _HERO_CUBES + '</div>'
        '</section>'
    )


def section_heading(title: str, subtitle: str = "", icon: str = "") -> None:
    """Render a compact icon-led heading inside a content panel."""
    safe_icon = html.escape(icon)
    safe_title = html.escape(title)
    safe_subtitle = html.escape(subtitle)
    st.html(
        '<div class="df-section-heading">'
        + (f'<span class="df-panel-icon">{safe_icon}</span>' if icon else "")
        + '<span><strong>' + safe_title + '</strong>'
        + (f'<small>{safe_subtitle}</small>' if subtitle else "")
        + '</span></div>'
    )


def review_empty_state(title: str, description: str, target: str) -> None:
    """Explain why a review queue is empty and show how samples reach it."""
    safe_title = html.escape(title)
    safe_description = html.escape(description)
    safe_target = html.escape(target)
    st.html(
        '<section class="df-review-empty" aria-label="审核队列状态">'
        '<div class="df-review-empty-main">'
        '<span class="df-review-empty-icon" aria-hidden="true">✎</span>'
        '<span class="df-review-empty-kicker">REVIEW QUEUE</span>'
        f'<strong>{safe_title}</strong>'
        f'<p>{safe_description}</p>'
        '<div class="df-review-empty-count"><b>0</b><span>待审核样本</span></div>'
        '</div>'
        '<div class="df-review-route">'
        '<span class="df-review-route-title">样本进入审核的流程</span>'
        f'<div><b>01</b><span><strong>选择目标</strong><small>{safe_target}</small></span></div>'
        '<i aria-hidden="true">›</i>'
        '<div><b>02</b><span><strong>自动生成与质检</strong><small>仅合格候选进入队列</small></span></div>'
        '<i aria-hidden="true">›</i>'
        '<div><b>03</b><span><strong>人工复核</strong><small>修订、通过或退回</small></span></div>'
        '</div>'
        '</section>'
    )
