"""Exercise optional JEV-node configuration without making model calls."""
import re
from copy import deepcopy

from streamlit.testing.v1 import AppTest

from tests.test_workflow_draft import SCRIPT
from tests.test_workflow_follow import SCRIPT as FOLLOW_SCRIPT
from lib.presentation.streamlit.i18n import translate_markup
from lib.presentation.streamlit.workflow_package_review_report import package_review_report_html
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft


def test_review_toggle_models_prompts_and_scope_survive_node_changes():
    ui = AppTest.from_string(SCRIPT).run()
    ui.button(key='fixture-node:package').click().run()
    enabled = 'workflow-package-review-enabled:fixture'
    assert sum(widget.key == enabled for widget in ui.toggle) == 1
    assert not ui.toggle(key=enabled).value
    assert not any(widget.key == 'fixture-node:jev' for widget in ui.button)
    assert not any(w.key.startswith('node-model:fixture:jev:') for w in ui.selectbox)
    ui.toggle(key=enabled).set_value(True).run()
    ui.button(key='fixture-node:jev').click().run()
    assert not ui.exception
    assert ui.segmented_control(key='workflow-package-review-mode:fixture').value == 'sample'
    assert ui.number_input(key='workflow-package-review-percent:fixture').value == 1.0
    assert ui.number_input(key='workflow-package-review-limit:fixture').value == 1000
    assert ui.selectbox(key='node-model:fixture:jev:jev:backend')
    prompt = 'workflow-node-prompt:fixture:jev:workflow.package_review'
    assert ui.text_area(key=prompt).value
    ui.number_input(key='workflow-package-review-percent:fixture').set_value(2.5).run()
    ui.number_input(key='workflow-package-review-limit:fixture').set_value(250).run()
    ui.text_area(key=prompt).set_value('Custom packaging quality instructions.').run()
    ui.segmented_control(key='workflow-package-review-mode:fixture').set_value('all').run()
    ui.button(key='fixture-node:sft').click().run()
    ui.button(key='fixture-node:jev').click().run()
    assert ui.segmented_control(key='workflow-package-review-mode:fixture').value == 'all'
    assert ui.text_area(key=prompt).value == 'Custom packaging quality instructions.'
    ui.segmented_control(key='workflow-package-review-mode:fixture').set_value('sample').run()
    assert ui.number_input(key='workflow-package-review-percent:fixture').value == 2.5
    assert ui.number_input(key='workflow-package-review-limit:fixture').value == 250
    ui.toggle(key=enabled).set_value(False).run()
    assert not any(widget.key == 'fixture-node:jev' for widget in ui.button)
    assert not any(w.key.startswith('node-model:fixture:jev:') for w in ui.selectbox)
    ui.toggle(key=enabled).set_value(True).run()
    ui.button(key='fixture-node:jev').click().run()
    assert ui.text_area(key=prompt).value == 'Custom packaging quality instructions.'
    assert not ui.exception


def test_missing_review_model_blocks_only_enabled_packaging_review():
    ui = AppTest.from_string(SCRIPT).run()
    ui.pills(key='workflow-targets:fixture:自动推荐').set_value(['cpt']).run()
    ui.button(key='fixture-node:package').click().run()
    assert not ui.button(key='workflow-create:fixture').disabled
    # Previously initialized with no binding: no new default should override
    # an operator's empty selection when the optional role is enabled again.
    ui.session_state['workflow-node-bindings:fixture'] = {}
    ui.session_state['workflow-node-bindings:fixture:initialized'] = ['jev:jev']
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(True).run()
    ui.button(key='fixture-node:jev').click().run()
    assert ui.button(key='workflow-create:fixture').disabled
    ui.selectbox(key='node-model:fixture:jev:jev:backend').set_value('local').run()
    ui.selectbox(key='node-model:fixture:jev:jev:model:local').set_value('judge').run()
    assert not ui.exception
    assert not ui.button(key='workflow-create:fixture').disabled
    assert ui.number_input(key='node-model:fixture:jev:jev:output:local:judge').value == 32768


