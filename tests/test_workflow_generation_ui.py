"""Exercise optional node settings without model calls or user cache writes."""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from tests.test_workflow_draft import SCRIPT
from lib.presentation.streamlit import workflow_generation_settings as settings
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft


def test_generation_is_opt_in_and_file_format_remains_independent():
    ui = AppTest.from_string(SCRIPT).run()
    enabled = "workflow-generation-enabled:fixture:sft"
    style = "workflow-generation-style:fixture:sft"
    instruction = "workflow-generation-instruction:fixture:sft"
    assert ui.toggle(key=enabled).value is False
    assert ui.selectbox(key=style).disabled
    assert ui.text_area(key=instruction).disabled
    assert not any(row.key == "workflow-generation-upload:fixture:sft" for row in ui.get("file_uploader"))
    assert ui.selectbox(key="workflow-sft-output-style:fixture").label == "SFT 训练文件格式"
    ui.toggle(key=enabled).set_value(True).run()
    assert any(row.key == "workflow-generation-upload:fixture:sft" for row in ui.get("file_uploader"))
    ui.selectbox(key=style).set_value("skeptical").run()
    ui.text_area(key=instruction).set_value("Keep source citations and check assumptions.").run()
    ui.selectbox(key="workflow-sft-output-style:fixture").set_value("drop").run()
    ui.button(key="fixture-node:ingest").click().run()
    ui.button(key="fixture-node:sft").click().run()
    assert ui.toggle(key=enabled).value is True
    assert ui.selectbox(key=style).value == "skeptical"
    assert ui.text_area(key=instruction).value == "Keep source citations and check assumptions."
    assert ui.selectbox(key="workflow-sft-output-style:fixture").value == "drop"
    assert not ui.exception


def test_custom_style_requires_instruction_only_while_enabled():
    ui = AppTest.from_string(SCRIPT).run()
    enabled = "workflow-generation-enabled:fixture:sft"
    ui.toggle(key=enabled).set_value(True).run()
    ui.selectbox(key="workflow-generation-style:fixture:sft").set_value("custom").run()
    assert any("风格配置尚未完成" in row.value for row in ui.warning)
    assert ui.button(key="workflow-create:fixture").disabled
    ui.toggle(key=enabled).set_value(False).run()
    assert not any("风格配置尚未完成" in row.value for row in ui.warning)
    assert not ui.exception


def test_incomplete_cot_custom_style_is_editable_without_crashing_model_controls():
    ui = AppTest.from_string(SCRIPT).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cot"]).run()
    ui.button(key="fixture-node:cot").click().run()
    ui.toggle(key="workflow-generation-enabled:fixture:cot").set_value(True).run()
    ui.selectbox(key="workflow-generation-style:fixture:cot").set_value("custom").run()
    assert not ui.exception
    assert any("风格配置尚未完成" in row.value for row in ui.warning)
    assert ui.selectbox(key="node-model:fixture:cot:generation:backend").value == "local"
    ui.text_area(key="workflow-generation-instruction:fixture:cot").set_value("Use a brief teaching style.").run()
    assert not ui.exception
    assert not any("风格配置尚未完成" in row.value for row in ui.warning)


def test_cot_and_sft_keep_separate_style_drafts_after_page_and_goal_changes():
    ui = AppTest.from_string(SCRIPT).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cot"]).run()
    ui.button(key="fixture-node:cot").click().run()
    assert not any(row.key == "node-model:fixture:cot:generation:backend" for row in ui.selectbox)
    assert ui.selectbox(key="node-model:fixture:cot:jev:backend").value == "local"
    enabled = "workflow-generation-enabled:fixture:cot"
    ui.toggle(key=enabled).set_value(True).run()
    assert ui.selectbox(key="node-model:fixture:cot:generation:backend").value == "local"
    ui.selectbox(key="workflow-generation-style:fixture:cot").set_value("reflective").run()
    ui.text_area(key="workflow-generation-instruction:fixture:cot").set_value("Check the result once.").run()
    ui.checkbox(key="fixture-show").uncheck().run()
    ui.checkbox(key="fixture-show").check().run()
    assert ui.toggle(key=enabled).value is True
    assert ui.selectbox(key="workflow-generation-style:fixture:cot").value == "reflective"
    ui.button(key="fixture-node:sft").click().run()
    assert ui.toggle(key="workflow-generation-enabled:fixture:sft").value is False
    assert ui.selectbox(key="workflow-generation-style:fixture:sft").value == "default"
    assert not any(row.key == "workflow-sft-output-style:fixture" for row in ui.selectbox)
    assert not ui.exception


