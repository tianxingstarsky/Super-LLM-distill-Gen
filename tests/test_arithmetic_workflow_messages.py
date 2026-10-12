"""Arithmetic allocation failures give safe recovery steps in both UI languages."""

import re

import pytest
from streamlit.testing.v1 import AppTest


def test_registry_and_history_failures_have_localized_recovery_steps():
    from lib.presentation.streamlit.i18n import translate_label
    from lib.presentation.streamlit.workflow_page import _workflow_error

    for code in (
        "arithmetic_registry_missing", "arithmetic_registry_integrity_error", "arithmetic_registry_invalid_path",
        "arithmetic_history_invalid", "arithmetic_history_changed", "arithmetic_legacy_run_active",
        "arithmetic_legacy_run_requires_migration", "invalid_arithmetic_run_id", "invalid_arithmetic_ordinal",
        "invalid_arithmetic_theme", "invalid_arithmetic_history_source",
    ):
        chinese = _workflow_error(ValueError(code))
        english = translate_label(chinese, "en")
        assert code not in chinese and "请" in chinese
        assert english != chinese and not re.search(r"[\u4e00-\u9fff]", english), code
        assert not re.search(r"[\u4e00-\u9fff]", translate_label("运行失败：" + chinese, "en")), code
    for code in ("arithmetic_registry_missing", "arithmetic_registry_integrity_error"):
        message = translate_label(_workflow_error(code), "en")
        assert "backup" in message and "Keep" in message
        assert "delete the registry to restart" not in message or "Do not delete the registry" in message
    migration = translate_label(_workflow_error("arithmetic_legacy_run_requires_migration"), "en")
    assert translate_label("复制配置并修改", "en") in migration


def _progress_screen(language, production_mode):
    import streamlit as st
    from lib.presentation.streamlit.workflow_production_settings import render_delivery_progress
    st.session_state["ui_language"] = language
    state = {"status": "completed", "arithmetic_registry": {"stop_reason": "arithmetic_space_exhausted"}}
    if production_mode:
        state["production"] = {"version": 2, "stop_reason": "arithmetic_space_exhausted",
            "goals": {"gsm8k": {"goal": 1000, "eligible": 15, "stop_reason": "arithmetic_space_exhausted"}}}
    if production_mode == "mixed":
        state["status"] = "running"
        state["production"]["stop_reason"] = None
        state["production"]["goals"]["sft"] = {"goal": 100, "eligible": 30}
    render_delivery_progress(state)


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("production_mode", [None, "pure", "mixed"])
def test_space_exhaustion_is_a_normal_stop_with_preserved_results(language, production_mode):
    ui = AppTest.from_function(_progress_screen, args=(language, production_mode)).run()
    assert not ui.exception and not ui.error and not ui.warning
    needle = "本分支正常停止" if language == "zh" else "This branch stopped normally"
    notice = [item.value for item in ui.caption if needle in item.value]
    assert len(notice) == 1
    assert ("导出" if language == "zh" else "export") in notice[0]
    if language == "en":
        assert not any(re.search(r"[\u4e00-\u9fff]", item.value) for item in ui.caption)
        assert not any(re.search(r"[\u4e00-\u9fff]", item.proto.text) for item in ui.get("progress"))


@pytest.mark.parametrize("language", ["zh", "en"])
def test_registry_error_opens_arithmetic_node_without_resuming_or_changing_recipe(language):
    from copy import deepcopy
    from tests.test_workflow_run_issue_navigation import SCRIPT
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["ui_language"] = language
    ui.run()
    state = deepcopy(ui.session_state["fixture-state"])
    state.update(error="arithmetic_registry_missing", targets=["gsm8k"])
    state["stages"] = {
        "ingest": {"label": "输入解析", "status": "pending", "done": 0, "total": 0},
        "gsm8k": {"label": "模板算术题", "status": "pending", "done": 0, "total": 100},
        "package": {"label": "输出打包", "status": "pending", "done": 0, "total": 100},
    }
    recipe = {"targets": ["gsm8k"], "sources": [], "version": 20,
              "arithmetic_seed_protocol_version": 1, "arithmetic_registry_version": 1}
    ui.session_state["fixture-state"] = state
    ui.session_state["fixture-recipe"] = recipe
    ui.run()
    assert not ui.exception
    action = ui.button(key="workflow-run-failure:fixture")
    assert ("备份" if language == "zh" else "backup") in action.label
    action.click().run()
    assert not ui.exception
    assert ui.session_state["fixture-selected"] == "gsm8k"
    assert ui.session_state["fixture-state"] == state
    assert ui.session_state["fixture-recipe"] == recipe
    assert "fixture-begin-calls" not in ui.session_state
