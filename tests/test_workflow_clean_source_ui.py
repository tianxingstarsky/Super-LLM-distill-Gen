"""Shared document cleaning remains visible and configurable without CPT export."""
from itertools import combinations
from pathlib import Path
import json
import shutil
import subprocess

import pytest

from streamlit.testing.v1 import AppTest

from lib.domain.workflow_targets import TARGETS
from lib.presentation.streamlit.workflow_canvas import canvas_spec, node_display_label


LABELS = {"ingest": "输入解析", "director": "对话指导员", "cpt": "CPT 清洗评审",
          "sft": "SFT 生成", "multiturn": "多轮对话", "package": "打包"}


def _spec(targets, **options):
    return canvas_spec(targets, {}, "cpt", LABELS, {}, recipe_version=19,
                       source_cleaning=True, review_repair={"mode": "auto"}, **options)


def test_sft_without_cpt_export_shows_a_prior_shared_cleaning_column():
    spec = _spec(["sft"], qa_director={"enabled": True}, cpt_processing={"mode": "native"})
    nodes = {node["id"]: node for node in spec["nodes"]}
    assert nodes["cpt"]["label"] == "CPT 语料处理"
    assert nodes["cpt"]["intermediate"] is True
    assert nodes["ingest"]["x"] < nodes["cpt"]["x"] < nodes["director"]["x"] < nodes["sft"]["x"]
    assert ("ingest", "cpt") in spec["edges"]
    assert ("cpt", "director") in spec["edges"]
    assert ("director", "sft") in spec["edges"]
    assert "合格" in nodes["cpt"]["description"]
    assert "基于清洗合格资料" in nodes["sft"]["description"]
    assert "生成模型" not in nodes["cpt"]["subtitle"]


def test_model_cleaning_role_is_named_for_cleaning_not_sample_generation():
    bindings = {"cpt": {"generation": {"backend": "local", "model": "cleaner"}}}
    spec = _spec(["sft"], bindings=bindings, cpt_processing={"mode": "model"}, language="en")
    node = next(node for node in spec["nodes"] if node["id"] == "cpt")
    assert node["models"] == ["Clean: local · cleaner"]
    assert node["label"] == "CPT corpus processing"


def test_legacy_and_cpt_only_nodes_keep_their_dependencies_and_cpt_name():
    assert node_display_label("cpt", LABELS, source_cleaning=True, recipe_version=18) == "CPT 语料处理"
    historical = canvas_spec(["cpt", "sft"], {}, "cpt", LABELS, {},
                             recipe_version=18, source_cleaning=True)
    nodes = {node["id"]: node for node in historical["nodes"]}
    assert nodes["cpt"]["label"] == "CPT 语料处理"
    assert nodes["cpt"]["x"] == nodes["sft"]["x"]
    assert ("ingest", "sft") in historical["edges"]
    cpt_only = _spec(["cpt"])
    node = next(node for node in cpt_only["nodes"] if node["id"] == "cpt")
    assert node["label"] == "CPT 语料处理" and not node["intermediate"]


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("targets", [["sft"], ["cpt", "sft"]])
def test_shared_cpt_name_and_export_explanation_follow_selected_targets(targets, language):
    node = next(row for row in _spec(targets, language=language)["nodes"] if row["id"] == "cpt")
    assert node["label"] == ("CPT 语料处理" if language == "zh" else "CPT corpus processing")
    assert node["intermediate"] is ("cpt" not in targets)
    explanation = ("本次导出 CPT 语料" if "cpt" in targets else "本次不导出 CPT") if language == "zh" else (
        "Export CPT corpus" if "cpt" in targets else "Do not export CPT in this run")
    assert explanation in node["description"]
    if language == "en":
        assert not any("\u4e00" <= char <= "\u9fff" for char in node["description"])


def test_all_new_target_combinations_have_clear_nonoverlapping_columns():
    for count in range(len(TARGETS) + 1):
        for targets in combinations(TARGETS, count):
            for directed in (False, True):
                spec = _spec(targets, qa_director={"enabled": directed}, reasoning_trim=True)
                columns = {}
                for node in spec["nodes"]:
                    assert 0 <= node["x"] <= spec["width"] - 218, targets
                    assert 0 <= node["y"] <= spec["height"] - 78, targets
                    columns.setdefault(node["x"], []).append(node["y"])
                for positions in columns.values():
                    positions.sort()
                    assert all(b - a >= 78 for a, b in zip(positions, positions[1:])), targets
                ids = {node["id"]: node for node in spec["nodes"]}
                if any(edge[0] == "cpt" and edge[1] in {"director", "sft", "multiturn"} for edge in spec["edges"]):
                    assert ids["cpt"]["x"] < min(ids[key]["x"] for key in ("sft", "multiturn") if key in ids)


