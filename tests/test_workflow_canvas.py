"""Exercise the real canvas script with browser-style selection events."""
from pathlib import Path
from itertools import combinations
import json
import re
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

def _spec(targets, *, reasoning_trim=False, package_review=None):
    from lib.domain.workflow_graph import execution_graph
    from lib.presentation.streamlit.workflow_canvas import canvas_spec

    labels = {key: key for key in execution_graph(targets, reasoning_trim=reasoning_trim, package_review=package_review)[0]}
    return canvas_spec(targets, {}, "package", labels, labels, language="en",
                       reasoning_trim=reasoning_trim, package_review=package_review)


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

    specs = [_spec(targets, reasoning_trim=trim) for trim in (False, True)
             for targets in ([], ["cpt"], ["sft"], ["dpo", "cot"],
                             ["sft", "dpo", "cot"], list(TARGETS))]
    specs.extend(_spec(targets, reasoning_trim=trim, package_review={"enabled": True, "node": "jev"})
                 for trim in (False, True) for targets in (["cpt"], ["sft", "cot"], list(TARGETS)))
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


def _inspector_app(*, live=False, expanded=False, reveal_target_key=None):
    from streamlit.testing.v1 import AppTest

    reveal_options = {'reveal_key': 'fixture-reveal'}
    if reveal_target_key is not None:
        reveal_options['reveal_target_key'] = reveal_target_key
    script = f'''
from copy import deepcopy
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import workflow_canvas as canvas
st.session_state.setdefault('selected-node', 'ingest')
st.session_state.setdefault('workflow-node-bindings:fixture', {{'sft': {{}}}})
st.session_state.setdefault('workflow-form-draft:fixture', {{'prompt': 'Keep my unsaved prompt'}})
labels = {{key:key for key in ('ingest','sft','package')}}
spec = canvas.canvas_spec(['sft'], {{'sft': {{'status': 'configuration_required'}}}},
                         st.session_state['selected-node'], labels, labels,
                         st.session_state['workflow-node-bindings:fixture'], live={live!r})
def component(**kwargs):
    st.session_state['captured-spec'] = deepcopy(kwargs['spec'])
    return st.session_state.get('event')
with patch.object(canvas, '_canvas', component):
    canvas.render_canvas(spec, 'selected-node', key='fixture-canvas',
                         follow_key={'workflow-follow:fixture' if live else None!r},
                         inspector_key='fixture-inspector', expanded={expanded!r},
                         **{reveal_options!r})
'''
    return AppTest.from_string(script).run()


def _assert_node_drafts_preserved(ui):
    assert ui.session_state['workflow-node-bindings:fixture'] == {'sft': {}}
    assert ui.session_state['workflow-form-draft:fixture'] == {'prompt': 'Keep my unsaved prompt'}


@pytest.mark.parametrize('expanded', [False, True])
def test_setup_canvas_passes_inspector_state_and_opens_incomplete_node(expanded):
    ui = _inspector_app(expanded=expanded)
    assert not ui.exception
    assert ui.session_state['captured-spec']['inspector'] == {'key': 'fixture-inspector', 'open': False, 'wide': False}
    assert ui.session_state['captured-spec'].get('expanded', False) is expanded
    assert 'canvas-open:fixture-canvas' not in ui.session_state

    ui.session_state['event'] = {'node': 'sft', 'serial': 'click-1'}
    ui.run()
    assert not ui.exception
    assert ui.session_state['selected-node'] == 'sft'
    assert ui.session_state['canvas-open:fixture-canvas'] is True
    assert ui.session_state['captured-spec']['inspector'] == {'key': 'fixture-inspector', 'open': True, 'wide': False}
    assert 'canvas-pause:workflow-follow:fixture' not in ui.session_state
    _assert_node_drafts_preserved(ui)


@pytest.mark.parametrize('paused', [False, True])
def test_closing_inspector_preserves_selection_following_and_node_drafts(paused):
    ui = _inspector_app(live=True, expanded=True)
    ui.session_state['event'] = {'node': 'sft', 'serial': 1}
    ui.run()
    assert not ui.exception
    assert ui.session_state['canvas-pause:workflow-follow:fixture'] is True

    ui.session_state['canvas-pause:workflow-follow:fixture'] = paused
    ui.session_state['event'] = {'action': 'close', 'node': 'sft', 'serial': 2}
    ui.run()
    assert not ui.exception
    assert ui.session_state['canvas-open:fixture-canvas'] is False
    assert ui.session_state['selected-node'] == 'sft'
    assert ui.session_state['canvas-pause:workflow-follow:fixture'] is paused
    assert ui.session_state['captured-spec']['inspector']['open'] is False
    _assert_node_drafts_preserved(ui)

    # A refresh, including a replayed selection with the close event's serial,
    # must leave the inspector closed. Only a new click can reopen it.
    ui.run()
    ui.session_state['event'] = {'node': 'sft', 'serial': 2}
    ui.run()
    assert not ui.exception
    assert ui.session_state['canvas-open:fixture-canvas'] is False
    assert ui.session_state['canvas-pause:workflow-follow:fixture'] is paused
    ui.session_state['event'] = {'node': 'sft', 'serial': 3}
    ui.run()
    assert not ui.exception
    assert ui.session_state['canvas-open:fixture-canvas'] is True
    assert ui.session_state['canvas-pause:workflow-follow:fixture'] is True
    _assert_node_drafts_preserved(ui)


