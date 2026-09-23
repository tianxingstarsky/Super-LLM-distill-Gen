"""Exercise the CPT review flow through the actual Streamlit page."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent


def test_reviewer_can_approve_and_release_cpt_candidate(tmp_path, monkeypatch):
    from lib import workspace as ws

    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    source = tmp_path / "source"
    source.mkdir()
    name = ws.add_folder(source)
    run_id = "e" * 32
    run = ws.out(name) / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    cpt = artifacts / "cpt.jsonl"
    cpt.write_text('{"text":"设备检查前先断电。"}\n', encoding="utf-8")
    records = artifacts / "cpt.records.json"
    records.write_text(json.dumps([{"status": "eligible", "text": "设备检查前先断电。", "kind": "document",
                                    "source_id": "guide", "location": 1, "evidence_level": "source_text"}],
                                  ensure_ascii=False), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "CPT 设备手册", "status": "completed", "targets": ["cpt"],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({"status": "complete", "sha256": {
        cpt.name: hashlib.sha256(cpt.read_bytes()).hexdigest(),
        records.name: hashlib.sha256(records.read_bytes()).hexdigest(),
    }}), encoding="utf-8")
    from lib import review_management
    monkeypatch.setattr(review_management, "reviewer_identity", lambda: "admin")

    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "人工审核"
    app.session_state[f"review-mode:{name}"] = "CPT 语料审核"
    app.run()
    assert not app.exception
    assert any(item.value == "人工审核 / 模型对齐" for item in app.title)
    evidence = [item.value for item in app.get("html") if isinstance(item.value, str)]
    assert any("来源与质检证据" in value for value in evidence)
    assert any("guide" in value and "source_text" in value for value in evidence)

    next(button for button in app.button if button.label == "通过并保存修订").click().run()
    assert not app.exception
    next(button for button in app.button if button.label == "生成已审核 CPT 版本").click().run()
    assert not app.exception
    assert isinstance(app.session_state[f"corpus-release:{run_id}"], bytes)
