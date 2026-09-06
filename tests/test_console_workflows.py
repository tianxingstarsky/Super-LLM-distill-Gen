"""Exercise the actual Streamlit form and job runner without paid model calls."""
from __future__ import annotations

import json
from pathlib import Path
import time
import pytest

ROOT = Path(__file__).resolve().parent.parent
def test_cli_default_workspace_override_does_not_leak(tmp_path, monkeypatch):
    from lib import cli, workspace as ws
    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "data/workspaces")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("DF_WORKSPACE", "alpha")
    monkeypatch.setattr("sys.argv", ["df", "workspace", "status", "--ws", "default"])
    assert cli.main() == 0
    assert cli.OUT_DIR == tmp_path / "data/output"
def test_missing_corpus_input_preserves_existing_output(tmp_path, monkeypatch):
    from lib import cli
    from types import SimpleNamespace
    existing = tmp_path / "corpus.jsonl"
    existing.write_text('{"text":"preserve me"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        cli.cmd_doc2corpus(SimpleNamespace(input=str(tmp_path / "missing"), out=str(existing), chunk_size=None, overlap=None))
    assert existing.read_text(encoding="utf-8") == '{"text":"preserve me"}\n'
