"""Bounded retrieval settings and language-neutral local search tokens."""
from __future__ import annotations

import math
import re

from lib.domain.backend_config import validate_backend_url

MAX_HITS = 50
MAX_CHUNK_CHARS = 20000


def search_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.casefold())
    tokens = []
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            tokens.extend(word[i:i + 2] for i in range(max(1, len(word) - 1)))
        else:
            tokens.append(word)
    return list(dict.fromkeys(tokens))


def validate_query(query: str, limit: int) -> str:
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("knowledge_query_required")
    if type(limit) is not int or not 1 <= limit <= MAX_HITS:
        raise ValueError("knowledge_hit_limit_invalid")
    return query.strip()


def validate_connection(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("knowledge_connection_invalid")
    result = {}
    for key in ("url", "embedding_url"):
        result[key] = validate_backend_url(value.get(key, "").strip()).rstrip("/")
    for key in ("collection", "embedding_model", "text_field", "source_field", "vector_name"):
        text = value.get(key, "")
        if (not isinstance(text, str) or len(text) > 256 or any(ord(char) < 32 for char in text)
                or key in {"collection", "embedding_model", "text_field"} and not text.strip()):
            raise ValueError("knowledge_connection_invalid")
        result[key] = text.strip()
    return result


def validate_vector(vector: object, dimensions: int) -> list[float]:
    if (not isinstance(vector, list) or len(vector) != dimensions or not vector
            or len(vector) > 65536 or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
        raise ValueError("knowledge_embedding_dimension_mismatch")
    return [float(v) for v in vector]


def payload_value(payload: dict, field: str):
    value = payload
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value
