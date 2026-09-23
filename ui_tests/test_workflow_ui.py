"""Real Streamlit forms; run separately from distilabel multiprocessing tests."""
from pathlib import Path
import hashlib
import json
import re

from streamlit.testing.v1 import AppTest

from lib.domain.workflow_graph import execution_graph
from lib.workflow import Workflow, create_run


ROOT = Path(__file__).resolve().parent.parent


def setup_workspace(tmp_path, monkeypatch):
    from lib import workspace as ws
    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    folder = tmp_path / "sources"
    folder.mkdir()
    source = folder / "guide.txt"
    source.write_text("操作前先断电，再检查线路。", encoding="utf-8")
    name = ws.add_folder(folder)
    return ws, name, source


def test_workbench_empty_and_completed_run_visible_after_refresh(tmp_path, monkeypatch):
    ws, name, source = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()
    assert not app.exception
    assert any("数据生成工作台" == item.value for item in app.title)
    assert any("尚无运行记录" in item.value for item in app.info)
    rid = create_run(ws.out(name), sources=[source], targets=["cpt"], name="文档自动验收")
    Workflow(ws.out(name), rid, ROOT).execute()
    app.run()
    assert not app.exception
    assert any("文档自动验收" == item.value for item in app.subheader)
    assert any("所选目标已完成" in item.value for item in app.success)
    node_buttons = [item for item in app.button if item.key and item.key.startswith(f"flow-node:{rid}:")]
    assert len(node_buttons) == 9
    assert not any(item.label == "查看工作流节点" for item in app.selectbox)
    assert app.session_state[f"workflow-stage:{rid}"] == "package"
    overview = [item.value for item in app.get("html") if isinstance(item.value, str)
                and 'class="df-run-overview"' in item.value]
    assert overview and "已完成节点 <strong>3 / 3</strong>" in overview[0]
    routes = [item.value for item in app.get("html") if isinstance(item.value, str)
              and 'class="df-dag-canvas"' in item.value]
    assert routes and 'data-from="ingest" data-to="cpt"' in routes[0]
    assert 'data-from="cpt" data-to="package"' in routes[0]
    assert 'data-stage="sft"' not in routes[0]
    assert any("下载本次训练数据与质量证据" in item.label for item in app.download_button)
    assert any("CPT 预训练语料 · 样本预览" == item.label for item in app.expander)
    assert any("来源与配方" in item.label for item in app.tabs)
    next(item for item in node_buttons if item.key.endswith(":cpt")).click().run()
    assert not app.exception
    assert app.session_state[f"workflow-stage:{rid}"] == "cpt"
    node_logs = [item.value for item in app.get("html") if isinstance(item.value, str)
                 and '<div class="df-run-log">' in item.value]
    assert node_logs and "节点处理完成" in node_logs[0]
    assert "run_finished" not in node_logs[0]
    # A fresh browser session discovers the same persistent run.
    other = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    other.session_state["ws"] = name
    other.session_state["nav"] = "自动工作流"
    other.run()
    assert not other.exception
    assert any("文档自动验收" == item.value for item in other.subheader)


