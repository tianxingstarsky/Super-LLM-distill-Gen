"""审核 UX 后端测试：身份、队列分页/转义搜索、完整记录、单字段修订的
陈旧哈希/作用域/多模态保留/请求幂等/并发版本分配。"""
from __future__ import annotations

import contextlib
import json

import pytest

DATASET = "rollout_review"


# ── 数据与直连辅助（不依赖被广泛覆盖的 HTTP 层） ─────────────────────────────
def _admin_key():
    from lib import review_center as rc
    rc.ensure_admin("k-admin")
    return "k-admin"


def _sample(sample_id="s1", *, instruction="问题", answer="答案", reasoning=None):
    assistant = {"role": "assistant", "content": answer}
    if reasoning is not None:
        assistant["reasoning_content"] = reasoning
    return {
        "id": sample_id,
        "messages": [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": instruction},
            assistant,
        ],
        "images": [{"path": "a.png", "sha256": "abc"}],
        "tools": [{"type": "function", "function": {"name": "fn"}}],
        "caption": "图注",
        "session_id": "sess-1",
        "usage": {"total_tokens": 7},
        "model": "m",
        "finish_reason": "stop",
    }


def _push(dataset, *samples):
    from lib import review_center as rc
    from lib.review import build_records
    rc.add_records(dataset, build_records(list(samples), {}))


def _record(dataset, sample_id):
    """直连读取原始记录行（测试观察用，不走业务鉴权）。"""
    from lib import review_center as rc
    with rc._conn() as con:
        row = con.execute(
            "SELECT id,sample_id,instruction,conversation,meta,sample_hash,payload "
            "FROM records WHERE dataset=? AND sample_id=?", (dataset, sample_id)).fetchone()
    assert row, f"record {sample_id} missing"
    return dict(zip(("record_id", "sample_id", "instruction", "conversation", "meta",
                     "sample_hash", "payload"), row))


def _sample_ids(dataset):
    from lib import review_center as rc
    with rc._conn() as con:
        return [r[0] for r in con.execute(
            "SELECT sample_id FROM records WHERE dataset=? ORDER BY id", (dataset,)).fetchall()]


def _revision_ids(dataset, base):
    prefix = base + "-r"
    return [s for s in _sample_ids(dataset) if s.startswith(prefix) and s[len(prefix):].isdigit()]


# ── 身份：authenticate 返回角色，坏 key 一律 403 语义 ────────────────────────
def test_authenticate_identity_and_bad_keys():
    from lib import review_center as rc

    admin = _admin_key()
    alice = rc.create_user("alice")
    bob = rc.create_user("bob", role="admin")
    assert rc.authenticate(admin) == {"username": "admin", "role": "admin"}
    assert rc.authenticate(alice) == {"username": "alice", "role": "annotator"}
    assert rc.authenticate(bob) == {"username": "bob", "role": "admin"}
    # 隔离：不同 key 解析到各自身份，互不影响
    assert rc.authenticate(alice)["username"] != rc.authenticate(bob)["username"]
    for bad in ("", "no-such-key", None, 123, b"bytes"):
        with pytest.raises(PermissionError):
            rc.authenticate(bad)


