"""审核中心（单进程融合）：SQLite 存储 + 内置 HTTP API + 静态预览文件服务。

替代原 Redis+Elasticsearch+Argilla 三进程协作栈——一个控制台进程（或独立
`df review-server`）同时提供：
  - 审核数据存储（SQLite：用户/记录/响应，按工作区 dataset 列隔离）
  - 协作者远程 API（拉取待审/以身份提交，Authorization: Bearer agent.<key>）
  - 静态预览文件（/files/<path> → data/output，融合原 preview http.server）

零外部依赖（sqlite3/urllib/http.server 均为标准库），无 JVM、无看门狗。
"""
from __future__ import annotations

import json
import pathlib
import re
import secrets
import sqlite3
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "review_center.db"
OUT_ROOT = ROOT / "data" / "output"
DEFAULT_PORT = 6900
DEFAULT_ADMIN_KEY = "distill.apikey"

_SAFE_DS = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


# ── 存储（SQLite；CLI 同机直连与 HTTP 服务共用） ────────────────────────────
def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _dedupe_responses() -> None:
    """历史重复响应收敛（每记录每用户保留最早一条；唯一索引建立前的遗留清理）。"""
    with _conn() as con:
        con.execute("""
            DELETE FROM responses WHERE id NOT IN (
                SELECT MIN(id) FROM responses GROUP BY record_id, username
            )
        """)


