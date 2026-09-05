"""审核中心测试：SQLite 存储语义 + HTTP API 认证/待审/提交/幂等/静态文件。"""
from __future__ import annotations

import json
import pathlib
import socket
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_core_store_roundtrip(tmp_path, monkeypatch):
    """建号/入库/待审过滤/以身份提交/幂等/汇总。"""
    from lib import review_center as rc

    monkeypatch.setattr(rc, "DB_PATH", tmp_path / "rc.db")
    monkeypatch.setattr(rc, "OUT_ROOT", tmp_path)
    rc.init_db()
    rc.ensure_admin("k-admin")
    key_a = rc.create_user("alice")
    key_b = rc.create_user("bob", role="admin")
    assert key_a != key_b

    rc.add_records("rollout_review", [
        {"sample_id": "s1", "instruction": "q1", "conversation": "a1", "meta": ""},
        {"sample_id": "s2", "instruction": "q2", "conversation": "a2", "meta": ""},
    ])
    # upsert：同 sample_id 重推不重复
    rc.add_records("rollout_review", [{"sample_id": "s2", "instruction": "q2b", "conversation": "a2b", "meta": ""}])
    pend = rc.pending("rollout_review", "alice", 10)
    assert len(pend) == 2

    rid = {r["sample_id"]: r["record_id"] for r in pend}
    n = rc.submit("rollout_review", "alice", [
        {"record_id": rid["s1"], "decision": "keep", "reason": "好", "model": "m"},
        {"record_id": rid["s1"], "decision": "keep", "reason": "重复提交应被忽略", "model": "m"},
    ])
    assert n == 1  # 唯一约束：同人同记录只计一次
    assert len(rc.pending("rollout_review", "alice", 10)) == 1  # alice 只剩 s2
    assert len(rc.pending("rollout_review", "bob", 10)) == 2  # bob 不受 alice 提交影响

    rc.submit("rollout_review", "bob", [{"record_id": rid["s2"], "decision": "reject", "reason": "差", "model": ""}])
    resp = rc.responses("rollout_review")
    assert {r["sample_id"] for r in resp} == {"s1", "s2"}
    assert {r["username"] for r in resp} == {"alice", "bob"}


def test_http_api_auth_and_flow(tmp_path, monkeypatch):
    """HTTP 全链：健康/鉴权 401/待审/以身份提交/中心汇总。"""
    from lib import review_center as rc

    monkeypatch.setattr(rc, "DB_PATH", tmp_path / "rc.db")
    monkeypatch.setattr(rc, "OUT_ROOT", tmp_path)
    port = _free_port()
    rc.init_db()
    rc.ensure_admin("k-admin")
    key = rc.create_user("carol")
    assert rc.start_in_thread(port=port) is True

    base = f"http://127.0.0.1:{port}"
    health = json.loads(urllib.request.urlopen(f"{base}/health", timeout=5).read())
    assert health["ok"] is True

    # 无凭据 → 401
    try:
        urllib.request.urlopen(f"{base}/api/me", timeout=5)
        raise AssertionError("应 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401

    def req(method: str, path: str, body=None, key_=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        r = urllib.request.Request(f"{base}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {key_ or key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=5) as resp:
            return json.loads(resp.read())

    assert req("GET", "/api/me")["username"] == "carol"
    # 入库用直连（中心机侧操作），协作者走 HTTP
    rc.add_records("rollout_review", [{"sample_id": "h1", "instruction": "问", "conversation": "答", "meta": ""}])
    pend = req("GET", "/api/pending?dataset=rollout_review&batch=5")["records"]
    assert len(pend) == 1 and pend[0]["sample_id"] == "h1"
    out = req("POST", "/api/submit", {"dataset": "rollout_review", "records": [
        {"record_id": pend[0]["record_id"], "decision": "keep", "reason": "理由", "model": "my-model"}]})
    assert out["submitted"] == 1 and out["username"] == "carol"
    assert req("GET", "/api/pending?dataset=rollout_review")["records"] == []  # 已提交被过滤
    # 重复提交幂等
    assert req("POST", "/api/submit", {"dataset": "rollout_review", "records": [
        {"record_id": pend[0]["record_id"], "decision": "keep", "reason": "again", "model": ""}]})["submitted"] == 0

    admin = req("GET", "/api/responses?dataset=rollout_review", key_="k-admin")
    assert len(admin["responses"]) == 1
    assert admin["responses"][0]["username"] == "carol"  # 身份可审计


def test_files_serving_and_traversal_guard(tmp_path, monkeypatch):
    """静态预览 /files/ 正常服务 + 路径穿越防护。"""
    from lib import review_center as rc

    out_root = tmp_path / "out"
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "preview.html").write_text("<html>ok</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(rc, "DB_PATH", tmp_path / "rc.db")
    monkeypatch.setattr(rc, "OUT_ROOT", out_root)
    port = _free_port()
    rc.init_db()
    monkeypatch.setattr(rc, '_thread', None)  # 跨测试重置线程单例
    assert rc.start_in_thread(port=port) is True
    base = f"http://127.0.0.1:{port}"

    with urllib.request.urlopen(f"{base}/files/preview.html", timeout=5) as r:
        assert b"ok" in r.read()
    try:
        urllib.request.urlopen(f"{base}/files/../secret.txt", timeout=5)
        raise AssertionError("穿越应被拒")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_user_validation():
    from lib import review_center as rc

    import pytest

    for bad in ("../x", "a b", "", "超长" * 20):
        with pytest.raises(ValueError):
            rc.create_user(bad)
