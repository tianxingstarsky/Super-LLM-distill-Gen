import hashlib
import json
import tracemalloc

import pytest

from lib.domain.sample_distribution import Distribution
from lib.infrastructure.workflow_statistics import build_statistics, cached_statistics
from lib.infrastructure import review_release_jobs


def _run(tmp_path, records, *, counts=None, extra=None):
    run = tmp_path / "workflows" / ("a" * 32)
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    manifest = {"status": "complete", "counts": {}, "sha256": {}}
    for target, rows in records.items():
        name = f"{target}.jsonl"
        content = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode()
        (artifacts / name).write_bytes(content)
        manifest["counts"][target] = len(rows)
        manifest["sha256"][name] = hashlib.sha256(content).hexdigest()
    for name, content in (extra or {}).items():
        (artifacts / name).write_bytes(content)
        manifest["sha256"][name] = hashlib.sha256(content).hexdigest()
    if counts is not None:
        manifest["counts"] = counts
    (artifacts / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({"id": run.name}), encoding="utf-8")
    return run, manifest


def test_roles_and_native_cot_keep_reasoning_separate_from_language_and_turns():
    stats = Distribution()
    stats.add("sft", {"messages": [
        {"role": "system", "content": "never count this system prompt"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀", "reasoning_content": "very long English reasoning",
         "thinking": "very long English reasoning"},
        {"role": "user", "content": [{"type": "tool_result", "content": "not a user turn"}]},
        {"role": "tool", "content": "ignore tool text"},
    ]})
    stats.add("cot", {"question": [{"role": "user", "content": "为何"}],
                      "reasoning": ["先检查", "再核对"], "answer": "因为如此"})
    stats.add("cpt", {"text": "预训练资料"})
    result = stats.result()
    assert result["rows"] == 3
    assert result["languages"]["zh"] == 3
    assert result["user_languages"]["zh"] == 2
    assert result["user_languages"]["none"] == 1
    assert result["turns"] == {"0": 1, "1": 2, "2": 0, "3–5": 0, "6+": 0}
    assert result["reasoning_chars"] == len("very long English reasoning先检查再核对")
    assert result["reasoning_rows"] == 2
    assert result["body_chars"] == len("你好你好呀为何因为如此预训练资料")


def test_multimodal_and_preference_variants_are_counted_as_records_not_responses():
    stats = Distribution()
    stats.add("multiturn", {"messages": [
        {"role": "user", "content": [{"type": "text", "text": "What is this?"},
                                     {"type": "image_url", "image_url": {"url": "not text"}}]},
        {"role": "assistant", "content": "A cube."},
        {"role": "user", "content": "And the colour?"},
        {"role": "assistant", "content": "Blue."}]})
    stats.add("dpo", {"prompt": "Hello?", "chosen": "Hi!", "rejected": "No."})
    stats.add("rlaif", {"prompt": "Hi?", "responses": [{"response": "Hello."}, {"response": "Bye."}]})
    result = stats.result()
    assert result["rows"] == 3
    assert result["languages"]["en"] == 3
    assert result["turns"]["2"] == 1
    assert result["turns"]["1"] == 2
    assert result["image_rows"] == 1


def test_only_native_files_count_and_cache_is_bound_to_verified_version(tmp_path):
    run, manifest = _run(tmp_path, {"sft": [{"messages": [{"role": "user", "content": "Hi"},
                                                          {"role": "assistant", "content": "Hello"}]}]},
                         extra={"sft.trl.jsonl": b'{"messages": []}\n' * 30,
                                "sft.records.json": b'[{"status":"quarantined"}]'})
    result = build_statistics(run)
    assert result["total"]["rows"] == 1
    assert list(result["targets"]) == ["sft"]
    before = (run / "analysis/sample-distribution.json").stat().st_mtime_ns
    assert build_statistics(run) == result
    assert (run / "analysis/sample-distribution.json").stat().st_mtime_ns == before
    (run / "artifacts/sft.jsonl").write_text('{}\n', encoding="utf-8")
    assert cached_statistics(run, manifest) is None
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        build_statistics(run)


