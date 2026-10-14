"""Real artifact reports: bounded paging, integrity, provenance and safe HTML."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from filelock import FileLock

import pytest

from lib.application.workflow_report_service import WorkflowReportApplication
from lib.bootstrap.workflow_reports import workflow_report_application
from lib.domain.workflow_report import content_digest
from lib.infrastructure.training_workflow import preferred_training_record
from lib.infrastructure import workflow_report as report_module
from lib.infrastructure.workflow_report_jobs import ReportJobs

RID = "a" * 32


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Read-only reports need no model server or paid/model request."""
    yield None


@pytest.fixture(autouse=True)
def isolated_report_jobs(monkeypatch):
    jobs = ReportJobs()
    monkeypatch.setattr(report_module, "REPORT_JOBS", jobs)
    return jobs


def ready_page(app, target="cot", **kwargs):
    until = time.monotonic() + 5
    while True:
        page = app.samples(RID, target, **kwargs)
        if page.get("preparation", {}).get("status") == "failed":
            raise AssertionError(page["preparation"]["error"])
        if not page.get("indexing"):
            return page
        if time.monotonic() > until:
            raise AssertionError("Background report did not finish")
        time.sleep(.01)


def failed_page(app, target="cot"):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        page = app.samples(RID, target)
        if page.get("preparation", {}).get("status") == "failed":
            return page
        time.sleep(.01)
    raise AssertionError("Expected report preparation failure")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def seal(run, recipe):
    files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in (run / "artifacts").iterdir() if path.name != "manifest.json"}
    counts = {target: sum(1 for line in (run / "artifacts" / f"{target}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
              for target in recipe["targets"]}
    write_json(run / "artifacts" / "manifest.json", {"status": "complete", "run_id": RID,
               "recipe_hash": content_digest(recipe), "counts": counts, "sha256": files})


def fixture_run(tmp_path, rows, target="cot", *, status="completed", style=None, warm=True):
    output = tmp_path / "output"
    run = output / "workflows" / RID
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    recipe = {"targets": [target], "sources": [{"name": "Actual document.md", "sha256": "b" * 64, "bytes": 125}],
              "sft_output_style": style}
    state = {"id": RID, "name": "Real task", "status": status, "recipe_hash": content_digest(recipe),
             "created_at": "2026-10-14T01:00:00Z", "updated_at": "2026-10-14T01:01:00Z",
             "stages": {target: {"status": "completed", "done": len(rows), "total": len(rows), "outputs": len(rows)}},
             "quality": {"targets": {target: {"eligible": sum(row["status"] == "eligible" for row in rows),
                                              "total": len(rows), "reasons": {"actual_reason": 1}}},
                         "human_review": "not_performed"}}
    write_json(run / "recipe.json", recipe)
    write_json(run / "state.json", state)
    write_json(artifacts / f"{target}.records.json", rows)
    (artifacts / f"{target}.jsonl").write_text("".join(json.dumps(preferred_training_record(target, row, sft_output_style=style), ensure_ascii=False) + "\n"
                                                    for row in rows if row["status"] == "eligible"), encoding="utf-8")
    write_json(artifacts / "quality.json", {"targets": state["quality"]["targets"], "input_issues": [{"huge": "never read wholesale"}]})
    seal(run, recipe)
    app = workflow_report_application(tmp_path, output)
    if warm and status == "completed":
        ready_page(app, target)
    return app, run, recipe, state


def cot(index=0, **extra):
    return {"id": str(index), "status": "eligible", "question": [{"role": "user", "content": f"Real question {index}"}],
            "reasoning": f"Task-specific explanation {index}.", "answer": f"Real answer {index}.", **extra}


def test_full_actual_body_and_distinct_current_review_scopes(tmp_path):
    text = "A complete paragraph.\n\n" + "Full reasoning, without clipping. " * 300
    row = cot(reasoning=text, source_context={"text": "PRIVATE_TEACHER_SENTINEL", "source_id": "source-1"},
              judge={"keep": True, "correctness": 5, "scores": {"correctness": 5}, "reason": "Actual factual decision"},
              learner_context_check={"keep": True, "context_complete": True, "leakage_free": True, "reason": "Actual context decision"},
              review_repair={"mode": "auto", "status": "accepted", "score": 1, "history": [{"old": "OLD_ANSWER_SENTINEL"}]},
              reasoning_prefill={"applied": True, "prefix_sha256": "c" * 64})
    app, run, _, _ = fixture_run(tmp_path, [row])
    original_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (run / "artifacts").iterdir()}
    overview, page = app.overview(RID), app.samples(RID, "cot")
    assert overview["artifact_state"] == "formal" and overview["targets"][0]["eligible"] == 1
    assert overview["targets"][0]["counts_verified"] is False
    sample = page["rows"][0]
    assert page["summary"]["counts_verified"] and page["summary"]["quality"]["fact_correctness_mean"] == 5
    assert sample["training_body"]["reasoning"] == text
    assert sample["training_body_sha256"] == content_digest(preferred_training_record("cot", row))
    assert sample["reviews"]["fact"] and sample["reviews"]["context"]
    assert sample["reviews"]["human"] == [] and sample["reviews"]["independent"] == []
    document = app.html(RID, "cot")
    assert "OLD_ANSWER_SENTINEL" not in document and "PRIVATE_TEACHER_SENTINEL" not in document
    assert "Task-specific" not in document and "without clipping" in document
    assert original_hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (run / "artifacts").iterdir()}


