"""Strict Chat tool transport, assembled by the OpenAI SDK, never by JSON repair."""
from copy import deepcopy
from urllib.parse import urlsplit

from openai.lib.streaming.chat import ChatCompletionStreamState


def request_strict_tool_stream(client, kwargs, *, on_stream=None):
    # Scope the official beta route to this request; never mutate a shared
    # client, loosen strict mode, or forward credentials to another host.
    sdk = client.client
    endpoint = urlsplit(client.base_url)
    if endpoint.hostname == "api.deepseek.com":
        if endpoint.scheme != "https" or endpoint.path.rstrip("/") not in ("", "/v1", "/beta"):
            raise ValueError("model_strict_tools_endpoint_unsupported")
        sdk = sdk.with_options(base_url="https://api.deepseek.com/beta")
    wire = {**deepcopy(kwargs), "stream": True, "stream_options": {"include_usage": True}}
    # No response_format or prose JSON parsing. The provider constrains the
    # tool arguments. SDK accumulation retains unknown reasoning_content too.
    state = ChatCompletionStreamState()
    stream = sdk.chat.completions.create(**wire)
    seen = False
    incomplete = False
    try:
        iterator = iter(stream)
        while True:
            if client.cancel_check:
                client.cancel_check()
            try:
                chunk = next(iterator)
            except StopIteration:
                break
            except Exception:
                if not seen:
                    raise
                incomplete = True
                break
            seen = True
            state.handle_chunk(chunk)
            if on_stream:
                for choice in chunk.choices:
                    delta = choice.delta
                    for channel, text in (("reasoning", getattr(delta, "reasoning_content", None)),
                                          ("text", delta.content)):
                        if text:
                            on_stream({"type": "delta", "channel": channel, "text": text, "delivery": "stream"})
                    for call in delta.tool_calls or []:
                        if call.function and call.function.arguments:
                            on_stream({"type": "delta", "channel": "text", "text": call.function.arguments,
                                       "tool_index": call.index, "delivery": "stream", "tool_arguments": True})
    finally:
        stream.close()
    response = state.current_completion_snapshot.model_dump() if seen else {"choices": [], "usage": {}}
    if incomplete or not seen or not response.get("choices") or any(not c.get("finish_reason") for c in response["choices"]):
        response["stream_incomplete"] = True
    return response
