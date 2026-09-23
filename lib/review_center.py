"""Local SQLite review store and authenticated collaborator API."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from typing import List
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "review_center.db"
OUT_ROOT = ROOT / "data" / "output"
DEFAULT_PORT = 6900
_SAFE_DS = re.compile(r"[A-Za-z0-9_-]{1,64}")
_lock = threading.RLock()
_thread = None
_httpd = None
MAX_BODY = 8 * 1024 * 1024
QUEUE_STATUSES = ("pending", "reviewed", "all")
QUEUE_LIMIT_MAX = 200
QUEUE_QUERY_MAX = 200
QUEUE_INSTRUCTION_CHARS = 200
QUEUE_META_CHARS = 300
MAX_REQUEST_ID = 128
EDITABLE_FIELDS = ("content", "reasoning_content")


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            yield connection
    finally:
        connection.close()


def _key_hash(key):
    return "sha256:" + hashlib.sha256(key.encode()).hexdigest()


def init_db():
    with _lock, _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                username TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'annotator',
                api_key TEXT UNIQUE, created_at TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE IF NOT EXISTS records(
                id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT NOT NULL,
                sample_id TEXT NOT NULL, instruction TEXT, conversation TEXT,
                meta TEXT, suggestion TEXT, sample_hash TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '',
                UNIQUE(dataset, sample_id));
            CREATE TABLE IF NOT EXISTS responses(
                id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT NOT NULL,
                record_id INTEGER NOT NULL, username TEXT NOT NULL, decision TEXT NOT NULL,
                reason TEXT, model TEXT, at TEXT NOT NULL DEFAULT (datetime('now')),
                sample_hash TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS dataset_users(
                dataset TEXT NOT NULL, username TEXT NOT NULL,
                PRIMARY KEY(dataset, username));
            CREATE TABLE IF NOT EXISTS mutation_receipts(
                dataset TEXT NOT NULL, username TEXT NOT NULL, request_id TEXT NOT NULL,
                payload_digest TEXT NOT NULL, result TEXT NOT NULL,
                at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(dataset, username, request_id));
            CREATE INDEX IF NOT EXISTS idx_records_ds ON records(dataset);
            CREATE INDEX IF NOT EXISTS idx_responses_ds ON responses(dataset, record_id);
        """)
        for table in ("records", "responses"):
            columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if "sample_hash" not in columns:
                con.execute(f"ALTER TABLE {table} ADD COLUMN sample_hash TEXT NOT NULL DEFAULT ''")
        if "payload" not in {r[1] for r in con.execute("PRAGMA table_info(records)")}:
            con.execute("ALTER TABLE records ADD COLUMN payload TEXT NOT NULL DEFAULT ''")
        # Legacy rows stay intact. Migration must never discard historical responses.
        try:
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_resp_user ON responses(record_id, username)")
        except sqlite3.IntegrityError as error:
            raise ValueError("Duplicate historical responses: back up and reconcile explicitly") from error
        for username, key in con.execute("SELECT username, api_key FROM users").fetchall():
            if key and not key.startswith("sha256:"):
                con.execute("UPDATE users SET api_key=? WHERE username=?", (_key_hash(key), username))
            con.execute("INSERT OR IGNORE INTO dataset_users VALUES('rollout_review', ?)", (username,))


def ensure_admin(admin_key=None):
    init_db()
    with _lock, _conn() as con:
        if con.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            return None
        key = admin_key or os.environ.get("REVIEW_ADMIN_KEY") or "agent." + secrets.token_urlsafe(32)
        con.execute("INSERT INTO users(username,role,api_key) VALUES('admin','admin',?)", (_key_hash(key),))
        return key