def test_paging_uses_stream_once_then_seek_and_keeps_last_row(tmp_path, monkeypatch):
    app, run, _, _ = fixture_run(tmp_path, [cot(index) for index in range(2000)], warm=False)
    actual_iter = report_module.iter_json_records
    scans = []
    def tracked(path, **kwargs):
        scans.append(path)
        yield from actual_iter(path, **kwargs)
    monkeypatch.setattr(report_module, "iter_json_records", tracked)
    first = ready_page(app, offset=1987, limit=7)
    second = ready_page(app, offset=1994, limit=7)
    assert len(scans) == 1
    assert [row["id"] for row in first["rows"]] == [str(i) for i in range(1987, 1994)]
    assert len(second["rows"]) == 6 and second["rows"][-1]["training_body"]["answer"] == "Real answer 1999."
    assert first["has_next"] and not second["has_next"] and second["total"] == 2000
    assert app.samples(RID, "cot", offset=2000)["rows"] == []


def test_application_caps_pages_and_validates_targets_languages():
    class Driver:
        def samples(self, run, target, offset, limit):
            return offset, limit
        def html(self, run, target, offset, limit, language):
            return language
    app = WorkflowReportApplication(Driver())
    assert app.samples(RID, "sft", -4, 1000000) == (0, 50)
    assert app.samples(RID, "cot", 0, 0) == (0, 1)
    with pytest.raises(ValueError, match="unknown_training_target"):
        app.samples(RID, "imaginary")
    with pytest.raises(ValueError, match="unsupported_report_language"):
        app.html(RID, "cot", language="unknown")


def test_reading_title_uses_literal_legacy_utterance_without_changing_training_body():
    row = {"training_body": {"messages": [{"role": "user", "content":
           "资料：\n\nActual visible article.\n\n用户消息：\n\nWhat does the article say?"}]}}
    original = deepcopy(row)
    assert report_module._reading_title(row, report_module._LABELS["en"]) == "What does the article say?"
    assert row == original


def test_unfinished_live_index_counts_are_explicit_lower_bounds():
    overview = {"run_id": RID, "name": "Real work", "status": "failed", "finalized": False,
                "updated_at": "now", "sources": [], "stages": []}
    page = {"run_id": RID, "target": "sft", "offset": 0, "rows": [], "finalized": False,
            "total": None, "summary": {"indexing": True, "quarantined": 160}}
    document = report_module.render_report_html(overview, page, language="en")
    assert "<strong>≥ 160</strong>" in document


def test_index_damage_rebuilds_from_immutable_formal_records(tmp_path):
    app, run, _, _ = fixture_run(tmp_path, [cot()])
    assert app.samples(RID, "cot")["rows"][0]["training_body"]["answer"] == "Real answer 0."
    index = next((run / "reports" / ".indexes").glob("*.jsonl"))
    index.write_text('{"answer":"FAKE_CACHE_ANSWER"}\n', encoding="utf-8")
    page = ready_page(app)
    assert page["rows"][0]["training_body"]["answer"] == "Real answer 0."
    assert "FAKE_CACHE_ANSWER" not in app.html(RID, "cot")


