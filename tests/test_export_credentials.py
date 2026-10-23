"""Exact real-configuration credential matches block publication, never rewrite CoT."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from lib.infrastructure.export_credentials import ExportCredentialGuard
from lib.bootstrap.releases import release_application
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_archive import prepare_bundle, prepared_bundle, artifact_bytes

ROOT = Path(__file__).resolve().parent.parent
SECRET = "sk-test-only-export-canary-73920185063749201850"


@pytest.fixture(autouse=True)
def mock_llm_server():
    yield  # Every operation is local; no model HTTP request is permitted.


def config(root):
    folder = root / "configs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "backends.local.yaml").write_text("backends:\n  local:\n    api_key: " + SECRET + "\n", encoding="utf-8")


@pytest.mark.parametrize("value", [
    SECRET, {"id": SECRET}, {SECRET: "value"}, {"messages": [{"role": "user", "content": SECRET}]},
    {"messages": [{"role": "assistant", "reasoning_content": SECRET, "content": "answer"}]},
    {"tool_calls": [{"function": {"arguments": json.dumps({"key": SECRET})}}]},
    {"chosen": [{"content": SECRET}], "rejected": [{"content": "clean"}]},
    {"text": "prefix " + SECRET + " suffix"},
])
def test_credentials_in_any_training_field_or_mapping_key_are_blocked_without_echo(value):
    original = json.dumps(value, ensure_ascii=False)
    with pytest.raises(ValueError, match="^export_contains_configured_credential$") as error:
        ExportCredentialGuard([SECRET]).check_value(value)
    assert SECRET not in str(error.value)
    assert json.dumps(value, ensure_ascii=False) == original


def test_file_scan_crosses_byte_boundaries_and_checks_compressed_sidecars(tmp_path):
    file = tmp_path / "records.jsonl"
    file.write_bytes(b"x" * 65530 + SECRET.encode() + b"\n")
    original = file.read_bytes()
    guard = ExportCredentialGuard([SECRET])
    with pytest.raises(ValueError):
        guard.check_file(file)
    archive = tmp_path / "source_figures.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("caption.jsonl", json.dumps({"text": SECRET}))
    with pytest.raises(ValueError):
        guard.check_file(archive)
    assert file.read_bytes() == original


def test_explicit_environment_reference_is_resolved_without_network(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/backends.yaml").write_text("backends:\n  x:\n    api_key_env: TEST_EXPORT_CREDENTIAL\n", encoding="utf-8")
    monkeypatch.setenv("TEST_EXPORT_CREDENTIAL", SECRET)
    with pytest.raises(ValueError, match="export_contains_configured_credential"):
        ExportCredentialGuard.from_root(tmp_path).check_value({"thinking": SECRET})


def test_invalid_credential_configuration_fails_without_yaml_snippets(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/backends.yaml").write_text("backends: [" + SECRET, encoding="utf-8")
    with pytest.raises(ValueError, match="^export_credential_config_invalid$") as error:
        ExportCredentialGuard.from_root(tmp_path)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("fmt", ["chat", "llamafactory", "minimind", "all"])
def test_formal_release_never_commits_a_credential_and_does_not_drop_reasoning(tmp_path, fmt):
    config(tmp_path)
    sample = {"id": "original", "messages": [
        {"role": "user", "content": "Explain the procedure."},
        {"role": "assistant", "content": "A clean-looking answer.", "reasoning_content": "private " + SECRET}]}
    original = json.dumps(sample)
    with pytest.raises(ValueError, match="^export_contains_configured_credential$"):
        release_application(tmp_path).export_release([sample], fmt, tmp_path / "export", tag="blocked")
    assert json.dumps(sample) == original
    for manifest in (tmp_path / "export").glob("*/manifest.json"):
        assert json.loads(manifest.read_text(encoding="utf-8"))["status"] != "complete"
    for output in (tmp_path / "export").glob("*/*.jsonl"):
        assert SECRET not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("sidecar", ["corpus_path", "dpo_path"])
def test_sidecars_cannot_bypass_the_release_guard(tmp_path, sidecar):
    config(tmp_path)
    source = tmp_path / "sidecar.jsonl"
    source.write_text(json.dumps({"text": SECRET}), encoding="utf-8")
    sample = {"id": "one", "messages": [{"role": "user", "content": "Q"}, {"role": "assistant", "content": "A"}]}
    with pytest.raises(ValueError, match="^export_contains_configured_credential$"):
        release_application(tmp_path).export_release([sample], "minimind", tmp_path / "export", **{sidecar: source})
    assert not (tmp_path / "export").exists()
    assert SECRET in source.read_text(encoding="utf-8")


def test_native_workflow_scans_audit_records_too_and_never_publishes_the_package(tmp_path):
    config(tmp_path)
    shutil.copyfile(ROOT / "configs/preferences.yaml", tmp_path / "configs/preferences.yaml")
    source = tmp_path / "source.md"
    original_source = "Disconnect equipment power before inspecting cable connections. Record the maintenance result."
    source.write_text(original_source, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"], sample_count=1, settings_root=tmp_path)
    class NoCalls:
        model, usage = "unused", {}
        def chat(self, *args, **kwargs):
            raise AssertionError("Native CPT must not call a model")
    workflow = engine.Workflow(output, run_id, tmp_path, generator=NoCalls(), judge=NoCalls())
    clean_cpt = workflow.cpt
    def with_private_audit(unit):
        rows = clean_cpt(unit)
        assert any(row["status"] == "eligible" for row in rows)
        for row in rows:
            row["private_audit"] = {"observed_credential": SECRET}
        return rows
    workflow.cpt = with_private_audit
    state = workflow.execute()
    assert state["status"] == "failed"
    assert "export_contains_configured_credential" in json.dumps(state)
    assert SECRET not in json.dumps(state)
    manifest = engine.run_path(output, run_id) / "artifacts/manifest.json"
    assert not manifest.exists() or json.loads(manifest.read_text())["status"] != "complete"
    assert source.read_text(encoding="utf-8") == original_source


def test_old_cached_archive_cannot_bypass_a_new_current_credential_check(tmp_path, monkeypatch):
    run = tmp_path / "run"
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    payload = json.dumps({"text": SECRET}).encode()
    (artifacts / "cpt.jsonl").write_bytes(payload)
    manifest = {"status": "complete", "sha256": {"cpt.jsonl": hashlib.sha256(payload).hexdigest()}}
    (artifacts / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    cached = prepare_bundle(run)
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    for operation in [lambda: prepare_bundle(run), lambda: prepared_bundle(run), lambda: artifact_bytes(run, "cpt.jsonl", 10000)]:
        with pytest.raises(ValueError, match="^export_contains_configured_credential$"):
            operation()
    assert Path(cached["path"]).exists()  # originals and evidence are retained


def test_external_controller_response_does_not_send_a_configured_credential_to_an_agent(tmp_path):
    config(tmp_path)
    run_id = "a" * 32
    run = tmp_path / "data/output/workflows" / run_id
    run.mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({"id": run_id, "name": SECRET, "status": "completed"}), encoding="utf-8")
    (run / "recipe.json").write_text("{}", encoding="utf-8")
    request = {"protocol": "dataforge.control", "version": 1, "request_id": "inspect", "action": "runs.inspect", "params": {"run_id": run_id}}
    result = subprocess.run([sys.executable, "-m", "lib.control", "--root", str(tmp_path)], cwd=ROOT,
                            input=json.dumps(request), encoding="utf-8", capture_output=True, timeout=30)
    reply = json.loads(result.stdout)
    assert result.returncode == 2 and reply["error"]["code"] == "export_contains_configured_credential"
    assert SECRET not in result.stdout + result.stderr
    assert SECRET in (run / "state.json").read_text()
