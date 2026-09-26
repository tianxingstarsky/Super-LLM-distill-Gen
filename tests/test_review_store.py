"""Legacy migration, transactional heads and deferred archive integrity."""
import json
import io
import sqlite3
import zipfile

import pytest

from test_review_pagination import fixture_app
from lib.domain.sft_review import sft_review_record
from lib.infrastructure.review_store import review_store
from lib.infrastructure.audit_stream import JSONStream


@pytest.mark.parametrize("target", ["cpt", "sft", "dpo", "orpo"])
def test_migration_preserves_legacy_and_release_has_verified_download(tmp_path, target):
    app, run_id, _ = fixture_app(tmp_path, target, count=3)
    audit = tmp_path / "workflows" / run_id / "human-review"
    legacy = next(audit.glob("*.json"))
    original = legacy.read_bytes()
    assert app.queue(run_id)["counts"]["approved"] == 3
    assert legacy.read_bytes() == original
    with sqlite3.connect(legacy.with_suffix(".sqlite")) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    release = app.prepare_release(run_id)
    with app.release_archive(run_id, release["id"], release["sha256"]) as handle:
        with zipfile.ZipFile(handle) as archive:
            assert len(archive.read(f"{target}.jsonl").splitlines()) == 3
            snapshot = json.loads(archive.read("review.json"))
            assert len(snapshot["events"]) == len(snapshot["current"]) == 3
    archive_path = legacy.parent.parent / "releases" / ".archives" / f"{release['id']}.zip"
    with archive_path.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="review_archive_integrity_error"):
        app.release_archive(run_id, release["id"], release["sha256"])


def test_failed_legacy_migration_rolls_back_events(tmp_path):
    app, run_id, _ = fixture_app(tmp_path, "sft", count=3)
    legacy = tmp_path / "workflows" / run_id / "human-review" / "sft.json"
    original = legacy.read_bytes()
    payload = json.loads(original)
    payload["current"] = {}
    legacy.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_sft_review_history"):
        app.queue(run_id)
    with sqlite3.connect(legacy.with_suffix(".sqlite")) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    legacy.write_bytes(original)
    assert app.queue(run_id)["counts"]["approved"] == 3


def test_missing_current_head_is_rejected(tmp_path):
    app, run_id, _ = fixture_app(tmp_path, "sft", count=3)
    app.queue(run_id)
    database = tmp_path / "workflows" / run_id / "human-review" / "sft.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM current")
    with pytest.raises(ValueError, match="invalid_sft_review_history"):
        app.queue(run_id)


def test_legacy_writer_cannot_silently_replace_migrated_history(tmp_path):
    app, run_id, _ = fixture_app(tmp_path, "sft", count=3)
    app.queue(run_id)
    legacy = tmp_path / "workflows" / run_id / "human-review" / "sft.json"
    with legacy.open("a", encoding="utf-8") as handle:
        handle.write(" ")
    with pytest.raises(ValueError, match="invalid_sft_review_history"):
        app.queue(run_id)


def test_saves_append_events_without_rewriting_old_history(tmp_path):
    row = {"messages": [{"role": "user", "content": "Question"},
                        {"role": "assistant", "content": "Answer"}]}
    first = sft_review_record(row, decision="skipped", reviewer="first")
    second = sft_review_record(row, decision="approved", reviewer="second")
    legacy = tmp_path / "sft.json"
    lookup = lambda key: row if key == first["sample_id"] else None
    with review_store(legacy, "sft", lookup) as store:
        store.append(first)
        original = store.db.execute("SELECT payload,digest FROM events WHERE sequence=1").fetchone()
        store.append(second)
        assert store.db.execute("SELECT payload,digest FROM events WHERE sequence=1").fetchone() == original
        assert store.get(first["sample_id"]) == second
        store.db.execute("UPDATE current SET sequence=1")
        with pytest.raises(ValueError, match="invalid_sft_review_history"):
            store.get(first["sample_id"])
    assert not legacy.exists()


def test_audit_token_reader_handles_small_chunks_and_reordered_fields():
    payload = {"current": {"first": {"reason": "中文\\\"内容"}}, "events": [12, True, None, {"x": [1, 2]}]}
    reader = JSONStream(io.StringIO(json.dumps(payload, ensure_ascii=False)), chunk_size=1)
    result = {}
    for key in reader.keys():
        if key == "current":
            values = {}
            for sample_id in reader.keys():
                values[sample_id] = reader.value()
            result[key] = values
        else:
            result[key] = list(reader.array())
    reader.finish()
    assert result == payload


@pytest.mark.parametrize("payload", ['{"events":[],"events":[]}', '{"events":[1,]}', '{"events":[]}x'])
def test_audit_token_reader_rejects_malformed_history(payload):
    reader = JSONStream(io.StringIO(payload), chunk_size=2)
    with pytest.raises(ValueError, match="invalid_review_json"):
        for key in reader.keys():
            list(reader.array())
        reader.finish()