# ── 队列状态与“只看到自己的判定” ────────────────────────────────────────────
def test_queue_page_status_and_own_decisions():
    from lib import review_center as rc

    _admin_key()
    rc.create_user("alice")
    rc.create_user("bob")
    _push(DATASET, _sample("s1"), _sample("s2"), _sample("s3"))
    r1 = _record(DATASET, "s1")
    rc.submit(DATASET, "alice", [{"record_id": r1["record_id"], "decision": "keep",
                                  "reason": "好", "sample_hash": r1["sample_hash"]}])

    page = rc.queue_page(DATASET, "alice", status="pending")
    assert (page["total"], page["pending"], page["reviewed"]) == (2, 2, 1)
    assert [i["sample_id"] for i in page["items"]] == ["s2", "s3"]
    assert page["offset"] == 0 and page["limit"] == 30

    mine = rc.queue_page(DATASET, "alice", status="reviewed")
    assert [i["sample_id"] for i in mine["items"]] == ["s1"]
    assert mine["items"][0]["decision"] == "keep" and mine["items"][0]["reason"] == "好"

    every = rc.queue_page(DATASET, "alice", status="all")
    assert (every["total"], every["pending"], every["reviewed"]) == (3, 2, 1)

    # bob 未提交：同一批队列里看不到 alice 的判定
    bob = rc.queue_page(DATASET, "bob", status="all")
    assert (bob["pending"], bob["reviewed"]) == (3, 0)
    assert all(i["decision"] is None and i["reason"] is None for i in bob["items"])
    assert all("decision" in i and "reason" in i for i in bob["items"])


# ── >100 条分页 + 转义搜索（%/_/\ 按字面值） ────────────────────────────────
def test_queue_page_pagination_over_hundred():
    from lib import review_center as rc

    _admin_key()
    _push(DATASET, *[_sample(f"s{i:03d}") for i in range(130)])

    first = rc.queue_page(DATASET, "admin", limit=100)
    assert first["total"] == 130 and first["pending"] == 130 and len(first["items"]) == 100
    second = rc.queue_page(DATASET, "admin", offset=100, limit=100)
    assert len(second["items"]) == 30 and second["total"] == 130
    ids = [i["sample_id"] for i in first["items"] + second["items"]]
    assert ids == [f"s{i:03d}" for i in range(130)]  # 稳定顺序 + 无重复
    assert rc.queue_page(DATASET, "admin", offset=130)["items"] == []

    for bad_limit in (0, -1, 201, 1.5, True):
        with pytest.raises(ValueError):
            rc.queue_page(DATASET, "admin", limit=bad_limit)
    for bad_offset in (-1, 1.5, True):
        with pytest.raises(ValueError):
            rc.queue_page(DATASET, "admin", offset=bad_offset)
    with pytest.raises(ValueError):
        rc.queue_page(DATASET, "admin", status="bogus")
    with pytest.raises(ValueError):
        rc.queue_page(DATASET, "admin", query="x" * 201)


def test_queue_page_search_wildcards_are_literal():
    from lib import review_center as rc

    _admin_key()
    _push(DATASET, *[
        _sample("p%q", instruction="plain"),
        _sample("pXq", instruction="plain"),
        _sample("other", instruction="p%q inside"),
        _sample("a_b", instruction="under"),
        _sample("axb", instruction="under"),
        _sample(r"b\c", instruction="slash"),
        _sample("bc", instruction="slash"),
    ])

    def ids(query):
        return {i["sample_id"] for i in rc.queue_page(DATASET, "admin", query=query)["items"]}

    # % 不被当通配符：pXq 不匹配，p%q 与 instruction 含 p%q 的记录匹配
    assert ids("p%q") == {"p%q", "other"}
    assert ids("%") == {"p%q", "other"}
    # _ 不被当单字符通配符
    assert ids("a_b") == {"a_b"}
    assert ids("_") == {"a_b"}
    # 反斜杠按字面值（否则 ESCAPE '\' 会把 \c 解释成 c，从而误匹配 bc）
    assert ids(r"b\c") == {r"b\c"}
    # instruction 命中
    assert ids("inside") == {"other"}
    # 无命中
    assert ids("nothing-matches") == set()


