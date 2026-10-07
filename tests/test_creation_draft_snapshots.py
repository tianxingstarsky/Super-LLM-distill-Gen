"""Multiple unfinished jobs survive restarts without sharing mutable form state."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.infrastructure import creation_draft_file as storage


def test_named_drafts_survive_restart_and_restore_replaces_all_current_values(tmp_path):
    first = {'workflow-name:default': '文档任务', 'workflow-source-mode:default': '文档资料',
             'workflow-count:default': 50000, 'workflow-targets:default:自动推荐': ['cpt'],
             'workflow-sources:default:文档资料': ['/cached/manual.md']}
    application = creation_draft_application(tmp_path)
    application.update(first)
    document_id = application.save_snapshot()
    application.update({'workflow-name:default': 'Agent任务',
                        'workflow-source-mode:default': 'Agent 上下文',
                        'workflow-turns:default': 4})
    second = application.load()
    agent_id = application.save_snapshot('Agent研究备份')
    application.update({'workflow-open-brief:default': 'Later unsaved changes'})

    restarted = creation_draft_application(tmp_path)
    metadata = restarted.snapshots()
    assert [row['id'] for row in metadata] == [agent_id, document_id]
    assert [row['name'] for row in metadata] == ['Agent研究备份', '文档任务']
    assert [row['source_mode'] for row in metadata] == ['Agent 上下文', '文档资料']
    assert all(datetime.fromisoformat(row['created_at']).tzinfo is not None for row in metadata)
    assert all('values' not in row for row in metadata)
    assert restarted.restore_snapshot(document_id) == first
    assert creation_draft_application(tmp_path).load() == first
    assert 'workflow-turns:default' not in restarted.load()
    assert 'workflow-open-brief:default' not in restarted.load()
    assert restarted.restore_snapshot(agent_id) == second
    assert creation_draft_application(tmp_path).load() == second
    assert [row['id'] for row in restarted.snapshots()] == [agent_id, document_id]


def test_snapshot_is_an_independent_copy_and_restoring_does_not_change_it(tmp_path):
    application = creation_draft_application(tmp_path)
    application.update({'workflow-targets:w:p': ['sft']})
    identifier = application.save_snapshot()
    before = (tmp_path / '.creation-drafts' / (identifier + '.json')).read_bytes()
    restored = application.restore_snapshot(identifier)
    restored['workflow-targets:w:p'].append('dpo')
    assert application.load() == {'workflow-targets:w:p': ['sft']}
    assert application.restore_snapshot(identifier) == {'workflow-targets:w:p': ['sft']}
    assert (tmp_path / '.creation-drafts' / (identifier + '.json')).read_bytes() == before


def test_snapshots_with_identical_names_are_independent_and_pagination_loses_none(tmp_path):
    application = creation_draft_application(tmp_path)
    expected = []
    for count in range(1, 8):
        application.update({'workflow-count:default': count})
        expected.append(application.save_snapshot('Same name'))
    fresh = creation_draft_application(tmp_path)
    first, second, third = fresh.snapshots(3, 0), fresh.snapshots(3, 3), fresh.snapshots(3, 6)
    assert [row['id'] for row in first + second + third] == expected[::-1]
    assert fresh.snapshots(3, 7) == []
    assert len(list((tmp_path / '.creation-drafts').glob('*.json'))) == 7
    for index, identifier in enumerate(expected, start=1):
        assert fresh.restore_snapshot(identifier) == {'workflow-count:default': index}


def test_separate_output_roots_cannot_list_or_restore_each_others_snapshots(tmp_path):
    one = creation_draft_application(tmp_path / 'one')
    two = creation_draft_application(tmp_path / 'two')
    one.update({'workflow-name:default': 'First'})
    identifier = one.save_snapshot()
    assert two.snapshots() == []
    with pytest.raises(ValueError):
        two.restore_snapshot(identifier)
    assert two.load() == {}
    assert one.load() == {'workflow-name:default': 'First'}


@pytest.mark.parametrize('identifier', ['../escape', '..\\escape', '/absolute', 'C:\\escape',
                                        'a' * 32 + '.json', 'A' * 32, '', None, 123])
def test_restore_rejects_invalid_identifiers_before_making_directories(tmp_path, identifier):
    output = tmp_path / 'not-yet-created'
    with pytest.raises(ValueError, match='invalid_creation_draft_id'):
        creation_draft_application(output).restore_snapshot(identifier)
    assert not output.exists()


@pytest.mark.parametrize('limit,offset', [(0, 0), (501, 0), (True, 0), (1, -1), (1, True)])
def test_invalid_pagination_is_rejected(tmp_path, limit, offset):
    with pytest.raises(ValueError, match='invalid_creation_draft_page'):
        creation_draft_application(tmp_path).snapshots(limit, offset)


@pytest.mark.parametrize('name', [123, True, 'x' * 101])
def test_invalid_snapshot_name_does_not_write_anything(tmp_path, name):
    with pytest.raises(ValueError, match='invalid_creation_draft'):
        creation_draft_application(tmp_path).save_snapshot(name)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('field,value', [('api-key:default', 'secret'),
                                        ('workflow-node-model:default', {'api_key': 'secret'}),
                                        ('workflow-web-research-enabled:default', True)])
def test_snapshots_reject_credentials_or_prior_network_authorization_on_disk(tmp_path, field, value):
    current = tmp_path / '.creation-draft.json'
    current.write_text(json.dumps({'version': 1, 'values': {field: value}}), encoding='utf-8')
    with pytest.raises(ValueError, match='invalid_creation_draft'):
        creation_draft_application(tmp_path).save_snapshot()
    assert not (tmp_path / '.creation-drafts').exists()
    assert json.loads(current.read_text(encoding='utf-8'))['values'] == {field: value}


def test_corrupt_snapshot_does_not_replace_current_work(tmp_path):
    application = creation_draft_application(tmp_path)
    application.update({'workflow-name:default': 'Saved'})
    identifier = application.save_snapshot()
    application.update({'workflow-name:default': 'Current'})
    snapshot = tmp_path / '.creation-drafts' / (identifier + '.json')
    snapshot.write_text('broken JSON', encoding='utf-8')
    with pytest.raises(ValueError):
        application.restore_snapshot(identifier)
    assert creation_draft_application(tmp_path).load() == {'workflow-name:default': 'Current'}
    assert snapshot.read_text(encoding='utf-8') == 'broken JSON'


def test_snapshot_size_is_bounded_on_save_and_restore(tmp_path, monkeypatch):
    application = creation_draft_application(tmp_path)
    application.update({'workflow-open-brief:default': 'Document content ' * 30})
    identifier = application.save_snapshot()
    snapshot = tmp_path / '.creation-drafts' / (identifier + '.json')
    original = snapshot.read_bytes()
    current = (tmp_path / '.creation-draft.json').read_bytes()
    monkeypatch.setattr(storage, 'MAX_DRAFT_BYTES', len(original) - 1)
    with pytest.raises(ValueError, match='invalid_creation_draft'):
        application.save_snapshot()
    assert list((tmp_path / '.creation-drafts').glob('*.json')) == [snapshot]
    with pytest.raises(ValueError, match='invalid_creation_draft'):
        application.restore_snapshot(identifier)
    assert snapshot.read_bytes() == original
    assert (tmp_path / '.creation-draft.json').read_bytes() == current


def test_id_collision_never_overwrites_existing_snapshot(tmp_path, monkeypatch):
    application = creation_draft_application(tmp_path)
    application.update({'workflow-name:default': 'First'})
    identifier = application.save_snapshot()
    original = (tmp_path / '.creation-drafts' / (identifier + '.json')).read_bytes()
    monkeypatch.setattr(storage.uuid, 'uuid4', lambda: SimpleNamespace(hex=identifier))
    with pytest.raises(ValueError, match='creation_draft_id_collision'):
        application.save_snapshot('Second')
    assert (tmp_path / '.creation-drafts' / (identifier + '.json')).read_bytes() == original


@pytest.mark.parametrize('linked_at', ['output', 'current', 'lock', 'snapshots', 'snapshot'])
def test_links_cannot_redirect_snapshot_read_or_write(tmp_path, linked_at):
    output = tmp_path / 'output'
    outside = tmp_path / 'outside'
    outside.mkdir()
    output.mkdir()
    application = creation_draft_application(output)
    application.update({'workflow-name:default': 'Inside'})
    identifier = application.save_snapshot()
    source = output / '.creation-drafts' / (identifier + '.json')
    external_file = outside / 'protected.json'
    external_file.write_bytes(source.read_bytes())
    link = {'output': output, 'current': output / '.creation-draft.json',
            'lock': output / '.creation-draft.json.lock', 'snapshots': output / '.creation-drafts',
            'snapshot': source}[linked_at]
    if link.is_dir():
        link.rename(tmp_path / ('original-' + linked_at))
    else:
        link.unlink(missing_ok=True)
    is_directory = linked_at in {'output', 'snapshots'}
    try:
        link.symlink_to(outside if is_directory else external_file, target_is_directory=is_directory)
    except OSError as error:
        pytest.skip(f'Host cannot create symbolic links: {error}')
    before = external_file.read_bytes()
    operation = (lambda: application.restore_snapshot(identifier)) if linked_at == 'snapshot' else application.save_snapshot
    with pytest.raises(ValueError, match='linked_creation_draft_path'):
        operation()
    assert external_file.read_bytes() == before
    assert list(outside.iterdir()) == [external_file]


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction protection')
def test_snapshot_junction_cannot_escape_output(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    junction = output / '.creation-drafts'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
    if result.returncode:
        pytest.skip('Host cannot create Windows junctions')
    try:
        with pytest.raises(ValueError, match='linked_creation_draft_path'):
            creation_draft_application(output).save_snapshot()
        assert list(outside.iterdir()) == []
    finally:
        os.rmdir(junction)


def test_parallel_snapshot_saves_create_distinct_durable_drafts(tmp_path):
    creation_draft_application(tmp_path).update({'workflow-name:default': 'Shared current form'})
    with ThreadPoolExecutor(max_workers=6) as pool:
        identifiers = list(pool.map(lambda index: creation_draft_application(tmp_path).save_snapshot(f'Copy {index}'), range(12)))
    assert len(set(identifiers)) == 12
    metadata = creation_draft_application(tmp_path).snapshots()
    assert {row['id'] for row in metadata} == set(identifiers)
    for identifier in identifiers:
        assert creation_draft_application(tmp_path).restore_snapshot(identifier) == {'workflow-name:default': 'Shared current form'}


def test_linked_snapshot_cannot_be_listed_or_restored(tmp_path):
    application = creation_draft_application(tmp_path)
    identifier = application.save_snapshot('Original')
    snapshot = tmp_path / '.creation-drafts' / (identifier + '.json')
    external = tmp_path / 'external.json'
    snapshot.rename(external)
    os.link(external, snapshot)
    with pytest.raises(ValueError, match='linked_creation_draft_path'):
        application.snapshots()
    with pytest.raises(ValueError, match='linked_creation_draft_path'):
        application.restore_snapshot(identifier)
    assert not (tmp_path / '.creation-draft.json').exists()


@pytest.mark.parametrize('changes', [{'id': 'a' * 32}, {'created_at': 'yesterday'},
                                    {'created_at': '2026-10-07T12:00:00'},
                                    {'values': {'api-key:default': 'secret'}}])
def test_tampered_snapshot_document_is_rejected_without_replacing_current(tmp_path, changes):
    application = creation_draft_application(tmp_path)
    application.update({'workflow-name:default': 'Current'})
    identifier = application.save_snapshot()
    snapshot = tmp_path / '.creation-drafts' / (identifier + '.json')
    document = json.loads(snapshot.read_text(encoding='utf-8'))
    document.update(changes)
    snapshot.write_text(json.dumps(document), encoding='utf-8')
    before = (tmp_path / '.creation-draft.json').read_bytes()
    with pytest.raises(ValueError, match='invalid_creation_draft'):
        application.restore_snapshot(identifier)
    assert (tmp_path / '.creation-draft.json').read_bytes() == before


def test_interrupted_atomic_write_keeps_current_and_existing_snapshot(tmp_path, monkeypatch):
    from lib import io_utils

    application = creation_draft_application(tmp_path)
    application.update({'workflow-name:default': 'Snapshot'})
    identifier = application.save_snapshot()
    application.update({'workflow-name:default': 'Current'})
    current = (tmp_path / '.creation-draft.json').read_bytes()
    snapshot = tmp_path / '.creation-drafts' / (identifier + '.json')
    original = snapshot.read_bytes()

    def interrupted(*args):
        raise OSError('storage disconnected')

    monkeypatch.setattr(io_utils, '_replace_state', interrupted)
    with pytest.raises(OSError, match='storage disconnected'):
        application.restore_snapshot(identifier)
    with pytest.raises(OSError, match='storage disconnected'):
        application.save_snapshot('New snapshot')
    assert (tmp_path / '.creation-draft.json').read_bytes() == current
    assert snapshot.read_bytes() == original
    assert list((tmp_path / '.creation-drafts').glob('*.json')) == [snapshot]
    assert not list(tmp_path.rglob('.pending-*.json'))


def test_concurrent_lock_cleanup_after_path_observation_does_not_reject_draft(tmp_path, monkeypatch):
    lock = tmp_path / '.creation-draft.json.lock'
    lock.write_bytes(b'')
    original_stat = Path.stat
    observations = 0
    removed = False

    def concurrent_cleanup(path, *args, **kwargs):
        nonlocal observations, removed
        info = original_stat(path, *args, **kwargs)
        if path == lock:
            observations += 1
            if observations == 2:
                lock.unlink()
                removed = True
        return info

    monkeypatch.setattr(Path, 'stat', concurrent_cleanup)
    application = creation_draft_application(tmp_path)
    application.update({'workflow-name:default': 'Persistent draft'})
    assert removed
    assert application.load() == {'workflow-name:default': 'Persistent draft'}
