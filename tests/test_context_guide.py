"""Page help stays with the task and uses the active route."""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.context_guide import render_context_guide
from lib.presentation.streamlit.i18n import install_streamlit_localization

st.session_state['ui_language'] = 'en'
install_streamlit_localization()

def navigate(page):
    st.session_state['nav'] = page

render_context_guide(st.session_state.get('current_page', '总览'), navigate)
'''


def test_overview_help_opens_workflow_without_a_separate_guide_route():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert "Guide" not in [button.label for button in ui.button]
    ui.button(key="context-guide-start:总览").click().run()
    assert ui.session_state["context-guide-step:总览"] == 0
    assert "home-source-panel" in " ".join(item.proto.body for item in ui.get("html"))
    ui.button(key="context-guide-next:总览").click().run()
    assert ui.session_state["context-guide-step:总览"] == 1
    ui.button(key="context-guide-action:总览").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "自动工作流"


def test_workflow_help_describes_node_models_in_place():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["current_page"] = "自动工作流"
    ui.run()
    assert not ui.exception
    content = " ".join(item.value for item in [*ui.markdown, *ui.caption])
    assert "Set models on nodes" in content
    assert "Select a model node" in content
    assert all(button.key != "context-guide-action:自动工作流" for button in ui.button)


def _active_highlight(ui):
    matches = [item.proto for item in ui.get("html")
               if "const selectors =" in item.proto.body]
    assert len(matches) == 1
    assert matches[0].unsafe_allow_javascript
    return matches[0].body


def test_page_walkthroughs_target_real_key_prefixes_and_can_be_exited():
    cases = (
        ("数据管理", "st-key-data-view-", "st-key-asset-category-"),
        ("任务管理", "st-key-task-view-", "st-key-task-center-filter-"),
        ("人工审核", "st-key-review-mode-", "st-key-review-overview-open-sft"),
        ("系统设置", "st-key-ui-language-choice", "st-key-preference-area"),
    )
    for page, first, second in cases:
        ui = AppTest.from_string(SCRIPT)
        ui.session_state["current_page"] = page
        ui.run()
        assert not ui.exception
        ui.button(key=f"context-guide-start:{page}").click().run()
        assert first in _active_highlight(ui)
        ui.button(key=f"context-guide-next:{page}").click().run()
        assert second in _active_highlight(ui)
        exit_key = (f"context-guide-exit:{page}" if any(
            button.key == f"context-guide-exit:{page}" for button in ui.button)
            else f"context-guide-finish:{page}")
        ui.button(key=exit_key).click().run()
        assert not ui.exception
        assert f"context-guide-step:{page}" not in ui.session_state
        assert not any("const selectors =" in item.proto.body for item in ui.get("html"))


def test_review_walkthrough_follows_selected_queue_without_embedding_workspace_data():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["current_page"] = "人工审核"
    ui.session_state["ws"] = "private-workspace-marker"
    ui.session_state["review-mode:private-workspace-marker"] = "DPO 偏好优化"
    ui.run()
    ui.button(key="context-guide-start:人工审核").click().run()
    ui.button(key="context-guide-next:人工审核").click().run()
    body = _active_highlight(ui)
    assert "st-key-review-overview-open-dpo" in body
    assert "private-workspace-marker" not in body
    ui.button(key="context-guide-next:人工审核").click().run()
    body = _active_highlight(ui)
    assert "st-key-df-review-actions-dpo" in body
    assert "st-key-review-overview-open-dpo" in body


def test_legacy_direct_subpages_do_not_inherit_nonexistent_composite_controls():
    for page in ("资产管理", "数据预览", "质量报告", "管线运行", "运行监控", "偏好设置"):
        ui = AppTest.from_string(SCRIPT)
        ui.session_state["current_page"] = page
        ui.run()
        assert not ui.exception
        assert not any(button.key and button.key.startswith("context-guide-start:")
                       for button in ui.button)


PACKAGE_SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.context_guide import render_context_guide
from lib.presentation.streamlit.package_page import render_package_page
st.session_state.setdefault('ws', 'fixture')

def navigate(page):
    st.session_state['nav'] = page

class Application:
    def task_runs(self):
        if st.session_state.get('package_state') == 'empty':
            return []
        return [dict(id='run-1', name='Real run', status='completed',
                     targets=['sft'], created_at='2026-10-03T00:00:00Z')]
    def list_releases(self): return []
    def bundle_job(self, run_id):
        if st.session_state.get('package_state') == 'busy':
            return dict(status='running', phase='archive', done=1, total=2,
                        cancel_requested=False)
        return None
    def state(self, run_id): return dict(targets=['sft'], stages={})
    def package_contents(self, run_id):
        return dict(manifest=dict(counts={'sft': 1}, sha256={}, sources=[]),
                    files=[], quality=dict(targets={'sft': dict(total=1, eligible=1)}),
                    bundle=None)
    def artifact_preview(self, *args, **kwargs): return []

render_context_guide('输出打包', navigate)
render_package_page(Application())
'''


def test_package_walkthrough_targets_controls_present_in_each_real_page_state():
    cases = (
        ("empty", "package-empty-workflow", "st-key-package-empty-workflow"),
        ("ready", "package-run:fixture", "st-key-prepare-package-"),
        ("busy", "package-run:fixture", "st-key-stop-package-"),
    )
    for state, first_widget, final_target in cases:
        ui = AppTest.from_string(PACKAGE_SCRIPT)
        ui.session_state["package_state"] = state
        ui.run()
        assert not ui.exception
        assert any(item.key == first_widget for item in [*ui.button, *ui.selectbox])
        ui.button(key="context-guide-start:输出打包").click().run()
        first_html = _active_highlight(ui)
        assert ("st-key-package-run-" if state != "empty"
                else "st-key-package-empty-workflow") in first_html
        ui.button(key="context-guide-next:输出打包").click().run()
        assert not ui.exception
        assert any(item.key == ("stop-package:run-1" if state == "busy"
                                    else "prepare-package:run-1" if state == "ready"
                                    else "package-empty-workflow")
                   for item in ui.button)
        assert final_target in _active_highlight(ui)
