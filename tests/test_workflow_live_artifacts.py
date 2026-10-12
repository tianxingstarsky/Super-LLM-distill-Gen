"""Live node completion is readable without trusting unfinished release files."""
import json
import os
from pathlib import Path

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure import workflow_live_artifacts as live
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
from lib.io_utils import atomic_json


_CPT_PREVIEW_SCRIPT = '''
from copy import deepcopy
import streamlit as st
from lib.presentation.streamlit.i18n import install_streamlit_localization
from lib.presentation.streamlit.workflow_live_artifacts_view import render_live_artifacts

install_streamlit_localization()
recipe = {"version": 19, "targets": st.session_state.get("fixture-targets", ["sft"]),
          "source_cleaning": True, "qa_director": {"enabled": True}}
saved_state = {"status": "running", "stages": {"cpt": {
    "label": "资料清洗", "status": "completed", "done": 1, "total": 1}}}
original = deepcopy(saved_state)
class Application:
    def live_artifacts(self, run_id, **kwargs):
        return {"stages": [{"stage": "cpt", "written": 1, "processed": 1,
                             "eligible": 1}], "rows": [], "selected_stage": "cpt"}
render_live_artifacts(Application(), "fixture", state=saved_state, recipe=recipe)
assert saved_state == original
st.session_state["fixture-old-state"] = saved_state
'''


@pytest.mark.parametrize("targets", [["sft"], ["cpt", "sft"]])
@pytest.mark.parametrize("language", ["zh", "en"])
def test_cpt_live_preview_keeps_identity_and_explains_actual_export_without_rewriting_history(targets, language):
    from streamlit.testing.v1 import AppTest

    ui = AppTest.from_string(_CPT_PREVIEW_SCRIPT)
    ui.session_state["fixture-targets"] = targets
    ui.session_state["ui_language"] = language
    ui.run()
    assert not ui.exception
    label = "CPT 语料处理" if language == "zh" else "CPT corpus processing"
    assert ui.dataframe[0].value.iloc[0]["节点" if language == "zh" else "Stage"] == label
    if language == "en":
        assert "Review passed" in ui.dataframe[0].value.columns
    assert ui.selectbox[0].options[-1] == label
    expected = ("本次导出 CPT 语料，合格正文同时供下游节点复用。" if "cpt" in targets else
                "本次不导出 CPT，合格正文仅供下游节点复用。")
    if language == "en":
        expected = ("Export CPT corpus and reuse accepted text in downstream nodes."
                    if "cpt" in targets else
                    "Do not export CPT in this run. Reuse accepted text only in downstream nodes.")
    assert expected in [caption.value for caption in ui.caption]
    assert ui.session_state["fixture-old-state"]["stages"]["cpt"]["label"] == "资料清洗"


@pytest.fixture(autouse=True)
def empty_cache():
    live._CACHE.clear()
    yield
    live._CACHE.clear()


def state(**stages):
    return {"status": "running", "updated_at": "2026-10-11T04:00:00+00:00", "stages": stages}


def write(run, stage, rows, *, pending=False):
    directory = run / "stage-results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (f".{stage}.pending" if pending else f"{stage}.jsonl")
    path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in rows))
    return path


def test_pending_completed_rows_are_visible_without_a_final_manifest(tmp_path):
    path = write(tmp_path, "sft", [{"id": "first", "status": "eligible", "answer": "已完成"}], pending=True)
    with path.open("ab") as handle:
        handle.write('{"id":"second","status":"eligible","answer":"还在'.encode())
    running = state(sft={"status": "running", "done": 1, "outputs": 1})
    first = live.live_artifacts(tmp_path, running)
    assert first["rows"] == [{"id": "first", "status": "eligible", "answer": "已完成"}]
    assert first["source_kind"] == "stage_pending" and not first["finalized"]
    assert first["stages"][0]["written"] == 1 and first["stages"][0]["partial_line"]
    assert not (tmp_path / "artifacts" / "manifest.json").exists()
    with path.open("ab") as handle:
        handle.write('写入"}\n'.encode())
    second = live.live_artifacts(tmp_path, running, stage="sft")
    assert [row["id"] for row in second["rows"]] == ["first", "second"]
    assert second["stages"][0]["written"] == 2
    assert not second["stages"][0]["partial_line"]


