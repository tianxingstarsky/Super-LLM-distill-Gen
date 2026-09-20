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
    from lib import review_workspace
    # 每个 AppTest 拥有独立 runtime：清掉进程级 cache_resource，让 v2 组件在新 runtime 重新注册
    review_workspace._register_component.clear()
    return AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=30).run()


def review_envelope(view):
    """审核工作台 v2 组件信封：BidiComponent.proto.json（AppTest 不暴露为 widget）。"""
    nodes = view.get("bidi_component")
    assert len(nodes) == 1, f"期望 1 个 bidi_component，实际 {[n.type for n in nodes]}"
    proto = nodes[0].proto
    assert proto.component_name == "df_review_workspace"
    assert proto.isolate_styles is True
    assert proto.html_content and proto.js_content and proto.css_content  # 组件声明成功（文件齐备）
    envelope = json.loads(proto.json)
    assert envelope["scope"] and envelope["identity"]
    return proto, envelope


def seed_bare_session_state(**values):
    """AppTest 在测试进程求值 selectbox.format_func（无 ScriptRunContext）时走全局 mock
    session state；补上 webapp format_func 依赖的键，避免其 KeyError（仅测试侧垫片）。"""
    from streamlit.runtime.state.session_state_proxy import get_session_state
    state = get_session_state()
    for key, value in values.items():
        state[key] = value


def sample_record(sample_id="ui-test"):
    return {"id": sample_id, "model": "m", "finish_reason": "stop", "messages": [
        {"role": "user", "content": "请检查 **Markdown** 与 <script>alert(1)</script>"},
        {"role": "assistant", "content": "## 标题\n- 正文\n```python\nprint(1)\n```", "reasoning_content": "先检查"},
    ]}


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


def test_run_page_blocks_missing_required_input():
    """必填参数（如 doc2corpus --input）留空时提交被拦下并明确提示，不启动注定失败的任务。"""
    view = app()
    view.sidebar.radio[0].set_value("管线运行").run()
    next(w for w in view.selectbox if w.label == "任务").set_value("文档语料整理").run()
    assert any(w.label.endswith("（必填）") for w in view.text_input)  # 必填项有标记
    next(w for w in view.button if w.label == "运行").click().run()
    assert any("必填参数" in w.value for w in view.warning)
    assert "job:default" not in view.session_state  # 没有启动任务
    assert not view.exception


def test_preview_caches_samples_across_rerenders(tmp_path, monkeypatch):
    """预览页翻页/重渲染不重读整个 JSONL（路径+mtime 缓存）。"""
    from lib import quality

    calls = {"n": 0}
    real_read = quality.read_samples

    def counting(path):
        calls["n"] += 1
        return real_read(path)

    monkeypatch.setattr(quality, "read_samples", counting)
    seed_bare_session_state(ws="default")  # AppTest 在测试进程求值 format_func（依赖 ws）
    out = tmp_path / "app" / "data" / "output"
    out.mkdir(parents=True)
    (out / "rollout_samples.jsonl").write_text(
        "\n".join(json.dumps(sample_record(f"cache-{i}"), ensure_ascii=False) for i in (1, 2)) + "\n",
        encoding="utf-8")
    view = app()
    view.sidebar.radio[0].set_value("数据预览").run()
    assert calls["n"] == 1  # 首次渲染读取一次
    view.number_input[0].set_value(2).run()  # 翻到第 2 条：重渲染走缓存
    assert calls["n"] == 1
    assert not view.exception
    assert any(w.value == 2 for w in view.number_input)


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