def test_index_summary_damage_does_not_invent_counts(tmp_path):
    app, run, _, _ = fixture_run(tmp_path, [cot()])
    app.samples(RID, "cot")
    receipt = next((run / "reports" / ".indexes").glob("*.receipt.json"))
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["summary"]["eligible"] = 1000000
    write_json(receipt, value)
    # A ready worker remembers its checked receipt; explicit preparation retry
    # revalidates the changed derived receipt against the bound footer.
    app.retry(RID, "cot")
    assert ready_page(app)["summary"]["eligible"] == 1


def test_tampered_formal_artifact_never_falls_back_to_live(tmp_path):
    app, run, _, _ = fixture_run(tmp_path, [cot()])
    (run / "artifacts" / "cot.jsonl").write_text('{"answer":"bad"}\n', encoding="utf-8")
    page = failed_page(app)
    assert app.overview(RID)["artifact_state"] == "invalid"
    assert page["preparation"]["error"] == "artifact_integrity_error"
    assert page["rows"] == [] and page["total"] is None


def test_valid_manifest_with_wrong_training_body_fails_index_join(tmp_path):
    app, run, recipe, _ = fixture_run(tmp_path, [cot()])
    (run / "artifacts" / "cot.jsonl").write_text(json.dumps({"question": [], "reasoning": "fake", "answer": "wrong"}) + "\n", encoding="utf-8")
    seal(run, recipe)
    assert failed_page(app)["preparation"]["error"] == "report_training_record_mismatch"


def test_nonfinal_stage_outputs_are_never_marked_as_exported(tmp_path):
    row = cot()
    app, run, _, state = fixture_run(tmp_path, [row], status="running")
    state["stages"]["cot"]["status"] = "running"
    write_json(run / "state.json", state)
    (run / "stage-results").mkdir()
    (run / "stage-results" / ".cot.live.jsonl").write_text(json.dumps({"item_index": 0, "record": row}) + "\n", encoding="utf-8")
    overview, page = app.overview(RID), app.samples(RID, "cot")
    assert overview["artifact_state"] == "provisional" and not overview["finalized"]
    assert not page["finalized"] and not page["rows"][0]["released"]
    assert page["total"] is None and page["source_kind"] == "stage_live"
    assert app.samples(RID, "cot", offset=5)["rows"] == []
    document = app.html(RID, "cot", language="en")
    assert "Live records · Not sealed" in document and "Unreleased candidate" in document
    assert "Node passed" in document and "Eligible</span>" not in document


def test_no_complete_output_keeps_real_question_not_old_history(tmp_path):
    row = {"id": "empty", "status": "quarantined", "reason": "native_answer_missing_after_retry",
           "question": [{"role": "user", "content": "The exact original question"}], "messages": [],
           "native_regeneration_history": [{"original_record": cot(answer="OLD_WRONG_ANSWER")}],
           "native_answer_terminal": {"reason_code": "model_visible_output_missing", "attempts": 2}}
    app, _, _, _ = fixture_run(tmp_path, [row])
    sample = app.samples(RID, "cot")["rows"][0]
    assert sample["training_body"] is None and sample["training_body_sha256"] is None
    assert sample["record"]["question"] == row["question"] and not sample["released"]
    document = app.html(RID, "cot", language="en")
    assert "No complete output yet" in document and "The exact original question" in document
    assert "OLD_WRONG_ANSWER" not in document


