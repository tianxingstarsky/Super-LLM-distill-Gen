"""Workflow shortcuts keep their run and native target across page handoffs."""
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from lib.presentation.streamlit import review_navigation as navigation


def test_fragment_navigation_is_consumed_at_run_entry_and_requests_full_app(monkeypatch):
    from lib.presentation.streamlit.workflow_page import render_run

    values = {"ws": "fixture"}
    calls = []

    class AppRerunRequested(Exception):
        pass

    def rerun(**options):
        assert "workflow-app-navigation-pending" not in values
        calls.append(options)
        raise AppRerunRequested

    monkeypatch.setattr(navigation, "st", SimpleNamespace(session_state=values, rerun=rerun))
    navigation.open_package("chosen-run", from_fragment=True)
    assert values["workflow-app-navigation-pending"] == {"workspace": "fixture", "run_id": "chosen-run"}
    assert calls == []  # The callback must never call st.rerun itself.
    with pytest.raises(AppRerunRequested):
        render_run.__wrapped__(None, "chosen-run", lambda args: None)
    assert calls == [{"scope": "app"}]
    navigation.consume_app_navigation("chosen-run")
    assert calls == [{"scope": "app"}]  # A full rerun cannot repeat the handoff.


def test_unrelated_pending_navigation_cannot_rerun_another_workflow(monkeypatch):
    values = {"ws": "fixture", "workflow-app-navigation-pending": {
        "workspace": "other", "run_id": "chosen-run"}}
    monkeypatch.setattr(navigation, "st", SimpleNamespace(session_state=values))
    navigation.consume_app_navigation("chosen-run")
    assert "workflow-app-navigation-pending" not in values


def test_sample_preview_callback_defers_navigation_without_callback_rerun(monkeypatch):
    from lib.presentation.streamlit import workflow_page

    values = {"ws": "fixture"}
    widgets = SimpleNamespace(session_state=values)
    monkeypatch.setattr(navigation, "st", widgets)
    monkeypatch.setattr(workflow_page, "st", widgets)
    workflow_page._open_sample_browser("chosen-run", "multiturn")
    assert values["workflow-open-preview"] == {
        "workspace": "fixture", "run_id": "chosen-run", "target": "multiturn"}
    assert values["workflow-app-navigation-pending"] == {"workspace": "fixture", "run_id": "chosen-run"}


def test_verified_review_callback_marks_full_rerun_only_after_success(monkeypatch):
    values = {"ws": "fixture"}
    monkeypatch.setattr(navigation, "st", SimpleNamespace(session_state=values))
    app = SimpleNamespace(
        state=lambda run_id: {"status": "completed", "targets": ["sft"]},
        package_inventory=lambda run_id: {
            "manifest": {"counts": {"sft": 1}}, "files": [{"name": "sft.jsonl"}]},
    )
    navigation.open_verified_review(app, "chosen-run", "sft", from_fragment=True)
    assert values["sft-review-run"] == "chosen-run"
    assert values["workflow-app-navigation-pending"] == {"workspace": "fixture", "run_id": "chosen-run"}


@pytest.mark.parametrize("target,mode,key", [
    ("cpt", "CPT 语料审核", "corpus-review-run"),
    ("sft", "SFT 数据调整", "sft-review-run"),
    ("dpo", "DPO 偏好优化", "preference-review-run"),
    ("orpo", "ORPO 偏好优化", "preference-review-run:orpo"),
    ("rlaif", "RLAIF 反馈审核", "preference-review-run:rlaif"),
])
def test_review_handoff_selects_exact_run_and_preserves_other_queues(monkeypatch, target, mode, key):
    values = {"ws": "fixture", "sft-review-run": "previous", "review-decisions": {"one": "pending"}}
    monkeypatch.setattr(navigation, "st", SimpleNamespace(session_state=values))
    navigation.open_review("chosen-run", target)
    assert values["nav"] == "人工审核"
    assert values["review-mode:fixture"] == mode
    assert values[key] == "chosen-run"
    assert values["review-decisions"] == {"one": "pending"}
    if target != "sft":
        assert values["sft-review-run"] == "previous"


@pytest.mark.parametrize("target,kind", [("agent", "训练候选"), ("agent_negative", "失败轨迹")])
def test_agent_handoff_locates_embedded_queue_despite_task_filters(monkeypatch, target, kind):
    values = {"ws": "fixture", "task-center-search:fixture": "unrelated",
              "task-center-filter:fixture": "已完成", "task-center-page:fixture": 8}
    monkeypatch.setattr(navigation, "st", SimpleNamespace(session_state=values))
    navigation.open_review("chosen-run", target)
    assert values["workflow-open-run"] == {"workspace": "fixture", "run_id": "chosen-run"}
    assert values["task-center-run:fixture"] == values["task-center-locate:fixture"] == "chosen-run"
    assert values["task-center-search:fixture"] == ""
    assert values["task-center-filter:fixture"] == "全部"
    assert values["task-center-agent-review:fixture:chosen-run"] is True
    assert values["agent-review:fixture:chosen-run:kind"] == kind
    assert values["nav"] == "任务管理"


