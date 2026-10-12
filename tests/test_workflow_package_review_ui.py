"""Exercise optional JEV-node configuration without making model calls."""
import re
from copy import deepcopy

from streamlit.testing.v1 import AppTest

from tests.test_workflow_draft import SCRIPT
from tests.test_workflow_follow import SCRIPT as FOLLOW_SCRIPT
from lib.presentation.streamlit.i18n import translate_markup
from lib.presentation.streamlit.workflow_package_review_report import package_review_report_html
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft


def confirm_source_review_models(ui):
    """Complete the same node confirmation steps required in the workbench."""
    for node in ('ingest', 'cpt', 'sft', 'review'):
        ui.button(key='fixture-node:' + node).click().run()
        confirm = ui.button(key='node-model-confirm:fixture:' + node)
        if not confirm.disabled:
            confirm.click().run()
    ui.button(key='fixture-node:review').click().run()


def source_script(tmp_path, code=SCRIPT):
    source = tmp_path / 'fixture.txt'
    source.write_text('Inspect the cable before using the equipment.', encoding='utf-8')
    return code.replace("'fixture.txt'", repr(str(source))), str(source)


def test_review_roles_prompts_and_policy_survive_node_and_mode_changes():
    ui = AppTest.from_string(SCRIPT).run()
    ui.button(key='fixture-node:review').click().run()
    enabled = 'workflow-package-review-enabled:fixture'
    assert sum(widget.key == enabled for widget in ui.toggle) == 1
    assert not ui.toggle(key=enabled).value
    assert not any(widget.key == 'fixture-node:jev' for widget in ui.button)
    assert ui.selectbox(key='node-model:fixture:review:generation:backend')
    assert not any(w.key.startswith('node-model:fixture:review:jev:') for w in ui.selectbox)
    ui.toggle(key=enabled).set_value(True).run()
    assert ui.selectbox(key='node-model:fixture:review:jev:backend')
    assert not any(widget.key == 'fixture-node:jev' for widget in ui.button)
    score = 'workflow-node-prompt:fixture:review:workflow.review_score'
    repair = 'workflow-node-prompt:fixture:review:workflow.review_repair'
    ui.number_input(key='workflow-review-repair-threshold:fixture').set_value(85).run()
    ui.number_input(key='workflow-review-repair-rounds:fixture').set_value(3).run()
    ui.text_area(key=score).set_value('Custom scoring instructions.').run()
    ui.selectbox(key='workflow-node-prompt-selection:fixture:review').set_value('workflow.review_repair').run()
    ui.text_area(key=repair).set_value('Correct only the flagged facts.').run()
    ui.button(key='fixture-node:sft').click().run()
    ui.button(key='fixture-node:review').click().run()
    ui.selectbox(key='workflow-node-prompt-selection:fixture:review').set_value('workflow.review_repair').run()
    assert ui.text_area(key=repair).value == 'Correct only the flagged facts.'
    ui.segmented_control(key='workflow-review-repair-mode:fixture').set_value('human').run()
    assert ui.number_input(key='workflow-review-repair-rounds:fixture').disabled
    assert not any(w.key.startswith('node-model:fixture:review:') for w in ui.selectbox)
    ui.segmented_control(key='workflow-review-repair-mode:fixture').set_value('auto').run()
    assert ui.number_input(key='workflow-review-repair-threshold:fixture').value == 85
    assert ui.number_input(key='workflow-review-repair-rounds:fixture').value == 3
    ui.selectbox(key='workflow-node-prompt-selection:fixture:review').set_value('workflow.review_repair').run()
    assert ui.text_area(key=repair).value == 'Correct only the flagged facts.'
    ui.selectbox(key='workflow-node-prompt-selection:fixture:review').set_value('workflow.review_score').run()
    assert ui.text_area(key=score).value == 'Custom scoring instructions.'
    ui.toggle(key=enabled).set_value(False).run()
    assert not any(w.key.startswith('node-model:fixture:review:jev:') for w in ui.selectbox)
    assert ui.selectbox(key='node-model:fixture:review:generation:backend')
    ui.toggle(key=enabled).set_value(True).run()
    assert ui.text_area(key=score).value == 'Custom scoring instructions.'
    assert not ui.exception