def test_counters_distinguish_source_ready_candidates_review_and_quarantine(tmp_path):
    write(tmp_path, "ingest", [{"id": "input", "status": "ready"}])
    write(tmp_path, "review", [
        {"id": "kept", "status": "eligible", "review_repair": {"status": "accepted"}},
        {"id": "isolated", "status": "quarantined", "review_repair": {"status": "rejected"}},
        {"id": "pending", "status": "eligible"},
    ], pending=True)
    snapshot = live.live_artifacts(tmp_path, state(ingest={"status": "completed"}, review={"status": "running"}))
    by_stage = {row["stage"]: row for row in snapshot["stages"]}
    assert by_stage["ingest"]["ready"] == 1 and by_stage["ingest"]["eligible"] == 0
    assert by_stage["review"]["eligible"] == 2 and by_stage["review"]["quarantined"] == 1
    assert by_stage["review"]["reviewed"] == 2
    assert by_stage["review"]["review_accepted"] == by_stage["review"]["unreviewed"] == 1
    assert snapshot["selected_stage"] == "review" and not snapshot["finalized"]


def test_tail_is_live_while_cold_large_index_advances_only_a_bounded_window(tmp_path, monkeypatch):
    path = write(tmp_path, "sft", [{"id": str(index), "status": "eligible", "answer": "x" * 100}
                                   for index in range(300)])
    monkeypatch.setattr(live, "SCAN_BYTES", 1024)
    running = state(sft={"status": "running", "done": 300, "outputs": 300})
    first = live.live_artifacts(tmp_path, running, limit=3)
    assert first["stages"][0]["indexing"]
    assert first["stages"][0]["scanned_bytes"] == 1024
    assert first["stages"][0]["outputs"] == 300 and first["stages"][0]["written"] < 300
    assert [row["id"] for row in first["rows"]] == ["297", "298", "299"]
    second = live.live_artifacts(tmp_path, running, limit=3)
    assert second["stages"][0]["scanned_bytes"] == 2048
    assert path.stat().st_size > second["stages"][0]["scanned_bytes"]
    for _ in range(100):
        snapshot = live.live_artifacts(tmp_path, running)
        if not snapshot["stages"][0]["indexing"]:
            break
    assert snapshot["stages"][0]["written"] == 300


def test_warm_poll_does_not_reread_complete_file_and_append_is_incremental(tmp_path, monkeypatch):
    path = write(tmp_path, "sft", [{"id": str(index), "status": "eligible"} for index in range(100)])
    running = state(sft={"status": "running"})
    live.live_artifacts(tmp_path, running)
    total_read = []
    original = live._open
    class Measured:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def read(self, size=-1):
            assert size >= 0, "Live polling must never issue an unbounded read"
            payload = self.handle.read(size)
            total_read.append(len(payload))
            return payload
        def __getattr__(self, name):
            return getattr(self.handle, name)
    monkeypatch.setattr(live, "_open", lambda path: (Measured(original(path)[0]), path.stat()))
    for _ in range(12):
        assert live.live_artifacts(tmp_path, running)["stages"][0]["written"] == 100
    assert sum(total_read) == 0
    with path.open("ab") as handle:
        handle.write(b'{"id":"new","status":"eligible"}\n')
    appended = live.live_artifacts(tmp_path, running)
    assert appended["stages"][0]["written"] == 101
    assert appended["rows"][-1]["id"] == "new"
    assert sum(total_read) < 1024


def test_atomic_stage_promotion_preserves_cached_cursor(tmp_path, monkeypatch):
    path = write(tmp_path, "sft", [{"id": str(index), "status": "eligible"} for index in range(50)], pending=True)
    monkeypatch.setattr(live, "SCAN_BYTES", 300)
    running = state(sft={"status": "running"})
    first = live.live_artifacts(tmp_path, running)
    path.replace(path.with_name("sft.jsonl"))
    second = live.live_artifacts(tmp_path, state(sft={"status": "completed"}))
    assert first["stages"][0]["scanned_bytes"] == 300
    assert second["stages"][0]["scanned_bytes"] == 600
    assert second["source_kind"] == "stage_result" and not second["stages"][0]["complete"]