def test_review_workspace_v2_envelope_has_rendered_markdown():
    """日常审核只在渲染后的信封里给出 Markdown HTML，不再有整页编辑器。"""
    from lib import review_center as rc
    from lib.review import build_records

    rc.ensure_admin()
    rc.add_records("rollout_review", build_records([sample_record()], {}))
    view = app()
    view.sidebar.radio[0].set_value("人工审核").run()
    assert not view.exception

    _proto, envelope = review_envelope(view)
    assert envelope["scope"] == "default:rollout_review:admin"
    assert envelope["record"]["sample_id"] == "ui-test"
    messages = envelope["record"]["messages"]
    user_html = messages[0]["content"]["html"]
    assistant_html = messages[1]["content"]["html"]
    assert "<strong>Markdown</strong>" in user_html           # Markdown 已渲染
    assert "&lt;script&gt;" in user_html and "<script>" not in user_html  # 不可信 HTML 转义
    assert "<h2>标题</h2>" in assistant_html and "<li>正文</li>" in assistant_html
    assert '<code class="language-python">' in assistant_html
    assert "<p>先检查</p>" in messages[1]["reasoning_content"]["html"]


def test_daily_review_avoids_sample_files_import_and_old_editor(monkeypatch):
    """日常连续审核不得读样本文件/触发导入，也不得再出现旧版编辑器与 AI 展开器。"""
    from lib import review_center as rc, workspace as ws
    from lib.review import build_records

    rc.ensure_admin()
    rc.add_records("rollout_review", build_records([sample_record()], {}))
    # 让样本文件真实存在：若日常审核误走“导入/预览”路径，read_samples 必然被调用
    out = ws.out("default")
    out.mkdir(parents=True, exist_ok=True)
    (out / "rollout_samples.jsonl").write_text(json.dumps(sample_record(), ensure_ascii=False) + "\n",
                                               encoding="utf-8")
    calls = {"read": 0, "push": 0}

    def _read_spy(*_args, **_kwargs):
        calls["read"] += 1
        raise AssertionError("日常审核不应读取样本文件")

    monkeypatch.setattr("lib.quality.read_samples", _read_spy)
    monkeypatch.setattr("lib.review.push_samples",
                        lambda *_args, **_kwargs: calls.__setitem__("push", calls["push"] + 1))

    view = app()
    view.sidebar.radio[0].set_value("人工审核").run()
    assert not view.exception
    review_envelope(view)  # 组件仍正常声明/渲染
    assert calls == {"read": 0, "push": 0}
    labels = [w.label for w in view.button] + [w.label for w in view.expander]
    assert not any(label.startswith("✏️ 编辑第") for label in labels)   # 旧版全量编辑器
    assert not any("AI 按指令修改" in label for label in labels)         # 旧版 AI 展开器
    assert not any("Markdown 渲染" in str(c.value) for c in view.caption)
    assert not any("源码视图" in w.value for w in view.info)
    assert not any(w.label == "样本文件" for w in view.selectbox)
    assert not view.get("component_instance")                           # 不再注入旧 v1 编辑器


def test_review_management_import_writes_isolated_db_only():
    """导入入口走管理页；样本入库到隔离 DB，源文件只读且不触发日常审核。"""
    from lib import review_center as rc, workspace as ws

    rc.ensure_admin()
    out = ws.out("default")
    out.mkdir(parents=True, exist_ok=True)
    source = out / "rollout_samples.jsonl"
    source.write_text(json.dumps(sample_record("imp-1"), ensure_ascii=False) + "\n", encoding="utf-8")
    before = source.read_bytes()

    view = app()
    view.sidebar.radio[0].set_value("人工审核").run()
    view.session_state["review-management"] = "import"
    view.run()
    assert not view.exception
    assert any(t.value == "导入待审样本" for t in view.title)
    assert any(w.label == "样本文件" for w in view.selectbox)      # 导入页才读文件
    assert rc.queue_page("rollout_review", "admin")["total"] == 0

    seed_bare_session_state(ws="default")  # 点击触发 AppTest 重算 format_func（依赖 ws）
    next(w for w in view.button if w.label == "将所选样本加入待审").click().run()
    assert not view.exception
    assert any("样本已加入待审" in w.value for w in view.success)
    page = rc.queue_page("rollout_review", "admin")
    assert page["total"] == 1 and page["items"][0]["sample_id"] == "imp-1"
    assert source.read_bytes() == before                            # 原始文件只读


