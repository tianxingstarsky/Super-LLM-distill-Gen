"""Offline UI acceptance for stored issues and existing review-to-rework actions."""
from pathlib import Path
import re
import sys

import pytest
from streamlit.testing.v1 import AppTest


sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_human_workspace_ui import (WORKSPACE, context, create_workspace, complete_round,
                                     studio)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def _result(root, *, mode="auto"):
    from lib.bootstrap.workflows import human_augmentation_application

    sid = create_workspace(root, review_mode=mode, draft={"enabled": True, "seeds": [
        {"question": "什么时候停止泵？", "answer": "密封泄漏时停止泵。"}]})
    app = human_augmentation_application(root, root / "output")
    app.generate_round(sid, request_id="offline-fixture", expected_version=app.session(sid)["version"])
    parent, records = complete_round(root, sid, question="什么时候停止泵？", answer="密封泄漏时停止泵。")
    return app, sid, parent, records


def _button(ui, label):
    return next(item for item in ui.button if item.label == label)


def _text_area(ui, label):
    return next(item for item in ui.text_area if item.label == label)


@pytest.mark.parametrize("mode", ["auto", "human"])
def test_save_an_edited_result_into_issues_without_begin_then_reopen_a_fresh_window(tmp_path, mode):
    app, sid, parent, records = _result(tmp_path, mode=mode)
    ui = studio(tmp_path, sid)
    assert ui.session_state["fixture-begin-calls"] == []
    if mode == "auto":
        _text_area(ui, "修正意见").set_value("补充发现泄漏后该做什么。")
        _text_area(ui, "修订参考答案").set_value("立即停止泵，检查密封并记录泄漏。")
    else:
        _text_area(ui, "评分与修正说明").set_value("补充发现泄漏后该做什么。")
        _text_area(ui, "修订答案").set_value("立即停止泵，检查密封并记录泄漏。")
    _button(ui, "存入返工集合").click().run()
    assert not ui.exception and not ui.error, [item.value for item in ui.error]
    assert ui.session_state["fixture-begin-calls"] == []
    assert len(app.session(sid)["rounds"]) == 1
    result = app.issues(sid, status="pending")
    assert result["total"] == 1
    assert result["rows"][0]["candidate_id"] == records[0]["id"]
    assert "补充发现泄漏" in result["rows"][0]["instruction"]
    assert ui.segmented_control(key=f"human-result-view:{context(sid)}").value == "issues"

    fresh = studio(tmp_path, sid)
    fresh.segmented_control(key=f"human-result-view:{context(sid)}").set_value("issues").run()
    assert not fresh.exception and not fresh.error
    assert fresh.session_state["fixture-begin-calls"] == []
    assert any("什么时候停止泵" in str(item.value) for item in fresh.dataframe)
    assert app.issues(sid, status="pending")["rows"][0]["issue_id"] == result["rows"][0]["issue_id"]


def collection_screen():
    import streamlit as st
    from lib.presentation.streamlit.human_workspace_page import _render_rework_collection
    from lib.presentation.streamlit.i18n import install_streamlit_localization

    install_streamlit_localization()
    st.session_state.setdefault("fixture-revise-calls", [])
    st.session_state.setdefault("fixture-begin-calls", [])

    class Application:
        def issues(self, session_id, **kwargs):
            rows = [{"issue_id": f"{i:032x}", "round_id": "2" * 32, "run_id": "3" * 32,
                     "target": "sft", "candidate_id": f"真实候选-{i}", "status": "pending",
                     "question_preview": f"客户问题 {i}：什么时候停止泵？",
                     "answer_preview": "密封泄漏时停止泵。", "instruction": "保留安全前提。"}
                    for i in range(50)]
            return {"rows": rows, "total": 301, "offset": 0, "limit": 50, "has_more": True,
                    "version": 1, "issue_counts": {"pending": 301, "running": 0, "completed": 0, "needs_revision": 0}}

        def revise_issues(self, session_id, **kwargs):
            st.session_state["fixture-revise-calls"].append(kwargs)
            return {"id": "4" * 32, "run_id": "5" * 32}

        def session(self, session_id):
            return {"version": 2}

    def begin(command):
        st.session_state["fixture-begin-calls"].append(command)

    _render_rework_collection(Application(), {"id": "1" * 32, "version": 1},
                              "human:fixture:" + "1" * 32, begin, active=False)


@pytest.mark.parametrize("next_batch", [True, False])
def test_batch_action_uses_backend_next_batch_or_the_bounded_explicit_selection(next_batch):
    ui = AppTest.from_function(collection_screen, default_timeout=15).run()
    assert not ui.exception
    if next_batch:
        action = _button(ui, "AI 返工下一批")
        assert not action.disabled
    else:
        # Select raw IDs instead of the localized user-visible labels.
        ui.multiselect[0].set_value([f"{i:032x}" for i in range(3)])
        action = _button(ui, "AI 返工选中结果")
    action.click().run()
    assert not ui.exception and not ui.error
    calls = ui.session_state["fixture-revise-calls"]
    assert len(calls) == 1 and len(ui.session_state["fixture-begin-calls"]) == 1
    if next_batch:
        assert calls[0]["selected_issue_ids"] is None
    else:
        assert len(calls[0]["selected_issue_ids"]) == 3


