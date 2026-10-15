"""Request preview status follows the durable SQLite model checkpoint."""
import json

import httpx
import pytest

from lib.infrastructure.knowledge_agent_session import digest
from lib.infrastructure.workflow_stream_journal import StreamJournal, read_streams
from tests.test_model_tool_calls import MESSAGES, TOOLS, reply, sdk_client
from tests.test_workflow_knowledge_agent import Worker


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # Use the real client and SDK with MockTransport, without network services.
    yield


def real_worker(tmp_path):
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=reply("chat", calls=False, content='{"tasks": []}'))

    client = sdk_client("chat", handle)
    return Worker(tmp_path, client), captured


def test_real_model_request_promotes_preview_after_sqlite_checkpoint(tmp_path):
    worker, captured = real_worker(tmp_path)
    key = ["actual-knowledge-plan", 0]
    try:
        value = worker._knowledge_model_turn(key, MESSAGES, TOOLS, {"max_output_tokens": 1024})
        found, stored = worker._checkpoints.get("director", digest(key))
        assert found and stored == value
        assert len(captured) == 1
        assert not (worker.path / "checkpoints" / "director" / f"{digest(key)}.json").exists()
        rows = read_streams(worker.path, active=True, run_attempt=0, stage="director")
        assert len(rows) == 1
        assert rows[0]["status"] == "completed" and rows[0]["retryable"] is False
        assert json.loads(rows[0]["text"]) == {"tasks": []}
        assert list((worker.path / "streams").glob("*.completed.json"))
        assert not list((worker.path / "streams").glob("*.active.json"))
        # A committed response is reusable without another SDK request or a
        # new preview, independent of the plan's later semantic assessment.
        assert worker._knowledge_model_turn(key, MESSAGES, TOOLS, {"max_output_tokens": 1024}) == value
        assert len(captured) == 1
    finally:
        worker._client.client.close()


def test_sqlite_checkpoint_write_failure_interrupts_preview_and_preserves_error(tmp_path, monkeypatch):
    worker, captured = real_worker(tmp_path)
    original = OSError("checkpoint disk unavailable")

    def fail_write(*args, **kwargs):
        # Completion must not be advertised before the actual durable write.
        rows = read_streams(worker.path, active=True, run_attempt=0, stage="director")
        assert len(rows) == 1 and rows[0]["status"] == "active"
        raise original

    monkeypatch.setattr(worker._checkpoints, "put", fail_write)
    key = ["failed-knowledge-checkpoint", 0]
    try:
        with pytest.raises(OSError) as caught:
            worker._knowledge_model_turn(key, MESSAGES, TOOLS, {"max_output_tokens": 1024})
        assert caught.value is original
        assert len(captured) == 1
        assert worker._checkpoints.get("director", digest(key))[0] is False
        rows = read_streams(worker.path, active=True, run_attempt=0, stage="director")
        assert len(rows) == 1 and rows[0]["status"] == "interrupted" and rows[0]["retryable"]
        assert json.loads(rows[0]["text"]) == {"tasks": []}
        assert not list((worker.path / "streams").glob("*.completed.json"))
        assert not list((worker.path / "streams").glob("*.active.json"))
    finally:
        worker._client.client.close()


@pytest.mark.parametrize("preview_error", [OSError, ValueError])
def test_preview_completion_failure_cannot_discard_committed_model_result(tmp_path, monkeypatch, preview_error):
    worker, captured = real_worker(tmp_path)

    def fail_preview(self):
        self._close_log()
        raise preview_error("preview unavailable")

    monkeypatch.setattr(StreamJournal, "completed", fail_preview)
    key = ["durable-response-preview-failure", 0]
    try:
        value = worker._knowledge_model_turn(key, MESSAGES, TOOLS, {"max_output_tokens": 1024})
        assert json.loads(value["content"]) == {"tasks": []}
        assert worker._checkpoints.get("director", digest(key)) == (True, value)
        assert worker._knowledge_model_turn(key, MESSAGES, TOOLS, {"max_output_tokens": 1024}) == value
        assert len(captured) == 1
    finally:
        worker._client.client.close()


def test_interruption_preview_failure_does_not_replace_checkpoint_error(tmp_path, monkeypatch):
    worker, captured = real_worker(tmp_path)
    original = OSError("original checkpoint failure")

    def fail_checkpoint(*args, **kwargs):
        raise original

    def fail_preview(self):
        self._close_log()
        raise ValueError("preview interruption unavailable")

    monkeypatch.setattr(worker._checkpoints, "put", fail_checkpoint)
    monkeypatch.setattr(StreamJournal, "interrupted", fail_preview)
    try:
        with pytest.raises(OSError) as caught:
            worker._knowledge_model_turn(["both-writes-fail"], MESSAGES, TOOLS, {"max_output_tokens": 1024})
        assert caught.value is original
        assert len(captured) == 1
    finally:
        worker._client.client.close()
