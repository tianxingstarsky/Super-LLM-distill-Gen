"""UI 套件：Streamlit AppTest 与 distilabel multiprocessing 不能共用进程，独立运行。
（主套件 pytest.ini 已 ignore tests/ui；CI 分两步跑）"""
from __future__ import annotations
import os
import time

from streamlit.testing.v1 import AppTest

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent


def test_form_runs_readonly_diagnostics_twice(monkeypatch):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("管线运行").run()
    next(w for w in app.selectbox if w.label == "任务").set_value("环境自检").run()
    for _ in range(2):
        next(w for w in app.button if w.label == "运行").click().run()
        job = app.session_state["job:default"]
        deadline = time.monotonic() + 20
        while job.snapshot()[0] is None and time.monotonic() < deadline:
            time.sleep(.1)
        app.run()
        assert job.snapshot()[0] == 0, job.snapshot()
        assert not app.exception
        assert any(w.value == "任务完成" for w in app.success)
        assert any("Local service" in w.value for w in app.code)


def test_sessions_choose_independent_workspaces(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from lib import workspace as ws
    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "data/workspaces")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "data/workspaces/current.json")
    ws.out("alpha")
    ws.out("beta")
    a = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=20).run()
    b = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=20).run()
    a.sidebar.selectbox[0].set_value("alpha").run()
    b.sidebar.selectbox[0].set_value("beta").run()
    a.run()
    assert a.session_state["ws"] == "alpha"
    assert b.session_state["ws"] == "beta"
    import os
    assert "DF_WORKSPACE" not in os.environ  # 会话选择不污染全局环境
    a.sidebar.selectbox[0].set_value("default").run()
    assert a.session_state["ws"] == "default"
    assert not a.exception and not b.exception



def test_backends_page_renders_without_exception():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("模型与密钥").run()
    assert not app.exception
    assert any(w.label == "后端名" for w in app.text_input)
    assert any("密钥来源" in [str(c) for c in w.value.columns] for w in app.dataframe)


def test_review_page_renders_bubbles_and_editor():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("人工审核").run()
    assert not app.exception
    # 选最新一条待审（带结构化 payload 的记录在队尾）
    selector = next(w for w in app.selectbox if w.label == "待审样本")
    selector.set_value(selector.options[-1]).run()
    assert not app.exception
    captions = "\n".join(str(c.value) for c in app.caption)
    assert "Markdown 渲染" in captions                          # 默认渲染视图提示
    labels = [w.label for w in app.button] + [w.label for w in app.expander]
    assert any(l.startswith("✏️ 编辑第") for l in labels)         # 渲染视图内的编辑入口
    assert any("AI 按指令修改" in l for l in labels)             # AI 临时修订面板
    # 源码视图（组件 iframe）的进入/保存/取消往返由浏览器 E2E 覆盖
