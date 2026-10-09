from types import SimpleNamespace
import re

from streamlit.testing.v1 import AppTest

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


def test_shared_node_model_controls_translate_without_chinese_leaks():
    for phrase in ("质量评审模型", "沿用本节点生成模型", "复用其他节点模型", "已有模型配置", "用于当前角色",
                   "同一模型可用于多个节点，也可同时用于生成和评审；各角色的参数分别保存。",
                   "复制生成模型及 token 上限到评审配置；只在点击时替换，之后可分别调整。",
                   "只复制到当前角色。模型服务、模型和 token 上限可继续分别调整。"):
        assert not re.search(r"[\u4e00-\u9fff]", translate(phrase, "en")), phrase


def test_persistent_creation_draft_status_translates_completely():
    assert translate("配置草稿未能保存或恢复。当前修改仍保留在会话中。", "en") == (
        "The draft could not be saved or restored. Your changes remain in this session.")
    assert translate("参数、目标与需求文本自动保存到当前工作区；上传文件和节点模型选择需重新确认。", "en") == (
        "Settings, goals, and brief text are saved in this workspace. Reconfirm uploads and node models when you reopen it.")


def test_generation_style_and_cleanup_controls_have_complete_english_copy():
    import ast
    from pathlib import Path
    from lib.domain.workflow_generation import STYLE_PRESETS
    from lib.presentation.streamlit.workflow_generation_settings import (
        STYLE_LABELS, TRIM_LABELS, TRIM_DESCRIPTIONS,
    )

    source = Path(__file__).resolve().parents[1] / "lib/presentation/streamlit/workflow_generation_settings.py"
    phrases = [node.value for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and re.search(r"[\u4e00-\u9fff]", node.value)]
    phrases += [*STYLE_PRESETS.values(), *STYLE_LABELS.values(), *TRIM_LABELS.values(),
                *TRIM_DESCRIPTIONS.values(), "CoT 推理生成", "推理链修剪与核验", "SFT 训练文件格式"]
    for phrase in phrases:
        assert not re.search(r"[\u4e00-\u9fff]", translate(phrase, "en")), phrase
        assert translate(phrase, "zh") == phrase


def test_node_prompt_controls_have_complete_english_copy():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "lib/presentation/streamlit/workflow_prompt_settings.py"
    phrases = [node.value for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and re.search(r"[\u4e00-\u9fff]", node.value)]
    for phrase in phrases:
        assert not re.search(r"[\u4e00-\u9fff]", translate(phrase, "en")), phrase
        assert translate(phrase, "zh") == phrase


def test_preference_review_controls_and_progress_translate_completely():
    assert translate("编辑两个回答", "en") == "Edit both responses"
    assert translate("已审核 12 对 · 待处理 50000 对", "en") == "Reviewed 12 · Pending 50000"
    assert translate("提示 3 轮", "en") == "Prompt: 3 turns"
    assert translate("生成已审核 RLAIF 版本", "en") == "Create reviewed RLAIF release"
    assert translate_markup('<strong>偏好审核队列</strong><span data-user-content>编辑两个回答</span>', "en") == (
        '<strong>Preference review queue</strong><span data-user-content>编辑两个回答</span>')


def test_review_import_translates_controls_and_preserves_dataset_name():
    assert translate("将所选样本加入待审", "en") == "Import selected examples"
    assert translate("检测到 50000 条样本，目标数据集：工作流", "en") == "50000 examples found. Target dataset: 工作流"
    assert translate("返回审核工作台", "en") == "Return to review"


def test_explicit_user_markup_preserves_names_and_nested_attributes():
    source = '<strong data-user-content title="工作流">工作流<span>执行中</span></strong><span>执行中</span>'
    assert translate_markup(source, "en") == (
        '<strong data-user-content title="工作流">工作流<span>执行中</span></strong><span>Running</span>')
    assert translate_markup(source, "zh") == source


