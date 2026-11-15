"""Native-prefix repair reuses author bindings, usage and durable checkpoints offline."""
from copy import deepcopy
import hashlib

import pytest

from lib.contexts.workflows.infrastructure.native_feedback_adapter import NativeFeedbackAdapter
from test_native_feedback_adapter import ORIGINAL, runtime_for, session_for


PREFIX = "原始原生推理保持不变。\n\n不过，我需要重新核对已指出的条件。"
SUFFIX = "条件尚未确认，所以我保留这部分不确定性，再给出可执行方案。"
ANSWER = "可以先准备演示；是否能按计划完成，取决于尚待确认的条件。"
PENDING = [*ORIGINAL, {"role": "system", "content": "已有审查指出条件尚未确认；保持条件性。"}]


def prefix_runtime(tmp_path, *, missing_answer=False, truncated=False):
    runtime, clients, loaded, keys = runtime_for(tmp_path)
    for client in clients.values():
        client.base_url = "https://api.deepseek.com/v1"
        client.model_info["tools"] = False
        client.tool_turn = None

        def chat(messages, *, _client=client, **options):
            _client.calls.append((deepcopy(messages), {key: deepcopy(value)
                for key, value in options.items() if key not in {"on_stream", "response_details"}}))
            _client.usage.update(calls=_client.usage.get("calls", 0) + 1,
                prompt_tokens=_client.usage.get("prompt_tokens", 0) + 100,
                completion_tokens=_client.usage.get("completion_tokens", 0) + 20)
            details = {"api_format": "chat", "reasoning_content": SUFFIX,
                "reasoning_prefill": {"version": 3, "transport": "deepseek_chat_beta",
                    "visible_prefix": "", "continuation_mode": "reasoning",
                    "prefix_sha256": hashlib.sha256(options["reasoning_prefix"].encode()).hexdigest(),
                    "prefix_utf8_bytes": len(options["reasoning_prefix"].encode()),
                    "response_reasoning": "provider_continuation"}}
            options["on_stream"]({"type": "delta", "channel": "reasoning", "text": SUFFIX})
            if missing_answer or truncated:
                if truncated:
                    from lib.contexts.models.contracts import ModelOutputLimitError
                    error = ModelOutputLimitError()
                    details["incomplete_response"] = {"version": 1, "status": "truncated",
                        "finish_reason": "length", "content": "Partial answer.",
                        "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
                else:
                    error = ValueError("model_visible_output_missing")
                error.response_details = details
                raise error
            options["response_details"].update(details)
            options["on_stream"]({"type": "delta", "channel": "text", "text": ANSWER})
            return ANSWER

        client.chat = chat
    return runtime, clients, loaded, keys


@pytest.mark.parametrize("stage", ["sft", "multiturn", "cot"])
def test_native_prefix_uses_original_node_without_tools_and_replays_paid_response(tmp_path, stage):
    runtime, clients, loaded, keys = prefix_runtime(tmp_path)
    client = clients[stage]
    session = session_for(runtime, client, stage)
    before = deepcopy((session, PENDING))
    adapter = NativeFeedbackAdapter(runtime)
    first = adapter.continue_prefix(["candidate"], session, PENDING, reasoning_prefix=PREFIX)
    again = adapter.continue_prefix(["candidate"], session, PENDING, reasoning_prefix=PREFIX)
    assert first == again
    assert (session, PENDING) == before
    assert len(client.calls) == 1 and sum(len(c.calls) for c in clients.values()) == 1
    assert all(item == ("generation", stage) for item in loaded)
    assert runtime.stage == "review"
    sent, options = client.calls[0]
    assert sent == PENDING
    assert options == {"max_tokens": 33000, "temperature": 0.2, "retries": 1,
        "reasoning_prefix": PREFIX, "reasoning_prefix_mode": "reasoning", "allow_reasoning_fallback": False}
    assert first["request_messages"] == [*PENDING, {"role": "assistant", "content": "",
        "prefix": True, "reasoning_content": PREFIX}]
    assert first["assistant"] == {"role": "assistant", "content": ANSWER, "reasoning_content": SUFFIX}
    assert first["author_continuation"] == {"version": 1, "author": session["author"],
        "model_stage": stage, "message_prefix_preserved": True, "tools_preserved": True,
        "transport": "deepseek_chat_beta"}
    assert first["response_details"]["reasoning_prefill"]["continuation_mode"] == "reasoning"
    assert runtime.state["usage"]["generation"] == {"calls": 1, "prompt_tokens": 100, "completion_tokens": 20}
    assert "native_reasoning_prefix_v1" in keys[0]


@pytest.mark.parametrize("change", ["model", "binding"])
def test_native_prefix_rejects_changed_author_before_paid_call(tmp_path, change):
    runtime, clients, _, _ = prefix_runtime(tmp_path)
    client = clients["sft"]
    session = session_for(runtime, client, "sft")
    if change == "model":
        client.model = "different-author"
    else:
        runtime.recipe["node_models"]["sft"]["generation"]["max_output_tokens"] = 65536
    with pytest.raises(ValueError, match="^native_feedback_original_model_changed$"):
        NativeFeedbackAdapter(runtime).continue_prefix([], session, PENDING, reasoning_prefix=PREFIX)
    assert not client.calls


def test_native_prefix_checkpoints_reasoning_only_paid_failure_without_rebuying(tmp_path):
    runtime, clients, _, _ = prefix_runtime(tmp_path, missing_answer=True)
    client = clients["multiturn"]
    session = session_for(runtime, client, "multiturn")
    adapter = NativeFeedbackAdapter(runtime)
    first = adapter.continue_prefix(["candidate"], session, PENDING, reasoning_prefix=PREFIX)
    again = adapter.continue_prefix(["candidate"], session, PENDING, reasoning_prefix=PREFIX)
    assert first == again and len(client.calls) == 1
    assert first["assistant"] == {"role": "assistant", "content": "", "reasoning_content": SUFFIX}
    assert first["response_details"]["reasoning_content"] == SUFFIX
    assert first["response_details"]["reasoning_prefill"]["prefix_sha256"] == hashlib.sha256(PREFIX.encode()).hexdigest()
    assert runtime.state["usage"]["generation"] == {"calls": 1, "prompt_tokens": 100, "completion_tokens": 20}


def test_native_prefix_identity_changes_when_the_reasoning_prefix_changes(tmp_path):
    runtime, clients, _, keys = prefix_runtime(tmp_path)
    client = clients["cot"]
    session = session_for(runtime, client, "cot")
    adapter = NativeFeedbackAdapter(runtime)
    adapter.continue_prefix(["candidate"], session, PENDING, reasoning_prefix=PREFIX)
    changed = PREFIX + "\n我还需要核对另一处不确定性。"
    adapter.continue_prefix(["candidate"], session, PENDING, reasoning_prefix=changed)
    assert len(client.calls) == 2 and keys[0] != keys[1]
    assert [call[1]["reasoning_prefix"] for call in client.calls] == [PREFIX, changed]


def test_native_prefix_checkpoints_paid_truncation_without_rebuying(tmp_path):
    runtime, clients, _, _ = prefix_runtime(tmp_path, truncated=True)
    client = clients["multiturn"]
    session = session_for(runtime, client, "multiturn")
    adapter = NativeFeedbackAdapter(runtime)
    first = adapter.continue_prefix(["truncated"], session, PENDING, reasoning_prefix=PREFIX)
    again = adapter.continue_prefix(["truncated"], session, PENDING, reasoning_prefix=PREFIX)
    assert first == again and len(client.calls) == 1
    assert first["response_details"]["reasoning_content"] == SUFFIX
    assert first["response_details"]["incomplete_response"] == {"version": 1, "status": "truncated",
        "finish_reason": "length", "content": "Partial answer.",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    assert runtime.state["usage"]["generation"] == {"calls": 1, "prompt_tokens": 100, "completion_tokens": 20}


def test_answer_finalization_has_its_own_boundary_and_durable_identity(tmp_path):
    runtime, clients, _, keys = prefix_runtime(tmp_path)
    client = clients["multiturn"]
    session = session_for(runtime, client, "multiturn")
    adapter = NativeFeedbackAdapter(runtime)
    adapter.continue_prefix(["same"], session, PENDING, reasoning_prefix=PREFIX)
    final = adapter.finalize_prefix(["same"], session, PENDING, reasoning_prefix=PREFIX)
    again = adapter.finalize_prefix(["same"], session, PENDING, reasoning_prefix=PREFIX)
    assert final == again and len(client.calls) == 2
    assert keys[0] != keys[1] and keys[1] == keys[2]
    assert client.calls[1][1]["reasoning_prefix_mode"] == "answer"
    assert final["request_messages"][-1] == {"role": "assistant", "content": "\n",
        "prefix": True, "reasoning_content": PREFIX}
