"""Offline reviewed rejection handoffs, authenticated identities, and cached lookup."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from lib.application.review_rework_service import ReviewReworkApplication
from lib.bootstrap.corpus_reviews import corpus_review_application
from lib.bootstrap.preference_reviews import preference_review_application
from lib.bootstrap.sft_reviews import sft_review_application
from lib.domain.corpus_review import corpus_identity
from lib.domain.preference_review import pair_identity
from lib.domain.sft_review import sft_identity
from lib.infrastructure import rework_candidate_index as candidate_cache
from lib.infrastructure.review_rework_driver import FilesystemReviewReworkDriver
from lib.infrastructure.training_workflow import digest, preferred_training_record


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # These cases call no model and must not reserve the shared mock API port.
    yield


def _record(target, position=0):
    row = {"id": f"sealed-candidate-{position}", "status": "eligible", "source_id": f"source-{position}"}
    messages = [{"role": "user", "content": f"检查设备 {position} 前需要注意什么？"},
                {"role": "assistant", "content": f"设备 {position} 先断电，再检查线路。", "reasoning": "保障安全。"}]
    if target == "cpt":
        row["text"] = f"设备 {position} 检查前先断电，再检查线路。"
    elif target in {"sft", "multiturn", "agent"}:
        row["messages"] = messages
    elif target in {"cot", "gsm8k"}:
        row.update(question=f"{position}+2=?", answer=str(position + 2), reasoning="计算整数之和。")
    else:
        row.update(prompt=messages[:1], chosen=[messages[1]],
                   rejected=[{"role": "assistant", "content": "不必断电，直接检查。"}])
        if target == "rlaif":
            def check(score, reason):
                return {"keep": score >= 4, "grounded": score >= 4, "reasoning_valid": score >= 4,
                        "correctness": score, "scores": {name: score for name in
                            ("correctness", "reasoning", "grounding", "instruction", "safety")}, "reason": reason}
            row["preference"] = {"dimension": "correctness", "chosen": check(5, "安全前提正确。"),
                                 "rejected": check(1, "遗漏安全前提。"), "minimum_gap": 2}
            row["rlaif"] = {"criterion": "correctness", "judge": "jev",
                            "chosen_feedback": "安全前提正确。", "rejected_feedback": "遗漏安全前提。"}
    return row


def _refresh(artifacts):
    names = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in artifacts.iterdir() if path.name != "manifest.json"}
    (artifacts / "manifest.json").write_text(json.dumps({"status": "complete", "sha256": names}), encoding="utf-8")


def _run(root, target="sft", count=3, *, drop=False, records=None):
    run_id = "a" * 32
    run = root / "workflows" / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    rows = records if records is not None else [_record(target, position) for position in range(count)]
    recipe = {"sft_output_style": "drop" if drop else "separated"}
    (run / "recipe.json").write_text(json.dumps(recipe), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({"id": run_id, "name": "真实退回映射测试", "status": "completed",
        "targets": [target], "created_at": "2026-10-11T00:00:00Z"}), encoding="utf-8")
    native = [preferred_training_record(target, row, sft_output_style=recipe["sft_output_style"])
              for row in rows if row["status"] == "eligible"]
    (artifacts / f"{target}.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in native), encoding="utf-8")
    (artifacts / f"{target}.records.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    _refresh(artifacts)
    return run, run_id, rows, native


@pytest.mark.parametrize("target", ["sft", "cpt", "dpo", "orpo", "rlaif", "cot", "gsm8k", "multiturn", "agent"])
def test_all_targets_lookup_exact_sealed_candidates_and_never_guess_ids(tmp_path, target):
    run, _, records, _ = _run(tmp_path, target)
    found = candidate_cache.lookup_candidates(run, target, [records[2]["id"], records[0]["id"]])
    assert found[records[2]["id"]] == records[2]
    assert found[records[0]["id"]] == records[0]
    with pytest.raises(ValueError, match="candidate_not_found"):
        candidate_cache.lookup_candidates(run, target, ["0"])


@pytest.mark.parametrize("target", ["sft", "cpt", "dpo", "orpo", "rlaif"])
def test_review_digest_maps_to_real_id_including_native_sft_reasoning_projection(tmp_path, target):
    run, _, records, native = _run(tmp_path, target)
    identity = (sft_identity if target == "sft" else corpus_identity if target == "cpt" else pair_identity)
    with candidate_cache.rework_candidate_index(run, target) as index:
        assert index.reviewed_candidate(identity(records[1] if target == "rlaif" else native[1])) == records[1]["id"]
    if target == "sft":
        assert "reasoning_content" in native[1]["messages"][1]
        assert "reasoning" in records[1]["messages"][1]


def test_sft_drop_export_still_maps_to_the_full_sealed_reasoning_record(tmp_path):
    run, _, records, native = _run(tmp_path, drop=True)
    assert "reasoning_content" not in native[0]["messages"][1]
    with candidate_cache.rework_candidate_index(run, "sft") as index:
        assert index.reviewed_candidate(sft_identity(native[0])) == records[0]["id"]
        assert index.candidates([records[0]["id"]])[records[0]["id"]]["messages"][1]["reasoning"] == "保障安全。"


def test_cached_lookup_and_nested_batch_authentication_do_not_rescan_records(tmp_path, monkeypatch):
    run, _, records, _ = _run(tmp_path)
    candidate_cache.lookup_candidates(run, "sft", [records[0]["id"]])
    monkeypatch.setattr(candidate_cache, "iter_json_records", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected scan")))
    with candidate_cache.rework_candidate_index(run, "sft"):
        assert candidate_cache.lookup_candidates(run, "sft", [records[2]["id"]])[records[2]["id"]] == records[2]
        assert candidate_cache.lookup_candidates(run, "sft", [records[1]["id"]])[records[1]["id"]] == records[1]


def test_unsealed_artifact_edit_is_rejected_before_lookup(tmp_path):
    run, _, records, _ = _run(tmp_path)
    candidate_cache.lookup_candidates(run, "sft", [records[0]["id"]])
    path = run / "artifacts" / "sft.records.json"
    path.write_text(path.read_text(encoding="utf-8").replace("保障安全。", "不管安全。"), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        candidate_cache.lookup_candidates(run, "sft", [records[0]["id"]])


def test_disposable_cache_tamper_rebuilds_from_sealed_records(tmp_path):
    run, _, records, _ = _run(tmp_path)
    candidate_cache.lookup_candidates(run, "sft", [records[0]["id"]])
    cached = run / "human-review" / ".indexes" / "sft.rework.sqlite"
    db = sqlite3.connect(cached)
    try:
        with db:
            db.execute("UPDATE candidates SET payload='{}' WHERE id=?", (records[0]["id"],))
    finally:
        db.close()
    assert candidate_cache.lookup_candidates(run, "sft", [records[0]["id"]])[records[0]["id"]] == records[0]


def test_duplicate_candidate_ids_are_rejected_and_ambiguous_payload_is_not_guessed(tmp_path):
    first = _record("sft")
    duplicate = deepcopy(first)
    run, _, _, _ = _run(tmp_path, records=[first, duplicate])
    with pytest.raises(ValueError, match="duplicate_candidate_id"):
        candidate_cache.lookup_candidates(run, "sft", [first["id"]])
    duplicate["id"] = "different-real-sealed-id"
    path = run / "artifacts" / "sft.records.json"
    path.write_text(json.dumps([first, duplicate]), encoding="utf-8")
    _refresh(run / "artifacts")
    with candidate_cache.rework_candidate_index(run, "sft") as index:
        identity = sft_identity(preferred_training_record("sft", first))
        with pytest.raises(ValueError, match="mapping_ambiguous"):
            index.reviewed_candidate(identity)


@pytest.mark.parametrize("target", ["sft", "cpt", "dpo", "orpo", "rlaif"])
def test_existing_rejected_queue_preserves_each_review_note_and_excludes_other_states(tmp_path, target):
    run, run_id, records, _ = _run(tmp_path, target)
    application = (sft_review_application(tmp_path) if target == "sft" else corpus_review_application(tmp_path)
                   if target == "cpt" else preference_review_application(tmp_path, target=target))
    items = application.queue(run_id)["items"]
    key = "pair_id" if target in {"dpo", "orpo", "rlaif"} else "sample_id"
    for i, decision in enumerate(("rejected", "approved", "skipped")):
        application.decide(run_id, items[i][key], decision=decision, reviewer="test",
                           expected_hash=items[i][key], reason=f"修正意见 {i}")
    batches = list(FilesystemReviewReworkDriver(tmp_path).rejected_batches(run_id, target))
    assert batches == [{"rows": [{"candidate_id": records[0]["id"], "instruction": "修正意见 0"}]}]


def test_collection_transfer_is_bounded_and_preserves_stable_save_request_ids():
    class Driver:
        request_id = staticmethod(FilesystemReviewReworkDriver.request_id)

        def rejected_batches(self, run_id, target):
            for count in (100, 25):
                yield {"rows": [{"candidate_id": f"candidate-{count}-{i}", "instruction": f"问题 {i}"}
                                for i in range(count)]}

    class Human:
        def __init__(self):
            self.calls, self.version = [], 1

        def review_session(self, run_id):
            return "b" * 32

        def session(self, session_id):
            return {"rounds": [{"id": "c" * 32, "run_id": "a" * 32}], "version": self.version}

        def save_issues(self, session_id, selections, **kwargs):
            self.calls.append((deepcopy(selections), kwargs))
            assert kwargs["expected_version"] == self.version
            self.version += 1
            return {"saved_count": len(selections), "version": self.version, "issue_counts": {"pending": 125}}

    human = Human()
    application = ReviewReworkApplication(Driver(), human)
    result = application.save_rejected("a" * 32, "sft", instruction="更自然的回答")
    assert result["saved_count"] == 125 and result["batch_count"] == 2
    assert [len(call[0]) for call in human.calls] == [100, 25]
    first_keys = [call[1]["request_id"] for call in human.calls]
    application.save_rejected("a" * 32, "sft", instruction="更自然的回答")
    assert [call[1]["request_id"] for call in human.calls[2:]] == first_keys
    assert human.calls[0][0][0]["instruction"] == "问题 0"


def test_real_rejection_opens_one_durable_issue_collection_without_generation(tmp_path):
    from test_human_sessions import application, generate

    human, original_session, output = application(tmp_path)
    parent, writer, reviewer = generate(human, original_session, output, tmp_path)
    before_calls = len(writer.calls), len(reviewer.calls)
    sft = sft_review_application(output)
    item = sft.queue(parent["run_id"])["items"][0]
    sft.decide(parent["run_id"], item["sample_id"], decision="rejected", reviewer="test",
               expected_hash=item["sample_id"], reason="安全提醒需要更清楚。")
    rework = ReviewReworkApplication(FilesystemReviewReworkDriver(output), human)
    first = rework.save_rejected(parent["run_id"], "sft")
    assert first["saved_count"] == 1
    issues = human.issues(first["session_id"], status="pending")
    assert issues["total"] == 1 and issues["rows"][0]["instruction"] == "安全提醒需要更清楚。"
    assert issues["rows"][0]["candidate_id"] != item["sample_id"]
    second = rework.save_rejected(parent["run_id"], "sft")
    reopened = ReviewReworkApplication(FilesystemReviewReworkDriver(output), human).collection(parent["run_id"])
    assert first["session_id"] == second["session_id"] == reopened["session_id"]
    assert human.issues(second["session_id"], status="all")["total"] == 1
    assert len(human.session(second["session_id"])["rounds"]) == 1
    assert (len(writer.calls), len(reviewer.calls)) == before_calls


def test_compact_controls_save_without_launching_and_open_the_existing_issues_view():
    from lib.presentation.streamlit.review_rework_controls import render_review_rework_controls

    class UI:
        def __init__(self, action):
            self.action, self.session_state, self.query_params = action, {"ws": "default"}, {}
            self.labels, self.messages, self.reran = [], [], False

        def caption(self, text):
            self.messages.append(text)

        def text_input(self, label, **kwargs):
            return "保持安全前提"

        def button(self, label, **kwargs):
            self.labels.append(label)
            return label == self.action and not kwargs.get("disabled", False)

        def spinner(self, *_args):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def rerun(self):
            self.reran = True

    class Application:
        def __init__(self):
            self.saved = []

        def save_rejected(self, run_id, target, **kwargs):
            self.saved.append((run_id, target, kwargs))
            return {"saved_count": 125, "session_id": "b" * 32}

        def collection(self, _run):
            return {"session_id": "b" * 32}

    app = Application()
    save_ui = UI("把退回结果存入返工集合")
    render_review_rework_controls(app, "a" * 32, "sft", 125, widgets=save_ui)
    assert len(app.saved) == 1 and save_ui.labels == ["把退回结果存入返工集合", "打开返工集合"]
    assert "nav" not in save_ui.session_state and not save_ui.reran
    assert any("125" in message for message in save_ui.messages)
    open_ui = UI("打开返工集合")
    render_review_rework_controls(app, "a" * 32, "sft", 125, widgets=open_ui)
    assert len(app.saved) == 1
    assert open_ui.session_state["nav"] == "人工问答增强"
    assert open_ui.session_state["human-result-view-pending:human:default:" + "b" * 32] == "issues"
    assert open_ui.query_params["human"] == "b" * 32 and open_ui.reran
