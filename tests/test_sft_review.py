"""SFT review protects conversation structure and produces audited versions."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from lib.application.sft_review_service import SftReviewApplication
from lib.domain.sft_review import sft_identity, sft_review_record, validate_sft_revision
from lib.infrastructure.sft_review_driver import FilesystemSftReviewDriver


def sample(index: int = 1) -> dict:
    return {"messages": [
        {"role": "system", "content": "你是设备维护助手。"},
        {"role": "user", "content": f"怎样检查设备 {index}？"},
        {"role": "assistant", "content": f"先断电，再检查线路 {index}。", "reasoning_content": "安全优先。"},
    ]}


def make_review_app(tmp_path: Path, rows: list[dict]):
    run_id = "f" * 32
    run = tmp_path / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                      for row in rows)
    artifact = artifacts / "sft.jsonl"
    artifact.write_text(payload, encoding="utf-8")
    records = [{"status": "eligible", "messages": row["messages"], "kind": "document", "source_id": f"src-{i}",
                "location": i + 1, "evidence_level": "source_and_model_assessed", "quotes": ["设备手册证据"]}
               for i, row in enumerate(rows)]
    records_path = artifacts / "sft.records.json"
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "SFT 对话审核", "status": "completed", "targets": ["sft"],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({"status": "complete", "sha256": {
        artifact.name: hashlib.sha256(artifact.read_bytes()).hexdigest(),
        records_path.name: hashlib.sha256(records_path.read_bytes()).hexdigest(),
    }}), encoding="utf-8")
    app = SftReviewApplication(FilesystemSftReviewDriver(tmp_path))
    return app, run_id, artifact


def test_domain_allows_assistant_text_revision_and_preserves_prompt_metadata():
    original = sample()
    revised = sample()
    revised["messages"][2]["content"] = "经确认设备已断电，再检查线路。"
    result = validate_sft_revision(original, revised)
    assert result["messages"][1] == original["messages"][1]
    assert result["messages"][2]["reasoning_content"] == "安全优先。"
    changed_user = sample()
    changed_user["messages"][1]["content"] = "改写 prompt"
    with pytest.raises(ValueError, match="sft_message_metadata_is_immutable"):
        validate_sft_revision(original, changed_user)
    changed_role = sample()
    changed_role["messages"][2]["finish_reason"] = "stop"
    with pytest.raises(ValueError, match="sft_message_structure_is_immutable"):
        validate_sft_revision(original, changed_role)


def test_stale_decision_is_not_recorded_and_source_evidence_is_available(tmp_path):
    app, run_id, _ = make_review_app(tmp_path, [sample(i) for i in range(1, 4)])
    queue = app.queue(run_id, offset=2, limit=1)
    item = queue["items"][0]
    assert queue["total"] == 3 and len(queue["items"]) == 1
    assert item["evidence"]["quotes"] == ["设备手册证据"]
    with pytest.raises(ValueError, match="stale_sft_sample_refresh_required"):
        app.decide(run_id, item["sample_id"], decision="approved", reviewer="admin",
                   expected_hash="0" * 64)
    assert app.queue(run_id)["counts"]["pending"] == 3


def test_release_requires_terminal_decisions_and_contains_only_approved_rows(tmp_path):
    original = [sample(1), sample(2), sample(3)]
    app, run_id, artifact = make_review_app(tmp_path, original)
    first, second, third = app.queue(run_id)["items"]
    revised = sample(1)
    revised["messages"][2]["content"] = "人工审核后的回答。"
    app.decide(run_id, first["sample_id"], decision="approved", reviewer="admin",
               expected_hash=first["sample_id"], candidate=revised)
    app.decide(run_id, second["sample_id"], decision="rejected", reviewer="admin",
               expected_hash=second["sample_id"], reason="回答不准确")
    app.decide(run_id, third["sample_id"], decision="skipped", reviewer="admin",
               expected_hash=third["sample_id"])
    with pytest.raises(ValueError, match="sft_review_incomplete:1"):
        app.release(run_id)

    app.decide(run_id, third["sample_id"], decision="approved", reviewer="admin",
               expected_hash=third["sample_id"])
    data = app.release(run_id)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        payload = archive.read("sft.jsonl")
        audit = json.loads(archive.read("review.json"))
        manifest = json.loads(archive.read("manifest.json"))
    released = [json.loads(line) for line in payload.splitlines()]
    assert [row["messages"][-1]["content"] for row in released] == ["人工审核后的回答。", "先断电，再检查线路 3。"]
    assert len(audit["events"]) == 4
    assert manifest["target"] == "sft" and manifest["status"] == "human_reviewed"
    assert manifest["counts"] == {"candidate": 3, "approved": 2, "rejected": 1}
    assert manifest["source_artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert manifest["sha256"]["sft.jsonl"] == hashlib.sha256(payload).hexdigest()
    app.release(run_id)
    assert len(list((tmp_path / "workflows" / run_id / "releases").glob("sft-v*"))) == 2


def test_adapter_rejects_prompt_edit_on_nonapproved_decision(tmp_path):
    app, run_id, _ = make_review_app(tmp_path, [sample()])
    item = app.queue(run_id)["items"][0]
    forged = sft_review_record(item["row"], decision="rejected", reviewer="admin")
    forged["candidate"]["messages"][1]["content"] = "prompt tampering"
    with pytest.raises(ValueError, match="sft_message_metadata_is_immutable"):
        app._driver.record_decision(run_id, item["sample_id"], forged)


def test_tampered_sft_artifact_is_not_reviewable(tmp_path):
    app, run_id, artifact = make_review_app(tmp_path, [sample()])
    artifact.write_text("tampered", encoding="utf-8")
    assert app.reviewable_runs() == []
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        app.queue(run_id)