def test_trim_node_is_optional_and_custom_template_survives_switching():
    ui = AppTest.from_string(SCRIPT).run()
    toggle = "workflow-trim-enabled:fixture"
    assert not any(row.key == "fixture-node:trim" for row in ui.button)
    ui.toggle(key=toggle).set_value(True).run()
    ui.button(key="fixture-node:trim").click().run()
    ui.selectbox(key="workflow-trim-template:fixture").set_value("custom").run()
    assert any("修剪配置尚未完成" in row.value for row in ui.warning)
    prompt = "Remove only repeated framing. Keep the reasoning facts and final answer."
    ui.text_area(key="workflow-trim-prompt:fixture").set_value(prompt).run()
    ui.text_area(key="workflow-trim-instruction:fixture").set_value("Keep citations.").run()
    assert not any("修剪配置尚未完成" in row.value for row in ui.warning)
    ui.button(key="fixture-node:sft").click().run()
    ui.button(key="fixture-node:trim").click().run()
    assert ui.text_area(key="workflow-trim-prompt:fixture").value == prompt
    assert ui.text_area(key="workflow-trim-instruction:fixture").value == "Keep citations."
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cpt"]).run()
    assert ui.toggle(key=toggle).disabled
    assert not any(row.key == "fixture-node:trim" for row in ui.button)
    assert not ui.exception


def test_snapshots_include_active_nodes_only_and_preserve_hidden_draft_values(monkeypatch):
    values = {"workflow-generation-enabled:w:sft": True,
              "workflow-generation-style:w:sft": "concise",
              "workflow-generation-instruction:w:sft": "Use plain words.",
              "workflow-generation-enabled:w:cot": True,
              "workflow-generation-style:w:cot": "reflective",
              "workflow-trim-enabled:w": True}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state={"workflow-form-draft:w": values}))
    assert settings.generation_snapshot("w", ("ingest", "sft", "package")) == {
        "sft": {"enabled": True, "style": "concise", "instruction": "Use plain words."}}
    assert settings.generation_snapshot("w", ("ingest", "cpt", "package")) == {}
    assert settings.trim_snapshot("w", eligible=False)["enabled"] is False


@pytest.mark.parametrize("payload,message", [
    (b"\xff\xfe", "UTF-8"), (b"", "为空"), (b"A" * 65537, "64 KiB"),
    (b"A" * 16001, "16,000"), (b"text\x00more", "无效字符"),
], ids=["bad-encoding", "empty", "byte-limit", "character-limit", "null-character"])
def test_import_template_rejects_bad_content_without_replacing_draft(monkeypatch, payload, message):
    upload = BytesIO(payload)
    upload.name = "template.md"
    state = {"workflow-trim-upload:w": upload, "workflow-trim-prompt:w": "Keep existing text."}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=state))
    saved = []
    settings._import_trim_template("w", lambda *args: saved.append(args))
    assert message in state["workflow-trim-upload-error:w"]
    assert state["workflow-trim-prompt:w"] == "Keep existing text."
    assert saved == []


def test_import_utf8_template_keeps_content_and_saves_editable_prompt(monkeypatch):
    upload = BytesIO("\ufeff保留必要事实，只清理重复包装。".encode("utf-8"))
    upload.name = "template.TXT"
    state = {"workflow-trim-upload:w": upload}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=state))
    saved = []
    settings._import_trim_template("w", lambda *args: saved.append(args))
    assert state["workflow-trim-prompt:w"] == "保留必要事实，只清理重复包装。"
    assert state["workflow-trim-template:w"] == "custom"
    assert len(saved) == 2
    assert "workflow-trim-upload-error:w" not in state


@pytest.mark.parametrize("payload,name,message", [
    (b"\xff\xfe", "style.txt", "UTF-8"), (b"", "style.txt", "为空"),
    (b"A" * 65537, "style.md", "64 KiB"), (b"A" * 4001, "style.md", "4,000"),
    (b"text\x00more", "style.txt", "无效字符"), (b"Keep facts.", "style.json", "TXT"),
], ids=["bad-encoding", "empty", "byte-limit", "character-limit", "null-character", "wrong-extension"])
def test_import_style_rejects_bad_file_and_keeps_existing_node_values(monkeypatch, payload, name, message):
    upload = BytesIO(payload)
    upload.name = name
    state = {"workflow-generation-upload:w:cot": upload,
             "workflow-generation-instruction:w:cot": "Keep existing instructions.",
             "workflow-generation-style:w:cot": "structured"}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=state))
    saved = []
    settings._import_generation_instruction("w", "cot", lambda *args: saved.append(args))
    assert message in state["workflow-generation-upload-error:w:cot"]
    assert state["workflow-generation-instruction:w:cot"] == "Keep existing instructions."
    assert state["workflow-generation-style:w:cot"] == "structured"
    assert saved == []


