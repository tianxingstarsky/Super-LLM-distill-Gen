"""Mark observed DeepSeek verbosity text in a caller-selected reasoning view.

This profile records literal self-descriptions, not authenticated provider
settings. The caller owns source-model identification and task-reference
ambiguity. Source reasoning stays intact outside the returned in-memory view.
"""
from __future__ import annotations

import re


PROFILE = "deepseek-v4.1-flash-control/v1"
MARKER = "[[SJ_OUTPUT_VERBOSITY:5]]"

# Only these spellings occurred in the retained live records. The final guard
# permits sentence punctuation but rejects numeric and word continuations.
_OBSERVED_TEXT = re.compile(
    r"(?<!\w)(?:Desired oververbosity 5|desired verbosity 5)(?!\w|\.\d)"
)


def normalize_deepseek_control_text(reasoning: str) -> tuple[str, list[dict[str, object]]]:
    """Return marked reasoning and reversible edits at original text offsets.

    Offsets are zero-based Python string indices, with an exclusive ``end``.
    The observed verbosity value is deliberately separate from reasoning
    effort; this adapter neither infers an effort nor changes surrounding text.
    """
    edits: list[dict[str, object]] = []

    def mark(match: re.Match[str]) -> str:
        edits.append({
            "start": match.start(),
            "end": match.end(),
            "original": match.group(),
            "replacement": MARKER,
            "field": "output_verbosity",
            "observed_value": 5,
            "evidence_kind": "observed_self_description",
            "upstream_control_verified": False,
        })
        return MARKER

    return _OBSERVED_TEXT.sub(mark, reasoning), edits
