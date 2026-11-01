"""Persistent ReasoningBank cache with a lazy, offline local semantic embedder.

No model API or cloud SDK is used. The model cache is prepared by installation;
an unavailable model is reported explicitly, never replaced by keyword matching.
"""
from contextlib import contextmanager
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Lock
from typing import Dict, List, Tuple

from filelock import FileLock

from ..domain.experience_evolution import canonical, digest
from ..domain.reasoning_bank import (
    ReasoningBankUnavailable, memory_text, merge_entries,
)
from .reasoning_bank_source import load_upstream_functions

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class FastEmbedReasoningEmbedder:
    def __init__(self, cache_directory, *, model_name=DEFAULT_EMBEDDING_MODEL):
        self.cache_directory = Path(cache_directory)
        self.model_name = model_name
        self._model = None
        self._lock = Lock()

    @property
    def identity(self):
        return f"fastembed:{self.model_name}:mean-pooling:v1"

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from fastembed import TextEmbedding
                        self._model = TextEmbedding(model_name=self.model_name,
                            cache_dir=str(self.cache_directory), local_files_only=True)
                    except (ImportError, OSError, ValueError, RuntimeError) as error:
                        raise ReasoningBankUnavailable("reasoning_bank_local_embedding_unavailable") from error
        return self._model

    def embed(self, texts):
        try:
            return [vector.tolist() for vector in self._load().embed(texts)]
        except ReasoningBankUnavailable:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            raise ReasoningBankUnavailable("reasoning_bank_local_embedding_failed") from error

    def embed_query(self, query, instruction):
        # MiniLM is a symmetric sentence encoder, not an instruction-tuned model.
        # Other embedding adapters may use Google's Instruct:/Query: projection.
        return self.embed([query])[0]


def load_memory_management(embedder, upstream_root=None):
    """Run Google's exact retrieval functions with a real local embedding provider."""
    try:
        import torch
        import torch.nn.functional as functional
    except ImportError as error:
        raise ReasoningBankUnavailable("reasoning_bank_torch_unavailable") from error
    namespace = {"os": os, "json": json, "torch": torch, "F": functional,
                 "Tensor": torch.Tensor, "List": List, "Dict": Dict, "Tuple": Tuple,
                 "logger": logging.getLogger(__name__)}
    module, receipt = load_upstream_functions("WebArena/memory_management.py",
        names=("l2_normalize", "load_cached_embeddings", "get_detailed_instruct",
               "select_memory", "screening"), namespace=namespace, upstream_root=upstream_root)

    def local_embedding(query, dimensionality=None):
        instruction = ""
        if query.startswith("Instruct: ") and "\nQuery: " in query:
            instruction, query = query[len("Instruct: "):].split("\nQuery: ", 1)
        vector = embedder.embed_query(query, instruction)
        _validate_vector(vector)
        return torch.tensor([vector], dtype=torch.float32)

    module.embed_query_with_gemini = local_embedding
    module.embed_query_with_qwen = local_embedding
    receipt["provider_adapter"] = {"embedding": embedder.identity,
        "replaced_symbols": ["embed_query_with_gemini", "embed_query_with_qwen"],
        "instruction_handling": "embedding_provider_contract"}
    return module, receipt


def _validate_vector(vector):
    if (not vector or any(isinstance(value, bool) or not isinstance(value, (int, float))
                          or not math.isfinite(value) for value in vector)
            or not any(value != 0 for value in vector)):
        raise ValueError("reasoning_bank_invalid_embedding")


