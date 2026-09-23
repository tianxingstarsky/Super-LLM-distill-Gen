"""The data library browses integrity-checked modern training artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent


def test_preview_offers_verified_agent_negative_sidecar():
    from lib.presentation.streamlit.dataset_preview_page import _preview_targets

    manifest = {"counts": {"agent": 2}, "negative_counts": {"agent": 1}}
    files = [{"name": "agent.jsonl"}, {"name": "agent.negative.jsonl"}]
    assert _preview_targets(manifest, files) == ["agent", "agent_negative"]


def test_data_preview_renders_verified_orpo_comparison(tmp_path, monkeypatch):
    from lib import workspace as ws

    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    source = tmp_path / "source"
    source.mkdir()
    name = ws.add_folder(source)
    run_id = "f" * 32
    run = ws.out(name) / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    pair = {
        "prompt": [{"role": "user", "content": "如何检查电源？"}],
        "chosen": [{"role": "assistant", "content": "先断电，再核对隔离状态。"}],
        "rejected": [{"role": "assistant", "content": "保持通电直接检查。"}],
    }
    native = artifacts / "orpo.jsonl"
    quality = artifacts / "quality.json"
    native.write_text(json.dumps(pair, ensure_ascii=False) + "\n", encoding="utf-8")
    quality.write_text(json.dumps({"targets": {"orpo": {"eligible": 1, "total": 1}}}), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "ORPO 预览验收", "status": "completed", "targets": ["orpo"],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({
        "status": "complete", "run_id": run_id, "counts": {"orpo": 1},
        "sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (native, quality)},
    }), encoding="utf-8")

    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "数据管理"
    app.session_state[f"data-view:{name}"] = "数据预览"
    app.run()
    assert not app.exception
    markup = "\n".join(str(item.value) for item in app.get("html"))
    assert "真实样本预览" in markup
    assert "如何检查电源？" in markup
    assert "先断电，再核对隔离状态。" in markup
    assert "保持通电直接检查。" in markup
    assert "SHA-256 通过" in markup

    native.write_text("tampered\n", encoding="utf-8")
    app.run()
    assert not app.exception
    assert any("产物校验失败" in str(error.value) for error in app.error)
    assert not any("保持通电直接检查。" in str(item.value) for item in app.get("html"))
