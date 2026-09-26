"""Real Streamlit forms; run separately from distilabel multiprocessing tests."""
from pathlib import Path
import hashlib
import json
import re

from streamlit.testing.v1 import AppTest

from lib.domain.workflow_graph import execution_graph
from lib.workflow import Workflow, create_run


ROOT = Path(__file__).resolve().parent.parent


def canvas(app, key):
    return next(json.loads(item.proto.json_args)["spec"] for item in app.get("component_instance")
                if json.loads(item.proto.json_args).get("key") == key)


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
    route = canvas(app, f"live-canvas:{rid}")
    assert [node["id"] for node in route["nodes"]] == ["ingest", "cpt", "package"]
    assert not any(item.label == "查看工作流节点" for item in app.selectbox)
    assert app.session_state[f"workflow-stage:{rid}"] == "package"
    overview = [item.value for item in app.get("html") if isinstance(item.value, str)
                and 'class="df-run-overview"' in item.value]
    assert overview and "已完成节点 <strong>3 / 3</strong>" in overview[0]
    assert route["edges"] == [["ingest", "cpt"], ["cpt", "package"]]
    assert any("下载本次训练数据与质量证据" in item.label for item in app.download_button)
    assert any("CPT 预训练语料 · 样本预览" == item.label for item in app.expander)
    assert any("来源与配方" in item.label for item in app.tabs)
    app.session_state[f"live-canvas:{rid}"] = {"node": "cpt", "serial": "click-1"}
    app.run()
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
    assert not any("留空使用" in widget.label for widget in app.text_input)
    for widget in app.number_input:
        if widget.label == "候选样本规模":
            widget.set_value(50000)
        elif widget.label == "并发请求上限":
            widget.set_value(8)
        elif widget.label == "每批候选数":
            widget.set_value(200)
    next(widget for widget in app.number_input if widget.label == "每段对话轮数").set_value(5)
    next(b for b in app.button if b.label == "开始自动生成").click().run()
    assert not app.exception
    assert commands and commands[0][:3] == ("workflow", "--action", "resume")
    run_id = app.session_state[f"workflow-selected:{name}"]
    assert commands[0][-1] == run_id
    assert app.session_state["nav"] == "任务管理"
    assert app.session_state[f"task-view:{name}"] == "数据工作流"
    assert app.session_state[f"task-center-run:{name}"] == run_id
    assert canvas(app, f"live-canvas:{run_id}")["nodes"][0]["id"] == "ingest"
    assert not any(item.label == "开始自动生成" for item in app.button)
    from lib.workflow import read_json, run_path
    recipe = read_json(run_path(ws.out(name), run_id) / "recipe.json")
    assert set(recipe["node_models"]) == {"multiturn"}
    assert set(recipe["node_models"]["multiturn"]) == {"generation", "jev"}
    assert recipe["sample_count"] == 50000 and recipe["concurrency"] == 8 and recipe["batch_size"] == 200
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


def test_node_model_choices_survive_switching_nodes_and_do_not_change_other_nodes(tmp_path, monkeypatch):
    _, workspace, _ = setup_workspace(tmp_path, monkeypatch)
    from lib import backend_manager
    monkeypatch.setattr(backend_manager, "list_backends", lambda: {
        "default_backend": "writer", "roles": {"generation": {"backend": "writer", "model": "write-v1"},
                                                 "jev": {"backend": "review", "model": "review-v1"}},
        "backends": [{"name": "writer", "models": ["write-v1", "write-v2"]},
                     {"name": "review", "models": ["review-v1", "review-v2"]}]})
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace
    app.session_state["nav"] = "自动工作流"
    app.run()
    draft_key = f"workflow-node-bindings:{workspace}"
    generation_key = f"node-model:{workspace}:sft:generation:model:writer"
    next(widget for widget in app.selectbox if widget.key == generation_key).set_value("write-v2").run()
    assert not app.exception
    assert app.session_state[draft_key]["sft"]["generation"]["model"] == "write-v2"
    app.session_state[f"setup-canvas:{workspace}"] = {"node": "preference", "serial": "switch-1"}
    app.run()
    assert not app.exception
    review_key = f"node-model:{workspace}:preference:jev:backend"
    next(widget for widget in app.selectbox if widget.key == review_key).set_value("writer").run()
    assert not app.exception
    app.session_state[f"setup-canvas:{workspace}"] = {"node": "sft", "serial": "switch-2"}
    app.run()
    assert not app.exception
    assert next(widget for widget in app.selectbox if widget.key == generation_key).value == "write-v2"
    assert app.session_state[draft_key]["preference"]["generation"]["model"] == "write-v1"
    assert app.session_state[draft_key]["preference"]["jev"]["backend"] == "writer"
    assert app.session_state[draft_key]["sft"]["jev"]["backend"] == "review"


