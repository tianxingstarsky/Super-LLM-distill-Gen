"""PDF formatting tolerance must retain exact, auditable original source spans."""
from __future__ import annotations

from copy import deepcopy
import pytest

from lib.domain.source_quote import quote_spans
from lib.infrastructure.training_workflow import Workflow


@pytest.mark.parametrize(("source", "quote"), [
    ("before\nThe result is\n  39% across six tasks.\nafter", "The result is 39% across six tasks."),
    ("Before: a minor loss in apti tude and a significant increase in un reliability.",
     "a minor loss in aptitude and a significant increase in unreliability."),
    # Actual extraction pattern from arXiv:2505.06120, page 1, abbreviated.
    ("Analysis of 200,000+ simulated conversations decomposes the performance degradation into two\n"
     "components: a minor loss in aptitude and a significant increase in unreliability.",
     "Analysis of 200,000+ simulated conversations decomposes the performance degradation into two "
     "components: a minor loss in aptitude and a significant increase in unreliability."),
])
def test_pdf_whitespace_matches_and_preserves_exact_original_span(source, quote):
    original = [quote]
    spans = quote_spans(source, original, pdf_whitespace=True)
    assert spans is not None
    assert spans[0]["match"] == "pdf_whitespace"
    assert source[spans[0]["start"]:spans[0]["end"]] == spans[0]["source_quote"]
    assert spans[0]["source_quote"] != quote
    assert original == [quote]
    assert quote_spans(source, original) is None


@pytest.mark.parametrize("quote", [
    "The model did not recover and scored 38%.",  # changed number
    "The model did recover and scored 39%.",  # deleted negation
    "The model did not recover and scored 39% reliably.",  # added word
    "The Model did not recover and scored 39%.",  # changed case
    "The model did not recover and scored 39%!",  # changed punctuation
    "The model did not recover and scored 39%. Elsewhere it succeeded.",  # another unit
])
def test_pdf_quotes_cannot_change_or_extend_source_characters(quote):
    source = "The model did not\n recover and scored 39%."
    assert quote_spans(source, [quote], pdf_whitespace=True) is None


@pytest.mark.parametrize("quotes", [[], None, [""], [" \n"], [None], "The result"])
def test_missing_or_invalid_quotes_are_rejected(quotes):
    assert quote_spans("The result is 39%.", quotes, pdf_whitespace=True) is None


def test_exact_unicode_quote_offsets_use_the_original_source():
    source = "前言：结果为39%。\n结语。"
    spans = quote_spans(source, ["结果为39%。"], pdf_whitespace=True)
    assert spans == [{"quote_index": 0, "start": 3, "end": 10, "offset_unit": "unicode_character",
                      "source_quote": "结果为39%。", "match": "exact"}]


def workflow_for_quote(quote, *, pdf=True, source_id="source-digest"):
    workflow = object.__new__(Workflow)
    workflow.recipe = {"brief": "", "sources": [{"file": "0000.pdf" if pdf else "0000.txt",
                       "sha256": source_id, "name": "Research paper"}]}
    workflow.generation_style = lambda *args: None
    candidate = {"question": "What happened?", "answer": "The model did not recover.",
                 "reasoning": "The source reports that result.", "quotes": [quote] if quote is not None else []}
    workflow.ask = lambda *args, **kwargs: deepcopy(candidate)
    workflow.qa_metadata = lambda unit: {}
    calls = []
    def judge(*args, **kwargs):
        calls.append(args)
        return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
                "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "Grounded in the source."}
    workflow.judge_answer = judge
    unit = {"id": "unit", "source_id": "source-digest", "source_name": "Research paper",
            "kind": "document", "text": "The model did not\n recover and scored 39%."}
    return workflow, unit, calls


def test_real_pdf_provenance_allows_review_and_preserves_model_quote_and_source_span():
    quote = "The model did not recover and scored 39%."
    workflow, unit, calls = workflow_for_quote(quote)
    row = workflow.sft(unit)[0]
    assert row["status"] == "eligible"
    assert len(calls) == 1
    assert row["quotes"] == [quote]
    span = row["quote_spans"][0]
    assert row["source_context"]["text"][span["start"]:span["end"]] == span["source_quote"]
    assert span["source_quote"] == "The model did not\n recover and scored 39%."


@pytest.mark.parametrize("options", [{"pdf": False}, {"source_id": "other-source-digest"}])
def test_text_or_another_source_cannot_claim_pdf_whitespace_tolerance(options):
    workflow, unit, calls = workflow_for_quote("The model did not recover and scored 39%.", **options)
    row = workflow.sft(unit)[0]
    assert row["status"] == "quarantined"
    assert row["reason"] == "sft_quality_failed_after_repair"
    assert calls == []


def test_empty_pdf_evidence_still_blocks_model_review():
    workflow, unit, calls = workflow_for_quote(None)
    assert workflow.sft(unit)[0]["status"] == "quarantined"
    assert calls == []
