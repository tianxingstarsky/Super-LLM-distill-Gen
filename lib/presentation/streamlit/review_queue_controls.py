"""Consistent global status filters and bounded pagination in review pages."""
from __future__ import annotations

import math
import streamlit as st


PAGE_SIZE = 20
_FILTERS = {"全部": None, "待审核": "pending", "已通过": "approved",
            "已退回": "rejected", "已跳过": "skipped"}


def review_queue_controls(prefix: str, counts: dict, total: int, *, widgets=st) -> tuple[int, str | None, int, int]:
    label = widgets.selectbox("处理状态", list(_FILTERS), index=1, key=f"{prefix}:filter")
    decision = _FILTERS[label]
    matched = total if decision is None else counts.get(decision, 0)
    pages = max(1, math.ceil(matched / PAGE_SIZE))
    key = f"{prefix}:page:{decision or 'all'}"
    # Approval removes rows from the pending view. Keep its last page in range.
    current = int(widgets.session_state.get(key, 1))
    clamped = max(1, min(current, pages))
    if current != clamped:
        widgets.session_state[key] = clamped
    page = (widgets.number_input("队列页码", min_value=1, max_value=pages, step=1, key=key)
            if pages > 1 else 1)
    return (int(page) - 1) * PAGE_SIZE, decision, int(page), pages
