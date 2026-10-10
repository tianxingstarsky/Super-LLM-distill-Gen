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
    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "SEEDS_DIR", tmp_path / "data/seeds")
    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    folder = tmp_path / "data/seeds"
    folder.mkdir(parents=True)
    source = folder / "guide.txt"
    source.write_text("操作前先断电，再检查线路。", encoding="utf-8")
    name = ws.DEFAULT
    return ws, name, source


def test_workbench_keeps_flow_and_common_generation_controls_visible(tmp_path, monkeypatch):
    _, workspace, _ = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace
    app.session_state["nav"] = "自动工作流"
    app.run()
    assert not app.exception
    assert any('class="df-wb-plan"' in str(item.value) for item in app.get("html"))
    assert any(item.label == "补充生成要求（可选）" for item in app.text_area)
    assert app.number_input(key=f"workflow-concurrency:{workspace}")
    assert app.number_input(key=f"workflow-batch-size:{workspace}")
    assert not any(item.label in {"补充生成要求（可选）", "处理与批次设置"}
                   for item in app.expander)
    assert any(item.label == "输入范围与文档分块（可选）" for item in app.expander)


def test_generation_controls_survive_node_layout_changes_and_empty_targets(tmp_path, monkeypatch):
    from lib.application.backend_service import BackendApplication

    _, workspace, source = setup_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(BackendApplication, "list_backends", lambda self: {"backends": []})
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace
    app.session_state["nav"] = "自动工作流"
    app.session_state["ui_language"] = "zh"
    app.run()
    assert not app.exception
    targets_key = next(widget.key for widget in app.pills
                       if widget.key.startswith("workflow-targets:"))
    app.pills(key=targets_key).set_value(["sft"]).run()
    source_key = f"workflow-sources:{workspace}:文档资料"
    app.multiselect(key=source_key).set_value([str(source)]).run()
    expected_numbers = {
        f"workflow-count:{workspace}": 50_000,
        f"workflow-concurrency:{workspace}": 6,
        f"workflow-batch-size:{workspace}": 200,
    }
    name_key = f"workflow-name:{workspace}"
    run_name = "Preserve this bulk draft"
    for key, value in expected_numbers.items():
        app.number_input(key=key).set_value(value)
    app.text_input(key=name_key).set_value(run_name).run()

    def assert_controls(*, quantity_visible=True):
        assert not app.exception
        for key, value in expected_numbers.items():
            widgets = [widget for widget in app.number_input if widget.key == key]
            if key == f"workflow-count:{workspace}" and not quantity_visible:
                assert not widgets
                assert app.session_state[f"workflow-form-draft:{workspace}"][key] == value
                continue
            assert len(widgets) == 1 and widgets[0].value == value
        names = [widget for widget in app.text_input if widget.key == name_key]
        assert len(names) == 1 and names[0].value == run_name
        assert app.multiselect(key=source_key).value == [str(source)]

    assert_controls()
    assert canvas(app, f"setup-canvas:{workspace}")["selected"] == "sft"
    for serial, node in enumerate(("ingest", "package", "sft"), start=1):
        app.session_state[f"setup-canvas:{workspace}"] = {
            "node": node, "serial": f"layout-switch-{serial}",
        }
        app.run()
        assert_controls()
        assert canvas(app, f"setup-canvas:{workspace}")["selected"] == node

    app.pills(key=targets_key).set_value([]).run()
    assert_controls(quantity_visible=False)
    assert not app.pills(key=targets_key).value
    assert app.button(key=f"workflow-create:{workspace}").disabled
    assert len([widget for widget in app.get("file_uploader")
                if widget.key == f"workflow-upload:{workspace}:文档资料"]) == 1
    assert not any(json.loads(item.proto.json_args).get("key") == f"setup-canvas:{workspace}"
                   for item in app.get("component_instance"))
    # With no target selected, source and parameter edits remain usable.
    brief_key = f"workflow-source-brief:{workspace}:文档资料"
    app.text_area(key=brief_key).set_value("Keep the source ready for the next target choice.")
    run_name = "Editable draft with no target"
    app.text_input(key=name_key).set_value(run_name).run()
    assert_controls(quantity_visible=False)
    assert app.text_area(key=brief_key).value == "Keep the source ready for the next target choice."
    assert app.button(key=f"workflow-create:{workspace}").disabled
    app.pills(key=targets_key).set_value(["sft"]).run()
    assert_controls()


