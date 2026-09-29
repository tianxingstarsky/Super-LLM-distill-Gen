"""Durable drafts remain bounded, isolated and safe to merge across sessions."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.domain.creation_draft import validate_creation_draft
from lib.infrastructure.creation_draft_file import CreationDraftFile
from test_workflow_draft import SCRIPT, restore_canvas_renderer  # noqa: F401


@pytest.mark.parametrize('key,value', [
    ('workflow-count:w', True), ('workflow-count:w', 100001),
    ('workflow-concurrency:w', 17), ('workflow-turns:w', 1),
    ('workflow-targets:w:p', ['unknown']), ('workflow-sources:w:p', [123]),
    ('api-key:w', 'credential'), ('workflow-node-model:w', {'model': 'writer'}),
    ('workflow-open-brief:w', 'x' * 20001), ('workflow-name', 'No workspace'),
])
def test_draft_rejects_invalid_or_non_form_values(key, value):
    with pytest.raises(ValueError, match='invalid_creation_draft'):
        validate_creation_draft({key: value})


def test_validation_returns_independent_values():
    original = {'workflow-targets:w:p': ['sft']}
    result = validate_creation_draft(original)
    result['workflow-targets:w:p'].append('dpo')
    assert original['workflow-targets:w:p'] == ['sft']


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
    ui.multiselect(key='workflow-targets:fixture:自动推荐').set_value(['orpo', 'rlaif']).run()
    ui.multiselect(key='workflow-sources:fixture:文档资料').set_value(['fixture.txt']).run()
    ui.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    ui.text_area(key='workflow-open-brief:fixture').set_value('设备维护对话').run()
    fresh = AppTest.from_string(script).run()
    assert not fresh.exception
    assert fresh.number_input(key='workflow-count:fixture').value == 50000
    assert fresh.number_input(key='workflow-batch-size:fixture').value == 250
    assert fresh.multiselect(key='workflow-targets:fixture:自动推荐').value == ['orpo', 'rlaif']
    assert fresh.text_area(key='workflow-open-brief:fixture').value == '设备维护对话'
    fresh.text_area(key='workflow-open-brief:fixture').set_value('').run()
    again = AppTest.from_string(script).run()
    assert again.text_area(key='workflow-open-brief:fixture').value == ''
    again.session_state['fixture-remove-source'] = True
    again.segmented_control(key='workflow-source-mode:fixture').set_value('文档资料').run()
    assert again.multiselect(key='workflow-sources:fixture:文档资料').value == []
    assert not again.exception


def test_bad_disk_draft_shows_warning_and_keeps_session_changes(tmp_path):
    path = tmp_path / '.creation-draft.json'
    path.write_text('broken JSON', encoding='utf-8')
    ui = AppTest.from_string(persistent_script(tmp_path)).run()
    ui.number_input(key='workflow-count:fixture').set_value(50000).run()
    assert not ui.exception
    assert ui.number_input(key='workflow-count:fixture').value == 50000
    assert any('草稿未能' in warning.value for warning in ui.warning)
    assert path.read_text(encoding='utf-8') == 'broken JSON'
