"""Human revision routes supplement the real DAG without running a model."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield None


def graph(*, language="en", target="sft", jev=False, live=True):
    from lib.domain.workflow_graph import execution_graph
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    targets = [target]
    review = {"enabled": True, "node": "jev"} if jev else None
    keys, _ = execution_graph(targets, package_review=review, recipe_version=17)
    labels = {key: key for key in keys}
    return canvas_spec(targets, {}, "ingest", labels, labels, language=language,
                       live=live, package_review=review, recipe_version=17)


def projection(phase="needs_revision", *, generation="sft", review="sft", actionable=True, scored=False,
               kind="revision", parent_run_ids=()):
    return {"phase": phase, "generation_node": generation, "review_node": review, "actionable": actionable,
            "kind": kind, "parent_run_ids": list(parent_run_ids),
            "stages": {"self_check": {"coupled_with_generation": True},
                       "scoring": {"enabled": scored, "status": "completed" if scored else "disabled"}}}


def test_overlay_keeps_the_real_dag_and_maps_feedback_to_real_generation():
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    original = graph(jev=True)
    unchanged = deepcopy(original)
    spec = extend_feedback_branch(original, projection(review="jev", scored=True))
    assert original == unchanged
    assert [node["id"] for node in spec["nodes"]] == [node["id"] for node in original["nodes"]]
    assert spec["edges"] == original["edges"]
    assert spec["control_nodes"][0]["select_node"] == "sft"
    assert spec["control_edges"][0]["from"] == "jev"
    assert next(edge for edge in spec["control_edges"] if edge["kind"] == "return")["to"] == "sft"
    assert all(node["y"] + 78 < spec["control_nodes"][0]["y"] for node in spec["nodes"])
    assert spec["height"] - spec["control_nodes"][0]["y"] >= 78


def test_disabled_and_unsampled_scoring_are_never_presented_as_passed_scoring():
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    spec = extend_feedback_branch(graph(), projection("approved"))
    assert spec["feedback_branch"]["review_node"] == "sft"
    assert spec["feedback_branch"]["review_label"] == "Quality check during generation"
    skipped = projection("approved", review="jev", scored=True)
    skipped["selected_result"] = {"optional_score_status": "not_selected"}
    spec = extend_feedback_branch(graph(jev=True), skipped)
    assert "not selected for scoring" in spec["control_edges"][2]["label"]


@pytest.mark.parametrize("phase,status", [("waiting", "pending"), ("editing", "needs_revision"),
    ("generating", "completed"), ("reviewing", "completed"), ("packaging", "completed"),
    ("approved", "completed"), ("needs_revision", "needs_revision"),
    ("revision_limit", "needs_revision"), ("interrupted", "needs_revision")])
def test_projection_states_have_a_visible_route_without_automatic_rebuild(phase, status):
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    spec = extend_feedback_branch(graph(), projection(phase))
    assert spec["control_nodes"][0]["status"] == status
    assert spec["feedback_branch"]["phase"] == phase
    assert all(edge["status"] == "pending" for edge in spec["control_edges"]) if phase == "waiting" else True
    if phase == "revision_limit":
        assert not spec["control_nodes"][0]["actionable"]


def test_unknown_node_projection_cannot_create_a_fake_engine_node():
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    spec = graph()
    assert extend_feedback_branch(spec, projection(generation="imaginary")) == spec
    assert extend_feedback_branch(spec, None) == spec


@pytest.mark.parametrize("kind", [None, "generation", "imported"])
@pytest.mark.parametrize("phase", ["generating", "reviewing", "packaging", "approved"])
def test_first_version_does_not_claim_a_human_revision_was_submitted(kind, phase):
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    original = graph(jev=True)
    original["nodes"][1]["status"] = "running"
    spec = extend_feedback_branch(original, projection(phase, review="jev", scored=True, kind=kind))
    correction = spec["control_nodes"][0]
    assert correction["status"] == "pending"
    assert not spec["feedback_branch"]["revision"]
    assert next(edge for edge in spec["control_edges"] if edge["kind"] == "return")["status"] == "pending"
    assert spec["nodes"][1]["status"] == "running", "the actual stage retains its real working state"
    if phase == "approved":
        assert correction["actionable"]
        assert "feedback is optional" in correction["subtitle"]
    else:
        assert not correction["actionable"]
        assert correction["subtitle"] == "Add feedback after results are available"


@pytest.mark.parametrize("kind,parents", [("revision", []), (None, ["actual-parent-run"])])
@pytest.mark.parametrize("phase,status", [("generating", "running"), ("reviewing", "completed"),
                                         ("packaging", "completed"), ("approved", "completed")])
def test_real_revision_activates_only_the_submitted_return_route(kind, parents, phase, status):
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    spec = extend_feedback_branch(graph(jev=True), projection(phase, review="jev", scored=True,
                                kind=kind, parent_run_ids=parents))
    assert spec["feedback_branch"]["revision"]
    assert spec["control_nodes"][0]["status"] == "completed"
    assert next(edge for edge in spec["control_edges"] if edge["kind"] == "return")["status"] == status


@pytest.mark.parametrize("phase,status", [("generating", "pending"), ("reviewing", "pending"),
                                         ("packaging", "running"), ("approved", "completed")])
def test_passing_is_conditional_until_checks_have_finished(phase, status):
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    spec = extend_feedback_branch(graph(jev=True), projection(phase, review="jev", scored=True))
    assert next(edge for edge in spec["control_edges"] if edge["kind"] == "pass")["status"] == status
    assert "result routes and human revision" in spec["labels"]["lineage"]


def test_feedback_routes_in_the_real_javascript_component():
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    node = shutil.which("node")
    if not node or not (ROOT / "node_modules/jsdom").is_dir():
        pytest.skip("Node and jsdom are required")
    specs = []
    for language in ("en", "zh"):
        for jev in (False, True):
            for phase in ("waiting", "editing", "generating", "reviewing", "approved", "needs_revision", "revision_limit"):
                specs.append(extend_feedback_branch(graph(language=language, jev=jev),
                    projection(phase, review="jev" if jev else "sft", scored=jev,
                               actionable=phase not in {"waiting", "generating", "reviewing", "revision_limit"})))
            for phase in ("generating", "reviewing", "packaging", "approved"):
                specs.append(extend_feedback_branch(graph(language=language, jev=jev),
                    projection(phase, review="jev" if jev else "sft", scored=jev, kind=None)))
    # Historical package review and multiple-target layouts retain a safe loop.
    specs.append(extend_feedback_branch(graph(), projection(review="package", scored=True)))
    result = subprocess.run([node, str(ROOT / "tests/workflow_feedback_canvas_harness.mjs")],
        input=json.dumps(specs), cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def feedback_app(actionable=True):
    from streamlit.testing.v1 import AppTest
    script = f'''
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import workflow_canvas as canvas
st.session_state.setdefault('selected', 'ingest')
labels = {{key:key for key in ('ingest','sft','package')}}
spec = canvas.canvas_spec(['sft'], {{}}, st.session_state['selected'], labels, labels, live=True,
    feedback_branch={{'phase':'needs_revision','generation_node':'sft','review_node':'sft','actionable':{actionable!r}}})
spec['feedback_target'] = 'human-results-editor'
with patch.object(canvas, '_canvas', lambda **kw: st.session_state.get('event')):
    canvas.render_canvas(spec, 'selected', key='feedback-fixture',
                         inspector_key='fixture-inspector', follow_key='fixture-follow')
'''
    return AppTest.from_string(script).run()


def test_feedback_receipt_is_explicit_and_consumed_once_without_opening_an_inspector():
    ui = feedback_app()
    ui.session_state['event'] = {'action': 'feedback', 'node': 'sft', 'serial': 'click-1'}
    ui.run()
    assert not ui.exception
    assert ui.session_state['selected'] == 'sft'
    assert ui.session_state['canvas-feedback:feedback-fixture'] == 'click-1'
    assert ui.session_state['canvas-open:feedback-fixture'] is False
    assert ui.session_state['canvas-pause:fixture-follow'] is True
    del ui.session_state['canvas-feedback:feedback-fixture']
    ui.run()
    assert 'canvas-feedback:feedback-fixture' not in ui.session_state


@pytest.mark.parametrize('actionable,node', [(False, 'sft'), (True, 'package'), (True, 'imaginary')])
def test_non_actionable_or_unmapped_feedback_does_not_publish_a_receipt(actionable, node):
    ui = feedback_app(actionable)
    ui.session_state['event'] = {'action': 'feedback', 'node': node, 'serial': 'invalid'}
    ui.run()
    assert not ui.exception
    assert ui.session_state['selected'] == 'ingest'
    assert 'canvas-feedback:feedback-fixture' not in ui.session_state
