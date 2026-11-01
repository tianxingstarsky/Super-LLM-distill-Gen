"""Full SDK request evidence and single-purchase behavior, with no network."""
from copy import deepcopy
from hashlib import sha256
import json

import httpx
from openai import OpenAI, APIStatusError
import pytest

from scripts.diagnostics.run_clean_context_probe import read, run_case


def body():
    return {"model": "deepseek-flash", "messages": [{"role": "user", "content": "Draft a classroom demo."}],
            "max_tokens": 32768, "reasoning_effort": "low", "temperature": 0.7,
            "thinking": {"type": "enabled"}}


@pytest.mark.parametrize("initial_system", [None, "初始规则：引用当前配置时使用 [[SJ_REASONING_EFFORT]]，保留完整推理。"])
def test_real_sdk_body_and_native_response_are_preserved_without_second_purchase(tmp_path, initial_system):
    received = []
    request_body = body()
    if initial_system is not None:
        request_body["messages"].insert(0, {"role": "system", "content": initial_system})
    raw_response = {"id": "offline-completion", "model": "deepseek-flash", "choices": [{"finish_reason": "stop",
                    "message": {"role": "assistant", "content": "A conditional plan.",
                                "reasoning_content": "Original native text stays unchanged."}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21}}

    def respond(request):
        received.append(request.content)
        return httpx.Response(200, json=raw_response)

    with OpenAI(api_key="offline-only", base_url="https://api.deepseek.com/v1", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(respond))) as sdk:
        result = run_case(sdk, request_body, tmp_path / "01", "01", network_route="offline", initial_system=initial_system)
        assert result["raw_response"] == raw_response
        capture = read(tmp_path / "01" / result["request_capture"])
        assert capture["json_body"] == request_body
        assert capture["body_utf8"].encode() == received[0]
        assert capture["body_sha256"] == sha256(received[0]).hexdigest()
        assert "thinking" in capture["json_body"] and "extra_body" not in capture["json_body"]
        assert "reasoning_effort" in capture["json_body"]
        assert "offline-only" not in json.dumps(capture)
        assert run_case(sdk, request_body, tmp_path / "01", "01", network_route="offline", initial_system=initial_system) == result
        assert len(received) == 1
        polluted = deepcopy(request_body)
        polluted["messages"][-1]["reasoning_content"] = "Old native reasoning"
        with pytest.raises(ValueError, match="unexpected_public_message_fields"):
            run_case(sdk, polluted, tmp_path / "polluted", "01", network_route="offline", initial_system=initial_system)
        if initial_system is not None:
            with pytest.raises(ValueError, match="unexpected_initial_system"):
                run_case(sdk, request_body, tmp_path / "changed", "01", network_route="offline", initial_system="Changed rules")
        assert len(received) == 1


def test_failed_request_is_recorded_but_never_automatically_repurchased(tmp_path):
    requests = []

    def fail(request):
        requests.append(request.content)
        return httpx.Response(503, json={"error": {"message": "Offline transport failure"}})

    with OpenAI(api_key="offline-only", base_url="https://api.deepseek.com/v1", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(fail))) as sdk:
        with pytest.raises(APIStatusError):
            run_case(sdk, body(), tmp_path / "01", "01", network_route="offline")
        assert read(tmp_path / "01/error.json")["automatic_retry"] is False
        assert len(list((tmp_path / "01/http").glob("*.json"))) == 1
        with pytest.raises(RuntimeError, match="prior_attempt_unresolved_do_not_repurchase"):
            run_case(sdk, body(), tmp_path / "01", "01", network_route="offline")
        assert len(requests) == 1
