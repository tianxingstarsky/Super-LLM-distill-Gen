"""Literal DeepSeek control views preserve the full source and provenance."""
from __future__ import annotations

import pytest

from lib.contexts.datasets.infrastructure.deepseek_control_text import (
    MARKER,
    PROFILE,
    normalize_deepseek_control_text,
)


# Short, verbatim excerpts from response-body.json reasoning_content. Keep the
# regression portable: local paid-call archives are not a CI dependency.
_LIVE_EXCERPTS = [
    pytest.param(
        'We can give concrete minimal Python pseudo-code? Desired oververbosity 5. '
        'We can include a minimal architecture and checklist.\n\n'
        'We can include small pseudo-code maybe 15 lines. Desired oververbosity 5. '
        'Need keep not too long. A bullet checklist is good.',
        ["Desired oververbosity 5", "Desired oververbosity 5"],
        id="reasoning-marker-live-20261101-call-01",
    ),
    pytest.param(
        'Need maybe deliver concise. Desired oververbosity 5. '
        'Need mention "do not use benchmark claims". Good.\n\n'
        'It would increase length. Desired oververbosity 5. Maybe not.',
        ["Desired oververbosity 5", "Desired oververbosity 5"],
        id="reasoning-marker-live-20261101-call-02",
    ),
    pytest.param(
        'But final should deliver. Desired oververbosity 5. Need be clear.\n\n'
        "Let's craft final in desired verbosity 5. Use headings. "
        'Ensure answer is self-contained.',
        ["Desired oververbosity 5", "desired verbosity 5"],
        id="clean-context-live-20261101-call-02",
    ),
]


@pytest.mark.parametrize(("reasoning", "observed_phrases"), _LIVE_EXCERPTS)
def test_live_excerpts_are_marked_with_reversible_source_offsets(reasoning, observed_phrases):
    marked, edits = normalize_deepseek_control_text(reasoning)

    assert PROFILE == "deepseek-v4.1-flash-control/v1"
    assert len(edits) == marked.count(MARKER) == 2
    assert [edit["original"] for edit in edits] == observed_phrases
    restored = []
    source_cursor = marked_cursor = 0
    for edit in edits:
        start, end = edit["start"], edit["end"]
        assert reasoning[start:end] == edit["original"]
        assert edit["field"] == "output_verbosity"
        assert edit["observed_value"] == 5
        assert edit["evidence_kind"] == "observed_self_description"
        assert edit["upstream_control_verified"] is False
        unchanged_length = start - source_cursor
        assert marked[marked_cursor:marked_cursor + unchanged_length] == reasoning[source_cursor:start]
        restored.append(marked[marked_cursor:marked_cursor + unchanged_length])
        marked_cursor += unchanged_length
        assert marked[marked_cursor:marked_cursor + len(MARKER)] == edit["replacement"] == MARKER
        restored.append(edit["original"])
        marked_cursor += len(MARKER)
        source_cursor = end
    restored.append(marked[marked_cursor:])
    assert "".join(restored) == reasoning
    assert normalize_deepseek_control_text(marked) == (marked, [])


@pytest.mark.parametrize("unmatched", [
    "Desired oververbosity 50",
    "Desired oververbosity 5.0",
    "desired verbosity 5e3",
    "xDesired oververbosity 5",
    "_desired verbosity 5",
    "Desired oververbosity 5fold",
    "desired verbosity 5_setting",
    "Desired verbosity 5",
    "desired oververbosity 5",
    "DESIRED OVERVERBOSITY 5",
])
def test_other_values_spellings_and_word_extensions_are_untouched(unmatched):
    assert normalize_deepseek_control_text(unmatched) == (unmatched, [])


def test_unicode_source_offsets_punctuation_and_existing_markers_are_preserved():
    original = f"原文🌱\n({MARKER}) Desired oververbosity 5.\n[desired verbosity 5]：继续。"
    marked, edits = normalize_deepseek_control_text(original)
    assert marked == f"原文🌱\n({MARKER}) {MARKER}.\n[{MARKER}]：继续。"
    assert len(edits) == 2
    assert edits[0]["start"] == original.index("Desired oververbosity 5")
    assert edits[1]["start"] == original.index("desired verbosity 5")
    assert normalize_deepseek_control_text(marked) == (marked, [])


def test_empty_reasoning_is_untouched():
    assert normalize_deepseek_control_text("") == ("", [])
