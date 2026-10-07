"""Page help stays with the task and uses the active route."""
from __future__ import annotations

import json
import re

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
    assert all(button.key != "context-guide-action:总览" for button in ui.button)
    ui.button(key="context-guide-start:总览").click().run()
    assert ui.session_state["context-guide-step:总览"] == 0
    assert "home-source-panel" in " ".join(item.proto.body for item in ui.get("html"))
    ui.button(key="context-guide-jump:总览:1").click().run()
    assert "st-key-home-strategy-options" in _active_highlight(ui)
    # The last control is reachable directly from the in-page banner.
    ui.button(key="context-guide-jump:总览:2").click().run()
    assert ui.session_state["context-guide-step:总览"] == 2
    assert "st-key-home-recent-panel" in _active_highlight(ui)
    ui.button(key="context-guide-action:总览").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "自动工作流"
    assert "context-guide-step:总览" not in ui.session_state
    assert not any("const selectors =" in item.proto.body for item in ui.get("html"))


def test_workflow_help_describes_node_models_in_place():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["current_page"] = "自动工作流"
    ui.run()
    assert not ui.exception
    ui.button(key="context-guide-start:自动工作流").click().run()
    assert "Set models on nodes" in ui.button(key="context-guide-jump:自动工作流:1").label
    ui.button(key="context-guide-jump:自动工作流:1").click().run()
    content = " ".join(item.value for item in [*ui.markdown, *ui.caption])
    assert "Select a model node" in content
    assert "st-key-workbench-node-panel" in _active_highlight(ui)
    assert all(button.key != "context-guide-action:自动工作流" for button in ui.button)


def _active_highlight(ui):
    matches = [item.proto for item in ui.get("html")
               if "const selectors =" in item.proto.body]
    assert len(matches) == 1
    assert matches[0].unsafe_allow_javascript
    return matches[0].body


def _resolved_highlight_key(ui):
    """Resolve tour selectors against keys on the actual rendered page tree."""
    script = _active_highlight(ui)
    selectors = json.loads(re.search(r"const selectors = (\[.*?\]);", script).group(1))
    keys = [node.key for node in ui if getattr(node, "key", None)]
    for selector in selectors:
        fragment = selector.split('"')[1]
        for key in keys:
            if fragment in "st-key-" + re.sub(r"[^a-zA-Z0-9_-]", "-", key):
                return key
    raise AssertionError(f"Tour points to no rendered control: {selectors}")


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


def test_task_walkthrough_uses_the_run_graph_control_and_respects_the_active_view():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["current_page"] = "任务管理"
    ui.session_state["ws"] = "fixture"
    ui.run()
    ui.button(key="context-guide-start:任务管理").click().run()
    ui.button(key="context-guide-jump:任务管理:2").click().run()
    body = _active_highlight(ui)
    assert "st-key-workflow-follow-" in body
    assert body.index("st-key-workflow-follow-") < body.index("st-key-task-center-focus-")
    assert "fixture" not in body

    ui.session_state["task-view:fixture"] = "命令管线"
    ui.run()
    body = _active_highlight(ui)
    assert "st-key-task-view-" in body
    assert "st-key-workflow-follow-" not in body


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
        if st.session_state.get('package_state') in ('empty', 'release', 'large-release', 'unverified-release'):
            return []
        return [dict(id='run-1', name='Real run', status='completed',
                     targets=['sft'], created_at='2026-10-03T00:00:00Z')]
    def list_releases(self):
        state = st.session_state.get('package_state')
        if state not in ('release', 'large-release', 'unverified-release'):
            return []
        size = 51 * 1024 * 1024 if state == 'large-release' else 80
        return [dict(id='release-1', name='Saved review', kind='review', target='sft',
                     path='/fixture/release', created_at='2026-10-03T00:00:00Z',
                     verified=state != 'unverified-release', error='file_hash_mismatch',
                     files=[dict(name='data.jsonl', path='/fixture/release/data.jsonl',
                                 bytes=size, sha256='a' * 64)])]
    def release_file(self, *args): return b'{}'
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


def test_package_walkthrough_handles_saved_releases_without_pointing_to_create():
    for state, final_prefix in (
        ("release", "package-release-download-"),
        ("large-release", "package-release-file-"),
        ("unverified-release", "package-releases-panel"),
    ):
        ui = AppTest.from_string(PACKAGE_SCRIPT)
        ui.session_state["package_state"] = state
        ui.run()
        assert not ui.exception
        assert ui.selectbox(key="package-release:fixture").value == "release-1"
        ui.button(key="context-guide-start:输出打包").click().run()
        ui.button(key="context-guide-next:输出打包").click().run()
        assert not ui.exception
        found = re.sub(r"[^a-zA-Z0-9_-]", "-", _resolved_highlight_key(ui))
        assert found.startswith(final_prefix)
        assert "package-empty-workflow" not in found


HOME_SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.context_guide import render_context_guide
from lib.presentation.streamlit.home_page import render_overview
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state['ui_language'] = 'en'
install_streamlit_localization()
def navigate(page): st.session_state['nav'] = page
class Application:
    def task_runs(self):
        return [] if st.session_state.get('empty') else [
            dict(id='run-1', name='Saved task', status='completed', targets=['sft'])]
    def list_releases(self): return []
