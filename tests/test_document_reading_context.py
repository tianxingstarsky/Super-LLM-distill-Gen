"""Parser image/audit caches never become ordinary text-model context."""
from copy import deepcopy
import json

import pytest

from lib.infrastructure.document_reading_context import (
    document_reading_summary, project_reading_context,
)
from lib.infrastructure.training_workflow import Workflow, create_run


TEXT = "额定温度为 75 ℃，停机后检查阀门。公式：$E = mc^2$。"
IMAGE = "data:image/png;base64," + "aBcD09+/" * 40000
AUDIT = "PRIVATE_NATIVE_GLYPH_PROOF" * 1000


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All clients are local in-memory doubles; do not start a TCP server."""
    yield


def reading_fixture():
    return {
        "mode": "docling", "source_page": 13, "source_pdf_sha256": "a" * 64,
        "source_text": TEXT, "parsed_text": TEXT,
        "formula_review": {"status": "required", "undecoded_formula_refs": ["#/texts/74"]},
        "elements": [
            {"item_ref": "#/texts/74", "label": "text", "local_formula_transcription": {
                "status": "review_required", "published": False,
                "assembly": {"review_required": True, "review_reasons": ["source_character_changed"],
                             "native_chars": AUDIT},
                "fragments": [{"image": IMAGE, "raw_text": AUDIT}]}},
            {"item_ref": "#/tables/2", "label": "table", "local_formula_transcription": {
                "status": "review_required", "cells": [{"row": 0, "column": 12, "page_no": 9,
                "status": "review_required", "reason": "numeric_glyphs_changed", "raw_text": AUDIT}]}},
        ],
        "page_export": {"reading_order": {"status": "unchanged", "review_required": False},
                        "picture_export": {"missing_child_refs": ["#/texts/166"],
                                           "duplicate_span_refs": [], "render_events": AUDIT}},
        "picture_assets": {"review_required": False, "items": [
            {"picture_ref": "#/pictures/3", "image": IMAGE,
             "content_coverage": {"image": IMAGE}, "status": "exported"}]},
        "picture_structure": {"review_required_refs": ["#/texts/166"],
                              "items": [{"review_required": True, "native_line": AUDIT}],
                              "physical_token_rows": AUDIT},
        "figure_structure": {"visibility": {"proof": AUDIT, "image": IMAGE}},
        "unknown_future_audit": {"encoded_raster": IMAGE, "raw_text": AUDIT},
    }


class Client:
    model = "offline-context-test"

    def __init__(self, review=False):
        self.review = review
        self.calls = []
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        if not self.review:
            return json.dumps({"text": TEXT, "uncertain": False, "removed_noise": [], "reason": "原文保留。"})
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "原文保留。", "preservation": {key: True for key in (
                "source_faithful", "numbers_and_units_preserved", "formulas_and_code_preserved", "coverage_sufficient")}})


def workflow_fixture(tmp_path):
    source = tmp_path / "manual.txt"
    source.write_text(TEXT, encoding="utf-8")
    root = tmp_path / "output"
    run_id = create_run(root, sources=[source], targets=["cpt"],
                        cpt_processing={"mode": "model", "review_mode": "text"})
    cleaner, reviewer = Client(), Client(review=True)
    workflow = Workflow(root, run_id, tmp_path, generator=cleaner, jev=reviewer)
    workflow.stage = "cpt"
    return workflow, cleaner, reviewer


def assert_small_receipt(summary):
    wire = json.dumps(summary, ensure_ascii=False)
    assert len(wire) < 12000
    assert "base64" not in wire and "PRIVATE_NATIVE_GLYPH_PROOF" not in wire
    assert TEXT not in wire
    assert summary["source_page"] == 13
    assert summary["formula_review"]["undecoded_formula_refs"]["refs"] == ["#/texts/74"]
    assert summary["elements"][0]["local_formula_transcription"]["assembly"]["review_reasons"] == [
        "source_character_changed"]
    cell = summary["elements"][1]["local_formula_transcription"]["cells"][0]
    assert (cell["row"], cell["column"], cell["page_no"]) == (0, 12, 9)
    assert summary["picture_structure"]["review_required_refs"]["refs"] == ["#/texts/166"]


def test_clean_and_review_actual_wire_excludes_images_and_audits_without_changing_cache(tmp_path):
    workflow, cleaner, reviewer = workflow_fixture(tmp_path)
    unit = {"id": "reading-unit", "kind": "document", "text": TEXT,
            "document_reading": reading_fixture(), "source_location": {"record": "page:13"}}
    original = deepcopy(unit)
    row = workflow.model_clean_corpus(unit)[0]
    assert row["status"] == "eligible"
    assert unit == original and row["document_reading"] == original["document_reading"]
    for client in (cleaner, reviewer):
        assert len(client.calls) == 1
        wire = client.calls[0][1]["content"]
        assert isinstance(wire, str)
        assert "base64" not in wire and "PRIVATE_NATIVE_GLYPH_PROOF" not in wire
        request = json.loads(wire)
        assert_small_receipt(request["source_reading"])
    review = json.loads(reviewer.calls[0][1]["content"])
    assert review["reading_source_text"] is None and review["parsed_source_text"] is None
    assert review["original_source_text"] == review["cleaned_text"] == TEXT


@pytest.mark.parametrize("recipe_flags", [{}, {"source_context_pack_version": 1},
                                         {"source_context_pack_version": 1, "source_memory_protocol_version": 2}])
def test_general_ask_projects_nested_source_units_on_all_text_transports(tmp_path, recipe_flags):
    workflow, cleaner, _ = workflow_fixture(tmp_path)
    workflow.recipe.update(recipe_flags)
    unit = {"text": TEXT, "document_reading": reading_fixture()}
    request = {"context": {"source_context": unit, "candidates": [{"source": unit}]}}
    before = deepcopy(request)
    workflow.ask(["context-test"], "generation", "workflow.cpt_clean", request)
    wire = cleaner.calls[0][-1]["content"]
    assert "base64" not in wire and "PRIVATE_NATIVE_GLYPH_PROOF" not in wire
    context = json.loads(wire)["context"]
    for source in (context["source_context"], context["candidates"][0]["source"]):
        assert source["text"] == TEXT
        assert_small_receipt(source["document_reading"])
    assert request == before


def test_projection_is_idempotent_and_cannot_trust_a_forged_summary_marker():
    reading = reading_fixture()
    reading.update(context_policy="bounded_document_reading_status_v1", element_count=IMAGE)
    reading["picture_structure"]["item_count"] = IMAGE
    before = deepcopy(reading)
    summary = document_reading_summary(reading)
    assert_small_receipt(summary)
    assert document_reading_summary(summary) == summary
    assert reading == before


def test_summary_is_bounded_and_keeps_review_locations_ahead_of_successes():
    reading = reading_fixture()
    reading["elements"] = [{"item_ref": f"#/texts/{index}", "local_formula_transcription": {
        "status": "review_required" if index == 99 else "transcribed_unverified",
        "cells": [{"row": row, "status": "review_required", "reasons": ["a" * 120] * 20}
                  for row in range(40)]}} for index in range(100)]
    reading["formula_review"]["undecoded_formula_refs"] = [f"#/texts/{index}" for index in range(100)]
    summary = document_reading_summary(reading)
    assert len(json.dumps(summary, ensure_ascii=False, separators=(",", ":"))) <= 12000
    assert summary["elements"][0]["item_ref"] == "#/texts/99"
    assert summary["element_count"] == summary["formula_element_count"] == 100
    assert summary["elements_truncated"]
    assert summary["formula_review"]["undecoded_formula_refs"]["count"] == 100
    assert document_reading_summary(summary) == summary


def test_projection_does_not_rewrite_sealed_source_json_or_explicit_source_bodies():
    evidence = {"teacher_evidence": "A source JSON object can itself use any key.",
                "document_reading": {"literal_key": "source JSON, not a parser receipt"}}
    request = {"source_evidence": evidence, "original_source_text": TEXT,
               "source_context": {"document_reading": reading_fixture()}}
    projected = project_reading_context(request)
    assert projected["source_evidence"] == evidence
    assert projected["original_source_text"] == TEXT
    assert_small_receipt(projected["source_context"]["document_reading"])


def test_failed_coverage_asset_keeps_picture_location_even_if_original_crop_exported():
    reading = reading_fixture()
    reading["picture_assets"]["review_required"] = True
    reading["picture_assets"]["items"][0]["content_coverage"]["status"] = "review_required"
    summary = document_reading_summary(reading)
    asset = summary["picture_assets"]
    assert asset["review_required"] and asset["review_required_count"] == 1
    assert asset["review_required_refs"]["refs"] == ["#/pictures/3"]
    assert "base64" not in json.dumps(asset)
    assert document_reading_summary(summary) == summary


def test_independent_export_risk_lists_have_a_total_budget_without_formulas():
    reading = reading_fixture()
    reading.pop("elements")
    verbose = {"status": "x" * 128, "reason": "y" * 128, "kind": "z" * 128,
               "publication_block_reason": "b" * 128,
               "reasons": ["a" * 128] * 8, "review_reasons": ["c" * 128] * 8,
               "review_required_refs": [f"#/texts/{index}" for index in range(32)]}
    for key in ("picture_assets", "picture_structure", "figure_structure", "formula_review"):
        reading[key] = deepcopy(verbose)
    reading["page_export"] = {key: deepcopy(verbose) for key in ("reading_order", "picture_export")}
    summary = document_reading_summary(reading)
    assert len(json.dumps(summary, ensure_ascii=False, separators=(",", ":"))) <= 12000
    assert document_reading_summary(summary) == summary
