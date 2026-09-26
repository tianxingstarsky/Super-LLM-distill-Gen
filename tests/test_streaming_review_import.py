"""Large review imports stay incremental and commit only complete source reads."""
import json
import pytest

from lib import review_center as rc
from lib.infrastructure.sample_preview import SamplePreview
from lib.review import push_samples


def sample(index):
    return {"id": str(index), "messages": [{"role": "user", "content": f"Question {index}"},
                                         {"role": "assistant", "content": "Answer"}]}


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "DB_PATH", tmp_path / "review.db")
    monkeypatch.setattr(rc, "OUT_ROOT", tmp_path)
    rc.ensure_admin("offline-test-key")


def test_streaming_import_reads_rows_only_as_database_consumes_them(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    from lib.infrastructure import sample_preview
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(sample(i)) + "\n" for i in range(1000)), encoding="utf-8")
    preview = SamplePreview(path)
    normalized = []
    original = sample_preview.normalize_sample
    monkeypatch.setattr(sample_preview, "normalize_sample",
                        lambda row: (normalized.append(row["id"]), original(row))[1])
    assert len(preview) == 1000 and normalized == []
    # Each conversion occurs while the preceding row is already in the transaction.
    original_records = rc.add_records
    def consume(dataset, rows):
        def checked():
            for i, row in enumerate(rows):
                assert len(normalized) == i + 1
                yield row
        return original_records(dataset, checked())
    monkeypatch.setattr(rc, "add_records", consume)
    assert push_samples(preview, {}, dataset_name="bulk") == 1000
    assert rc.queue_page("bulk", "admin", limit=1)["total"] == 1000


def test_invalid_late_record_rolls_back_entire_import(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    path = tmp_path / "broken.jsonl"
    path.write_text(json.dumps(sample(1)) + "\n\n[1,2]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"broken.jsonl:3:"):
        push_samples(SamplePreview(path), {}, dataset_name="bulk")
    assert rc.queue_page("bulk", "admin", limit=1)["total"] == 0


def test_source_change_during_import_rolls_back_entire_import(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    path = tmp_path / "changing.jsonl"
    path.write_text(json.dumps(sample(1)) + "\n", encoding="utf-8")
    preview = SamplePreview(path)
    def changed_source():
        for row in preview:
            yield row
            path.write_text(json.dumps(sample(2)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="preview_file_changed"):
        push_samples(changed_source(), {}, dataset_name="bulk")
    assert rc.queue_page("bulk", "admin", limit=1)["total"] == 0