def test_translates_known_copy_and_preserves_unknown_user_text():
    assert translate("数据生成", "en") == "Create Data"
    assert translate("最近任务", "en") == "Recent tasks"
    assert translate("打开Data Library", "en") == "Open Data Library"
    assert translate("系统设置视图", "en") == "Settings view"
    assert translate("已确认 3 / 3", "en") == "3 / 3 approved"
    assert translate("此确认点已通过；当时的确认参数未保存在记录中。", "en") == "This check passed. Its settings were not saved in the record."
    assert translate("数据生成", "zh") == "数据生成"
    assert translate("用户上传的内容", "en") == "用户上传的内容"


def test_task_shortcut_and_command_check_copy_has_english_labels():
    assert translate("排队中", "en") == "Queued"
    assert translate("本次命令需要确认", "en") == "This command needs confirmation"
    assert translate("高级单项工具", "en") == "Advanced single-task tools"
    assert translate("当前样本通过批量导出检查。", "en") == "These examples pass bulk export checks."
    assert translate("人工审核可继续进行。AI 修订不会自动放行，请联系管理员核对确认记录。", "en").startswith(
        "You can continue reviewing manually.")
    assert translate_markup("<small>请核对下面与当前命令有关的条件</small>", "en") == (
        "<small>Check the conditions for this command</small>")


def test_translates_icon_prefixed_navigation_labels():
    assert translate_label("◈　数据生成", "en") == "◈　Create Data"


def test_english_picker_can_render_numeric_options():
    ui = AppTest.from_string('''
import streamlit as st
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state["ui_language"] = "en"
install_streamlit_localization()
st.selectbox("每页条数", (1, 3, 5), index=1)
''').run()
    assert not ui.exception
    assert ui.selectbox[0].value == 3
    assert ui.selectbox[0].label == "Rows per page"


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


def test_source_preview_and_shortcut_copy_has_no_chinese_interface_leaks():
    from lib.presentation.streamlit.document_preview import _ERRORS
    from lib.presentation.streamlit.i18n import UntranslatedText

    phrases = [*_ERRORS.values(), "用于生成", "导入资料", "来源分块预览", "选择审核目标",
               "预览文档", "预览解析与分块", "正在本机解析文档…", "片段序号",
               "预览来源", "工作流产物", "已有对话文件", "逐条处理状态和来源证据",
               "解析字符数：250,000 · 分块数：125",
               "当前片段字符数：2,000 · 来源位置：document:chunk:7",
               "有 2 份已选资料已失联、超限或不属于本机来源目录，已移出本次选择；请重新添加。",
               "仅列出前 5,000 份来源，已选资料会保留；更多文件可从资料库搜索后添加。",
               "单次生成最多使用 200 份资料。请先在工作台减少已选资料，再添加新文件。",
               "资料未能加入生成草稿。请检查文件是否变化，或本机存储是否可用。",
               "无法读取或校验任务产物，请检查本次任务文件后重试。"]
    for phrase in phrases:
        assert not re.search(r"[\u4e00-\u9fff]", translate(phrase, "en")), phrase
        assert translate(phrase, "zh") == phrase
    assert translate(UntranslatedText("来源分块预览"), "en") == "来源分块预览"
    assert translate("当前片段字符数：2,000 · 来源位置：document:chunk:7", "en") == (
        "Chunk characters: 2,000 · Source location: document:chunk:7")


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
    assert translate_label("沿用 CPT 节点的 customer-模型", "en") == "Use CPT's customer-模型"
    assert translate("查看下个未核对片段", "en") == "Open next unchecked trace section"
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
    assert translate("仍有模型请求正在执行；待这些请求结算后再清零预算。", "en") == (
        "Model requests are still running. Reset the budget after they finish."
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


def test_workbench_styles_keep_visible_copy_out_of_css():
    style = workbench_style("en")
    assert style == workbench_style("zh")
    assert not re.search(r"[\u4e00-\u9fff]", style)
    assert not re.search(r"(?:^|[;{])\s*content\s*:", style)


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


def test_planning_failure_reason_is_localized_inside_run_message():
    assert translate(
        "运行失败：规划批次包含重复任务，请重试当前批次。。已完成的步骤与模型响应已保存。", "en"
    ) == (
        "Run failed: The planning batch contains duplicate tasks. Retry this batch. "
        "Completed steps and model responses are saved."
    )
