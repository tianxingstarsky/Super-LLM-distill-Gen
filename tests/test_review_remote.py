"""分布式评审客户端测试：真实本地 HTTP 审核中心 E2E + agent 判定离线用例。"""
from __future__ import annotations

import json
import pathlib
import socket

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_center(tmp_path, monkeypatch, dataset="rollout_review"):
    """起一个真实审核中心（线程内），返回 (base_url, collaborator_key)。"""
    from lib import review_center as rc

    monkeypatch.setattr(rc, "DB_PATH", tmp_path / "rc.db")
    monkeypatch.setattr(rc, "OUT_ROOT", tmp_path / "out")
    rc.init_db()
    rc.ensure_admin("k-admin")
    key = rc.create_user("collaborator_test")
    rc.add_records(dataset, [
        {"sample_id": "s1", "instruction": "解释过拟合", "conversation": "过拟合是…", "meta": "m"},
        {"sample_id": "s2", "instruction": "写代码", "conversation": "def f…", "meta": "m"},
    ])
    port = _free_port()
    monkeypatch.setattr(rc, '_thread', None)  # 跨测试重置线程单例
    assert rc.start_in_thread(port=port) is True
    return f"http://127.0.0.1:{port}", key


def test_pull_auto_submit_full_flow(tmp_path, monkeypatch):
    """协作者全链：pull（跳过已提交）→ auto 判定 → 以身份提交 → 中心可审计。"""
    from lib import review_remote as rr

    base, key = _start_center(tmp_path, monkeypatch)
    monkeypatch.setattr(rr, "INBOX_PATH", tmp_path / "inbox.jsonl")
    cfg = {"server": base, "api_key": key, "dataset": "rollout_review"}

    inbox = rr.pull(cfg, batch=5)
    assert len(inbox) == 2
    cached = [json.loads(l) for l in rr.INBOX_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert cached[0]["sample_id"] == "s1"

    # 判定（离线 FakeJudger，模型=协作者自己的模型名）
    seen = {}

    class FakeJudger:
        def chat(self, messages, **kw):
            seen["touched"] = True
            return '{"correctness": 5, "keep": true}'

    out = rr._judge_answers(inbox, FakeJudger(), "my-local-model")
    assert seen.get("touched")
    assert out[0]["model"] == "my-local-model" and out[0]["decision"] == "keep"
    assert "correctness=5" in out[0]["reason"]

    # 以我的身份提交（含理由）
    n = rr.submit(out, cfg)
    assert n == 2
    # 再拉：全部已提交 → 空
    assert rr.pull(cfg, batch=5) == []

    # 中心侧：身份+理由可审计
    from lib import review_center as rc

    resp = rc.responses("rollout_review")
    assert len(resp) == 2
    assert all(r["username"] == "collaborator_test" for r in resp)
    assert all(r["reason"].startswith("本地 agent 判定") for r in resp)


def test_submit_idempotent(tmp_path, monkeypatch):
    """重复提交被中心唯一约束忽略（返回实际写入数）。"""
    from lib import review_remote as rr

    base, key = _start_center(tmp_path, monkeypatch)
    monkeypatch.setattr(rr, "INBOX_PATH", tmp_path / "inbox.jsonl")
    cfg = {"server": base, "api_key": key, "dataset": "rollout_review"}
    inbox = rr.pull(cfg, batch=5)
    decisions = [{**d, "decision": "keep", "reason": "r", "model": "m"} for d in inbox]
    assert rr.submit(decisions, cfg) == 2
    assert rr.submit(decisions, cfg) == 0  # 幂等


def test_bad_key_rejected(tmp_path, monkeypatch):
    """无效密钥：拉取立即失败并带明确信息。"""
    from lib import review_remote as rr

    base, _ = _start_center(tmp_path, monkeypatch)
    monkeypatch.setattr(rr, "INBOX_PATH", tmp_path / "inbox.jsonl")
    cfg = {"server": base, "api_key": "agent.bad-key", "dataset": "rollout_review"}
    import pytest

    with pytest.raises(ConnectionError):
        rr.pull(cfg, batch=5)
