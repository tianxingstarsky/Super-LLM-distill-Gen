from types import SimpleNamespace

from lib.presentation.streamlit.i18n import (
    canonical_navigation_route,
    initialize_language,
    language_code,
    set_language_from_choice,
    translate,
    translate_label,
    translate_markup,
)


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


def test_language_choice_is_shareable_in_the_url():
    state = SimpleNamespace(session_state={}, query_params={})
    initialize_language(state)
    assert state.session_state["ui_language"] == "zh"
    assert set_language_from_choice(state, "English") == "en"
    assert state.query_params["lang"] == "en"
    initialize_language(state)
    assert state.session_state["ui_language"] == "en"
    assert state.session_state["ui-language-choice"] == "English"
