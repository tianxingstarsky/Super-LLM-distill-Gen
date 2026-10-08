"""Knowledge retrieval use cases, independent from UI and storage adapters."""
from typing import Protocol

from lib.domain.knowledge import validate_connection, validate_query


class KnowledgePort(Protocol):
    def describe_source(self, source: str) -> dict: ...
    def status(self) -> dict: ...
    def index(self, sources: list[str], chunk_chars: int) -> dict: ...
    def connection(self) -> dict: ...
    def save_connection(self, config: dict, api_key: str, embedding_key: str) -> None: ...
    def check_connection(self) -> dict: ...
    def retrieve(self, provider: str, query: str, limit: int) -> dict: ...
    def materialize(self, result: dict) -> list[dict]: ...


class KnowledgeApplication:
    def __init__(self, port: KnowledgePort):
        self._port = port

    def status(self) -> dict:
        return self._port.status()

    def describe_source(self, source: str) -> dict:
        return self._port.describe_source(source)

    def index(self, sources: list[str], chunk_chars: int = 2000) -> dict:
        if (not isinstance(sources, list) or not 1 <= len(sources) <= 200
                or any(not isinstance(path, str) for path in sources)
                or type(chunk_chars) is not int or not 200 <= chunk_chars <= 20000):
            raise ValueError("knowledge_index_selection_invalid")
        return self._port.index(list(dict.fromkeys(sources)), chunk_chars)

    def connection(self) -> dict:
        return self._port.connection()

    def save_connection(self, config: dict, api_key: str = "", embedding_key: str = "") -> None:
        for key in (api_key, embedding_key):
            if not isinstance(key, str) or len(key) > 4096 or any(ord(char) < 32 for char in key):
                raise ValueError("knowledge_connection_invalid")
        self._port.save_connection(validate_connection(config), api_key, embedding_key)

    def check_connection(self) -> dict:
        return self._port.check_connection()

    def retrieve(self, provider: str, query: str, limit: int = 10) -> dict:
        if provider not in {"local", "qdrant"}:
            raise ValueError("knowledge_provider_invalid")
        return self._port.retrieve(provider, validate_query(query, limit), limit)

    def materialize(self, result: dict) -> list[dict]:
        if (not isinstance(result, dict) or result.get("provider") not in {"local", "qdrant"}
                or not isinstance(result.get("hits"), list) or not 1 <= len(result["hits"]) <= 50):
            raise ValueError("knowledge_results_required")
        return self._port.materialize(result)