def test_import_style_saves_custom_utf8_instructions_for_one_node_only(monkeypatch):
    upload = BytesIO("\ufeff先核对条件，再解释关键推导。".encode("utf-8"))
    upload.name = "style.MD"
    state = {"workflow-generation-upload:w:cot": upload,
             "workflow-generation-instruction:w:sft": "Existing SFT instructions."}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=state))
    saved = []

    def save_field(workspace, key):
        saved.append((workspace, key))
        state.setdefault(f"workflow-form-draft:{workspace}", {})[key] = state[key]

    settings._import_generation_instruction("w", "cot", save_field)
    assert state["workflow-generation-instruction:w:cot"] == "先核对条件，再解释关键推导。"
    assert state["workflow-generation-style:w:cot"] == "custom"
    assert state["workflow-generation-instruction:w:sft"] == "Existing SFT instructions."
    assert settings.generation_snapshot("w", ["cot"])["cot"]["instruction"] == "先核对条件，再解释关键推导。"
    assert len(saved) == 2


def test_reusing_recipe_preserves_generation_toggle_and_trim_template_body():
    recipe = {"sources": [], "targets": ["cot"], "brief": "Generate examples.",
              "node_generation": {"sft": {"enabled": False, "style": "custom", "instruction": ""},
                                  "cot": {"enabled": True, "style": "structured", "instruction": "Check units."}},
              "reasoning_trim": {"enabled": True, "template": "custom", "instruction": "Keep citations.",
                                 "custom_prompt": "Remove only redundant framing."}}
    unchanged = deepcopy(recipe)
    values = recipe_to_draft(recipe, "Task", "w", [])["values"]
    assert values["workflow-generation-enabled:w:sft"] is False
    assert values["workflow-generation-style:w:sft"] == "custom"
    assert values["workflow-generation-instruction:w:cot"] == "Check units."
    assert values["workflow-trim-enabled:w"] is True
    assert values["workflow-trim-prompt:w"] == "Remove only redundant framing."
    assert recipe == unchanged


def test_workbench_submits_active_node_styles_and_optional_trim_with_body():
    source = SCRIPT.replace(
        "def task_runs(self): return []",
        "def create_run(self,**values):\n"
        "        st.session_state['fixture-created']=values\n"
        "        return 'fixture-run'\n"
        "    def task_runs(self): return []",
    )
    ui = AppTest.from_string(source).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["sft", "cot"]).run()
    ui.segmented_control(key="workflow-source-mode:fixture").set_value("开放需求").run()
    ui.text_area(key="workflow-open-brief:fixture").set_value("Generate maintenance examples.").run()
    ui.button(key="fixture-node:cot").click().run()
    ui.toggle(key="workflow-generation-enabled:fixture:cot").set_value(True).run()
    ui.selectbox(key="workflow-generation-style:fixture:cot").set_value("skeptical").run()
    ui.text_area(key="workflow-generation-instruction:fixture:cot").set_value("Check assumptions.").run()
    ui.toggle(key="workflow-trim-enabled:fixture").set_value(True).run()
    ui.button(key="fixture-node:trim").click().run()
    ui.selectbox(key="workflow-trim-template:fixture").set_value("custom").run()
    ui.text_area(key="workflow-trim-prompt:fixture").set_value("Remove only redundant framing.").run()
    assert not ui.button(key="workflow-create:fixture").disabled
    ui.button(key="workflow-create:fixture").click().run()
    assert not ui.exception
    created = ui.session_state["fixture-created"]
    assert created["node_generation"] == {
        "sft": {"enabled": False, "style": "default", "instruction": ""},
        "cot": {"enabled": True, "style": "skeptical", "instruction": "Check assumptions."},
    }
    assert created["reasoning_trim"] == {
        "enabled": True, "template": "custom", "instruction": "",
        "custom_prompt": "Remove only redundant framing.",
    }
    assert set(created["node_models"]) == {"ingest", "sft", "cot", "trim"}


def test_legacy_cot_details_handle_optional_none_settings_without_invented_writer(monkeypatch):
    from lib.presentation.streamlit import workflow_page

    monkeypatch.setattr(workflow_page, "st", SimpleNamespace(session_state={"ui_language": "zh"}))
    config = workflow_page._stage_configuration("cot", {"targets": ["cot"],
        "node_generation": None, "reasoning_trim": None}, {})
    assert config["生成方式"] == "旧版解释核验"
    assert "生成模型" not in config