def test_configuration_issue_buttons_locate_panels_without_starting_run(tmp_path, monkeypatch):
    from copy import deepcopy
    from lib.application.backend_service import BackendApplication
    _, workspace, _ = setup_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(BackendApplication, "list_backends", lambda self: {"backends": []})
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace
    app.session_state["nav"] = "自动工作流"
    app.session_state["ui_language"] = "en"
    app.run()
    app.pills(key=f"workflow-targets:{workspace}:自动推荐").set_value(["sft", "dpo"]).run()
    key = f"workflow-config-fix:{workspace}:model:preference"
    canvas_key = f"setup-canvas:{workspace}"
    open_key = f"canvas-open:{canvas_key}"
    assert app.session_state[f"workflow-setup-node:{workspace}"] == "sft"
    assert canvas(app, canvas_key)["inspector"] == {"key": "workbench-node-panel", "open": False, "wide": False}
    assert any("missing Generation model, Process check model" in item.label
               for item in app.button)
    next(button for button in app.button if button.key == key).click().run()
    assert not app.exception
    assert app.session_state[f"workflow-setup-node:{workspace}"] == "preference"
    assert canvas(app, canvas_key)["selected"] == "preference"
    assert app.session_state[open_key] is True
    assert canvas(app, canvas_key)["inspector"]["open"] is True
    bindings = deepcopy(app.session_state[f"workflow-node-bindings:{workspace}"])
    draft = deepcopy(app.session_state[f"workflow-form-draft:{workspace}"])
    app.button(key=f"workflow-close-config:{workspace}").click().run()
    assert not app.exception
    assert app.session_state[open_key] is False
    assert canvas(app, canvas_key)["inspector"]["open"] is False
    assert app.session_state[f"workflow-setup-node:{workspace}"] == "preference"
    assert app.session_state[f"workflow-node-bindings:{workspace}"] == bindings
    assert app.session_state[f"workflow-form-draft:{workspace}"] == draft
    app.session_state[canvas_key] = {"node": "preference", "serial": "reopen-current-node"}
    app.run()
    assert not app.exception
    assert app.session_state[open_key] is True
    assert canvas(app, canvas_key)["inspector"]["open"] is True
    assert app.session_state[f"workflow-setup-node:{workspace}"] == "preference"
    app.button(key=f"workflow-config-fix:{workspace}:model:sft").click().run()
    assert app.session_state[f"workflow-setup-node:{workspace}"] == "sft"
    assert next(button for button in app.button if button.label == "Start generation").disabled


def test_agent_node_retains_verification_choice_and_blocks_unconfigured_start(tmp_path, monkeypatch):
    monkeypatch.delenv("DATAFORGE_AGENT_REPLAY_IMAGE", raising=False)
    ws, name, source = setup_workspace(tmp_path, monkeypatch)
    trace = source.with_name("agent.jsonl")
    trace.write_text(json.dumps({"messages": [
        {"role": "user", "content": "Check the recorded context."},
        {"role": "assistant", "content": "The recorded context is complete."},
    ]}), encoding="utf-8")
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()
    next(widget for widget in app.segmented_control
         if widget.label == "选择来源类型").set_value("Agent 上下文").run()
    app.multiselect(key=f"workflow-sources:{name}:Agent 上下文").set_value([str(trace)]).run()
    next(widget for widget in app.pills if widget.label == "训练目标").set_value(["agent"]).run()
    app.session_state[f"setup-canvas:{name}"] = {"node": "agent", "serial": "agent-1"}
    app.run()
    next(widget for widget in app.selectbox if widget.label == "轨迹验证方式").set_value("isolated").run()
    assert not app.exception
    assert next(button for button in app.button if button.key == f"workflow-create:{name}").disabled
    app.session_state[f"setup-canvas:{name}"] = {"node": "package", "serial": "package-1"}
    app.run()
    app.session_state[f"setup-canvas:{name}"] = {"node": "agent", "serial": "agent-2"}
    app.run()
    picker = next(widget for widget in app.selectbox if widget.label == "轨迹验证方式")
    assert picker.value == "isolated"
    picker.set_value("local").run()
    assert not app.exception
    assert not next(button for button in app.button if button.key == f"workflow-create:{name}").disabled