class ReasoningBankRepository:
    def __init__(self, root, edition_repository, embedder, *, upstream_root=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.editions = edition_repository
        self.embedder = embedder
        self.upstream_root = upstream_root

    @contextmanager
    def _database(self):
        path = self.root / "reasoning-bank.sqlite"
        if self.root.is_symlink() or path.is_symlink():
            raise ValueError("reasoning_bank_unsafe_path")
        connection = sqlite3.connect(path, timeout=30)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS memories(
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL,
                    sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS memory_scope ON memories(scope);
            """)
            yield connection
            connection.commit()
        finally:
            connection.close()

    def get_edition(self, identifier):
        return self.editions.get_version(identifier)

    def embedding_identity(self):
        return self.embedder.identity

    @staticmethod
    def _entry(payload, checksum):
        entry = json.loads(payload)
        if digest(entry) != checksum:
            raise ValueError("reasoning_bank_memory_changed")
        return entry

    def _vectors_path(self, scope):
        directory = self.root / "reasoning-bank-vectors" / digest(self.embedding_identity())
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{scope}.jsonl"
        if directory.is_symlink() or path.is_symlink():
            raise ValueError("reasoning_bank_unsafe_path")
        return path

    def _ensure_vectors(self, entries):
        module, _receipt = load_memory_management(self.embedder, self.upstream_root)
        for scope in sorted({entry["scope"] for entry in entries}):
            path = self._vectors_path(scope)
            with FileLock(str(path) + ".lock"):
                ids, _texts, _vectors = module.load_cached_embeddings(str(path))
                missing = {entry["id"]: entry for entry in entries
                           if entry["scope"] == scope and entry["id"] not in set(ids)}
                if not missing:
                    continue
                generated = self.embedder.embed([memory_text(entry) for entry in missing.values()])
                if len(generated) != len(missing):
                    raise ValueError("reasoning_bank_embedding_count_mismatch")
                for vector in generated:
                    _validate_vector(vector)
                # Keep the original upstream JSONL protocol; no parallel vector DB.
                with path.open("a", encoding="utf-8") as stream:
                    for entry, vector in zip(missing.values(), generated):
                        stream.write(json.dumps({"id": entry["id"], "text": memory_text(entry),
                                                 "embedding": vector}, ensure_ascii=True) + "\n")

    def index(self, entries):
        texts = [memory_text(entry) for entry in entries]
        self._ensure_vectors(entries)
        indexed = merged = 0
        with self._database() as database:
            database.execute("BEGIN IMMEDIATE")
            for incoming, text in zip(entries, texts):
                previous = database.execute("SELECT payload,sha256 FROM memories WHERE id=?",
                                            (incoming["id"],)).fetchone()
                existing = self._entry(*previous) if previous else None
                entry = merge_entries(existing, incoming)
                indexed += existing is None
                merged += existing is not None
                database.execute("INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?)",
                    (entry["id"], entry["scope"], canonical(entry), digest(entry), digest(text)))
        return {"indexed": indexed, "merged": merged}

    def has_entries(self, scope, edition_ids=None):
        selected = None if edition_ids is None else frozenset(edition_ids)
        with self._database() as database:
            for payload, checksum in database.execute("SELECT payload,sha256 FROM memories WHERE scope=?", (scope,)):
                if selected is None:
                    return True
                entry = self._entry(payload, checksum)
                if any(source["edition"]["id"] in selected for source in entry["sources"]):
                    return True
        return False

    def select_memories(self, scope, query, limit, edition_ids=None):
        selected = None if edition_ids is None else frozenset(edition_ids)
        bank = []
        with self._database() as database:
            for payload, checksum in database.execute("SELECT payload,sha256 FROM memories WHERE scope=?", (scope,)):
                entry = self._entry(payload, checksum)
                if selected is not None:
                    entry["sources"] = [source for source in entry["sources"] if source["edition"]["id"] in selected]
                    if not entry["sources"]:
                        continue
                bank.append({"task_id": entry["id"], "query": memory_text(entry),
                             "memory_items": [memory_text(entry)], "entry": entry})
        module, receipt = load_memory_management(self.embedder, self.upstream_root)
        path = self._vectors_path(scope)
        with FileLock(str(path) + ".lock"):
            ids, texts, vectors = module.load_cached_embeddings(str(path))
        cached = {identifier: {"id": identifier, "text": text, "embedding": vector}
                  for identifier, text, vector in zip(ids, texts, vectors.tolist())}
        if any(item["task_id"] not in cached for item in bank):
            raise ReasoningBankUnavailable("reasoning_bank_embedding_reindex_required")
        scores = {}
        original_screening = module.screening

        def record_screening(*args, **kwargs):
            score_items, ordered = original_screening(*args, **kwargs)
            scores.update(score_items)
            return score_items, ordered

        module.screening = record_screening
        # Upstream screening appends the current query. Keep that side effect in
        # a task-local native cache: queries are not accepted procedural memories.
        with tempfile.TemporaryDirectory(prefix="reasoning-bank-", dir=self.root) as temporary:
            projection = Path(temporary) / "embeddings.jsonl"
            with projection.open("w", encoding="utf-8") as stream:
                for item in bank:
                    stream.write(json.dumps(cached[item["task_id"]], ensure_ascii=True) + "\n")
            matches = module.select_memory(limit, bank, query, task_id=digest(query),
                                           cache_path=str(projection), prefer_model=self.embedding_identity())
            query_records = [json.loads(line) for line in projection.read_text(encoding="utf-8").splitlines()][len(bank):]
        receipt.update({"invoked": ["select_memory", "screening", "load_cached_embeddings", "l2_normalize", "get_detailed_instruct"],
            "cache_projection": "task_local_original_jsonl_protocol",
            "candidate_ids": [item["task_id"] for item in bank],
            "query_cache_records": [{"id": record["id"], "text": record["text"],
                                     "embedding_sha256": digest(record["embedding"])} for record in query_records]})
        return {"matches": [{"entry": item["entry"], "score": scores[item["task_id"]]} for item in matches],
                "execution": receipt}
