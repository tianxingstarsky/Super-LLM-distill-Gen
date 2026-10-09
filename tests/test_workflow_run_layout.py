"""Keep the native runtime inspector beside its canvas across fragment refreshes."""
from unittest.mock import patch

from streamlit.runtime.scriptrunner import ScriptRunnerEvent
from streamlit.runtime.state.common import user_key_from_element_id
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Block
from streamlit.testing.v1.local_script_runner import LocalScriptRunner


SCRIPT = '''
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx
from streamlit.runtime.scriptrunner_utils.script_run_context import ThreadState
from streamlit.runtime.scriptrunner_utils.script_requests import RerunData
from lib.presentation.streamlit.workflow_page import render_run, GRAPH_LABELS
st.session_state['ws']='layout-fixture'
st.session_state.setdefault('workflow-stage:layout-fixture','sft')
st.session_state.setdefault('workflow-follow:layout-fixture',False)
st.session_state.setdefault('canvas-open:live-canvas:layout-fixture',True)
class Application:
    def state(self,run_id):
        ctx=get_script_run_ctx()
        st.session_state['fixture-outer-fragment']=ThreadState.get().fragment_id
        modes=st.session_state.setdefault('fixture-run-modes',[])
        modes.append(list(ctx.fragment_ids_this_run or []))
        stages={key:{'label':label,'status':'pending','done':0,'total':3}
                for key,label in GRAPH_LABELS.items()}
        stages['ingest'].update(status='completed',done=3)
        stages['sft'].update(status='running',done=len(modes))
        return {'name':'Runtime layout fixture','status':'running','targets':['sft'],
                'stages':stages,'events':[{'stage':'sft','kind':'stage_started','at':'2026-10-09T10:00:00Z'}]}
    def recipe(self,run_id):
        return {'targets':['sft'],'sources':[],'node_prompt_templates':{'sft':{
            'workflow.sft':'Saved writer instructions with {literal braces}.',
            'workflow.jev_score':'Saved reviewer instructions.'}}}
    def is_active(self,run_id): return True
    def read_streams(self,run_id,*,stage): return []
render_run(Application(),'layout-fixture',lambda args:None)
if st.session_state.get('fixture-auto-refresh'):
    ctx=get_script_run_ctx()
    # Queue the same fragment-only request the auto-rerun protocol uses. The
    # runner completes the full script, then executes only this stored fragment.
    ctx.script_requests.request_rerun(RerunData(
        fragment_id=st.session_state['fixture-outer-fragment'],is_auto_rerun=True))
'''


def _path_to(block, target):
    for index, child in block.children.items():
        if child is target:
            return (index,)
        if isinstance(child, Block):
            found = _path_to(child, target)
            if found is not None:
                return (index, *found)
    return None


def _assert_runtime_structure(ui):
    from lib.presentation.streamlit.workflow_page import _run_panel_keys

    assert not ui.exception
    keys = _run_panel_keys('layout-fixture')
    layout = ui.main.get_by_key('workflow-run-layout:layout-fixture')
    children = [child for _, child in sorted(layout.children.items())]
    assert [child.key for child in children] == [keys['canvas'], keys['node']]
    canvas, panel = children
    assert canvas.proto.flex_container.border is False
    prompt = panel.text_area(key='workflow-run-prompt-body:layout-fixture:sft:workflow.sft')
    assert prompt.disabled and prompt.value == 'Saved writer instructions with {literal braces}.'
    assert _path_to(ui.main, prompt)[:len(_path_to(ui.main, panel))] == _path_to(ui.main, panel)
    tabs = list(ui.tabs)
    assert [tab.label for tab in tabs] == ['产物与质量', '节点日志', '全部事件', '来源与配方']
    log_tab = tabs[1]
    assert any('当前筛选：' in item.proto.body for item in log_tab.get('html'))
    assert not any('当前筛选：' in item.proto.body for item in layout.get('html'))
    return {name: _path_to(ui.main, node) for name, node in
            (('canvas', canvas), ('panel', panel), ('prompt', prompt), ('log', log_tab))}


def test_runtime_canvas_and_native_prompt_panel_are_direct_siblings_with_logs_in_a_tab():
    ui = AppTest.from_string(SCRIPT).run()
    before = _assert_runtime_structure(ui)
    ui.run()  # This is a full AppTest rerun, not a simulated timer refresh.
    assert _assert_runtime_structure(ui) == before
    assert ui.session_state['fixture-run-modes'] == [[], []]


def test_queued_auto_fragment_refresh_keeps_canvas_panel_prompt_and_log_delta_paths():
    runs = []
    original_run = LocalScriptRunner.run

    def capture_runner(runner, *args, **kwargs):
        def capture(_sender, event, **data):
            if event == ScriptRunnerEvent.SCRIPT_STARTED:
                runs.append({'fragments': tuple(data.get('fragment_ids_this_run') or ()), 'paths': {}})
            elif event == ScriptRunnerEvent.ENQUEUE_FORWARD_MSG:
                msg = data['forward_msg']
                if not msg.HasField('delta') or not runs:
                    return
                key = None
                if msg.delta.WhichOneof('type') == 'add_block':
                    block = msg.delta.add_block
                    if block.id:
                        key = user_key_from_element_id(block.id)
                    elif block.WhichOneof('type') == 'tab' and block.tab.label == '节点日志':
                        key = 'node-log-tab'
                elif msg.delta.WhichOneof('type') == 'new_element':
                    element = msg.delta.new_element
                    if element.WhichOneof('type') == 'text_area':
                        key = user_key_from_element_id(element.text_area.id)
                if key:
                    runs[-1]['paths'][key] = tuple(msg.metadata.delta_path)
        runner.on_event.connect(capture, weak=False)
        return original_run(runner, *args, **kwargs)

    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-auto-refresh'] = True
    with patch.object(LocalScriptRunner, 'run', capture_runner):
        ui.run()
    _assert_runtime_structure(ui)
    fragment_id = ui.session_state['fixture-outer-fragment']
    assert [run['fragments'] for run in runs] == [(), (fragment_id,)]
    assert ui.session_state['fixture-run-modes'] == [[], [fragment_id]]
    from lib.presentation.streamlit.workflow_page import _run_panel_keys
    keys = _run_panel_keys('layout-fixture')
    for key in (keys['canvas'], keys['node'], 'node-log-tab',
                'workflow-run-prompt-body:layout-fixture:sft:workflow.sft'):
        assert key in runs[0]['paths'] and key in runs[1]['paths']
        assert runs[0]['paths'][key] == runs[1]['paths'][key], key