def test_workbench_empty_and_completed_run_visible_after_refresh(tmp_path, monkeypatch):
    ws, name, source = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()
    assert not app.exception
    assert any("数据生成工作台" == item.value for item in app.title)
    assert not any('class="df-run-summary"' in str(item.value) for item in app.get("html"))
    app.sidebar.button(key="nav-button:任务管理").click().run()
    assert not app.exception
    assert any("本机暂无自动工作流" in str(item.value) for item in app.get("html"))
    assert not any(item.key.startswith("sidebar-task:") for item in app.sidebar.button)
    app.sidebar.button(key="nav-button:自动工作流").click().run()
    assert not app.exception
    rid = create_run(ws.out(name), sources=[source], targets=["cpt"], name="文档自动验收")
    Workflow(ws.out(name), rid, ROOT).execute()
    app.run()
    assert not app.exception
    assert not any('class="df-run-summary"' in str(item.value) for item in app.get("html"))
    app.session_state[f"task-center-filter:{name}"] = "未结束"
    app.session_state[f"task-center-search:{name}"] = "stale search"
    app.sidebar.button(key=f"sidebar-task:{name}:{rid}").click().run()
    assert not app.exception
    assert app.session_state["nav"] == "任务管理"
    assert app.session_state[f"task-center-search:{name}"] == ""
    assert app.session_state[f"task-center-filter:{name}"] == "全部"
    assert any("文档自动验收" in str(item.value) and 'class="df-run-summary"' in str(item.value)
               for item in app.get("html"))
    assert any("按实际合格数量交付" in item.value for item in app.success)
    route = canvas(app, f"live-canvas:{rid}")
    assert [node["id"] for node in route["nodes"]] == ["ingest", "cpt", "package"]
    assert not any(item.label == "查看工作流节点" for item in app.selectbox)
    assert app.session_state[f"workflow-stage:{rid}"] == "package"
    overview = [item.value for item in app.get("html") if isinstance(item.value, str)
                and 'class="df-run-summary"' in item.value]
    assert overview and "已完成节点 <b>3 / 3</b>" in overview[0]
    assert route["edges"] == [["ingest", "cpt"], ["cpt", "package"]]
    assert any("查看并打包本次训练数据" in item.label for item in app.button)
    assert any(item.key == f"browse-preview:{rid}:cpt" and not item.disabled for item in app.button)
    assert any("来源与配方" in item.label for item in app.tabs)
    app.session_state[f"live-canvas:{rid}"] = {"node": "cpt", "serial": "click-1"}
    app.run()
    assert not app.exception
    assert app.session_state[f"workflow-stage:{rid}"] == "cpt"
    node_logs = [item.value for item in app.get("html") if isinstance(item.value, str)
                 and '<div class="df-run-log">' in item.value]
    assert node_logs and "节点处理完成" in node_logs[0]
    assert "run_finished" not in node_logs[0]
    next(item for item in app.button if item.key == f"browse-preview:{rid}:cpt").click().run()
    assert not app.exception
    assert app.session_state["nav"] == "数据管理"
    assert app.session_state[f"data-preview-run:{name}"] == rid
    assert app.session_state[f"data-preview-target:{name}:{rid}"] == "cpt"
    assert any("操作前先断电" in str(item.value) for item in app.get("html"))
    # A fresh browser session discovers the same persistent run.
    other = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    other.session_state["ws"] = name
    other.session_state["nav"] = "自动工作流"
    other.run()
    assert not other.exception
    other.sidebar.button(key=f"sidebar-task:{name}:{rid}").click().run()
    assert not other.exception
    assert any("文档自动验收" in str(item.value) and 'class="df-run-summary"' in str(item.value)
               for item in other.get("html"))


