"""Exercise the output package flow through Streamlit widgets."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent


def test_review_entry_only_exposes_supported_nonempty_artifacts():
    from lib.presentation.streamlit.package_page import _review_choices

    state = {"targets": ["orpo", "cpt", "dpo", "sft"]}
    manifest = {"counts": {"orpo": 3, "cpt": 0, "dpo": 2, "sft": 1}}
    assert _review_choices(state, manifest) == [
        ("sft", "SFT 数据调整"), ("dpo", "DPO 偏好优化"),
        ("orpo", "ORPO 偏好优化")]


def test_empty_workflow_state_shows_real_local_release(tmp_path, monkeypatch):
    from lib import workspace as ws

    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    source = tmp_path / "source"
    source.mkdir()
    workspace_id = ws.add_folder(source)
    folder = ws.out(workspace_id) / "export" / "published-one"
    folder.mkdir(parents=True)
    data = b'{"messages":[]}\n'
    (folder / "sft.jsonl").write_bytes(data)
    (folder / "quality.json").write_text('{"reviewed":1}', encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({
        "status": "complete", "format": "chat", "created_at": "2026-09-23T00:00:00+00:00",
        "sha256": {"sft.jsonl": hashlib.sha256(data).hexdigest()},
    }), encoding="utf-8")

    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace_id
    app.session_state["nav"] = "输出打包"
    app.run()

    assert not app.exception
    page = "\n".join(str(item.value) for item in app.get("html"))
    assert "暂无新的工作流训练包" in page
    assert "已有本地发布版本" in page
    assert "published-one" in page
    assert "准备第一个训练数据包" not in page
    assert any(str(folder.resolve()) in str(item.value) for item in app.code)
    assert any(item.label == "下载已校验文件" for item in app.download_button)


def test_post_bundle_check_rejects_changed_or_extra_files():
    from lib.presentation.streamlit.package_page import _verify_archive

    content = b'{"text":"verified"}\n'
    manifest = {"sha256": {"cpt.jsonl": hashlib.sha256(content).hexdigest()},
                "status": "complete"}

    def package(payload: bytes, extra: bool = False) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("cpt.jsonl", payload)
            if extra:
                archive.writestr("unlisted.txt", b"unexpected")
        return buffer.getvalue()

    _verify_archive(package(content), manifest)
    with pytest.raises(ValueError, match="文件校验失败"):
        _verify_archive(package(b"tampered"), manifest)
    with pytest.raises(ValueError, match="文件列表"):
        _verify_archive(package(content, extra=True), manifest)


def test_verified_workflow_outputs_can_be_prepared_for_download(tmp_path, monkeypatch):
    from lib import workspace as ws

    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    source = tmp_path / "source"
    source.mkdir()
    name = ws.add_folder(source)
    run_id = "c" * 32
    run = ws.out(name) / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    data = artifacts / "cpt.jsonl"
    quality = artifacts / "quality.json"
    data.write_text('{"text":"设备检查前应先断电。"}\n', encoding="utf-8")
    quality.write_text(json.dumps({"targets": {"cpt": {"eligible": 1, "total": 1, "reasons": {}}}}), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "CPT 输出验收", "status": "completed", "targets": ["cpt"],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
        # State is not the verified quality source. The page must read quality.json.
        "quality": {"targets": {"cpt": {"eligible": 1, "total": 100, "reasons": {}}}}, "usage": {},
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({
        "status": "complete", "run_id": run_id, "release_kind": "automatically_checked_candidate",
        "created_at": "2026-09-23T00:00:00+00:00", "sources": [], "counts": {"cpt": 1},
        "sha256": {"cpt.jsonl": hashlib.sha256(data.read_bytes()).hexdigest(),
                   "quality.json": hashlib.sha256(quality.read_bytes()).hexdigest()},
    }), encoding="utf-8")

    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "输出打包"
    app.run()
    assert not app.exception
    assert any(item.value == "输出打包" for item in app.title)
    assert any("包内容预览" in str(item.value) for item in app.get("html"))
    assert any("设备检查前应先断电" in str(item.value) for item in app.get("html"))
    assert any("合格产出率" in str(item.value) and "100%" in str(item.value)
               for item in app.get("html"))
    assert any('data-kind="zip"' in str(item.value) and "原生 JSONL" in str(item.value)
               for item in app.get("html"))
    assert any('data-ready="false"' in str(item.value) for item in app.get("html"))

    next(button for button in app.button if button.label == "生成并校验完整 ZIP").click().run()
    assert not app.exception
    package = app.session_state[f"verified-package:{run_id}"]["bytes"]
    assert isinstance(package, bytes) and package.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert {"cpt.jsonl", "quality.json", "manifest.json"} <= set(archive.namelist())
    assert any("已完成 · 产物校验通过" in str(item.value) for item in app.get("html"))
    assert any('data-ready="true"' in str(item.value) for item in app.get("html"))
    next(button for button in app.button if button.label == "进入当前任务的人工审核").click().run()
    assert not app.exception
    assert app.session_state["nav"] == "人工审核"
    assert app.session_state[f"review-mode:{name}"] == "CPT 语料审核"
    assert app.session_state["corpus-review-run"] == run_id

    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "needs_attention"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    app.session_state["nav"] = "输出打包"
    app.run()
    assert not app.exception
    assert any('data-attention="true"' in str(item.value) for item in app.get("html"))
    assert any("文件完整，质量需检查" in str(item.value) for item in app.get("html"))


def test_orpo_package_opens_its_own_human_review_queue(tmp_path, monkeypatch):
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
    pair = {
        "prompt": [{"role": "user", "content": "怎样安全检查设备？"}],
        "chosen": [{"role": "assistant", "content": "先断电并确认隔离。"}],
        "rejected": [{"role": "assistant", "content": "直接拆开检查。"}],
        "tools": [],
    }
    native = artifacts / "orpo.jsonl"
    quality = artifacts / "quality.json"
    native.write_text(json.dumps(pair, ensure_ascii=False) + "\n", encoding="utf-8")
    quality.write_text(json.dumps({"targets": {"orpo": {"eligible": 1, "total": 1}}}), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "ORPO 输出验收", "status": "completed", "targets": ["orpo"],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({
        "status": "complete", "run_id": run_id, "counts": {"orpo": 1},
        "sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (native, quality)},
    }), encoding="utf-8")

    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "输出打包"
    app.run()
    assert not app.exception
    assert any("ORPO 偏好优化" in str(item.value) for item in app.get("html"))
    next(button for button in app.button if button.label == "进入当前任务的人工审核").click().run()
    assert not app.exception
    assert app.session_state["nav"] == "人工审核"
    assert app.session_state[f"review-mode:{name}"] == "ORPO 偏好优化"
    assert app.session_state["preference-review-run:orpo"] == run_id
    assert any("偏好对比" in str(item.value) for item in app.get("html"))


def test_trl_sidecar_and_cached_zip_follow_verified_manifest(tmp_path, monkeypatch):
    from lib import workspace as ws
    from lib.presentation.streamlit.package_page import _sidecar_bytes

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
    native = artifacts / "sft.jsonl"
    trainer = artifacts / "trl_sft.jsonl"
    quality = artifacts / "quality.json"
    sample = {"messages": [{"role": "user", "content": "请解释<unsafe>设备步骤"},
                           {"role": "assistant", "content": "先断电。"}]}
    native.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
    trainer.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
    report = {"targets": {"sft": {"eligible": 1, "total": 1, "reasons": {}}},
              "trainer_exports": {"sft": {"status": "ready", "file": "trl_sft.jsonl",
                                          "summary": {"compatible": 1, "total": 1, "reasons": {}}}}}
    quality.write_text(json.dumps(report), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "SFT 输出验收", "status": "completed", "targets": ["sft"],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
        "quality": report,
    }), encoding="utf-8")
    manifest_file = artifacts / "manifest.json"

    def write_manifest(created_at: str):
        manifest = {"status": "complete", "run_id": run_id,
                    "created_at": created_at, "release_kind": "automatically_checked_candidate",
                    "sources": [], "counts": {"sft": 1}, "trainer_counts": {"sft": 1},
                    "sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in (native, trainer, quality)}}
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    manifest = write_manifest("2026-09-23T00:00:00+00:00")
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "输出打包"
    app.run()
    assert not app.exception
    assert any("TRL 训练器兼容性" in str(item.value) for item in app.get("html"))
    assert any("&lt;unsafe&gt;" in str(item.value) for item in app.get("html"))
    assert any("先断电" in str(item.value) for item in app.get("html"))

    next(button for button in app.button if button.label == "生成并校验完整 ZIP").click().run()
    assert not app.exception
    key = f"verified-package:{run_id}"
    package = app.session_state[key]["bytes"]
    assert _sidecar_bytes(package, "trl_sft.jsonl", manifest) == trainer.read_bytes()

    # A new valid manifest invalidates previously prepared bytes.
    native.write_text(json.dumps(sample, ensure_ascii=False) + "\n\n", encoding="utf-8")
    write_manifest("2026-09-23T00:01:00+00:00")
    app.run()
    assert not app.exception
    assert key not in app.session_state

    # A tampered file without a corresponding manifest update blocks the page and download.
    next(button for button in app.button if button.label == "生成并校验完整 ZIP").click().run()
    assert key in app.session_state
    native.write_text("tampered\n", encoding="utf-8")
    app.run()
    assert key not in app.session_state
    assert any("无法读取或校验任务产物" in str(error.value) for error in app.error)