def test_create_button_wires_exact_persisted_run_to_job(tmp_path, monkeypatch):
    ws, name, source = setup_workspace(tmp_path, monkeypatch)
    from lib.console_jobs import Job
    commands = []
    monkeypatch.setattr(Job, "start", lambda self: commands.append(self.command))
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()
    assert not app.exception
    inputs = {widget.label: widget for widget in app.multiselect}
    inputs["训练目标"].set_value(["cpt", "multiturn"]).run()
    assert not app.exception
    inputs = {widget.label: widget for widget in app.multiselect}
    inputs["或选择当前文件夹内的来源"].set_value([str(source)])
    text_inputs = {widget.label: widget for widget in app.text_input}
    text_inputs["JEV 打分后端（留空使用专用槽位）"].set_value("local_gpu")
    text_inputs["JEV 打分模型（留空使用专用槽位）"].set_value("reviewer-model")
    next(widget for widget in app.number_input if widget.label == "每段对话轮数").set_value(5)
    next(b for b in app.button if b.label == "开始自动生成").click().run()
    assert not app.exception
    assert commands and commands[0][:3] == ("workflow", "--action", "resume")
    run_id = app.session_state[f"workflow-selected:{name}"]
    assert commands[0][-1] == run_id
    assert app.session_state["nav"] == "任务管理"
    assert app.session_state[f"task-view:{name}"] == "数据工作流"
    assert app.session_state[f"task-center-run:{name}"] == run_id
    assert any(item.key == f"flow-node:{run_id}:ingest" for item in app.button)
    assert not any(item.label == "开始自动生成" for item in app.button)
    from lib.workflow import read_json, run_path
    recipe = read_json(run_path(ws.out(name), run_id) / "recipe.json")
    assert recipe["jev_backend"] == "local_gpu" and recipe["jev_model"] == "reviewer-model"
    assert recipe["conversation_turns"] == 5


def test_target_group_cards_and_detailed_picker_share_state(tmp_path, monkeypatch):
    _, name, _ = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()

    target_picker = next(widget for widget in app.multiselect if widget.label == "训练目标")
    target_picker.set_value(["orpo"]).run()
    assert not app.exception
    rendered = "".join(str(node.value) for node in app.get("html"))
    assert any(button.key == "workbench-target-card-preference-on" for button in app.button)
    assert any(button.key == "workbench-target-card-cpt-off" for button in app.button)
    assert "已选目标 1" in rendered and "ORPO 偏好对" in rendered
    assert "SFT 中间步骤" in rendered

    next(button for button in app.button if button.key == "workbench-target-card-preference-on").click().run()
    assert not app.exception
    assert set(next(widget for widget in app.multiselect if widget.label == "训练目标").value) == {
        "orpo", "dpo", "rlaif"}
    next(button for button in app.button if button.key == "workbench-target-card-preference-on").click().run()
    assert not app.exception
    assert next(widget for widget in app.multiselect if widget.label == "训练目标").value == []

    next(widget for widget in app.multiselect if widget.label == "训练目标").set_value(["agent", "gsm8k"]).run()
    assert not app.exception
    rendered = "".join(str(node.value) for node in app.get("html"))
    assert "已选目标 2" in rendered
    assert "Agent 验证轨迹" in rendered and "基础算术（GSM8K 格式）" in rendered
    assert "SFT 中间步骤" not in rendered


def test_target_plan_preview_follows_current_graph_edges(tmp_path, monkeypatch):
    _, name, _ = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()
    assert not app.exception

    def assert_preview(targets):
        nodes, edges = execution_graph(targets)
        markup = [str(item.value) for item in app.get("html")]
        summary = next(value for value in markup if 'class="df-wb-plan"' in value)
        detail = next(value for value in markup if 'class="df-wb-plan-detail"' in value)
        assert tuple(re.findall(r'data-stage="([^"]+)"', summary)) == nodes
        assert tuple(re.findall(r'data-from="([^"]+)" data-to="([^"]+)"', detail)) == edges
        assert "各阶段仍按顺序执行" in detail
        assert f"{len(nodes)} 个阶段 · {len(edges)} 条数据依赖" in summary
        return summary, detail

    assert_preview(["cpt", "sft", "orpo", "dpo"])
    picker = next(widget for widget in app.multiselect if widget.label == "训练目标")
    picker.set_value(["orpo"]).run()
    assert not app.exception
    summary, detail = assert_preview(["orpo"])
    assert 'data-stage="sft" data-intermediate="true"' in summary
    assert "SFT 中间步骤" in summary
    assert 'data-from="sft" data-to="package"' not in detail

    next(widget for widget in app.multiselect if widget.label == "训练目标").set_value(
        ["agent", "gsm8k"]).run()
    assert not app.exception
    summary, detail = assert_preview(["agent", "gsm8k"])
    assert 'data-stage="sft"' not in summary
    assert 'data-from="agent" data-to="gsm8k"' not in detail

    next(widget for widget in app.multiselect if widget.label == "训练目标").set_value([]).run()
    assert not app.exception
    markup = [str(item.value) for item in app.get("html")]
    assert any('class="df-wb-plan df-wb-plan-empty"' in value for value in markup)
    assert not any('class="df-wb-plan-detail"' in value for value in markup)