def test_create_button_wires_exact_persisted_run_to_job(tmp_path, monkeypatch):
    ws, name, source = setup_workspace(tmp_path, monkeypatch)
    from lib.application.backend_service import BackendApplication
    from lib.bootstrap import workflows as workflow_bootstrap
    from lib.console_jobs import Job
    commands = []
    monkeypatch.setattr(Job, "start", lambda self: commands.append(self.command))
    settings = tmp_path / "configs"
    settings.mkdir()
    (settings / "backends.yaml").write_text(
        "backends:\n  writer:\n    base_url: https://models.example.test/v1\n"
        "    api_format: chat\n    api_key_env: FIXTURE_ONLY_KEY\n"
        "    models: [write-v1, judge-v1]\n",
        encoding="utf-8",
    )
    (settings / "preferences.yaml").write_text(
        (ROOT / "configs" / "preferences.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    real_workflow_application = workflow_bootstrap.workflow_application
    monkeypatch.setattr(workflow_bootstrap, "workflow_application",
                        lambda _root, output: real_workflow_application(tmp_path, output))
    monkeypatch.setattr(BackendApplication, "list_backends", lambda self: {
        "default_backend": "writer", "default_model": "write-v1",
        "roles": {"generation": {"backend": "writer", "model": "write-v1"},
                  "jev": {"backend": "writer", "model": "unlisted-judge"}},
        "backends": [{"name": "writer", "models": ["write-v1", "judge-v1"]}],
    })
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()
    assert not app.exception
    inputs = {widget.label: widget for widget in [*app.multiselect, *app.pills]}
    inputs["训练目标"].set_value(["cpt", "multiturn"]).run()
    assert not app.exception
    inputs = {widget.label: widget for widget in [*app.multiselect, *app.pills]}
    inputs["本次使用的资料"].set_value([str(source)])
    assert not any("留空使用" in widget.label for widget in app.text_input)
    for widget in app.number_input:
        if widget.key == f"workflow-count:{name}":
            widget.set_value(50000)
        elif widget.label == "并发请求上限":
            widget.set_value(8)
        elif widget.label == "每批候选数":
            widget.set_value(200)
    next(widget for widget in app.number_input if widget.label == "每段对话轮数").set_value(5)
    app.run()
    # This test covers local corpus processing and the multi-turn model node.
    app.session_state[f"setup-canvas:{name}"] = {"node": "ingest", "serial": "choose-native-parser"}
    app.run()
    app.radio(key=f"workflow-document-parse-mode:{name}").set_value("native").run()
    app.session_state[f"setup-canvas:{name}"] = {"node": "cpt", "serial": "choose-native-cpt"}
    app.run()
    app.radio(key=f"workflow-cpt-processing-mode:{name}").set_value("native").run()
    app.session_state[f"setup-canvas:{name}"] = {"node": "multiturn", "serial": "choose-review-model"}
    app.run()
    assert next(b for b in app.button if b.label == "开始自动生成").disabled
    app.selectbox(key=f"node-model:{name}:multiturn:jev:backend").set_value("writer").run()
    app.selectbox(key=f"node-model:{name}:multiturn:jev:model:writer").set_value("judge-v1").run()
    confirm = app.button(key=f"node-model-confirm:{name}:multiturn")
    assert not confirm.disabled
    confirm.click().run()
    assert app.button(key=f"node-model-confirm:{name}:multiturn").disabled
    assert not next(b for b in app.button if b.label == "开始自动生成").disabled
    app.session_state[f"setup-canvas:{name}"] = {"node": "package", "serial": "scale-settings-check"}
    app.run()
    assert not app.exception
    values = {widget.label: widget.value for widget in app.number_input}
    assert values["期望样本量（非必达）"] == 50000
    assert values["并发请求上限"] == 8 and values["每批候选数"] == 200
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


def test_quick_presets_keep_every_target_available_and_custom_choices_independent(tmp_path, monkeypatch):
    _, name, _ = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()

    target_picker = next(widget for widget in app.pills if widget.label == "训练目标")
    from lib.domain.workflow_targets import TARGETS
    from lib.presentation.streamlit.workflow_page import TARGET_LABELS
    from streamlit.proto.ButtonGroup_pb2 import ButtonGroup
    assert target_picker.options == [TARGET_LABELS[target] for target in TARGETS]
    assert target_picker.proto.style == ButtonGroup.PILLS
    assert target_picker.proto.click_mode == ButtonGroup.MULTI_SELECT
    target_picker.set_value(["orpo"]).run()
    assert not app.exception
    rendered = "".join(str(node.value) for node in app.get("html"))
    assert 'class="df-wb-selected"' not in rendered
    assert "SFT 中间候选" in rendered
    assert next(widget for widget in app.pills if widget.label == "训练目标").value == ["orpo"]

    app.selectbox(key=f"workflow-preset:{name}").set_value("偏好对齐").run()
    assert not app.exception
    assert set(next(widget for widget in app.pills if widget.label == "训练目标").value) == {
        "sft", "orpo", "dpo", "rlaif"}
    next(widget for widget in app.pills if widget.label == "训练目标").set_value(["rlaif"]).run()
    app.selectbox(key=f"workflow-preset:{name}").set_value("自动推荐").run()
    assert not app.exception
    assert next(widget for widget in app.pills if widget.label == "训练目标").value == ["orpo"]

    next(widget for widget in app.pills if widget.label == "训练目标").set_value(["agent", "gsm8k"]).run()
    assert not app.exception
    rendered = "".join(str(node.value) for node in app.get("html"))
    assert next(widget for widget in app.pills if widget.label == "训练目标").value == ["agent", "gsm8k"]
    assert "SFT 中间候选" not in rendered
    assert [node["id"] for node in canvas(app, f"setup-canvas:{name}")["nodes"]] == list(
        execution_graph(["agent", "gsm8k"])[0])


def test_node_model_choices_survive_switching_nodes_and_do_not_change_other_nodes(tmp_path, monkeypatch):
    _, workspace, _ = setup_workspace(tmp_path, monkeypatch)
    from lib.application.backend_service import BackendApplication
    monkeypatch.setattr(BackendApplication, "list_backends", lambda self: {
        "default_backend": "writer", "roles": {"generation": {"backend": "writer", "model": "write-v1"},
                                                 "jev": {"backend": "review", "model": "review-v1"}},
        "backends": [{"name": "writer", "models": ["write-v1", "write-v2"]},
                     {"name": "review", "models": ["review-v1", "review-v2"]}]})
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace
    app.session_state["nav"] = "自动工作流"
    app.run()
    app.pills(key=f"workflow-targets:{workspace}:自动推荐").set_value(["sft", "dpo"]).run()
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
    assert "jev" not in app.session_state[draft_key]["preference"]
    review_model_key = f"node-model:{workspace}:preference:jev:model:writer"
    next(widget for widget in app.selectbox if widget.key == review_model_key).set_value("write-v2").run()
    assert not app.exception
    app.session_state[f"setup-canvas:{workspace}"] = {"node": "sft", "serial": "switch-2"}
    app.run()
    assert not app.exception
    assert next(widget for widget in app.selectbox if widget.key == generation_key).value == "write-v2"
    assert app.session_state[draft_key]["preference"]["generation"]["model"] == "write-v1"
    assert app.session_state[draft_key]["preference"]["jev"]["backend"] == "writer"
    assert app.session_state[draft_key]["preference"]["jev"]["model"] == "write-v2"
    assert app.session_state[draft_key]["sft"]["jev"]["backend"] == "review"


def test_english_workflow_controls_and_canvas_are_localized(tmp_path, monkeypatch):
    _, workspace, source = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace
    app.session_state["nav"] = "自动工作流"
    app.session_state["ui_language"] = "en"
    app.run()
    assert not app.exception
    assert any(widget.label == "Expected samples (not required)" for widget in app.number_input)
    target_picker = app.pills(key=f"workflow-targets:{workspace}:自动推荐")
    assert len(target_picker.options) == 9
    assert not re.search(r"[\u4e00-\u9fff]", target_picker.label + " ".join(target_picker.options))
    assert any(button.key == "nav-button:系统设置" for button in app.sidebar.button)
    assert not any(button.key == "nav-button:模型与密钥" for button in app.sidebar.button)
    spec = canvas(app, f"setup-canvas:{workspace}")
    assert not re.search(r"[\u4e00-\u9fff]", json.dumps(spec, ensure_ascii=False))
    markup = "".join(str(node.value) for node in app.get("html"))
    assert "Generation model" in markup and "Process check model" in markup
    visible = re.sub(r"<style\b[^>]*>.*?</style>", "", markup, flags=re.S)
    visible = re.sub(r"<[^>]*>", "", visible)
    assert not re.search(r"[\u4e00-\u9fff]", visible), visible
    app.session_state["nav"] = "模型与密钥"
    app.run()
    assert not app.exception and app.session_state["nav"] == "系统设置"
    assert any(item.label == "Connections and budget (advanced)" for item in app.expander)
    assert not any(widget.key == "backend-role-edit" for widget in app.selectbox)


def test_english_run_canvas_translates_all_stage_statuses():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS
    for status in ("pending", "queued", "running", "completed", "failed", "cancelled"):
        spec = canvas_spec(["sft"], {"sft": {"status": status, "done": 25, "total": 50000}},
                           "sft", GRAPH_LABELS, STAGE_GLYPHS, language="en", live=True)
        assert not re.search(r"[\u4e00-\u9fff]", json.dumps(spec, ensure_ascii=False))


def test_removed_node_service_requires_explicit_replacement(tmp_path, monkeypatch):
    _, workspace, _ = setup_workspace(tmp_path, monkeypatch)
    from lib.application.backend_service import BackendApplication
    inventory = {"default_backend": "writer", "roles": {"generation": {"backend": "writer", "model": "write"},
                 "jev": {"backend": "review", "model": "judge"}},
                 "backends": [{"name": "writer", "models": ["write"]}, {"name": "review", "models": ["judge"]}]}
    monkeypatch.setattr(BackendApplication, "list_backends", lambda self: inventory)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = workspace
    app.session_state["nav"] = "自动工作流"
    app.session_state["ui_language"] = "en"
    app.run()
    inventory["backends"] = [inventory["backends"][0]]
    app.run()
    assert not app.exception
    key = f"node-model:{workspace}:sft:jev:backend"
    assert next(widget for widget in app.selectbox if widget.key == key).value is None
    assert app.session_state[f"workflow-node-bindings:{workspace}"]["sft"]["jev"]["backend"] == "review"
    assert next(button for button in app.button if button.label == "Start generation").disabled
    assert any("saved model connection is unavailable" in item.value for item in app.warning)
    node = next(node for node in canvas(app, f"setup-canvas:{workspace}")["nodes"] if node["id"] == "sft")
    assert node["status"] == "configuration_required" and node["subtitle"] == "Missing: Review model"
    next(widget for widget in app.selectbox if widget.key == key).set_value("writer").run()
    assert not app.exception
    assert "jev" not in app.session_state[f"workflow-node-bindings:{workspace}"]["sft"]
    model_key = f"node-model:{workspace}:sft:jev:model:writer"
    next(widget for widget in app.selectbox if widget.key == model_key).set_value("write").run()
    assert not app.exception
    assert app.session_state[f"workflow-node-bindings:{workspace}"]["sft"]["jev"]["backend"] == "writer"


def test_all_target_canvas_localizes_inspection_and_configuration_controls():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS
    targets = ["cpt", "sft", "multiturn", "agent", "gsm8k", "cot", "orpo", "dpo", "rlaif"]
    setup = canvas_spec(targets, {}, "agent", GRAPH_LABELS, STAGE_GLYPHS, language="en")
    live = canvas_spec(targets, {}, "agent", GRAPH_LABELS, STAGE_GLYPHS, language="en", live=True)
    assert "inspect" in setup["labels"]["hint"] and "inspect" in live["labels"]["hint"]
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
        assert tuple(node["id"] for node in canvas(app, f"setup-canvas:{name}")["nodes"]) == nodes
        assert tuple(re.findall(r'data-from="([^"]+)" data-to="([^"]+)"', detail)) == edges
        assert "各阶段仍按顺序执行" in detail
        assert f"{len(nodes)} 个阶段 · {len(edges)} 条数据依赖" in summary
        return summary, detail

    assert_preview(["sft"])
    picker = next(widget for widget in app.pills if widget.label == "训练目标")
    picker.set_value(["orpo"]).run()
    assert not app.exception
    summary, detail = assert_preview(["orpo"])
    assert "SFT 中间候选" in summary
    assert any("仅用于下游目标的 SFT 中间候选不会单独导出。" in item.value for item in app.caption)
    assert 'data-from="sft" data-to="package"' not in detail

    next(widget for widget in app.pills if widget.label == "训练目标").set_value(
        ["agent", "gsm8k"]).run()
    assert not app.exception
    summary, detail = assert_preview(["agent", "gsm8k"])
    assert "SFT 中间候选" not in summary
    assert 'data-from="agent" data-to="gsm8k"' not in detail

    next(widget for widget in app.pills if widget.label == "训练目标").set_value([]).run()
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
    assert not any(widget.label == "本次使用的资料" for widget in app.multiselect)


def test_open_brief_checks_brave_connection_in_place_without_rendering_key(tmp_path, monkeypatch):
    from lib.infrastructure import brave_web_research

    _, name, _ = setup_workspace(tmp_path, monkeypatch)
    secret = "private-brave-test-key"
    monkeypatch.setenv(brave_web_research.KEY_ENV, secret)
    checks = []
    monkeypatch.setattr(brave_web_research, "check_connection",
                        lambda *args, **kwargs: checks.append(True) or "ready")
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.run()
    next(widget for widget in app.segmented_control
         if widget.label == "选择来源类型").set_value("开放需求").run()
    assert not app.exception and checks == []
    assert not next(widget for widget in app.checkbox
                    if widget.label == "联网查找公开资料").value

    next(button for button in app.button if button.label == "检查 Brave 连接").click().run()
    assert not app.exception and checks == [True]
    assert any("Brave Search 连接可用" in item.value for item in app.success)
    visible = "\n".join(str(item.value) for item in
                        [*app.get("html"), *app.caption, *app.success, *app.warning, *app.error])
    assert secret not in visible


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
    app.sidebar.button(key=f"sidebar-task:{name}:{run_id}").click().run()
    assert not app.exception
    overview = [item.value for item in app.get("html") if isinstance(item.value, str)
                and 'class="df-run-summary"' in item.value]
    assert overview and "已完成节点 <b>0 / 3</b>" in overview[0]
    assert "sft" not in {node["id"] for node in canvas(app, f"live-canvas:{run_id}")["nodes"]}
    assert "继续执行沿用本次来源快照与节点配方" in app.button(key=f"resume:{run_id}").proto.help
    next(item for item in app.button if item.label == "继续执行 / 从断点重试").click().run()
    assert commands and commands[-1][-1] == run_id

    monkeypatch.setattr(FilesystemWorkflowDriver, "is_active", lambda self, _run_id: True)
    app.run()
    assert not app.exception
    next(item for item in app.button if item.label == "停止后续步骤").click().run()
    assert (ws.out(name) / "workflows" / run_id / "cancel.json").exists()


def test_run_inspector_distinguishes_reused_units_in_english(tmp_path, monkeypatch):
    ws, name, source = setup_workspace(tmp_path, monkeypatch)
    run_id = create_run(ws.out(name), sources=[source], targets=["cpt"])
    from lib.infrastructure.training_workflow import atomic_json, read_json, run_path
    path = run_path(ws.out(name), run_id) / "state.json"
    state = read_json(path)
    state["status"] = "cancelled"
    state["stages"]["cpt"].update(status="cancelled", done=40000, total=50000,
                                   cached=35000, batches_done=200, batches_total=250)
    atomic_json(path, state)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.session_state["nav"] = "自动工作流"
    app.session_state["ui_language"] = "en"
    app.session_state[f"workflow-stage:{run_id}"] = "cpt"
    app.run()
    assert not app.exception
    app.sidebar.button(key=f"sidebar-task:{name}:{run_id}").click().run()
    assert not app.exception
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["Reused checkpoints"] == "35,000"
    assert metrics["Processed this attempt"] == "5,000"
    assert "Create a new run to change models" in app.button(key=f"resume:{run_id}").proto.help
    markup = "".join(str(node.value) for node in app.get("html"))
    visible = re.sub(r"<style\b[^>]*>.*?</style>", "", markup, flags=re.S)
    # A task name is workspace data, so preserve its original language.
    visible = re.sub(r"<(strong|h3)\b(?=[^>]*\bdata-user-content\b)[^>]*>.*?</\1>", "", visible, flags=re.S)
    visible = re.sub(r"<[^>]*>", "", visible)
    assert "No events for this stage yet" in visible
    assert not re.search(r"[\u4e00-\u9fff]", visible), visible


def test_workflow_navigation_from_overview(tmp_path, monkeypatch):
    _, name, _ = setup_workspace(tmp_path, monkeypatch)
    app = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=15)
    app.session_state["ws"] = name
    app.run()
    app.button(key="overview:文档资料").click().run()
    assert not app.exception
    assert app.session_state["nav"] == "自动工作流"
    assert app.session_state[f"workflow-source-mode:{name}"] == "文档资料"
    assert app.session_state[f"workflow-preset:{name}"] == "自动推荐"


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
    assert any("偏好审核队列为空" in str(item.value) and "DPO 候选" in str(item.value)
               for item in app.get("html"))
    assert app.button(key="preference-review-empty-workflow").label == "前往数据生成"