def test_review_identity_scopes_queue_per_credential():
    """身份由 API 密钥决定；同一数据集下不同身份的待审计数互相独立。"""
    from lib import review_center as rc
    from lib.review import build_records

    rc.ensure_admin()
    reviewer_key = rc.create_user("reviewer1", api_key="agent.ui-reviewer-1")
    rc.add_records("rollout_review", build_records([sample_record("scope-1"), sample_record("scope-2")], {}))
    first = rc.pending("rollout_review", "reviewer1", batch=10)[0]
    rc.submit("rollout_review", "reviewer1", [{
        "record_id": first["record_id"], "decision": "keep", "reason": "测试票",
        "model": "human", "sample_hash": first["sample_hash"]}])

    admin_view = app()
    admin_view.sidebar.radio[0].set_value("人工审核").run()
    assert not admin_view.exception
    _proto, admin_env = review_envelope(admin_view)
    assert admin_env["identity"] == "admin" and admin_env["scope"] == "default:rollout_review:admin"
    assert admin_env["queue"]["pending"] == 2 and admin_env["queue"]["reviewed"] == 0

    user_view = app()
    user_view.session_state["review-auth-key"] = reviewer_key
    user_view.sidebar.radio[0].set_value("人工审核").run()
    assert not user_view.exception
    _proto, user_env = review_envelope(user_view)
    assert user_env["identity"] == "reviewer1" and user_env["scope"] == "default:rollout_review:reviewer1"
    assert user_env["queue"]["pending"] == 1 and user_env["queue"]["reviewed"] == 1


def test_review_management_identity_and_denial_paths(tmp_path):
    """管理页展示密钥身份；未授权数据集只回执提示，坏密钥走页面级守卫。"""
    from lib import review_center as rc, workspace as ws

    rc.ensure_admin()
    reviewer_key = rc.create_user("reviewer1", api_key="agent.ui-reviewer-1")
    outsider_key = rc.create_user("outsider", api_key="agent.ui-outsider-1")

    settings = app()
    settings.session_state["review-auth-key"] = reviewer_key
    settings.sidebar.radio[0].set_value("人工审核").run()
    settings.session_state["review-management"] = "settings"
    settings.run()
    assert not settings.exception
    assert any("审核身份" in t.value for t in settings.title)
    assert any("当前身份：**reviewer1**" in str(w.value) for w in settings.markdown)
    assert any("密钥认证" in str(w.value) for w in settings.markdown)
    assert any(w.label == "本审核中心的个人 API 密钥" for w in settings.text_input)
    assert any(w.label == "退出个人身份，返回本机管理入口" for w in settings.button)

    import_page = app()
    import_page.session_state["review-auth-key"] = reviewer_key
    import_page.sidebar.radio[0].set_value("人工审核").run()
    import_page.session_state["review-management"] = "import"
    import_page.run()
    assert not import_page.exception
    assert any("导入由本机管理员执行" in w.value for w in import_page.info)  # 非管理员不导入

    # 未授权数据集：init_db 只为 rollout_review 自动授权，alpha 数据集对 outsider 无权 →
    # render_workspace 上抛 PermissionError，webapp 走身份守卫（不渲染组件）
    source = tmp_path / "outsider-data"
    source.mkdir()
    ws.add_folder(source, name="alpha")
    ws.set_current("alpha")
    denied = app()
    denied.session_state["review-auth-key"] = outsider_key
    denied.sidebar.radio[0].set_value("人工审核").run()
    assert not denied.exception
    assert any("没有此数据集的权限" in w.value for w in denied.error)
    assert not denied.get("bidi_component")

    bad_key = app()
    bad_key.session_state["review-auth-key"] = "agent.not-a-real-key"
    bad_key.sidebar.radio[0].set_value("人工审核").run()
    assert not bad_key.exception
    assert any("没有此数据集的权限" in w.value for w in bad_key.error)


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