@pytest.mark.parametrize("replacement", ["atomic", "truncate", "same_size"])
def test_rewrite_never_retains_old_rows_or_counts(tmp_path, replacement):
    path = write(tmp_path, "sft", [{"id": "old", "status": "eligible"}, {"id": "old-two", "status": "eligible"}])
    running = state(sft={"status": "running"})
    live.live_artifacts(tmp_path, running)
    payload = b'{"id":"new","status":"quarantined"}\n'
    if replacement == "atomic":
        temporary = path.with_name("replacement")
        temporary.write_bytes(payload)
        temporary.replace(path)
    elif replacement == "same_size":
        payload = payload[:-1] + b" " * (path.stat().st_size - len(payload)) + b"\n"
        path.write_bytes(payload)
    else:
        path.write_bytes(payload)
    snapshot = live.live_artifacts(tmp_path, running)
    assert snapshot["stages"][0]["written"] == snapshot["stages"][0]["quarantined"] == 1
    assert snapshot["stages"][0]["eligible"] == 0
    assert snapshot["rows"] == [{"id": "new", "status": "quarantined"}]


def test_symlink_hardlink_and_unknown_node_are_rejected(tmp_path):
    path = write(tmp_path, "sft", [{"id": "private"}])
    running = state(sft={"status": "running"})
    live.live_artifacts(tmp_path, running)
    alias = path.with_name("alias.jsonl")
    os.link(path, alias)
    with pytest.raises(ValueError, match="live_artifact_storage_invalid"):
        live.live_artifacts(tmp_path, running)
    alias.unlink()
    with pytest.raises(ValueError, match="unknown_workflow_stage"):
        live.live_artifacts(tmp_path, running, stage="../sft")
    try:
        path.rename(alias)
        path.symlink_to(alias)
    except OSError:
        pytest.skip("This Windows session cannot create symlinks")
    with pytest.raises(ValueError, match="live_artifact_storage_invalid"):
        live.live_artifacts(tmp_path, running)


def test_limits_invalid_lines_oversized_rows_and_incomplete_output_remain_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "SCAN_BYTES", 64)
    monkeypatch.setattr(live, "LINE_BYTES", 80)
    path = write(tmp_path, "sft", [], pending=True)
    path.write_bytes(b'{"answer":"' + b"x" * 150 + b'"}\ninvalid-json\n' + b'{"id":"valid"}\n')
    running = state(sft={"status": "running"})
    for _ in range(5):
        snapshot = live.live_artifacts(tmp_path, running, limit=10000)
        assert all(entry.cost < 4096 for entry in live._CACHE.values())
    assert snapshot["stages"][0]["written"] == 1
    assert snapshot["stages"][0]["invalid"] == snapshot["stages"][0]["oversized"] == 1
    assert snapshot["rows"] == [{"id": "valid"}]


