"""Exercise the real canvas script with browser-style selection events."""
from pathlib import Path
from itertools import combinations
import json
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

def _spec(targets):
    from lib.domain.workflow_graph import execution_graph
    from lib.presentation.streamlit.workflow_canvas import canvas_spec

    labels = {key: key for key in execution_graph(targets)[0]}
    return canvas_spec(targets, {}, "package", labels, labels, language="en")


def test_all_target_combinations_fit_without_overlapping_nodes():
    from lib.domain.workflow_targets import TARGETS

    targets = tuple(TARGETS)
    for count in range(len(targets) + 1):
        for selected in combinations(targets, count):
            spec = _spec(selected)
            columns = {}
            for node in spec["nodes"]:
                assert 0 <= node["x"] <= spec["width"] - 218, selected
                assert 0 <= node["y"] <= spec["height"] - 78, selected
                columns.setdefault(node["x"], []).append(node["y"])
            for column in columns.values():
                positions = sorted(column)
                assert all(after - before >= 78 for before, after in zip(positions, positions[1:])), selected

    assert _spec(["cpt"])["height"] < 200
    assert _spec(["dpo", "cot"])["height"] > _spec(["dpo"])["height"]


def test_canvas_node_navigation_and_dependency_highlight():
    node = shutil.which('node')
    if not node or not (ROOT / 'node_modules/jsdom').is_dir():
        pytest.skip('Node and jsdom are required')
    from lib.domain.workflow_targets import TARGETS

    specs = [_spec(targets) for targets in ([], ["cpt"], ["dpo", "cot"],
                                           ["sft", "dpo", "cot"], list(TARGETS))]
    result = subprocess.run([node, str(ROOT / 'tests/workflow_canvas_harness.mjs'), '--check-specs'],
                            input=json.dumps(specs), cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_clicking_a_live_node_opens_its_output_and_pauses_automatic_following():
    from streamlit.testing.v1 import AppTest

    script = '''
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import workflow_canvas as canvas
labels = {key:key for key in ('ingest','sft','package')}
spec = canvas.canvas_spec(['sft'], {}, 'ingest', labels, labels, live=True)
with patch.object(canvas, '_canvas', lambda **kw: st.session_state.get('event')):
    canvas.render_canvas(spec, 'workflow-stage:run', key='live-canvas:run', follow_key='workflow-follow:run')
'''
    ui = AppTest.from_string(script).run()
    assert not ui.exception and 'canvas-open:live-canvas:run' not in ui.session_state
    ui.session_state['event'] = {'node':'sft', 'serial':1}
    ui.run()
    assert not ui.exception
    assert ui.session_state['workflow-stage:run'] == 'sft'
    assert ui.session_state['canvas-open:live-canvas:run'] is True
    assert ui.session_state['canvas-pause:workflow-follow:run'] is True
    # Closing must stay closed on a later refresh of the same canvas event.
    ui.session_state['canvas-open:live-canvas:run'] = False
    ui.run()
    assert not ui.exception and ui.session_state['canvas-open:live-canvas:run'] is False
    ui.session_state['event'] = {'node':'sft', 'serial':2}
    ui.run()
    assert ui.session_state['canvas-open:live-canvas:run'] is True