def review_controls_screen():
    import streamlit as st
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    from lib.presentation.streamlit.review_rework_controls import render_review_rework_controls

    install_streamlit_localization()
    st.session_state["ws"] = "fixture"
    st.session_state.setdefault("fixture-save-calls", [])

    class Application:
        def save_rejected(self, run_id, target, **kwargs):
            st.session_state["fixture-save-calls"].append({"run_id": run_id, "target": target, **kwargs})
            if st.session_state.get("fixture-save-error"):
                raise ValueError("incomplete_save")
            return {"saved_count": 125, "session_id": "1" * 32,
                    "issue_counts": st.session_state.get("fixture-counts", {"pending": 125})}

        def existing_collection(self, run_id, target):
            if st.session_state["fixture-save-calls"] or st.session_state.get("fixture-existing"):
                return {"session_id": "1" * 32, "saved_count": 125}
            return None

    render_review_rework_controls(Application(), "3" * 32, "sft",
                                  st.session_state.get("fixture-rejected", 125))


def test_formal_review_save_stays_in_review_and_open_targets_the_issue_collection():
    ui = AppTest.from_function(review_controls_screen, default_timeout=15).run()
    assert not ui.exception
    ui.text_input[0].set_value("保留安全前提。")
    assert not any(item.label == "打开已存返工" for item in ui.button)
    _button(ui, "仅保存退回结果").click().run()
    assert not ui.exception and not ui.error
    assert len(ui.session_state["fixture-save-calls"]) == 1
    assert "nav" not in ui.session_state.filtered_state
    _button(ui, "打开已存返工").click().run()
    assert not ui.exception and not ui.error
    assert len(ui.session_state["fixture-save-calls"]) == 1
    assert ui.session_state["nav"] == "人工问答增强"
    assert ui.session_state["human-result-view-pending:human:fixture:" + "1" * 32] == "issues"


def test_save_and_open_is_one_action_and_does_not_open_after_a_partial_failure():
    ui = AppTest.from_function(review_controls_screen, default_timeout=15).run()
    ui.text_input[0].set_value("保留每一轮的推理。")
    _button(ui, "保存并打开返工").click().run()
    assert not ui.exception and not ui.error
    assert len(ui.session_state["fixture-save-calls"]) == 1
    assert ui.session_state["nav"] == "人工问答增强"
    assert ui.session_state["fixture-save-calls"][0]["instruction"] == "保留每一轮的推理。"

    failed = AppTest.from_function(review_controls_screen, default_timeout=15)
    failed.session_state["fixture-save-error"] = True
    failed.run()
    _button(failed, "保存并打开返工").click().run()
    assert not failed.exception and failed.error
    assert "nav" not in failed.session_state.filtered_state
    assert "human" not in failed.query_params


@pytest.mark.parametrize("counts,expected", [({"pending": 3}, "pending"),
    ({"needs_revision": 1}, "needs_revision"), ({"completed": 125}, "all")])
def test_open_rework_resets_stale_filter_and_page_to_show_saved_records(counts, expected):
    ui = AppTest.from_function(review_controls_screen, default_timeout=15)
    context = "human:fixture:" + "1" * 32
    ui.session_state["fixture-counts"] = counts
    ui.session_state[f"rework-status:{context}"] = "completed"
    ui.session_state[f"rework-offset:{context}:{expected}"] = 100
    ui.run()
    _button(ui, "保存并打开返工").click().run()
    assert not ui.exception and not ui.error
    assert ui.session_state[f"rework-status:{context}"] == expected
    assert ui.session_state[f"rework-offset:{context}:{expected}"] == 0


def test_existing_rework_can_reopen_in_a_fresh_session_with_no_current_rejections():
    ui = AppTest.from_function(review_controls_screen, default_timeout=15)
    ui.session_state["fixture-existing"] = True
    ui.session_state["fixture-rejected"] = 0
    ui.run()
    assert not ui.exception and not ui.error
    assert _button(ui, "保存并打开返工").disabled
    _button(ui, "打开已存返工").click().run()
    assert not ui.session_state["fixture-save-calls"]
    assert ui.session_state["nav"] == "人工问答增强"


@pytest.mark.parametrize("screen", [collection_screen, review_controls_screen])
def test_english_collection_controls_translate_ui_and_preserve_chinese_user_content(screen):
    ui = AppTest.from_function(screen, default_timeout=15)
    ui.session_state["ui_language"] = "en"
    ui.run()
    assert not ui.exception
    values = [item.label for group in (ui.button, ui.text_input, ui.text_area, ui.multiselect, ui.segmented_control)
              for item in group]
    values.extend(item.value for group in (ui.caption, ui.info, ui.warning) for item in group)
    assert not any(re.search(r"[\u4e00-\u9fff]", value) for value in values), values
    if screen == collection_screen:
        assert any("什么时候停止泵" in str(item.value) for item in ui.dataframe)
    else:
        ui.text_input[0].set_value("保留安全前提。")
        ui.button[0].click().run()
        assert not ui.exception and not ui.error
        assert ui.session_state["fixture-save-calls"][0]["instruction"] == "保留安全前提。"
        assert not any(re.search(r"[\u4e00-\u9fff]", item.value) for item in ui.caption)