def test_package_review_can_be_copied_back_to_an_editable_draft():
    recipe = {'targets': ['cpt'], 'sources': [], 'brief': 'Fixture task',
              'package_review': {'enabled': True, 'mode': 'sample', 'sample_percent': 2.5,
                                 'max_samples_per_target': 250},
              'node_prompts': {'package': {'workflow.package_review': 'Custom review.'}}}
    values = recipe_to_draft(recipe, 'Copied', 'fixture', [])['values']
    assert values['workflow-package-review-enabled:fixture'] is True
    assert values['workflow-package-review-mode:fixture'] == 'sample'
    assert values['workflow-package-review-percent:fixture'] == 2.5
    assert values['workflow-package-review-limit:fixture'] == 250
    assert values['workflow-node-prompt:fixture:jev:workflow.package_review'] == 'Custom review.'


def test_existing_package_prompt_is_migrated_once_and_jev_edits_take_precedence():
    old_key = 'workflow-node-prompt:fixture:package:workflow.package_review'
    new_key = 'workflow-node-prompt:fixture:jev:workflow.package_review'
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['workflow-form-draft:fixture'] = {old_key: 'Keep my existing review prompt.'}
    ui.run()
    assert not ui.exception
    assert ui.session_state['workflow-form-draft:fixture'][new_key] == 'Keep my existing review prompt.'
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(True).run()
    ui.button(key='fixture-node:jev').click().run()
    assert ui.text_area(key=new_key).value == 'Keep my existing review prompt.'
    ui.text_area(key=new_key).set_value('Updated JEV criteria.').run()
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(False).run()
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(True).run()
    ui.button(key='fixture-node:jev').click().run()
    assert ui.text_area(key=new_key).value == 'Updated JEV criteria.'
    assert ui.session_state['workflow-form-draft:fixture'][old_key] == 'Keep my existing review prompt.'


def test_reuse_moves_old_reviewer_to_jev_without_editing_historical_recipe():
    recipe = {'targets': ['cpt'], 'sources': [], 'brief': 'Fixture task',
              'package_review': {'enabled': False},
              'node_models': {'package': {'jev': {'backend': 'local', 'model': 'judge'}}},
              'node_prompts': {'package': {'workflow.package_review': 'My existing criteria.'}}}
    original = deepcopy(recipe)
    copied = recipe_to_draft(recipe, 'Copied', 'fixture', [])
    assert copied['node_bindings']['jev']['jev']['model'] == 'judge'
    assert 'package' not in copied['node_bindings']
    assert copied['values']['workflow-package-review-enabled:fixture'] is False
    assert copied['values']['workflow-node-prompt:fixture:jev:workflow.package_review'] == 'My existing criteria.'
    assert recipe == original


def test_new_package_report_and_controls_have_english_translations():
    report = package_review_report_html({'package_review': {'enabled': True, 'mode': 'sample',
        'targets': {'cpt': {'candidates': 10000, 'reviewed': 100, 'accepted': 95,
                            'rejected': 5, 'unreviewed': 9900}}}})
    translated = translate_markup(report, 'en')
    assert 'AI package review' in translated
    assert not re.search(r'[\u4e00-\u9fff]', translated)
    code = SCRIPT.replace('import streamlit as st', '''import streamlit as st
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
st.session_state['ui_language'] = 'en' ''', 1)
    ui = AppTest.from_string(code).run()
    ui.button(key='fixture-node:package').click().run()
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(True).run()
    ui.button(key='fixture-node:jev').click().run()
    assert ui.toggle(key='workflow-package-review-enabled:fixture').label == 'Add JEV scoring'
    assert ui.segmented_control(key='workflow-package-review-mode:fixture').label == 'Review scope'
    assert ui.number_input(key='workflow-package-review-limit:fixture').label == 'Sample cap per goal'
    assert not ui.exception


