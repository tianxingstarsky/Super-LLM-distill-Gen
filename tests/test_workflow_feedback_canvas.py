"""Correction stays inside the real scoring/repair stage, never returns to SFT."""
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


def graph(*, language="en", target="sft", jev=False, live=True, mode="human", version=18,
          repair_only=False):
    from lib.domain.workflow_graph import execution_graph
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    targets = [target]
    review = {"enabled": True, "node": "jev"} if jev else None
    keys, _ = execution_graph(targets, package_review=review, recipe_version=version,
                              repair_only=repair_only)
    labels = {key: key for key in keys}
    return canvas_spec(targets, {}, "ingest", labels, labels, language=language,
        live=live, package_review=review, recipe_version=version,
        review_repair={"mode": mode} if version >= 18 else None, repair_only=repair_only)


def projection(phase="needs_revision", *, actionable=True, scored=False, mode="human",
               kind="revision", parent_run_ids=()):
    return {"phase": phase, "generation_node": "sft", "review_node": "review", "actionable": actionable,
            "kind": kind, "parent_run_ids": list(parent_run_ids), "mode": mode,
            "stages": {"scoring": {"enabled": scored,
                                    "status": "completed" if scored else "disabled"}}}


def test_repair_branch_opens_and_rechecks_the_same_real_stage_without_sft_return():
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    original = graph(jev=True)
    unchanged = deepcopy(original)
    spec = extend_feedback_branch(original, projection(scored=True))
    assert original == unchanged
    assert [node["id"] for node in spec["nodes"]] == [node["id"] for node in original["nodes"]]
    assert spec["edges"] == original["edges"]
    assert spec["control_nodes"][0]["select_node"] == "review"
    assert spec["control_edges"][0]["from"] == "review"
    assert next(edge for edge in spec["control_edges"] if edge["kind"] == "return")["to"] == "review"
    assert not any(edge["to"] == "sft" for edge in spec["control_edges"])
    assert spec["feedback_branch"]["group"]["x"] <= spec["control_nodes"][0]["x"]


@pytest.mark.parametrize("version", [14, 16, 17])
@pytest.mark.parametrize("jev", [False, True])
def test_historical_recipes_do_not_gain_a_fake_repair_stage_or_generation_return(version, jev):
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    spec = graph(version=version, jev=jev)
    assert "review" not in {node["id"] for node in spec["nodes"]}
    assert extend_feedback_branch(spec, projection()) == spec
    assert "control_nodes" not in spec


@pytest.mark.parametrize("mode,scored,description", [
    ("human", False, "Human score + edit"),
    ("auto", False, "One model: score + repair"),
    ("auto", True, "JEV score + repair model")])
def test_branch_labels_explain_human_single_model_and_jev_roles(mode, scored, description):
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    spec = extend_feedback_branch(graph(mode=mode, jev=scored), projection(mode=mode, scored=scored))
    assert spec["feedback_branch"]["review_label"] == description
    assert spec["control_nodes"][0]["action"] == ("feedback" if mode == "human" else None)


@pytest.mark.parametrize("phase,status", [("waiting", "pending"), ("editing", "needs_revision"),
    ("repairing", "running"), ("generating", "running"), ("reviewing", "completed"),
    ("packaging", "completed"), ("approved", "completed"), ("needs_revision", "needs_revision"),
    ("revision_limit", "needs_revision"), ("interrupted", "needs_revision")])
def test_projection_states_keep_score_repair_and_recheck_in_one_stage(phase, status):
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    spec = extend_feedback_branch(graph(), projection(phase))
    assert spec["control_nodes"][0]["status"] == status
    assert spec["feedback_branch"]["phase"] == phase
    assert spec["control_nodes"][0]["select_node"] == "review"
    assert next(edge for edge in spec["control_edges"] if edge["kind"] == "return")["to"] == "review"
    if phase == "revision_limit":
        assert not spec["control_nodes"][0]["actionable"]


def test_manual_correction_child_has_no_fake_generation_nodes():
    spec = graph(repair_only=True)
    assert [node["id"] for node in spec["nodes"]] == ["ingest", "review", "package"]
    assert spec["edges"] == (("ingest", "review"), ("review", "package"))
    assert spec["width"] == 810


def test_human_control_waits_for_real_results_but_auto_control_opens_stage_configuration():
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    human = graph()
    assert not human["control_nodes"][0]["actionable"]
    automatic = graph(mode="auto")
    assert automatic["control_nodes"][0]["actionable"]
    assert automatic["control_nodes"][0]["action"] is None


@pytest.mark.parametrize("phase,status", [("repairing", "pending"), ("reviewing", "pending"),
                                         ("packaging", "running"), ("approved", "completed")])
def test_passing_waits_for_actual_scoring(phase, status):
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    spec = extend_feedback_branch(graph(jev=True), projection(phase, scored=True))
    assert next(edge for edge in spec["control_edges"] if edge["kind"] == "pass")["status"] == status


def test_score_repair_routes_in_the_real_javascript_component():
    from lib.presentation.streamlit.workflow_canvas import extend_feedback_branch
    node = shutil.which("node")
    if not node or not (ROOT / "node_modules/jsdom").is_dir():
        pytest.skip("Node and jsdom are required")
    specs = []
    for language in ("en", "zh"):
        for mode in ("human", "auto"):
            for jev in (False, True):
                for phase in ("waiting", "editing", "repairing", "reviewing", "approved", "needs_revision", "revision_limit"):
                    specs.append(extend_feedback_branch(graph(language=language, jev=jev, mode=mode),
                        projection(phase, scored=jev, mode=mode,
                                   actionable=phase not in {"waiting", "repairing", "reviewing", "revision_limit"})))
    specs.append(graph(language="en", mode="auto", repair_only=True))
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
labels = {{key:key for key in ('ingest','sft','review','package')}}
spec = canvas.canvas_spec(['sft'], {{}}, st.session_state['selected'], labels, labels, live=True,
    recipe_version=18, review_repair={{'mode':'human'}},
    feedback_branch={{'phase':'needs_revision','mode':'human','review_node':'review','actionable':{actionable!r}}})
spec['feedback_target'] = 'human-results-editor'
with patch.object(canvas, '_canvas', lambda **kw: st.session_state.get('event')):
    canvas.render_canvas(spec, 'selected', key='feedback-fixture',
                         inspector_key='fixture-inspector', follow_key='fixture-follow')
'''
    return AppTest.from_string(script).run()


def test_feedback_receipt_opens_review_and_is_consumed_once():
    ui = feedback_app()
    ui.session_state['event'] = {'action': 'feedback', 'node': 'review', 'serial': 'click-1'}
    ui.run()
    assert not ui.exception
    assert ui.session_state['selected'] == 'review'
    assert ui.session_state['canvas-feedback:feedback-fixture'] == 'click-1'
    assert ui.session_state['canvas-open:feedback-fixture'] is False
    del ui.session_state['canvas-feedback:feedback-fixture']
    ui.run()
    assert 'canvas-feedback:feedback-fixture' not in ui.session_state


@pytest.mark.parametrize('actionable,node', [(False, 'review'), (True, 'sft'), (True, 'package'), (True, 'imaginary')])
def test_non_actionable_or_generation_mapped_feedback_is_rejected(actionable, node):
    ui = feedback_app(actionable)
    ui.session_state['event'] = {'action': 'feedback', 'node': node, 'serial': 'invalid'}
    ui.run()
    assert not ui.exception
    assert ui.session_state['selected'] == 'ingest'
    assert 'canvas-feedback:feedback-fixture' not in ui.session_state
