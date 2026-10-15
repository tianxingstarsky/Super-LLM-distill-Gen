"""Authored reasoning language stays node-local, durable, and opt-in."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.bootstrap.prompt_library import prompt_library_application
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.prompt_library import validate_prompt_payload
from lib.domain.workflow_generation import (
    REASONING_LANGUAGES, STYLE_PRESETS, reasoning_language_instruction,
    style_for_sample, validate_node_generation,
)
from lib.presentation.streamlit import workflow_generation_settings as settings
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
from tests.test_workflow_draft import SCRIPT
from tests.test_workflow_prompt_library import library_key, workbench
from tests.test_creation_draft_storage import persistent_script


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All checks are local settings, storage, or Streamlit doubles."""
    yield


@pytest.mark.parametrize("language", REASONING_LANGUAGES)
@pytest.mark.parametrize("node", ["sft", "cot", "multiturn"])
def test_language_normalization_is_independent_of_style_and_keeps_input(node, language):
    supplied = {node: {"enabled": False, "style": "custom", "reasoning_language": language}}
    original = deepcopy(supplied)
    assert validate_node_generation(supplied) == {
        node: {"enabled": False, "style": "custom", "instruction": "", "reasoning_language": language}}
    assert supplied == original


@pytest.mark.parametrize("value", [None, True, [], {}, 1, "", "mixed", "ZH", "zh-en"])
def test_unknown_language_never_enters_recipe_or_prompt(value):
    with pytest.raises(ValueError, match="invalid_node_generation"):
        validate_node_generation({"sft": {"reasoning_language": value}})
    with pytest.raises(ValueError, match="invalid_node_generation"):
        reasoning_language_instruction(value)


def test_absent_auto_and_disabled_language_do_not_change_existing_style_output():
    config = {"style": "concise", "instruction": "Keep the actual premises."}
    expected = {"preset": "concise", "instruction": STYLE_PRESETS["concise"] + "\n" + config["instruction"]}
    assert style_for_sample(config, "same-sample") == expected
    assert style_for_sample({**config, "reasoning_language": "auto"}, "same-sample") == expected
    assert style_for_sample({**config, "enabled": False, "reasoning_language": "zh_en"}, "same-sample") == expected
    assert validate_node_generation({"sft": config})["sft"] == {"enabled": True, **config}
    assert reasoning_language_instruction() == ""


@pytest.mark.parametrize("language", ["zh", "en", "zh_en"])
def test_active_language_is_separate_from_user_style_and_answer_language(language):
    config = {"style": "custom", "instruction": "Use concrete evidence.", "reasoning_language": language}
    actual = style_for_sample(config, "candidate")
    assert actual == {"preset": "custom", "reasoning_language": language,
                      "instruction": config["instruction"] + "\n" + reasoning_language_instruction(language)}
    assert "最终答案跟随当前用户的语言与明确要求" in actual["instruction"]
    assert "不修改或冒称上游原生推理" in actual["instruction"]
    assert "不在答案或推理正文复述这些配置" in actual["instruction"]


def test_bilingual_reasoning_is_contentful_code_switching_without_rigid_layout():
    instruction = reasoning_language_instruction("zh_en")
    assert "中文与 English" in instruction
    assert "两种语言共同表达具体依据、推导与核对" in instruction
    assert "不做逐句双语翻译" in instruction
    assert "不设置固定语言比例或固定步骤" in instruction
    assert "不能只给单语正文添加几个英文标签、术语或重复翻译" in instruction
    ids = [f"sample-{index}" for index in range(20)]
    config = {"style": "mixed", "reasoning_language": "zh_en"}
    assert {key: style_for_sample(config, key) for key in ids} == {
        key: style_for_sample(config, key) for key in reversed(ids)}


@pytest.mark.parametrize("language", REASONING_LANGUAGES)
@pytest.mark.parametrize("node", ["sft", "cot", "multiturn"])
def test_draft_language_survives_restart_named_restore_and_recipe_reuse(tmp_path, node, language):
    key = f"workflow-generation-reasoning-language:default:{node}"
    values = {f"workflow-generation-enabled:default:{node}": False, key: language}
    app = creation_draft_application(tmp_path)
    app.replace(values)
    saved = app.save_snapshot("Language choice")
    assert creation_draft_application(tmp_path).load() == values
    app.update({key: "en"})
    assert app.restore_snapshot(saved) == values
    recipe = {"sources": [], "targets": [node], "brief": "Create examples.",
              "node_generation": {node: {"enabled": False, "reasoning_language": language}}}
    assert recipe_to_draft(recipe, "Copied", "default", [])["values"][key] == language
    assert recipe["node_generation"][node] == {"enabled": False, "reasoning_language": language}


