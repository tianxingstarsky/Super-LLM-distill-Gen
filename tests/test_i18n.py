from types import SimpleNamespace

from lib.presentation.streamlit.i18n import (
    _localized_dataframe,
    canonical_navigation_route,
    initialize_language,
    language_code,
    set_language_from_choice,
    translate,
    translate_label,
    translate_markup,
)
from lib.presentation.streamlit.workflow_workbench_style import workbench_style


def test_language_choice_accepts_chinese_and_english_values():
    assert language_code("English") == "en"
    assert language_code("en") == "en"
    assert language_code("简体中文") == "zh"


def test_translates_known_copy_and_preserves_unknown_user_text():
    assert translate("数据生成", "en") == "Create Data"
    assert translate("最近任务", "en") == "Recent tasks"
    assert translate("打开Data Library", "en") == "Open Data Library"
    assert translate("系统设置视图", "en") == "Settings view"
    assert translate("已确认 3 / 3", "en") == "3 / 3 approved"
    assert translate("此确认点已通过；当时的确认参数未保存在记录中。", "en") == "This check passed. Its settings were not saved in the record."
    assert translate("数据生成", "zh") == "数据生成"
    assert translate("用户上传的内容", "en") == "用户上传的内容"


def test_translates_icon_prefixed_navigation_labels():
    assert translate_label("◈　数据生成", "en") == "◈　Create Data"


def test_navigation_recovers_localized_values_saved_by_old_widget():
    routes = ("首页", "总览", "系统设置")
    icons = {"首页": "⌂", "系统设置": "⚙"}
    assert canonical_navigation_route("⌂ Home", routes, icons) == "首页"
    assert canonical_navigation_route("⚙   Settings", routes, icons) == "系统设置"
    assert canonical_navigation_route("unknown route", routes, icons) == "总览"


def test_markup_localization_changes_interface_text_only():
    source = '<div class="card"><strong>数据生成</strong><small>用户上传的内容</small></div>'
    expected = '<div class="card"><strong>Create Data</strong><small>用户上传的内容</small></div>'
    assert translate_markup(source, "en") == expected


def test_markup_localizes_preview_labels_but_preserves_example_content():
    source = (
        '<div><div class="role-tag" data-i18n-before="工具返回 · ">web_search</div>'
        '<div class="md">工具返回</div><div class="df-artifact-body">样本内容</div>'
        '<strong>样本内容</strong></div>'
    )
    expected = (
        '<div><div class="role-tag" data-i18n-before="Tool result · ">web_search</div>'
        '<div class="md">工具返回</div><div class="df-artifact-body">样本内容</div>'
        '<strong>Example content</strong></div>'
    )
    assert translate_markup(source, "en") == expected


def test_translates_data_library_metadata_without_changing_paths():
    assert translate("数据视图", "en") == "Data view"
    assert translate("37 个匹配文件 · 来源优先", "en") == "37 matching files · sources first"
    assert translate("来源 / 用户资料/产品说明.txt", "en") == "Source / 用户资料/产品说明.txt"
    assert translate("用户上传的内容", "en") == "用户上传的内容"


def test_translates_preference_controls_and_pipeline_copy():
    assert translate_label("默认样本占比", "en") == "Default example share"
    assert translate_label("推理与反思", "en") == "Reasoning and reflection"
    assert translate_markup("<small>保留无风格注入的基线</small>", "en") == (
        "<small>Keep a baseline without injected styles</small>"
    )
    assert translate_label("按任务选择工具，只填写必要参数；运行记录会保留命令与输出。", "en") == (
        "Choose a tool and fill in the required settings. Runs keep their commands and output."
    )


def test_translates_dynamic_metadata_and_html_accessibility_labels():
    assert translate_label("预训练语料 · 1 项已选", "en") == "Pretraining text · 1 selected"
    assert translate("4 个步骤 · 6 条消息", "en") == "4 steps · 6 messages"
    assert translate("调用与返回 · 2 条消息", "en") == "Calls and results · 2 messages"
    assert translate("生成成对回答并通过质量检查后，DPO 候选会显示在这里供人工比较。", "en") == (
        "DPO candidates appear here for review after paired answers pass quality checks."
    )
    assert translate(
        "当前工作区没有通过产物校验的 ORPO 工作流。先在“自动工作流”生成 ORPO 候选，再进入人工审核。", "en"
    ) == "No checked ORPO workflow is available. Create ORPO candidates in Workflows, then review them here."
    assert translate_label("当前默认：deepseek", "en") == "Current default: deepseek"
    assert translate("剩余额度 $4.9999", "en") == "Remaining budget $4.9999"
    assert translate("预算已清零（原已用 $1.2500，已记审计）", "en") == (
        "Budget reset (previous usage $1.2500; audit recorded)"
    )
    assert translate("3 次调用已核对", "en") == "3 calls verified"
    assert translate("2 组重复调用已剪枝", "en") == "2 duplicate call groups pruned"
    assert translate("4 个工具定义", "en") == "4 tool definitions"
    assert translate("候选 2 · 排名 1", "en") == "Candidate 2 · Rank 1"
    assert translate("当前未达到放量条件：有效审核覆盖不足 90%、审核共识未达成", "en") == (
        "Release checks not met: Review coverage below 90%, Reviewer consensus not met"
    )
    assert translate("第 3 条消息（索引 2） · 来源：用户资料/问题.txt", "en") == (
        "Message 3 (index 2) · Source: 用户资料/问题.txt"
    )
    assert translate("对话过程", "en") == "Conversation flow"
    assert translate_markup("<span>⚠ 执行失败</span>", "en") == "<span>⚠ Execution failed</span>"
    assert translate_label("文档清洗、分块与去重。点击启用整组；下方可逐项调整。", "en") == (
        "Clean, split, and deduplicate documents. Click to enable this group. Adjust individual goals below."
    )
    assert translate_markup(
        '<button aria-label="本次包含的处理阶段" title="来源 / 用户资料/说明.txt">选择目标</button>',
        "en",
    ) == '<button aria-label="Stages in this run" title="Source / 用户资料/说明.txt">Choose goals</button>'


def test_workbench_card_copy_is_localized_for_english():
    style = workbench_style("en")
    assert 'content:"Clean, split, and deduplicate documents"' in style
    assert 'content:"SFT, multi-turn, and agent traces"' in style
    assert 'content:"ORPO, DPO, and RLAIF"' in style
    assert 'content:"CoT and checked arithmetic"' in style
    assert "文档清洗、分块与去重" not in style
    assert 'content:"文档清洗、分块与去重"' in workbench_style("zh")


def test_dataframe_localization_changes_headers_and_preserves_values():
    source = [{"样本 ID": "用户自定义中文内容", "审核状态": "已通过"}]
    assert _localized_dataframe(source, "en") == [
        {"Example ID": "用户自定义中文内容", "Review status": "已通过"}
    ]
    assert source[0]["样本 ID"] == "用户自定义中文内容"


def test_language_choice_is_shareable_in_the_url():
    state = SimpleNamespace(session_state={}, query_params={})
    initialize_language(state)
    assert state.session_state["ui_language"] == "zh"
    assert set_language_from_choice(state, "English") == "en"
    assert state.query_params["lang"] == "en"
    initialize_language(state)
    assert state.session_state["ui_language"] == "en"
    assert state.session_state["ui-language-choice"] == "English"
