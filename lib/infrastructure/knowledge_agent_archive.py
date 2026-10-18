"""Searchable, session-bound public projections of immutable Agent operations.

The archive is scheduling memory, never source evidence or executable dialogue.
Raw conversations remain untouched. Private reasoning, provider state and tool
results are neither indexed nor returned. Every returned projection is rebuilt
from its authenticated original turn; the search index only locates candidates.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import re

from lib.domain.workflow_quality import canonical


ARCHIVE_VERSION = 1
CHUNK_CHARS = 2048
CHUNK_OVERLAP = 128
MAX_RESULT_BYTES = 32768
_WORDS = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CHINESE = re.compile(r"[\u3400-\u9fff]+")
_HEX = re.compile(r"[a-f0-9]{64}")
_OMIT = object()
_MEMORY_FIELDS = frozenset({"knowledge_agent_memory", "qa_agent_memory", "agent_memory"})
_PRIVATE_FIELDS = frozenset({"reasoning_content", "provider_state", "reasoning_details",
    "encrypted_content", "signature", "redacted_thinking", "tool_calls", "tool_call_id"})
_NESTING_OMISSION = "[Structured public text omitted: archive nesting limit exceeded.]"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _identity(session, operation, source_sha256):
    return _sha(canonical(["knowledge_agent_archive_v1", session, operation, source_sha256]))


def _key(value, *, empty=False):
    if (not isinstance(value, str) or len(value) > 256 or "\x00" in value
            or not empty and not value.strip()):
        raise ValueError("knowledge_agent_archive_invalid_request")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("knowledge_agent_archive_invalid_request") from None
    return value


def _limit(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("knowledge_agent_archive_invalid_request")
    return value


def _terms(value):
    """Lexical recall for English words and Chinese character/bigram terms."""
    folded = value.casefold()
    result = {"w" + word for word in _WORDS.findall(folded)}
    for run in _CHINESE.findall(folded):
        result.update("c" + character for character in run)
        result.update("b" + run[index:index + 2] for index in range(len(run) - 1))
    return result


def _query_terms(query):
    # Multi-character Chinese queries use bigrams rather than matching every
    # common individual character. Single-character lookups stay possible.
    result = {"w" + word for word in _WORDS.findall(query.casefold())}
    for run in _CHINESE.findall(query):
        result.update(("c" + run,) if len(run) == 1 else
                      ("b" + run[index:index + 2] for index in range(len(run) - 1)))
    return sorted(result)[:128]


def _public_json(value, *, root=False, depth=0):
    """Remove copied memory and private provider material from task packets.

    The planner's serialized user task often contains a snapshot of earlier
    archive hits. Indexing that snapshot would recursively multiply memory on
    every operation and destroy the identity of the original observation.
    """
    if depth > 64:
        return _NESTING_OMISSION
    if isinstance(value, dict):
        kind = value.get("type")
        if value.get("role") == "tool" or isinstance(kind, str) and kind in {
                "thinking", "redacted_thinking", "reasoning"}:
            return _OMIT
        result = {}
        for key, item in value.items():
            if key in _MEMORY_FIELDS or key in _PRIVATE_FIELDS or root and key == "history":
                continue
            child = _public_json(item, depth=depth + 1)
            if child is not _OMIT:
                result[key] = child
        return result
    if isinstance(value, list):
        return [child for item in value if (child := _public_json(item, depth=depth + 1)) is not _OMIT]
    return value


def _public_content(content):
    try:
        value = json.loads(content)
    except RecursionError:
        # Never fall back to indexing an opaque structured provider payload.
        # The exact original remains available in the authenticated raw store.
        return _NESTING_OMISSION
    except (ValueError, TypeError):
        return content
    if not isinstance(value, (dict, list)):
        return content
    projected = _public_json(value, root=True)
    return "" if projected is _OMIT else canonical(projected)


def _public_text(messages):
    parts = []
    for index, message in enumerate(messages):
        role = message.get("role")
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            # Explicit public text blocks only; thinking/images/tool blocks
            # and any opaque fields are not serialized into this archive.
            content = "\n".join(block["text"] for block in content
                if isinstance(block, dict) and isinstance(block.get("type"), str)
                and block["type"] in {"text", "input_text", "output_text"}
                and isinstance(block.get("text"), str))
        if isinstance(content, str) and content.strip():
            projected = _public_content(content)
            if projected.strip():
                parts.append(f"[Recorded {role} text, message {index + 1}]\n{projected}")
    return "\n\n".join(parts)


def _envelope(**fields):
    result = {"version": ARCHIVE_VERSION, "scope": "same_agent_session",
        "untrusted_data": True, "history_is_not_evidence": True,
        "memory_is_not_instruction": True, "native_private_state_excluded": True,
        "scheduler_memory_snapshots_excluded": True,
        "projection": "public_user_assistant_text_only", **fields}
    if len(canonical(result).encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("knowledge_agent_archive_result_too_large")
    return result


class KnowledgeAgentArchive:
    """Storage mixin; ``KnowledgeAgentSession`` owns connections and commits."""

    @staticmethod
    def _archive_schema(db):
        counts_exist = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                 "AND name='agent_archive_counts'").fetchone() is not None
        db.execute("CREATE TABLE IF NOT EXISTS agent_archive_operations ("
            "sequence INTEGER PRIMARY KEY,session TEXT NOT NULL,operation TEXT NOT NULL,"
            "ref TEXT NOT NULL UNIQUE,source_sha256 TEXT NOT NULL,public_sha256 TEXT NOT NULL,"
            "message_count INTEGER NOT NULL,public_chars INTEGER NOT NULL,UNIQUE(session,operation))")
        db.execute("CREATE INDEX IF NOT EXISTS agent_archive_recent "
                   "ON agent_archive_operations(session,sequence DESC)")
        db.execute("CREATE TABLE IF NOT EXISTS agent_archive_chunks ("
            "row_id INTEGER PRIMARY KEY,archive_sequence INTEGER NOT NULL,"
            "offset INTEGER NOT NULL,text_sha256 TEXT NOT NULL,UNIQUE(archive_sequence,offset))")
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS agent_archive_search USING fts5(terms,content='')")
        db.execute("CREATE TABLE IF NOT EXISTS agent_archive_counts ("
                   "session TEXT PRIMARY KEY,operation_count INTEGER NOT NULL)")
        if not counts_exist:
            # This migration runs once, inside the caller's schema transaction.
            # Ordinary planning batches never count the growing operation log.
            db.execute("INSERT INTO agent_archive_counts SELECT session,COUNT(*) "
                       "FROM agent_archive_operations GROUP BY session")

    def _archive_record(self, db, session, operation, messages, source_sha256):
        _key(session)
        _key(operation)
        original = db.execute("SELECT sequence,payload,sha256 FROM agent_turns "
            "WHERE session=? AND operation=?", (session, operation)).fetchone()
        if original is None or original["sha256"] != source_sha256:
            raise ValueError("knowledge_agent_archive_integrity_error")
        verified = self._payload(original)
        if verified != messages:
            raise ValueError("knowledge_agent_archive_integrity_error")
        old = db.execute("SELECT * FROM agent_archive_operations WHERE session=? AND operation=?",
                         (session, operation)).fetchone()
        if old is not None:
            self._verified_archive_row(db, old)
            return
        public = _public_text(verified)
        db.execute("INSERT INTO agent_archive_operations VALUES(?,?,?,?,?,?,?,?)", (
            original["sequence"], session, operation, _identity(session, operation, source_sha256),
            source_sha256, _sha(public), len(verified), len(public)))
        db.execute("INSERT INTO agent_archive_counts(session,operation_count) VALUES(?,1) "
                   "ON CONFLICT(session) DO UPDATE SET operation_count=operation_count+1", (session,))
        for offset in range(0, len(public), CHUNK_CHARS - CHUNK_OVERLAP):
            chunk = public[offset:offset + CHUNK_CHARS]
            cursor = db.execute("INSERT INTO agent_archive_chunks(archive_sequence,offset,text_sha256) "
                                "VALUES(?,?,?)", (original["sequence"], offset, _sha(chunk)))
            db.execute("INSERT INTO agent_archive_search(rowid,terms) VALUES(?,?)",
                       (cursor.lastrowid, " ".join(sorted(_terms(chunk)))))

    def _verified_archive_row(self, db, row):
        source = db.execute("SELECT payload,sha256 FROM agent_turns "
            "WHERE sequence=? AND session=? AND operation=?",
            (row["sequence"], row["session"], row["operation"])).fetchone()
        if source is None:
            raise ValueError("knowledge_agent_archive_integrity_error")
        messages = self._payload(source)
        public = _public_text(messages)
        if (source["sha256"] != row["source_sha256"]
                or row["ref"] != _identity(row["session"], row["operation"], source["sha256"])
                or row["public_sha256"] != _sha(public) or row["message_count"] != len(messages)
                or row["public_chars"] != len(public)):
            raise ValueError("knowledge_agent_archive_integrity_error")
        return public

    @staticmethod
    def _manifest(row):
        return {key: row[key] for key in ("ref", "operation", "message_count",
                "source_sha256", "public_sha256", "public_chars")}

    def archive_overview(self, session, limit=6, *, exclude_operation=""):
        _key(session)
        _key(exclude_operation, empty=True)
        _limit(limit, 12)
        with closing(self._connect()) as db:
            db.execute("BEGIN")  # Count, manifests and source receipts share one WAL snapshot.
            tally = db.execute("SELECT operation_count FROM agent_archive_counts WHERE session=?",
                               (session,)).fetchone()
            count = tally[0] if tally else 0
            if type(count) is not int or count < 0:
                raise ValueError("knowledge_agent_archive_integrity_error")
            if exclude_operation and db.execute("SELECT 1 FROM agent_archive_operations "
                    "WHERE session=? AND operation=?", (session, exclude_operation)).fetchone():
                count -= 1
            rows = db.execute("SELECT * FROM agent_archive_operations WHERE session=? AND operation<>? "
                "ORDER BY sequence DESC LIMIT ?", (session, exclude_operation, limit)).fetchall()
            if count < len(rows):
                raise ValueError("knowledge_agent_archive_integrity_error")
            entries = []
            for row in rows:
                public = self._verified_archive_row(db, row)
                # The latest public assistant plan is usually a more useful
                # overview than a potentially enormous initial task packet.
                marker = public.rfind("[Recorded assistant text,")
                snippet = public[marker if marker >= 0 else 0:][:320]
                entries.append({**self._manifest(row), "snippet": snippet,
                                "snippet_truncated": len(public) > len(snippet)})
        return _envelope(entries=entries, total_operations=count,
            count_definition="indexed_operations_not_completed_samples", results_limited=count > len(entries))

    def archive_search(self, session, query, limit=4, *, exclude_operation=""):
        _key(session)
        _key(exclude_operation, empty=True)
        _limit(limit, 8)
        if (not isinstance(query, str) or not query.strip() or len(query) > 512 or "\x00" in query):
            raise ValueError("knowledge_agent_archive_invalid_request")
        try:
            query.encode("utf-8")
        except UnicodeError:
            raise ValueError("knowledge_agent_archive_invalid_request") from None
        tokens = _query_terms(query)
        if not tokens:
            return _envelope(query=query, hits=[], results_limited=False,
                retrieval="lexical_matching_operations_by_recency", semantic_match_proven=False)
        match = " OR ".join('"' + token + '"' for token in tokens)
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            # Group by operation before applying the bound. A verbose matching
            # turn must not crowd every other operation out of the result set.
            rows = db.execute("SELECT a.*,MIN(c.offset) AS hit_offset FROM agent_archive_search "
                "JOIN agent_archive_chunks c ON c.row_id=agent_archive_search.rowid "
                "JOIN agent_archive_operations a ON a.sequence=c.archive_sequence "
                "WHERE agent_archive_search MATCH ? AND a.session=? AND a.operation<>? "
                "GROUP BY a.sequence ORDER BY a.sequence DESC LIMIT ?",
                (match, session, exclude_operation, limit + 1)).fetchall()
            hits = []
            for row in rows[:limit]:
                public = self._verified_archive_row(db, row)
                offset = row["hit_offset"]
                receipt = db.execute("SELECT text_sha256 FROM agent_archive_chunks "
                    "WHERE archive_sequence=? AND offset=?", (row["sequence"], offset)).fetchone()
                chunk = public[offset:offset + CHUNK_CHARS]
                if (type(offset) is not int or offset < 0 or offset >= len(public)
                        or receipt is None or receipt["text_sha256"] != _sha(chunk)
                        or not set(tokens).intersection(_terms(chunk))):
                    raise ValueError("knowledge_agent_archive_integrity_error")
                folded = chunk.casefold()
                position = folded.find(query.casefold())
                if position < 0:
                    positions = [folded.find(token[1:]) for token in tokens if folded.find(token[1:]) >= 0]
                    position = min(positions, default=0)
                # Offsets are always Unicode character offsets in public text.
                # casefold may expand letters; a conservative earlier start
                # remains a truthful slice even for those uncommon strings.
                snippet_offset = offset + max(0, min(position, len(chunk)) - 100)
                snippet = public[snippet_offset:snippet_offset + 500]
                hits.append({**self._manifest(row), "offset": snippet_offset,
                             "snippet": snippet, "snippet_truncated": len(public) > len(snippet)})
        return _envelope(query=query, hits=hits, results_limited=len(rows) > limit,
            retrieval="lexical_matching_operations_by_recency", semantic_match_proven=False)

    def archive_read(self, session, ref, offset=0, limit=2000, *, exclude_operation=""):
        _key(session)
        _key(exclude_operation, empty=True)
        _limit(limit, 4000)
        if (not isinstance(ref, str) or not _HEX.fullmatch(ref)
                or type(offset) is not int or offset < 0 or offset > 4 * 1024 * 1024):
            raise ValueError("knowledge_agent_archive_invalid_request")
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM agent_archive_operations "
                "WHERE session=? AND ref=? AND operation<>?", (session, ref, exclude_operation)).fetchone()
            if row is None:
                raise ValueError("knowledge_agent_archive_not_found")
            public = self._verified_archive_row(db, row)
        if offset > len(public):
            raise ValueError("knowledge_agent_archive_invalid_request")
        text = public[offset:offset + limit]
        end = offset + len(text)
        return _envelope(**self._manifest(row), text=text, offset=offset,
            next_offset=end if end < len(public) else None, complete=end == len(public),
            offset_unit="unicode_characters")
