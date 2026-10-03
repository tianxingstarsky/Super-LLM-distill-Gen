"""A queued node may not silently adopt a replaced shared endpoint."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from lib import llm_client
from lib.domain.backend_config import validate_credential_reference, validate_endpoint
from lib.infrastructure.training_workflow import Workflow, create_run, read_json, run_path


PREFERENCES = (Path(__file__).resolve().parents[1] / "configs" / "preferences.yaml").read_text(
    encoding="utf-8")


def _write_endpoint(root: Path, *, base_url="https://old.example/v1", api_format="chat",
                    models=("writer",), api_key="sk-top-secret", api_key_env=None):
    entry = {"base_url": base_url, "api_format": api_format, "models": list(models)}
    if api_key_env is None:
        entry["api_key"] = api_key
    else:
        entry["api_key_env"] = api_key_env
    (root / "configs" / "backends.yaml").write_text(
        yaml.safe_dump({"backends": {"shared": entry}}, sort_keys=False), encoding="utf-8")


def _run(tmp_path, *, api_format="chat"):
    root = tmp_path / "settings"
    folder = root / "configs"
    folder.mkdir(parents=True)
    (folder / "preferences.yaml").write_text(PREFERENCES, encoding="utf-8")
    _write_endpoint(root, api_format=api_format)
    output = tmp_path / "output"
    run_id = create_run(output, brief="Make service examples", targets=["sft"],
                        settings_root=root, node_models={"sft": {"generation": {
                            "backend": "shared", "model": "writer"}}})
    return root, output, run_id


def _fake_client(monkeypatch):
    constructed = []

    def make(**kwargs):
        constructed.append(dict(kwargs))
        return SimpleNamespace(client=SimpleNamespace(base_url=kwargs["base_url"]),
                               api_format=kwargs["api_format"], model=kwargs["model"],
                               usage={})

    monkeypatch.setattr(llm_client, "ChatClient", make)
    return constructed


def _client(root, output, run_id):
    run = Workflow(output, run_id, root)
    run.stage = "sft"
    return run.client("generation")


def test_submission_pins_safe_transport_and_never_persists_key_value(tmp_path, monkeypatch):
    root, output, run_id = _run(tmp_path)
    created = _fake_client(monkeypatch)
    recipe = read_json(run_path(output, run_id) / "recipe.json")
    pin = recipe["endpoint_pins"]["sft"]["generation"]
    assert pin["endpoint_origin"] == "https://old.example"
    assert pin["base_url_sha256"] == hashlib.sha256(b"https://old.example/v1").hexdigest()
    assert "https://old.example/v1" not in json.dumps(recipe)
    assert pin["api_format"] == "chat"
    assert pin["models"] == ["writer"]
    assert pin["model"] == "writer"
    assert pin["credential_ref"] == "inline"
    assert len(pin["config_sha256"]) == 64
    assert "sk-top-secret" not in json.dumps(recipe)
    client = _client(root, output, run_id)
    assert client.client.base_url == "https://old.example/v1"
    assert created[0]["api_format"] == "chat" and created[0]["model"] == "writer"


@pytest.mark.parametrize("change", [
    {"base_url": "https://new.example/v1"},
    {"base_url": "https://old.example/v2"},
    {"api_format": "responses"},
    {"api_format": "anthropic"},
    {"models": ("other",)},
    {"api_key_env": "OTHER_ACCOUNT_KEY"},
])
def test_changed_shared_endpoint_fails_before_client_or_request(tmp_path, monkeypatch, change):
    root, output, run_id = _run(tmp_path)
    created = _fake_client(monkeypatch)
    _write_endpoint(root, **change)
    run = Workflow(output, run_id, root)
    run.stage = "sft"
    with pytest.raises(ValueError, match="workflow_endpoint_changed_create_new_run"):
        run.client("generation")
    assert created == []


@pytest.mark.parametrize("original,replacement", [
    ("chat", "responses"), ("responses", "anthropic"), ("anthropic", "chat"),
])
def test_every_protocol_transition_fails_closed(tmp_path, monkeypatch, original, replacement):
    root, output, run_id = _run(tmp_path, api_format=original)
    created = _fake_client(monkeypatch)
    _write_endpoint(root, api_format=replacement)
    with pytest.raises(ValueError, match="workflow_endpoint_changed_create_new_run"):
        _client(root, output, run_id)
    assert created == []


def test_local_overlay_replacement_cannot_redirect_queued_run(tmp_path, monkeypatch):
    root, output, run_id = _run(tmp_path)
    created = _fake_client(monkeypatch)
    overlay = {"backends": {"shared": {"base_url": "https://overlay.example/v1",
                                       "api_format": "responses", "models": ["writer"]}}}
    (root / "configs" / "backends.local.yaml").write_text(
        yaml.safe_dump(overlay), encoding="utf-8")
    with pytest.raises(ValueError, match="workflow_endpoint_changed_create_new_run"):
        _client(root, output, run_id)
    assert created == []


def test_resume_checks_pin_again_and_new_run_adopts_new_endpoint(tmp_path, monkeypatch):
    root, output, run_id = _run(tmp_path)
    created = _fake_client(monkeypatch)
    assert _client(root, output, run_id).api_format == "chat"
    assert _client(root, output, run_id).api_format == "chat"
    _write_endpoint(root, base_url="https://new.example/v1", api_format="responses")
    with pytest.raises(ValueError, match="workflow_endpoint_changed_create_new_run"):
        _client(root, output, run_id)
    assert len(created) == 2
    newer = create_run(output, brief="New request", targets=["sft"], settings_root=root,
                       node_models={"sft": {"generation": {
                           "backend": "shared", "model": "writer"}}})
    assert _client(root, output, newer).api_format == "responses"
    assert created[-1]["base_url"] == "https://new.example/v1"


def test_key_rotation_at_same_credential_reference_is_allowed_without_secret_snapshot(
    tmp_path, monkeypatch,
):
    root, output, run_id = _run(tmp_path)
    created = _fake_client(monkeypatch)
    _write_endpoint(root, api_key="sk-rotated")
    _client(root, output, run_id)
    assert created[0]["api_key"] == "sk-rotated"
    recipe = read_json(run_path(output, run_id) / "recipe.json")
    assert "sk-top-secret" not in json.dumps(recipe)
    assert "sk-rotated" not in json.dumps(recipe)


@pytest.mark.parametrize("unsafe_url", [
    "https://user:secret@example.test/v1",
    "https://api.example.test/v1?token=secret",
    "https://api.example.test/v1#secret",
])
def test_unsafe_endpoint_url_is_rejected_before_recipe_write(tmp_path, unsafe_url):
    with pytest.raises(ValueError):
        validate_endpoint("shared", unsafe_url, ["writer"])
    root = tmp_path / "settings"
    folder = root / "configs"
    folder.mkdir(parents=True)
    (folder / "preferences.yaml").write_text(PREFERENCES, encoding="utf-8")
    _write_endpoint(root, base_url=unsafe_url)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="workflow_endpoint_url_or_protocol_invalid"):
        create_run(output, brief="Make service examples", targets=["sft"],
                   settings_root=root, node_models={"sft": {"generation": {
                       "backend": "shared", "model": "writer"}}})
    assert not output.exists()


def test_unsafe_url_added_after_submission_fails_before_client(tmp_path, monkeypatch):
    root, output, run_id = _run(tmp_path)
    created = _fake_client(monkeypatch)
    _write_endpoint(root, base_url="https://api.example.test/v1?token=secret")
    with pytest.raises(ValueError, match="workflow_endpoint_changed_create_new_run"):
        _client(root, output, run_id)
    assert created == []
    assert "secret" not in (run_path(output, run_id) / "recipe.json").read_text(encoding="utf-8")


def test_pasted_key_cannot_be_persisted_as_credential_reference(tmp_path):
    with pytest.raises(ValueError, match="api_key_env"):
        validate_credential_reference("sk-pasted-secret")
    root = tmp_path / "settings"
    folder = root / "configs"
    folder.mkdir(parents=True)
    (folder / "preferences.yaml").write_text(PREFERENCES, encoding="utf-8")
    _write_endpoint(root, api_key_env="sk-pasted-secret")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="workflow_endpoint_credential_ref_invalid"):
        create_run(output, brief="Make service examples", targets=["sft"],
                   settings_root=root, node_models={"sft": {"generation": {
                       "backend": "shared", "model": "writer"}}})
    assert not output.exists()


def test_legacy_recipe_without_endpoint_pin_can_resume(tmp_path, monkeypatch):
    root = tmp_path / "settings"
    (root / "configs").mkdir(parents=True)
    _write_endpoint(root)
    output = tmp_path / "output"
    run_id = create_run(output, brief="Earlier run", targets=["sft"],
                        node_models={"sft": {"generation": {
                            "backend": "shared", "model": "writer"}}})
    recipe = read_json(run_path(output, run_id) / "recipe.json")
    assert "endpoint_pins" not in recipe
    _fake_client(monkeypatch)
    assert _client(root, output, run_id).model == "writer"