def init_db() -> None:
    with _lock, _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            username TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'annotator',
            api_key TEXT UNIQUE, created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS records(
            id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT NOT NULL,
            sample_id TEXT NOT NULL, instruction TEXT, conversation TEXT,
            meta TEXT, suggestion TEXT,
            UNIQUE(dataset, sample_id)
        );
        CREATE TABLE IF NOT EXISTS responses(
            id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT NOT NULL,
            record_id INTEGER NOT NULL, username TEXT NOT NULL,
            decision TEXT NOT NULL, reason TEXT, model TEXT,
            at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_records_ds ON records(dataset);
        CREATE INDEX IF NOT EXISTS idx_responses_ds ON responses(dataset, record_id);
        """)
    # 先收敛历史重复（唯一索引建立前的遗留），再建唯一约束
    _dedupe_responses()
    with _lock, _conn() as con:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_resp_user ON responses(record_id, username)")


def ensure_admin(admin_key: str = DEFAULT_ADMIN_KEY) -> str:
    """幂等：admin 账号恒在（key 与环境变量/默认一致）。返回其 key。"""
    init_db()
    with _lock, _conn() as con:
        row = con.execute("SELECT api_key FROM users WHERE username='admin'").fetchone()
        if row:
            return row[0]
        con.execute("INSERT INTO users(username, role, api_key) VALUES('admin','admin',?)", (admin_key,))
        return admin_key


def create_user(username: str, role: str = "annotator", api_key: str | None = None) -> str:
    """创建协作者（幂等：已存在则更新角色；每人唯一 api_key=审计锚点）。"""
    username = username.strip()
    if not re.match(r"^[A-Za-z0-9_-]{1,32}$", username):
        raise ValueError(f"用户名只允许字母/数字/下划线/连字符：{username!r}")
    init_db()
    key = api_key or ("agent." + secrets.token_urlsafe(24))
    with _lock, _conn() as con:
        row = con.execute("SELECT api_key FROM users WHERE username=?", (username,)).fetchone()
        if row:
            con.execute("UPDATE users SET role=?, api_key=? WHERE username=?", (role, key, username))
        else:
            con.execute("INSERT INTO users(username, role, api_key) VALUES(?,?,?)", (username, role, key))
    return key


def _auth(request_headers: Dict[str, str]) -> str:
    """Bearer agent.<key> → username；无凭据/未知 key 抛 ValueError。"""
    auth = request_headers.get("Authorization", "") or request_headers.get("X-Api-Key", "")
    token = auth.removeprefix("Bearer ").strip()
    with _lock, _conn() as con:
        row = con.execute("SELECT username, role FROM users WHERE api_key=?", (token,)).fetchone()
    if not row:
        raise PermissionError("无效的 API key")
    return row[0]


def add_records(dataset: str, records: List[Dict[str, Any]]) -> int:
    """样本入库（按 dataset+sample_id upsert；suggestion=judge 建议值）。返回写入条数。"""
    if not _SAFE_DS.match(dataset):
        raise ValueError(f"数据集名不合法：{dataset!r}")
    init_db()
    with _lock, _conn() as con:
        for r in records:
            con.execute(
                """INSERT INTO records(dataset, sample_id, instruction, conversation, meta, suggestion)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(dataset, sample_id) DO UPDATE SET
                     instruction=excluded.instruction, conversation=excluded.conversation,
                     meta=excluded.meta, suggestion=excluded.suggestion""",
                (dataset, r["sample_id"], r.get("instruction", ""), r.get("conversation", ""),
                 r.get("meta", ""), r.get("suggestion", "")),
            )
    return len(records)


def pending(dataset: str, username: str, batch: int = 10) -> List[Dict[str, Any]]:
    """该用户尚未提交过响应的记录（身份=username，跳过已审）。"""
    with _lock, _conn() as con:
        rows = con.execute(
            """SELECT r.id, r.sample_id, r.instruction, r.conversation, r.meta, r.suggestion
               FROM records r
               WHERE r.dataset=? AND NOT EXISTS(
                   SELECT 1 FROM responses x WHERE x.record_id=r.id AND x.username=?
               )
               ORDER BY r.id LIMIT ?""",
            (dataset, username, batch),
        ).fetchall()
    return [{"record_id": row[0], "sample_id": row[1], "instruction": row[2],
             "conversation": row[3], "meta": row[4], "suggestion": row[5]} for row in rows]


def submit(dataset: str, username: str, decisions: List[Dict[str, Any]]) -> int:
    """以 username 身份提交判定（decision keep/reject + reason），可审计。

    幂等：同一用户对同一记录的重复提交被唯一约束忽略（返回实际写入数）。"""
    init_db()
    n = 0
    with _lock, _conn() as con:
        for d in decisions:
            cur = con.execute(
                """INSERT OR IGNORE INTO responses(dataset, record_id, username, decision, reason, model)
                   VALUES(?,?,?,?,?,?)""",
                (dataset, d["record_id"], username, d["decision"],
                 str(d.get("reason", ""))[:500], d.get("model", "")),
            )
            n += cur.rowcount
    return n


def responses(dataset: str) -> List[Dict[str, str]]:
    """中心侧拉全部响应（按样本+身份+理由，供 G3 汇总统计）。"""
    with _lock, _conn() as con:
        rows = con.execute(
            """SELECT r.sample_id, x.decision, x.reason, x.username, x.at
               FROM responses x JOIN records r ON r.id = x.record_id
               WHERE x.dataset=? ORDER BY x.at""",
            (dataset,),
        ).fetchall()
    return [{"sample_id": row[0], "decision": row[1], "reason": row[2],
             "username": row[3], "at": row[4]} for row in rows]


def user_rows() -> List[Dict[str, str]]:
    init_db()
    with _lock, _conn() as con:
        rows = con.execute("SELECT username, role, api_key, created_at FROM users ORDER BY username").fetchall()
    return [{"username": r[0], "role": r[1], "api_key": r[2], "created_at": r[3]} for r in rows]


# ── HTTP API（协作者远程审核；/files 融合静态预览） ─────────────────────────
class _Handler(BaseHTTPRequestHandler):
    server_version = "df-review-center/1.0"

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg: str, status: int = 400) -> None:
        self._json({"error": msg}, status)

    def _auth_or_401(self) -> Optional[str]:
        try:
            return _auth({k: v for k, v in self.headers.items()})
        except PermissionError as e:
            self._error(str(e), 401)
            return None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self._json({"ok": True, "service": "df-review-center"})
            return
        if parsed.path == "/api/me":
            me = self._auth_or_401()
            if me:
                self._json({"id": me, "username": me})
            return
        if parsed.path == "/api/pending":
            me = self._auth_or_401()
            if not me:
                return
            q = urllib.parse.parse_qs(parsed.query)
            dataset = (q.get("dataset") or ["rollout_review"])[0]
            batch = int((q.get("batch") or ["10"])[0])
            self._json({"records": pending(dataset, me, batch)})
            return
        if parsed.path == "/api/responses":
            me = self._auth_or_401()
            if not me:
                return
            q = urllib.parse.parse_qs(parsed.query)
            dataset = (q.get("dataset") or ["rollout_review"])[0]
            self._json({"responses": responses(dataset)})
            return
        if parsed.path.startswith("/files/"):
            self._serve_file(parsed.path[len("/files/"):])
            return
        self._error("not found", 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/submit":
            self._error("not found", 404)
            return
        me = self._auth_or_401()
        if not me:
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            dataset = body.get("dataset", "rollout_review")
            decisions = body.get("records", [])
            n = submit(dataset, me, decisions)
            self._json({"submitted": n, "username": me})
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._error(f"请求不合法: {e}", 400)

    def _serve_file(self, rel: str) -> None:
        rel = urllib.parse.unquote(rel).lstrip("/")
        target = (OUT_ROOT / rel).resolve()
        if not str(target).startswith(str(OUT_ROOT.resolve())) or not target.is_file():
            self._error("file not found", 404)
            return
        try:
            data = target.read_bytes()
        except OSError:
            self._error("file read failed", 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if target.suffix == ".html" else "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:  # 安静：不刷 stdout
        pass


def serve(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> None:
    """阻塞运行 HTTP 服务（独立模式：df review-server）。"""
    ensure_admin()
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"审核中心 API: http://{host}:{port}（/health /api/pending /api/submit /api/responses /files/）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


def start_in_thread(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> bool:
    """控制台进程内后台线程托管（融合：一个进程同时提供 UI 与协作 API）。幂等。"""
    global _thread
    if _thread and _thread.is_alive():
        return False
    ensure_admin()
    try:
        ThreadingHTTPServer.allow_reuse_address = True
        httpd = ThreadingHTTPServer((host, port), _Handler)
    except OSError:
        return False  # 端口被占（如旧服务未下线）：控制台照常，健康页显示 ❌
    _thread = threading.Thread(target=httpd.serve_forever, name="df-review-center", daemon=True)
    _thread.start()
    return True
