"""One strict review submission, without model-generated JSON envelopes."""
import json
from types import SimpleNamespace

import pytest

from lib.contexts.models.contracts import ModelJSONError
from lib.contexts.review.infrastructure.review_evolution_request import request_review_evolution
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.prompts import get, render


def _quality():
    return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
        "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
        "reason": "The answer respects the visible evidence."}


def test_review_and_evolution_share_one_strict_tool_turn(monkeypatch):
    from lib.bootstrap import review_evolution
    calls = []
    expected = {"verdict": _quality(), "task_notes": [],
        "memory_proposal": {"status": "no_update", "memory_items": []}}
    raw = {**{key: value for key, value in _quality().items() if key != "scores"},
        **_quality()["scores"], "task_notes": [], "memory_status": "no_update", "memory_items": []}

    def submit(*args, **kwargs):
        calls.append((args, kwargs))
        return {"assistant": {"role": "assistant", "content": "", "tool_calls": [{"id": "review-1",
            "type": "function", "function": {"name": "submit_review_evolution", "arguments": json.dumps(raw)}}]},
            "response_details": {"receipt": "actual-tool-turn"}}

    monkeypatch.setattr(review_evolution, "review_evolution_tool_transport",
        lambda runtime: SimpleNamespace(prepared_tool_turn=submit))
    runtime = SimpleNamespace(prompt_text=lambda *args, **kwargs: "You are the reviewer.")
    parsed, original, details = request_review_evolution(runtime, ["review"], "generation", {"candidate": "draft"})
    assert parsed == expected
    assert original == raw
    assert details == {"receipt": "actual-tool-turn"}
    assert len(calls) == 1
    args, options = calls[0]
    assert options == {"model_stage": "review", "strict": True, "capture_details": True}
    assert [message["role"] for message in args[2]] == ["system", "user"]
    schema = args[3][0]["function"]["parameters"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["memory_items"]["items"]["type"] == "object"
    assert not {"verdict", "scores", "memory_proposal"}.intersection(schema["properties"])
    def supported_subset(value):
        if isinstance(value, dict):
            assert not {"minLength", "maxLength", "minItems", "maxItems"}.intersection(value)
            for item in value.values():
                supported_subset(item)
        elif isinstance(value, list):
            for item in value:
                supported_subset(item)
    supported_subset(schema)


def test_invalid_review_submission_is_not_retried(monkeypatch):
    from lib.bootstrap import review_evolution
    calls = []

    def submit(*args, **kwargs):
        calls.append(args)
        return {"assistant": {"role": "assistant", "content": "unfinished prose", "tool_calls": []}}

    monkeypatch.setattr(review_evolution, "review_evolution_tool_transport",
        lambda runtime: SimpleNamespace(prepared_tool_turn=submit))
    runtime = SimpleNamespace(prompt_text=lambda *args, **kwargs: "You are the reviewer.")
    with pytest.raises(ModelJSONError) as caught:
        request_review_evolution(runtime, ["review"], "generation", {"candidate": "draft"})
    assert caught.value.attempts == 1
    assert len(calls) == 1


def test_new_review_protocol_does_not_upgrade_unflagged_frozen_templates():
    legacy = snapshot_node_prompts(None, recipe_version=42, review_advisory_policy_version=1)
    current = snapshot_node_prompts(None, recipe_version=42, review_advisory_policy_version=1, native_sft_review_evolution_version=1)
    assert legacy["review"]["workflow.review_score"] == render(get("workflow.review_score", version="1.28.0"))
    assert current["review"]["workflow.review_score"] == render(get("workflow.review_score", version="1.29.0"))


def test_bad_provider_tool_arguments_keep_original_failure_details(monkeypatch):
    from lib.bootstrap import review_evolution
    calls = []
    raw = {"incomplete_response": {"status": "incomplete", "details": {
        "strict_tool_response": {"tool_calls": [{"arguments": "{bad arguments}"}]}}}}

    def submit(*args, **kwargs):
        calls.append(args)
        error = ValueError("invalid_model_tool_arguments")
        error.response_details = raw
        raise error

    monkeypatch.setattr(review_evolution, "review_evolution_tool_transport",
        lambda runtime: SimpleNamespace(prepared_tool_turn=submit))
    runtime = SimpleNamespace(prompt_text=lambda *args, **kwargs: "You are the reviewer.")
    with pytest.raises(ModelJSONError) as caught:
        request_review_evolution(runtime, ["review"], "generation", {"candidate": "draft"})
    assert caught.value.response_details == raw
    assert caught.value.attempts == len(calls) == 1
