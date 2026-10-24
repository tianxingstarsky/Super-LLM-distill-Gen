"""Empty-source delivery must seal an honest report without another model call."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib.contexts.workflows.domain.director_audit import director_delivery_audit
from lib.contexts.workflows.infrastructure.execution_runtime import FilesystemRunSession
from lib.infrastructure import training_workflow as engine


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield  # These tests never call a model service.


class NoRequests:
    model = "offline-no-requests"
    usage = {}

    def chat(self, *args, **kwargs):
        pytest.fail("An unusable input must not trigger generation or review.")


@pytest.mark.parametrize("production", [None, {"goals": {"sft": 2}, "round_size": 2}])
def test_unstarted_director_seals_zero_delivery_and_resumes_without_model_calls(tmp_path, production):
    source = tmp_path / "paper.md"
    source.write_text("An equation could not be parsed with sufficient confidence.", encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"],
                               qa_director={"enabled": True}, production=production)

    class UncertainInput(engine.Workflow):
        def iter_source_units(self, source):
            yield {"id": "uncertain", "source_id": source["sha256"], "kind": "document",
                   "status": "quarantined", "reason": "document_parse_uncertain", "text": ""}

    runtime = UncertainInput(output, run_id, tmp_path, generator=NoRequests(), jev=NoRequests())
    state = runtime.execute()
    assert state["status"] == "needs_attention", state.get("error")
    run_path = engine.run_path(output, run_id)
    manifest = engine.verify_artifacts(run_path)
    assert manifest["counts"] == {"sft": 0}
    audit = manifest["qa_director"]
    assert audit["batches_planned"] == 0
    assert audit["coverage"]
    assert all(set(counts.values()) == {0} for counts in audit["coverage"].values())
    assert engine.read_json(run_path / "artifacts/quality.json")["qa_director"]["coverage"] == audit["coverage"]
    assert runtime.execute(resume_run=True)["status"] == "needs_attention"
    assert engine.verify_artifacts(run_path)["sha256"] == manifest["sha256"]


@pytest.mark.parametrize("audit", [
    None, [],
    {"batches_planned": 1}, {"coverage": {}}, {"coverage": [], "batches_planned": 0},
    {"coverage": {}, "batches_planned": True}, {"coverage": {}, "batches_planned": -1},
])
def test_partial_execution_audit_is_not_replaced_with_guessed_counters(audit):
    with pytest.raises(ValueError, match="director_audit_incomplete"):
        director_delivery_audit(audit, {"closed_book": 1})


def test_existing_audit_fields_and_counters_are_preserved_without_aliasing():
    audit = {"batches_planned": 2, "coverage": {"closed_book": {"planned": 2, "accepted": 1}},
             "feedback": {"rejected": 1}, "history_limit": 8}
    original = deepcopy(audit)
    delivered = director_delivery_audit(audit, {"closed_book": 1})
    assert delivered == original
    delivered["coverage"]["closed_book"]["accepted"] = 99
    assert audit == original


@pytest.mark.parametrize("error,expected", [
    (KeyError("coverage"), "KeyError"),
    (TypeError("invalid local operation"), "TypeError"),
    (ValueError("director_audit_incomplete"), "director_audit_incomplete"),
    (TimeoutError("provider timed out"), "service_timeout"),
])
def test_production_failure_distinguishes_workflow_errors_from_model_errors(error, expected):
    exports = []
    runtime = SimpleNamespace(
        state={"production": {}, "stages": {"package": {}}}, stage="package",
        production_enabled=lambda: True,
        _production_partial_export=lambda *args: exports.append(args), event=lambda *args, **kwargs: None,
    )
    FilesystemRunSession(runtime, supported_recipe_versions=()).record_failure(error)
    assert runtime.state["error"] == expected
    assert runtime.state["stages"]["package"]["error"] == expected
    assert runtime.state["production"]["stop_reason"] == expected
    assert exports == [("paused", expected)]
