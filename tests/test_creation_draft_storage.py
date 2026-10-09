"""Durable drafts remain bounded, isolated and safe to merge across sessions."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.domain.creation_draft import validate_creation_draft
from lib.infrastructure.creation_draft_file import CreationDraftFile
from tests.test_workflow_draft import SCRIPT


@pytest.mark.parametrize('key,value', [
    ('workflow-count:w', True), ('workflow-count:w', 100001),
    ('workflow-concurrency:w', 17), ('workflow-turns:w', 1),
    ('workflow-targets:w:p', ['unknown']), ('workflow-sources:w:p', [123]),
    ('api-key:w', 'credential'), ('workflow-node-model:w', {'model': 'writer'}),
    ('workflow-web-research-enabled:w', True),
    ('workflow-web-research-more:w', 'x' * 701),
    ('workflow-open-brief:w', 'x' * 20001), ('workflow-name', 'No workspace'),
    ('workflow-sft-output-style:w', 'DROP'), ('workflow-sft-output-style:w', True),
    ('workflow-sft-output-style:w', None), ('workflow-sft-output-style:w', ['drop']),
    ('workflow-generation-style:w:sft', 'unknown'), ('workflow-generation-style:w:trim', 'concise'),
    ('workflow-generation-style:w', 'concise'), ('workflow-generation-style::sft', 'concise'),
    ('workflow-generation-enabled:w:cot', 1), ('workflow-generation-instruction:w:sft', 'x' * 4001),
    ('workflow-trim-enabled:w', 'true'), ('workflow-trim-template:w', 'unknown'),
    ('workflow-trim-instruction:w', 'x' * 4001), ('workflow-trim-prompt:w', 'x' * 16001),
])
def test_draft_rejects_invalid_or_non_form_values(key, value):
    with pytest.raises(ValueError, match='invalid_creation_draft'):
        validate_creation_draft({key: value})


def test_validation_returns_independent_values():
    original = {'workflow-targets:w:p': ['sft']}
    result = validate_creation_draft(original)
    result['workflow-targets:w:p'].append('dpo')
    assert original['workflow-targets:w:p'] == ['sft']


def test_generation_and_trim_drafts_preserve_incomplete_custom_edits_across_restart(tmp_path):
    values = {
        'workflow-generation-enabled:w:sft': False,
        'workflow-generation-style:w:sft': 'custom',
        'workflow-generation-instruction:w:sft': '',
        'workflow-generation-enabled:w:cot': True,
        'workflow-generation-style:w:cot': 'structured',
        'workflow-generation-instruction:w:cot': 'Keep task evidence.',
        'workflow-trim-enabled:w': True,
        'workflow-trim-template:w': 'custom',
        'workflow-trim-instruction:w': '',
        'workflow-trim-prompt:w': '',
    }
    application = creation_draft_application(tmp_path)
    application.replace(values)
    identifier = application.save_snapshot()
    assert creation_draft_application(tmp_path).load() == values
    application.replace({'workflow-name:w': 'Other form'})
    assert creation_draft_application(tmp_path).restore_snapshot(identifier) == values


def test_fresh_instance_restores_values_without_rewriting_unchanged_draft(tmp_path):
    app = creation_draft_application(tmp_path)
    values = {'workflow-count:w': 50000, 'workflow-open-brief:w': '设备维护对话'}
    app.update(values)
    store = CreationDraftFile(tmp_path)
    before = store.path.stat().st_mtime_ns
    creation_draft_application(tmp_path).update(values)
    assert store.path.stat().st_mtime_ns == before
    assert creation_draft_application(tmp_path).load() == values
    assert creation_draft_application(tmp_path / 'other').load() == {}


def test_parallel_sessions_merge_independent_fields(tmp_path):
    patches = [{'workflow-count:w': 50000}, {'workflow-name:w': 'Bulk run'},
               {'workflow-batch-size:w': 250}, {'workflow-open-brief:w': 'Brief'}]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda patch: creation_draft_application(tmp_path).update(patch), patches))
    assert creation_draft_application(tmp_path).load() == {k: v for p in patches for k, v in p.items()}


@pytest.mark.parametrize('content', ['broken JSON', '{"version":true,"values":{}}',
                                  '{"version":1,"values":{"workflow-count:w":false}}'])
def test_corrupt_draft_is_preserved(tmp_path, content):
    store = CreationDraftFile(tmp_path)
    store.path.write_text(content, encoding='utf-8')
    with pytest.raises(ValueError):
        store.update({'workflow-count:w': 50000})
    assert store.path.read_text(encoding='utf-8') == content


def test_oversized_draft_is_not_loaded_or_written(tmp_path, monkeypatch):
    from lib.infrastructure import creation_draft_file as module
    monkeypatch.setattr(module, 'MAX_DRAFT_BYTES', 128)
    store = CreationDraftFile(tmp_path)
    with pytest.raises(ValueError):
        store.update({'workflow-open-brief:w': '文本' * 100})
    assert not store.path.exists()
    store.path.write_bytes(b' ' * 129)
    with pytest.raises(ValueError):
        store.load()


def persistent_script(tmp_path):
    injected = ('from pathlib import Path\n'
                'from lib.bootstrap.creation_drafts import creation_draft_application\n'
                f'draft_app = creation_draft_application(Path({str(tmp_path)!r}))\n')
    code = SCRIPT.replace('import streamlit as st', 'import streamlit as st\n' + injected)
    code = code.replace('WorkflowNodeModelsApplication(Inventory()))',
                        'WorkflowNodeModelsApplication(Inventory()), draft_application=draft_app)')
    return code.encode('ascii', 'backslashreplace').decode('ascii')


def test_new_ui_session_restores_bulk_settings_goals_and_cleared_brief(tmp_path):
    script = persistent_script(tmp_path)
    ui = AppTest.from_string(script).run()
    ui.number_input(key='workflow-count:fixture').set_value(50000).run()
    ui.number_input(key='workflow-batch-size:fixture').set_value(250).run()
    ui.pills(key='workflow-targets:fixture:自动推荐').set_value(['orpo', 'rlaif']).run()
    ui.multiselect(key='workflow-sources:fixture:文档资料').set_value(['fixture.txt']).run()
    ui.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    ui.text_area(key='workflow-open-brief:fixture').set_value('设备维护对话').run()
    fresh = AppTest.from_string(script).run()
    assert not fresh.exception
    assert fresh.number_input(key='workflow-count:fixture').value == 50000
    assert fresh.number_input(key='workflow-batch-size:fixture').value == 250
    assert fresh.pills(key='workflow-targets:fixture:自动推荐').value == ['orpo', 'rlaif']
    assert fresh.text_area(key='workflow-open-brief:fixture').value == '设备维护对话'
    fresh.text_area(key='workflow-open-brief:fixture').set_value('').run()
    again = AppTest.from_string(script).run()
    assert again.text_area(key='workflow-open-brief:fixture').value == ''
    again.session_state['fixture-remove-source'] = True
    again.segmented_control(key='workflow-source-mode:fixture').set_value('文档资料').run()
    assert again.session_state['workflow-sources:fixture:文档资料'] == []
    assert not any(item.key == 'workflow-sources:fixture:文档资料' for item in again.multiselect)
    assert not again.exception


def test_public_query_restores_but_new_session_requires_fresh_web_consent(tmp_path):
    script = persistent_script(tmp_path)
    ui = AppTest.from_string(script).run()
    ui.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    ui.checkbox(key='workflow-web-research-enabled:fixture').check().run()
    ui.text_input(key='workflow-web-research-query:fixture').set_value('设备维护安全规范').run()
    ui.number_input(key='workflow-web-research-count:fixture').set_value(4).run()
    ui.text_area(key='workflow-web-research-more:fixture').set_value('设备检修风险\n维护记录质量规范').run()
    fresh = AppTest.from_string(script).run()
    assert not fresh.exception
    assert fresh.checkbox(key='workflow-web-research-enabled:fixture').value is False
    assert 'workflow-web-research-enabled:fixture' not in creation_draft_application(tmp_path).load()
    fresh.checkbox(key='workflow-web-research-enabled:fixture').check().run()
    assert fresh.text_input(key='workflow-web-research-query:fixture').value == '设备维护安全规范'
    assert fresh.number_input(key='workflow-web-research-count:fixture').value == 4
    assert fresh.text_area(key='workflow-web-research-more:fixture').value == '设备检修风险\n维护记录质量规范'
    assert creation_draft_application(tmp_path / 'other').load() == {}


def test_direct_target_entry_survives_restart_without_replaying_the_shortcut(tmp_path):
    application = creation_draft_application(tmp_path)
    application.replace({
        'workflow-name:fixture': 'Existing 50K source task',
        'workflow-count:fixture': 50000,
        'workflow-targets:fixture:预训练语料': [],
        'workflow-source-mode:fixture': 'Agent 上下文',
        'workflow-sources:fixture:文档资料': ['fixture.txt'],
    })
    script = persistent_script(tmp_path)
    ui = AppTest.from_string(script)
    ui.session_state['workflow-entry-target:fixture'] = 'cpt'
    ui.run()
    assert not ui.exception
    assert ui.pills(key='workflow-targets:fixture:自选目标').value == ['cpt']
    assert 'workflow-entry-target:fixture' not in ui.session_state
    stored = application.load()
    assert stored['workflow-preset:fixture'] == '自选目标'
    assert stored['workflow-targets:fixture:自选目标'] == ['cpt']
    assert not any('workflow-entry-target:' in key for key in stored)

    fresh = AppTest.from_string(script).run()
    assert not fresh.exception
    assert fresh.pills(key='workflow-targets:fixture:自选目标').value == ['cpt']
    assert fresh.text_input(key='workflow-name:fixture').value == 'Existing 50K source task'
    assert fresh.number_input(key='workflow-count:fixture').value == 50000
    assert fresh.multiselect(key='workflow-sources:fixture:文档资料').value == ['fixture.txt']
    fresh.pills(key='workflow-targets:fixture:自选目标').set_value(['dpo']).run()
    again = AppTest.from_string(script).run()
    assert not again.exception
    assert again.pills(key='workflow-targets:fixture:自选目标').value == ['dpo']


def test_bad_disk_draft_shows_warning_and_keeps_session_changes(tmp_path):
    path = tmp_path / '.creation-draft.json'
    path.write_text('broken JSON', encoding='utf-8')
    ui = AppTest.from_string(persistent_script(tmp_path)).run()
    ui.number_input(key='workflow-count:fixture').set_value(50000).run()
    assert not ui.exception
    assert ui.number_input(key='workflow-count:fixture').value == 50000
    assert any('草稿未能' in warning.value for warning in ui.warning)
    assert path.read_text(encoding='utf-8') == 'broken JSON'


def test_named_drafts_keep_sft_style_and_exact_session_values_across_restarts(tmp_path):
    script = persistent_script(tmp_path)
    first = AppTest.from_string(script).run()
    first.text_input(key='workflow-name:fixture').set_value('First task').run()
    first.number_input(key='workflow-count:fixture').set_value(50000).run()
    first.selectbox(key='workflow-sft-output-style:fixture').set_value('drop').run()
    first.button(key='workflow-save-draft:fixture').click().run()
    application = creation_draft_application(tmp_path)
    first_id = application.snapshots()[0]['id']

    second = AppTest.from_string(script).run()
    assert second.selectbox(key='workflow-sft-output-style:fixture').value == 'drop'
    second.text_input(key='workflow-name:fixture').set_value('Second task').run()
    second.number_input(key='workflow-count:fixture').set_value(1000).run()
    second.selectbox(key='workflow-sft-output-style:fixture').set_value('separated').run()
    second.button(key='workflow-save-draft:fixture').click().run()
    second_id = application.snapshots()[0]['id']

    # Saving the still-open first session must snapshot that session, even
    # after another browser session has changed the shared automatic draft.
    first.button(key='workflow-save-draft:fixture').click().run()
    first_copy = application.snapshots()[0]['id']
    assert len({first_id, second_id, first_copy}) == 3
    for identifier, name, count, style in (
            (first_id, 'First task', 50000, 'drop'),
            (second_id, 'Second task', 1000, 'separated'),
            (first_copy, 'First task', 50000, 'drop')):
        restored = creation_draft_application(tmp_path).restore_snapshot(identifier)
        fresh = AppTest.from_string(script).run()
        assert not fresh.exception
        assert fresh.text_input(key='workflow-name:fixture').value == name
        assert fresh.number_input(key='workflow-count:fixture').value == count
        assert fresh.selectbox(key='workflow-sft-output-style:fixture').value == style
        assert restored['workflow-sft-output-style:fixture'] == style
        assert not any('node-model' in key or 'consent' in key or 'api-key' in key for key in restored)
    assert not first.exception and not second.exception


def test_failed_autosave_edit_is_retried_before_clearing_its_warning(tmp_path, monkeypatch):
    from lib.application.creation_draft_service import CreationDraftApplication

    ui = AppTest.from_string(persistent_script(tmp_path)).run()
    original = CreationDraftApplication.replace
    attempts = []

    def fail_once(self, values):
        if values.get('workflow-count:fixture') == 50000:
            attempts.append(values.copy())
            if len(attempts) == 1:
                raise OSError('Controlled transient autosave failure')
        return original(self, values)

    monkeypatch.setattr(CreationDraftApplication, 'replace', fail_once)
    ui.number_input(key='workflow-count:fixture').set_value(50000).run()
    assert not ui.exception
    assert len(attempts) >= 2
    assert creation_draft_application(tmp_path).load()['workflow-count:fixture'] == 50000
    assert 'workflow-draft-error:fixture' not in ui.session_state


def test_complete_autosave_replaces_other_fields_but_incremental_update_still_merges(tmp_path):
    application = creation_draft_application(tmp_path)
    application.update({'workflow-name:fixture': 'Other task',
                        'workflow-open-brief:fixture': 'Other requirements',
                        'workflow-sft-output-style:fixture': 'drop'})
    current = {'workflow-name:fixture': 'Current task', 'workflow-count:fixture': 50000}
    application.replace(current)
    fresh = creation_draft_application(tmp_path)
    assert fresh.load() == current
    fresh.update({'workflow-batch-size:fixture': 250})
    assert fresh.load() == {**current, 'workflow-batch-size:fixture': 250}


def test_complete_autosave_rejects_secrets_and_keeps_unreadable_draft(tmp_path):
    application = creation_draft_application(tmp_path)
    original = {'workflow-name:fixture': 'Recoverable task'}
    application.replace(original)
    path = tmp_path / '.creation-draft.json'
    before = path.read_bytes()
    with pytest.raises(ValueError, match='invalid_creation_draft'):
        application.replace({'api-key:fixture': 'Never save credentials'})
    assert path.read_bytes() == before
    path.write_text('Broken draft', encoding='utf-8')
    with pytest.raises(ValueError):
        application.replace(original)
    assert path.read_text(encoding='utf-8') == 'Broken draft'


def test_two_sessions_do_not_mix_sft_default_or_absent_brief_after_autosave_and_restart(tmp_path):
    script = persistent_script(tmp_path)
    mine = AppTest.from_string(script).run()
    mine.text_input(key='workflow-name:fixture').set_value('My task').run()
    assert mine.selectbox(key='workflow-sft-output-style:fixture').value == 'separated'
    # An old/default form need not contain the newly supported optional field.
    assert 'workflow-sft-output-style:fixture' not in mine.session_state['workflow-form-draft:fixture']

    other = AppTest.from_string(script).run()
    other.text_input(key='workflow-name:fixture').set_value('Other task').run()
    other.selectbox(key='workflow-sft-output-style:fixture').set_value('drop').run()
    other.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    other.text_area(key='workflow-open-brief:fixture').set_value('Other task requirements').run()

    mine.number_input(key='workflow-count:fixture').set_value(50000).run()
    assert not mine.exception and not other.exception
    expected = mine.session_state['workflow-form-draft:fixture']
    assert creation_draft_application(tmp_path).load() == expected
    assert 'workflow-sft-output-style:fixture' not in expected
    assert 'workflow-open-brief:fixture' not in expected
    fresh = AppTest.from_string(script).run()
    assert not fresh.exception
    assert fresh.text_input(key='workflow-name:fixture').value == 'My task'
    assert fresh.number_input(key='workflow-count:fixture').value == 50000
    assert fresh.selectbox(key='workflow-sft-output-style:fixture').value == 'separated'
    fresh.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    assert fresh.text_area(key='workflow-open-brief:fixture').value == ''
    # The other session still owns its separate in-memory form.
    assert other.session_state['workflow-form-draft:fixture']['workflow-sft-output-style:fixture'] == 'drop'