def test_markdown_is_safe_with_literal_multimodal_and_tool_roles(tmp_path):
    messages = [{"role": "system", "content": "Real system role"},
                {"role": "user", "content": "<script>alert('x')</script>\n![remote](https://example.invalid/track.png)\n[bad](javascript:alert(1))\n\n**bold**"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "tool1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "tool1", "content": [{"type": "image_url", "image_url": {"url": "https://example.invalid/private.png"}}]},
                {"role": "assistant", "content": "The full answer", "reasoning_content": "Literal visible training reasoning"}]
    row = {"id": "tool-row", "status": "eligible", "messages": messages}
    app, _, _, _ = fixture_run(tmp_path, [row], target="agent")
    assert app.samples(RID, "agent")["rows"][0]["training_body"]["messages"] == messages
    document = app.html(RID, "agent", language="en")
    assert "<script>" not in document and "<img" not in document and 'href="javascript:' not in document
    assert "&lt;script&gt;" in document and "<strong>bold</strong>" in document
    assert 'img-src &#x27;none&#x27;' not in document and "img-src 'none'" in document
    assert "tool_call_id" in document and "Literal visible training reasoning" in document


def test_sft_body_matches_actual_export_preferences_not_metadata_draft(tmp_path):
    row = {"id": "sft-1", "status": "eligible", "messages": [
        {"role": "user", "content": "Question"}, {"role": "assistant", "content": "Answer", "reasoning_content": "HIDDEN_DROPPED_REASONING"}]}
    app, _, _, _ = fixture_run(tmp_path, [row], target="sft", style="drop")
    body = app.samples(RID, "sft")["rows"][0]["training_body"]
    assert body["messages"][-1] == {"role": "assistant", "content": "Answer"}
    assert "HIDDEN_DROPPED_REASONING" not in app.html(RID, "sft")


def test_human_and_independent_evidence_are_explicit_and_body_bound(tmp_path):
    row = cot(review_repair={"mode": "human", "status": "accepted", "approved": True, "score": 1},
              independent_review={"status": "passed", "training_body_sha256": "stale"})
    app, _, _, _ = fixture_run(tmp_path, [row])
    sample = app.samples(RID, "cot")["rows"][0]
    assert sample["reviews"]["human"] and not sample["reviews"]["fact"]
    assert sample["reviews"]["independent"][0]["bound_to_current_body"] is False
    assert app.samples(RID, "cot")["summary"]["quality"]["independent_body_bound"] == 0
    assert "Not bound to this body; not a verified pass" in app.html(RID, "cot", language="en")


def test_overview_does_not_load_large_quality_issue_list(tmp_path, monkeypatch):
    app, _, _, _ = fixture_run(tmp_path, [cot()])
    actual = Path.read_text
    def guarded(path, *args, **kwargs):
        if path.name == "quality.json":
            raise AssertionError("Report must not load the million-row issue list")
        return actual(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    assert app.overview(RID)["finalized"]
    assert app.samples(RID, "cot")["total"] == 1


def test_recipe_identity_and_target_guard(tmp_path):
    app, run, recipe, _ = fixture_run(tmp_path, [cot()])
    with pytest.raises(ValueError, match="report_target_unavailable"):
        app.samples(RID, "sft")
    recipe["targets"].append("sft")
    write_json(run / "recipe.json", recipe)
    assert failed_page(app)["preparation"]["error"] == "report_manifest_context_mismatch"
    assert app.overview(RID)["artifact_state"] == "invalid"


def test_html_snapshot_reuses_page_and_rejects_cross_run_or_manifest(tmp_path, monkeypatch):
    app, _, _, _ = fixture_run(tmp_path, [cot()])
    overview, page = app.overview(RID), app.samples(RID, "cot")
    def forbidden(*args, **kwargs):
        raise AssertionError("Already-read HTML must not reread records or summaries")
    monkeypatch.setattr(report_module.FilesystemWorkflowReportDriver, "samples", forbidden)
    monkeypatch.setattr(report_module.FilesystemWorkflowReportDriver, "overview", forbidden)
    assert "Real answer 0" in app.html_snapshot(overview, page, language="en")
    changed = deepcopy(page)
    changed["run_id"] = "b" * 32
    with pytest.raises(ValueError, match="report_snapshot_changed"):
        app.html_snapshot(overview, changed)
    changed = deepcopy(page)
    changed["manifest_sha256"] = "different"
    with pytest.raises(ValueError, match="report_snapshot_changed"):
        app.html_snapshot(overview, changed)


def test_many_distinct_rejection_reasons_keep_bounded_summary_without_losing_rows(tmp_path):
    rows = [cot(i, status="quarantined", reason=f"Unique model feedback {i}") for i in range(180)]
    app, _, _, _ = fixture_run(tmp_path, rows)
    page = ready_page(app, offset=175, limit=10)
    assert page["total"] == 180 and len(page["rows"]) == 5
    assert len(page["summary"]["reasons"]) == 100
    assert page["summary"]["other_reason_count"] == 80 and page["summary"]["reason_categories_truncated"]
    assert page["rows"][-1]["reason"] == "Unique model feedback 179"


@pytest.mark.parametrize("target", ["cpt", "gsm8k", "dpo", "orpo", "rlaif"])
def test_other_dataset_types_display_actual_training_schema_and_saved_feedback(tmp_path, target):
    def check(score, reason):
        return {"keep": score >= 4, "grounded": score >= 4, "reasoning_valid": score >= 4,
                "correctness": score, "reason": reason, "scores": {key: score for key in (
                    "correctness", "reasoning", "grounding", "instruction", "safety")}}
    row = {"id": "actual-other", "status": "eligible", "text": "Actual corpus text.",
           "question": "What is 2+2?", "answer": "4", "prompt": [{"role": "user", "content": "What is 2+2?"}],
           "chosen": [{"role": "assistant", "content": "4"}], "rejected": [{"role": "assistant", "content": "5"}],
           "preference": {"dimension": "correctness", "minimum_gap": 2,
                          "chosen": check(5, "Correct saved answer"), "rejected": check(1, "Wrong saved answer")},
           "rlaif": {"criterion": "correctness", "judge": "jev", "chosen_feedback": "Correct saved answer",
                     "rejected_feedback": "Wrong saved answer"}}
    app, _, _, _ = fixture_run(tmp_path, [row], target=target)
    sample = app.samples(RID, target)["rows"][0]
    assert sample["training_body"] == preferred_training_record(target, row)
    document = app.html(RID, target, language="en")
    if target == "rlaif":
        assert "Preference rank 1" in document and "AI score 5" in document
        assert "Wrong saved answer" in document and "Criterion" not in sample["training_body"]
    assert sample["reviews"]["independent"] == []


def test_cold_verification_returns_immediately_and_coalesces_concurrent_requests(tmp_path, monkeypatch):
    app, _, _, _ = fixture_run(tmp_path, [cot()], warm=False)
    original = report_module.verified_snapshot
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(run):
        calls.append(run)
        entered.set()
        assert release.wait(5)
        return original(run)
    monkeypatch.setattr(report_module, "verified_snapshot", blocked)
    try:
        start = time.monotonic()
        overview = app.overview(RID)
        assert time.monotonic() - start < .5
        assert entered.wait(2) and overview["artifact_state"] == "indexing"
        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = list(pool.map(lambda _: app.samples(RID, "cot"), range(24)))
        assert len(calls) == 1
        assert all(page["indexing"] and page["total"] is None and page["rows"] == [] for page in pages)
        assert all(not page["finalized"] and page["preparation"]["phase"] == "verification" for page in pages)
        document = app.html_snapshot(overview, pages[0], language="en")
        assert "Preparing report" in document and "ShuJian Cube" in document
        assert "Live records" not in document and "Formally exported</span><strong>0" not in document
    finally:
        release.set()
    assert ready_page(app)["rows"][0]["training_body"]["answer"] == "Real answer 0."


def test_cold_index_is_background_and_single_scan_for_concurrent_readers(tmp_path, monkeypatch):
    app, _, _, _ = fixture_run(tmp_path, [cot(i) for i in range(12)], warm=False)
    # Complete only the shared verification, without building any target index.
    until = time.monotonic() + 5
    while not app.overview(RID)["finalized"]:
        assert time.monotonic() < until
        time.sleep(.01)
    original = report_module.iter_json_records
    entered, release, calls = threading.Event(), threading.Event(), []
    def blocked(path, **kwargs):
        calls.append(path)
        entered.set()
        assert release.wait(5)
        yield from original(path, **kwargs)
    monkeypatch.setattr(report_module, "iter_json_records", blocked)
    try:
        start = time.monotonic()
        first = app.samples(RID, "cot", limit=3)
        assert time.monotonic() - start < .5 and entered.wait(2)
        assert first["indexing"] and first["finalized"] and first["total"] is None
        assert first["preparation"]["phase"] == "indexing"
        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = list(pool.map(lambda _: app.samples(RID, "cot", offset=6, limit=3), range(20)))
        assert len(calls) == 1 and all(page["rows"] == [] for page in pages)
    finally:
        release.set()
    page = ready_page(app, offset=6, limit=3)
    assert len(calls) == 1 and [row["id"] for row in page["rows"]] == ["6", "7", "8"]


def test_cross_process_cache_lock_never_blocks_ui_or_reports_zero_samples(tmp_path):
    app, run, _, _ = fixture_run(tmp_path, [cot()], warm=False)
    directory = run / "reports" / ".indexes"
    directory.mkdir(parents=True)
    lock = FileLock(str(directory / "cot.lock"), timeout=0)
    with lock:
        until = time.monotonic() + 5
        while True:
            start = time.monotonic()
            page = app.samples(RID, "cot")
            assert time.monotonic() - start < .5
            if page["preparation"]["status"] == "waiting":
                break
            assert time.monotonic() < until
            time.sleep(.01)
        assert page["indexing"] and page["total"] is None and page["rows"] == []
        assert page["preparation"]["error"] is None
    assert ready_page(app)["total"] == 1


def test_background_failure_stays_explicit_until_read_only_retry(tmp_path, monkeypatch):
    app, run, _, _ = fixture_run(tmp_path, [cot()], warm=False)
    original, calls = report_module.iter_json_records, []
    original_records = (run / "artifacts" / "cot.records.json").read_bytes()
    def fail_once(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise ValueError("injected_report_reader_failure")
        yield from original(path, **kwargs)
    monkeypatch.setattr(report_module, "iter_json_records", fail_once)
    page = failed_page(app)
    assert not page["indexing"] and page["rows"] == [] and page["total"] is None
    assert page["preparation"] == {"status": "failed", "phase": "indexing", "error": "injected_report_reader_failure", "retryable": True}
    assert app.samples(RID, "cot")["preparation"]["status"] == "failed" and len(calls) == 1
    document = app.html_snapshot(app.overview(RID), page, language="en")
    assert "Report preparation failed" in document and "No complete output yet" not in document
    app.retry(RID, "cot")
    assert ready_page(app)["total"] == 1 and len(calls) == 2
    assert (run / "artifacts" / "cot.records.json").read_bytes() == original_records


def test_completed_index_survives_worker_restart_without_source_rescan(tmp_path, monkeypatch):
    app, _, _, _ = fixture_run(tmp_path, [cot()])
    monkeypatch.setattr(report_module, "REPORT_JOBS", ReportJobs())
    def forbidden(*args, **kwargs):
        raise AssertionError("Durable completed index must be reused after restart")
    monkeypatch.setattr(report_module, "iter_json_records", forbidden)
    page = ready_page(app)
    assert page["total"] == 1 and page["rows"][0]["training_body"]["answer"] == "Real answer 0."


def test_worker_capacity_is_bounded_and_queued_work_resumes():
    jobs = ReportJobs(concurrency=1, capacity=2)
    release, entered, calls = threading.Event(), threading.Event(), []
    def blocked():
        calls.append("first")
        entered.set()
        assert release.wait(5)
        return "done"
    key = ("index", "fixture", "cot")
    try:
        jobs.request(key, blocked, phase="indexing")
        assert entered.wait(2)
        assert jobs.request(("index", "fixture", "sft"), lambda: "second", phase="indexing")["status"] == "queued"
        assert jobs.request(("index", "other", "sft"), lambda: "third", phase="indexing")["phase"] == "capacity"
        for _ in range(20):
            assert jobs.request(key, blocked, phase="indexing")["status"] == "running"
        assert calls == ["first"]
    finally:
        release.set()
    until = time.monotonic() + 5
    while jobs.request(("index", "fixture", "sft"), lambda: "second", phase="indexing")["status"] != "ready":
        assert time.monotonic() < until
        time.sleep(.01)


def test_reading_headers_use_real_question_and_keep_full_identifiers_in_metadata(tmp_path):
    identifier = "d" * 64
    app, _, _, _ = fixture_run(tmp_path, [cot(id=identifier)])
    document = app.html(RID, "cot", language="en")
    assert "<h3>Real question 0</h3>" in document
    assert 'title="' + identifier + '"' in document and '"record_id": "' not in document
    assert '&quot;record_id&quot;: &quot;' + identifier + '&quot;' in document
    assert "Eligible</span>" not in document
    row = app.samples(RID, "cot")["rows"][0]
    assert "SHA256 " + row["training_body_sha256"][:12] + "</summary>" in document
    assert row["training_body_sha256"] in document


def test_cold_high_offset_jsonl_seek_index_is_only_built_on_worker(tmp_path, monkeypatch):
    app, _, _, _ = fixture_run(tmp_path, [cot(i) for i in range(2000)])
    original, entered, release = report_module.read_rows, threading.Event(), threading.Event()
    calls = []
    def blocked(path, offset, limit):
        if offset:
            calls.append(threading.current_thread().name)
            entered.set()
            assert release.wait(5)
        return original(path, offset, limit)
    monkeypatch.setattr(report_module, "read_rows", blocked)
    try:
        start = time.monotonic()
        page = app.samples(RID, "cot", offset=1989, limit=3)
        assert time.monotonic() - start < .5 and entered.wait(2)
        assert page["indexing"] and page["preparation"]["phase"] == "reading"
        assert page["rows"] == [] and page["total"] is None
    finally:
        release.set()
    assert [row["id"] for row in ready_page(app, offset=1989, limit=3)["rows"]] == ["1989", "1990", "1991"]
    assert calls == ["workflow-report-reader"]


def test_embedded_report_uses_identical_safe_body_scoped_styles_and_no_extra_disk_reads(tmp_path, monkeypatch):
    content = "**Original answer**\n\n<script>alert('x')</script>\n![external](https://example.invalid/track.png)"
    app, _, _, _ = fixture_run(tmp_path, [cot(answer=content)])
    overview, page = app.overview(RID), app.samples(RID, "cot")
    download = app.html_snapshot(overview, page, language="en")
    def forbidden(*args, **kwargs):
        raise AssertionError("Snapshots must not reread files or the task")
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(report_module.FilesystemWorkflowReportDriver, "samples", forbidden)
    monkeypatch.setattr(report_module.FilesystemWorkflowReportDriver, "overview", forbidden)
    embedded = app.html_embedded_snapshot(overview, page, language="en")
    assert '<section class="sj-workflow-report" data-user-content="true"' in embedded
    assert '<script>' not in embedded and '<img' not in embedded and '<iframe' not in embedded
    assert '&lt;script&gt;' in embedded and '<strong>Original answer</strong>' in embedded
    assert '<body>' not in embedded and '<html' not in embedded
    assert "default-src 'none'" in download and "img-src 'none'" in download
    css = embedded.split('<style>', 1)[1].split('</style>', 1)[0]
    for rule in css.split('}'):
        if not rule.strip():
            continue
        header = rule.split('{', 1)[0]
        assert all(selector.strip().startswith('.sj-workflow-report') for selector in header.split(','))
    assert '.sj-workflow-report{margin:0;' in css
    assert '.sj-workflow-report nav{' in css and '.sj-workflow-report,.sj-workflow-report *{' in css
    start = embedded.index('<section class="sj-workflow-report"')
    embedded_body = embedded[embedded.index('>', start) + 1:embedded.rfind('</section>')]
    download_body = download.split('<body>', 1)[1].rsplit('</body>', 1)[0]
    assert embedded_body == download_body


@pytest.mark.parametrize('css', ['@media screen{nav{color:red}}', '@import url(example);', 'nav{color:red; h1{display:none}}'])
def test_embedded_css_rejects_unsupported_or_nested_global_rules(css):
    with pytest.raises(ValueError, match='report_css_'):
        report_module.scope_report_stylesheet(css)


def test_embedded_training_content_is_preserved_by_console_localization(tmp_path):
    from lib.presentation.streamlit.i18n import translate_markup
    row = cot(question=[{"role": "user", "content": "工作流"}], answer="下一页")
    app, _, _, _ = fixture_run(tmp_path, [row])
    overview, page = app.overview(RID), app.samples(RID, "cot")
    embedded = app.html_embedded_snapshot(overview, page, language="en")
    assert "<p>工作流</p>" in embedded and "<p>下一页</p>" in embedded
    assert "ShuJian Cube" in embedded and "Full conversation" not in embedded
    assert translate_markup(embedded, "en") == embedded
    assert app.samples(RID, "cot")["rows"][0]["training_body"]["answer"] == "下一页"
