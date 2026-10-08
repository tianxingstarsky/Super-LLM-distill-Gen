from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from lib.application.knowledge_service import KnowledgeApplication
from lib.bootstrap.knowledge import knowledge_application
from lib.bootstrap.local_inputs import local_input_application
from lib.domain.knowledge import validate_connection, validate_vector
from lib.infrastructure.knowledge_driver import FilesystemKnowledgeDriver, _request_json


def local_sources(root):
    return local_input_application(root).store([
        ("repair.md", "# 设备维护\n\n更换电池之前应断开电源。维修工具需定期检查。".encode()),
        ("garden.txt", b"Rose plants need sunlight and regular watering. Prune flowers in spring.")])


def test_persistent_local_retrieval_ranks_real_chinese_and_english_content(tmp_path):
    sources = local_sources(tmp_path)
    application = knowledge_application(root=tmp_path)
    assert application.index([row["path"] for row in sources])["documents"] == 2
    chinese = application.retrieve("local", "更换电池", 2)
    english = application.retrieve("local", "rose sunlight", 2)
    assert len(chinese["hits"]) == 1 and "断开电源" in chinese["hits"][0]["text"]
    assert "Rose" in english["hits"][0]["text"]
    assert chinese["hits"][0]["location"].startswith("document:chunk:")
    reopened = knowledge_application(root=tmp_path)
    assert reopened.retrieve("local", "更换电池", 2)["hits"] == chinese["hits"]
    assert reopened.retrieve("local", "no_match_xyz", 2)["hits"] == []


def test_incremental_index_updates_without_duplicate_chunks(tmp_path):
    source = local_sources(tmp_path)[0]["path"]
    application = knowledge_application(root=tmp_path)
    first = application.index([source])
    assert application.index([source])["unchanged"] == 1
    Path(source).write_text("Replacement content about ocean coral.", encoding="utf-8")
    second = application.index([source])
    assert second["updated"] == 1 and second["chunks"] == 1
    assert first["revision"] != second["revision"]
    assert application.retrieve("local", "电池", 2)["hits"] == []


def test_failed_index_batch_preserves_prior_index(tmp_path):
    source = local_sources(tmp_path)[0]["path"]
    application = knowledge_application(root=tmp_path)
    original = application.index([source])
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(ValueError, match="preview_source_outside_cache"):
        application.index([source, str(outside)])
    assert application.status() == {key: original[key] for key in ("documents", "chunks", "revision")}


def test_retrieval_snapshots_preserve_text_and_receipts_without_keys(tmp_path):
    source = local_sources(tmp_path)[0]["path"]
    application = knowledge_application(root=tmp_path)
    application.index([source])
    result = application.retrieve("local", "电池", 2)
    saved = application.materialize(result)
    assert Path(saved[0]["path"]).read_text(encoding="utf-8") == result["hits"][0]["text"]
    receipt = json.loads(Path(saved[0]["receipt"]).read_text(encoding="utf-8"))
    assert receipt["query"] == "电池"
    assert receipt["hits"][0]["fingerprint"]
    assert "document:chunk:" in saved[0]["name"]


@contextmanager
def qdrant_server(*, dimensions=3, vector=None, error=False):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            calls.append(("GET", self.path, dict(self.headers), None))
            body = {"result": {"config": {"params": {"vectors": {"dense": {"size": dimensions}}}}}}
            self.send_response(401 if error else 200)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(("POST", self.path, dict(self.headers), body))
            if self.path == "/v1/embeddings":
                result = {"data": [{"embedding": [0.1, 0.2, 0.3] if vector is None else vector}]}
            else:
                result = {"result": {"points": [{"id": 42, "score": .87, "payload": {"content": {"text": "Verified manual excerpt."}, "metadata": {"source": "manual.pdf"}}}]}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def config(url):
    return {"url": url, "collection": "manuals", "embedding_url": url + "/v1", "embedding_model": "same-index-model",
            "text_field": "content.text", "source_field": "metadata.source", "vector_name": "dense"}


def test_qdrant_sends_real_query_vector_and_named_vector(tmp_path):
    with qdrant_server() as (url, calls):
        application = knowledge_application(root=tmp_path)
        application.save_connection(config(url), "qdrant-private-key", "embedding-private-key")
        safe = application.connection()
        assert "api_key" not in safe and "embedding_key" not in safe
        assert "private-key" not in json.dumps(safe)
        assert application.check_connection()["dimensions"] == 3
        result = application.retrieve("qdrant", "manual operation", 4)
        assert calls[-2][1] == "/v1/embeddings"
        assert calls[-2][3] == {"model": "same-index-model", "input": "manual operation"}
        assert calls[-2][2]["Authorization"] == "Bearer embedding-private-key"
        assert calls[-1][1] == "/collections/manuals/points/query"
        assert calls[-1][3] == {"query": [.1, .2, .3], "limit": 4, "with_payload": True, "with_vector": False, "using": "dense"}
        assert calls[-1][2]["Api-Key"] == "qdrant-private-key"
        assert result["hits"][0]["source"] == "manual.pdf"
        assert result["hits"][0]["location"] == "qdrant:point:42"
        assert "private-key" not in json.dumps(result)


def test_embedding_dimension_mismatch_blocks_qdrant_query(tmp_path):
    with qdrant_server(dimensions=4) as (url, calls):
        application = knowledge_application(root=tmp_path)
        application.save_connection(config(url))
        with pytest.raises(ValueError, match="knowledge_embedding_dimension_mismatch"):
            application.retrieve("qdrant", "manual operation", 4)
        assert not any(path.endswith("points/query") for _, path, _, _ in calls)


def test_remote_error_does_not_expose_provider_content(tmp_path):
    with qdrant_server(error=True) as (url, _):
        application = knowledge_application(root=tmp_path)
        application.save_connection(config(url), "private-key")
        with pytest.raises(ValueError, match="^knowledge_connection_unavailable$"):
            application.retrieve("qdrant", "manual operation", 4)


def test_changing_recipient_never_reuses_previous_credentials(tmp_path):
    application = knowledge_application(root=tmp_path)
    value = config("http://127.0.0.1:6333")
    application.save_connection(value, "q-secret", "e-secret")
    application.save_connection(value)
    assert application.connection()["has_api_key"]
    application.save_connection(config("http://localhost:6333"))
    assert not application.connection()["has_api_key"]
    assert not application.connection()["has_embedding_key"]


@pytest.mark.parametrize("value", [[float("nan")], [float("inf")], [True], ["1"], []])
def test_invalid_embedding_values_are_rejected(value):
    with pytest.raises(ValueError):
        validate_vector(value, 1)


def test_credential_urls_are_rejected():
    with pytest.raises(ValueError):
        validate_connection(config("http://secret:password@localhost:6333"))


def test_query_and_input_limits(tmp_path):
    application = knowledge_application(root=tmp_path)
    for query, limit in (("", 10), ("x" * 2001, 10), ("query", 0), ("query", 51)):
        with pytest.raises(ValueError):
            application.retrieve("local", query, limit)
    with pytest.raises(ValueError):
        application.materialize({"provider": "local", "hits": []})
