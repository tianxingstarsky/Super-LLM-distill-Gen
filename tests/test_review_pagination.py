"""Large review queues filter globally and retain only the requested payloads."""
from __future__ import annotations

import hashlib
import json

import pytest

from lib.application.corpus_review_service import CorpusReviewApplication
from lib.application.preference_review_service import PreferenceReviewApplication
from lib.application.sft_review_service import SftReviewApplication
from lib.domain.corpus_review import corpus_review_record
from lib.domain.preference_review import review_record
from lib.domain.sft_review import sft_review_record
from lib.infrastructure.corpus_review_driver import FilesystemCorpusReviewDriver
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.preference_review_driver import FilesystemPreferenceReviewDriver
from lib.infrastructure.sft_review_driver import FilesystemSftReviewDriver
from lib.infrastructure import review_index as index_adapter


def fixture_app(root, target, count=45):
    run_id = "b" * 32
    run = root / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    rows, records, events = [], [], []
    for index in range(count):
        decision = "approved" if index < 20 else "skipped"
        if target == "cpt":
            row = {"text": f"Source paragraph {index}"}
            event = corpus_review_record(row, decision=decision, reviewer="test")
        elif target == "sft":
            row = {"messages": [{"role": "user", "content": f"Question {index}"},
                                {"role": "assistant", "content": f"Answer {index}"}]}
            event = sft_review_record(row, decision=decision, reviewer="test")
        else:
            row = {"prompt": [{"role": "user", "content": f"Question {index}"}],
                   "chosen": [{"role": "assistant", "content": f"Answer {index}"}],
                   "rejected": [{"role": "assistant", "content": f"Weak answer {index}"}]}
            event = review_record(row, decision=decision, reviewer="test", reviewed_at="now", target=target)
        rows.append(row)
        records.append({**row, "status": "eligible", "source_id": f"source-{index}", "location": index})
        if index < 23:
            events.append(event)
    artifact = artifacts / f"{target}.jsonl"
    artifact.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    records_path = artifacts / f"{target}.records.json"
    records_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    (artifacts / "manifest.json").write_text(json.dumps({"status": "complete", "sha256": {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (artifact, records_path)
    }}), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({"id": run_id, "name": "Review pagination",
        "status": "completed", "targets": [target], "created_at": "2026-09-27T00:00:00Z"}), encoding="utf-8")
    audit = run / "human-review"
    audit.mkdir()
    name = {"cpt": "cpt.json", "sft": "sft.json", "dpo": "preferences.json", "orpo": "preferences-orpo.json"}[target]
    key = "sample_id" if target in {"cpt", "sft"} else "pair_id"
    (audit / name).write_text(json.dumps({"version": 1, "target": target, "events": events,
                                         "current": {event[key]: event for event in events}}), encoding="utf-8")
    if target == "cpt":
        app = CorpusReviewApplication(FilesystemCorpusReviewDriver(root))
    elif target == "sft":
        app = SftReviewApplication(FilesystemSftReviewDriver(root))
    else:
        app = PreferenceReviewApplication(FilesystemPreferenceReviewDriver(root, target=target))
    return app, run_id, rows


@pytest.mark.parametrize("target", ["cpt", "sft", "dpo", "orpo"])
def test_status_filter_precedes_pagination_and_keeps_global_counts(tmp_path, target):
    app, run_id, rows = fixture_app(tmp_path, target)
    # The old whole-dataset readers have been removed from the shared adapter.
    method = "_read_rows" if target in {"cpt", "sft"} else "_read_pairs"
    assert not hasattr(app._driver, method)
    result = app.queue(run_id, decision="pending", limit=20)
    key = "row" if target in {"cpt", "sft"} else "pair"
    assert result["items"][0][key] == rows[23]
    assert result["total"] == 45 and result["matched"] == 22
    assert result["counts"] == {"approved": 20, "skipped": 3, "rejected": 0, "pending": 22}
    tail = app.queue(run_id, decision="pending", offset=20, limit=20)
    assert [item[key] for item in tail["items"]] == rows[43:]
    if target in {"sft", "cpt"}:
        assert tail["items"][0]["evidence"]["source_id"] == "source-43"
        assert tail["items"][1]["evidence"]["location"] == 44
    assert app.queue(run_id, decision="approved", offset=20)["items"] == []
    assert len(app.queue(run_id, decision="skipped")["items"]) == 3
    with pytest.raises(ValueError, match="invalid_review_filter"):
        app.queue(run_id, decision="made-up")


def test_incremental_record_array_keeps_unicode_and_chunk_boundaries(tmp_path):
    path = tmp_path / "records.json"
    records = [{"content": '第一轮 { \\"测试\\" }\n' * 4, "data": [None, True, 0, {"x": 2}]}, {"content": "下一轮"}]
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    assert list(iter_json_records(path, chunk_size=3)) == records