def test_shared_cleaning_canvas_preserves_real_frontend_node_navigation():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if not node or not (root / "node_modules/jsdom").is_dir():
        pytest.skip("Node and jsdom are required")
    specs = [_spec(targets, qa_director={"enabled": directed}, reasoning_trim=True)
             for targets in (["sft"], ["sft", "cpt"], ["sft", "multiturn", "dpo"], list(TARGETS))
             for directed in (False, True)]
    result = subprocess.run([node, str(root / "tests/workflow_canvas_harness.mjs"), "--check-specs"],
                            input=json.dumps(specs), cwd=root, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


SCRIPT = '''
from copy import deepcopy
import streamlit as st
from lib.presentation.streamlit.workflow_cpt_settings import render_cpt_settings
from lib.presentation.streamlit.workflow_prompt_settings import render_node_prompts

workspace = "sft-only"
st.session_state.setdefault("workflow-node-scope:sft-only", {"source_cleaning": True, "nodes": ["ingest", "cpt", "sft"], "source_mode": "文档资料"})
bindings = st.session_state.get("workflow-node-bindings:sft-only", {})
endpoints = {"local": {"models": ["cleaner"], "base_url": "http://localhost:1234/v1", "prices": {"input_per_1m_usd": 0, "output_per_1m_usd": 0}}}
def save_field(workspace, key):
    st.session_state.setdefault("workflow-form-draft:" + workspace, {})[key] = deepcopy(st.session_state[key])
config = render_cpt_settings(workspace, "文档资料", bindings, endpoints, save_field=save_field, source_cleaning=True)
render_node_prompts("cpt", "文档资料", workspace, save_field=save_field, cpt_processing=config, source_cleaning=True)
st.session_state["fixture-cleaning"] = config
'''


def test_sft_only_shared_node_exposes_models_prompts_and_local_cleaning():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert ui.radio(key="workflow-cpt-processing-mode:sft-only").label == "CPT 清洗方式"
    assert ui.selectbox(key="node-model:sft-only:cpt:generation:backend")
    assert ui.selectbox(key="node-model:sft-only:cpt:jev:backend")
    prompt = ui.selectbox(key="workflow-node-prompt-selection:sft-only:cpt")
    assert prompt.options == ["资料清洗", "资料保真核对"]
    assert any("本次不导出 CPT" in item.value for item in ui.info)
    ui.radio(key="workflow-cpt-processing-mode:sft-only").set_value("native").run()
    assert not ui.exception and ui.session_state["fixture-cleaning"] == {"mode": "native"}
    assert not any(widget.key.startswith("node-model:sft-only:cpt:") for widget in ui.selectbox)
    assert any("合格正文" in item.value for item in ui.info)


def test_shared_cpt_settings_do_not_claim_cpt_export_is_unneeded_when_selected():
    ui = AppTest.from_string(SCRIPT.replace(
        "source_cleaning=True)", "source_cleaning=True, targets=['cpt', 'sft'])", 1)).run()
    assert not ui.exception
    assert any("本次导出 CPT 语料" in item.value for item in ui.info)
    assert not any("不导出 CPT" in item.value or "不需要导出 CPT" in item.value for item in ui.info)


def test_custom_cleaning_prompt_can_be_restored_to_default_in_place():
    ui = AppTest.from_string(SCRIPT).run()
    key = "workflow-node-prompt:sft-only:cpt:workflow.cpt_clean"
    ui.text_area(key=key).set_value("My custom source cleaning prompt.").run()
    assert not ui.exception
    assert ui.session_state["workflow-form-draft:sft-only"][key] == "My custom source cleaning prompt."
    ui.button(key="workflow-node-prompt-reset:sft-only:cpt:workflow.cpt_clean").click().run()
    assert not ui.exception
    from lib.domain.workflow_node_prompts import builtin_node_prompt
    assert ui.text_area(key=key).value == builtin_node_prompt("workflow.cpt_clean")


def test_cleaning_model_can_be_reused_for_fidelity_review_with_clear_labels():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["workflow-node-bindings:sft-only"] = {"cpt": {
        "generation": {"backend": "local", "model": "cleaner", "context_window_tokens": 131072,
                       "max_output_tokens": 32768}}}
    ui.run()
    assert not ui.exception
    button = ui.button(key="node-model:sft-only:cpt:jev:reuse-generation")
    assert button.label == "沿用本节点清洗模型"
    button.click().run()
    assert not ui.exception
    roles = ui.session_state["workflow-node-bindings:sft-only"]["cpt"]
    assert roles["generation"] == roles["jev"]
