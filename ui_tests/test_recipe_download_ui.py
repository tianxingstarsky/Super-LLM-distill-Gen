"""Large repair recipes must not saturate the refreshing workbench."""
from copy import deepcopy
import json

from streamlit.testing.v1 import AppTest


def test_full_snapshot_is_deferred_and_download_keeps_every_nested_field(monkeypatch):
    from lib.presentation.streamlit import workflow_recipe_controls as controls
    calls=[]
    class Widgets:
        def caption(self, value):pass
        def download_button(self, label, data, **options):calls.append((data,options))
    monkeypatch.setattr(controls,'st',Widgets())
    recipe={'targets':['cot'],'repair_inputs':[{'record':{'reasoning':'long trace\n'*200000}}],
            'node_prompt_templates':{'review':{'custom':'Keep {all} conditions. 中文'}}}
    frozen=deepcopy(recipe)
    controls.render_recipe_download(recipe,'fixture')
    callback,options=calls[0]
    assert callable(callback) and options['on_click']=='ignore'
    assert json.loads(callback())==recipe==frozen
    assert options['file_name']=='fixture-recipe.json'


def test_actual_running_page_does_not_emit_megabytes_of_hidden_repair_json():
    app=AppTest.from_string('''
from unittest.mock import patch
import streamlit as st
from ui_tests.test_running_workflow_activity_ui import activity_application
from lib.presentation.streamlit import workflow_canvas
from lib.presentation.streamlit.workflow_page import render_run
st.session_state['ws']='fixture'
application,begin=activity_application()
recipe=application.recipe('a'*32)
recipe['repair_inputs']=[{'record':{'reasoning':'PRIVATE_LARGE_REPAIR_TRACE'*100000}}]
application.recipe=lambda _:recipe
with patch.object(workflow_canvas,'_canvas',lambda **kwargs:None):
    render_run(application,'a'*32,begin)
''',default_timeout=20).run()
    assert not app.exception
    assert any(button.key=='workflow-recipe-download:'+'a'*32 for button in app.get('download_button'))
    assert all('PRIVATE_LARGE_REPAIR_TRACE' not in str(item.value) for item in app.get('json'))
    assert sum(len(str(item.value)) for item in app.get('json'))<20000
