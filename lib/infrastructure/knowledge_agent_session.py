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
from lib.infrastructure.knowledge_agent_archive import KnowledgeAgentArchive


MAX_EXPRESSION_BYTES = 32768
MAX_EXPRESSION_DIMENSIONS = 32
EXPRESSION_COVERAGE_LIMIT = 12


def _expression(value):
    from lib.domain.dialogue_expression import validate_expression_design
    result = validate_expression_design(value)
    try:
        if len(canonical(result).encode("utf-8")) > MAX_EXPRESSION_BYTES:
            raise ValueError("invalid_expression_design")
    except UnicodeError:
        raise ValueError("invalid_expression_design") from None
    return result


def _stored_expression(payload):
    try:
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_EXPRESSION_BYTES:
            raise ValueError
        return _expression(json.loads(payload))
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("knowledge_agent_expression_integrity_error") from None


def _expression_option(value):
    if type(value) is not bool:
        raise ValueError("knowledge_agent_invalid_expression_option")


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


class KnowledgeAgentSession(KnowledgeAgentArchive):
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
                    expression_design TEXT NOT NULL DEFAULT '',
                    UNIQUE(knowledge, identity));
                CREATE INDEX IF NOT EXISTS agent_tasks_recent ON agent_tasks(knowledge, row_id DESC);
                CREATE VIRTUAL TABLE IF NOT EXISTS agent_task_search USING fts5(
                    question_terms, content='');
            """)
            # Serialise schema inspection and ALTER across processes opening an
            # old store together. Old rows have no inferred expression labels.
            db.execute("BEGIN IMMEDIATE")
            if "expression_design" not in {row["name"] for row in db.execute("PRAGMA table_info(agent_tasks)")}:
                db.execute("ALTER TABLE agent_tasks ADD COLUMN expression_design TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE TABLE IF NOT EXISTS agent_task_dimensions ("
                "task_row_id INTEGER NOT NULL,dimension TEXT NOT NULL,value TEXT NOT NULL,"
                "PRIMARY KEY(task_row_id,dimension))")
            db.execute("CREATE INDEX IF NOT EXISTS agent_task_dimension_values "
                "ON agent_task_dimensions(dimension,value,task_row_id)")
            db.execute("CREATE INDEX IF NOT EXISTS agent_tasks_knowledge_status "
                "ON agent_tasks(knowledge,status,row_id)")
            self._archive_schema(db)

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

    def remember_turn(self, session, operation, messages, *, archive=False):
        if type(archive) is not bool:
            raise ValueError("knowledge_agent_archive_invalid_request")
        payload = canonical(messages)
        if len(payload.encode()) > 4 * 1024 * 1024:
            raise ValueError("knowledge_agent_turn_too_large")
        sha = hashlib.sha256(payload.encode()).hexdigest()
        with closing(self._connect()) as db, db:
            if archive:
                db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload,sha256 FROM agent_turns WHERE session=? AND operation=?",
                             (session, operation)).fetchone()
            if old is not None:
                self._payload(old)
                if old["sha256"] != sha:
                    raise ValueError("knowledge_agent_operation_changed")
                if archive:
                    self._archive_record(db, session, operation, messages, sha)
                return False
            db.execute("INSERT INTO agent_turns(session,operation,payload,sha256) VALUES(?,?,?,?)",
                       (session, operation, payload, sha))
            if archive:
                self._archive_record(db, session, operation, messages, sha)
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

    def claim(self, knowledge, identity, *, run_id, sample_id, question, qa_type, topic="", expression_design=None):
        """Reserve one task, with optional validated editorial expression notes.

        An owner's replay can fill old missing notes but cannot rewrite existing
        notes; omitting the option never erases previously recorded metadata.
        """
        if not isinstance(question, str) or not question.strip() or len(question) > 40000:
            raise ValueError("knowledge_agent_invalid_question")
        expression = _expression(expression_design) if expression_design is not None else None
        payload = canonical(expression) if expression is not None else ""
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM agent_tasks WHERE knowledge=? AND identity=?",
                             (knowledge, identity)).fetchone()
            if old is not None:
                if old["run_id"] == run_id and old["sample_id"] == sample_id:
                    if expression is not None:
                        if old["expression_design"]:
                            if _stored_expression(old["expression_design"]) != expression:
                                raise ValueError("knowledge_agent_expression_changed")
                        else:
                            db.execute("UPDATE agent_tasks SET expression_design=? WHERE row_id=?", (payload, old["row_id"]))
                            self._save_expression_dimensions(db, old["row_id"], expression)
                    return None
                return {"id": old["sample_id"], "run_id": old["run_id"], "status": old["status"]}
            cursor = db.execute("INSERT INTO agent_tasks(knowledge,identity,run_id,sample_id,question,qa_type,"
                "topic,stage,status,reason,expression_design) VALUES(?,?,?,?,?,?,?,'director','reserved','',?)",
                (knowledge, identity, run_id, sample_id, question, qa_type, topic[:2000], payload))
            if expression is not None:
                self._save_expression_dimensions(db, cursor.lastrowid, expression)
            # A bounded shingle index recalls related wording in Chinese/English.
            terms = " ".join(hashlib.sha256(term.encode()).hexdigest() for term in _terms(question + " " + topic))
            db.execute("INSERT INTO agent_task_search(rowid,question_terms) VALUES(?,?)", (cursor.lastrowid, terms))
        return None

    @staticmethod
    def _save_expression_dimensions(db, row_id, expression):
        db.execute("DELETE FROM agent_task_dimensions WHERE task_row_id=?", (row_id,))
        db.executemany("INSERT INTO agent_task_dimensions(task_row_id,dimension,value) VALUES(?,?,?)",
            ((row_id, "expression_" + name, value) for name, value in expression.items()))

    def outcome(self, knowledge, identity, *, run_id, sample_id, stage, status, reason=""):
        with closing(self._connect()) as db, db:
            db.execute("UPDATE agent_tasks SET stage=?,status=?,reason=? "
                       "WHERE knowledge=? AND identity=? AND run_id=? AND sample_id=?",
                       (stage, status, str(reason)[:500], knowledge, identity, run_id, sample_id))

    def search(self, knowledge, *, query, limit=8, include_expression=False):
        """Recall this knowledge scope's questions, optionally with design notes."""
        _expression_option(include_expression)
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
                 "score": float(-row["rank"]),
                 **({"expression_design": _stored_expression(row["expression_design"]),
                     "expression_design_scope": "same_knowledge_task_editorial_annotation"}
                    if include_expression and row["expression_design"] else {})} for row in rows]

    def coverage(self, knowledge, *, include_expression=False):
        """Count ledger states; optional design dimensions use final rows only.

        These indexed annotations describe recorded editorial choices, not
        independently classified language or guaranteed semantic coverage.
        """
        _expression_option(include_expression)
        with closing(self._connect()) as db:
            rows = db.execute("SELECT qa_type,status,COUNT(*) AS count FROM agent_tasks "
                              "WHERE knowledge=? GROUP BY qa_type,status", (knowledge,)).fetchall()
            dimensions = {}
            if include_expression:
                names = db.execute("SELECT DISTINCT d.dimension FROM agent_tasks t "
                    "JOIN agent_task_dimensions d ON d.task_row_id=t.row_id "
                    "WHERE t.knowledge=? AND t.status='final_eligible' ORDER BY d.dimension LIMIT ?",
                    (knowledge, MAX_EXPRESSION_DIMENSIONS + 1)).fetchall()
                if len(names) > MAX_EXPRESSION_DIMENSIONS:
                    raise ValueError("knowledge_agent_expression_integrity_error")
                for row in names:
                    name = row["dimension"]
                    if not isinstance(name, str) or not name.startswith("expression_"):
                        raise ValueError("knowledge_agent_expression_integrity_error")
                    values = db.execute("SELECT d.value,COUNT(*) AS accepted FROM agent_tasks t "
                        "JOIN agent_task_dimensions d ON d.task_row_id=t.row_id "
                        "WHERE t.knowledge=? AND t.status='final_eligible' AND d.dimension=? "
                        "GROUP BY d.value ORDER BY accepted DESC,d.value LIMIT ?",
                        (knowledge, name, EXPRESSION_COVERAGE_LIMIT)).fetchall()
                    entries = []
                    for value in values:
                        # The same domain whitelist validates both persisted
                        # designs and the bounded, indexed editorial aggregate.
                        try:
                            _expression({name[len("expression_"):]: value["value"]})
                        except ValueError:
                            raise ValueError("knowledge_agent_expression_integrity_error") from None
                        entries.append({"value": value["value"], "accepted": value["accepted"]})
                    dimensions[name] = entries
        result = {"scope": "same_accepted_knowledge", "history_is_not_evidence": True,
                "semantic_uniqueness_guaranteed": False,
                "counts": [{"qa_type": row["qa_type"], "status": row["status"], "count": row["count"]} for row in rows]}
        if include_expression:
            result.update(accepted_dimensions=dimensions,
                expression_coverage_scope="same_knowledge_final_eligible_editorial_designs",
                expression_coverage_is_annotation=True)
        return result