render_context_guide('总览', navigate)
render_overview(Application(), 'fixture', [], 0, 0, '/fixture/input', '/fixture/out',
                navigate=navigate, job_status=lambda: None)
'''


def test_every_home_tour_step_resolves_after_layout_changes_in_empty_and_populated_states():
    for empty in (True, False):
        ui = AppTest.from_string(HOME_SCRIPT)
        ui.session_state['empty'] = empty
        ui.run()
        ui.button(key='context-guide-start:总览').click().run()
        for index, target in enumerate(('home-source-panel', 'home-strategy-options', 'home-recent-panel')):
            if index:
                ui.button(key=f'context-guide-jump:总览:{index}').click().run()
            assert not ui.exception
            assert _resolved_highlight_key(ui) == target
        ui.button(key='context-guide-finish:总览').click().run()
        assert 'context-guide-step:总览' not in ui.session_state
        assert not any('const selectors =' in item.proto.body for item in ui.get('html'))


MANUAL_SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.context_guide import render_context_guide
from lib.presentation.streamlit.manual_dataset_page import render_manual_datasets
st.session_state.setdefault('ws', 'fixture')
def navigate(page): st.session_state['nav'] = page
class Application:
    def list_datasets(self):
        return [] if st.session_state.get('empty') else [
            {'id':'a'*32,'name':'Images','count':0,'updated_at':'2026-10-07'}]
page = st.session_state.get('current_page', '自动工作流')
render_context_guide(page, navigate)
manual = (page == '自动工作流' and st.session_state.get('workflow-creation-mode:fixture') == '人工制作图文'
          or page == '数据管理' and st.session_state.get('data-view:fixture') == '人工制作')
if manual:
    render_manual_datasets(Application(), 'fixture', show_title=False)
'''


def test_manual_mode_in_workflow_and_library_targets_actual_authoring_controls():
    for page, mode_key, value in (
        ('自动工作流', 'workflow-creation-mode:fixture', '人工制作图文'),
        ('数据管理', 'data-view:fixture', '人工制作'),
    ):
        for empty in (True, False):
            ui = AppTest.from_string(MANUAL_SCRIPT)
            ui.session_state['current_page'] = page
            ui.session_state[mode_key] = value
            ui.session_state['empty'] = empty
            ui.run()
            assert not ui.exception
            ui.button(key='context-guide-start:人工制作数据').click().run()
            for step, target in enumerate(('manual-datasets-picker',
                                            'manual-datasets-picker' if empty else 'manual-datasets-editor',
                                            'manual-datasets-picker' if empty else 'manual-datasets-saved')):
                if step:
                    ui.button(key=f'context-guide-jump:人工制作数据:{step}').click().run()
                assert not ui.exception
                assert _resolved_highlight_key(ui) == target
                body = _active_highlight(ui)
                assert 'st-key-workbench-node-panel' not in body
                assert 'st-key-asset-category-' not in body
                assert 'fixture' not in body
            assert not any(item.key == 'context-guide-action:人工制作数据' for item in ui.button)


def test_switching_manual_and_automatic_modes_clears_previous_tour_and_highlight():
    ui = AppTest.from_string(MANUAL_SCRIPT)
    ui.run()
    ui.button(key='context-guide-start:自动工作流').click().run()
    ui.button(key='context-guide-jump:自动工作流:1').click().run()
    assert 'st-key-workbench-node-panel' in _active_highlight(ui)
    ui.session_state['workflow-creation-mode:fixture'] = '人工制作图文'
    ui.run()
    assert not ui.exception
    assert 'context-guide-step:自动工作流' not in ui.session_state
    assert not any('const selectors =' in item.proto.body for item in ui.get('html'))
    ui.button(key='context-guide-start:人工制作数据').click().run()
    assert _resolved_highlight_key(ui) == 'manual-datasets-picker'
    ui.session_state['workflow-creation-mode:fixture'] = '自动生成'
    ui.run()
    assert not ui.exception
    assert 'context-guide-step:人工制作数据' not in ui.session_state
    assert not any('const selectors =' in item.proto.body for item in ui.get('html'))
    assert any(item.key == 'context-guide-start:自动工作流' for item in ui.button)


def test_switching_library_views_does_not_keep_manual_authoring_highlight():
    ui = AppTest.from_string(MANUAL_SCRIPT)
    ui.session_state['current_page'] = '数据管理'
    ui.session_state['data-view:fixture'] = '人工制作'
    ui.run()
    ui.button(key='context-guide-start:人工制作数据').click().run()
    ui.button(key='context-guide-jump:人工制作数据:2').click().run()
    assert _resolved_highlight_key(ui) == 'manual-datasets-saved'
    ui.session_state['data-view:fixture'] = '资产管理'
    ui.run()
    assert 'context-guide-step:人工制作数据' not in ui.session_state
    assert not any('const selectors =' in item.proto.body for item in ui.get('html'))
    ui.button(key='context-guide-start:数据管理').click().run()
    assert 'st-key-data-view-' in _active_highlight(ui)