@pytest.mark.parametrize("payload", ['[{"ok":1},]', '[{"ok":1}', '[{"ok":1}] extra', '{}', '[1]', '[{"ok":1}{"bad":2}]'])
def test_incremental_record_array_rejects_malformed_documents(tmp_path, payload):
    path = tmp_path / "records.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_record_array"):
        list(iter_json_records(path, chunk_size=2))


def test_pending_review_ui_uses_global_pages_and_clamps_finished_last_page(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from lib import review_management

    _, run_id, _ = fixture_app(tmp_path, "sft")
    monkeypatch.setattr(review_management, "reviewer_identity", lambda: "test")
    script = f'''
import streamlit as st
from pathlib import Path
from lib.application.sft_review_service import SftReviewApplication
from lib.infrastructure.sft_review_driver import FilesystemSftReviewDriver
from lib.presentation.streamlit.sft_review_page import render_sft_review
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
st.session_state["ui_language"] = "en"
render_sft_review(SftReviewApplication(FilesystemSftReviewDriver(Path({str(tmp_path)!r}))))
'''
    ui = AppTest.from_string(script, default_timeout=15).run()
    assert not ui.exception
    ui.selectbox(key=f"sft-review:{run_id}:filter").select_index(1).run()
    assert not ui.exception
    assert len(ui.radio[0].options) == 20
    assert "Question 23" in ui.radio[0].options[0]
    ui.number_input(key=f"sft-review:{run_id}:page:pending").set_value(2).run()
    assert len(ui.radio[0].options) == 2
    assert "Question 43" in ui.radio[0].options[0]
    next(button for button in ui.button if button.label == "Approve and save changes").click().run()
    assert not ui.exception and len(ui.radio[0].options) == 1
    assert "Question 44" in ui.radio[0].options[0]
    next(button for button in ui.button if button.label == "Approve and save changes").click().run()
    assert not ui.exception and len(ui.radio[0].options) == 20
    assert "Question 23" in ui.radio[0].options[0]
    assert ui.session_state[f"sft-review:{run_id}:page:pending"] == 1
    assert any("Page 1 of 1" in item.value for item in ui.caption)
    rendered = "".join(str(item.value) for item in ui.get("html"))
    assert "Turn input" in rendered and "Assistant response and tool activity" in rendered
    assert "本轮输入" not in rendered
    assert "按真实轮次" not in rendered and "指纹 " not in rendered and "处理状态" not in rendered
    assert "Review status" == ui.selectbox(key=f"sft-review:{run_id}:filter").label
    assert ui.get("progress")[0].proto.text == "Reviewed 22 · Pending 23"


@pytest.mark.parametrize("target", ["cpt", "sft"])
def test_duplicate_conversation_or_corpus_cannot_share_one_approval(tmp_path, target):
    app, run_id, rows = fixture_app(tmp_path, target)
    artifact = tmp_path / "workflows" / run_id / "artifacts" / f"{target}.jsonl"
    with artifact.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(rows[0]) + "\n")
    manifest_path = artifact.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"][artifact.name] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=f"duplicate_{target}_sample_id"):
        app.queue(run_id, limit=1)
    with pytest.raises(ValueError, match=f"duplicate_{target}_sample_id"):
        app.release(run_id)


@pytest.mark.parametrize("target", ["cpt", "sft", "dpo", "orpo"])
def test_warm_pages_reuse_verified_index_and_source_change_rebuilds_it(tmp_path, target, monkeypatch):
    app, run_id, rows = fixture_app(tmp_path, target)
    app.queue(run_id)
    original_iterator = index_adapter.iter_review_rows
    monkeypatch.setattr(index_adapter, "iter_review_rows", lambda *_args, **_kwargs: pytest.fail("rebuilt unchanged index"))
    tail = app.queue(run_id, offset=20, limit=20, decision="pending")
    assert len(tail["items"]) == 2
    key, id_key = ("row", "sample_id") if target in {"cpt", "sft"} else ("pair", "pair_id")
    item = tail["items"][0]
    lookup = app._driver.row if key == "row" else app._driver.pair
    assert lookup(run_id, item[id_key]) == rows[43]
    assert app.reviewable_runs()[0]["sample_count" if key == "row" else "pair_count"] == 45
    monkeypatch.setattr(index_adapter, "iter_review_rows", original_iterator)
    artifact = tmp_path / "workflows" / run_id / "artifacts" / f"{target}.jsonl"
    artifact.write_text("".join(json.dumps(row) + "\n" for row in rows[:-1]), encoding="utf-8")
    manifest_path = artifact.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"][artifact.name] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    changed = app.queue(run_id, offset=20, limit=20, decision="pending")
    assert changed["total"] == 44 and changed["matched"] == 21
    assert len(changed["items"]) == 1


def test_corrupt_disposable_index_is_rebuilt_from_verified_artifacts(tmp_path):
    app, run_id, _ = fixture_app(tmp_path, "sft")
    app.queue(run_id)
    cache = tmp_path / "workflows" / run_id / "human-review" / ".indexes" / "sft.sqlite"
    cache.write_bytes(b"broken disposable cache")
    assert app.queue(run_id)["total"] == 45