# ── 摘要上限 + 不读 payload/conversation ────────────────────────────────────
def test_queue_page_bounded_summaries_never_reads_payload(monkeypatch):
    from lib import review_center as rc

    _admin_key()
    huge = json.dumps({"blob": "x" * (2 * 1024 * 1024), "messages": []})
    rc.add_records(DATASET, [{
        "sample_id": "big", "instruction": "问" * 5000, "conversation": "对话" * 2000,
        "meta": "m" * 5000, "sample_hash": "h" * 64, "payload": huge}])
    rc.init_db()
    monkeypatch.setattr(rc, "init_db", lambda: None)  # DDL 文本不混入本次 SQL 追踪

    statements = []
    real_conn = rc._conn

    @contextlib.contextmanager
    def traced():
        with real_conn() as con:
            con.set_trace_callback(statements.append)
            yield con

    monkeypatch.setattr(rc, "_conn", traced)
    page = rc.queue_page(DATASET, "admin")
    item = page["items"][0]
    assert set(item) == {"record_id", "sample_id", "instruction", "meta",
                         "sample_hash", "decision", "reason"}
    assert len(item["instruction"]) <= rc.QUEUE_INSTRUCTION_CHARS
    assert len(item["meta"]) <= rc.QUEUE_META_CHARS
    assert item["sample_hash"] == "h" * 64
    sql = "\n".join(statements).lower()
    assert "payload" not in sql and "conversation" not in sql


# ── 完整记录：选择器校验、本人判定、数据集作用域 ────────────────────────────
def test_get_record_selectors_own_decision_and_dataset_scope():
    from lib import review_center as rc

    _admin_key()
    rc.create_user("alice")
    _push(DATASET, _sample("s1", instruction="Q1", answer="A1"))
    rid = _record(DATASET, "s1")["record_id"]

    by_id = rc.get_record(DATASET, "alice", record_id=rid)
    by_sid = rc.get_record(DATASET, "alice", sample_id="s1")
    assert by_id == by_sid
    assert set(by_id) == {"record_id", "sample_id", "instruction", "conversation", "meta",
                          "suggestion", "sample_hash", "payload", "decision", "reason"}
    assert by_id["instruction"] == "Q1" and "A1" in by_id["conversation"]
    assert json.loads(by_id["payload"])["messages"][1]["content"] == "Q1"
    assert by_id["decision"] is None and by_id["reason"] is None

    rc.submit(DATASET, "alice", [{"record_id": rid, "decision": "keep", "reason": "ok",
                                  "sample_hash": by_id["sample_hash"]}])
    assert rc.get_record(DATASET, "alice", record_id=rid)["decision"] == "keep"
    rc.create_user("bob")
    assert rc.get_record(DATASET, "bob", record_id=rid)["decision"] is None  # 只看自己的票

    for kwargs in ({}, {"record_id": rid, "sample_id": "s1"}, {"record_id": 0},
                   {"record_id": 999999}, {"sample_id": ""}, {"sample_id": 7}):
        with pytest.raises(ValueError):
            rc.get_record(DATASET, "alice", **kwargs)

    # 未授权数据集：读写 API 都拒绝；grant 后放行；admin 始终可读
    rc.create_user("carol")
    rc._validate_dataset("docs_review")
    rc.add_records("docs_review", [{"sample_id": "d1", "instruction": "x",
                                    "conversation": "y", "meta": ""}])
    with pytest.raises(PermissionError):
        rc.get_record("docs_review", "carol", sample_id="d1")
    with pytest.raises(PermissionError):
        rc.queue_page("docs_review", "carol")
    rc.grant("carol", "docs_review")
    assert rc.get_record("docs_review", "carol", sample_id="d1")["sample_id"] == "d1"
    assert rc.get_record("docs_review", "admin", sample_id="d1")["sample_id"] == "d1"