def create_user(username, role="annotator", api_key=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", username or "") or role not in ("admin", "annotator"):
        raise ValueError("Invalid username or role")
    init_db()
    with _lock, _conn() as con:
        if con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("User already exists; credentials were not changed")
        key = api_key or "agent." + secrets.token_urlsafe(32)
        con.execute("INSERT INTO users(username,role,api_key) VALUES(?,?,?)", (username, role, _key_hash(key)))
        con.execute("INSERT INTO dataset_users VALUES('rollout_review',?)", (username,))
    return key


def grant(username, dataset):
    _validate_dataset(dataset)
    init_db()
    with _lock, _conn() as con:
        if not con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("Unknown user")
        con.execute("INSERT OR IGNORE INTO dataset_users VALUES(?,?)", (dataset, username))


def _validate_dataset(dataset):
    if not isinstance(dataset, str) or not _SAFE_DS.fullmatch(dataset):
        raise ValueError("Invalid dataset")


def _authorize(con, username, dataset=None, admin=False):
    user = con.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        raise PermissionError("Unknown user")
    if user[0] == "admin":
        return
    if admin or (dataset and not con.execute("SELECT 1 FROM dataset_users WHERE dataset=? AND username=?", (dataset, username)).fetchone()):
        raise PermissionError("Dataset access denied")


def _token_identity(token):
    """API key → (username, role)；与 HTTP 头鉴权共用同一套哈希与查询路径。"""
    if not token:
        raise PermissionError("Missing API key")
    with _conn() as con:
        user = con.execute("SELECT username, role FROM users WHERE api_key=?", (_key_hash(token),)).fetchone()
    if not user:
        raise PermissionError("Invalid API key")
    return user


def authenticate(api_key):
    """校验协作者 API key，返回 ``{"username", "role"}``；缺失/无效抛 PermissionError。

    供审核 UX 在建立会话时拿到身份与角色（admin 可访问全部数据集，annotator 需显式授权）。
    """
    if not isinstance(api_key, str):
        raise PermissionError("Missing API key")
    username, role = _token_identity(api_key)
    return {"username": username, "role": role}


def _auth(headers):
    headers = {k.lower(): v for k, v in headers.items()}
    authorization = headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else headers.get("x-api-key", "")
    return _token_identity(token)[0]


def add_records(dataset, records):
    _validate_dataset(dataset)
    init_db()
    with _lock, _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        for row in records:
            if not isinstance(row.get("sample_id"), str) or not row["sample_id"]:
                raise ValueError("sample_id required")
            existing = con.execute("SELECT id,instruction,conversation,sample_hash FROM records WHERE dataset=? AND sample_id=?", (dataset, row["sample_id"])).fetchone()
            if existing:
                changed = existing[1:3] != (row.get("instruction", ""), row.get("conversation", "")) or (existing[3] and existing[3] != row.get("sample_hash", ""))
                if changed and con.execute("SELECT 1 FROM responses WHERE record_id=?", (existing[0],)).fetchone():
                    raise ValueError("Reviewed content is immutable; publish changed content with a new sample_id")
                # A legacy unbound response must not acquire approval of a new content hash.
                if con.execute("SELECT 1 FROM responses WHERE record_id=?", (existing[0],)).fetchone():
                    continue
            con.execute("""INSERT INTO records(dataset,sample_id,instruction,conversation,meta,suggestion,sample_hash,payload)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(dataset,sample_id) DO UPDATE SET
                instruction=excluded.instruction,conversation=excluded.conversation,meta=excluded.meta,
                suggestion=excluded.suggestion,sample_hash=excluded.sample_hash,payload=excluded.payload""",
                (dataset,row["sample_id"],row.get("instruction",""),row.get("conversation",""),row.get("meta",""),
                 row.get("suggestion",""),row.get("sample_hash",""),row.get("payload","")))
    return len(records)


def pending(dataset, username, batch=10):
    _validate_dataset(dataset)
    if type(batch) is not int or not 1 <= batch <= 100:
        raise ValueError("batch must be 1..100")
    init_db()
    with _conn() as con:
        _authorize(con, username, dataset)
        rows = con.execute("""SELECT r.id,r.sample_id,r.instruction,r.conversation,r.meta,r.suggestion,r.sample_hash,r.payload
            FROM records r WHERE dataset=? AND NOT EXISTS(
            SELECT 1 FROM responses x WHERE x.record_id=r.id AND x.username=?) ORDER BY r.id LIMIT ?""",
            (dataset, username, batch)).fetchall()
    keys = ("record_id", "sample_id", "instruction", "conversation", "meta", "suggestion", "sample_hash", "payload")
    return [dict(zip(keys, r)) for r in rows]


def sample_ids_like(dataset: str, prefix: str) -> List[str]:
    """列出 dataset 下 sample_id 以 prefix 开头的全部 id（修订号计算用）。"""
    init_db()
    with _conn() as con:
        rows = con.execute("SELECT sample_id FROM records WHERE dataset=? AND sample_id LIKE ?",
                           (dataset, prefix + "%")).fetchall()
    return [r[0] for r in rows]


def _allocate_revision(con, dataset, base_sample_id, build_record, marker="-r"):
    """在调用方已持有的写事务内分配 ``<base>-rN`` 并插入（普通 INSERT，不 upsert）。"""
    prefix = base_sample_id + marker
    rows = con.execute(
        "SELECT sample_id FROM records WHERE dataset=? AND substr(sample_id,1,?)=?",
        (dataset, len(prefix), prefix)).fetchall()
    numbers = []
    for (sample_id,) in rows:
        tail = sample_id[len(prefix):]
        if tail.isdigit():
            numbers.append(int(tail))
    new_id = f"{prefix}{max(numbers, default=0) + 1}"
    record = build_record(new_id)
    if not isinstance(record, dict) or record.get("sample_id") != new_id:
        raise ValueError("build_record must return a record with the allocated sample_id")
    if con.execute("SELECT 1 FROM records WHERE dataset=? AND sample_id=?", (dataset, new_id)).fetchone():
        raise ValueError(f"Revision id already exists: {new_id}")
    con.execute("""INSERT INTO records(dataset,sample_id,instruction,conversation,meta,suggestion,sample_hash,payload)
        VALUES(?,?,?,?,?,?,?,?)""",
        (dataset, new_id, record.get("instruction", ""), record.get("conversation", ""),
         record.get("meta", ""), record.get("suggestion", ""), record.get("sample_hash", ""),
         record.get("payload", "")))
    return new_id


def add_revision(dataset, base_sample_id, build_record, marker="-r", *, connection=None):
    """原子分配下一个 ``<base>-rN`` 并插入修订记录（并发安全，绝不覆盖同 id）。

    与 add_records 的关键区别：序号分配与插入在同一个 BEGIN IMMEDIATE 事务内完成，
    并发调用被 SQLite 写锁串行化，各自拿到唯一序号；插入用普通 INSERT（不 upsert），
    任何同 id 冲突（含历史评审内容）直接报错而不是静默覆盖。
    ``build_record(new_id)`` 必须返回与 add_records 同形的记录 dict 且 sample_id=new_id。
    返回新 sample_id。

    ``connection`` 为内部事务路径：调用方已持有 BEGIN IMMEDIATE 写事务与 _lock 时传入
    该连接，复用同一事务而不是另开连接抢写锁（同线程重入 _lock 后再开新连接会死锁）。
    公开调用不传该参数，行为与历史一致。
    """
    _validate_dataset(dataset)
    if not isinstance(base_sample_id, str) or not base_sample_id:
        raise ValueError("base_sample_id required")
    if connection is not None:
        return _allocate_revision(connection, dataset, base_sample_id, build_record, marker)
    init_db()
    with _lock, _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        return _allocate_revision(con, dataset, base_sample_id, build_record, marker)


def submit(dataset, username, decisions):
    _validate_dataset(dataset)
    if not isinstance(decisions, list) or len(decisions) > 100:
        raise ValueError("records must be a list of at most 100 decisions")
    init_db()
    with _lock, _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        _authorize(con, username, dataset)
        verified = []
        for row in decisions:
            if not isinstance(row, dict) or row.get("decision") not in ("keep", "reject") or type(row.get("record_id")) is not int:
                raise ValueError("Invalid record_id or decision")
            if not isinstance(row.get("reason"), str) or not row["reason"].strip() or len(row["reason"]) > 16000:
                raise ValueError("A reason of 1..16000 characters is required")
            record = con.execute("SELECT sample_hash FROM records WHERE id=? AND dataset=?", (row["record_id"], dataset)).fetchone()
            if not record or (record[0] and row.get("sample_hash") != record[0]):
                raise ValueError("Record absent, wrong dataset, or stale content hash")
            verified.append((row, record[0]))
        n = 0
        for row, fingerprint in verified:
            n += con.execute("""INSERT OR IGNORE INTO responses(dataset,record_id,username,decision,reason,model,sample_hash)
                VALUES(?,?,?,?,?,?,?)""", (dataset,row["record_id"],username,row["decision"],row["reason"],row.get("model",""),fingerprint)).rowcount
    return n


def responses(dataset):
    _validate_dataset(dataset)
    init_db()
    with _conn() as con:
        rows = con.execute("""SELECT r.sample_id,x.decision,x.reason,x.username,x.at,x.model,x.sample_hash
            FROM responses x JOIN records r ON r.id=x.record_id AND r.dataset=x.dataset
            WHERE x.dataset=? ORDER BY x.id""", (dataset,)).fetchall()
    return [dict(zip(("sample_id","decision","reason","username","at","model","sample_hash"), r)) for r in rows]


def _like_contains(text):
    """LIKE 字面值包含搜索：转义通配符（配合 ESCAPE '\\'），用户输入不放大匹配范围。"""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


def queue_page(dataset, username, *, status="pending", query="", offset=0, limit=30):
    """审核队列分页（摘要级），返回 ``{items,total,pending,reviewed,offset,limit}``。

    - status 仅 pending（本人未审）/ reviewed（本人已审）/ all；
    - query 对 sample_id 与 instruction 做**字面值**包含搜索（%/_/\\ 已转义）；
    - items 每项仅含 record_id、sample_id、instruction 摘要、sample_hash、meta 摘要、
      decision、reason（本人在该记录的判定）；SQL 只取摘要列 + 本人 join，
      不读 payload/conversation，也不展开全部 responses；
    - pending/reviewed 为当前数据集下该用户的整体计数，total 为过滤后的条数。
    """
    _validate_dataset(dataset)
    if status not in QUEUE_STATUSES:
        raise ValueError("status must be one of " + "/".join(QUEUE_STATUSES))
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    if len(query) > QUEUE_QUERY_MAX:
        raise ValueError(f"query must be at most {QUEUE_QUERY_MAX} characters")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if type(limit) is not int or not 1 <= limit <= QUEUE_LIMIT_MAX:
        raise ValueError(f"limit must be 1..{QUEUE_LIMIT_MAX}")
    init_db()
    reviewed = ("EXISTS(SELECT 1 FROM responses x WHERE x.record_id=r.id "
                "AND x.dataset=r.dataset AND x.username=?)")
    where = ["r.dataset=?"]
    params = [dataset]
    if status == "pending":
        where.append("NOT " + reviewed)
        params.append(username)
    elif status == "reviewed":
        where.append(reviewed)
        params.append(username)
    if query.strip():
        pattern = _like_contains(query.strip())
        where.append("(r.sample_id LIKE ? ESCAPE '\\' OR r.instruction LIKE ? ESCAPE '\\')")
        params.extend((pattern, pattern))
    clause = " AND ".join(where)
    with _conn() as con:
        _authorize(con, username, dataset)
        reviewed_total, pending_total = con.execute(
            f"""SELECT SUM(CASE WHEN {reviewed} THEN 1 ELSE 0 END),
                SUM(CASE WHEN NOT {reviewed} THEN 1 ELSE 0 END)
            FROM records r WHERE r.dataset=?""",
            (username, username, dataset)).fetchone()
        total = con.execute(f"SELECT COUNT(*) FROM records r WHERE {clause}", params).fetchone()[0]
        rows = con.execute(
            f"""SELECT r.id, r.sample_id,
                substr(COALESCE(r.instruction,''),1,{QUEUE_INSTRUCTION_CHARS}),
                r.sample_hash,
                substr(COALESCE(r.meta,''),1,{QUEUE_META_CHARS}),
                x.decision, x.reason
            FROM records r LEFT JOIN responses x
              ON x.record_id=r.id AND x.dataset=r.dataset AND x.username=?
            WHERE {clause} ORDER BY r.id LIMIT ? OFFSET ?""",
            [username, *params, limit, offset]).fetchall()
    keys = ("record_id", "sample_id", "instruction", "sample_hash", "meta", "decision", "reason")
    return {
        "items": [dict(zip(keys, row)) for row in rows],
        "total": int(total),
        "pending": int(pending_total or 0),
        "reviewed": int(reviewed_total or 0),
        "offset": offset,
        "limit": limit,
    }


def queue_position(dataset, username, *, status="pending", query="", sample_id=None):
    """sample_id 在过滤队列中的 0 基位置（与 queue_page 同序：``ORDER BY r.id``）。

    单条 SQL（相关子查询 COUNT + 存在性）按 record id 计数，不扫全库、不读 payload；
    记录不存在或不在该过滤结果（pending/reviewed/all + query）中时返回 None。
    """
    _validate_dataset(dataset)
    if status not in QUEUE_STATUSES:
        raise ValueError("status must be one of " + "/".join(QUEUE_STATUSES))
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    if len(query) > QUEUE_QUERY_MAX:
        raise ValueError(f"query must be at most {QUEUE_QUERY_MAX} characters")
    if not isinstance(sample_id, str) or not sample_id:
        raise ValueError("sample_id must be a non-empty string")
    init_db()
    reviewed = ("EXISTS(SELECT 1 FROM responses x WHERE x.record_id=r.id "
                "AND x.dataset=r.dataset AND x.username=?)")
    where = ["r.dataset=?"]
    params = [dataset]
    if status == "pending":
        where.append("NOT " + reviewed)
        params.append(username)
    elif status == "reviewed":
        where.append(reviewed)
        params.append(username)
    if query.strip():
        pattern = _like_contains(query.strip())
        where.append("(r.sample_id LIKE ? ESCAPE '\\' OR r.instruction LIKE ? ESCAPE '\\')")
        params.extend((pattern, pattern))
    clause = " AND ".join(where)
    with _conn() as con:
        _authorize(con, username, dataset)
        row = con.execute(
            f"""SELECT
                (SELECT COUNT(*) FROM records r WHERE {clause} AND r.id < t.id),
                (SELECT 1 FROM records r WHERE {clause} AND r.id = t.id)
            FROM records t WHERE t.dataset=? AND t.sample_id=?""",
            [*params, *params, dataset, sample_id]).fetchone()
    if not row or row[1] is None:
        return None
    return int(row[0])


def get_record(dataset, username, *, record_id=None, sample_id=None):
    """授权读取单条完整记录（pending 的既有字段 + 本人在该记录的 decision/reason）。

    必须且只能给 record_id / sample_id 之一；记录不存在或选择器不合法抛 ValueError，
    数据集未授权抛 PermissionError。"""
    _validate_dataset(dataset)
    if (record_id is None) == (sample_id is None):
        raise ValueError("Provide exactly one of record_id or sample_id")
    if record_id is not None and (type(record_id) is not int or record_id <= 0):
        raise ValueError("record_id must be a positive integer")
    if sample_id is not None and (not isinstance(sample_id, str) or not sample_id):
        raise ValueError("sample_id must be a non-empty string")
    init_db()
    column, value = ("r.id", record_id) if record_id is not None else ("r.sample_id", sample_id)
    with _conn() as con:
        _authorize(con, username, dataset)
        row = con.execute(
            f"""SELECT r.id,r.sample_id,r.instruction,r.conversation,r.meta,r.suggestion,
                r.sample_hash,r.payload,x.decision,x.reason
            FROM records r LEFT JOIN responses x
              ON x.record_id=r.id AND x.dataset=r.dataset AND x.username=?
            WHERE r.dataset=? AND {column}=?""",
            (username, dataset, value)).fetchone()
    if not row:
        raise ValueError("Record not found")
    keys = ("record_id", "sample_id", "instruction", "conversation", "meta", "suggestion",
            "sample_hash", "payload", "decision", "reason")
    return dict(zip(keys, row))


def _payload_digest(payload):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _field_revision_builder(record, index, field, text, reviewer):
    """构造 ``build_record(new_id)``：只改一条消息的一个可编辑文本字段，其余深拷贝保留。

    校验/打包与 review.revise_sample 同管线：unpack_record 完整还原（images/tools/
    其余元数据不丢），validate_edits 服务端强制白名单（非 assistant 不能改
    reasoning_content；多模态结构不能压成文本）；build_records 重新计算内容哈希。
    """
    from lib.domain import review_edit as editor
    from lib.review import build_records

    sample = editor.unpack_record(record)
    messages = sample.get("messages")
    if not isinstance(messages, list) or not 0 <= index < len(messages):
        raise ValueError("Message index out of range")
    message = messages[index]
    if not isinstance(message, dict):
        raise ValueError("Message must be a JSON object")
    if field == "reasoning_content" and message.get("role") != "assistant":
        raise ValueError("reasoning_content can only be revised on assistant messages")
    edited = deepcopy(messages)
    edited[index][field] = text
    revised = editor.validate_edits(sample, edited)
    base = record["sample_id"]

    def build(new_id):
        revised["id"] = new_id
        revised["source"] = "human-edit"
        revised["model"] = "human-edit"
        revised["finish_reason"] = "edited"
        item = build_records([revised], {})[0]
        item["meta"] = (f"{record.get('meta', '')} | 修订自 {base}（{reviewer}）").strip(" |")
        return item

    return build


def revise_field(dataset, username, record_id, expected_hash, index, field, text, request_id):
    """人工修订单个字段 → 新版本 sample_id（内容哈希绑定、幂等、单事务）。

    - 仅允许 content / reasoning_content（后者仅 assistant 消息）纯文本；
      多模态结构化内容与任何元数据修改一律 ValueError（validate_edits 同款安全约束）；
    - expected_hash 与库中 sample_hash 不符（陈旧视图）时拒绝；空指纹历史记录沿用
      submit 的宽松规则；检查与版本分配在同一个 BEGIN IMMEDIATE 事务内完成；
    - request_id 幂等键：同一 (dataset, username, request_id) 且 payload 摘要相同 →
      直接返回首次结果（UI 重试不产生重复版本）；payload 不同 → ValueError。
    """
    _validate_dataset(dataset)
    if type(record_id) is not int or record_id <= 0:
        raise ValueError("record_id must be a positive integer")
    if not isinstance(expected_hash, str):
        raise ValueError("expected_hash must be a string")
    if type(index) is not int or index < 0:
        raise ValueError("index must be a non-negative integer")
    if field not in EDITABLE_FIELDS:
        raise ValueError("field must be one of " + "/".join(EDITABLE_FIELDS))
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= MAX_REQUEST_ID:
        raise ValueError(f"request_id must be a string of 1..{MAX_REQUEST_ID} characters")
    digest = _payload_digest({"record_id": record_id, "expected_hash": expected_hash,
                              "index": index, "field": field, "text": text})
    init_db()
    with _lock, _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        _authorize(con, username, dataset)
        receipt = con.execute(
            "SELECT payload_digest,result FROM mutation_receipts WHERE dataset=? AND username=? AND request_id=?",
            (dataset, username, request_id)).fetchone()
        if receipt:
            if receipt[0] != digest:
                raise ValueError("request_id was already used with a different payload")
            return receipt[1]
        row = con.execute(
            """SELECT id,sample_id,instruction,conversation,meta,suggestion,sample_hash,payload
            FROM records WHERE dataset=? AND id=?""", (dataset, record_id)).fetchone()
        if not row:
            raise ValueError("Record not found")
        record = dict(zip(("record_id", "sample_id", "instruction", "conversation", "meta",
                           "suggestion", "sample_hash", "payload"), row))
        if record["sample_hash"] and expected_hash != record["sample_hash"]:
            raise ValueError("Stale content hash: reload the record before revising")
        new_id = _allocate_revision(con, dataset, record["sample_id"],
                                    _field_revision_builder(record, index, field, text, username))
        con.execute(
            "INSERT INTO mutation_receipts(dataset,username,request_id,payload_digest,result) VALUES(?,?,?,?,?)",
            (dataset, username, request_id, digest, new_id))
    return new_id


def user_rows():
    init_db()
    with _conn() as con:
        return [dict(zip(("username", "role", "created_at"), r)) for r in con.execute("SELECT username,role,created_at FROM users ORDER BY username")]


class _Handler(BaseHTTPRequestHandler):
    server_version = "df-review-center/2"

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self._json({"ok": True, "service": "df-review-center", "pid": os.getpid()})
            return
        try:
            user = _auth(dict(self.headers.items()))
        except PermissionError as error:
            self._json({"error": str(error)}, 401)
            return
        try:
            q = urllib.parse.parse_qs(parsed.query)
            dataset = q.get("dataset", ["rollout_review"])[0]
            if parsed.path == "/api/me":
                self._json({"id": user, "username": user})
            elif parsed.path == "/api/pending":
                self._json({"records": pending(dataset, user, int(q.get("batch", ["10"])[0]))})
            elif parsed.path == "/api/responses":
                with _conn() as con:
                    _authorize(con, user, admin=True)
                self._json({"responses": responses(dataset)})
            elif parsed.path.startswith("/files/"):
                with _conn() as con:
                    _authorize(con, user, admin=True)
                target = (OUT_ROOT / urllib.parse.unquote(parsed.path[7:])).resolve()
                if not target.is_relative_to(OUT_ROOT.resolve()) or not target.is_file():
                    self._json({"error": "file not found"}, 404)
                    return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "attachment")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json({"error": "not found"}, 404)
        except PermissionError as error:
            self._json({"error": str(error)}, 403)
        except (ValueError, TypeError, KeyError) as error:
            self._json({"error": str(error)}, 400)

    def do_POST(self):
        if self.path != "/api/submit":
            self._json({"error": "not found"}, 404)
            return
        try:
            user = _auth(dict(self.headers.items()))
        except PermissionError as error:
            self._json({"error": str(error)}, 401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                self._json({"error": "body exceeds limit or is empty"}, 413)
                return
            body = json.loads(self.rfile.read(length))
            n = submit(body["dataset"], user, body["records"])
            self._json({"submitted": n, "username": user})
        except PermissionError as error:
            self._json({"error": str(error)}, 403)
        except (ValueError, TypeError, KeyError) as error:
            self._json({"error": str(error)}, 400)

    def log_message(self, fmt, *args):
        pass


def start_in_thread(port=DEFAULT_PORT, host="127.0.0.1"):
    global _thread, _httpd
    with _lock:
        if _thread and _thread.is_alive():
            return False
        ensure_admin()
        server = ThreadingHTTPServer((host, port), _Handler)
        server.daemon_threads = True
        _httpd = server
        _thread = threading.Thread(target=server.serve_forever, name="df-review-center", daemon=True)
        _thread.start()
        return True


def stop_thread():
    global _thread, _httpd
    if _httpd:
        _httpd.shutdown()
        _httpd.server_close()
    if _thread:
        _thread.join(timeout=5)
    _thread = _httpd = None


def serve(port=DEFAULT_PORT, host="127.0.0.1"):
    start_in_thread(port, host)
    try:
        _thread.join()
    except KeyboardInterrupt:
        stop_thread()