@pytest.mark.parametrize("key,value", [
    ("workflow-generation-reasoning-language:w", "zh"),
    ("workflow-generation-reasoning-language:w:sft:extra", "zh"),
    ("workflow-generation-reasoning-language:w:trim", "zh"),
    ("workflow-generation-reasoning-language::sft", "zh"),
    ("workflow-generation-reasoning-language:w:sft", "mixed"),
    ("workflow-generation-reasoning-language:w:sft", ["en"]),
])
def test_language_draft_scope_is_not_a_generic_whitelist(key, value):
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({key: value})


def test_old_recipe_and_opening_old_draft_do_not_invent_language(monkeypatch):
    recipe = {"sources": [], "targets": ["sft"], "node_generation": {"sft": {"style": "default"}}}
    values = recipe_to_draft(recipe, "Legacy", "w", [])["values"]
    key = "workflow-generation-reasoning-language:w:sft"
    assert key not in values
    state = {"workflow-form-draft:w": values, key: "auto"}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=state))
    assert "reasoning_language" not in settings.generation_snapshot("w", ["sft"])["sft"]
    assert key not in values
    values[key] = "auto"
    assert settings.generation_snapshot("w", ["sft"])["sft"]["reasoning_language"] == "auto"


def test_real_node_control_preserves_disabled_choice_after_switching():
    ui = AppTest.from_string(SCRIPT).run()
    key = "workflow-generation-reasoning-language:fixture:sft"
    enabled = "workflow-generation-enabled:fixture:sft"
    assert ui.selectbox(key=key).value == "auto"
    assert ui.selectbox(key=key).disabled
    assert key not in ui.session_state["workflow-form-draft:fixture"]
    ui.toggle(key=enabled).set_value(True).run()
    ui.selectbox(key=key).set_value("zh_en").run()
    ui.toggle(key=enabled).set_value(False).run()
    ui.button(key="fixture-node:ingest").click().run()
    ui.button(key="fixture-node:sft").click().run()
    assert ui.selectbox(key=key).value == "zh_en"
    assert ui.selectbox(key=key).disabled
    assert ui.session_state["workflow-form-draft:fixture"][key] == "zh_en"
    assert not ui.exception


def test_real_widget_autosave_restores_language_after_application_restart(tmp_path):
    app = creation_draft_application(tmp_path)
    code = persistent_script(tmp_path)
    ui = AppTest.from_string(code).run()
    enabled = "workflow-generation-enabled:fixture:sft"
    key = "workflow-generation-reasoning-language:fixture:sft"
    ui.toggle(key=enabled).set_value(True).run()
    ui.selectbox(key=key).set_value("zh_en").run()
    ui.toggle(key=enabled).set_value(False).run()
    assert app.load()[key] == "zh_en"
    fresh = AppTest.from_string(code).run()
    assert fresh.selectbox(key=key).value == "zh_en"
    assert fresh.selectbox(key=key).disabled
    assert not ui.exception and not fresh.exception
    assert not any("草稿未能" in item.value for item in (*ui.warning, *fresh.warning))


