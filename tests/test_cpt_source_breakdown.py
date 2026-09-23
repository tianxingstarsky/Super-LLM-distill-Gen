"""CPT source retention must reflect final export and protect held-out text."""
from __future__ import annotations

from lib.domain.corpus_quality import summarize_corpus_sources
from lib.presentation.streamlit.workflow_quality_page import _source_breakdown_html


def test_source_retention_separates_equal_names_and_keeps_only_counts():
    rows = [
        {"source_id": "hash-a", "source_name": "manual.txt", "status": "eligible",
         "text": "独立正文一"},
        {"source_id": "hash-a", "source_name": "manual.txt", "status": "duplicate",
         "text": "独立正文一", "reason": "exact_duplicate_corpus"},
        {"source_id": "hash-b", "source_name": "manual.txt", "status": "quarantined",
         "reason": "evaluation_verbatim_overlap", "quarantined_text_chars": 42,
         "quarantined_text_sha256": "d" * 64},
    ]

    sources = summarize_corpus_sources(rows)

    assert len(sources) == 2
    assert sources[0]["candidates"] == 2
    assert sources[0]["exported"] == 1
    assert sources[0]["duplicates"] == 1
    assert sources[0]["retention_rate"] == 0.5
    assert sources[1]["quarantined"] == 1
    assert sources[1]["candidate_chars"] == 42
    assert sources[1]["exported_chars"] == 0
    assert "text" not in sources[1]
    assert "quarantined_text_sha256" not in sources[1]


def test_source_names_are_escaped_in_quality_cards():
    markup = _source_breakdown_html([{"source_name": "<script>alert(1)</script>",
                                      "candidates": 1, "exported": 0,
                                      "duplicates": 0, "quarantined": 1,
                                      "exported_chars": 0,
                                      "reasons": {"evaluation_verbatim_overlap": 1}}])

    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup
    assert "命中评测参照原文" in markup