def test_submission_pins_package_scope_model_limits_and_custom_prompt():
    code = SCRIPT.replace('    def task_runs(self): return []', '''    def create_run(self, **kwargs):
        st.session_state['fixture-submitted'] = kwargs
        return '0' * 32
    def task_runs(self): return []''')
    ui = AppTest.from_string(code).run()
    ui.pills(key='workflow-targets:fixture:自动推荐').set_value(['cpt']).run()
    ui.multiselect(key='workflow-sources:fixture:文档资料').set_value(['fixture.txt']).run()
    ui.button(key='fixture-node:package').click().run()
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(True).run()
    ui.button(key='fixture-node:jev').click().run()
    ui.number_input(key='workflow-package-review-percent:fixture').set_value(2.5).run()
    ui.text_area(key='workflow-node-prompt:fixture:jev:workflow.package_review').set_value('Custom review.').run()
    ui.button(key='workflow-create:fixture').click().run()
    assert not ui.exception
    saved = ui.session_state['fixture-submitted']
    assert saved['package_review'] == {'enabled': True, 'node': 'jev', 'mode': 'sample',
                                       'sample_percent': 2.5, 'max_samples_per_target': 1000,
                                       'escalate_failure_percent': 0.0}
    assert saved['node_models']['jev']['jev'] == {'backend': 'local', 'model': 'writer',
        'context_window_tokens': 131072, 'max_output_tokens': 32768}
    assert saved['node_prompts']['jev']['workflow.package_review'] == 'Custom review.'


def test_live_packaging_shows_review_counts_and_distinguishes_file_writing():
    code = FOLLOW_SCRIPT.replace("        return {'name':", '''        if phase in ('ai_review', 'writing_artifacts'):
            stages['package'].update(status='running', phase=phase, done=100, total=100,
                                     eligible=95, quarantined=5)
        return {'name':''').replace(
        "def recipe(self,run_id): return {'targets':['sft'],'sources':[]}",
        "def recipe(self,run_id): return {'targets':['sft'],'sources':[],'package_review':{'enabled':True}}")
    ui = AppTest.from_string(code)
    ui.session_state['phase'] = 'ai_review'
    ui.run()
    assert not ui.exception
    assert any('正在逐条 AI 评审' in item.value for item in ui.caption)
    assert any('AI 通过' in item.proto.body and '95' in item.proto.body for item in ui.get('html'))
    ui.session_state['phase'] = 'writing_artifacts'
    ui.run()
    assert any('AI 评审已结束，正在写入' in item.value for item in ui.caption)
    assert not ui.exception


def test_new_live_jev_progress_is_separate_from_pending_packaging():
    code = FOLLOW_SCRIPT.replace("        return {'name':", '''        if phase == 'jev':
            stages['sft']['status'] = 'completed'
            stages['jev'].update(status='running', phase='ai_review', done=5, total=20,
                                 eligible=4, quarantined=1)
        return {'name':''').replace(
        "def recipe(self,run_id): return {'targets':['sft'],'sources':[]}",
        "def recipe(self,run_id): return {'version':16,'targets':['sft'],'sources':[],"
        "'package_review':{'enabled':True,'node':'jev'}}")
    ui = AppTest.from_string(code)
    ui.session_state['phase'] = 'jev'
    ui.run()
    assert not ui.exception
    spec = ui.session_state['fixture-canvas-spec']
    assert spec['selected'] == 'jev'
    nodes = {node['id']: node for node in spec['nodes']}
    assert nodes['jev']['percent'] == 25
    assert nodes['package']['status'] == 'pending'
    assert any('正在逐条 AI 评审' in item.value for item in ui.caption)


def test_new_packaging_does_not_label_local_exports_as_ai_verdicts():
    code = FOLLOW_SCRIPT.replace("        return {'name':", '''        stages['sft']['status'] = 'completed'
        stages['package'].update(status='running', phase='writing_artifacts', done=0, total=1)
        return {'name':''').replace(
        "def recipe(self,run_id): return {'targets':['sft'],'sources':[]}",
        "def recipe(self,run_id): return {'version':16,'targets':['sft'],'sources':[],"
        "'package_review':{'enabled':False,'node':'jev'}}").replace(
        "'targets':['sft'],'stages':stages,'events':[]",
        "'targets':['sft'],'stages':stages,'events':[],"
        "'quality':{'targets':{'sft':{'total':100,'eligible':95,'reasons':{'duplicate':5}}}}").replace(
        "    def state(self,run_id):", "    def artifact_location(self,run_id): return 'fixture-output'\n"
        "    def state(self,run_id):")
    ui = AppTest.from_string(code)
    ui.session_state['phase'] = 'writing_artifacts'
    ui.run()
    assert not ui.exception
    assert ui.session_state['fixture-canvas-spec']['selected'] == 'package'
    assert any('导出样本' in item.proto.body for item in ui.get('html'))
    assert not any('AI 通过' in item.proto.body for item in ui.get('html'))
    assert any(item.value == '正在写入训练文件、报告与清单。' for item in ui.caption)
