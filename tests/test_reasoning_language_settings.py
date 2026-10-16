"""Authored reasoning language stays node-local, durable, and opt-in."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.bootstrap.prompt_library import prompt_library_application
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.prompt_library import validate_prompt_payload
from lib.domain.workflow_generation import (
    REASONING_LANGUAGES, REASONING_LANGUAGE_CONTRACT_VERSION, STYLE_PRESETS, reasoning_language_instruction,
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


def test_frozen_bilingual_contract_keeps_original_instruction_byte_for_byte():
    expected = (
        "显式推理语言要求：显式推理自然结合中文与 English，让两种语言共同表达具体依据、推导与核对；"
        "可以在适合的语境中切换，不做逐句双语翻译，不设置固定语言比例或固定步骤。"
        "不能只给单语正文添加几个英文标签、术语或重复翻译来冒充中英混合推理。"
        "最终答案跟随当前用户的语言与明确要求，不因推理语言设置而强制双语。"
        "语言要求只控制新撰写的显式推理，不修改或冒称上游原生推理；"
        "事实正确、证据范围和用户任务优先于语言表现。"
        "不在答案或推理正文复述这些配置、角色指令、内部审核或生成流程。"
    )
    assert reasoning_language_instruction("zh_en") == expected
    assert reasoning_language_instruction("zh_en", version=1) == expected
    config = {"style": "custom", "instruction": "Keep my actual instruction.", "reasoning_language": "zh_en"}
    original = deepcopy(config)
    assert style_for_sample(config, "saved") == style_for_sample(config, "saved", reasoning_language_version=1)
    assert config == original


def test_bilingual_contract_v2_requires_full_paired_translation_and_relevant_coverage():
    instruction = reasoning_language_instruction("zh_en", version=2)
    assert "完整自然的中文分析段" in instruction
    assert "紧接一个忠实、完整的英文译段" in instruction
    assert "用空行分隔即可" in instruction
    assert "不能只给英文摘要" in instruction
    assert "术语、标签或零散英语词" in instruction
    for concept in ("任务理解", "相关知识与证据", "假设和适用条件", "推导中间关系",
                    "替代解释或反例", "计算或逻辑核对", "结论边界与剩余不确定性"):
        assert concept in instruction
    assert "不以提纲、结论或短小概括代替必要分析" in instruction
    assert "不强制编号、固定标签、固定步骤或固定长度" in instruction
    assert "不为凑篇幅重复论点" in instruction
    assert "不编造无关分支、证据或未执行的验证" in instruction
    assert "译段不额外添加中文段没有的结论" in instruction
    assert "不修改或冒称上游原生推理" in instruction
    assert "最终答案跟随当前用户的语言与明确要求" in instruction
    assert "不要描述私有资料的投喂、检索、分块或挑选过程" in instruction


@pytest.mark.parametrize("version,expected", [
    (1, "97d48c72d677834944e9d97844740d116e6383392ff714a5ac4aa383661705c2"),
    (2, "5a91e05d591adead69ba6422da525764f0b7e0e37e87f67aa2ebca2083868632"),
])
def test_frozen_bilingual_contract_versions_keep_their_original_utf8_hash(version, expected):
    assert hashlib.sha256(reasoning_language_instruction("zh_en", version=version).encode("utf-8")).hexdigest() == expected


def test_bilingual_contract_v3_requires_semantic_translation_with_complete_reasoning():
    assert REASONING_LANGUAGE_CONTRACT_VERSION == 3
    instruction = reasoning_language_instruction("zh_en", version=3)
    assert "关键概念、短语和关键句" in instruction
    assert "中英文对应须完整、忠实" in instruction
    assert "含义、作用、适用条件或可能歧义" in instruction
    assert "用这种跨语言澄清推进实际推导" in instruction
    assert "不强制中文在前" in instruction
    assert "不要把完整中文分析逐段复制成完整英文分析" in instruction
    assert "不将全文写成两份纯翻译" in instruction
    assert "脱离论证的词汇表、装饰性英语标签或英文术语" in instruction
    for concept in ("依据、前提、假设、适用条件", "依赖关系", "推导中间环节", "替代解释或反例",
                    "计算或逻辑核验", "结论边界与剩余不确定性"):
        assert concept in instruction
    assert "不放过会改变结论的推理要点" in instruction
    assert "不规定固定字数、步骤、条数" in instruction
    assert "不为拉长篇幅重复论点" in instruction
    assert "不编造无关分支、证据或未执行的工具调用和验证" in instruction
    assert "在知识应用任务中" in instruction
    assert "推理和答案直接运用相关知识、事实与条件自然解决问题" in instruction
    assert "‘从资料可以直接支持’、‘资料没有规定’" in instruction
    assert "真实文章理解任务可按用户要求讨论文章和引用原文" in instruction
    assert "公开研究名称与真实事实归属也应保留" in instruction
    assert "后台生成、评审、提示词、语言设置或角色安排" in instruction
    assert "最终答案跟随当前用户的语言与明确要求" in instruction
    assert "不修改或冒称上游原生推理" in instruction


@pytest.mark.parametrize("style", ["concise", "custom", "mixed"])
def test_v3_preserves_custom_instruction_and_stable_style_while_adding_semantic_translation(style):
    config = {"style": style, "instruction": "Keep the clinical definitions.\nPreserve the actual caveats.",
              "reasoning_language": "zh_en"}
    original = deepcopy(config)
    selected = style_for_sample(config, "sample", reasoning_language_version=3)
    assert config == original
    assert selected["preset"] == style_for_sample(config, "sample")["preset"]
    assert selected["reasoning_language"] == "zh_en"
    assert config["instruction"] in selected["instruction"]
    assert selected["instruction"].endswith(reasoning_language_instruction("zh_en", version=3))
    assert "关键概念、短语和关键句" in selected["instruction"]
    assert "紧接一个忠实、完整的英文译段" not in selected["instruction"]
    assert "不做逐句双语翻译" not in selected["instruction"]


@pytest.mark.parametrize("style", ["concise", "custom", "mixed"])
def test_v2_preserves_user_style_and_instruction_while_full_translation_takes_priority(style):
    config = {"style": style, "instruction": "Use my chosen terms.\nDo not alter this instruction.",
              "reasoning_language": "zh_en"}
    original = deepcopy(config)
    selected = style_for_sample(config, "sample", reasoning_language_version=2)
    legacy = style_for_sample(config, "sample")
    assert config == original
    assert selected["preset"] == legacy["preset"]
    assert selected["reasoning_language"] == "zh_en"
    assert config["instruction"] in selected["instruction"]
    assert selected["instruction"].endswith(reasoning_language_instruction("zh_en", version=2))
    assert "完整覆盖和忠实互译优先于缩短" in selected["instruction"]
    assert "不做逐句双语翻译" not in selected["instruction"]
    assert "不做逐句双语翻译" in legacy["instruction"]


@pytest.mark.parametrize("language", ["auto", "zh", "en"])
@pytest.mark.parametrize("version", [2, 3])
def test_bilingual_version_does_not_change_other_language_contracts(language, version):
    assert reasoning_language_instruction(language, version=version) == reasoning_language_instruction(language)
    config = {"style": "reflective", "instruction": "Retain the actual evidence.",
              "reasoning_language": language}
    assert style_for_sample(config, "same", reasoning_language_version=version) == style_for_sample(config, "same")


@pytest.mark.parametrize("version", [None, False, True, "2", "3", 0, 4, 2.0, 3.0, [], {}])
def test_unknown_language_contract_versions_fail_before_generation(version):
    with pytest.raises(ValueError, match="invalid_node_generation"):
        reasoning_language_instruction("zh_en", version=version)
    with pytest.raises(ValueError, match="invalid_node_generation"):
        style_for_sample({"reasoning_language": "zh_en"}, "sample", reasoning_language_version=version)


@pytest.mark.parametrize("version", [2, 3])
def test_disabled_language_versions_do_not_enable_style_or_change_native_reasoning(version):
    config = {"enabled": False, "style": "default", "reasoning_language": "zh_en"}
    assert style_for_sample(config, "sample", reasoning_language_version=version) == {
        "preset": "default", "instruction": STYLE_PRESETS["default"]}


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