def test_source_mode_switches_to_open_brief_without_file_controls(tmp_path, monkeypatch):
    _, name, _ = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()
    source_mode = next(widget for widget in app.segmented_control
                       if widget.label == "选择来源类型")
    source_mode.set_value("开放需求").run()

    assert not app.exception
    assert any(widget.label == "开放性需求" for widget in app.text_area)
    assert not any(widget.label == "或选择当前文件夹内的来源" for widget in app.multiselect)


def test_run_inspector_keeps_resume_and_stop_controls(tmp_path, monkeypatch):
    ws, name, source = setup_workspace(tmp_path, monkeypatch)
    run_id = create_run(ws.out(name), sources=[source], targets=["cpt"], name="等待恢复的工作流")
    from lib.console_jobs import Job
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver

    commands = []
    monkeypatch.setattr(Job, "start", lambda self: commands.append(self.command))
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()
    assert not app.exception
    overview = [item.value for item in app.get("html") if isinstance(item.value, str)
                and 'class="df-run-overview"' in item.value]
    assert overview and "已完成节点 <strong>0 / 3</strong>" in overview[0]
    sft_node = next(item for item in app.button if item.key == f"flow-node:{run_id}:sft")
    assert "已跳过" in sft_node.label
    next(item for item in app.button if item.label == "继续执行 / 从断点重试").click().run()
    assert commands and commands[-1][-1] == run_id

    monkeypatch.setattr(FilesystemWorkflowDriver, "is_active", lambda self, _run_id: True)
    app.run()
    assert not app.exception
    next(item for item in app.button if item.label == "停止后续步骤").click().run()
    assert (ws.out(name) / "workflows" / run_id / "cancel.json").exists()


def test_workflow_navigation_from_overview(tmp_path, monkeypatch):
    _, name, _ = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.run()
    next(b for b in app.button if b.label == "进入数据生成工作台 →").click().run()
    assert not app.exception
    assert app.session_state["nav"] == "自动工作流"


def test_verified_workflow_sft_artifact_is_available_in_data_preview(tmp_path, monkeypatch):
    ws, name, _ = setup_workspace(tmp_path, monkeypatch)
    run_id = "a" * 32
    run = ws.out(name) / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    sft = artifacts / "sft.jsonl"
    sft.write_text(json.dumps({"id": "review-me", "messages": [
        {"role": "user", "content": "怎样检查设备？"},
        {"role": "assistant", "content": "先断电，再检查线路。"},
    ]}, ensure_ascii=False) + "\n", encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "已完成的 SFT", "status": "completed", "targets": ["sft"],
        "created_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({
        "status": "complete", "counts": {"sft": 1},
        "sha256": {sft.name: hashlib.sha256(sft.read_bytes()).hexdigest()},
    }), encoding="utf-8")

    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "数据预览"
    app.run()

    assert not app.exception
    picker = next(widget for widget in app.selectbox if widget.label == "训练目标")
    assert any("SFT" in str(option) for option in picker.options)
    markup = "\n".join(str(item.value) for item in app.get("html"))
    assert "先断电，再检查线路。" in markup
    assert "SHA-256 通过" in markup


def test_preference_review_page_explains_empty_queue(tmp_path, monkeypatch):
    _, name, _ = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "人工审核"
    app.session_state[f"review-mode:{name}"] = "DPO 偏好优化"
    app.run()

    assert not app.exception
    assert any("没有通过产物校验的 DPO 工作流" in item.value for item in app.info)
