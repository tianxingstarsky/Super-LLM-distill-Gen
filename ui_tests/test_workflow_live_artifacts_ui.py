"""Saved candidates are visible before packaging and update without new jobs."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def live_results_screen():
    import streamlit as st
    from copy import deepcopy
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    from lib.presentation.streamlit.workflow_live_artifacts_view import render_live_artifacts

    install_streamlit_localization()
    st.session_state.setdefault("fixture-result", {
        "stages": [{"stage": "cpt", "written": 1, "eligible": 1, "ready": 0,
                    "review_accepted": 0, "quarantined": 0, "indexing": False},
                   {"stage": "sft", "written": 0, "eligible": 0},
                   {"stage": "agent", "written": 0, "eligible": 0}],
        "selected_stage": "cpt", "rows": [{"id": "sample-1", "text": "已清洗的真实资料内容",
                                               "status": "eligible"}], "finalized": False,
    })
    st.session_state.setdefault("fixture-reads", [])

    class Application:
        def live_artifacts(self, run_id, *, stage=None, limit=3):
            st.session_state["fixture-reads"].append((stage, limit))
            value = deepcopy(st.session_state["fixture-result"])
            if stage is not None:
                value["selected_stage"] = stage
            return value

    render_live_artifacts(Application(), "fixture", state={"status": "running"},
        recipe={"version": 19, "targets": ["sft"], "source_cleaning": True,
                "review_repair": {"mode": "auto"}})


def markup(ui):
    return "\n".join(item.proto.body for item in ui.get("html"))


def test_stage_rows_appear_before_quality_report_and_update_when_new_samples_are_saved():
    ui = AppTest.from_function(live_results_screen, default_timeout=15).run()
    assert not ui.exception
    assert "实时阶段产物" in markup(ui)
    assert "已清洗的真实资料内容" in markup(ui)
    assert "节点检查通过" in markup(ui)
    assert "不等于最终交付通过" in ui.caption[-1].value
    frame = ui.dataframe[0].value
    assert "资料清洗" in frame["节点"].values and "Agent 轨迹" not in frame["节点"].values
    assert frame.iloc[0]["已落盘"] == "1" and frame.iloc[0]["评分通过"] == "0"
    updated = deepcopy(ui.session_state["fixture-result"])
    updated["stages"][0].update(written=2, eligible=1, quarantined=1)
    updated["rows"].append({"id": "sample-2", "text": "刚刚落盘的第二条结果", "status": "quarantined",
                            "reason": "对照原文核对未通过"})
    ui.session_state["fixture-result"] = updated
    ui.run()
    assert not ui.exception
    assert ui.dataframe[0].value.iloc[0]["已落盘"] == "2"
    assert "刚刚落盘的第二条结果" in markup(ui) and "对照原文核对未通过" in markup(ui)
    assert ui.session_state["fixture-reads"] == [(None, 3), (None, 3)]


def test_large_history_counts_show_indexed_lower_bound_without_final_approval_claim():
    ui = AppTest.from_function(live_results_screen, default_timeout=15).run()
    updated = deepcopy(ui.session_state["fixture-result"])
    updated["stages"][0].update(indexing=True, written=200, eligible=180, quarantined=20)
    ui.session_state["fixture-result"] = updated
    ui.run()
    assert not ui.exception
    assert ui.dataframe[0].value.iloc[0]["已落盘"] == "≥ 200"
    assert any("索引" in item.value for item in ui.caption)
    assert "最终交付" not in markup(ui)


def test_english_live_view_translates_controls_and_keeps_source_text():
    ui = AppTest.from_function(live_results_screen, default_timeout=15)
    ui.session_state["ui_language"] = "en"
    ui.run()
    assert not ui.exception
    assert "Live stage results" in markup(ui) and "Stage checks passed" in markup(ui)
    assert "已清洗的真实资料内容" in markup(ui)
    assert ui.selectbox[0].label == "Preview stage"
    assert "Source cleaning" in ui.dataframe[0].value.iloc[0]["Stage"]