def test_personal_template_roundtrip_and_old_template_neutral_default(tmp_path):
    library = prompt_library_application(root=tmp_path)
    scope = "generation:sft"
    old_payload = {"style": "custom", "instruction": "Keep the decisive evidence."}
    old = library.save_template(scope, "Existing template", old_payload)
    ui = workbench(tmp_path)
    key = "workflow-generation-reasoning-language:fixture:sft"
    ui.toggle(key="workflow-generation-enabled:fixture:sft").set_value(True).run()
    ui.selectbox(key=key).set_value("zh_en").run()
    ui.selectbox(key=library_key(scope, "selection")).set_value(old["id"]).run()
    assert ui.selectbox(key=key).value == "auto"
    assert ui.text_area(key="workflow-generation-instruction:fixture:sft").value == old_payload["instruction"]
    assert library.get_template(old["id"], scope)["payload"] == old_payload
    ui.selectbox(key=key).set_value("zh_en").run()
    ui.text_input(key=library_key(scope, "name")).set_value("Bilingual explanation").run()
    ui.button(key=library_key(scope, "save")).click().run()
    saved = next(row for row in library.list_templates(scope) if row["name"] == "Bilingual explanation")
    assert library.get_template(saved["id"], scope)["payload"] == {**old_payload, "reasoning_language": "zh_en"}
    fresh = workbench(tmp_path)
    fresh.selectbox(key=library_key(scope, "selection")).set_value(saved["id"]).run()
    assert fresh.selectbox(key=key).value == "zh_en"
    fresh.button(key=library_key(scope, "restore")).click().run()
    assert fresh.selectbox(key=key).value == "auto"
    assert fresh.toggle(key="workflow-generation-enabled:fixture:sft").value is False
    assert library.get_template(saved["id"], scope)["payload"]["reasoning_language"] == "zh_en"
    assert not ui.exception and not fresh.exception


def test_optional_language_is_valid_only_in_generation_templates():
    payload = {"style": "default", "instruction": "", "reasoning_language": "zh_en"}
    assert validate_prompt_payload("generation:sft", payload) == payload
    assert validate_prompt_payload("generation:cot", payload) == payload
    assert validate_prompt_payload("generation:multiturn", payload) == payload
    with pytest.raises(ValueError, match="prompt_library_invalid_payload"):
        validate_prompt_payload("generation:sft", {**payload, "reasoning_language": "mixed"})
    with pytest.raises(ValueError, match="prompt_library_invalid_payload"):
        validate_prompt_payload("node:sft:workflow.sft", {"text": "Text.", "reasoning_language": "en"})


def test_multiturn_style_does_not_enable_cot_only_answer_transport():
    with pytest.raises(ValueError, match="invalid_cot_answer_mode"):
        validate_node_generation({"multiturn": {"style": "default", "answer_mode": "joint"}})
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({"workflow-generation-answer-mode:w:multiturn": "joint"})


@pytest.mark.parametrize("goal", ["knowledge_application", "document_reading", "mixed"])
@pytest.mark.parametrize("languages", [["zh"], ["en"], ["zh", "en"], ["en", "zh"]])
def test_director_goal_and_language_drafts_have_exact_typed_keys(goal, languages):
    values = {"workflow-director-learning-goal:w": goal,
              "workflow-director-user-languages:w": languages}
    result = validate_creation_draft(values)
    assert result == values
    result["workflow-director-user-languages:w"].reverse()
    assert values["workflow-director-user-languages:w"] == languages


@pytest.mark.parametrize("key,value", [
    ("workflow-director-user-languages:w", []),
    ("workflow-director-user-languages:w", ["en", "en"]),
    ("workflow-director-user-languages:w", ["zh", "fr"]),
    ("workflow-director-user-languages:w", "zh"),
    ("workflow-director-user-languages:w", [True]),
    ("workflow-director-user-languages:w:director", ["zh"]),
    ("workflow-director-learning-goal:w:director", "knowledge_application"),
    ("workflow-director-learning-goal:w", "closed_book"),
])
def test_director_goal_and_language_invalid_drafts_fail_before_storage(key, value):
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({key: value})


@pytest.mark.parametrize("enabled", [True, False])
def test_director_reuse_copies_only_explicit_learning_requirements(enabled):
    recipe = {"sources": [], "targets": ["sft"], "qa_director": {
        "enabled": enabled, "learning_goal": "knowledge_application", "user_languages": ["en", "zh"]}}
    original = deepcopy(recipe)
    values = recipe_to_draft(recipe, "Copied", "w", [])["values"]
    assert values["workflow-director-learning-goal:w"] == "knowledge_application"
    assert values["workflow-director-user-languages:w"] == ["en", "zh"]
    values["workflow-director-user-languages:w"].reverse()
    assert recipe == original
    del recipe["qa_director"]["learning_goal"], recipe["qa_director"]["user_languages"]
    old = recipe_to_draft(recipe, "Legacy", "w", [])["values"]
    assert "workflow-director-learning-goal:w" not in old
    assert "workflow-director-user-languages:w" not in old
