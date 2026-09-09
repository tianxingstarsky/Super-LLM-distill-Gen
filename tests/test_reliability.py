"""Failure-path regression tests for real isolation and release guarantees."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from lib import review_center as rc
from lib import review_remote as rr
from lib.quality import sample_hash, report, export_release


def sample(n=1):
    return {"id": f"s{n}", "messages": [{"role": "user", "content": f"Question {n}"}, {"role": "assistant", "content": f"Answer {n}"}]}


@pytest.fixture
def center(tmp_path, monkeypatch):
    rc.stop_thread()
    monkeypatch.setattr(rc, "DB_PATH", tmp_path / "center.db")
    rc.ensure_admin("admin-test-key")
    rc.create_user("alice", api_key="alice-test-key")
    rc.create_user("bob", api_key="bob-test-key")
    yield rc
    rc.stop_thread()


def test_concurrent_reviewers_never_share_batch(tmp_path):
    cfg = {"server": "http://localhost:6900", "dataset": "rollout_review"}
    paths = [rr.inbox_path(cfg, SimpleNamespace(me={"username": user}), tmp_path) for user in ("alice", "bob")]
    assert paths[0] != paths[1]
    from lib.io_utils import atomic_json
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda pair: atomic_json(pair[0], {"records": [pair[1]]}), zip(paths, ["alice", "bob"])))
    assert rr.load_batch(paths[0])["records"] == ["alice"]
    assert rr.load_batch(paths[1])["records"] == ["bob"]
    assert rr.inbox_path({**cfg, "dataset": "rollout_review_other"}, SimpleNamespace(me={"username": "alice"}), tmp_path) not in paths


def test_pull_does_not_overwrite_unsubmitted_batch(tmp_path):
    cfg = {"server": "http://center", "dataset": "rollout_review"}
    path = tmp_path / "batch.json"
    class Client:
        me = {"username": "alice"}
        calls = 0
        def pending(self, dataset, count):
            self.calls += 1
            return [{"record_id": 1, "sample_id": "s1"}]
    client = Client()
    assert rr.pull(cfg, client=client, path=path) == rr.pull(cfg, client=client, path=path)
    assert client.calls == 1


@pytest.mark.parametrize("reply", ['{"keep":"false","correctness":5,"reason":"r"}', '{"keep":true,"correctness":5}', '{}'])
def test_invalid_judge_output_never_becomes_a_vote(reply):
    class Judge:
        def chat(self, *args, **kwargs): return reply
    result = rr._judge_answers([{"instruction": "q", "conversation": "a"}], Judge(), "test")
    assert "decision" not in result[0] and result[0]["review_error"]
    with pytest.raises(ValueError, match="unfinished"):
        rr.submit(result, {}, client=object())


def test_judge_sees_tail_and_uses_distinct_rubric():
    captured = []
    class Judge:
        def chat(self, messages, **kwargs):
            captured.append(messages)
            return '{"keep":true,"correctness":5,"reason":"Supported by the supplied source"}'
    row = {"instruction": "q", "conversation": "a" * 5000 + "TAIL_EVIDENCE"}
    for policy in ("quality", "safety"):
        assert rr._judge_answers([row], Judge(), "m", policy)[0]["decision"] == "keep"
    assert "TAIL_EVIDENCE" in captured[0][1]["content"]
    assert captured[0][0] != captured[1][0]
    result = rr._judge_answers([row], Judge(), "m", max_chars=2000)
    assert result[0]["review_error"].startswith("context_limit")
    assert len(captured) == 2


def test_budget_exception_is_not_sample_rejection():
    from lib.llm_client import BudgetExceeded
    class Judge:
        def chat(self, *args, **kwargs): raise BudgetExceeded("exhausted")
    with pytest.raises(BudgetExceeded):
        rr._judge_answers([{"instruction": "q", "conversation": "a"}], Judge(), "m")


def test_access_and_dataset_membership_enforced(center):
    center.add_records("private", [{"sample_id": "s1", "conversation": "a"}])
    with pytest.raises(PermissionError): center.pending("private", "alice")
    center.grant("alice", "private")
    row = center.pending("private", "alice")[0]
    with pytest.raises(ValueError):
        center.submit("rollout_review", "alice", [{**row, "decision": "keep", "reason": "r"}])
    with pytest.raises(PermissionError):
        center.submit("private", "bob", [{**row, "decision": "keep", "reason": "r"}])
    with pytest.raises(ValueError): center.pending("private", "alice", -1)
    assert center.responses("private") == []


def test_reviewed_content_immutable_and_stale_votes_rejected(center):
    from lib.review import build_records
    center.add_records("rollout_review", build_records([sample()], {}))
    row = center.pending("rollout_review", "alice")[0]
    with pytest.raises(ValueError):
        center.submit("rollout_review", "alice", [{**row, "sample_hash": "wrong", "decision": "keep", "reason": "r"}])
    assert center.submit("rollout_review", "alice", [{**row, "decision": "keep", "reason": "r"}]) == 1
    modified = sample()
    modified["messages"][-1]["content"] = "A different answer"
    with pytest.raises(ValueError, match="immutable"):
        center.add_records("rollout_review", build_records([modified], {}))
    center.init_db()
    assert len(center.responses("rollout_review")) == 1
    assert "api_key" not in center.user_rows()[0]
    assert center._auth({"authorization": "Bearer alice-test-key"}) == "alice"
    with pytest.raises(ValueError, match="already exists"):
        center.create_user("alice")


def test_consensus_counts_samples_not_votes():
    from lib.review import decide_gate
    rows = [{"sample_id": "s1", "decision": "keep", "username": str(i)} for i in range(15)]
    assert decide_gate(rows)["reviewed"] == 1
    assert not decide_gate(rows)["release"]
    rows.append({"sample_id": "s1", "decision": "reject"})
    assert decide_gate(rows)["conflicts"] == 1
    assert decide_gate(rows)["keep"] == 0


def test_export_versions_immutable_and_current_evidence_required(tmp_path):
    samples = [sample(i) for i in range(10)]
    votes = [{"sample_id": s["id"], "decision": "keep", "sample_hash": sample_hash(s)} for s in samples]
    assert report(samples, votes)["ready_for_bulk"]
    changed = json.loads(json.dumps(samples))
    changed[0]["messages"][-1]["content"] = "changed"
    assert report(changed, votes)["review_coverage"] == .9
    assert not report(changed, votes)["ready_for_bulk"]  # Only nine samples have current reviews.
    destination, counts = export_release(samples, "chat", tmp_path, votes, tag="release-a", bulk=True)
    original = (destination / "sft.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        export_release(samples, "chat", tmp_path, votes, tag="release-a", bulk=True)
    assert (destination / "sft.jsonl").read_bytes() == original
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["sha256"]["sft.jsonl"] == hashlib.sha256(original).hexdigest()
    with pytest.raises(ValueError, match="Quality blocked"):
        export_release(samples, "chat", tmp_path, [], bulk=True)
    assert report([samples[0], samples[0]])["duplicate_content"] == 1


def test_doctor_does_not_run_tests_or_modify_state(tmp_path, monkeypatch):
    from lib import doctor
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    monkeypatch.setattr(doctor, "subprocess", object())  # nvidia-smi 探测失败应优雅降级，不抛错
    import subprocess as real_subprocess
    monkeypatch.setattr(real_subprocess, "run", lambda *a, **k: pytest.fail("doctor spawned a command"))
    rows = doctor.checks(tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert any("GPU" in r["check"] for r in rows)
    assert rows[-1]["check"].startswith("DSH_HOME")


def test_incremental_import_preserves_samples_and_does_not_skip_capped_rows(tmp_path, monkeypatch):
    import scripts.import_rollout as importer
    monkeypatch.setattr(importer, "ROLLOUT_DIR", Path(__file__).parent / "fixtures")
    monkeypatch.setattr(importer, "ROLLOUT_PATTERN", "rollout_sample.jsonl")
    importer.run(export_limit=1, out_dir=tmp_path)
    first = (tmp_path / "rollout_samples.jsonl").read_bytes()
    importer.run(export_limit=10, out_dir=tmp_path)
    final = (tmp_path / "rollout_samples.jsonl").read_bytes()
    assert final.startswith(first)
    assert len(final.splitlines()) > 1
    importer.run(export_limit=10, out_dir=tmp_path)
    assert (tmp_path / "rollout_samples.jsonl").read_bytes() == final


def test_budget_updates_from_independent_instances_accumulate(tmp_path):
    from lib.llm_client import BudgetGuard
    a, b = BudgetGuard(tmp_path, 10), BudgetGuard(tmp_path, 10)
    a.add_usd(1)
    b.add_usd(2)
    assert BudgetGuard(tmp_path, 10).spent == 3


def test_human_edit_creates_new_version(center):
    """审核页逐条编辑：保存为新版本（新 ID），原记录保留，内容哈希不同。"""
    from lib.review import build_records, revise_sample
    center.add_records("rollout_review", build_records([sample()], {}))
    row = center.pending("rollout_review", "admin")[0]
    assert row["payload"] and '"messages"' not in row["payload"]  # payload 是消息数组
    edited = json.loads(row["payload"])
    edited[-1]["content"] = "修正后的答案"
    new_id = revise_sample("rollout_review", row, edited, reviewer="tester")
    assert new_id == row["sample_id"] + "-r1"
    new_row = next(r for r in center.pending("rollout_review", "admin", 50) if r["sample_id"] == new_id)
    assert json.loads(new_row["payload"])[-1]["content"] == "修正后的答案"
    assert new_row["sample_hash"] != row["sample_hash"]
    assert "修订自" in new_row["meta"]
    # 再修订一次 → 序号递增；原记录仍在
    assert revise_sample("rollout_review", row, edited, "tester") == row["sample_id"] + "-r2"
    assert any(r["sample_id"] == row["sample_id"] for r in center.pending("rollout_review", "admin", 50))


def test_launcher_never_opens_browser_or_loops(tmp_path):
    """启动器回归：单实例抢锁失败必须静默退出，禁止 webbrowser.open（分离进程挂死留僵尸）。"""
    source = (Path(__file__).resolve().parent.parent / "scripts" / "launch_console.py").read_text(encoding="utf-8")
    assert "import webbrowser" not in source and "webbrowser.open(" not in source
    assert "while True" not in source  # 无看门狗/无重启循环


def test_minimind_rejects_lossy_image_export():
    from lib.exporters import to_minimind_sft
    with pytest.raises(ValueError, match="images"):
        to_minimind_sft({**sample(), "images": ["frame.png"]})


def test_gui_form_and_session_isolation(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from lib import workspace as ws
    root = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setenv("DF_WORKSPACE", "default")
    app = AppTest.from_file(str(root / "lib/webapp.py"), default_timeout=20).run()
    assert not app.exception
    app.sidebar.radio[0].set_value("管线运行").run()
    assert not app.exception
    assert app.selectbox[1].label == "任务" or any(w.label == "任务" for w in app.selectbox)
    assert all("命令参数" not in w.label for w in app.text_input)
    assert os_workspace() == "default"


def os_workspace():
    import os
    return os.environ.get("DF_WORKSPACE")
