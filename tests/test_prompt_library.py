"""Personal templates survive restarts without crossing users or editor scopes."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest

from lib.bootstrap.prompt_library import prompt_library_application
from lib.domain.prompt_library import validate_prompt_payload
from lib.infrastructure.prompt_library_sqlite import PromptLibrarySQLite


NODE_SCOPE = "node:sft:workflow.sft"
LITERAL = '  Keep {question}, {{braces}}, ${literal}, and "quotes".\r\n  第二行  \n'


def app(root, user="alice"):
    return prompt_library_application(root=root, user_key=user)


def test_restart_keeps_literal_payload_and_normalizes_only_name(tmp_path):
    saved = app(tmp_path).save_template(NODE_SCOPE, "  My template  ", {"text": LITERAL})
    restored = app(tmp_path).get_template(saved["id"], NODE_SCOPE)
    assert restored == saved
    assert restored["name"] == "My template"
    assert restored["payload"] == {"text": LITERAL}
    assert restored["revision"] == 1
    assert Path(tmp_path, "prompt-library.sqlite3").is_file()


def test_users_and_editor_scopes_are_isolated(tmp_path):
    alice = app(tmp_path)
    bob = app(tmp_path, "bob")
    saved = alice.save_template(NODE_SCOPE, "Common", {"text": "Alice's prompt"})
    assert bob.list_templates(NODE_SCOPE) == []
    assert alice.list_templates("node:cot:workflow.cot_generate") == []
    for library, scope in ((bob, NODE_SCOPE), (alice, "node:cot:workflow.cot_generate")):
        with pytest.raises(ValueError, match="^prompt_library_not_found$"):
            library.get_template(saved["id"], scope)
        with pytest.raises(ValueError, match="^prompt_library_not_found$"):
            library.update_template(saved["id"], scope, "Changed", {"text": "changed"}, 1)
    other = bob.save_template(NODE_SCOPE, "Common", {"text": "Bob's prompt"})
    assert bob.get_template(other["id"], NODE_SCOPE)["payload"]["text"] == "Bob's prompt"
    alice.save_template("node:cot:workflow.cot_generate", "Common", {"text": "CoT"})
    with sqlite3.connect(tmp_path / "prompt-library.sqlite3") as connection:
        namespaces = [row[0] for row in connection.execute("SELECT DISTINCT user_namespace FROM prompt_templates")]
    assert len(namespaces) == 2
    assert all(len(namespace) == 64 and namespace not in {"alice", "bob"} for namespace in namespaces)


def test_same_name_does_not_overwrite_and_update_uses_revision(tmp_path):
    library = app(tmp_path)
    first = library.save_template(NODE_SCOPE, "Rules", {"text": "first"})
    second = library.save_template(NODE_SCOPE, "Other", {"text": "other"})
    with pytest.raises(ValueError, match="^prompt_library_name_conflict$"):
        library.save_template(NODE_SCOPE, "  rULes  ", {"text": "discarded"})
    with pytest.raises(ValueError, match="^prompt_library_name_conflict$"):
        library.update_template(second["id"], NODE_SCOPE, "RULES", {"text": "discarded"}, 1)
    assert library.get_template(second["id"], NODE_SCOPE) == second
    updated = library.update_template(first["id"], NODE_SCOPE, " Rules ", {"text": LITERAL}, 1)
    assert updated["revision"] == 2
    assert updated["created_at"] == first["created_at"]
    assert updated["updated_at"] >= first["updated_at"]
    with pytest.raises(ValueError, match="^prompt_library_revision_conflict$"):
        app(tmp_path).update_template(first["id"], NODE_SCOPE, "Rules", {"text": "stale"}, 1)
    assert library.get_template(first["id"], NODE_SCOPE) == updated


def test_two_windows_cannot_lose_each_others_updates(tmp_path):
    initial = app(tmp_path).save_template(NODE_SCOPE, "Shared", {"text": "original"})

    def update(text):
        try:
            return app(tmp_path).update_template(initial["id"], NODE_SCOPE, "Shared", {"text": text}, 1)
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ("window A", "window B")))
    success = [result for result in results if isinstance(result, dict)]
    assert len(success) == 1
    assert "prompt_library_revision_conflict" in results
    assert app(tmp_path).get_template(initial["id"], NODE_SCOPE) == success[0]


def test_two_windows_cannot_save_duplicate_names(tmp_path):
    # Initialize schema before parallel operations; production schema creation
    # remains idempotent when the first connection happens concurrently.
    app(tmp_path).list_templates(NODE_SCOPE)

    def save(text):
        try:
            return app(tmp_path).save_template(NODE_SCOPE, "Shared", {"text": text})
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ("window A", "window B")))
    assert len([result for result in results if isinstance(result, dict)]) == 1
    assert "prompt_library_name_conflict" in results
    assert len(app(tmp_path).list_templates(NODE_SCOPE)) == 1


def test_list_only_returns_metadata_and_invalid_body_does_not_hide_names(tmp_path, monkeypatch):
    library = app(tmp_path)
    saved = library.save_template(NODE_SCOPE, "Recoverable name", {"text": LITERAL})
    connection_factory = sqlite3.connect
    with connection_factory(tmp_path / "prompt-library.sqlite3") as connection:
        connection.execute("UPDATE prompt_templates SET payload=? WHERE id=?", ("not JSON", saved["id"]))
    queries = []

    def recording_connect(*args, **kwargs):
        connection = connection_factory(*args, **kwargs)
        connection.set_trace_callback(queries.append)
        return connection

    monkeypatch.setattr("lib.infrastructure.prompt_library_sqlite.sqlite3.connect", recording_connect)
    metadata = library.list_templates(NODE_SCOPE)
    assert len(metadata) == 1
    assert set(metadata[0]) == {"id", "name", "scope", "created_at", "updated_at", "revision"}
    select_queries = [query for query in queries if query.startswith("SELECT ")]
    assert len(select_queries) == 1 and "payload" not in select_queries[0]
    with pytest.raises(ValueError, match="^prompt_library_invalid_record$"):
        library.get_template(saved["id"], NODE_SCOPE)


@pytest.mark.parametrize(("scope", "payload"), [
    ("director.rules", {"question_rules": "  literal {question}\n", "answer_rules": "  Answer\n"}),
    ("generation:sft", {"style": "custom", "instruction": "  {custom style}\r\n"}),
    ("generation:cot", {"style": "structured", "instruction": "  Keep derivations.\n"}),
    ("trim.rules", {"template": "custom", "instruction": "  extra\n", "custom_prompt": "  {trim}\r\n"}),
])
def test_supported_rule_templates_keep_literal_fields(tmp_path, scope, payload):
    assert validate_prompt_payload(scope, payload) == payload
    saved = app(tmp_path).save_template(scope, "Personal rules", payload)
    assert app(tmp_path).get_template(saved["id"], scope)["payload"] == payload


@pytest.mark.parametrize(("scope", "payload"), [
    (NODE_SCOPE, {"text": ""}),
    (NODE_SCOPE, {"text": "x" * 32769}),
    (NODE_SCOPE, {"text": "good", "api_key": "excluded"}),
    (NODE_SCOPE, {"text": "nul\x00"}),
    ("director.rules", {"question_rules": "x" * 12001, "answer_rules": "good"}),
    ("director.rules", {"question_rules": "good", "answer_rules": ""}),
    ("generation:sft", {"style": "unknown", "instruction": "good"}),
    ("generation:sft", {"style": "custom", "instruction": ""}),
    ("generation:sft", {"style": "default", "instruction": "x" * 4001}),
    ("trim.rules", {"template": "custom", "instruction": "", "custom_prompt": ""}),
    ("trim.rules", {"template": "leakage", "instruction": "", "custom_prompt": "x" * 16001}),
    ("trim.rules", {"template": "leakage", "instruction": "", "custom_prompt": "", "enabled": True}),
])
def test_invalid_payload_is_rejected_without_a_partial_template(tmp_path, scope, payload):
    with pytest.raises(ValueError, match="^prompt_library_invalid_payload$"):
        app(tmp_path).save_template(scope, "Invalid", payload)
    assert app(tmp_path).list_templates(scope) == []


@pytest.mark.parametrize("scope", ["unknown", "generation:agent", "node:sft:workflow.trim", "node:unknown:workflow.sft", "../escape"])
def test_unsupported_scopes_are_rejected_before_storage(tmp_path, scope):
    with pytest.raises(ValueError, match="^prompt_library_invalid_scope$"):
        app(tmp_path).save_template(scope, "Invalid", {"text": "test"})
    assert not (tmp_path / "prompt-library.sqlite3").exists()


@pytest.mark.parametrize("name", ["", "   ", "x" * 81, "line\nbreak", "nul\x00", "\ud800"])
def test_invalid_names_are_rejected(tmp_path, name):
    with pytest.raises(ValueError, match="^prompt_library_invalid_name$"):
        app(tmp_path).save_template(NODE_SCOPE, name, {"text": "test"})


@pytest.mark.parametrize("identifier", ["../escape", "0" * 31, "0" * 33, "G" * 32, "' OR 1=1 --"])
def test_identifiers_never_become_paths_or_sql(tmp_path, identifier):
    with pytest.raises(ValueError, match="^prompt_library_invalid_id$"):
        app(tmp_path).get_template(identifier, NODE_SCOPE)
    assert not (tmp_path / "prompt-library.sqlite3").exists()


@pytest.mark.parametrize("revision", [True, 0, -1, "1", None, 2**63])
def test_invalid_revision_does_not_change_a_template(tmp_path, revision):
    library = app(tmp_path)
    saved = library.save_template(NODE_SCOPE, "Rules", {"text": "original"})
    with pytest.raises(ValueError, match="^prompt_library_invalid_revision$"):
        library.update_template(saved["id"], NODE_SCOPE, "Rules", {"text": "bad"}, revision)
    assert library.get_template(saved["id"], NODE_SCOPE) == saved


def test_default_root_is_the_current_user_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    saved = prompt_library_application().save_template(NODE_SCOPE, "Home", {"text": "private"})
    assert (tmp_path / ".shujian-cube" / "prompt-library.sqlite3").is_file()
    assert prompt_library_application().get_template(saved["id"], NODE_SCOPE) == saved


def test_linked_database_is_rejected(tmp_path):
    target = tmp_path / "other.sqlite3"
    target.write_bytes(b"untouched")
    root = tmp_path / "library"
    root.mkdir()
    linked = root / "prompt-library.sqlite3"
    try:
        linked.hardlink_to(target)
    except OSError:
        pytest.skip("Hard links are unavailable on this filesystem")
    with pytest.raises(ValueError, match="^prompt_library_linked_path$"):
        app(root).list_templates(NODE_SCOPE)
    assert target.read_bytes() == b"untouched"


def test_linked_parent_directory_is_rejected(tmp_path):
    target = tmp_path / "real-library"
    target.mkdir()
    linked = tmp_path / "alias-library"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this filesystem")
    with pytest.raises(ValueError, match="^prompt_library_linked_path$"):
        app(linked).list_templates(NODE_SCOPE)
    assert list(target.iterdir()) == []


def test_busy_database_gives_a_clear_error_and_does_not_change_saved_content(tmp_path):
    library = app(tmp_path)
    saved = library.save_template(NODE_SCOPE, "Original", {"text": "saved"})
    with sqlite3.connect(tmp_path / "prompt-library.sqlite3", isolation_level=None) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(ValueError, match="^prompt_library_busy$"):
                library.update_template(saved["id"], NODE_SCOPE, "Original", {"text": "blocked"}, 1)
        finally:
            connection.execute("ROLLBACK")
    assert library.get_template(saved["id"], NODE_SCOPE) == saved


def test_unknown_database_version_is_preserved(tmp_path):
    path = tmp_path / "prompt-library.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=22")
    with pytest.raises(ValueError, match="^prompt_library_unsupported_version$"):
        app(tmp_path).list_templates(NODE_SCOPE)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 22


def test_sql_characters_in_names_and_text_remain_literal(tmp_path):
    name = "Robert'); DROP TABLE prompt_templates; --"
    text = json.dumps({"example": name, "rule": "${do_not_execute}"})
    saved = app(tmp_path).save_template(NODE_SCOPE, name, {"text": text})
    assert app(tmp_path).get_template(saved["id"], NODE_SCOPE)["payload"]["text"] == text
    assert len(app(tmp_path).list_templates(NODE_SCOPE)) == 1


def test_empty_or_invalid_user_namespace_is_rejected(tmp_path):
    for user in ("", " ", None, "\ud800"):
        if user is None:
            continue  # None intentionally selects the current OS user.
        with pytest.raises(ValueError, match="^prompt_library_invalid_user$"):
            PromptLibrarySQLite(root=tmp_path, user_key=user)
