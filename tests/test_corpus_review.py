"""CPT review decisions preserve source candidates and publish verified versions."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from lib.application.corpus_review_service import CorpusReviewApplication
from lib.domain.corpus_review import corpus_identity, corpus_review_record, validate_corpus_row
from lib.infrastructure.corpus_review_driver import FilesystemCorpusReviewDriver


def make_review_app(tmp_path: Path, rows: list[dict]):
    run_id = "d" * 32
    run = tmp_path / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                      for row in rows)
    artifact = artifacts / "cpt.jsonl"
    artifact.write_text(payload, encoding="utf-8")
    records = [{"status": "eligible", "text": row["text"], "kind": "document", "source_id": f"src-{i}",
                "location": i + 1, "evidence_level": "source_text"} for i, row in enumerate(rows)]
    records_path = artifacts / "cpt.records.json"
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "CPT 语料审核", "status": "completed", "targets": ["cpt"],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    manifest = {"status": "complete", "sha256": {
        artifact.name: hashlib.sha256(artifact.read_bytes()).hexdigest(),
        records_path.name: hashlib.sha256(records_path.read_bytes()).hexdigest(),
    }}
    (artifacts / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    app = CorpusReviewApplication(FilesystemCorpusReviewDriver(tmp_path))
    return app, run_id, artifact


def test_domain_requires_nonempty_text_and_limits_revisions():
    source = {"text": "设备使用前先断电。"}
    assert validate_corpus_row(source) == source
    with pytest.raises(ValueError, match="empty_cpt_record"):
        validate_corpus_row({"text": "  "})
    with pytest.raises(ValueError, match="invalid_cpt_record"):
        validate_corpus_row({"text": "内容", "source": "metadata"})
    with pytest.raises(ValueError, match="invalid_cpt_revision"):
        corpus_review_record(source, decision="approved", reviewer="admin", text=" ")


def test_stale_decision_is_not_recorded_and_queue_is_paginated(tmp_path):
    app, run_id, _ = make_review_app(tmp_path, [{"text": f"语料 {i}"} for i in range(3)])
    first_page = app.queue(run_id, limit=2)
    assert first_page["total"] == 3 and len(first_page["items"]) == 2
    second_page = app.queue(run_id, offset=2, limit=2)
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["evidence"]["source_id"] == "src-2"
    sample_id = first_page["items"][0]["sample_id"]
    with pytest.raises(ValueError, match="stale_cpt_sample_refresh_required"):
        app.decide(run_id, sample_id, decision="approved", reviewer="admin",
                   expected_hash="0" * 64, text="修订内容")
    assert app.queue(run_id)["counts"]["pending"] == 3


def test_release_requires_terminal_decisions_and_contains_approved_rows_only(tmp_path):
    original = [{"text": "语料 A"}, {"text": "语料 B"}, {"text": "语料 C"}]
    app, run_id, source_artifact = make_review_app(tmp_path, original)
    items = app.queue(run_id)["items"]
    first, second, third = items
    app.decide(run_id, first["sample_id"], decision="approved", reviewer="admin",
               expected_hash=first["sample_id"], text="人工修订语料 A")
    app.decide(run_id, second["sample_id"], decision="rejected", reviewer="admin",
               expected_hash=second["sample_id"], reason="重复内容")
    app.decide(run_id, third["sample_id"], decision="skipped", reviewer="admin",
               expected_hash=third["sample_id"])
    assert app.queue(run_id)["counts"] == {"approved": 1, "rejected": 1, "skipped": 1, "pending": 0}
    with pytest.raises(ValueError, match="cpt_review_incomplete:1"):
        app.release(run_id)

    app.decide(run_id, third["sample_id"], decision="approved", reviewer="admin",
               expected_hash=third["sample_id"])
    data = app.release(run_id)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        released = archive.read("cpt.jsonl")
        review = json.loads(archive.read("review.json"))
        manifest = json.loads(archive.read("manifest.json"))
    assert [json.loads(line)["text"] for line in released.splitlines()] == ["人工修订语料 A", "语料 C"]
    assert len(review["events"]) == 4
    assert manifest["status"] == "human_reviewed" and manifest["target"] == "cpt"
    assert manifest["counts"] == {"candidate": 3, "approved": 2, "rejected": 1}
    assert manifest["source_artifact_sha256"] == hashlib.sha256(source_artifact.read_bytes()).hexdigest()
    assert manifest["sha256"]["cpt.jsonl"] == hashlib.sha256(released).hexdigest()
    assert app.release(run_id)  # each release is versioned independently
    assert (tmp_path / "workflows" / run_id / "artifacts" / "cpt.jsonl").read_text(encoding="utf-8").count("\n") == 3


def test_tampered_cpt_artifact_cannot_be_reviewed(tmp_path):
    app, run_id, artifact = make_review_app(tmp_path, [{"text": "有效 CPT 语料"}])
    artifact.write_text('{"text":"被改写"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        app.queue(run_id)


def test_adapter_rejects_forged_revision_on_rejected_sample(tmp_path):
    app, run_id, _ = make_review_app(tmp_path, [{"text": "原始语料"}])
    sample = app.queue(run_id)["items"][0]
    forged = corpus_review_record(sample["row"], decision="rejected", reviewer="admin")
    forged["candidate"] = {"text": "被非法修改"}
    with pytest.raises(ValueError, match="only_approved_cpt_samples_may_be_revised"):
        app._driver.record_decision(run_id, sample["sample_id"], forged)