# ── 修订：多模态/元数据保留，字段作用域收紧 ─────────────────────────────────
def test_revise_field_preserves_multimodal_and_restricts_scope():
    from lib import review_center as rc

    _admin_key()
    rc.create_user("alice")
    sample = _sample("s1", reasoning="原始思考")
    sample["messages"][2]["content"] = [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
        {"type": "text", "text": "看这张图"},
    ]
    _push(DATASET, sample)
    row = _record(DATASET, "s1")

    new_id = rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                             2, "reasoning_content", "新思考", "req-1")
    assert new_id == "s1-r1"
    revised = json.loads(_record(DATASET, new_id)["payload"])
    assert revised["images"] == sample["images"] and revised["tools"] == sample["tools"]
    assert revised["caption"] == sample["caption"] and revised["session_id"] == sample["session_id"]
    assert revised["usage"] == sample["usage"]
    assert revised["messages"][2]["reasoning_content"] == "新思考"
    assert revised["messages"][2]["content"] == sample["messages"][2]["content"]  # 多模态原样
    assert revised["messages"][1]["content"] == "问题"
    assert revised["source"] == "human-edit" and revised["model"] == "human-edit"
    assert "修订自 s1" in _record(DATASET, new_id)["meta"]
    # 旧记录原样保留
    original = _record(DATASET, "s1")
    assert original["payload"] == row["payload"] and original["sample_hash"] == row["sample_hash"]

    # 多模态 content 不允许压成文本
    with pytest.raises(ValueError, match="多模态"):
        rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                        2, "content", "纯文本", "req-2")
    # reasoning_content 仅限 assistant
    with pytest.raises(ValueError, match="assistant"):
        rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                        1, "reasoning_content", "x", "req-3")
    # 只允许 content / reasoning_content
    for bad_field in ("role", "toolCalls", "images", "metadata", ""):
        with pytest.raises(ValueError, match="field"):
            rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                            1, bad_field, "x", "req-bad-field")
    with pytest.raises(ValueError, match="index"):
        rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                        99, "content", "x", "req-bad-index")
    with pytest.raises(ValueError):
        rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                        1, "content", 123, "req-bad-text")
    assert _revision_ids(DATASET, "s1") == ["s1-r1"]  # 失败尝试不落版本


# ── 陈旧哈希与历史记录 ──────────────────────────────────────────────────────
def test_revise_field_stale_hash_and_legacy_records():
    from lib import review_center as rc

    _admin_key()
    rc.create_user("alice")
    _push(DATASET, _sample("s1", instruction="旧"))
    row = _record(DATASET, "s1")

    with pytest.raises(ValueError, match="[Ss]tale"):
        rc.revise_field(DATASET, "alice", row["record_id"], "deadbeef", 1, "content", "新", "req-stale")
    assert _revision_ids(DATASET, "s1") == []

    # 内容在审核视图加载后被重推（尚未有人投票，upsert 允许）→ 旧哈希必须失败
    rc.add_records(DATASET, [{"sample_id": "s1", "instruction": "已更新", "conversation": "c",
                              "meta": "", "sample_hash": "h2", "payload": row["payload"]}])
    with pytest.raises(ValueError, match="[Ss]tale"):
        rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"], 1, "content", "新", "req-stale-2")
    fresh = _record(DATASET, "s1")
    assert rc.revise_field(DATASET, "alice", fresh["record_id"], "h2",
                           1, "content", "新", "req-fresh") == "s1-r1"

    with pytest.raises(ValueError, match="not found"):
        rc.revise_field(DATASET, "alice", 999999, "x", 0, "content", "y", "req-missing")

    # 历史 payload（messages 数组、空指纹）沿用 submit 的宽松哈希规则
    rc.add_records(DATASET, [{"sample_id": "legacy", "meta": "images=0", "sample_hash": "",
                              "payload": json.dumps([{"role": "user", "content": "q"},
                                                     {"role": "assistant", "content": "a"}],
                                                    ensure_ascii=False)}])
    lrow = _record(DATASET, "legacy")
    assert rc.revise_field(DATASET, "alice", lrow["record_id"], "",
                           1, "content", "a2", "req-legacy") == "legacy-r1"


