"""CPT cleaning is grounded, independently checked, and resumable."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.domain.cpt_processing import validate_cpt_processing
from lib.domain.workflow_node_prompts import (active_node_prompt_ids, validate_node_prompt_snapshot,
                                              VERSION_13_NODE_PROMPT_IDS)
from lib.domain.workflow_scale import node_roles
from lib.infrastructure.training_workflow import Workflow, create_run, read_json, run_path, verify_artifacts


ORIGINAL = "设备手册\n页码 12\n额定温度为 75 ℃，停机后检查阀门。公式：$E = mc^2$。"
CLEAN = "额定温度为 75 ℃，停机后检查阀门。公式：$E = mc^2$。"
MODEL_CONFIG = {"mode": "model", "review_mode": "text"}


class Cleaner:
    model = "offline-cleaner"

    def __init__(self, text=CLEAN, *, uncertain=False, invalid=False):
        self.text, self.uncertain, self.invalid = text, uncertain, invalid
        self.calls = []
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        return json.dumps({"text": self.text} if self.invalid else {
            "text": self.text, "uncertain": self.uncertain,
            "removed_noise": ["页码"], "reason": "仅删除排版页码，保留温度与公式。"}, ensure_ascii=False)


class Reviewer:
    model = "offline-cpt-reviewer"

    def __init__(self, *, invalid=False, fail_dimension=None):
        self.invalid, self.fail_dimension = invalid, fail_dimension
        self.calls = []
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        result = {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
                  "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                  "reason": "原文温度、单位、阀门操作和公式均保留。"}
        if not self.invalid:
            result["preservation"] = {key: key != self.fail_dimension for key in (
                "source_faithful", "numbers_and_units_preserved", "formulas_and_code_preserved", "coverage_sufficient")}
        return json.dumps(result, ensure_ascii=False)


def make_run(tmp_path, **options):
    source = tmp_path / "manual.txt"
    source.write_text(ORIGINAL, encoding="utf-8")
    output = tmp_path / "output"
    rid = create_run(output, sources=[source], targets=["cpt"], **options)
    return output, rid, run_path(output, rid)


@pytest.mark.parametrize("invalid", [False, [], {"mode": []}, {"mode": "unlimited"},
    {"mode": "model", "review_mode": []}, {"mode": "native", "review_mode": "text"}, {"extra": 1}])
def test_invalid_cpt_configuration_fails_before_output(tmp_path, invalid):
    with pytest.raises(ValueError, match="invalid_cpt_processing"):
        make_run(tmp_path, cpt_processing=invalid)
    assert not (tmp_path / "output").exists()


def test_cpt_settings_are_forwarded_and_only_available_on_cpt(tmp_path):
    driver = Mock()
    application = WorkflowApplication(driver)
    application.create_run(targets=["cpt"], cpt_processing=MODEL_CONFIG)
    assert driver.create.call_args.kwargs["cpt_processing"] == MODEL_CONFIG
    with pytest.raises(ValueError, match="cpt_processing_requires_cpt"):
        application.create_run(targets=["sft"], cpt_processing=MODEL_CONFIG)
    assert node_roles("cpt", "文档资料", cpt_processing=MODEL_CONFIG) == ("generation", "jev")
    assert node_roles("cpt", "文档资料") == ()
    assert active_node_prompt_ids("cpt", "文档资料", cpt_processing=MODEL_CONFIG) == (
        "workflow.cpt_clean", "workflow.cpt_review")
    assert active_node_prompt_ids("cpt", "文档资料") == ()
    assert active_node_prompt_ids("cpt", "开放需求", cpt_processing=MODEL_CONFIG) == (
        "workflow.corpus", "workflow.jev_score")


def test_model_cleaning_uses_source_and_independent_review_before_export(tmp_path):
    output, rid, path = make_run(tmp_path, cpt_processing=MODEL_CONFIG)
    cleaner, reviewer = Cleaner(), Reviewer()
    state = Workflow(output, rid, tmp_path, generator=cleaner, jev=reviewer).execute()
    assert state["status"] == "completed"
    assert len(cleaner.calls) == len(reviewer.calls) == 1
    assert ORIGINAL in json.loads(cleaner.calls[0][1]["content"])["source_text"]
    review_data = json.loads(reviewer.calls[0][1]["content"])
    assert review_data["original_source_text"] == ORIGINAL and review_data["cleaned_text"] == CLEAN
    assert json.loads((path / "artifacts/cpt.jsonl").read_text(encoding="utf-8")) == {"text": CLEAN}
    row = read_json(path / "artifacts/cpt.records.json")[0]
    assert row["evidence_level"] == "model_reviewed_source_text"
    assert row["retention_reason"] == "source_text_cleaned_and_independently_reviewed"
    assert row["judge"]["preservation"]["formulas_and_code_preserved"]
    recipe = read_json(path / "recipe.json")
    assert recipe["version"] == 14 and recipe["cpt_processing"] == MODEL_CONFIG
    validate_node_prompt_snapshot(recipe["node_prompt_templates"], recipe["node_prompt_system"], recipe_version=14)


@pytest.mark.parametrize("dimension", ["source_faithful", "numbers_and_units_preserved",
                                       "formulas_and_code_preserved", "coverage_sufficient"])
def test_a_failed_preservation_dimension_cannot_be_overridden_by_high_scores(tmp_path, dimension):
    output, rid, path = make_run(tmp_path, cpt_processing=MODEL_CONFIG)
    state = Workflow(output, rid, tmp_path, generator=Cleaner(), jev=Reviewer(fail_dimension=dimension)).execute()
    assert state["status"] == "needs_attention"
    assert verify_artifacts(path)["counts"] == {"cpt": 0}
    row = read_json(path / "artifacts/cpt.records.json")[0]
    assert row["reason"] == "cpt_source_review_rejected" and "text" not in row


def test_uncertain_cleaning_never_reaches_review_or_export(tmp_path):
    output, rid, path = make_run(tmp_path, cpt_processing=MODEL_CONFIG)
    reviewer = Reviewer()
    state = Workflow(output, rid, tmp_path, generator=Cleaner(uncertain=True), jev=reviewer).execute()
    assert state["status"] == "needs_attention" and reviewer.calls == []
    assert read_json(path / "artifacts/cpt.records.json")[0]["reason"] == "cpt_clean_uncertain"


def test_invalid_review_retry_reuses_durable_cleaning_call(tmp_path):
    output, rid, path = make_run(tmp_path, cpt_processing=MODEL_CONFIG)
    cleaner = Cleaner()
    assert Workflow(output, rid, tmp_path, generator=cleaner, jev=Reviewer(invalid=True)).execute()["status"] == "failed"
    reviewer = Reviewer()
    state = Workflow(output, rid, tmp_path, generator=cleaner, jev=reviewer).execute(resume_run=True)
    assert state["status"] == "completed" and len(cleaner.calls) == 1 and len(reviewer.calls) == 1
    assert verify_artifacts(path)["counts"] == {"cpt": 1}


def test_custom_prompts_are_literal_node_scoped_and_pinned(tmp_path):
    output, rid, _ = make_run(tmp_path, cpt_processing=MODEL_CONFIG,
        node_prompts={"cpt": {"workflow.cpt_clean": "CLEAN_CUSTOM {literal}",
                              "workflow.cpt_review": "REVIEW_CUSTOM {literal}"}})
    cleaner, reviewer = Cleaner(), Reviewer()
    assert Workflow(output, rid, tmp_path, generator=cleaner, jev=reviewer).execute()["status"] == "completed"
    assert cleaner.calls[0][0]["content"].endswith("CLEAN_CUSTOM {literal}")
    assert reviewer.calls[0][0]["content"].endswith("REVIEW_CUSTOM {literal}")


def test_absent_configuration_preserves_historical_native_catalog_and_calls(tmp_path):
    output, rid, path = make_run(tmp_path)
    cleaner, reviewer = Cleaner(), Reviewer()
    state = Workflow(output, rid, tmp_path, generator=cleaner, jev=reviewer).execute()
    assert state["status"] == "completed" and not cleaner.calls and not reviewer.calls
    recipe = read_json(path / "recipe.json")
    assert recipe["version"] == 11 and "cpt_processing" not in recipe
    assert set(recipe["node_prompt_templates"]["cpt"]) == set(VERSION_13_NODE_PROMPT_IDS["cpt"])
    assert read_json(path / "artifacts/cpt.records.json")[0]["text"] == ORIGINAL
    assert validate_cpt_processing(None) == {"mode": "native"}


def test_review_gets_original_evidence_even_if_document_parser_changed_a_number(tmp_path, monkeypatch):
    from lib.infrastructure import training_workflow
    monkeypatch.setattr(training_workflow, "snapshot_backend_endpoint", lambda root, backend, model: {
        "backend": backend, "model": model})
    changed = CLEAN.replace("75", "76")
    class ReaderCleaner(Cleaner):
        def chat(self, messages, **kwargs):
            data = json.loads(messages[1]["content"])
            if "text" in data:
                self.calls.append(deepcopy(messages))
                self.usage["calls"] += 1
                return json.dumps({"text": changed, "uncertain": False}, ensure_ascii=False)
            return super().chat(messages, **kwargs)
    output, rid, path = make_run(tmp_path, cpt_processing=MODEL_CONFIG, settings_root=Path.cwd(),
        document_parser={"mode": "model", "binding": {"backend": "offline", "model": "text-reader"}})
    cleaner, reviewer = ReaderCleaner(text=changed), Reviewer(fail_dimension="numbers_and_units_preserved")
    state = Workflow(output, rid, tmp_path, generator=cleaner, jev=reviewer).execute()
    assert state["status"] == "needs_attention"
    evidence = json.loads(reviewer.calls[0][1]["content"])
    assert evidence["original_source_text"] == changed
    assert evidence["reading_source_text"] == ORIGINAL
    assert evidence["parsed_source_text"] == changed
    assert "75" in evidence["reading_source_text"] and "76" in evidence["cleaned_text"]
    assert verify_artifacts(path)["counts"] == {"cpt": 0}


def test_complete_parser_output_is_reviewed_for_missing_paragraph_before_chunk_passes(tmp_path):
    output, rid, _ = make_run(tmp_path, cpt_processing=MODEL_CONFIG)
    missing = "备用温度上限为 65 ℃，故障后必须复核功率。"
    class CoverageReviewer(Reviewer):
        def chat(self, messages, **kwargs):
            evidence = json.loads(messages[1]["content"])
            if missing in evidence["reading_source_text"] and missing not in evidence["parsed_source_text"]:
                self.fail_dimension = "coverage_sufficient"
            return super().chat(messages, **kwargs)
    reviewer = CoverageReviewer()
    workflow = Workflow(output, rid, tmp_path, generator=Cleaner(), jev=reviewer)
    workflow.stage = "cpt"
    row = workflow.cpt({"id": "parsed-chunk", "kind": "document", "text": CLEAN,
                        "document_reading": {"mode": "model", "source_text": ORIGINAL + "\n" + missing,
                                             "parsed_text": CLEAN}})[0]
    assert row["status"] == "quarantined" and row["reason"] == "cpt_source_review_rejected"
    assert not row["judge"]["preservation"]["coverage_sufficient"]
    assert row["judge"]["scores"]["correctness"] == 5
    assert "reading_evidence_sha256" in row["cpt_processing"]
    evidence = json.loads(reviewer.calls[0][1]["content"])
    assert evidence["parsed_source_text"] == CLEAN and missing in evidence["reading_source_text"]


def test_visual_review_requires_confirmed_vision_before_creation(tmp_path, monkeypatch):
    from lib.infrastructure import document_vision
    source = tmp_path / "document.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    def reject(*args):
        raise ValueError("document_model_vision_not_confirmed")
    monkeypatch.setattr(document_vision, "require_vision_model", reject)
    with pytest.raises(ValueError, match="document_model_vision_not_confirmed"):
        create_run(tmp_path / "output", sources=[source], targets=["cpt"], settings_root=Path.cwd(),
                   cpt_processing={"mode": "model", "review_mode": "vision"},
                   node_models={"cpt": {"jev": {"backend": "test", "model": "vision"}}})
    assert not (tmp_path / "output").exists()


def test_visual_review_sends_original_page_only_to_reviewer_and_fails_closed_without_it(tmp_path, monkeypatch):
    from lib.infrastructure import document_vision, training_workflow
    monkeypatch.setattr(document_vision, "require_vision_model", lambda *args: None)
    monkeypatch.setattr(training_workflow, "snapshot_backend_endpoint", lambda root, backend, model: {
        "backend": backend, "model": model})
    source = tmp_path / "document.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    output = tmp_path / "output"
    rid = create_run(output, sources=[source], targets=["cpt"], settings_root=Path.cwd(),
                     cpt_processing={"mode": "model", "review_mode": "vision"},
                     node_models={"cpt": {"jev": {"backend": "test", "model": "vision"}}})
    cleaner, reviewer = Cleaner(), Reviewer()
    workflow = Workflow(output, rid, tmp_path, generator=cleaner, jev=reviewer)
    workflow.stage = "cpt"
    unit = {"id": "test-unit", "kind": "document", "text": ORIGINAL,
            "source_location": {"file": "document.pdf", "record": "page:1", "chunk": 0}}
    monkeypatch.setattr(workflow, "cpt_source_image", lambda unit: None, raising=False)
    assert workflow.cpt(unit)[0]["reason"] == "cpt_visual_source_unavailable"
    assert not cleaner.calls and not reviewer.calls
    image = "data:image/png;base64,original-pinned-page"
    monkeypatch.setattr(workflow, "cpt_source_image", lambda unit: image)
    row = workflow.cpt(unit)[0]
    assert row["status"] == "eligible" and row["evidence_level"] == "model_reviewed_visual_source"
    assert isinstance(cleaner.calls[0][1]["content"], str)
    assert reviewer.calls[0][1]["content"][1] == {"type": "image_url", "image_url": {"url": image}}
    text_unit = {**unit, "id": "text-unit", "source_location": {"file": "manual.md", "record": "document"}}
    monkeypatch.setattr(workflow, "cpt_source_image", lambda unit: None)
    row = workflow.cpt(text_unit)[0]
    assert row["status"] == "eligible" and row["evidence_level"] == "model_reviewed_source_text"
    assert row["cpt_processing"]["review_mode"] == "text"
    assert row["cpt_processing"]["requested_review_mode"] == "vision"
    assert isinstance(reviewer.calls[-1][1]["content"], str)
