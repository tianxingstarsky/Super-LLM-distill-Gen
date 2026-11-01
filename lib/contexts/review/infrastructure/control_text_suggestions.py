"""Persist rule suggestions beside requests; index only small pending proposals."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from lib.io_utils import atomic_json
from ..domain.control_text_suggestions import (
    control_text_review_instruction, digest, split_control_text_review_response, scope_control_text_review_context,
    answer_control_text_review_context,
)


class ControlTextSuggestionRepository:
    def __init__(self, output):
        self.output = Path(output)
        self.index = self.output / ".control-text-suggestions.sqlite3"

    @contextmanager
    def _connection(self, *, write=False):
        connection = None
        try:
            if write:
                self.output.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.index, timeout=5)
                connection.execute("CREATE TABLE IF NOT EXISTS suggestions "
                "(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, receipt_id TEXT NOT NULL, "
                "item_index INTEGER NOT NULL, node TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at TEXT NOT NULL, summary TEXT NOT NULL)")
                connection.execute("CREATE INDEX IF NOT EXISTS suggestions_pending "
                               "ON suggestions(node,status,created_at DESC)")
            else:
                connection = sqlite3.connect(self.index.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            yield connection
            if write:
                connection.commit()
        except sqlite3.Error as error:
            raise ValueError("control_text_suggestion_storage_unavailable") from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _proposal_id(item):
        proposal = item["proposal"]
        return digest({"configuration_sha256": item["source"]["configuration_sha256"], "node": item["node"],
                       "rule_id": proposal["rule_id"], "quote": proposal["quote"]})

    def _receipt_path(self, run_id, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValueError("invalid_control_text_suggestion_reference")
        return self.output / "workflows" / run_id / "control-text-suggestions" / (identifier + ".json")

    def save(self, run_id, *, context, raw_response, key, prompt_id):
        _, proposals, invalid = split_control_text_review_response(raw_response, context)
        # Empty/invalid advice already remains in the ordinary request checkpoint.
        if not proposals:
            return
        identities = [self._proposal_id(item) for item in proposals]
        if self.index.is_file():
            with self._connection() as connection:
                existing = connection.execute("SELECT id FROM suggestions WHERE id IN (" +
                    ",".join("?" for _ in identities) + ")", identities).fetchall()
            if len(existing) == len(identities):
                return
        identity = digest({"context": context, "response": raw_response, "key": key, "prompt_id": prompt_id})
        path = self._receipt_path(run_id, identity)
        if path.exists():
            receipt = self._read_receipt(path)
        else:
            body = {"version": 1, "id": identity, "run_id": run_id, "context": deepcopy(context),
                    "raw_response": deepcopy(raw_response), "key": deepcopy(key), "prompt_id": prompt_id,
                    "proposals": proposals, "invalid": invalid,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds")}
            receipt = {**body, "sha256": digest(body)}
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, receipt)
        if not receipt["proposals"]:
            return
        with self._connection(write=True) as connection:
            for index, item in enumerate(receipt["proposals"]):
                item_id = self._proposal_id(item)
                summary = {**item, "id": item_id, "run_id": run_id, "created_at": receipt["created_at"]}
                connection.execute("INSERT OR IGNORE INTO suggestions VALUES (?,?,?,?,?,?,?,?)",
                    (item_id, run_id, identity, index, item["node"], "pending", receipt["created_at"],
                     json.dumps(summary, ensure_ascii=False)))

    @staticmethod
    def _read_receipt(path):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or receipt.get("sha256") != digest(
                {key: value for key, value in receipt.items() if key != "sha256"}):
            raise ValueError("control_text_suggestion_receipt_changed")
        if receipt.get("id") != digest({"context": receipt["context"], "response": receipt["raw_response"],
                                        "key": receipt["key"], "prompt_id": receipt["prompt_id"]}):
            raise ValueError("control_text_suggestion_receipt_changed")
        return receipt

    def list_pending(self, node, limit):
        if not self.index.is_file():
            return []
        with self._connection() as connection:
            rows = connection.execute("SELECT summary,status FROM suggestions WHERE node=? AND status='pending' "
                                      "ORDER BY created_at DESC,id LIMIT ?", (node, limit)).fetchall()
        return [{**json.loads(row["summary"]), "status": row["status"]} for row in rows]

    def get(self, identifier):
        if not self.index.is_file():
            raise ValueError("control_text_suggestion_not_found")
        with self._connection() as connection:
            indexed = connection.execute("SELECT * FROM suggestions WHERE id=?", (identifier,)).fetchone()
        if indexed is None:
            raise ValueError("control_text_suggestion_not_found")
        path = self._receipt_path(indexed["run_id"], indexed["receipt_id"])
        receipt = self._read_receipt(path)
        if receipt.get("run_id") != indexed["run_id"] or receipt.get("id") != indexed["receipt_id"]:
            raise ValueError("control_text_suggestion_receipt_changed")
        _, valid, invalid = split_control_text_review_response(receipt["raw_response"], receipt["context"])
        if valid != receipt["proposals"] or invalid != receipt["invalid"]:
            raise ValueError("control_text_suggestion_receipt_changed")
        index = indexed["item_index"]
        if not 0 <= index < len(valid) or identifier != self._proposal_id(valid[index]):
            raise ValueError("control_text_suggestion_receipt_changed")
        expected = {**valid[index], "id": identifier, "run_id": indexed["run_id"], "created_at": receipt["created_at"]}
        if expected != json.loads(indexed["summary"]):
            raise ValueError("control_text_suggestion_index_changed")
        return {**expected, "status": indexed["status"]}

    def resolve(self, identifier, status):
        self.get(identifier)
        with self._connection(write=True) as connection:
            connection.execute("UPDATE suggestions SET status=? WHERE id=? AND status='pending'", (status, identifier))


def request_with_control_suggestions(runtime, key, role, prompt_id, payload, *, control_context=None, **options):
    """Ask once; persist optional advice; return the untouched inner quality reply."""
    control_context = scope_control_text_review_context(control_context, payload)
    if control_context is None:
        return runtime.ask(key, role, prompt_id, payload, **options)
    options["instruction"] = (options.get("instruction") or "") + control_text_review_instruction(control_context)
    raw_response = runtime.ask(key, role, prompt_id, payload, **options)
    review, _, _ = split_control_text_review_response(raw_response, control_context)
    # Review candidates, generation settings and eligibility are never edited.
    repository = ControlTextSuggestionRepository(runtime.path.parent.parent)
    try:
        repository.save(runtime.state["id"], context=control_context, raw_response=raw_response,
                        key=key, prompt_id=prompt_id)
    except OSError:
        runtime.event("control_text_suggestion_save_failed", role=role)
    except ValueError as error:
        if str(error) != "control_text_suggestion_storage_unavailable":
            raise
        runtime.event("control_text_suggestion_save_failed", role=role)
    return review


def request_answer_review(runtime, key, context, answer, prompt_id, **options):
    from lib.contexts.datasets.contracts import without_control_text_metadata
    payload = {"context": without_control_text_metadata(context), "answer": without_control_text_metadata(answer)}
    control_context = answer_control_text_review_context(context, answer, payload["answer"])
    return request_with_control_suggestions(runtime, key, "jev", prompt_id, payload,
                                            control_context=control_context, **options)