def test_application_driver_cannot_turn_node_preview_into_a_final_artifact(tmp_path):
    run_id = "a" * 32
    run = tmp_path / "workflows" / run_id
    write(run, "sft", [{"id": "candidate", "status": "eligible"}], pending=True)
    atomic_json(run / "state.json", state(sft={"status": "running"}))
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, tmp_path))
    assert app.live_artifacts(run_id, stage="sft")["rows"][0]["id"] == "candidate"
    assert app.artifact_preview(run_id, "sft") == []
    artifact = run / "artifacts" / "sft.jsonl"
    artifact.parent.mkdir()
    artifact.write_text('{"messages":[]}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete_artifact_manifest"):
        app.artifact_preview(run_id, "sft")
    assert not app.live_artifacts(run_id)["finalized"]


def test_selected_stage_and_completed_cot_sft_lineage_preview(tmp_path):
    write(tmp_path, "sft", [{"id": "candidate", "answer": "old"}])
    final = write(tmp_path, "sft-cot-final", [{"id": "candidate", "answer": "final", "reasoning": "reasoned"}])
    write(tmp_path, "cot", [{"id": "candidate", "reasoning": "reasoned"}])
    finished = state(sft={"status": "completed"}, cot={"status": "completed"})
    snapshot = live.live_artifacts(tmp_path, finished, stage="sft")
    assert final.exists() and snapshot["rows"][0]["answer"] == "final"
    assert snapshot["stages"][0]["complete"] and not snapshot["finalized"]


def test_memory_cache_evicts_old_runs_and_mutating_response_cannot_corrupt_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "CACHE_ENTRIES", 2)
    running = state(sft={"status": "running"})
    for number in range(3):
        run = tmp_path / str(number)
        write(run, "sft", [{"id": str(number), "messages": [{"content": "private"}]}])
        snapshot = live.live_artifacts(run, running)
        snapshot["rows"][0]["messages"][0]["content"] = "changed"
        assert live.live_artifacts(run, running)["rows"][0]["messages"][0]["content"] == "private"
    assert len(live._CACHE) == 2


def journal(run, stage, records):
    directory = run / "stage-results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f".{stage}.live.jsonl"
    path.write_bytes(b"".join(json.dumps({"item_index": index, "record": row}, ensure_ascii=False).encode() + b"\n"
                              for index, row in records))
    return path


def test_completion_order_journal_previews_fast_items_before_slow_input_zero(tmp_path):
    write(tmp_path, "sft", [], pending=True)
    journal(tmp_path, "sft", [(2, {"id": "fast-two", "status": "eligible"}),
                              (1, {"id": "fast-one", "status": "quarantined"})])
    snapshot = live.live_artifacts(tmp_path, state(sft={"status": "running", "outputs": 2}))
    assert snapshot["source_kind"] == "stage_live"
    assert [row["id"] for row in snapshot["rows"]] == ["fast-two", "fast-one"]
    assert snapshot["stages"][0]["written"] == 2
    assert snapshot["stages"][0]["eligible"] == snapshot["stages"][0]["quarantined"] == 1
    assert all("item_index" not in row and "record" not in row for row in snapshot["rows"])
    assert not snapshot["finalized"] and not snapshot["stages"][0]["complete"]


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_interrupted_stage_retains_real_completion_order_rows_and_ignores_partial_wrapper(tmp_path, status):
    write(tmp_path, "sft", [{"id": "stale", "status": "eligible"}])
    write(tmp_path, "sft", [], pending=True)
    path = journal(tmp_path, "sft", [(3, {"id": "completed", "status": "eligible"})])
    with path.open("ab") as handle:
        handle.write(b'{"item_index":4,"record":{"id":"unfinished"')
    snapshot = live.live_artifacts(tmp_path, state(sft={"status": status}))
    assert snapshot["source_kind"] == "stage_live"
    assert snapshot["rows"] == [{"id": "completed", "status": "eligible"}]
    assert snapshot["stages"][0]["written"] == 1 and snapshot["stages"][0]["partial_line"]
    assert not snapshot["finalized"]


def test_finished_stage_switches_journal_to_canonical_without_double_counting(tmp_path):
    first, second = {"id": "zero", "status": "eligible"}, {"id": "one", "status": "eligible"}
    path = journal(tmp_path, "sft", [(1, second), (0, first)])
    write(tmp_path, "sft", [first, second])
    running = live.live_artifacts(tmp_path, state(sft={"status": "running"}))
    assert [row["id"] for row in running["rows"]] == ["one", "zero"]
    finished = live.live_artifacts(tmp_path, state(sft={"status": "completed"}))
    assert path.exists() and finished["source_kind"] == "stage_result"
    assert [row["id"] for row in finished["rows"]] == ["zero", "one"]
    assert finished["stages"][0]["written"] == finished["stages"][0]["eligible"] == 2
    assert finished["stages"][0]["complete"] and not finished["finalized"]


