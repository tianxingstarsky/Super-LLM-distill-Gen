"""Independent template arithmetic must not inherit a document pipeline."""

import pytest
from streamlit.testing.v1 import AppTest

from tests.test_workflow_draft import SCRIPT


TARGET_KEY = "workflow-targets:fixture:自动推荐"
THEME_KEY = "workflow-math-theme:fixture"


def _script(source=None):
    code = SCRIPT.replace(
        "    def task_runs(self): return []",
        "    def create_run(self, **kwargs):\n"
        "        st.session_state['fixture-submitted'] = kwargs\n"
        "        return '0' * 32\n"
        "    def task_runs(self): return []",
    ).replace(
        "def canvas(spec,selection_key,**kwargs):\n",
        "def canvas(spec,selection_key,**kwargs):\n"
        "    st.session_state['fixture-canvas'] = spec\n",
    ).replace(
        "st.checkbox('Show workbench',value=True,key='fixture-show')",
        "class NoModels:\n"
        "    def list_backends(self): return {'backends': [], 'roles': {}}\n"
        "st.checkbox('Show workbench',value=True,key='fixture-show')",
    ).replace("WorkflowNodeModelsApplication(Inventory())", "WorkflowNodeModelsApplication(NoModels())")
    return code.replace("'fixture.txt'", repr(str(source))) if source is not None else code


def _arithmetic_ui(*, source_mode="文档资料", source=None):
    ui = AppTest.from_string(_script(source))
    ui.session_state[TARGET_KEY] = ["gsm8k"]
    ui.session_state["workflow-source-mode:fixture"] = source_mode
    ui.session_state["workflow-review-repair-mode:fixture"] = "human"
    return ui


@pytest.mark.parametrize("source_mode", ["文档资料", "Agent 上下文", "知识库检索", "开放需求"])
def test_template_arithmetic_submits_without_models_or_sources_and_ignores_stale_source_settings(source_mode):
    ui = _arithmetic_ui(source_mode=source_mode)
    # An earlier document/search draft must not block or leak into this branch.
    ui.session_state["workflow-form-draft:fixture"] = {
        "workflow-sources:fixture:文档资料": ["fixture.txt"],
        "workflow-document-parse-mode:fixture": "vision",
        "workflow-web-research-enabled:fixture": True,
        "workflow-web-research-query:fixture": "Earlier unrelated topic",
        "workflow-open-brief:fixture": "An earlier source-dependent request.",
    }
    ui.session_state["workflow-document-parse-mode-draft:fixture"] = "vision"
    ui.session_state["workflow-upload-error:fixture:" + source_mode] = "stale-upload-error"
    ui.session_state["knowledge-result:fixture"] = (
        ["old-retrieval.txt"], {"query": "Earlier knowledge query", "hits": []},
    )
    ui.run()
    assert not ui.exception
    assert ui.text_area(key=THEME_KEY).value == ""
    assert not ui.get("file_uploader")
    assert not any(button.key == "fixture-node:ingest" for button in ui.button)
    assert not ui.button(key="workflow-create:fixture").disabled

    ui.button(key="workflow-create:fixture").click().run()
    assert not ui.exception
    submitted = ui.session_state["fixture-submitted"]
    assert submitted["targets"] == ["gsm8k"]
    assert submitted["sources"] == []
    assert submitted["brief"] == ""
    assert submitted["node_models"] == {}
    assert submitted["document_parser"] == {"mode": "native"}
    assert submitted["knowledge_retrieval"] is None
    assert submitted["web_research"] is None
    assert submitted["source_names"] == {}
    assert submitted["review_repair"]["mode"] == "human"
    assert submitted["cpt_processing"] is None


