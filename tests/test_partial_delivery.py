"""Failed runs expose only committed, explicitly marked and verified results."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from filelock import FileLock
import pytest
from streamlit.testing.v1 import AppTest

from lib.domain.workflow_delivery import has_deliverable_results, delivery_manifest_matches
from lib.infrastructure import workflow_driver, review_release_jobs
from lib.infrastructure.training_workflow import Workflow, create_run, read_json, run_path
from lib.io_utils import atomic_json
from lib.presentation.streamlit import review_navigation


@pytest.mark.parametrize("status,partial,expected", [
    ("completed", False, True), ("needs_attention", False, True),
    ("failed", True, True), ("cancelled", True, True),
    ("failed", False, False), ("cancelled", False, False),
    ("running", True, False), ("queued", True, False),
    ("failed", 1, False), ("failed", "true", False),
])
def test_delivery_requires_terminal_status_and_an_explicit_partial_marker(status, partial, expected):
    assert has_deliverable_results({"status": status, "production": {"partial_export": partial}}) is expected
    assert has_deliverable_results(None) is False


def _partial_run(tmp_path, status="failed"):
    source = tmp_path / "manual.txt"
    source.write_text("Disconnect power before checking wiring and reconnect after inspection.", encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source], targets=["cpt"])
    path = run_path(output, run_id)
    assert Workflow(output, run_id, tmp_path).execute()["status"] == "completed"
    state = read_json(path / "state.json")
    manifest = read_json(path / "artifacts" / "manifest.json")
    state.update(status=status, production={"partial_export": True, "round": 1})
    manifest["production"] = {"partial_export": True, "round": 1}
    atomic_json(path / "state.json", state)
    atomic_json(path / "artifacts" / "manifest.json", manifest)
    return workflow_driver.FilesystemWorkflowDriver(tmp_path, output), run_id, path


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_bundle_action_accepts_verified_partial_results_without_changing_task_status(tmp_path, monkeypatch, status):
    driver, run_id, path = _partial_run(tmp_path, status)
    before = (path / "state.json").read_bytes()
    calls = []
    monkeypatch.setattr(review_release_jobs, "start_release_job", lambda *args: calls.append(args) or {"status": "queued"})
    assert driver.start_bundle(run_id)["status"] == "queued"
    assert len(calls) == 1
    assert (path / "state.json").read_bytes() == before


@pytest.mark.parametrize("problem", ["missing_marker", "manifest_mismatch", "tampered", "active"])
def test_partial_bundle_cannot_bypass_readiness_activity_or_file_integrity(tmp_path, monkeypatch, problem):
    driver, run_id, path = _partial_run(tmp_path)
    calls = []
    monkeypatch.setattr(review_release_jobs, "start_release_job", lambda *args: calls.append(args) or {})
    if problem == "missing_marker":
        state = read_json(path / "state.json")
        state["production"]["partial_export"] = False
        atomic_json(path / "state.json", state)
    elif problem == "manifest_mismatch":
        manifest = read_json(path / "artifacts" / "manifest.json")
        manifest["production"]["partial_export"] = False
        atomic_json(path / "artifacts" / "manifest.json", manifest)
    elif problem == "tampered":
        (path / "artifacts" / "cpt.jsonl").write_text('{"text":"tampered"}\n', encoding="utf-8")
    if problem == "active":
        with FileLock(str(path / ".run.lock"), timeout=0):
            with pytest.raises(ValueError):
                driver.start_bundle(run_id)
    else:
        with pytest.raises(ValueError):
            driver.start_bundle(run_id)
    assert calls == []


def test_training_review_checks_partial_manifest_and_existing_native_records(tmp_path):
    from lib.infrastructure.training_review_driver import FilesystemTrainingReviewDriver

    driver, run_id, path = _partial_run(tmp_path)
    review = FilesystemTrainingReviewDriver(driver.output, "cpt")
    assert review._run(run_id) == path
    assert [item["id"] for item in review.reviewable_runs()] == [run_id]
    manifest = read_json(path / "artifacts" / "manifest.json")
    manifest["production"]["partial_export"] = False
    atomic_json(path / "artifacts" / "manifest.json", manifest)
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        review._run(run_id)
    assert review.reviewable_runs() == []


def test_agent_partial_review_preserves_recorded_evidence_and_negative_read_only(tmp_path):
    from tests.test_agent_review_backend import _run
    from lib.infrastructure.agent_review_driver import FilesystemAgentReviewDriver

    output, run_id, path = _run(tmp_path)
    state = read_json(path / "state.json")
    manifest = read_json(path / "artifacts" / "manifest.json")
    state.update(status="cancelled", production={"partial_export": True})
    manifest["production"] = {"partial_export": True}
    atomic_json(path / "state.json", state)
    atomic_json(path / "artifacts" / "manifest.json", manifest)
    driver = FilesystemAgentReviewDriver(output)
    assert [item["id"] for item in driver.reviewable_runs()] == [run_id]
    assert driver.queue(run_id, kind="positive")["total"] == 1
    assert driver.queue(run_id, kind="negative")["total"] == 1
    with pytest.raises(ValueError, match="negative_agent_trajectories_are_read_only"):
        driver.queue(run_id, kind="negative", decision="approved")


@pytest.mark.parametrize("mismatch", [False, True])
def test_review_action_rechecks_partial_readiness_and_marker_before_navigation(monkeypatch, mismatch):
    values = {"ws": "fixture", "nav": "任务管理"}
    monkeypatch.setattr(review_navigation, "st", SimpleNamespace(session_state=values))
    app = SimpleNamespace(state=lambda run_id: {"status": "failed", "targets": ["sft"],
                                                "production": {"partial_export": True}},
                          is_active=lambda run_id: False,
                          package_inventory=lambda run_id: {
                              "manifest": {"counts": {"sft": 1}, "production": {"partial_export": not mismatch}},
                              "files": [{"name": "sft.jsonl"}]})
    review_navigation.open_verified_review(app, "partial-run", "sft")
    assert values["nav"] == ("任务管理" if mismatch else "人工审核")
    assert ("sft-review-run" in values) is not mismatch


def test_partial_preview_and_package_selector_keep_failure_status():
    from tests.test_review_navigation import PREVIEW_SCRIPT
    from tests.test_package_page import SCRIPT as PACKAGE_SCRIPT

    preview = PREVIEW_SCRIPT.replace("status='completed'", "status='failed', production={'partial_export':True}")
    preview = preview.replace("manifest=dict(counts=", "manifest=dict(production={'partial_export':True},counts=")
    ui = AppTest.from_string(preview.encode("ascii", "backslashreplace").decode("ascii")).run()
    assert not ui.exception
    assert ui.selectbox(key="data-preview-run:fixture").value == "first-run"
    assert any("部分交付" in item.value for item in ui.caption)

    package = PACKAGE_SCRIPT.replace("status='completed'", "status='failed', production={'partial_export':True}")
    package = package.replace("return dict(targets=", "return dict(status='failed',production={'partial_export':True},targets=")
    package = package.replace("manifest=dict(counts=", "manifest=dict(production={'partial_export':True},counts=")
    ui = AppTest.from_string(package.encode("ascii", "backslashreplace").decode("ascii")).run()
    assert not ui.exception
    assert ui.selectbox(key="package-run:fixture").value == "a"
    assert any("部分交付" in item.value for item in ui.warning)


def test_manifest_flags_cannot_be_confused_with_truthy_strings():
    state = {"status": "failed", "production": {"partial_export": True}}
    assert not delivery_manifest_matches(state, {"production": {"partial_export": "true"}})
    assert not delivery_manifest_matches({"status": "completed"}, {"production": {"partial_export": 1}})
    original = deepcopy(state)
    assert delivery_manifest_matches(state, {"production": {"partial_export": True}})
    assert state == original


@pytest.mark.parametrize("state", [None, [], {}, {"status": []}, {"status": {}}, {"status": True}])
def test_malformed_state_does_not_gain_delivery_readiness(state):
    assert has_deliverable_results(state) is False
    if not isinstance(state, dict):
        assert delivery_manifest_matches(state, {}) is False
