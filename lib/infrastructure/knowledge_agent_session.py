"""Durable conversation windows and indexed task memory for one knowledge set.

Historical generations are scheduling evidence, never factual source evidence.
Complete turns stay on disk; only whole, budgeted turns enter a model window.
"""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from lib.domain.workflow_quality import canonical
from lib.infrastructure.qa_history import _terms


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def knowledge_identity(materials):
    values = sorted({material["cleaned_text_sha256"] for material in materials})
    if not values or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in values):
        raise ValueError("knowledge_agent_invalid_material_identity")
    return digest(["accepted_knowledge_v1", values])


def knowledge_task_identity(contract, stage):
    """The same exact identity for native claims and verified legacy imports."""
    from lib.infrastructure.qa_history import learner_task_identity
    core = learner_task_identity(contract["question"], contract.get("visible_context", ""))
    if stage == "multiturn":
        core = digest(["multiturn_design", core, contract.get("dialogue_design")])
    revision = contract.get("revision_context") or (contract.get("human_design") or {}).get("seed", {}).get("revision_context")
    return digest([core, revision]) if revision else core


class KnowledgeAgentSession:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("knowledge_agent_storage_invalid")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_turns (
                    sequence INTEGER PRIMARY KEY, session TEXT NOT NULL,
                    operation TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                    UNIQUE(session, operation));
                CREATE INDEX IF NOT EXISTS agent_turns_recent ON agent_turns(session, sequence DESC);
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    row_id INTEGER PRIMARY KEY, knowledge TEXT NOT NULL,
                    identity TEXT NOT NULL, run_id TEXT NOT NULL, sample_id TEXT NOT NULL,
                    question TEXT NOT NULL, qa_type TEXT NOT NULL, topic TEXT NOT NULL,
                    stage TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                    UNIQUE(knowledge, identity));
                CREATE INDEX IF NOT EXISTS agent_tasks_recent ON agent_tasks(knowledge, row_id DESC);
                CREATE VIRTUAL TABLE IF NOT EXISTS agent_task_search USING fts5(
                    question_terms, content='');
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA cache_size=-2048")
        return db

    @staticmethod
    def _payload(row):
        if hashlib.sha256(row["payload"].encode()).hexdigest() != row["sha256"]:
            raise ValueError("knowledge_agent_conversation_integrity_error")
        value = json.loads(row["payload"])
        if not isinstance(value, list) or any(not isinstance(message, dict) for message in value):
            raise ValueError("knowledge_agent_conversation_integrity_error")
        return value

    def remember_turn(self, session, operation, messages):
        payload = canonical(messages)
        if len(payload.encode()) > 4 * 1024 * 1024:
            raise ValueError("knowledge_agent_turn_too_large")
        sha = hashlib.sha256(payload.encode()).hexdigest()
        with closing(self._connect()) as db, db:
            old = db.execute("SELECT payload,sha256 FROM agent_turns WHERE session=? AND operation=?",
                             (session, operation)).fetchone()
            if old is not None:
                self._payload(old)
                if old["sha256"] != sha:
                    raise ValueError("knowledge_agent_operation_changed")
                return False
            db.execute("INSERT INTO agent_turns(session,operation,payload,sha256) VALUES(?,?,?,?)",
                       (session, operation, payload, sha))
        return True

    def history(self, session, *, max_bytes=32768, max_turns=6, exclude_operation=""):
        """Drop whole old turns, preserving tool pairs and opaque thinking."""
        if not 0 <= max_bytes <= 1024 * 1024 or not 0 <= max_turns <= 24:
            raise ValueError("knowledge_agent_invalid_context_limit")
        selected, used = [], 0
        with closing(self._connect()) as db:
            rows = db.execute("SELECT payload,sha256 FROM agent_turns WHERE session=? AND operation<>? "
                              "ORDER BY sequence DESC LIMIT ?", (session, exclude_operation, max_turns))
            for row in rows:
                value = self._payload(row)
                size = len(row["payload"].encode())
                if used + size > max_bytes:
                    break
                selected.append(value)
                used += size
        return [deepcopy(message) for turn in reversed(selected) for message in turn]

    def claim(self, knowledge, identity, *, run_id, sample_id, question, qa_type, topic=""):
        """Atomic exact-task reservation across concurrent jobs and restarts."""
        if not isinstance(question, str) or not question.strip() or len(question) > 40000:
            raise ValueError("knowledge_agent_invalid_question")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM agent_tasks WHERE knowledge=? AND identity=?",
                             (knowledge, identity)).fetchone()
            if old is not None:
                if old["run_id"] == run_id and old["sample_id"] == sample_id:
                    return None
                return {"id": old["sample_id"], "run_id": old["run_id"], "status": old["status"]}
            cursor = db.execute("INSERT INTO agent_tasks(knowledge,identity,run_id,sample_id,question,qa_type,"
                "topic,stage,status,reason) VALUES(?,?,?,?,?,?,?,'director','reserved','')",
                (knowledge, identity, run_id, sample_id, question, qa_type, topic[:2000]))
            # A bounded shingle index recalls related wording in Chinese/English.
            terms = " ".join(hashlib.sha256(term.encode()).hexdigest() for term in _terms(question + " " + topic))
            db.execute("INSERT INTO agent_task_search(rowid,question_terms) VALUES(?,?)", (cursor.lastrowid, terms))
        return None

    def outcome(self, knowledge, identity, *, run_id, sample_id, stage, status, reason=""):
        with closing(self._connect()) as db, db:
            db.execute("UPDATE agent_tasks SET stage=?,status=?,reason=? "
                       "WHERE knowledge=? AND identity=? AND run_id=? AND sample_id=?",
                       (stage, status, str(reason)[:500], knowledge, identity, run_id, sample_id))

    def search(self, knowledge, *, query, limit=8):
        limit = max(0, min(int(limit), 20))
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError("knowledge_agent_invalid_search")
        terms = [hashlib.sha256(term.encode()).hexdigest() for term in _terms(query)]
        with closing(self._connect()) as db:
            if terms:
                rows = db.execute("SELECT t.*,bm25(agent_task_search) AS rank FROM agent_task_search "
                    "JOIN agent_tasks t ON t.row_id=agent_task_search.rowid "
                    "WHERE agent_task_search MATCH ? AND t.knowledge=? ORDER BY rank LIMIT ?",
                    (" OR ".join(terms), knowledge, limit)).fetchall()
            else:
                rows = []
            if not rows:
                rows = db.execute("SELECT *,0 AS rank FROM agent_tasks WHERE knowledge=? "
                                  "ORDER BY row_id DESC LIMIT ?", (knowledge, limit)).fetchall()
        return [{"id": row["sample_id"], "run_id": row["run_id"], "question": row["question"],
                 "qa_type": row["qa_type"], "topic": row["topic"], "status": row["status"],
                 "stage": row["stage"], "reason": row["reason"], "similarity_kind": "lexical_recall_not_semantic_proof",
                 "score": float(-row["rank"])} for row in rows]

    def coverage(self, knowledge):
        with closing(self._connect()) as db:
            rows = db.execute("SELECT qa_type,status,COUNT(*) AS count FROM agent_tasks "
                              "WHERE knowledge=? GROUP BY qa_type,status", (knowledge,)).fetchall()
        return {"scope": "same_accepted_knowledge", "history_is_not_evidence": True,
                "semantic_uniqueness_guaranteed": False,
                "counts": [{"qa_type": row["qa_type"], "status": row["status"], "count": row["count"]} for row in rows]}