def test_missing_separate_judge_blocks_auto_review_but_not_human_review(tmp_path):
    code, source = source_script(tmp_path)
    ui = AppTest.from_string(code).run()
    ui.pills(key='workflow-targets:fixture:自动推荐').set_value(['cpt', 'sft']).run()
    ui.multiselect(key='workflow-sources:fixture:文档资料').set_value([source]).run()
    confirm_source_review_models(ui)
    assert not ui.button(key='workflow-create:fixture').disabled
    bindings = deepcopy(ui.session_state['workflow-node-bindings:fixture'])
    bindings['review'].pop('jev', None)
    ui.session_state['workflow-node-bindings:fixture'] = bindings
    initialized = list(ui.session_state['workflow-node-bindings:fixture:initialized'])
    ui.session_state['workflow-node-bindings:fixture:initialized'] = list(dict.fromkeys([*initialized, 'review:jev']))
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(True).run()
    assert ui.button(key='workflow-create:fixture').disabled
    ui.segmented_control(key='workflow-review-repair-mode:fixture').set_value('human').run()
    assert not ui.button(key='workflow-create:fixture').disabled
    ui.segmented_control(key='workflow-review-repair-mode:fixture').set_value('auto').run()
    assert ui.button(key='workflow-create:fixture').disabled
    ui.selectbox(key='node-model:fixture:review:jev:backend').set_value('local').run()
    ui.selectbox(key='node-model:fixture:review:jev:model:local').set_value('judge').run()
    assert not ui.exception
    assert ui.number_input(key='node-model:fixture:review:jev:output:local:judge').value == 32768
    assert not ui.button(key='node-model-confirm:fixture:review').disabled
    ui.button(key='node-model-confirm:fixture:review').click().run()
    assert not ui.button(key='workflow-create:fixture').disabled


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


def test_historical_package_prompt_is_preserved_without_overwriting_new_review_contract():
    old_key = 'workflow-node-prompt:fixture:package:workflow.package_review'
    new_key = 'workflow-node-prompt:fixture:review:workflow.review_score'
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['workflow-form-draft:fixture'] = {old_key: 'Keep my historical review prompt.'}
    ui.run()
    ui.button(key='fixture-node:review').click().run()
    assert not ui.exception
    assert ui.text_area(key=new_key).value != 'Keep my historical review prompt.'
    assert ui.session_state['workflow-form-draft:fixture'][old_key] == 'Keep my historical review prompt.'
    ui.text_area(key=new_key).set_value('Updated scoring and correction criteria.').run()
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(True).run()
    ui.button(key='fixture-node:package').click().run()
    ui.button(key='fixture-node:review').click().run()
    assert ui.text_area(key=new_key).value == 'Updated scoring and correction criteria.'
    assert ui.session_state['workflow-form-draft:fixture'][old_key] == 'Keep my historical review prompt.'


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
    ui.button(key='fixture-node:review').click().run()
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(True).run()
    assert ui.toggle(key='workflow-package-review-enabled:fixture').label == 'Add JEV scoring'
    controls = [ui.segmented_control(key='workflow-review-repair-mode:fixture'),
                ui.number_input(key='workflow-review-repair-threshold:fixture'),
                ui.number_input(key='workflow-review-repair-rounds:fixture')]
    assert all(item.label and not re.search(r'[\u4e00-\u9fff]', item.label) for item in controls)
    assert not any(widget.key == 'fixture-node:jev' for widget in ui.button)
    assert not ui.exception


def test_submission_pins_unified_review_roles_policy_limits_and_custom_prompts(tmp_path):
    code = SCRIPT.replace('    def task_runs(self): return []', "    def create_run(self, **kwargs):\n        st.session_state['fixture-submitted'] = kwargs\n        return '0' * 32\n    def task_runs(self): return []")
    code, source = source_script(tmp_path, code)
    ui = AppTest.from_string(code).run()
    ui.pills(key='workflow-targets:fixture:自动推荐').set_value(['cpt', 'sft']).run()
    ui.multiselect(key='workflow-sources:fixture:文档资料').set_value([source]).run()
    ui.button(key='fixture-node:review').click().run()
    ui.toggle(key='workflow-package-review-enabled:fixture').set_value(True).run()
    ui.number_input(key='workflow-review-repair-threshold:fixture').set_value(85).run()
    ui.number_input(key='workflow-review-repair-rounds:fixture').set_value(3).run()
    ui.selectbox(key='node-model:fixture:review:jev:model:local').set_value('judge').run()
    ui.number_input(key='node-model:fixture:review:jev:context:local:judge').set_value(262144).run()
    ui.number_input(key='node-model:fixture:review:jev:output:local:judge').set_value(40000).run()
    ui.text_area(key='workflow-node-prompt:fixture:review:workflow.review_score').set_value('Custom score.').run()
    ui.selectbox(key='workflow-node-prompt-selection:fixture:review').set_value('workflow.review_repair').run()
    ui.text_area(key='workflow-node-prompt:fixture:review:workflow.review_repair').set_value('Custom correction.').run()
    confirm_source_review_models(ui)
    ui.button(key='workflow-create:fixture').click().run()
    assert not ui.exception
    saved = ui.session_state['fixture-submitted']
    assert saved['review_repair'] == {'mode': 'auto', 'max_rounds': 3, 'score_threshold': .85}
    assert saved['package_review']['enabled'] is True and saved['package_review']['mode'] == 'all'
    assert saved['node_models']['review']['jev'] == {'backend': 'local', 'model': 'judge',
        'context_window_tokens': 262144, 'max_output_tokens': 40000}
    assert saved['node_models']['review']['generation']['model'] == 'writer'
    assert not {'package', 'jev'}.intersection(saved['node_models'])
    assert saved['node_prompts']['review']['workflow.review_score'] == 'Custom score.'
    assert saved['node_prompts']['review']['workflow.review_repair'] == 'Custom correction.'


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