def test_choices_keep_unsupported_targets_out_of_unrelated_review_stores():
    targets = ["cpt", "sft", "dpo", "orpo", "rlaif", "agent", "multiturn", "cot", "gsm8k"]
    choices = navigation.review_choices({"targets": targets}, {
        "counts": {target: (0 if target == "dpo" else 2) for target in targets},
        "negative_counts": {"agent": 3},
    })
    assert [target for target, _ in choices] == ["sft", "orpo", "rlaif", "cpt", "agent", "agent_negative"]


@pytest.mark.parametrize("target", ["multiturn", "cot", "gsm8k", "unknown"])
def test_unsupported_review_handoff_cannot_change_navigation(monkeypatch, target):
    values = {"ws": "fixture", "nav": "数据管理"}
    monkeypatch.setattr(navigation, "st", SimpleNamespace(session_state=values))
    with pytest.raises(ValueError, match="unsupported_review_target"):
        navigation.open_review("run", target)
    assert values == {"ws": "fixture", "nav": "数据管理"}


@pytest.mark.parametrize("problem", ["running", "tampered", "missing", "empty"])
def test_review_click_rechecks_readiness_and_native_artifact(monkeypatch, problem):
    values = {"ws": "fixture", "nav": "任务管理"}
    monkeypatch.setattr(navigation, "st", SimpleNamespace(session_state=values))

    class Application:
        def state(self, run_id):
            assert run_id == "chosen-run"
            return {"status": "running" if problem == "running" else "completed", "targets": ["sft"]}

        def package_inventory(self, run_id):
            assert run_id == "chosen-run"
            if problem == "tampered":
                raise ValueError("artifact_integrity_error")
            return {"manifest": {"counts": {"sft": 0 if problem == "empty" else 1}},
                    "files": [] if problem == "missing" else [{"name": "sft.jsonl"}]}

    navigation.open_verified_review(Application(), "chosen-run", "sft", from_fragment=True)
    assert values["nav"] == "任务管理"
    assert values["workflow-review-error:chosen-run"] is True
    assert "sft-review-run" not in values
    assert "workflow-app-navigation-pending" not in values


PREVIEW_SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.dataset_preview_page import render_workflow_samples
st.session_state.setdefault('ws', 'fixture')
st.session_state.setdefault('target', 'orpo')
st.session_state.setdefault('available', True)
class Application:
    def task_runs(self):
        return [dict(id=key, name=key, status='completed', targets=[st.session_state['target']])
                for key in ['first-run', 'chosen-run']]
    def state(self, run_id):
        return dict(id=run_id, status='completed', targets=[st.session_state['target']])
    def package_inventory(self, run_id):
        target = st.session_state['target']
        return dict(manifest=dict(counts={target: 2 if st.session_state['available'] else 0}),
                    files=[dict(name=target+'.jsonl')])
    def artifact_preview(self, run_id, target, **options):
        return [dict(text='Corpus', messages=[dict(role='user', content='Question'),
                    dict(role='assistant', content='Answer')], prompt='Question',
                    chosen='Good answer', rejected='Bad answer', question='2 + 2?',
                    reasoning=['Add two and two'], answer='4', responses=[])]
