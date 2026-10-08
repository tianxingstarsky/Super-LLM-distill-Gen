"""Keep retrieval evidence bound to the exact immutable text-source snapshots."""
from copy import deepcopy
from collections import Counter, defaultdict
import hashlib
import math
import re


def validate_knowledge_receipt(value, sources: list[dict]):
    if value is None:
        return None
    allowed = {"provider", "query", "collection", "hits", "retrieved_at", "limit", "revision"}
    if (not isinstance(value, dict) or set(value) - allowed or value.get("provider") not in {"local", "qdrant"}
            or not isinstance(value.get("hits"), list) or not 1 <= len(value["hits"]) <= 50):
        raise ValueError("invalid_knowledge_retrieval_receipt")
    for key, limit in (("query", 2000), ("collection", 2048), ("retrieved_at", 100)):
        if not isinstance(value.get(key), str) or len(value[key]) > limit:
            raise ValueError("invalid_knowledge_retrieval_receipt")
    if "limit" in value and (type(value["limit"]) is not int or not 1 <= value["limit"] <= 50):
        raise ValueError("invalid_knowledge_retrieval_receipt")
    if "revision" in value and (not isinstance(value["revision"], str) or len(value["revision"]) > 200):
        raise ValueError("invalid_knowledge_retrieval_receipt")
    result = deepcopy(value)
    expected = []
    by_hash = defaultdict(list)
    for source in sources:
        by_hash[source["sha256"]].append(source)
    for hit in result["hits"]:
        if not isinstance(hit, dict) or set(hit) != {"id", "text", "source", "location", "score", "fingerprint"}:
            raise ValueError("invalid_knowledge_retrieval_receipt")
        for key, limit in (("id", 2048), ("text", 20000), ("source", 2048), ("location", 2048)):
            if not isinstance(hit[key], str) or not hit[key].strip() or len(hit[key]) > limit:
                raise ValueError("invalid_knowledge_retrieval_receipt")
        if (type(hit["score"]) not in (int, float) or not math.isfinite(hit["score"])
                or not isinstance(hit["fingerprint"], str) or not re.fullmatch(r"[a-f0-9]{64}", hit["fingerprint"])):
            raise ValueError("invalid_knowledge_retrieval_receipt")
        digest = hashlib.sha256(hit["text"].encode("utf-8")).hexdigest()
        expected.append(digest)
    if Counter(expected) != Counter(source["sha256"] for source in sources):
        raise ValueError("knowledge_retrieval_source_mismatch")
    for hit, digest in zip(result["hits"], expected):
        source = by_hash[digest].pop(0)
        hit["snapshot"] = {"file": source["file"], "sha256": source["sha256"]}
    return result
