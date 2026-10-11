"""Indexed, persistent review issues; pages never load all training records."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3


STATUSES = frozenset({"pending", "running", "completed", "needs_revision"})


class HumanIssueStore:
    def __init__(self, path: Path):
        self.path = Path(path) / "review-issues.sqlite3"

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS issues (
                    id TEXT PRIMARY KEY, round_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    target TEXT NOT NULL, candidate_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL, instruction TEXT NOT NULL,
                    question_preview TEXT NOT NULL, answer_preview TEXT NOT NULL,
                    snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', applied_round_id TEXT,
                    superseded_by TEXT, outcome TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS issue_status ON issues(superseded_by, status, created_at);
                CREATE INDEX IF NOT EXISTS issue_candidate ON issues(round_id, target, candidate_id);
                CREATE INDEX IF NOT EXISTS issue_round ON issues(applied_round_id);
                CREATE TABLE IF NOT EXISTS issue_requests (
                    id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL,
                    issue_ids TEXT NOT NULL, version INTEGER NOT NULL
                );
            """)
            with db:
                yield db
        finally:
            db.close()

    def request(self, request_id):
        if request_id is None:
            return None
        with self.connection() as db:
            row = db.execute("SELECT * FROM issue_requests WHERE id=?", (request_id,)).fetchone()
            return dict(row) if row else None

    def save(self, rows, *, request_id=None, payload_sha256=None, version=None):
        saved_ids = []
        with self.connection() as db:
            for item in rows:
                feedback, record = item["feedback"], item["record"]
                prior = db.execute("SELECT id,snapshot FROM issues WHERE round_id=? AND target=? "
                    "AND candidate_id=? AND content_sha256=? AND instruction=? AND superseded_by IS NULL",
                    (feedback["round_id"], feedback["target"], feedback["candidate_id"],
                     feedback["content_sha256"], feedback["instruction"])).fetchall()
                fields = ("question", "answer", "corrected_record")
                duplicate = next((row["id"] for row in prior if all(json.loads(row["snapshot"])["feedback"].get(field)
                    == feedback.get(field) for field in fields)), None)
                if duplicate:
                    # Re-importing an unchanged rejected queue does not turn a
                    # completed correction into a new pending duplicate.
                    saved_ids.append(duplicate)
                    continue
                db.execute("UPDATE issues SET superseded_by=? WHERE round_id=? AND target=? "
                    "AND candidate_id=? AND superseded_by IS NULL",
                    (feedback["id"], feedback["round_id"], feedback["target"], feedback["candidate_id"]))
                db.execute("INSERT INTO issues(id,round_id,run_id,target,candidate_id,content_sha256,"
                    "instruction,question_preview,answer_preview,snapshot,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (feedback["id"], feedback["round_id"], feedback["run_id"], feedback["target"],
                     feedback["candidate_id"], feedback["content_sha256"], feedback["instruction"],
                     item["question_preview"], item["answer_preview"],
                     json.dumps({"feedback": feedback, "record": record}, ensure_ascii=False), feedback["created_at"]))
                saved_ids.append(feedback["id"])
            if request_id is not None:
                db.execute("INSERT INTO issue_requests VALUES(?,?,?,?)", (request_id, payload_sha256,
                    json.dumps(saved_ids), version))
        return saved_ids

    @staticmethod
    def _view(row):
        value = dict(row)
        value["issue_id"] = value.pop("id")
        value["outcome"] = json.loads(value["outcome"])
        value["result_run_id"] = value["outcome"].get("run_id")
        value["result_candidate_id"] = value["outcome"].get("candidate_id")
        value["preview"] = value["question_preview"] or value["answer_preview"]
        value["question"] = value["question_preview"]
        return value

    def page(self, *, status="pending", target=None, round_id=None, offset=0, limit=20):
        where, args = ["superseded_by IS NULL"], []
        if status != "all":
            where.append("status=?")
            args.append(status)
        for field, value in (("target", target), ("round_id", round_id)):
            if value is not None:
                where.append(f"{field}=?")
                args.append(value)
        clause = " AND ".join(where)
        with self.connection() as db:
            total = db.execute(f"SELECT COUNT(*) FROM issues WHERE {clause}", args).fetchone()[0]
            # The immutable full result is intentionally absent from a list page.
            rows = db.execute("SELECT id,round_id,run_id,target,candidate_id,content_sha256,instruction,"
                "question_preview,answer_preview,created_at,status,applied_round_id,outcome "
                f"FROM issues WHERE {clause} ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                (*args, limit, offset)).fetchall()
            counts = {name: 0 for name in STATUSES}
            counts.update({row["status"]: row["count"] for row in db.execute(
                "SELECT status,COUNT(*) AS count FROM issues WHERE superseded_by IS NULL GROUP BY status")})
        return {"rows": [self._view(row) for row in rows], "total": total, "issue_counts": counts, "counts": counts,
                "offset": offset, "limit": limit, "has_more": total > offset + limit}

    def next_batch(self, *, limit=200):
        with self.connection() as db:
            return [row["id"] for row in db.execute("SELECT id FROM issues WHERE superseded_by IS NULL "
                "AND status IN ('pending','needs_revision') ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,"
                "created_at,rowid LIMIT ?", (limit,))]

    def selected(self, issue_ids):
        with self.connection() as db:
            rows = db.execute("SELECT id,status,applied_round_id,snapshot FROM issues WHERE superseded_by IS NULL "
                f"AND id IN ({','.join('?' for _ in issue_ids)})", issue_ids).fetchall()
        by_id = {row["id"]: {**dict(row), "snapshot": json.loads(row["snapshot"])} for row in rows}
        return [by_id[identity] for identity in issue_ids if identity in by_id]

    def assign(self, issue_ids, round_id):
        with self.connection() as db:
            db.executemany("UPDATE issues SET status='running',applied_round_id=?,outcome='{}' WHERE id=?",
                           [(round_id, issue_id) for issue_id in issue_ids])

    def apply_outcomes(self, values):
        with self.connection() as db:
            db.executemany("UPDATE issues SET status=?,outcome=? WHERE id=?",
                [(status, json.dumps(outcome, ensure_ascii=False), issue_id) for issue_id, status, outcome in values])

    def assigned(self):
        """Small per-batch identifiers only; bounded by the session's round limit."""
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT id,round_id,run_id,target,candidate_id,applied_round_id "
                "FROM issues WHERE status='running'")]