def test_arithmetic_theme_survives_target_switch_and_navigation_then_is_submitted():
    ui = _arithmetic_ui().run()
    theme = "A community garden shares seeds and harvests."
    ui.text_area(key=THEME_KEY).set_value(theme).run()
    assert ui.session_state["workflow-form-draft:fixture"][THEME_KEY] == theme
    ui.pills(key=TARGET_KEY).set_value(["sft"]).run()
    assert not any(widget.key == THEME_KEY for widget in ui.text_area)
    ui.pills(key=TARGET_KEY).set_value(["gsm8k"]).run()
    assert ui.text_area(key=THEME_KEY).value == theme
    ui.checkbox(key="fixture-show").uncheck().run()
    ui.checkbox(key="fixture-show").check().run()
    assert not ui.exception
    assert ui.text_area(key=THEME_KEY).value == theme
    ui.button(key="workflow-create:fixture").click().run()
    assert not ui.exception
    assert ui.session_state["fixture-submitted"]["brief"] == theme


def test_document_selection_survives_independent_arithmetic_and_returns_to_sft(tmp_path):
    source = tmp_path / "fixture.txt"
    source.write_text("A source retained for the later SFT run.", encoding="utf-8")
    ui = AppTest.from_string(_script(source)).run()
    source_key = "workflow-sources:fixture:文档资料"
    ui.multiselect(key=source_key).set_value([str(source)]).run()
    ui.pills(key=TARGET_KEY).set_value(["gsm8k"]).run()
    assert not any(widget.key == source_key for widget in ui.multiselect)
    assert ui.session_state["workflow-form-draft:fixture"][source_key] == [str(source)]
    ui.pills(key=TARGET_KEY).set_value(["sft"]).run()
    assert not ui.exception
    assert ui.multiselect(key=source_key).value == [str(source)]


def test_new_math_reasoning_preset_uses_source_based_sft_and_cot():
    ui = AppTest.from_string(_script()).run()
    ui.selectbox(key="workflow-preset:fixture").set_value("数学推理").run()
    assert not ui.exception
    assert ui.pills(key="workflow-targets:fixture:数学推理").value == ["sft", "cot"]
    assert any(button.key == "fixture-node:ingest" for button in ui.button)
    assert not any(button.key == "fixture-node:gsm8k" for button in ui.button)


@pytest.mark.parametrize("preset", ["数学推理", "自选目标"])
def test_explicit_saved_math_targets_are_not_replaced_by_new_preset_defaults(preset):
    key = f"workflow-targets:fixture:{preset}"
    saved = ["sft", "gsm8k", "cot"]
    ui = AppTest.from_string(_script())
    ui.session_state["workflow-form-draft:fixture"] = {
        "workflow-preset:fixture": preset, key: saved,
    }
    ui.run()
    assert not ui.exception
    assert ui.pills(key=key).value == saved
    ui.selectbox(key="workflow-preset:fixture").set_value("自动推荐").run()
    ui.selectbox(key="workflow-preset:fixture").set_value(preset).run()
    assert not ui.exception
    assert ui.pills(key=key).value == saved


def _canvas(targets, **options):
    from lib.presentation.streamlit.workflow_canvas import canvas_spec

    labels = {key: key for key in ("ingest", "cpt", "director", "sft", "gsm8k", "review", "package")}
    return canvas_spec(targets, {}, "gsm8k", labels, labels, recipe_version=20,
                       review_repair={"mode": "human"}, arithmetic_seed_protocol_version=1, **options)


def test_independent_arithmetic_canvas_starts_at_first_column_without_ingest():
    spec = _canvas(["gsm8k"])
    nodes = {node["id"]: node for node in spec["nodes"]}
    assert set(nodes) == {"gsm8k", "review", "package"}
    assert nodes["gsm8k"]["x"] == 24
    assert nodes["gsm8k"]["x"] < nodes["review"]["x"] < nodes["package"]["x"]
    assert spec["edges"] == (("gsm8k", "review"), ("review", "package"))


def test_mixed_arithmetic_canvas_preserves_document_dependencies_only_for_source_targets():
    spec = _canvas(["cpt", "sft", "gsm8k"], source_cleaning=True, qa_director=True)
    nodes = {node["id"]: node for node in spec["nodes"]}
    assert {"ingest", "cpt", "director", "sft", "gsm8k", "review", "package"} == set(nodes)
    assert not any(destination == "gsm8k" for _, destination in spec["edges"])
    assert {("ingest", "cpt"), ("cpt", "director"), ("director", "sft"),
            ("sft", "review"), ("cpt", "review"), ("gsm8k", "review"),
            ("review", "package")} == set(spec["edges"])
