"""Agent human review cannot promote failed or unverified trajectories."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import tracemalloc

import pytest

from lib.infrastructure.agent_review_driver import FilesystemAgentReviewDriver
from lib.infrastructure import agent_review_driver as review_adapter
from lib.infrastructure.training_workflow import Workflow, create_run, digest, read_json, run_path, verify_artifacts
from lib.domain.agent_trajectory import REPLAY_POLICY_VERSION
from lib.infrastructure.json_stream import iter_json_records
from lib.io_utils import atomic_json


def _call(call_id, expression):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id,
        "type": "function", "function": {"name": "calculator", "arguments":
                                     json.dumps({"expression": expression})}}]}


def _result(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _trace(call_id, expression, result):
    return [{"role": "user", "content": expression}, _call(call_id, expression),
            _result(call_id, result), {"role": "assistant", "content": result}]


def _run(tmp_path, *, mixed=True, source_name="traces.jsonl"):
    source = tmp_path / source_name
    traces = [_trace("a", "2+2", "4")]
    if mixed:
        traces.append(_trace("b", "3+3", "7"))
    source.write_text("".join(json.dumps({"messages": row}) + "\n" for row in traces),
                      encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source], targets=["agent"])
    state = Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == ("needs_attention" if mixed else "completed")
    return output, run_id, run_path(output, run_id)


def _decide(driver, run_id, page_item, signature, *, decision="approved", version=0):
    return driver.decide(run_id, page_item["candidate_id"], source_signature=signature,
                         native_sha256=page_item["native_sha256"],
                         record_sha256=page_item["record_sha256"],
                         expected_version=version, decision=decision,
                         reviewer="human-reviewer", reason="Replay evidence checked")


def _resign(path, *names):
    manifest_path = path / "artifacts" / "manifest.json"
    manifest = read_json(manifest_path)
    for name in names:
        manifest["sha256"][name] = hashlib.sha256((path / "artifacts" / name).read_bytes()).hexdigest()
    atomic_json(manifest_path, manifest)


def test_agent_review_keeps_negative_read_only_and_preserves_replay_evidence(tmp_path):
    output, run_id, _ = _run(tmp_path)
    driver = FilesystemAgentReviewDriver(output)
    positive = driver.queue(run_id, kind="positive", limit=1)
    negative = driver.queue(run_id, kind="negative", limit=1)
    assert positive["total"] == 1 and positive["counts"]["pending"] == 1
    assert positive["items"][0]["independent_replay"] is True
    assert negative["total"] == 1 and negative["items"][0]["independent_replay"] is False
    assert negative["items"][0]["negative"]["failure"] == "tool_observation_mismatch"
    assert positive["items"][0]["record"]["verification"]["verified_call_ids"] == ["a"]
    assert positive["items"][0]["record"]["verification"]["source_snapshot_sha256"] == \
           positive["items"][0]["record"]["source_id"]
    assert negative["items"][0]["candidate_id"] != positive["items"][0]["candidate_id"]
    with pytest.raises(ValueError, match="agent_positive_candidate_not_found"):
        _decide(driver, run_id, negative["items"][0], positive["source_signature"])

    item = positive["items"][0]
    first = _decide(driver, run_id, item, positive["source_signature"])
    assert first["decision"] == "approved" and first["version"] == 1
    with pytest.raises(ValueError, match="stale_agent_review_version"):
        _decide(driver, run_id, item, positive["source_signature"])
    reviewed = driver.queue(run_id, decision="approved")
    assert reviewed["counts"] == {"approved": 1, "rejected": 0, "pending": 0}
    assert reviewed["items"][0]["review_version"] == 1
    assert driver.queue(run_id, kind="negative")["items"][0]["negative"]["failure"] == \
           "tool_observation_mismatch"


@pytest.mark.parametrize("filename", ["agent.jsonl", "agent.records.json", "agent.negative.jsonl"])
def test_changed_agent_artifact_invalidates_review_even_after_manifest_resign(tmp_path, filename):
    output, run_id, path = _run(tmp_path)
    driver = FilesystemAgentReviewDriver(output)
    page = driver.queue(run_id)
    _decide(driver, run_id, page["items"][0], page["source_signature"])
    artifact = path / "artifacts" / filename
    artifact.write_bytes(artifact.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        driver.queue(run_id)
    manifest_path = path / "artifacts" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["sha256"][filename] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    atomic_json(manifest_path, manifest)
    assert verify_artifacts(path)["sha256"][filename] == manifest["sha256"][filename]
    with pytest.raises(ValueError, match="agent_review_source_changed"):
        driver.queue(run_id)
    with pytest.raises(ValueError, match="agent_review_source_changed"):
        _decide(driver, run_id, page["items"][0], page["source_signature"])


def test_review_history_cannot_move_to_another_run(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    output_a, run_a, path_a = _run(first, mixed=False)
    output_b, run_b, path_b = _run(second, mixed=False)
    driver_a = FilesystemAgentReviewDriver(output_a)
    page = driver_a.queue(run_a)
    _decide(driver_a, run_a, page["items"][0], page["source_signature"])
    destination = path_b / "human-review"
    destination.mkdir()
    shutil.copyfile(path_a / "human-review" / "agent.sqlite", destination / "agent.sqlite")
    with pytest.raises(ValueError, match="agent_review_source_changed"):
        FilesystemAgentReviewDriver(output_b).queue(run_b)


def test_review_lock_symlink_is_rejected(tmp_path):
    output, run_id, path = _run(tmp_path, mixed=False)
    folder = path / "human-review"
    folder.mkdir()
    outside = tmp_path / "outside-lock"
    try:
        (folder / "agent.sqlite.lock").symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks are unavailable in this test environment")
    with pytest.raises(ValueError, match="linked_agent_review_store"):
        FilesystemAgentReviewDriver(output).queue(run_id)
    assert not outside.exists()


def test_agent_review_page_bounds_and_filters(tmp_path):
    output, run_id, _ = _run(tmp_path)
    driver = FilesystemAgentReviewDriver(output)
    assert driver.queue(run_id, kind="positive", offset=0, limit=1)["next_offset"] is None
    assert driver.queue(run_id, kind="negative", offset=0, limit=1)["next_offset"] is None
    assert driver.queue(run_id, kind="positive", offset=1, limit=1)["items"] == []
    for kwargs in ({"offset": -1}, {"offset": 50_001}, {"limit": 0}, {"limit": 21},
                   {"decision": "unknown"}, {"kind": "unknown"},
                   {"kind": "negative", "decision": "approved"}):
        with pytest.raises(ValueError):
            driver.queue(run_id, **kwargs)


def test_agent_review_page_has_cumulative_byte_budget(tmp_path, monkeypatch):
    output, run_id, _ = _run(tmp_path)
    driver = FilesystemAgentReviewDriver(output)
    monkeypatch.setattr(review_adapter, "_MAX_PAGE_BYTES", 1)
    with pytest.raises(ValueError, match="agent_review_page_too_large_reduce_limit"):
        driver.queue(run_id, kind="positive")
    with pytest.raises(ValueError, match="agent_review_page_too_large_reduce_limit"):
        driver.queue(run_id, kind="negative")


def test_json_array_limit_bounds_one_object_without_limiting_whitespace(tmp_path):
    source = tmp_path / "records.json"
    source.write_text("[" + " " * 100_000 + '{"a":1}]', encoding="utf-8")
    assert list(iter_json_records(source, chunk_size=64, max_record_chars=7)) == [{"a": 1}]
    source.write_text('[{"a":"' + "x" * 1_000 + '"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="record_exceeds_limit"):
        list(iter_json_records(source, chunk_size=64, max_record_chars=128))


@pytest.mark.parametrize("filename", ["agent.jsonl", "agent.records.json", "agent.negative.jsonl"])
def test_single_oversized_agent_record_is_rejected_before_indexing(tmp_path, filename):
    output, run_id, path = _run(tmp_path)
    artifact = path / "artifacts" / filename
    if filename == "agent.records.json":
        records = read_json(artifact)
        records[0]["padding"] = "x" * (review_adapter._MAX_RECORD_BYTES // 4 + 70_000)
        artifact.write_text(json.dumps(records), encoding="utf-8")
    else:
        line = artifact.read_bytes().splitlines()[0]
        artifact.write_bytes(line + b" " * review_adapter._MAX_JSONL_ROW_BYTES + b"\n")
    manifest_path = path / "artifacts" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["sha256"][filename] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    atomic_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="record_exceeds_limit|agent_review_record_too_large"):
        FilesystemAgentReviewDriver(output).queue(run_id)
    assert not (path / "human-review" / "agent.sqlite").exists()


def test_jsonl_line_limit_counts_utf8_bytes_not_characters(tmp_path):
    output, run_id, path = _run(tmp_path)
    artifact = path / "artifacts" / "agent.negative.jsonl"
    artifact.write_text('{"text":"' + "汉" * (review_adapter._MAX_JSONL_ROW_BYTES // 3 + 1)
                        + '"}\n', encoding="utf-8")
    _resign(path, "agent.negative.jsonl")
    with pytest.raises(ValueError, match="agent_review_record_too_large"):
        FilesystemAgentReviewDriver(output).queue(run_id)


def test_forged_or_missing_replay_proof_does_not_enter_positive_queue(tmp_path):
    output, run_id, path = _run(tmp_path, mixed=False)
    records_path = path / "artifacts" / "agent.records.json"
    records = read_json(records_path)
    records[0]["verification"]["verified_call_ids"] = ["unrelated"]
    records_path.write_text(json.dumps(records), encoding="utf-8")
    manifest_path = path / "artifacts" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["sha256"]["agent.records.json"] = hashlib.sha256(records_path.read_bytes()).hexdigest()
    atomic_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="agent_review_replay_evidence_invalid"):
        FilesystemAgentReviewDriver(output).queue(run_id)


def test_forged_eligible_arithmetic_trace_with_resigned_manifest_is_rejected(tmp_path):
    output, run_id, path = _run(tmp_path, mixed=False)
    artifacts = path / "artifacts"
    native_path = artifacts / "agent.jsonl"
    record_path = artifacts / "agent.records.json"
    native = json.loads(native_path.read_text(encoding="utf-8"))
    native["messages"][2]["content"] = "5"
    native["messages"][3]["content"] = "5"
    native_path.write_text(json.dumps(native) + "\n", encoding="utf-8")
    records = read_json(record_path)
    records[0]["messages"] = native["messages"]
    records[0]["original_messages_sha256"] = digest(native["messages"])
    records[0]["verification"]["final_result"] = 5
    record_path.write_text(json.dumps(records), encoding="utf-8")
    _resign(path, "agent.jsonl", "agent.records.json")
    assert verify_artifacts(path)["counts"]["agent"] == 1
    with pytest.raises(ValueError, match="agent_review_independent_replay_failed"):
        FilesystemAgentReviewDriver(output).queue(run_id)


@pytest.mark.parametrize("unsupported", ["snapshot", "container", "pruned"])
def test_unanchored_positive_can_be_inspected_and_rejected_but_not_approved(tmp_path, unsupported):
    output, run_id, path = _run(tmp_path, mixed=False)
    record_path = path / "artifacts" / "agent.records.json"
    records = read_json(record_path)
    verification = records[0]["verification"]
    if unsupported == "snapshot":
        verification["method"] = "snapshot_json_pointer_replay"
        verification["call_evidence"][0]["method"] = "snapshot_json_pointer_replay"
    elif unsupported == "container":
        verification["method"] = "isolated_docker_ledger_replay"
        verification["call_evidence"][0]["method"] = "isolated_docker_ledger_replay"
        records[0]["evidence_level"] = "isolated_container_replay"
    else:
        verification["verified_call_ids"].append("pruned-call")
        verification["call_evidence"].append({"call_id": "pruned-call", "tool": "calculator",
                                              "method": "bounded_arithmetic_replay"})
        verification["pruned_call_ids"] = ["pruned-call"]
    record_path.write_text(json.dumps(records), encoding="utf-8")
    _resign(path, "agent.records.json")
    driver = FilesystemAgentReviewDriver(output)
    page = driver.queue(run_id)
    assert page["items"][0]["independent_replay"] is False
    with pytest.raises(ValueError, match="agent_review_independent_replay_required"):
        _decide(driver, run_id, page["items"][0], page["source_signature"])
    rejected = _decide(driver, run_id, page["items"][0], page["source_signature"],
                       decision="rejected")
    assert rejected["decision"] == "rejected"


def test_local_index_flag_cannot_bypass_independent_replay(tmp_path):
    output, run_id, path = _run(tmp_path, mixed=False)
    record_path = path / "artifacts" / "agent.records.json"
    records = read_json(record_path)
    records[0]["verification"]["method"] = "snapshot_json_pointer_replay"
    records[0]["verification"]["call_evidence"][0]["method"] = "snapshot_json_pointer_replay"
    record_path.write_text(json.dumps(records), encoding="utf-8")
    _resign(path, "agent.records.json")
    driver = FilesystemAgentReviewDriver(output)
    page = driver.queue(run_id)
    with sqlite3.connect(path / "human-review" / "agent.sqlite") as db:
        db.execute("UPDATE positives SET independent_replay=1")
    with pytest.raises(ValueError, match="invalid_agent_review_store"):
        driver.queue(run_id)
    with pytest.raises(ValueError, match="invalid_agent_review_store"):
        _decide(driver, run_id, page["items"][0], page["source_signature"])


def test_decision_history_has_per_candidate_limit(tmp_path):
    output, run_id, _ = _run(tmp_path, mixed=False)
    driver = FilesystemAgentReviewDriver(output)
    page = driver.queue(run_id)
    item = page["items"][0]
    for version in range(review_adapter._MAX_EVENTS_PER_CANDIDATE):
        _decide(driver, run_id, item, page["source_signature"], version=version)
    with pytest.raises(ValueError, match="agent_review_event_limit"):
        _decide(driver, run_id, item, page["source_signature"],
                version=review_adapter._MAX_EVENTS_PER_CANDIDATE)


def test_fifty_thousand_agent_rows_build_disk_index_and_page_without_full_load(tmp_path):
    count = 50_000
    run_id = "f" * 32
    source_id = "e" * 64
    output = tmp_path / "output"
    path = run_path(output, run_id)
    artifacts = path / "artifacts"
    artifacts.mkdir(parents=True)
    with (artifacts / "agent.jsonl").open("w", encoding="utf-8") as native, (
            artifacts / "agent.records.json").open("w", encoding="utf-8") as records:
        records.write("[")
        for index in range(count):
            call_id = f"call-{index}"
            messages = [{"role": "user", "content": f"Compute item {index}"},
                        _call(call_id, "2+2"), _result(call_id, "4"),
                        {"role": "assistant", "content": "4"}]
            native.write(json.dumps({"messages": messages}, separators=(",", ":")) + "\n")
            record = {"id": f"{index + 1:064x}", "source_id": source_id,
                      "status": "eligible", "evidence_level": "local_tool_replay",
                      "messages": messages,
                      "original_messages_sha256": digest(messages),
                      "verification": {"policy": REPLAY_POLICY_VERSION,
                          "method": "bounded_arithmetic_replay",
                          "verified_call_ids": [call_id], "verified_turns": [1],
                          "call_evidence": [{"call_id": call_id, "tool": "calculator",
                                             "method": "bounded_arithmetic_replay"}],
                          "source_snapshot_sha256": source_id,
                          "final_result": 4,
                          "pruned_call_ids": []}}
            if index:
                records.write(",")
            records.write(json.dumps(record, separators=(",", ":")))
        records.write("]")
    (artifacts / "agent.negative.jsonl").write_text("", encoding="utf-8")
    atomic_json(path / "state.json", {"id": run_id, "status": "completed", "targets": ["agent"]})
    names = ("agent.jsonl", "agent.records.json", "agent.negative.jsonl")
    hashes = {}
    for name in names:
        with (artifacts / name).open("rb") as handle:
            hashes[name] = hashlib.file_digest(handle, "sha256").hexdigest()
    atomic_json(artifacts / "manifest.json", {"status": "complete", "run_id": run_id,
                "counts": {"agent": count}, "negative_counts": {},
                "sources": [{"sha256": source_id}],
                "sha256": hashes})

    driver = FilesystemAgentReviewDriver(output)
    tracemalloc.start()
    start = time.perf_counter()
    last = driver.queue(run_id, offset=count - 1, limit=1)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert last["total"] == count and last["matched"] == count
    assert len(last["items"]) == 1 and last["items"][0]["ordinal"] == count
    assert driver.queue(run_id, offset=0, limit=1)["items"][0]["ordinal"] == 1
    assert peak < 96 * 1024 * 1024, f"index peak memory {peak:,} bytes"
    print(f"Agent review 50k index: {elapsed:.2f}s, peak traced memory {peak / 2**20:.1f} MiB")