render_workflow_samples(Application(), 'fixture')
'''


def _app(script):
    return AppTest.from_string(script.encode("ascii", "backslashreplace").decode("ascii")).run()


def test_sample_review_button_keeps_selected_run_and_native_preference_target():
    ui = _app(PREVIEW_SCRIPT)
    assert not ui.exception
    ui.selectbox(key="data-preview-run:fixture").set_value("chosen-run").run()
    ui.button(key="data-preview-review:fixture:chosen-run").click().run()
    assert not ui.exception
    assert ui.session_state["preference-review-run:orpo"] == "chosen-run"
    assert ui.session_state["review-mode:fixture"] == "ORPO 偏好优化"


@pytest.mark.parametrize("target", ["multiturn", "cot", "gsm8k"])
def test_sample_package_keeps_separate_targets_without_fake_sft_review(target):
    ui = _app(PREVIEW_SCRIPT)
    ui.session_state["target"] = target
    ui.run()
    ui.selectbox(key="data-preview-run:fixture").set_value("chosen-run").run()
    assert not ui.exception
    assert not any(button.key.startswith("data-preview-review:") for button in ui.button)
    ui.button(key="data-preview-package:fixture:chosen-run").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "输出打包"
    assert ui.session_state["package-run:fixture"] == "chosen-run"
    assert ui.session_state["package-target:fixture:chosen-run"] == target


def test_empty_verified_preview_has_no_review_or_package_shortcut():
    ui = _app(PREVIEW_SCRIPT)
    ui.session_state["available"] = False
    ui.run()
    assert not ui.exception
    assert not any(button.key.startswith(("data-preview-review:", "data-preview-package:"))
                   for button in ui.button)


RUN_SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.workflow_page import render_run, GRAPH_LABELS
st.session_state.setdefault('ws', 'fixture')
st.session_state.setdefault('nav', '任务管理')
st.session_state.setdefault('phase', 'completed')
st.session_state.setdefault('damaged', False)
class Application:
    def state(self, run_id):
        return dict(name='Chosen workflow', status=st.session_state['phase'],
                    targets=['sft', 'orpo'], events=[],
                    stages={key:dict(label=label, status='completed',done=1,total=1)
                            for key,label in GRAPH_LABELS.items()},
                    quality=dict(targets={key:dict(eligible=1,total=1,reasons={})
                                          for key in ['sft', 'orpo']}))
    def recipe(self, run_id): return dict(targets=['sft', 'orpo'], sources=[])
    def is_active(self, run_id): return st.session_state['phase']=='running'
    def artifact_location(self, run_id): return '/local/artifacts'
    def package_inventory(self, run_id):
        if st.session_state['damaged']: raise ValueError('artifact_integrity_error')
        return dict(manifest=dict(counts=dict(sft=1,orpo=1)),
                    files=[dict(name=key+'.jsonl') for key in ['sft','orpo']])
render_run(Application(),'chosen-run',lambda args:None)
'''


def test_task_shortcut_routes_to_current_target_after_validating_artifacts():
    ui = _app(RUN_SCRIPT)
    assert not ui.exception
    ui.selectbox(key="workflow-review-target:chosen-run").set_value("orpo").run()
    ui.button(key="workflow-review:chosen-run").click().run()
    assert not ui.exception
    assert ui.session_state["preference-review-run:orpo"] == "chosen-run"
    assert ui.session_state["review-mode:fixture"] == "ORPO 偏好优化"


def test_task_shortcut_reports_changed_files_without_switching_to_wrong_queue():
    ui = _app(RUN_SCRIPT)
    ui.session_state["damaged"] = True
    ui.button(key="workflow-review:chosen-run").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "任务管理"
    assert any("无法读取或校验任务产物" in error.value for error in ui.error)


def test_running_task_has_no_unavailable_review_or_package_actions():
    ui = _app(RUN_SCRIPT)
    ui.session_state["phase"] = "running"
    ui.run()
    assert not ui.exception
    assert not any(button.key in {"workflow-review:chosen-run", "zip:chosen-run"} for button in ui.button)


PACKAGE_SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.package_page import render_package_page
st.session_state.setdefault('ws', 'fixture')
st.session_state.setdefault('package-run:fixture','chosen-run')
st.session_state.setdefault('package-target:fixture:chosen-run','orpo')
st.session_state.setdefault('package-preview:chosen-run',('cpt','cpt.jsonl'))
st.session_state.setdefault('package-review-target:chosen-run','cpt')
class Application:
    def task_runs(self):
        return [dict(id='chosen-run',name='Chosen workflow',status='completed',targets=['cpt','orpo'])]
    def list_releases(self): return []
    def bundle_job(self, run_id): return None
    def state(self, run_id): return dict(targets=['cpt','orpo'],stages={})
    def package_contents(self, run_id):
        return dict(manifest=dict(counts=dict(cpt=1,orpo=1),sha256={},sources=[]),
                    files=[dict(name=key+'.jsonl',bytes=20,sha256='a'*64) for key in ['cpt','orpo']],
                    quality=dict(targets={key:dict(total=1,eligible=1) for key in ['cpt','orpo']}),
                    bundle=None)
    def artifact_preview(self,*args,**kwargs): return []
render_package_page(Application())
'''


def test_package_consumes_target_handoff_after_verification_and_aligns_review():
    ui = _app(PACKAGE_SCRIPT)
    assert not ui.exception
    assert ui.selectbox(key="package-preview:chosen-run").value == ("orpo", "orpo.jsonl")
    assert ui.selectbox(key="package-review-target:chosen-run").value == "orpo"
    assert "package-target:fixture:chosen-run" not in ui.session_state
    ui.button(key="package-review:chosen-run").click().run()
    assert not ui.exception
    assert ui.session_state["preference-review-run:orpo"] == "chosen-run"
