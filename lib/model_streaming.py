"""Visible model deltas, with explicit completion checks for all three APIs."""
from __future__ import annotations

from contextlib import suppress
import math
import socket
import threading
from time import monotonic
from typing import Callable

from lib.model_response_details import (build_response_details, commit_response_details,
                                        extract_response_details)


StreamCallback = Callable[[dict], None]


class ModelStreamError(ValueError):
    """A static, safe failure code: partial text must never become a sample."""


class _StreamWatchdog:
    """One bounded observer; heartbeats cannot extend meaningful-output idle."""

    def __init__(self, stream, idle_seconds, cancel_check):
        self.stream, self.idle_seconds, self.cancel_check = stream, idle_seconds, cancel_check
        self.last_output = monotonic()
        self.error = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, name="model-stream-watchdog", daemon=True)
        # HTTP/1.x owns its active connection. Never shut down a shared HTTP/2
        # socket; the SDK's response close remains responsible for that stream.
        self.sock = None
        response = self.response = getattr(stream, "response", None)
        if response is not None and getattr(response, "http_version", "") in {"HTTP/1.0", "HTTP/1.1"}:
            with suppress(Exception):
                network = response.extensions.get("network_stream")
                self.sock = network.get_extra_info("socket") if network is not None else None

    def start(self):
        self.thread.start()

    def progress(self):
        with self.lock:
            self.last_output = monotonic()

    def raise_if_interrupted(self):
        with self.lock:
            error = self.error
        if error is not None:
            raise error from None

    def seal(self):
        # Freeze the watchdog before publishing a successfully exhausted stream.
        with self.lock:
            self.stop.set()
            error = self.error
        if error is not None:
            raise error from None

    def _watch(self):
        try:
            while not self.stop.is_set():
                error = None
                if self.cancel_check is not None:
                    try:
                        self.cancel_check()
                    except BaseException as cancelled:
                        error = cancelled
                with self.lock:
                    if self.stop.is_set():
                        break
                    if error is None and monotonic() - self.last_output >= self.idle_seconds:
                        error = ModelStreamError("model_stream_idle_timeout")
                    if error is not None:
                        self.error = error
                if error is not None:
                    if self.sock is not None and not getattr(self.response, "is_closed", False):
                        with suppress(Exception):
                            self.sock.shutdown(socket.SHUT_RDWR)
                    break
                if self.stop.wait(min(0.25, self.idle_seconds)):
                    break
        finally:
            # SDK close can itself block. Keep it on this one daemon, never
            # create replacement closers, and never wait without a bound.
            with suppress(Exception):
                self.stream.close()

    def finish(self):
        with self.lock:
            self.stop.set()
        self.thread.join(timeout=0.5)


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def consume_stream(stream, api_format: str, emit: StreamCallback, *,
                   allow_reasoning_fallback: bool = True,
                   response_details: dict | None = None,
                   cancel_check: Callable[[], None] | None = None,
                   idle_timeout_seconds: float | None = None) -> tuple[str, int, int]:
    """Consume SDK-decoded SSE events, closing the HTTP response on every exit.

    A balanced JSON prefix is insufficient evidence of a completed response.
    Callback failures (including worker cancellation) propagate unchanged.
    SDK/SSE heartbeats do not reset the output idle clock. The existing read
    idle limit remains the transport backstop; continuous output has no deadline.
    """
    if idle_timeout_seconds is None:
        from lib.llm_client import MODEL_READ_IDLE_TIMEOUT_SECONDS
        idle_timeout_seconds = MODEL_READ_IDLE_TIMEOUT_SECONDS
    if (type(idle_timeout_seconds) not in (int, float)
            or not math.isfinite(idle_timeout_seconds) or idle_timeout_seconds <= 0):
        raise ValueError("invalid_model_stream_idle_timeout")
    if response_details is not None:
        response_details.clear()
        allow_reasoning_fallback = False
    text, reasoning, summary = [], [], []
    completed_details = {}
    prompt_tokens = completion_tokens = 0
    terminal = False
    stopped = False
    watchdog = _StreamWatchdog(stream, idle_timeout_seconds, cancel_check)
    watchdog.start()
    try:
        iterator = iter(stream)
        while True:
            watchdog.raise_if_interrupted()
            try:
                event = next(iterator)
            except StopIteration:
                watchdog.raise_if_interrupted()
                break
            except ModelStreamError:
                watchdog.raise_if_interrupted()
                raise
            except Exception:
                watchdog.raise_if_interrupted()
                raise ModelStreamError("model_stream_interrupted") from None
            watchdog.raise_if_interrupted()
            kind = _get(event, "type", "")
            was_terminal = terminal
            delta_text = delta_reasoning = delta_summary = ""
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
                elif kind == "response.reasoning_text.delta":
                    delta_reasoning = _get(event, "delta", "")
                elif kind == "response.reasoning_summary_text.delta":
                    delta_summary = _get(event, "delta", "")
                elif kind == "response.completed":
                    response = _get(event, "response")
                    if _get(response, "status", "completed") != "completed":
                        raise ModelStreamError("model_stream_incomplete")
                    usage = _get(response, "usage")
                    prompt_tokens = _get(usage, "input_tokens", 0) or 0
                    completion_tokens = _get(usage, "output_tokens", 0) or 0
                    if response_details is not None:
                        completed_details = extract_response_details(response, api_format)
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
                    elif _get(block, "type") == "thinking":
                        delta_reasoning = _get(block, "thinking", "")
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
                                           ("reasoning", delta_reasoning, reasoning),
                                           ("reasoning", delta_summary, summary)):
                if not isinstance(delta, str):
                    raise ModelStreamError("model_stream_invalid_event")
                if delta:
                    if was_terminal:
                        raise ModelStreamError("model_stream_invalid_event")
                    parts.append(delta)
                    watchdog.progress()
                    emit({"type": "delta", "channel": channel, "text": delta})
        watchdog.raise_if_interrupted()
        if not terminal:
            raise ModelStreamError("model_stream_incomplete")
        watchdog.seal()
        emit({"type": "response_completed", "prompt_tokens": prompt_tokens,
              "completion_tokens": completion_tokens})
        content = "".join(text).strip()
        if not content and api_format == "chat" and allow_reasoning_fallback:
            content = "".join(reasoning).strip()
        commit_response_details(response_details, build_response_details(api_format,
            reasoning_content="".join(reasoning) or completed_details.get("reasoning_content", ""),
            reasoning_summary="".join(summary) or completed_details.get("reasoning_summary", "")))
        return content, prompt_tokens, completion_tokens
    finally:
        watchdog.finish()
