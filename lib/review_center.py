"""Local SQLite review store and authenticated collaborator API."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
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
                UNIQUE(dataset, sample_id));
            CREATE TABLE IF NOT EXISTS responses(
                id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT NOT NULL,
                record_id INTEGER NOT NULL, username TEXT NOT NULL, decision TEXT NOT NULL,
                reason TEXT, model TEXT, at TEXT NOT NULL DEFAULT (datetime('now')),
                sample_hash TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS dataset_users(
                dataset TEXT NOT NULL, username TEXT NOT NULL,
                PRIMARY KEY(dataset, username));
            CREATE INDEX IF NOT EXISTS idx_records_ds ON records(dataset);
            CREATE INDEX IF NOT EXISTS idx_responses_ds ON responses(dataset, record_id);
        """)
        for table in ("records", "responses"):
            columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if "sample_hash" not in columns:
                con.execute(f"ALTER TABLE {table} ADD COLUMN sample_hash TEXT NOT NULL DEFAULT ''")
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


def _auth(headers):
    headers = {k.lower(): v for k, v in headers.items()}
    authorization = headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else headers.get("x-api-key", "")
    if not token:
        raise PermissionError("Missing API key")
    with _conn() as con:
        user = con.execute("SELECT username FROM users WHERE api_key=?", (_key_hash(token),)).fetchone()
    if not user:
        raise PermissionError("Invalid API key")
    return user[0]


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
            con.execute("""INSERT INTO records(dataset,sample_id,instruction,conversation,meta,suggestion,sample_hash)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(dataset,sample_id) DO UPDATE SET
                instruction=excluded.instruction,conversation=excluded.conversation,meta=excluded.meta,
                suggestion=excluded.suggestion,sample_hash=excluded.sample_hash""",
                (dataset,row["sample_id"],row.get("instruction",""),row.get("conversation",""),row.get("meta",""),row.get("suggestion",""),row.get("sample_hash","")))
    return len(records)


def pending(dataset, username, batch=10):
    _validate_dataset(dataset)
    if type(batch) is not int or not 1 <= batch <= 100:
        raise ValueError("batch must be 1..100")
    init_db()
    with _conn() as con:
        _authorize(con, username, dataset)
        rows = con.execute("""SELECT r.id,r.sample_id,r.instruction,r.conversation,r.meta,r.suggestion,r.sample_hash
            FROM records r WHERE dataset=? AND NOT EXISTS(
            SELECT 1 FROM responses x WHERE x.record_id=r.id AND x.username=?) ORDER BY r.id LIMIT ?""",
            (dataset, username, batch)).fetchall()
    keys = ("record_id", "sample_id", "instruction", "conversation", "meta", "suggestion", "sample_hash")
    return [dict(zip(keys, r)) for r in rows]


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
