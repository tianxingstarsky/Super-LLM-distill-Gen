"""Persistent local full-text retrieval and read-only Qdrant vector queries."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from filelock import FileLock

from lib import workspace
from lib.doc2corpus import chunk_text, import_text
from lib.domain.knowledge import search_tokens, payload_value, validate_connection, validate_vector
from lib.infrastructure.document_preview_driver import FilesystemDocumentPreviewDriver
from lib.infrastructure.local_input_cache import FilesystemLocalInputCacheDriver, _assert_unlinked
from lib.io_utils import atomic_json
from lib.infrastructure.web_research_connection import _protect, _read_descriptor


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _request_json(url: str, *, body=None, api_key="", bearer=False) -> dict:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization" if bearer else "api-key"] = "Bearer " + api_key if bearer else api_key
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        with build_opener(_NoRedirect()).open(Request(url, data=data, headers=headers), timeout=30) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("knowledge_response_too_large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("knowledge_response_invalid")
            return result
    except Exception as error:
        # Provider bodies/URLs may contain sensitive material. Keep them out of UI/logs.
        if isinstance(error, ValueError) and str(error).startswith("knowledge_"):
            raise
        raise ValueError("knowledge_connection_unavailable") from None


class FilesystemKnowledgeDriver:
    def __init__(self, workspace_id="default", root=None):
        self.workspace_id = workspace_id
        self.root = Path(root) if root is not None else None
        self.directory = workspace.folder(workspace_id, self.root).absolute() / ".dataforge" / "knowledge"
        self.config_path = (self.root or workspace.ROOT) / ".dataforge" / "connections" / "knowledge.json"
        self.source_driver = FilesystemDocumentPreviewDriver(workspace_id, self.root)

    @contextmanager
    def _database(self):
        _assert_unlinked(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "index.sqlite3"
        for candidate in (path, Path(str(path) + "-journal"), Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            _assert_unlinked(candidate)
        try:
            connection = sqlite3.connect(path, timeout=30)
        except sqlite3.Error:
            raise ValueError("knowledge_storage_unavailable") from None
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS documents (path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, name TEXT NOT NULL, chunks INTEGER NOT NULL)")
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(tokens, text UNINDEXED, source UNINDEXED, name UNINDEXED, location UNINDEXED, fingerprint UNINDEXED)")
            yield connection
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise ValueError("knowledge_storage_unavailable") from None
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def status(self):
        if not (self.directory / "index.sqlite3").exists():
            return {"documents": 0, "chunks": 0, "revision": "empty"}
        with self._database() as database:
            rows = database.execute("SELECT path, fingerprint, chunks FROM documents ORDER BY path").fetchall()
        revision = hashlib.sha256(json.dumps([tuple(row) for row in rows]).encode()).hexdigest()
        return {"documents": len(rows), "chunks": sum(row["chunks"] for row in rows), "revision": revision}

    def describe_source(self, source):
        return self.source_driver.describe(source, frozenset({".md", ".txt", ".pdf", ".docx"}))

    def index(self, sources, chunk_chars):
        added = unchanged = 0
        # A transaction replaces each source completely. Parsing failure keeps the prior index.
        with self._database() as database:
            for source in sources:
                info = self.source_driver.describe(source, frozenset({".md", ".txt", ".pdf", ".docx"}))
                path = Path(info["path"])
                if info["size"] > 10 * 1024 * 1024:
                    raise ValueError("knowledge_index_file_limit")
                with path.open("rb") as handle:
                    raw = handle.read(10 * 1024 * 1024 + 1)
                if len(raw) > 10 * 1024 * 1024:
                    raise ValueError("knowledge_index_file_limit")
                identity = json.dumps([hashlib.sha256(raw).hexdigest(), chunk_chars],
                                      separators=(",", ":"), ensure_ascii=False)
                fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                previous = database.execute("SELECT fingerprint FROM documents WHERE path=?", (info["path"],)).fetchone()
                if previous and previous[0] == fingerprint:
                    unchanged += 1
                    continue
                # Parse exactly the bytes hashed above, not a second view of a mutable source.
                try:
                    with tempfile.TemporaryDirectory(prefix="shujian-knowledge-") as temporary:
                        snapshot = Path(temporary) / ("source" + path.suffix.lower())
                        snapshot.write_bytes(raw)
                        if snapshot.suffix == ".pdf":
                            from pypdf import PdfReader
                            reader = PdfReader(snapshot)
                            if reader.is_encrypted or len(reader.pages) > 200:
                                raise ValueError("knowledge_pdf_limit")
                        elif snapshot.suffix == ".docx":
                            with zipfile.ZipFile(snapshot) as archive:
                                entries = archive.infolist()
                                if (len(entries) > 5000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024
                                        or any(item.flag_bits & 1 for item in entries)):
                                    raise ValueError("knowledge_docx_limit")
                        text = import_text(snapshot).replace("\r\n", "\n").strip()
                except Exception as error:
                    if isinstance(error, ValueError) and str(error).startswith("knowledge_"):
                        raise
                    raise ValueError("knowledge_document_parse_failed") from None
                if self.source_driver.describe(source, frozenset({path.suffix.lower()}))["version"] != info["version"]:
                    raise ValueError("knowledge_source_changed")
                if len(text) > 2_000_000:
                    raise ValueError("knowledge_index_text_limit")
                chunks = [part[offset:offset + chunk_chars]
                          for part in chunk_text(text, chunk_chars)
                          for offset in range(0, len(part), chunk_chars)]
                database.execute("DELETE FROM passages WHERE source=?", (info["path"],))
                database.executemany("INSERT INTO passages(tokens,text,source,name,location,fingerprint) VALUES (?,?,?,?,?,?)",
                    [(" ".join(search_tokens(chunk)), chunk, info["path"], info["label"], f"document:chunk:{i}", fingerprint)
                     for i, chunk in enumerate(chunks)])
                database.execute("INSERT OR REPLACE INTO documents(path,fingerprint,name,chunks) VALUES (?,?,?,?)",
                                 (info["path"], fingerprint, info["label"], len(chunks)))
                added += 1
        return {**self.status(), "updated": added, "unchanged": unchanged}

    def _read_connection(self):
        _assert_unlinked(self.config_path)
        if not self.config_path.exists():
            return {}
        info = self.config_path.stat()
        if (info.st_size > 65536 or not stat.S_ISREG(info.st_mode)
                or os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("knowledge_connection_invalid")
        try:
            with os.fdopen(_read_descriptor(self.config_path), "rb") as handle:
                opened = os.fstat(handle.fileno())
                if opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError
                document = json.loads(handle.read(65537))
            if document.get("version") != 1 or not isinstance(document.get("revision"), str):
                raise ValueError
            raw = base64.b64decode(document["protected_config"], validate=True)
            value = json.loads(_protect(raw, self.config_path, decrypt=True).decode("utf-8"))
            config = validate_connection(value)
            for key in ("api_key", "embedding_key"):
                secret = value.get(key, "")
                if not isinstance(secret, str) or len(secret) > 4096 or any(ord(c) < 32 for c in secret):
                    raise ValueError
                config[key] = secret
            return {**config, "_revision": document["revision"]}
        except (ValueError, TypeError, AttributeError, KeyError):
            raise ValueError("knowledge_connection_invalid") from None

    def connection(self):
        config = self._read_connection()
        if not config:
            return {"configured": False, "revision": "empty"}
        return {**{key: value for key, value in config.items() if key not in {"api_key", "embedding_key", "_revision"}},
                "configured": True, "has_api_key": bool(config["api_key"]),
                "has_embedding_key": bool(config["embedding_key"]), "revision": config["_revision"]}

    def save_connection(self, config, api_key, embedding_key):
        _assert_unlinked(self.config_path)
        self.config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.config_path.parent, 0o700)
        lock = Path(str(self.config_path) + ".lock")
        _assert_unlinked(lock)
        with FileLock(str(lock), timeout=10):
            previous = self._read_connection()
            # An empty field means retain a key only for the exact same recipient.
            same_qdrant = previous.get("url") == config["url"]
            same_embedding = previous.get("embedding_url") == config["embedding_url"]
            value = {**config,
                "api_key": api_key or (previous.get("api_key", "") if same_qdrant else ""),
                "embedding_key": embedding_key or (previous.get("embedding_key", "") if same_embedding else "")}
            protected = _protect(json.dumps(value).encode("utf-8"), self.config_path)
            atomic_json(self.config_path, {"version": 1, "revision": uuid.uuid4().hex,
                "protected_config": base64.b64encode(protected).decode("ascii")})
            os.chmod(self.config_path, 0o600)

    def _collection(self, config):
        info = _request_json(config["url"] + "/collections/" + quote(config["collection"], safe=""), api_key=config["api_key"])
        try:
            vectors = info["result"]["config"]["params"]["vectors"]
            vector = vectors[config["vector_name"]] if config["vector_name"] else vectors
            dimensions = vector["size"]
            if type(dimensions) is not int or not 1 <= dimensions <= 65536:
                raise ValueError
            return dimensions
        except (TypeError, KeyError, ValueError):
            raise ValueError("knowledge_vector_config_invalid") from None

    def check_connection(self):
        config = self._read_connection()
        if not config:
            raise ValueError("knowledge_connection_required")
        return {"dimensions": self._collection(config)}

    def retrieve(self, provider, query, limit):
        if provider == "local":
            tokens = search_tokens(query)[:64]
            if not tokens:
                raise ValueError("knowledge_query_required")
            expression = " OR ".join('"' + token + '"' for token in tokens)
            with self._database() as database:
                rows = database.execute("SELECT rowid,text,source,name,location,fingerprint,bm25(passages) AS rank FROM passages WHERE passages MATCH ? ORDER BY rank LIMIT ?", (expression, limit)).fetchall()
            hits = [{"id": str(row["rowid"]), "text": row["text"], "source": row["name"],
                     "location": row["location"], "fingerprint": row["fingerprint"],
                     "score": -row["rank"]} for row in rows]
            revision = self.status()["revision"]
            collection = "local"
        else:
            config = self._read_connection()
            if not config:
                raise ValueError("knowledge_connection_required")
            dimensions = self._collection(config)
            embedding = _request_json(config["embedding_url"] + "/embeddings", body={"model": config["embedding_model"], "input": query}, api_key=config["embedding_key"], bearer=True)
            try:
                vector = validate_vector(embedding["data"][0]["embedding"], dimensions)
            except (KeyError, IndexError, TypeError):
                raise ValueError("knowledge_embedding_response_invalid") from None
            body = {"query": vector, "limit": limit, "with_payload": True, "with_vector": False}
            if config["vector_name"]:
                body["using"] = config["vector_name"]
            response = _request_json(config["url"] + "/collections/" + quote(config["collection"], safe="") + "/points/query", body=body, api_key=config["api_key"])
            try:
                points = response["result"]["points"]
                if not isinstance(points, list) or len(points) > limit:
                    raise ValueError
                hits = []
                for point in points:
                    payload = point.get("payload") or {}
                    text = payload_value(payload, config["text_field"])
                    score = point["score"]
                    if (not isinstance(text, str) or not text.strip() or len(text) > 20000
                            or type(score) not in (float, int) or not math.isfinite(score)):
                        raise ValueError
                    source = payload_value(payload, config["source_field"]) if config["source_field"] else None
                    hits.append({"id": str(point["id"]), "text": text, "source": str(source or config["collection"])[:2048],
                                 "location": "qdrant:point:" + str(point["id"]), "score": float(score),
                                 "fingerprint": hashlib.sha256(text.encode()).hexdigest()})
            except (TypeError, KeyError, ValueError, AttributeError):
                raise ValueError("knowledge_payload_invalid") from None
            revision = self.connection()["revision"]
            collection = config["collection"]
        return {"provider": provider, "query": query, "limit": limit, "revision": revision,
                "collection": collection, "hits": hits, "retrieved_at": datetime.now(timezone.utc).isoformat()}

    def materialize(self, result):
        uploads = []
        names = []
        for i, hit in enumerate(result["hits"]):
            text = hit.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 20000:
                raise ValueError("knowledge_payload_invalid")
            identifier = hashlib.sha256((str(hit.get("location")) + text).encode()).hexdigest()[:16]
            uploads.append((f"rag-{result['provider']}-{identifier}.md", text.encode("utf-8")))
            names.append(f"RAG · {result['collection']} · {hit.get('source', '')} · {hit.get('location', '')}")
        saved = FilesystemLocalInputCacheDriver(self.root).store(tuple(uploads))
        receipt_path = self.directory / "retrievals" / (uuid.uuid4().hex + ".json")
        _assert_unlinked(receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(receipt_path, {**result, "sources": saved})
        return [{**row, "name": names[i], "receipt": str(receipt_path)} for i, row in enumerate(saved)]
