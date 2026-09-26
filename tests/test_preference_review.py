"""Preference review decisions are auditable and releases contain approved pairs only."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path
import zipfile

import pytest

from lib.application.preference_review_service import PreferenceReviewApplication
from lib.domain.preference_review import pair_identity, review_record, validate_pair
from lib.infrastructure.preference_review_driver import FilesystemPreferenceReviewDriver


def pair(index: int) -> dict:
    return {
        "prompt": [{"role": "user", "content": f"问题 {index}"}],
        "chosen": [{"role": "assistant", "content": f"准确回答 {index}", "reasoning_content": "证据充分"}],
        "rejected": [{"role": "assistant", "content": f"错误回答 {index}"}],
        "tools": [],
    }


def make_review_app(tmp_path: Path, pairs: list[dict], *, target: str = "dpo"):
    run_id = "a" * 32
    run = tmp_path / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    artifact = artifacts / f"{target}.jsonl"
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                      for row in pairs)
    artifact.write_text(payload, encoding="utf-8")
    (run / "state.json").write_text(json.dumps({
        "id": run_id, "name": "偏好人审验收", "status": "completed", "targets": [target],
        "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({
        "status": "complete", "sha256": {artifact.name: hashlib.sha256(artifact.read_bytes()).hexdigest()},
    }), encoding="utf-8")
    driver = FilesystemPreferenceReviewDriver(tmp_path, target=target)
    return PreferenceReviewApplication(driver), run_id, artifact


def test_domain_validates_pair_and_preserves_metadata_when_revising():
    original = pair(1)
    revised = review_record(original, decision="approved", reviewer="reviewer01", reviewed_at="now",
                            reason="修正表达", chosen="新的更优答案", rejected="新的较弱答案")
    assert revised["candidate"]["prompt"] == original["prompt"]
    assert revised["candidate"]["chosen"][-1]["reasoning_content"] == "证据充分"
    assert revised["source_hash"] == pair_identity(original)
    with pytest.raises(ValueError, match="must_differ|invalid_preference_revision"):
        review_record(original, decision="approved", reviewer="reviewer01", reviewed_at="now",
                      chosen="相同", rejected="相同")
    with pytest.raises(ValueError, match="invalid_preference_chosen"):
        validate_pair({**original, "chosen": [{"role": "user", "content": "错位"}]})


def test_application_rejects_stale_pair_before_recording():
    original = pair(1)

    class Driver:
        saved = False

        def pair(self, run_id, pair_id):
            return original

        def record_decision(self, run_id, expected_hash, record):
            self.saved = True
            return record

    driver = Driver()
    app = PreferenceReviewApplication(driver)
    with pytest.raises(ValueError, match="stale_preference_pair"):
        app.decide("run", pair_identity(original), decision="approved", reviewer="admin",
                   expected_hash="0" * 64)
    assert not driver.saved


def test_release_waits_for_all_decisions_and_contains_only_approved_rows(tmp_path):
    rows = [pair(index) for index in range(3)]
    app, run_id, _ = make_review_app(tmp_path, rows)
    queue = app.queue(run_id)
    assert queue["total"] == 3
    first, second, third = queue["items"]
    app.decide(run_id, first["pair_id"], decision="approved", reviewer="admin",
               expected_hash=first["pair_id"], chosen="人工确认的好答案")
    app.decide(run_id, second["pair_id"], decision="rejected", reviewer="admin",
               expected_hash=second["pair_id"], reason="两条回答都不合格")
    app.decide(run_id, third["pair_id"], decision="skipped", reviewer="admin",
               expected_hash=third["pair_id"])
    assert app.queue(run_id)["counts"] == {"approved": 1, "rejected": 1, "skipped": 1, "pending": 0}
    with pytest.raises(ValueError, match="preference_review_incomplete:1"):
        app.release(run_id)

    app.decide(run_id, third["pair_id"], decision="approved", reviewer="admin",
               expected_hash=third["pair_id"])
    archive_bytes = app.release(run_id)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        release_manifest = json.loads(archive.read("manifest.json"))
        released_rows = [json.loads(line) for line in archive.read("dpo.jsonl").decode().splitlines()]
        review_evidence = json.loads(archive.read("review.json"))
        dpo_payload = archive.read("dpo.jsonl")
        evidence_payload = archive.read("review.json")
    assert release_manifest["status"] == "human_reviewed"
    assert release_manifest["target"] == "dpo"
    assert release_manifest["counts"] == {"candidate": 3, "approved": 2, "rejected": 1}
    assert [row["chosen"][-1]["content"] for row in released_rows] == ["人工确认的好答案", "准确回答 2"]
    assert len(review_evidence["events"]) == 4
    assert release_manifest["sha256"]["dpo.jsonl"] == hashlib.sha256(dpo_payload).hexdigest()
    assert release_manifest["sha256"]["review.json"] == hashlib.sha256(evidence_payload).hexdigest()

    second_package = app.release(run_id)
    with zipfile.ZipFile(io.BytesIO(second_package)) as second_archive:
        assert json.loads(second_archive.read("manifest.json"))["version"] == 2


def test_tampered_dpo_artifact_cannot_enter_review_queue(tmp_path):
    app, run_id, artifact = make_review_app(tmp_path, [pair(1)])
    artifact.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        app.queue(run_id)


def test_orpo_only_run_has_its_own_review_queue_and_release(tmp_path):
    app, run_id, artifact = make_review_app(tmp_path, [pair(1), pair(2)], target="orpo")
    assert app.reviewable_runs()[0]["target"] == "orpo"
    assert FilesystemPreferenceReviewDriver(tmp_path).reviewable_runs() == []
    first, second = app.queue(run_id)["items"]
    app.decide(run_id, first["pair_id"], decision="approved", reviewer="reviewer01",
               expected_hash=first["pair_id"], chosen="人工核对过的回答")
    app.decide(run_id, second["pair_id"], decision="rejected", reviewer="reviewer01",
               expected_hash=second["pair_id"], reason="偏好不明确")
    data = app.release(run_id)
    release = tmp_path / "workflows" / run_id / "releases" / "orpo-preference-v0001"
    assert release.is_dir()
    assert not (release / "dpo.jsonl").exists()
    assert (tmp_path / "workflows" / run_id / "human-review" / "preferences-orpo.sqlite").exists()
    assert not (tmp_path / "workflows" / run_id / "human-review" / "preferences.sqlite").exists()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {"orpo.jsonl", "review.json", "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
        rows = [json.loads(line) for line in archive.read("orpo.jsonl").decode().splitlines()]
        evidence = json.loads(archive.read("review.json"))
        assert manifest["target"] == "orpo"
        assert manifest["source_artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert manifest["sha256"]["orpo.jsonl"] == hashlib.sha256(archive.read("orpo.jsonl")).hexdigest()
        assert len(rows) == 1 and rows[0]["chosen"][0]["content"] == "人工核对过的回答"
        assert all(event["target"] == "orpo" for event in evidence["events"])


def test_dpo_and_orpo_from_one_run_keep_independent_decisions_and_versions(tmp_path):
    dpo, run_id, dpo_artifact = make_review_app(tmp_path, [pair(1)])
    run = tmp_path / "workflows" / run_id
    orpo_artifact = run / "artifacts" / "orpo.jsonl"
    orpo_artifact.write_text(json.dumps(pair(2), ensure_ascii=False) + "\n", encoding="utf-8")
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    state["targets"].append("orpo")
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    manifest = json.loads((run / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    manifest["sha256"]["orpo.jsonl"] = hashlib.sha256(orpo_artifact.read_bytes()).hexdigest()
    (run / "artifacts" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    orpo = PreferenceReviewApplication(FilesystemPreferenceReviewDriver(tmp_path, target="orpo"))
    orpo_item = orpo.queue(run_id)["items"][0]
    orpo.decide(run_id, orpo_item["pair_id"], decision="approved", reviewer="admin",
                expected_hash=orpo_item["pair_id"])
    orpo.release(run_id)
    assert dpo.queue(run_id)["counts"]["pending"] == 1
    with pytest.raises(ValueError, match="preference_review_incomplete"):
        dpo.release(run_id)
    dpo_item = dpo.queue(run_id)["items"][0]
    dpo.decide(run_id, dpo_item["pair_id"], decision="approved", reviewer="admin",
               expected_hash=dpo_item["pair_id"])
    dpo.release(run_id)
    assert (run / "releases" / "preference-v0001" / "dpo.jsonl").exists()
    assert (run / "releases" / "orpo-preference-v0001" / "orpo.jsonl").exists()
    assert dpo_artifact.read_text(encoding="utf-8").count("\n") == 1


def test_orpo_driver_rejects_dpo_event_even_for_identical_pair(tmp_path):
    app, run_id, _ = make_review_app(tmp_path, [pair(1)], target="orpo")
    item = app.queue(run_id)["items"][0]
    forged = review_record(item["pair"], decision="approved", reviewer="admin",
                           reviewed_at="now", target="dpo")
    with pytest.raises(ValueError, match="preference_review_target_mismatch"):
        app._driver.record_decision(run_id, item["pair_id"], forged)
    assert app.queue(run_id)["counts"]["pending"] == 1


def test_orpo_release_rejects_modified_review_state(tmp_path):
    app, run_id, _ = make_review_app(tmp_path, [pair(1)], target="orpo")
    item = app.queue(run_id)["items"][0]
    app.decide(run_id, item["pair_id"], decision="approved", reviewer="admin",
               expected_hash=item["pair_id"])
    review_path = tmp_path / "workflows" / run_id / "human-review" / "preferences-orpo.sqlite"
    with sqlite3.connect(review_path) as database:
        sequence, payload = database.execute("SELECT sequence,payload FROM events LIMIT 1").fetchone()
        event = json.loads(payload)
        event["candidate"]["chosen"][0]["content"] = "未经审核的回答"
        database.execute("UPDATE events SET payload=? WHERE sequence=?", (json.dumps(event), sequence))
    with pytest.raises(ValueError, match="invalid_preference_review_history"):
        app.release(run_id)


def test_duplicate_orpo_pairs_do_not_share_one_review_decision(tmp_path):
    app, run_id, _ = make_review_app(tmp_path, [pair(1), pair(1)], target="orpo")
    assert app.reviewable_runs() == []
    with pytest.raises(ValueError, match="duplicate_preference_pair_id"):
        app.queue(run_id)