# ── request_id 幂等（UI 重试不产生重复版本） ────────────────────────────────
def test_revise_field_request_id_idempotent():
    from lib import review_center as rc

    _admin_key()
    rc.create_user("alice")
    rc.create_user("bob")
    _push(DATASET, _sample("s1"))
    row = _record(DATASET, "s1")

    first = rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                            2, "reasoning_content", "思考1", "retry-1")
    assert first == "s1-r1"
    assert rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                           2, "reasoning_content", "思考1", "retry-1") == "s1-r1"
    assert _revision_ids(DATASET, "s1") == ["s1-r1"]

    # 同一 request_id 不同 payload → 拒绝，不新增版本
    with pytest.raises(ValueError, match="request_id"):
        rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                        2, "reasoning_content", "思考2", "retry-1")
    assert _revision_ids(DATASET, "s1") == ["s1-r1"]

    # 重放命中收据：即使基准内容之后变化，也返回首次结果（已生效的变更不重新评估）
    rc.add_records(DATASET, [{"sample_id": "s1", "instruction": "别的内容", "conversation": "c",
                              "meta": "", "sample_hash": "other", "payload": row["payload"]}])
    assert rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                           2, "reasoning_content", "思考1", "retry-1") == "s1-r1"

    # 收据按 (dataset, username, request_id) 隔离：bob 用同 id 得到自己的版本
    assert rc.revise_field(DATASET, "bob", row["record_id"], "other",
                           2, "reasoning_content", "思考1", "retry-1") == "s1-r2"
    assert sorted(_revision_ids(DATASET, "s1")) == ["s1-r1", "s1-r2"]

    # 未授权用户不能消费他人收据，也不能借重试获得结果
    rc.create_user("carol")
    rc.add_records("docs_review", [{"sample_id": "d1", "instruction": "x", "conversation": "y",
                                   "meta": "", "sample_hash": ""}])
    with pytest.raises(PermissionError):
        rc.revise_field("docs_review", "carol", 1, "", 0, "content", "z", "retry-1")
    # request_id 校验
    for bad in ("", None, 123, "x" * 200):
        with pytest.raises(ValueError, match="request_id"):
            rc.revise_field(DATASET, "alice", row["record_id"], "other",
                            2, "reasoning_content", "思考1", bad)


# ── 并发：唯一版本号 / 同 request_id 只落一次 ───────────────────────────────
def test_revise_field_concurrent_versions_and_shared_request():
    from concurrent.futures import ThreadPoolExecutor
    from lib import review_center as rc

    _admin_key()
    rc.create_user("alice")
    _push(DATASET, _sample("s1"))
    row = _record(DATASET, "s1")

    def one(i):
        return rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                               2, "reasoning_content", f"思考{i}", f"req-{i}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(one, range(6)))
    assert sorted(ids) == [f"s1-r{i}" for i in range(1, 7)]
    assert sorted(_revision_ids(DATASET, "s1")) == sorted(ids)
    contents = {json.loads(_record(DATASET, s)["payload"])["messages"][2]["reasoning_content"]
                for s in ids}
    assert contents == {f"思考{i}" for i in range(6)}  # 每个版本内容独立，无覆盖

    def retry(_):
        return rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                               2, "reasoning_content", "并发重试", "retry-same")

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(retry, range(6)))
    assert set(results) == {"s1-r7"}
    assert _revision_ids(DATASET, "s1").count("s1-r7") == 1


def test_revise_field_transaction_serializes_without_process_lock(monkeypatch):
    """去掉进程内 RLock 也依赖同一 BEGIN IMMEDIATE 事务串行化：版本不重复、无死锁。"""
    import contextlib
    from concurrent.futures import ThreadPoolExecutor
    from lib import review_center as rc

    _admin_key()
    rc.create_user("alice")
    _push(DATASET, _sample("s1"))
    row = _record(DATASET, "s1")
    monkeypatch.setattr(rc, "_lock", contextlib.nullcontext())

    def one(i):
        return rc.revise_field(DATASET, "alice", row["record_id"], row["sample_hash"],
                               2, "reasoning_content", f"并发{i}", f"no-lock-{i}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(one, range(4)))
    assert sorted(ids) == [f"s1-r{i}" for i in range(1, 5)]
    assert sorted(_revision_ids(DATASET, "s1")) == sorted(ids)
