"""A quality page must describe verified workflow evidence and reject tampered artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent


def test_data_quality_shows_verified_targets_and_rejects_tampering(tmp_path, monkeypatch):
    from lib import workspace as ws

    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    source = tmp_path / "source"
    source.mkdir()
    name = ws.add_folder(source)
    run_id = "d" * 32
    run = ws.out(name) / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    data = artifacts / "orpo.jsonl"
    data.write_text(json.dumps({"prompt": "设备检查", "chosen": "先断电", "rejected": "直接触碰"},
                               ensure_ascii=False) + "\n", encoding="utf-8")
    report = artifacts / "quality.json"
    report.write_text(json.dumps({
        "targets": {"orpo": {"eligible": 1, "total": 2,
                               "reasons": {"preference_evidence_missing": 1}}},
        "input_issues": [{"source_name": "设备手册.pdf", "reason": "potential_secret"}],
    }, ensure_ascii=False), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "ORPO 质量验收", "status": "completed", "targets": ["orpo"],
        "created_at": "2026-09-23T00:00:00+00:00",
        "quality": {"targets": {"orpo": {"eligible": 0, "total": 0}}},
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({
        "status": "complete", "run_id": run_id, "counts": {"orpo": 1},
        "sources": ["设备手册.pdf"],
        "sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (data, report)},
    }), encoding="utf-8")

    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "数据管理"
    app.session_state[f"data-view:{name}"] = "质量报告"
    app.run()
    assert not app.exception
    markup = "\n".join(str(item.value) for item in app.get("html"))
    assert "逐目标质量" in markup
    assert "ORPO 偏好对" in markup
    assert "1 / 2 条候选通过" in markup
    assert "preference_evidence_missing" in markup
    assert "隔离输入" in markup
    assert app.get("progress")

    original_data = data.read_bytes()
    data.write_text("tampered\n", encoding="utf-8")
    app.run()
    assert not app.exception
    assert any("质量证据校验失败" in str(error.value) for error in app.error)
    assert not any("1 / 2 条候选通过" in str(item.value) for item in app.get("html"))

    data.write_bytes(original_data)
    report.write_text("{}", encoding="utf-8")
    app.run()
    assert not app.exception
    assert any("质量证据校验失败" in str(error.value) for error in app.error)
