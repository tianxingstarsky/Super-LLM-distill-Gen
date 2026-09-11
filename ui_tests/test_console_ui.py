"""AppTest runs separately from the distilabel multiprocessing suite."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    from lib import review_center as rc, workspace as ws
    monkeypatch.delenv("DF_WORKSPACE", raising=False)
    root = tmp_path / "app"
    monkeypatch.setattr(ws, "ROOT", root)
    monkeypatch.setattr(ws, "REGISTRY_PATH", root / "data/workspaces.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", root / "data/workspaces")
    monkeypatch.setattr(ws, "CURRENT_PATH", root / "data/workspaces/current.json")
    monkeypatch.setattr(ws, "SEEDS_DIR", root / "data/seeds")
    monkeypatch.setattr(rc, "DB_PATH", tmp_path / "review.db")
    return root


def app():
    return AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=30).run()


def test_form_runs_readonly_diagnostics_twice():
    view = app()
    view.sidebar.radio[0].set_value("管线运行").run()
    next(w for w in view.selectbox if w.label == "任务").set_value("环境自检").run()
    for _ in range(2):
        next(w for w in view.button if w.label == "运行").click().run()
        job = view.session_state["job:default"]
        deadline = time.monotonic() + 20
        while job.snapshot()[0] is None and time.monotonic() < deadline:
            time.sleep(.1)
        view.run()
        assert job.snapshot()[0] == 0, job.snapshot()
        assert not view.exception
        assert any(w.value == "任务完成" for w in view.success)
        assert any("Local service" in w.value for w in view.code)


def test_sessions_choose_independent_workspaces(tmp_path):
    from lib import workspace as ws
    for name in ("alpha", "beta"):
        source = tmp_path / name
        source.mkdir()
        ws.add_folder(source, name=name)
    ws.set_current("alpha")
    a, b = app(), app()
    assert a.session_state["ws"] == "alpha"
    b.sidebar.selectbox[0].set_value("beta").run()
    a.run()
    assert a.session_state["ws"] == "alpha"
    assert b.session_state["ws"] == "beta"
    assert ws.current() == "alpha"
    assert "DF_WORKSPACE" not in os.environ
    a.sidebar.selectbox[0].set_value("default").run()
    assert a.session_state["ws"] == "default"
    assert not a.exception and not b.exception
    assert not ws.out("alpha").exists()
    assert not ws.out("beta").exists()


def test_backends_page_renders_without_exception():
    view = app()
    view.sidebar.radio[0].set_value("模型与密钥").run()
    assert not view.exception
    assert any(w.label == "后端名" for w in view.text_input)
    assert any("密钥来源" in [str(c) for c in w.value.columns] for w in view.dataframe)


def test_review_page_renders_markdown_and_editor():
    from lib import review_center as rc
    from lib.review import build_records
    rc.ensure_admin()
    sample = {"id": "ui-test", "messages": [
        {"role": "user", "content": "请检查 **Markdown**"},
        {"role": "assistant", "content": "## 标题\n- 正文\n```python\nprint(1)\n```", "reasoning_content": "先检查"},
    ]}
    rc.add_records("rollout_review", build_records([sample], {}))
    view = app()
    view.sidebar.radio[0].set_value("人工审核").run()
    assert not view.exception
    assert "Markdown 渲染" in "\n".join(str(c.value) for c in view.caption)
    labels = [w.label for w in view.button] + [w.label for w in view.expander]
    assert any(label.startswith("✏️ 编辑第") for label in labels)
    assert any("AI 按指令修改" in label for label in labels)
    next(w for w in view.button if w.label.startswith("✏️ 编辑第 2")).click().run()
    assert not view.exception
    assert any("源码视图" in w.value for w in view.info)


def test_open_existing_folder_dialog(tmp_path):
    from lib import workspace as ws
    source = tmp_path / "现有 数据文件夹"
    source.mkdir()
    data = source / "dialogue.jsonl"
    data.write_text(json.dumps({"conversations": [{"role": "user", "content": "现有文件"}, {"role": "assistant", "content": "只读预览"}]}, ensure_ascii=False), encoding="utf-8")
    before = data.read_bytes()
    view = app()
    assert all("新建工作区" not in w.label for w in view.expander)
    next(w for w in view.button if w.label == "打开已有文件夹").click().run()
    next(w for w in view.text_input if w.label == "已有文件夹路径").set_value(str(source))
    next(w for w in view.button if w.label == "打开文件夹").click().run()
    assert not view.exception
    identifier = view.session_state["ws"]
    assert ws.folder(identifier) == source
    assert ws.current() == "default"
    assert not ws.out(identifier).exists()
    view.sidebar.radio[0].set_value("数据预览").run()
    assert not view.exception
    assert any("file-" in w.value for w in view.caption)
    assert data.read_bytes() == before


def test_unavailable_folder_is_recoverable(tmp_path):
    from lib import workspace as ws
    source = tmp_path / "source"
    source.mkdir()
    ws.set_current(ws.add_folder(source))
    source.rename(tmp_path / "moved")
    view = app()
    assert not view.exception
    assert any("不可用" in w.value for w in view.warning)
    view.sidebar.selectbox[0].set_value("default").run()
    assert not view.exception
    assert not source.exists()


def test_gui_form_and_environment_isolation(monkeypatch):
    monkeypatch.setenv("DF_WORKSPACE", "default")
    view = app()
    view.sidebar.radio[0].set_value("管线运行").run()
    assert not view.exception
    assert any(w.label == "任务" for w in view.selectbox)
    assert all("命令参数" not in w.label for w in view.text_input)
    assert os.environ["DF_WORKSPACE"] == "default"
