"""Depth choices survive real node edits and storage, without model calls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.bootstrap.prompt_library import prompt_library_application
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.prompt_library import validate_prompt_payload
from lib.domain.reasoning_depth import REASONING_DEPTHS
from lib.presentation.streamlit import workflow_generation_settings as settings
from lib.presentation.streamlit.i18n import translate_label
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
from tests.test_creation_draft_storage import persistent_script
from tests.test_workflow_draft import SCRIPT
from tests.test_workflow_prompt_library import library_key, workbench


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """This module checks local settings only; no model fixture is needed."""
    yield


@pytest.mark.parametrize("node", ["sft", "cot", "multiturn"])
@pytest.mark.parametrize("depth", REASONING_DEPTHS)
def test_depth_roundtrip_restarts_named_drafts_and_recipe_reuse(tmp_path, node, depth):
    key = f"workflow-generation-reasoning-depth:w:{node}"
    values = {f"workflow-generation-enabled:w:{node}": False, key: depth}
    app = creation_draft_application(tmp_path)
    app.replace(values)
    saved = app.save_snapshot("Explanation depth")
    assert creation_draft_application(tmp_path).load() == values
    app.update({key: "auto"})
    assert app.restore_snapshot(saved) == values
    recipe = {"sources": [], "targets": [node], "brief": "Explain a task.",
              "node_generation": {node: {"enabled": False, "reasoning_depth": depth}}}
    original = deepcopy(recipe)
    copied = recipe_to_draft(recipe, "Copied", "w", [])["values"]
    assert copied[key] == depth
    assert recipe == original


@pytest.mark.parametrize("key,value", [
    ("workflow-generation-reasoning-depth:w", "deep"),
    ("workflow-generation-reasoning-depth:w:sft:extra", "deep"),
    ("workflow-generation-reasoning-depth:w:trim", "deep"),
    ("workflow-generation-reasoning-depth::sft", "deep"),
    ("workflow-generation-reasoning-depth:w:sft", "max"),
    ("workflow-generation-reasoning-depth:w:sft", ["deep"]),
    ("workflow-generation-reasoning-depth:w:sft", True),
])
def test_depth_drafts_validate_exact_scope_and_type(key, value):
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({key: value})


def test_legacy_and_reset_snapshot_do_not_add_a_depth_requirement(monkeypatch):
    recipe = {"sources": [], "targets": ["sft"], "node_generation": {"sft": {"style": "default"}}}
    values = recipe_to_draft(recipe, "Legacy", "w", [])["values"]
    key = "workflow-generation-reasoning-depth:w:sft"
    assert key not in values
    state = {"workflow-form-draft:w": values, key: "auto"}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=state))
    assert "reasoning_depth" not in settings.generation_snapshot("w", ["sft"])["sft"]
    values[key] = "deep"
    assert settings.generation_snapshot("w", ["sft"])["sft"]["reasoning_depth"] == "deep"
    values[key] = "auto"
    assert "reasoning_depth" not in settings.generation_snapshot("w", ["sft"])["sft"]


def test_node_depth_is_opt_in_and_survives_hide_disable_and_reset():
    ui = AppTest.from_string(SCRIPT).run()
    key = "workflow-generation-reasoning-depth:fixture:sft"
    enabled = "workflow-generation-enabled:fixture:sft"
    assert ui.selectbox(key=key).value == "auto"
    assert ui.selectbox(key=key).disabled
    assert key not in ui.session_state["workflow-form-draft:fixture"]
    ui.toggle(key=enabled).set_value(True).run()
    ui.selectbox(key=key).set_value("deep").run()
    ui.toggle(key=enabled).set_value(False).run()
    ui.button(key="fixture-node:ingest").click().run()
    ui.button(key="fixture-node:sft").click().run()
    assert ui.selectbox(key=key).value == "deep"
    assert ui.selectbox(key=key).disabled
    ui.toggle(key=enabled).set_value(True).run()
    ui.selectbox(key=key).set_value("auto").run()
    assert ui.session_state["workflow-form-draft:fixture"][key] == "auto"
    assert not ui.exception


def test_actual_widget_autosave_restores_depth_after_restart(tmp_path):
    code = persistent_script(tmp_path)
    ui = AppTest.from_string(code).run()
    key = "workflow-generation-reasoning-depth:fixture:sft"
    ui.toggle(key="workflow-generation-enabled:fixture:sft").set_value(True).run()
    ui.selectbox(key=key).set_value("mixed").run()
    assert creation_draft_application(tmp_path).load()[key] == "mixed"
    fresh = AppTest.from_string(code).run()
    assert fresh.selectbox(key=key).value == "mixed"
    assert not ui.exception and not fresh.exception


def test_personal_depth_template_and_legacy_reset_preserve_saved_records(tmp_path):
    library = prompt_library_application(root=tmp_path)
    scope = "generation:sft"
    old_payload = {"style": "custom", "instruction": "Keep the decisive evidence."}
    old = library.save_template(scope, "Legacy style", old_payload)
    ui = workbench(tmp_path)
    key = "workflow-generation-reasoning-depth:fixture:sft"
    ui.toggle(key="workflow-generation-enabled:fixture:sft").set_value(True).run()
    ui.selectbox(key=key).set_value("deep").run()
    ui.selectbox(key=library_key(scope, "selection")).set_value(old["id"]).run()
    assert ui.selectbox(key=key).value == "auto"
    assert library.get_template(old["id"], scope)["payload"] == old_payload
    ui.selectbox(key=key).set_value("deep").run()
    ui.text_input(key=library_key(scope, "name")).set_value("Detailed explanation").run()
    ui.button(key=library_key(scope, "save")).click().run()
    saved = next(row for row in library.list_templates(scope) if row["name"] == "Detailed explanation")
    assert library.get_template(saved["id"], scope)["payload"]["reasoning_depth"] == "deep"
    fresh = workbench(tmp_path)
    fresh.selectbox(key=library_key(scope, "selection")).set_value(saved["id"]).run()
    assert fresh.selectbox(key=key).value == "deep"
    fresh.button(key=library_key(scope, "restore")).click().run()
    assert fresh.selectbox(key=key).value == "auto"
    assert fresh.toggle(key="workflow-generation-enabled:fixture:sft").value is False
    assert library.get_template(saved["id"], scope)["payload"]["reasoning_depth"] == "deep"
    # Save the neutral editor as a new template: UI state is not a requirement.
    fresh.text_input(key=library_key(scope, "name")).set_value("No depth control").run()
    fresh.button(key=library_key(scope, "save")).click().run()
    neutral = next(row for row in library.list_templates(scope) if row["name"] == "No depth control")
    assert "reasoning_depth" not in library.get_template(neutral["id"], scope)["payload"]
    assert not ui.exception and not fresh.exception


@pytest.mark.parametrize("depth", REASONING_DEPTHS)
@pytest.mark.parametrize("node", ["sft", "cot", "multiturn"])
def test_personal_template_allows_depth_only_in_generation_scope(depth, node):
    payload = {"style": "default", "instruction": "", "reasoning_depth": depth}
    assert validate_prompt_payload(f"generation:{node}", payload) == payload
    with pytest.raises(ValueError, match="prompt_library_invalid_payload"):
        validate_prompt_payload("node:sft:workflow.sft", {"text": "Keep evidence.", "reasoning_depth": depth})


def test_depth_labels_help_and_summary_have_english_translations(tmp_path):
    ui = workbench(tmp_path, english=True)
    key = "workflow-generation-reasoning-depth:fixture:sft"
    assert ui.selectbox(key=key).label == "Authored reasoning depth"
    assert "Deep · full explanation" in ui.selectbox(key=key).options
    assert "native thinking effort" in ui.selectbox(key=key).help
    ui.toggle(key="workflow-generation-enabled:fixture:sft").set_value(True).run()
    ui.selectbox(key=key).set_value("deep").run()
    summary = settings.generation_summary({"sft": {"enabled": True, "style": "default", "reasoning_depth": "deep"}}, "en")
    assert "Deep · full explanation" in summary
    for label in settings.REASONING_DEPTH_LABELS.values():
        assert translate_label(label, "en") != label
    assert not ui.exception
