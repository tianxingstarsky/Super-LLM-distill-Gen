"""Accepted QA history with bounded lexical recall and exact contract identities.

The inverted index is a local fallback, not an embedding/semantic deduplicator.
Similar answers are examples of prior coverage and must never become source facts.
Each lookup reads a fixed number of postings and candidate summaries; it does not
load or scan every QA in a large run. Only accepted rows may enter this store.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import heapq
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
import unicodedata

from lib.domain.dialogue_expression import EXPRESSION_FIELDS, validate_expression_design

MAX_HISTORY_ITEMS = 20
MAX_QUESTION_CHARS = 32768
MAX_QUESTION_SNIPPET = 1024
MAX_ANSWER_SNIPPET = 768
MAX_CONTEXT_SNIPPET = 1024
MAX_INDEX_TERMS = 48
MAX_QUERY_TERMS = 12
MAX_POSTINGS_PER_TERM = 64
MAX_CANDIDATES = 256
ACCEPTED_STATUSES = frozenset({"eligible", "accepted", "approved", "passed"})

_WORDS = re.compile(r"[a-z]+(?:'[a-z]+)?|[0-9]+(?:\.[0-9]+)*")
_CHINESE = re.compile(r"[\u3400-\u9fff]+")
_NUMBERS = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[%％])?")


def _normalise(value: str) -> str:
    # Keep operators, negation, units and numbers. Removing all punctuation can
    # incorrectly merge e.g. x+1 and x-1. NFKC handles full-width input safely.
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).rstrip("?？ ")


def _exact_text(value, *, strip=False) -> str:
    # Preserve code/literal case, mathematical compatibility characters and
    # internal indentation. Those can change a question's or evidence's meaning.
    text = unicodedata.normalize("NFC", _text(value).replace("\r\n", "\n").replace("\r", "\n"))
    return text.strip() if strip else text


def _question(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("qa_history_question_required")
    if len(value) > MAX_QUESTION_CHARS:
        raise ValueError("qa_history_question_too_long")
    return value.strip()


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dimension(value, fallback: str) -> str:
    result = _normalise(_text(value) or fallback)
    if len(result) > 256:
        raise ValueError("qa_history_dimension_too_long")
    return result


def _design_dimensions(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("qa_history_dialogue_design_invalid")
    goal, intent = value.get("interaction_goal"), value.get("user_intent", "")
    if (not isinstance(goal, str) or not goal.strip() or len(goal) > 4000 or "\x00" in goal
            or not isinstance(intent, str) or len(intent) > 2000 or "\x00" in intent):
        raise ValueError("qa_history_dialogue_design_invalid")
    return _exact_text(goal, strip=True), _exact_text(intent, strip=True)


def _identity(question: str, visible_context, answer_policy, qa_type, dialogue_design=None) -> str:
    contract = [_exact_text(question, strip=True), _exact_text(visible_context),
                _dimension(answer_policy, "answer"), _dimension(qa_type, "closed_book")]
    dimensions = _design_dimensions(dialogue_design)
    if dimensions is not None:
        contract.append(["dialogue_design_v1", *dimensions])
    return hashlib.sha256(json.dumps(contract, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def contract_identity(question, visible_context="", answer_policy="answer", qa_type="closed_book", *,
                      dialogue_design=None) -> str:
    """Use the same exact contract key for bounded pre-dispatch registration."""
    return _identity(_question(question), visible_context, answer_policy, qa_type, dialogue_design)


def learner_task_identity(question, visible_context="") -> str:
    """Exact learner input, independent of hidden editorial labels or policy.

    Preserve literal case, mathematics and complete evidence. This is not a
    semantic paraphrase detector and is not suitable for a multi-turn opening
    whose actual later dialogue has not yet been generated.
    """
    payload = [_exact_text(_question(question), strip=True), _exact_text(visible_context)]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _actual_input_hash(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("qa_history_actual_input_hash_invalid")
    return value


def _terms(question: str) -> tuple[str, ...]:
    """Deterministically sample word and Chinese shingles across the question."""
    normalised = _normalise(question)
    words = _WORDS.findall(normalised)
    terms = {"w:" + word for word in words}
    terms.update("w:" + left + " " + right for left, right in zip(words, words[1:]))
    for run in _CHINESE.findall(normalised):
        if len(run) == 1:
            terms.add("c:" + run)
        for width in (2, 3):
            terms.update("c:" + run[index:index + width]
                         for index in range(max(0, len(run) - width + 1)))
    # Hash order avoids always indexing only the beginning of a long question.
    return tuple(heapq.nsmallest(MAX_INDEX_TERMS, terms,
                 key=lambda item: (hashlib.blake2b(item.encode("utf-8"), digest_size=8).digest(), item)))


def _snippet(value, length: int) -> str:
    text = _text(value)
    return text if len(text) <= length else text[:length - 1] + "…"


def _limit(value: int) -> int:
    return max(0, min(int(value), MAX_HISTORY_ITEMS))


class QAHistory:
    """A durable, thread-safe accepted history, partitioned by target namespace.

    ``add`` is idempotent for the same sample id and immutable contract. It returns
    False for a rejected/unqualified row or a contract already accepted under a
    different id. UNIQUE indexes resolve concurrent exact duplicate submissions.
    ``duplicate`` deliberately makes no automatic semantic equivalence judgement.
    """

    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._closed = False
        self.connection = sqlite3.connect(path, timeout=30, check_same_thread=False,
                                          isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute("PRAGMA cache_size=-2048")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS qa_history (
                row_id INTEGER PRIMARY KEY,
                namespace TEXT NOT NULL,
                sample_id TEXT NOT NULL,
                identity TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                qa_type TEXT NOT NULL,
                visible_context TEXT NOT NULL,
                answer_policy TEXT NOT NULL,
                family_id TEXT NOT NULL,
                parent_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                run_id TEXT NOT NULL,
                dialogue_goal TEXT NOT NULL DEFAULT '',
                dialogue_intent TEXT NOT NULL DEFAULT '',
                UNIQUE(namespace, sample_id),
                UNIQUE(namespace, identity)
            );
            CREATE INDEX IF NOT EXISTS qa_history_recent
                ON qa_history(namespace, row_id DESC);
            CREATE INDEX IF NOT EXISTS qa_history_source_recent
                ON qa_history(namespace, source_id, row_id DESC);
            CREATE TABLE IF NOT EXISTS qa_history_terms (
                namespace TEXT NOT NULL,
                term TEXT NOT NULL,
                row_id INTEGER NOT NULL REFERENCES qa_history(row_id),
                PRIMARY KEY(namespace, term, row_id)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS qa_history_term_counts (
                namespace TEXT NOT NULL,
                term TEXT NOT NULL,
                total INTEGER NOT NULL,
                PRIMARY KEY(namespace, term)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS qa_history_coverage (
                namespace TEXT NOT NULL,
                qa_type TEXT NOT NULL,
                total INTEGER NOT NULL,
                PRIMARY KEY(namespace, qa_type)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS qa_history_core (
                namespace TEXT NOT NULL,
                core_identity TEXT NOT NULL,
                row_id INTEGER NOT NULL REFERENCES qa_history(row_id),
                PRIMARY KEY(namespace, core_identity)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS qa_history_dimensions (
                namespace TEXT NOT NULL,
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                total INTEGER NOT NULL,
                PRIMARY KEY(namespace, dimension, value)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS qa_history_actual (
                namespace TEXT NOT NULL,
                actual_input_sha256 TEXT NOT NULL,
                row_id INTEGER NOT NULL REFERENCES qa_history(row_id),
                PRIMARY KEY(namespace, actual_input_sha256),
                UNIQUE(namespace, row_id)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS qa_history_question
                ON qa_history(namespace, question);
            CREATE INDEX IF NOT EXISTS qa_history_dimensions_rank
                ON qa_history_dimensions(namespace, dimension, total DESC, value);
        """)
        # Old exact identities remain byte-for-byte valid. Only new adaptive
        # contracts opt in to a goal/intent dimension; no history rewrite is
        # needed. Serialize schema inspection with cross-process writers.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            columns = {row[1] for row in self.connection.execute("PRAGMA table_info(qa_history)")}
            for name in ("dialogue_goal", "dialogue_intent", "expression_design"):
                if name not in columns:
                    self.connection.execute(f"ALTER TABLE qa_history ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        with self._lock:
            if not self._closed:
                self.connection.close()
                self._closed = True

    @staticmethod
    def _summary(row, *, include_expression=False) -> dict:
        return {"id": row["sample_id"], "identity": row["identity"],
                "question": _snippet(row["question"], MAX_QUESTION_SNIPPET),
                "answer": row["answer"], "qa_type": row["qa_type"],
                "visible_context": row["visible_context"],
                "answer_policy": row["answer_policy"],
                "family_id": row["family_id"], "parent_id": row["parent_id"],
                "source_id": row["source_id"], "source_name": row["source_name"],
                "run_id": row["run_id"], "namespace": row["namespace"],
                **({"expression_design": json.loads(row["expression_design"])}
                   if include_expression and row["expression_design"] else {}),
                **({"dialogue_design": {"interaction_goal": row["dialogue_goal"],
                                        "user_intent": row["dialogue_intent"]}}
                   if row["dialogue_goal"] else {})}

    def source_recent(self, source_ids, *, limit=20, namespace="default"):
        """Bounded migration of prior accepted tasks from this source scope."""
        sources = tuple(sorted(set(source_ids)))
        if not sources or len(sources) > 2048:
            return []
        limit = _limit(limit)
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM qa_history WHERE namespace=? AND source_id IN ("
                + ",".join("?" for _ in sources) + ") ORDER BY row_id DESC LIMIT ?",
                (namespace, *sources, limit)).fetchall()
        return [self._summary(row, include_expression=True) for row in rows]

    def add(self, row: dict, *, namespace="default", learner_task_dedup=False) -> bool:
        status = row.get("status")
        if status not in ACCEPTED_STATUSES and not (status is None and row.get("accepted") is True):
            return False
        question = _question(row.get("question"))
        namespace = _dimension(namespace, "default")
        qa_type = _dimension(row.get("qa_type"), "closed_book")
        answer_policy = _dimension(row.get("answer_policy"), "answer")
        visible_context = row.get("visible_context", "")
        design = row.get("dialogue_design")
        dimensions = _design_dimensions(design)
        expression = (validate_expression_design(row["expression_design"])
                      if "expression_design" in row else None)
        identity = _identity(question, visible_context, answer_policy, qa_type, design)
        core_identity = learner_task_identity(question, visible_context)
        actual_identity = _actual_input_hash(row.get("actual_input_sha256"))
        actual_revision = row.get("actual_input_revision", False)
        if type(actual_revision) is not bool or (actual_revision and actual_identity is None):
            raise ValueError("qa_history_actual_input_revision_invalid")
        actual_only = row.get("actual_input_only_identity", False)
        if type(actual_only) is not bool or (actual_only and actual_identity is None):
            raise ValueError("qa_history_actual_input_only_invalid")
        if actual_only:
            identity = hashlib.sha256(json.dumps(["actual_training_input_v1", actual_identity], separators=(",", ":")).encode()).hexdigest()
        elif actual_revision:
            identity = hashlib.sha256(json.dumps([identity, actual_identity], separators=(",", ":")).encode()).hexdigest()
        display_question = _question(row.get("actual_question", question)) if actual_revision else question
        sample_id = _text(row.get("id"))
        if not sample_id or len(sample_id) > 512:
            raise ValueError("qa_history_sample_id_required")
        fields = (namespace, sample_id, identity, display_question,
                  _snippet(row.get("answer", ""), MAX_ANSWER_SNIPPET), qa_type,
                  _snippet(visible_context, MAX_CONTEXT_SNIPPET), answer_policy,
                  *(_snippet(row.get(key, ""), 256) for key in
                    ("family_id", "parent_id", "source_id", "source_name", "run_id")),
                  *(_snippet(value, limit) for value, limit in zip(dimensions or ("", ""), (1024, 512))),
                  json.dumps(expression, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                  if expression else "")
        terms = _terms(display_question)
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.connection.execute(
                    "SELECT row_id,identity FROM qa_history WHERE namespace=? AND sample_id=?",
                    (namespace, sample_id)).fetchone()
                if existing:
                    if existing["identity"] != identity:
                        saved_actual = self.connection.execute(
                            "SELECT actual_input_sha256 FROM qa_history_actual WHERE namespace=? AND row_id=?",
                            (namespace, existing["row_id"])).fetchone()
                        # Existing version-1 multi-turn runs may have a legacy
                        # contract identity but already pinned the exact input.
                        # An identical replay remains safe without rewriting it.
                        if not (actual_only and saved_actual is not None and saved_actual[0] == actual_identity):
                            raise ValueError("qa_history_sample_id_conflict")
                    if actual_identity:
                        prior_actual = self.connection.execute(
                            "SELECT actual_input_sha256 FROM qa_history_actual WHERE namespace=? AND row_id=?",
                            (namespace, existing["row_id"])).fetchone()
                        if prior_actual is not None and prior_actual[0] != actual_identity:
                            raise ValueError("qa_history_sample_input_conflict")
                        duplicate = self._actual_duplicate(actual_identity, namespace)
                        if duplicate is not None and duplicate["row_id"] != existing["row_id"]:
                            self.connection.execute("COMMIT")
                            return False
                        self.connection.execute("INSERT OR IGNORE INTO qa_history_actual VALUES (?,?,?)",
                                                (namespace, actual_identity, existing["row_id"]))
                    self.connection.execute("COMMIT")
                    return True
                if learner_task_dedup and not (actual_revision or actual_only) and self._core_duplicate(question, visible_context, namespace, core_identity):
                    self.connection.execute("COMMIT")
                    return False
                if actual_identity and self._actual_duplicate(actual_identity, namespace) is not None:
                    self.connection.execute("COMMIT")
                    return False
                inserted = self.connection.execute(
                    "INSERT OR IGNORE INTO qa_history (namespace,sample_id,identity,question,answer,"
                    "qa_type,visible_context,answer_policy,family_id,parent_id,source_id,source_name,run_id,dialogue_goal,dialogue_intent,expression_design) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", fields)
                if not inserted.rowcount:
                    self.connection.execute("COMMIT")
                    return False
                row_id = inserted.lastrowid
                if not (actual_revision or actual_only):
                    self.connection.execute("INSERT OR IGNORE INTO qa_history_core VALUES (?,?,?)",
                                            (namespace, core_identity, row_id))
                if actual_identity:
                    self.connection.execute("INSERT INTO qa_history_actual VALUES (?,?,?)",
                                            (namespace, actual_identity, row_id))
                self.connection.executemany("INSERT INTO qa_history_terms VALUES (?,?,?)",
                                           ((namespace, term, row_id) for term in terms))
                self.connection.executemany(
                    "INSERT INTO qa_history_term_counts VALUES (?,?,1) "
                    "ON CONFLICT(namespace,term) DO UPDATE SET total=total+1",
                    ((namespace, term) for term in terms))
                self.connection.execute(
                    "INSERT INTO qa_history_coverage VALUES (?,?,1) "
                    "ON CONFLICT(namespace,qa_type) DO UPDATE SET total=total+1",
                    (namespace, qa_type))
                dimensions = {name: _snippet(row.get(name, ""), 256) for name in ("source_id", "topic", "skill")}
                dimensions.update({"expression_" + name: value for name, value in (expression or {}).items()})
                for name, value in dimensions.items():
                    if value:
                        self.connection.execute(
                            "INSERT INTO qa_history_dimensions VALUES (?,?,?,1) "
                            "ON CONFLICT(namespace,dimension,value) DO UPDATE SET total=total+1",
                            (namespace, name, value))
                self.connection.execute("COMMIT")
                return True
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    def _core_duplicate(self, question, visible_context, namespace, identity):
        row = self.connection.execute(
            "SELECT h.* FROM qa_history_core c JOIN qa_history h ON h.row_id=c.row_id "
            "WHERE c.namespace=? AND c.core_identity=?", (namespace, identity)).fetchone()
        if row is not None:
            return row
        # Earlier versions stored full questions but only context snippets.
        # Recover exact short contexts through the question index; never infer
        # a complete long evidence identity from a truncated legacy summary.
        text = _exact_text(visible_context)
        if len(text) >= MAX_CONTEXT_SNIPPET:
            return None
        return self.connection.execute(
            "SELECT * FROM qa_history WHERE namespace=? AND question=? AND visible_context=? "
            "AND length(visible_context)<? ORDER BY row_id LIMIT 1",
            (namespace, _exact_text(question, strip=True), text, MAX_CONTEXT_SNIPPET)).fetchone()

    def _actual_duplicate(self, identity, namespace):
        return self.connection.execute(
            "SELECT h.* FROM qa_history_actual a JOIN qa_history h ON h.row_id=a.row_id "
            "WHERE a.namespace=? AND a.actual_input_sha256=?", (namespace, identity)).fetchone()

    def duplicate_actual(self, identity, *, namespace="default", exclude_id=None):
        """Exact delivered input; old rows without its hash are not guessed."""
        identity = _actual_input_hash(identity)
        if identity is None:
            return None
        with self._lock:
            row = self._actual_duplicate(identity, _dimension(namespace, "default"))
        if row is None or (exclude_id is not None and row["sample_id"] == str(exclude_id)):
            return None
        return {**self._summary(row), "match_kind": "exact_actual_input", "score": 1.0,
                "actual_input_sha256": identity}

    def duplicate(self, question, *, visible_context="", answer_policy="answer",
                  qa_type="closed_book", namespace="default", exclude_id=None, dialogue_design=None,
                  learner_task_dedup=False) -> dict | None:
        identity = _identity(_question(question), visible_context, answer_policy, qa_type, dialogue_design)
        with self._lock:
            row = (self._core_duplicate(question, visible_context, _dimension(namespace, "default"),
                                        learner_task_identity(question, visible_context))
                   if learner_task_dedup else self.connection.execute(
                "SELECT * FROM qa_history WHERE namespace=? AND identity=?",
                (_dimension(namespace, "default"), identity)).fetchone())
        if row is None or (exclude_id is not None and row["sample_id"] == str(exclude_id)):
            return None
        return {**self._summary(row), "match_kind": "exact_learner_task" if learner_task_dedup else "exact_contract", "score": 1.0}

    def recent(self, *, limit=10, namespace="default", include_expression=False) -> list[dict]:
        limit = _limit(limit)
        if not limit:
            return []
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM qa_history WHERE namespace=? ORDER BY row_id DESC LIMIT ?",
                (_dimension(namespace, "default"), limit)).fetchall()
        return [self._summary(row, include_expression=include_expression) for row in rows]

    def coverage(self, *, namespace="default") -> dict[str, int]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT qa_type,total FROM qa_history_coverage WHERE namespace=? ORDER BY qa_type",
                (_dimension(namespace, "default"),)).fetchall()
        return {row["qa_type"]: row["total"] for row in rows}

    def dimension_coverage(self, *, namespace="default", limit=12, include_expression=False) -> dict[str, list[dict]]:
        """Bounded aggregates of accepted work, without reloading old answers."""
        namespace, limit = _dimension(namespace, "default"), _limit(limit)
        with self._lock:
            return {dimension: [{"value": row["value"], "accepted": row["total"]} for row in
                    self.connection.execute(
                        "SELECT value,total FROM qa_history_dimensions WHERE namespace=? AND dimension=? "
                        "ORDER BY total DESC,value LIMIT ?", (namespace, dimension, limit)).fetchall()]
                    for dimension in ("source_id", "topic", "skill", *(
                        ("expression_" + name for name in EXPRESSION_FIELDS) if include_expression else ()))}

    def similar(self, question, *, limit=10, namespace="default", include_expression=False) -> list[dict]:
        question = _question(question)
        limit = _limit(limit)
        query_terms = _terms(question)
        if not query_terms or not limit:
            return []
        namespace = _dimension(namespace, "default")
        with self._lock:
            placeholders = ",".join("?" for _ in query_terms)
            frequencies = self.connection.execute(
                f"SELECT term,total FROM qa_history_term_counts WHERE namespace=? AND term IN ({placeholders}) "
                "ORDER BY total,term LIMIT ?", (namespace, *query_terms, MAX_QUERY_TERMS)).fetchall()
            hits = Counter()
            for term in frequencies:
                rows = self.connection.execute(
                    "SELECT row_id FROM qa_history_terms WHERE namespace=? AND term=? "
                    "ORDER BY row_id DESC LIMIT ?",
                    (namespace, term["term"], MAX_POSTINGS_PER_TERM)).fetchall()
                hits.update(row["row_id"] for row in rows)
            candidate_ids = sorted(hits, key=lambda item: (-hits[item], -item))[:MAX_CANDIDATES]
            if not candidate_ids:
                return []
            placeholders = ",".join("?" for _ in candidate_ids)
            candidates = self.connection.execute(
                f"SELECT * FROM qa_history WHERE row_id IN ({placeholders})", candidate_ids).fetchall()
        query_set = set(query_terms)
        numbers = _NUMBERS.findall(_normalise(question))
        ranked = []
        for row in candidates:
            candidate_terms = set(_terms(row["question"]))
            overlap = len(query_set & candidate_terms)
            if not overlap:
                continue
            score = 2 * overlap / (len(query_set) + len(candidate_terms))
            ranked.append({**self._summary(row, include_expression=include_expression), "match_kind": "lexical", "score": round(score, 6),
                           "same_numbers": numbers == _NUMBERS.findall(_normalise(row["question"]))})
        ranked.sort(key=lambda row: (-row["score"], row["id"]))
        return ranked[:limit]