def test_restart_completion_journal_rebuilds_counts_and_cold_tail_unwraps_records(tmp_path, monkeypatch):
    path = journal(tmp_path, "sft", [(index, {"id": str(index), "status": "eligible", "answer": "x" * 100})
                                      for index in range(50)])
    monkeypatch.setattr(live, "SCAN_BYTES", 256)
    running = state(sft={"status": "running"})
    first = live.live_artifacts(tmp_path, running)
    assert first["stages"][0]["indexing"] and [row["id"] for row in first["rows"]] == ["47", "48", "49"]
    path.write_bytes(b'{"item_index":0,"record":{"id":"replayed","status":"quarantined"}}\n')
    restarted = live.live_artifacts(tmp_path, running)
    assert restarted["rows"] == [{"id": "replayed", "status": "quarantined"}]
    assert restarted["stages"][0]["written"] == restarted["stages"][0]["quarantined"] == 1
    assert restarted["stages"][0]["eligible"] == 0 and not restarted["stages"][0]["indexing"]


def test_invalid_completion_wrapper_does_not_become_a_training_record(tmp_path):
    path = journal(tmp_path, "sft", [(0, {"id": "valid", "status": "eligible"})])
    with path.open("ab") as handle:
        handle.write(b'{"item_index":-1,"record":{"id":"bad"}}\n')
        handle.write(b'{"item_index":1,"record":[{"id":"bad"}]}\n')
        handle.write(b'{"item_index":2,"record":{"id":"bad"},"other":true}\n')
    snapshot = live.live_artifacts(tmp_path, state(sft={"status": "running"}))
    assert snapshot["rows"] == [{"id": "valid", "status": "eligible"}]
    assert snapshot["stages"][0]["written"] == 1 and snapshot["stages"][0]["invalid"] == 3


@pytest.mark.parametrize("changed", ["attempt", "started_at", "round"])
def test_writer_generation_resets_same_inode_regrowth_with_unchanged_boundaries(tmp_path, changed):
    """A fast rebuild may be longer again before the browser's next poll."""
    path = journal(tmp_path, "sft", [])
    def wrap(index, row):
        return (json.dumps({"item_index": index, "record": row}, separators=(",", ":")) + "\n").encode()
    prefix = wrap(0, {"id": "first", "status": "eligible", "padding": "x" * 300})
    old_middle = wrap(1, {"id": "middle", "status": "eligible", "padding": "aaa"})
    new_middle = wrap(1, {"id": "middle", "status": "quarantined", "padding": ""})
    suffix = wrap(2, {"id": "last", "status": "eligible", "padding": "y" * 300})
    assert len(old_middle) == len(new_middle)
    path.write_bytes(prefix + old_middle + suffix)
    current = state(sft={"status": "running", "started_at": "first-start"})
    current.update(attempt=1, production={"round": 1})
    before = live.live_artifacts(tmp_path, current)
    assert before["stages"][0]["eligible"] == 3
    previous_inode = path.stat().st_ino
    path.write_bytes(prefix + new_middle + suffix + wrap(3, {"id": "new", "status": "eligible"}))
    assert path.stat().st_ino == previous_inode
    if changed == "attempt":
        current["attempt"] = 2
    elif changed == "started_at":
        current["stages"]["sft"]["started_at"] = "second-start"
    else:
        current["production"]["round"] = 2
    after = live.live_artifacts(tmp_path, current)
    assert after["stages"][0]["written"] == 4
    assert after["stages"][0]["eligible"] == 3
    assert after["stages"][0]["quarantined"] == 1
    assert all("generation" not in row for row in after["rows"])


def test_next_production_round_does_not_reindex_reused_completed_source_stage(tmp_path, monkeypatch):
    write(tmp_path, "cpt", [{"id": str(index), "status": "ready", "text": "source" * 100}
                            for index in range(10)])
    monkeypatch.setattr(live, "SCAN_BYTES", 256)
    current = state(cpt={"status": "completed", "started_at": "cleaned-once"})
    current.update(attempt=1, production={"round": 1})
    first = live.live_artifacts(tmp_path, current)
    assert first["stages"][0]["scanned_bytes"] == 256
    current["production"]["round"] = 2
    second = live.live_artifacts(tmp_path, current)
    assert second["stages"][0]["scanned_bytes"] == 512
