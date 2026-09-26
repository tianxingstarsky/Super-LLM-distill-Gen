"""Exercise SFT review from the unified human-review workspace."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent


def test_reviewer_can_approve_and_release_sft_candidate(tmp_path, monkeypatch):
    from lib import workspace as ws

    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    source = tmp_path / "source"
    source.mkdir()
    name = ws.add_folder(source)
    run_id = "1" * 32
    run = ws.out(name) / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    row = {"messages": [{"role": "user", "content": "如何安全断电？"},
                        {"role": "assistant", "content": "先关闭电源总开关。"}]}
    sft = artifacts / "sft.jsonl"
    sft.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    records = artifacts / "sft.records.json"
    records.write_text(json.dumps([{"status": "eligible", **row, "kind": "document", "source_id": "manual",
                                    "location": 2, "evidence_level": "source_and_model_assessed",
                                    "quotes": ["断电步骤"]}], ensure_ascii=False), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "SFT 设备手册", "status": "completed", "targets": ["sft"],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({"status": "complete", "sha256": {
        sft.name: hashlib.sha256(sft.read_bytes()).hexdigest(),
        records.name: hashlib.sha256(records.read_bytes()).hexdigest(),
    }}), encoding="utf-8")
    from lib import review_management
    monkeypatch.setattr(review_management, "reviewer_identity", lambda: "admin")

    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "人工审核"
    app.session_state[f"review-mode:{name}"] = "SFT 数据调整"
    app.run()
    assert not app.exception
    assert any("人工审核 / 模型对齐" == item.value for item in app.title)
    assert any("SFT 数据调整" == item.value for item in app.subheader)
    overview = "".join(str(item.value) for item in app.get("html")
                       if isinstance(item.value, str) and 'class="df-review-overview"' in item.value)
    assert 'data-kind="sft"' in overview and '<span>1</span>' in overview
    assert 'data-kind="dpo"' in overview and 'data-kind="cpt"' in overview
    assert any("来源与自动质检证据" == item.label for item in app.expander)

    next(button for button in app.button if button.label == "通过并保存修订").click().run()
    assert not app.exception
    next(button for button in app.button if button.label == "生成已审核 SFT 版本").click().run()
    assert not app.exception
    release = app.session_state[f"sft-release:{run_id}"]
    assert isinstance(release, dict) and release["target"] == "sft"
    assert release["counts"]["approved"] == 1
    archive = run / "releases" / ".archives" / f"{release['id']}.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == release["sha256"]
    with zipfile.ZipFile(archive) as package:
        assert len(package.read("sft.jsonl").splitlines()) == 1
    assert not any(isinstance(value, bytes) for value in release.values())
    next(button for button in app.button if button.key == "review-overview-open-cpt").click().run()
    assert not app.exception
    assert app.session_state[f"review-mode:{name}"] == "CPT 语料审核"