def test_count_mismatch_never_publishes_completed_statistics(tmp_path):
    run, _ = _run(tmp_path, {"cpt": [{"text": "one row"}]}, counts={"cpt": 2})
    with pytest.raises(ValueError, match="statistics_count_mismatch"):
        build_statistics(run)
    assert not (run / "analysis/sample-distribution.json").exists()


def test_multiple_targets_merge_histograms_without_counting_conversion_copies(tmp_path):
    run, _ = _run(tmp_path, {
        "cpt": [{"text": "中文语料"}],
        "sft": [{"messages": [{"role": "user", "content": "Hello"},
                                {"role": "assistant", "content": "Hi", "reasoning_content": "Check."}]}],
    }, extra={"trl_sft.jsonl": b'{"messages":[]}\n' * 10})
    result = build_statistics(run)
    assert result["total"]["rows"] == 2
    assert result["total"]["body_chars"] == len("中文语料HelloHi")
    assert result["total"]["languages"] == {"zh": 1, "en": 1, "mixed": 0, "other": 0}
    assert result["total"]["user_languages"]["none"] == 1
    assert result["total"]["reasoning_rows"] == 1
    assert result["total"]["reasoning_chars"] == 6


def test_statistics_cache_contains_aggregates_without_copying_private_sample_text(tmp_path):
    private_text = "sample-private-content-must-stay-in-the-source"
    run, _ = _run(tmp_path, {"cpt": [{"text": private_text}]})
    build_statistics(run)
    cache = (run / "analysis/sample-distribution.json").read_text(encoding="utf-8")
    assert private_text not in cache
    assert json.loads(cache)["total"]["body_chars"] == len(private_text)


def test_negative_agent_traces_are_separate_from_positive_totals(tmp_path):
    run, manifest = _run(tmp_path, {"agent": [{"messages": [{"role": "user", "content": "help"}]}]},
                         extra={"agent.negative.jsonl": b'{"messages": []}\n' * 2})
    manifest["negative_counts"] = {"agent": 2}
    (run / "artifacts/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = build_statistics(run)
    assert result["total"]["rows"] == 1
    assert result["targets"]["agent_negative"]["rows"] == 2


def test_statistics_job_runs_offline_and_cancelled_queue_does_not_scan(tmp_path, monkeypatch):
    run, _ = _run(tmp_path, {"cpt": [{"text": "local corpus"}]})
    launched = []
    monkeypatch.setattr(review_release_jobs.subprocess, "Popen", lambda *a, **k: launched.append((a, k)))
    job = review_release_jobs.start_release_job(tmp_path, run.name, "statistics", 0)
    assert '--target' in launched[0][0][0] and 'statistics' in launched[0][0][0]
    assert review_release_jobs.start_release_job(tmp_path, run.name, "statistics", 0)["id"] == job["id"]
    assert len(launched) == 1
    review_release_jobs.cancel_release_job(tmp_path, run.name, "statistics")
    review_release_jobs.execute_release_job(tmp_path, run.name, "statistics", job["id"])
    assert not (run / "analysis/sample-distribution.json").exists()
    next_job = review_release_jobs.start_release_job(tmp_path, run.name, "statistics", 0)
    review_release_jobs.execute_release_job(tmp_path, run.name, "statistics", next_job["id"])
    finished = review_release_jobs.release_job(tmp_path, run.name, "statistics")
    assert finished["status"] == "completed" and finished["result"]["total"]["rows"] == 1


def test_fifty_thousand_records_use_bounded_scan_memory(tmp_path):
    run, manifest = _run(tmp_path, {}, extra={"sft.jsonl": b'{"messages":[{"role":"user","content":"Hi"},{"role":"assistant","content":"Hello"}]}\n' * 50000},
                         counts={"sft": 50000})
    tracemalloc.start()
    try:
        result = build_statistics(run)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["total"]["rows"] == 50000
    assert result["total"]["languages"]["en"] == 50000
    assert peak < 4 * 1024 * 1024
