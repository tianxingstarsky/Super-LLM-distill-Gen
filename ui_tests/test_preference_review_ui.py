"""Exercise the preference review page through real Streamlit widgets."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent


def test_reviewer_can_approve_and_release_dpo_candidate(tmp_path, monkeypatch):
    from lib import workspace as ws

    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    folder = tmp_path / "source"
    folder.mkdir()
    name = ws.add_folder(folder)
    run_id = "b" * 32
    run = ws.out(name) / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    dpo = artifacts / "dpo.jsonl"
    dpo.write_text(json.dumps({
        "prompt": [{"role": "user", "content": "怎样检查设备？"}],
        "chosen": [{"role": "assistant", "content": "先断电，再检查线路。"}],
        "rejected": [{"role": "assistant", "content": "可以直接带电检查。"}],
        "tools": [],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "设备偏好候选", "status": "completed", "targets": ["dpo"],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({
        "status": "complete", "sha256": {"dpo.jsonl": hashlib.sha256(dpo.read_bytes()).hexdigest()},
    }), encoding="utf-8")
    from lib import review_management
    monkeypatch.setattr(review_management, "reviewer_identity", lambda: "admin")

    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "人工审核"
    app.session_state[f"review-mode:{name}"] = "DPO 偏好优化"
    app.run()
    assert not app.exception
    assert any(item.value == "人工审核 / 模型对齐" for item in app.title)

    next(button for button in app.button if button.label == "通过并保存修订").click().run()
    assert not app.exception
    next(button for button in app.button if button.label == "生成已审核 DPO 版本").click().run()
    assert not app.exception
    release = app.session_state[f"preference-release:{run_id}"]
    assert isinstance(release, dict) and release["target"] == "dpo"
    assert release["counts"]["approved"] == 1
    archive = run / "releases" / ".archives" / f"{release['id']}.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == release["sha256"]
    with zipfile.ZipFile(archive) as package:
        assert len(package.read("dpo.jsonl").splitlines()) == 1
    assert not any(isinstance(value, bytes) for value in release.values())
