"""Mark observed DeepSeek verbosity text in a caller-selected reasoning view.

This profile records literal self-descriptions, not authenticated provider
settings. The caller owns source-model identification and task-reference
ambiguity. Source reasoning stays intact outside the returned in-memory view.
"""
from __future__ import annotations

from .control_text import ControlTextAdapter


PROFILE = "deepseek-v4.1-flash-control/v1"
MARKER = "[[SJ_OUTPUT_VERBOSITY:5]]"

# Freeze the legacy profile; editable configurations use the same matcher.
_PROFILE_ADAPTER = ControlTextAdapter({
    "schema": "shujian.control-text-adaptation/v1",
    "enabled": True,
    "rules": [{
        "id": PROFILE,
        "matches": ["Desired oververbosity 5", "desired verbosity 5"],
        "marker": MARKER,
        "replacement": None,
        "field": "output_verbosity",
        "observed_value": 5,
    }],
})


def normalize_deepseek_control_text(reasoning: str) -> tuple[str, list[dict[str, object]]]:
    """Return marked reasoning and reversible edits at original text offsets.

    Offsets are zero-based Python string indices, with an exclusive ``end``.
    The observed verbosity value is deliberately separate from reasoning
    effort; this adapter neither infers an effort nor changes surrounding text.
    """
    marked, edits = _PROFILE_ADAPTER.mark(reasoning)
    # Keep the existing v1 receipt shape for retained probes and old callers.
    return marked, [{key: value for key, value in edit.items() if key != "rule_id"}
                    for edit in edits]
