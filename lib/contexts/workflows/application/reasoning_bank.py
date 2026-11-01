"""Index authenticated editions, retrieve relevant memories, freeze new tasks."""
from typing import Protocol

from ..domain.experience_evolution import digest, validate_identifier, validate_scope
from ..domain.reasoning_bank import (
    FORMAT_VERSION, MAX_RETRIEVAL, RETRIEVAL_INSTRUCTION, ReasoningBankUnavailable,
    edition_reference, entries_from_edition, freeze_selection,
)


class ReasoningBankPort(Protocol):
    def get_edition(self, identifier: str) -> dict: ...
    def index(self, entries: list[dict]) -> dict: ...
    def has_entries(self, scope: str, edition_ids=None) -> bool: ...
    def embedding_identity(self) -> str: ...
    def select_memories(self, scope: str, query: str, limit: int, edition_ids=None) -> dict: ...


class ReasoningBank:
    def __init__(self, port: ReasoningBankPort):
        self._port = port

    def index_edition(self, edition_id):
        edition = self._port.get_edition(validate_identifier(edition_id))
        entries = entries_from_edition(edition)
        reference = edition_reference(edition)
        if not entries:
            return {"status": "empty", "indexed": 0, "merged": 0, "edition_ref": reference}
        try:
            return {"status": "indexed", **self._port.index(entries), "edition_ref": reference}
        except ReasoningBankUnavailable as error:
            return {"status": "unavailable", "indexed": 0, "merged": 0,
                    "edition_ref": reference, "error": str(error)}

    def retrieve(self, scope, task_text, limit=5, edition_ids=None):
        validate_scope(scope)
        if (not isinstance(task_text, str) or not task_text.strip() or len(task_text) > 32000
                or "\x00" in task_text):
            raise ValueError("reasoning_bank_invalid_task")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RETRIEVAL:
            raise ValueError("reasoning_bank_invalid_limit")
        if edition_ids is not None:
            edition_ids = sorted({validate_identifier(identifier) for identifier in edition_ids})
        receipt = {"format_version": FORMAT_VERSION, "scope": scope, "query": task_text,
                   "query_sha256": digest(task_text), "limit": limit, "edition_ids": edition_ids,
                   "embedding": self._port.embedding_identity(),
                   "retrieval_instruction": RETRIEVAL_INSTRUCTION}
        if not self._port.has_entries(scope, edition_ids):
            return {"status": "empty", "matches": [], "receipt": {**receipt, "status": "empty"}}
        try:
            selection = self._port.select_memories(scope, task_text, limit, edition_ids)
            matches = selection["matches"]
        except ReasoningBankUnavailable as error:
            return {"status": "unavailable", "matches": [],
                    "receipt": {**receipt, "status": "unavailable", "error": str(error)}}
        return {"status": "retrieved", "matches": matches,
                "receipt": {**receipt, "status": "retrieved", "matched": len(matches),
                            "upstream_execution": selection["execution"]}}

    def freeze(self, agent_experience_config, queries_by_scope, limit=5, edition_ids=None):
        if not isinstance(queries_by_scope, dict):
            raise ValueError("reasoning_bank_invalid_queries")
        selections = {scope: self.retrieve(scope, task, limit, edition_ids)
                      for scope, task in queries_by_scope.items()}
        return freeze_selection(agent_experience_config, selections)
