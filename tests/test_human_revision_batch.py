"""Repair execution validates originals once per batch without weakening checks."""
from pathlib import Path

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.human_revision import verified_revision_batch, verified_revision_parent
from test_human_rework_issues import configured, selection
from test_human_sessions import generate


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield None


def prepared(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    saved = app.save_issues(sid, [selection(first, candidate)], instruction="Use a clear safety reminder.",
        expected_version=app.session(sid)["version"])
    child = app.revise_issues(sid, request_id="batch-auth", expected_version=saved["version"])
    recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    return output, child["run_id"], recipe["repair_inputs"][0]


def test_batch_shares_strong_verification_without_rescanning_each_selection(tmp_path, monkeypatch):
    output, child_id, item = prepared(tmp_path)
    calls = []
    verify = engine.verify_artifacts

    def counted(path):
        calls.append(Path(path))
        return verify(path)

    monkeypatch.setattr(engine, "verify_artifacts", counted)
    context = item["revision_context"]
    with verified_revision_batch(output, child_id, [context]):
        assert verified_revision_parent(output, child_id, context) == item["record"]
        assert verified_revision_parent(output, child_id, context) == item["record"]
    assert len(calls) == 1
    # A new job performs a fresh strong verification; there is no cross-job lease.
    with verified_revision_batch(output, child_id, [context]):
        assert verified_revision_parent(output, child_id, context) == item["record"]
    assert len(calls) == 2


def test_batch_rejects_a_parent_changed_after_its_first_selection(tmp_path):
    output, child_id, item = prepared(tmp_path)
    context = item["revision_context"]
    parent = engine.run_path(output, context["parent_run_id"])
    with pytest.raises(ValueError, match="human_session_result_changed"):
        with verified_revision_batch(output, child_id, [context]):
            verified_revision_parent(output, child_id, context)
            path = parent / "artifacts" / "sft.records.json"
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            verified_revision_parent(output, child_id, context)


def test_batch_rejects_sidecar_changed_after_first_selection(tmp_path):
    output, child_id, item = prepared(tmp_path)
    context = item["revision_context"]
    folder = output / "human-sessions" / context["session_id"]
    session = engine.read_json(folder / "session.json")
    child = next(row for row in session["rounds"] if row["run_id"] == child_id)
    assert "details_ref" in child
    with pytest.raises(ValueError, match="human_session_result_changed"):
        with verified_revision_batch(output, child_id, [context]):
            verified_revision_parent(output, child_id, context)
            sidecar = folder / child["details_ref"]["file"]
            sidecar.write_text(sidecar.read_text(encoding="utf-8") + " ", encoding="utf-8")
            verified_revision_parent(output, child_id, context)


def test_unrelated_session_update_during_batch_keeps_child_authorization(tmp_path):
    from lib.io_utils import atomic_json

    output, child_id, item = prepared(tmp_path)
    context = item["revision_context"]
    session_file = output / "human-sessions" / context["session_id"] / "session.json"
    with verified_revision_batch(output, child_id, [context]):
        assert verified_revision_parent(output, child_id, context) == item["record"]
        session = engine.read_json(session_file)
        session["version"] += 1
        atomic_json(session_file, session)
        assert verified_revision_parent(output, child_id, context) == item["record"]
