"""Visible model deltas, with explicit completion checks for all three APIs."""
from __future__ import annotations

from contextlib import suppress
from typing import Callable


StreamCallback = Callable[[dict], None]


class ModelStreamError(ValueError):
    """A static, safe failure code: partial text must never become a sample."""


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def consume_stream(stream, api_format: str, emit: StreamCallback, *,
                   allow_reasoning_fallback: bool = True) -> tuple[str, int, int]:
    """Consume SDK-decoded SSE events, closing the HTTP response on every exit.

    A balanced JSON prefix is insufficient evidence of a completed response.
    Callback failures (including worker cancellation) propagate unchanged.
    """
    text, reasoning = [], []
    prompt_tokens = completion_tokens = 0
    terminal = False
    stopped = False
    iterator = iter(stream)
    try:
        while True:
            try:
                event = next(iterator)
            except StopIteration:
                break
            except Exception:
                raise ModelStreamError("model_stream_interrupted") from None
            kind = _get(event, "type", "")
            was_terminal = terminal
            delta_text = delta_reasoning = ""
            if api_format == "chat":
                usage = _get(event, "usage")
                if usage is not None:
                    prompt_tokens = _get(usage, "prompt_tokens", 0) or 0
                    completion_tokens = _get(usage, "completion_tokens", 0) or 0
                for choice in _get(event, "choices", []) or []:
                    if _get(choice, "index", 0) != 0:
                        continue
                    delta = _get(choice, "delta")
                    delta_text = _get(delta, "content", "") or ""
                    delta_reasoning = _get(delta, "reasoning_content", "") or ""
                    finish = _get(choice, "finish_reason")
                    if finish is not None:
                        if finish != "stop":
                            raise ModelStreamError("model_stream_incomplete")
                        terminal = True
            elif api_format == "responses":
                if kind == "response.output_text.delta":
                    delta_text = _get(event, "delta", "")
                elif kind in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                    delta_reasoning = _get(event, "delta", "")
                elif kind == "response.completed":
                    response = _get(event, "response")
                    if _get(response, "status", "completed") != "completed":
                        raise ModelStreamError("model_stream_incomplete")
                    usage = _get(response, "usage")
                    prompt_tokens = _get(usage, "input_tokens", 0) or 0
                    completion_tokens = _get(usage, "output_tokens", 0) or 0
                    terminal = True
                elif kind in {"response.incomplete", "response.failed", "error"}:
                    raise ModelStreamError("model_stream_incomplete")
            elif api_format == "anthropic":
                if kind == "message_start":
                    usage = _get(_get(event, "message"), "usage")
                    prompt_tokens = _get(usage, "input_tokens", 0) or 0
                elif kind == "content_block_start":
                    block = _get(event, "content_block")
                    if _get(block, "type") == "text":
                        delta_text = _get(block, "text", "")
                elif kind == "content_block_delta":
                    delta = _get(event, "delta")
                    if _get(delta, "type") == "text_delta":
                        delta_text = _get(delta, "text", "")
                    elif _get(delta, "type") == "thinking_delta":
                        delta_reasoning = _get(delta, "thinking", "")
                elif kind == "message_delta":
                    reason = _get(_get(event, "delta"), "stop_reason")
                    if reason is not None:
                        if reason not in {"end_turn", "stop_sequence"}:
                            raise ModelStreamError("model_stream_incomplete")
                        stopped = True
                    completion_tokens = _get(_get(event, "usage"), "output_tokens", 0) or 0
                elif kind == "message_stop":
                    terminal = stopped
                elif kind == "error":
                    raise ModelStreamError("model_stream_incomplete")
            for channel, delta, parts in (("text", delta_text, text),
                                           ("reasoning", delta_reasoning, reasoning)):
                if not isinstance(delta, str):
                    raise ModelStreamError("model_stream_invalid_event")
                if delta:
                    if was_terminal:
                        raise ModelStreamError("model_stream_invalid_event")
                    parts.append(delta)
                    emit({"type": "delta", "channel": channel, "text": delta})
        if not terminal:
            raise ModelStreamError("model_stream_incomplete")
        emit({"type": "response_completed", "prompt_tokens": prompt_tokens,
              "completion_tokens": completion_tokens})
        content = "".join(text).strip()
        if not content and api_format == "chat" and allow_reasoning_fallback:
            content = "".join(reasoning).strip()
        return content, prompt_tokens, completion_tokens
    finally:
        with suppress(Exception):
            stream.close()