@pytest.mark.parametrize('event', [
    {'action': 'close', 'node': 'unknown', 'serial': 2},
    {'action': 'close', 'node': 'ingest', 'serial': 2},
    {'action': 'close', 'serial': 2},
    {'action': 'close', 'node': 'sft'},
    {'action': 'close', 'node': 'sft', 'serial': None},
    {'action': 'close', 'node': 'sft', 'serial': ''},
    {'action': 'close', 'node': 'sft', 'serial': []},
    {'action': 'close', 'node': 'sft', 'serial': {}},
])
def test_invalid_close_event_does_not_consume_or_change_node_state(event):
    ui = _inspector_app(live=True)
    ui.session_state['event'] = {'node': 'sft', 'serial': 1}
    ui.run()
    ui.session_state['canvas-pause:workflow-follow:fixture'] = False
    ui.session_state['event'] = event
    ui.run()
    assert not ui.exception
    assert ui.session_state['canvas-open:fixture-canvas'] is True
    assert ui.session_state['selected-node'] == 'sft'
    assert ui.session_state['canvas-pause:workflow-follow:fixture'] is False
    assert ui.session_state['canvas-event:fixture-canvas'] == 1
    _assert_node_drafts_preserved(ui)


def test_runtime_panel_selectors_are_safe_stable_and_distinct_for_arbitrary_run_ids():
    from lib.presentation.streamlit.workflow_page import _run_panel_keys

    run_ids = ('run:one', 'run/one', 'run-one', '运行 一', 'run.one', 'run_one', '', '☷/节点:1')
    seen = set()
    for run_id in run_ids:
        keys = _run_panel_keys(run_id)
        assert keys == _run_panel_keys(run_id), 'refreshes must target the same mounted native panels'
        assert set(keys) == {'canvas', 'node', 'header'}
        assert len(set(keys.values())) == 3
        assert all(re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in keys.values())
        assert not seen.intersection(keys.values()), 'distinct run IDs must not alias after selector sanitization'
        seen.update(keys.values())


@pytest.mark.parametrize('target_key', [None, 'workflow-run-canvas-runtime-hash'])
def test_reveal_uses_the_default_or_custom_runtime_canvas_target_without_changing_the_node(target_key):
    ui = _inspector_app(live=True, expanded=True, reveal_target_key=target_key)
    ui.session_state['selected-node'] = 'sft'
    ui.session_state['canvas-open:fixture-canvas'] = True
    ui.session_state['canvas-wide:fixture-canvas'] = True
    ui.session_state['canvas-pause:workflow-follow:fixture'] = False
    ui.session_state['fixture-reveal'] = {'node': 'sft', 'serial': 1}
    ui.run()
    assert not ui.exception
    assert ui.session_state['captured-spec']['reveal'] == {
        'node': 'sft', 'serial': 1, 'key': target_key or 'workbench-canvas-panel'}
    assert ui.session_state['captured-spec']['inspector'] == {
        'key': 'fixture-inspector', 'open': True, 'wide': True}
    assert ui.session_state['captured-spec']['expanded'] is True
    ui.run()
    assert not ui.exception and ui.session_state['fixture-reveal'] == {'node': 'sft', 'serial': 1}
    assert ui.session_state['selected-node'] == 'sft'
    assert ui.session_state['canvas-pause:workflow-follow:fixture'] is False
    _assert_node_drafts_preserved(ui)


@pytest.mark.parametrize('outcome', ['completed', 'cancelled'])
@pytest.mark.parametrize('request_serial,receipt_serial,accepted', [
    (1, 1, True),
    (1, True, False),
    (1, 1.0, False),
    (1, '1', False),
    (1, 0, False),
    ('1', '1', True),
    ('1', 1, False),
])
def test_reveal_receipt_requires_the_exact_nonce_type_and_preserves_live_inspector_state(
        request_serial, receipt_serial, accepted, outcome):
    ui = _inspector_app(live=True, expanded=True,
                        reveal_target_key='workflow-run-canvas-runtime-hash')
    request = {'node': 'sft', 'serial': request_serial}
    ui.session_state['selected-node'] = 'sft'
    ui.session_state['canvas-open:fixture-canvas'] = True
    ui.session_state['canvas-wide:fixture-canvas'] = True
    ui.session_state['canvas-pause:workflow-follow:fixture'] = False
    ui.session_state['fixture-reveal'] = request
    ui.session_state['event'] = {'action': 'reveal', 'node': 'sft', 'request_serial': receipt_serial,
                                 'outcome': outcome, 'serial': 'bridge-ack'}
    ui.run()
    assert not ui.exception
    if accepted:
        assert 'fixture-reveal' not in ui.session_state
        ui.run()
        assert not ui.exception and 'reveal' not in ui.session_state['captured-spec']
    else:
        assert ui.session_state['fixture-reveal'] == request
        assert ui.session_state['captured-spec']['reveal'] == {
            **request, 'key': 'workflow-run-canvas-runtime-hash'}
    assert ui.session_state['selected-node'] == 'sft'
    assert ui.session_state['canvas-open:fixture-canvas'] is True
    assert ui.session_state['canvas-wide:fixture-canvas'] is True
    assert ui.session_state['canvas-pause:workflow-follow:fixture'] is False
    assert 'canvas-event:fixture-canvas' not in ui.session_state
    _assert_node_drafts_preserved(ui)
