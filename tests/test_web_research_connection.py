"""Local credential saves drive real probe/search adapters without a restart."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
from contextlib import contextmanager

import pytest

from lib import workspace
from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure import brave_web_research as brave
from lib.infrastructure import web_research_connection as connections
from lib.infrastructure.training_workflow import Workflow, create_run
from lib.infrastructure.training_workflow import verify_artifacts
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
from tests.test_web_research import CONFIG, FakeConnection, FakeResponse


FIRST = "BvR-local-first-credential-731"
SECOND = "BvR-local-second-credential-824"
ENVIRONMENT = "BvR-environment-credential-915"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv(connections.KEY_ENV, raising=False)
    FakeConnection.calls = []
    FakeConnection.response = FakeResponse()
    monkeypatch.setattr(brave.http.client, "HTTPSConnection", FakeConnection)


def application(root):
    return WorkflowApplication(FilesystemWorkflowDriver(root, root / "out"))


def new_worker(root):
    config = root / "configs"
    config.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "configs" / "preferences.yaml",
                    config / "preferences.yaml")
    run_id = create_run(root / "out", brief="Public maintenance exercises", targets=["cpt"],
                        tasks=1, max_units=1, web_research=CONFIG, settings_root=root)
    return Workflow(root / "out", run_id, root)


def all_run_text(run):
    return "\n".join(path.read_text(encoding="utf-8") for path in run.path.rglob("*")
                     if path.is_file() and path.suffix in {".json", ".jsonl", ".yaml"})


def test_save_probe_and_existing_worker_use_live_local_key_without_restart(tmp_path, monkeypatch):
    monkeypatch.setenv(connections.KEY_ENV, ENVIRONMENT)
    app = application(tmp_path)
    worker = new_worker(tmp_path)  # Worker composition must not capture credentials.
    before = app.web_research_capabilities()
    assert before["key_source"] == "environment" and before["brave_configured"]
    saved = app.save_web_research_connection(FIRST)
    assert FakeConnection.calls == []  # Saving never contacts a provider.
    assert saved == app.web_research_capabilities()
    assert saved["key_source"] == "local" and saved["connection_revision"] != before["connection_revision"]
    assert app.check_web_research_connection() == "ready"
    assert FakeConnection.calls[-1]["headers"]["X-Subscription-Token"] == FIRST
    first_probe = (saved["connection_revision"], "ready")
    updated = app.save_web_research_connection(SECOND)
    assert updated["connection_revision"] != first_probe[0]
    assert updated == app.web_research_capabilities()
    document = worker.research()
    assert document["results"] and FakeConnection.calls[-1]["headers"]["X-Subscription-Token"] == SECOND
    # No connection data is pinned in recipe, checkpoints, events or state.
    text = all_run_text(worker)
    for secret in (FIRST, SECOND, ENVIRONMENT):
        assert secret not in text and secret not in json.dumps(updated)
    assert "connection_revision" not in text and "protected_key" not in text
    assert app.check_web_research_connection() == "ready"
    assert FakeConnection.calls[-1]["headers"]["X-Subscription-Token"] == SECOND


def test_local_precedence_environment_fallback_and_safe_revisions(tmp_path, monkeypatch):
    app = application(tmp_path)
    assert app.web_research_capabilities() == {
        "brave_configured": False, "key_source": "none", "connection_revision": "none"}
    monkeypatch.setenv(connections.KEY_ENV, ENVIRONMENT)
    env = app.web_research_capabilities()
    assert app.web_research_capabilities() == env
    assert len(env["connection_revision"]) == 32
    monkeypatch.setenv(connections.KEY_ENV, SECOND)
    assert app.web_research_capabilities()["connection_revision"] != env["connection_revision"]
    local = app.save_web_research_connection(FIRST)
    assert app.web_research_capabilities() == local
    monkeypatch.setenv(connections.KEY_ENV, ENVIRONMENT)
    assert app.web_research_capabilities() == local
    assert app.save_web_research_connection("")["key_source"] == "environment"
    assert app.check_web_research_connection() == "ready"
    assert FakeConnection.calls[-1]["headers"]["X-Subscription-Token"] == ENVIRONMENT
    monkeypatch.delenv(connections.KEY_ENV)
    assert app.web_research_capabilities()["connection_revision"] == "none"
    monkeypatch.setenv(connections.KEY_ENV, ENVIRONMENT)
    assert app.web_research_capabilities()["connection_revision"] != env["connection_revision"]


def test_host_http_debug_setting_cannot_log_subscription_token(tmp_path, monkeypatch, capsys):
    class DebugConnection(FakeConnection):
        debuglevel = 1

        def request(self, method, path, headers):
            if self.debuglevel:
                print(headers)
            assert self.debuglevel == 0
            super().request(method, path, headers)

    monkeypatch.setattr(brave.http.client, "HTTPSConnection", DebugConnection)
    app = application(tmp_path)
    app.save_web_research_connection(FIRST)
    assert app.check_web_research_connection() == "ready"
    assert brave.search(CONFIG, root=tmp_path)
    captured = capsys.readouterr()
    assert FIRST not in captured.out + captured.err


def test_root_isolation_and_saved_key_is_read_by_fresh_driver(tmp_path, monkeypatch):
    monkeypatch.setenv(connections.KEY_ENV, ENVIRONMENT)
    first, second = application(tmp_path / "first"), application(tmp_path / "second")
    first.save_web_research_connection(FIRST)
    assert first.check_web_research_connection() == second.check_web_research_connection() == "ready"
    assert [call["headers"]["X-Subscription-Token"] for call in FakeConnection.calls] == [FIRST, ENVIRONMENT]
    second.save_web_research_connection(SECOND)
    assert application(tmp_path / "first").web_research_capabilities() == first.web_research_capabilities()
    assert first.web_research_capabilities()["connection_revision"] != second.web_research_capabilities()["connection_revision"]
    assert second.check_web_research_connection() == "ready"
    assert FakeConnection.calls[-1]["headers"]["X-Subscription-Token"] == SECOND


def test_windows_file_is_user_and_root_protected_posix_file_is_owner_only(tmp_path):
    application(tmp_path).save_web_research_connection(FIRST)
    target = tmp_path / ".dataforge" / "connections" / "brave.json"
    document = json.loads(target.read_bytes())
    if os.name == "nt":
        assert "api_key" not in document and FIRST.encode() not in target.read_bytes()
        other_root = tmp_path / "other"
        other_target = other_root / ".dataforge" / "connections" / "brave.json"
        other_target.parent.mkdir(parents=True)
        shutil.copyfile(target, other_target)
        assert application(other_root).web_research_capabilities()["brave_configured"] is False
        with pytest.raises(ValueError, match="^web_search_connection_invalid$"):
            brave.search(CONFIG, root=other_root)
    else:
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("content", [b"not-json", b"{}", b'{"api_key":"sensitive-local-key"}',
    b"x" * (connections.MAX_CONNECTION_BYTES + 1)])
def test_corrupt_local_file_fails_closed_without_using_environment(tmp_path, monkeypatch, content):
    monkeypatch.setenv(connections.KEY_ENV, ENVIRONMENT)
    app = application(tmp_path)
    app.save_web_research_connection(FIRST)
    target = tmp_path / ".dataforge" / "connections" / "brave.json"
    target.write_bytes(content)
    cap = app.web_research_capabilities()
    assert cap == {"brave_configured": False, "key_source": "local", "connection_revision": "invalid",
                   "connection_error": "web_search_connection_invalid"}
    assert app.check_web_research_connection() == "unavailable"
    with pytest.raises(ValueError, match="^web_search_connection_invalid$"):
        brave.search(CONFIG, root=tmp_path)
    assert FakeConnection.calls == []
    app.save_web_research_connection(SECOND)  # An explicit save can repair the local connection.
    assert app.check_web_research_connection() == "ready"
    assert FakeConnection.calls[-1]["headers"]["X-Subscription-Token"] == SECOND


@pytest.mark.parametrize("where", ["construct", "request", "response"])
def test_malicious_provider_exception_never_reaches_workflow_state_or_events(tmp_path, monkeypatch, where):
    app = application(tmp_path)
    app.save_web_research_connection(FIRST)
    class Leaking(FakeConnection):
        def __init__(self, *args, **kwargs):
            if where == "construct":
                raise ValueError(FIRST)
            super().__init__(*args, **kwargs)
        def request(self, *args, **kwargs):
            if where == "request":
                raise RuntimeError(FIRST)
            super().request(*args, **kwargs)
        def getresponse(self):
            if where == "response":
                raise ValueError(FIRST)
            return super().getresponse()
    monkeypatch.setattr(brave.http.client, "HTTPSConnection", Leaking)
    assert app.check_web_research_connection() == "unavailable"
    worker = new_worker(tmp_path)
    worker.ask = lambda *args: pytest.fail("Failed search must stop model calls")
    state = worker.execute()
    assert state["status"] == "failed" and state["error"] == "web_search_provider_error"
    assert FIRST not in all_run_text(worker)


@pytest.mark.parametrize("field", ["title", "description", "url"])
def test_provider_echo_of_key_is_not_a_result_or_a_checkpoint(tmp_path, field):
    application(tmp_path).save_web_research_connection(FIRST)
    row = {"title": "Guide", "description": "Public maintenance.", "url": "https://example.org/guide"}
    row[field] = "https://example.org/?token=" + FIRST if field == "url" else "Echo " + FIRST
    FakeConnection.response = FakeResponse(payload={"web": {"results": [row]}})
    worker = new_worker(tmp_path)
    with pytest.raises(ValueError, match="^web_search_no_safe_results$") as error:
        worker.research()
    assert FIRST not in str(error.value) and FIRST not in all_run_text(worker)


def test_failed_atomic_update_retains_old_key_and_cleans_temporary(tmp_path, monkeypatch):
    app = application(tmp_path)
    before = app.save_web_research_connection(FIRST)
    def failed(*args):
        raise RuntimeError(SECOND)
    monkeypatch.setattr(connections, "_replace_state", failed)
    with pytest.raises(ValueError, match="^web_search_connection_invalid$") as error:
        app.save_web_research_connection(SECOND)
    assert SECOND not in str(error.value)
    assert app.web_research_capabilities() == before
    assert not list((tmp_path / ".dataforge" / "connections").glob(".brave-*.tmp"))
    assert app.check_web_research_connection() == "ready"
    assert FakeConnection.calls[-1]["headers"]["X-Subscription-Token"] == FIRST


@pytest.mark.parametrize("value", [None, 42, "secret\nInjected: header", "密钥示例", "x" * 4097])
def test_invalid_credentials_are_not_written_or_returned(tmp_path, value):
    with pytest.raises(ValueError, match="^web_search_connection_invalid$"):
        application(tmp_path).save_web_research_connection(value)
    assert not (tmp_path / ".dataforge").exists()


def test_linked_local_file_and_hardlink_are_rejected_without_mutating_target(tmp_path):
    app = application(tmp_path)
    app.save_web_research_connection(FIRST)
    target = tmp_path / ".dataforge" / "connections" / "brave.json"
    victim = tmp_path / "victim.json"
    target.rename(victim)
    try:
        target.symlink_to(victim)
    except OSError:
        target.hardlink_to(victim)
    before = victim.read_bytes()
    assert app.web_research_capabilities()["brave_configured"] is False
    assert app.check_web_research_connection() == "unavailable"
    with pytest.raises(ValueError, match="^web_search_connection_invalid$"):
        app.save_web_research_connection(SECOND)
    assert victim.read_bytes() == before


def test_existing_team_guard_denies_all_connection_use_before_io(tmp_path, monkeypatch):
    app = application(tmp_path)
    def denied(root):
        assert root == tmp_path
        raise PermissionError("shared_team_access_denied")
    monkeypatch.setattr(workspace, "_require_shared_workspace_access", denied, raising=False)
    for operation in (lambda: app.save_web_research_connection(FIRST), app.web_research_capabilities,
                      app.check_web_research_connection, lambda: brave.search(CONFIG, root=tmp_path)):
        with pytest.raises(PermissionError, match="^shared_team_access_denied$"):
            operation()
    assert FakeConnection.calls == [] and not (tmp_path / ".dataforge").exists()


def test_saved_connection_reaches_full_workflow_export_without_entering_draft_or_recipe(tmp_path):
    from lib.infrastructure.creation_draft_file import CreationDraftFile

    app = application(tmp_path)
    worker = new_worker(tmp_path)
    draft = CreationDraftFile(tmp_path / "out")
    fields = {"workflow-open-brief:default": "Public maintenance exercises",
              "workflow-web-research-query:default": CONFIG["query"]}
    draft.replace(fields)
    app.save_web_research_connection(FIRST)
    with pytest.raises(ValueError, match="^invalid_creation_draft$"):
        draft.update({"workflow-web-research-api-key:default": FIRST})
    assert draft.load() == fields
    def answer(key, role, prompt_id, data):
        if prompt_id == "workflow.plan":
            assert data["web_research"]["leads"]
            return {"tasks": ["Explain safe equipment maintenance"]}
        if prompt_id == "workflow.corpus":
            return {"text": "Inspect the power connection before any equipment maintenance."}
        return {"keep": True, "grounded": True, "reasoning_valid": True,
                "correctness": 5, "scores": {key: 5 for key in
                    ("correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "The statement follows the task."}
    worker.ask = answer
    assert worker.execute()["status"] == "completed"
    assert "web_research.json" in verify_artifacts(worker.path)["sha256"]
    assert FakeConnection.calls[-1]["headers"]["X-Subscription-Token"] == FIRST
    assert FIRST not in all_run_text(worker)
    assert FIRST not in (tmp_path / "out" / ".creation-draft.json").read_text(encoding="utf-8")
    assert FIRST not in repr(connections.resolve_connection(tmp_path))


def test_private_publication_reuses_activation_boundary_until_replace(tmp_path, monkeypatch):
    from lib import review_center

    held = []
    @contextmanager
    def boundary(project_db):
        assert project_db == tmp_path / "data" / "review_center.db"
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr(review_center, "shared_cache_publish_boundary", boundary, raising=False)
    monkeypatch.setattr(workspace, "_require_shared_workspace_access", lambda root: None, raising=False)
    original = connections._replace_state
    def replace(*args):
        assert held == [True], "Credential publication must retain the private activation reservation"
        return original(*args)
    monkeypatch.setattr(connections, "_replace_state", replace)
    assert application(tmp_path).save_web_research_connection(FIRST)["key_source"] == "local"
    assert held == []


def test_activation_while_waiting_for_private_boundary_denies_save_without_side_effects(tmp_path, monkeypatch):
    from lib import review_center

    active = False
    @contextmanager
    def boundary(project_db):
        nonlocal active
        active = True
        yield
    def guard(root):
        if active:
            raise PermissionError("shared_team_access_denied")
    monkeypatch.setattr(review_center, "shared_cache_publish_boundary", boundary, raising=False)
    monkeypatch.setattr(workspace, "_require_shared_workspace_access", guard, raising=False)
    with pytest.raises(PermissionError, match="^shared_team_access_denied$"):
        application(tmp_path).save_web_research_connection(FIRST)
    assert not (tmp_path / ".dataforge").exists() and FakeConnection.calls == []
