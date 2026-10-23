"""Node requests retain durable calls and exact response evidence behind a port."""
from copy import deepcopy
import pytest

from lib.contexts.workflows.application.request_node_model import RequestNodeModel


class MemoryRequestPort:
    stage = "cot"

    def __init__(self, *, cached=None, failure=None):
        self.recipe = {"review_repair": {}, "provider_reasoning_capture_version": 1}
        self.cached = cached
        self.failure = failure
        self.requests = []
        self.finishes = []
        self.keys = []

    def checkpoint(self, key, action):
        self.keys.append(key)
        if self.cached is not None:
            return deepcopy(self.cached)
        return action()

    def invoke(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return {"answer": "unchanged", "reasoning_content": "full reasoning"}

    def finish_stream(self, *, interrupted):
        self.finishes.append(interrupted)

    def captured_result(self, value):
        return value["data"], value["response_details"]


def test_node_request_reuses_captured_checkpoint_without_new_model_call():
    cached = {"model_json_response_version": 1,
              "data": {"answer": "retained", "reasoning_content": "retained reasoning"},
              "response_details": {"checkpoint": {"stage": "cot", "sha256": "saved"}}}
    port = MemoryRequestPort(cached=cached)
    result = RequestNodeModel(port).execute(["source", "cot"], "generation", "workflow.cot_generate", {},
                                            capture_response_details=True)
    assert result == (cached["data"], cached["response_details"])
    assert port.keys == [["call", ["source", "cot"]]]
    assert not port.requests
    assert port.finishes == [False]


def test_node_request_routes_generation_check_but_keeps_review_role():
    port = MemoryRequestPort()
    use_case = RequestNodeModel(port)
    assert use_case.execute("id", "jev", "workflow.style_check", {})["reasoning_content"] == "full reasoning"
    use_case.execute("id2", "jev", "workflow.style_check", {}, model_stage="review")
    assert [request.role for request in port.requests] == ["generation", "jev"]


@pytest.mark.parametrize("options,reason", [
    ({"image": [""]}, "invalid_document_images"),
    ({"image": ["image"] * 9}, "invalid_document_images"),
    ({"native_request_attempts": 2}, "invalid_model_request_attempts"),
])
def test_invalid_request_never_enters_checkpoint_or_client(options, reason):
    port = MemoryRequestPort()
    with pytest.raises(ValueError, match=reason):
        RequestNodeModel(port).execute("id", "generation", "workflow.sft_answer", {}, **options)
    assert port.keys == port.requests == port.finishes == []


def test_node_request_interruption_preserves_original_failure():
    failure = KeyboardInterrupt()
    port = MemoryRequestPort(failure=failure)
    with pytest.raises(KeyboardInterrupt) as caught:
        RequestNodeModel(port).execute("id", "generation", "workflow.sft_answer", {})
    assert caught.value is failure
    assert port.finishes == [True]


def test_capture_checkpoint_is_not_silently_treated_as_plain_response():
    port = MemoryRequestPort(cached={"answer": "old"})
    with pytest.raises(ValueError, match="checkpoint_integrity_error"):
        RequestNodeModel(port).execute("id", "generation", "workflow.sft_answer", {},
                                      capture_response_details=True)
    assert not port.requests