def test_english_workflow_controls_and_canvas_are_localized(tmp_path, monkeypatch):
    _, workspace, source = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace
    app.session_state["nav"] = "自动工作流"
    app.session_state["ui_language"] = "en"
    app.run()
    assert not app.exception
    assert any(widget.label == "Candidate count" for widget in app.number_input)
    assert any(button.label == "⬡　Model Services" for button in app.sidebar.button)
    spec = canvas(app, f"setup-canvas:{workspace}")
    assert not re.search(r"[\u4e00-\u9fff]", json.dumps(spec, ensure_ascii=False))
    markup = "".join(str(node.value) for node in app.get("html"))
    assert "Generation model" in markup and "Independent review model" in markup
    visible = re.sub(r"<style\b[^>]*>.*?</style>", "", markup, flags=re.S)
    visible = re.sub(r"<[^>]*>", "", visible)
    assert not re.search(r"[\u4e00-\u9fff]", visible), visible
    app.session_state["nav"] = "模型与密钥"
    app.run()
    assert not app.exception and any(item.value == "Model Services" for item in app.title)
    assert not any(widget.key == "backend-role-edit" for widget in app.selectbox)


def test_english_run_canvas_translates_all_stage_statuses():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS
    for status in ("pending", "queued", "running", "completed", "failed", "cancelled"):
        spec = canvas_spec(["sft"], {"sft": {"status": status, "done": 25, "total": 50000}},
                           "sft", GRAPH_LABELS, STAGE_GLYPHS, language="en", live=True)
        assert not re.search(r"[\u4e00-\u9fff]", json.dumps(spec, ensure_ascii=False))


def test_all_target_canvas_localizes_inspection_and_configuration_controls():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS
    targets = ["cpt", "sft", "multiturn", "agent", "gsm8k", "cot", "orpo", "dpo", "rlaif"]
    setup = canvas_spec(targets, {}, "agent", GRAPH_LABELS, STAGE_GLYPHS, language="en")
    live = canvas_spec(targets, {}, "agent", GRAPH_LABELS, STAGE_GLYPHS, language="en", live=True)
    assert "configure" in setup["labels"]["hint"] and "inspect" in live["labels"]["hint"]
    assert setup["labels"]["overview"] == f"{len(setup['nodes'])} nodes · {len(setup['edges'])} links"
    assert setup["labels"]["focus"] == "Locate selected node"
    for spec in (setup, live):
        assert not re.search(r"[\u4e00-\u9fff]", json.dumps(spec, ensure_ascii=False))


def test_expanded_english_task_view_uses_full_canvas_and_localized_quality(tmp_path, monkeypatch):
    ws, workspace, source = setup_workspace(tmp_path, monkeypatch)
    rid = create_run(ws.out(workspace), sources=[source], targets=["cpt"], name="Offline document run")
    Workflow(ws.out(workspace), rid, ROOT).execute()
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace
    app.session_state["nav"] = "任务管理"
    app.session_state["ui_language"] = "en"
    app.session_state[f"task-center-focus:{workspace}"] = True
    app.run()
    assert not app.exception
    assert any(widget.label == "Expand workflow view" and widget.value for widget in app.toggle)
    spec = canvas(app, f"live-canvas:{rid}")
    assert spec["selected"] == "package"
    assert not re.search(r"[\u4e00-\u9fff]", json.dumps(spec, ensure_ascii=False))
    markup = "".join(str(node.value) for node in app.get("html"))
    visible = re.sub(r"<style\b[^>]*>.*?</style>", "", markup, flags=re.S)
    visible = re.sub(r"<[^>]*>", "", visible)
    assert not re.search(r"[\u4e00-\u9fff]", visible), visible


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
    assert "sft" not in {node["id"] for node in canvas(app, f"live-canvas:{run_id}")["nodes"]}
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
