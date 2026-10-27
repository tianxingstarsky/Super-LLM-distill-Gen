"""Bounded text request execution independent of SDK, clocks and storage."""
from __future__ import annotations
from .retry_port import RetryDirective, RetryPort
from contextlib import ExitStack
from typing import Any, Dict, List
from .request_ports import ModelRequestPort, StreamCallback
from ..domain.request_errors import BudgetExceeded, ModelQueueTimeout, ModelRequestError, ModelStreamError


class ExecuteModelRequest:
    def __init__(self, port: ModelRequestPort, *, retry: RetryPort):
        self.port = port
        self.retry = retry

    def execute(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int | None = 1024,
        temperature: float = 0.7,
        retries: int = 3,
        thinking: bool | None = None,
        json_mode: bool = False,
        on_stream: StreamCallback | None = None,
        allow_reasoning_fallback: bool = True,
        response_details: dict | None = None,
        reasoning_prefix: str | None = None,
    ) -> str:
        """Admit, execute and settle one text request under bounded retry rules."""
        port = self.port
        if response_details is not None:
            response_details.clear()
            allow_reasoning_fallback = False
        if reasoning_prefix is not None:
            allow_reasoning_fallback = False
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
            raise ValueError("max_tokens must be a positive integer")
        if type(retries) is not int or not 1 <= retries <= 10:
            raise ValueError("invalid_model_request_attempts")
        request_max_tokens, reservation_usd = port.prepare_request(
            messages, max_tokens, reasoning_prefix=reasoning_prefix, thinking=thinking)
        last_err: Exception | None = None
        callback_error = None
        cancel_error = None
        def check_cancel():
            nonlocal cancel_error
            if port.cancel_check:
                try:
                    port.cancel_check()
                except BaseException as error:
                    cancel_error = error
                    raise
        def notify(event):
            nonlocal callback_error
            try:
                on_stream(event)
            except Exception as error:
                callback_error = error
                raise
        def attempt_request(attempt):
            nonlocal last_err
            attempt_details = {}
            reservations = []
            admission = ExitStack()
            request_started = False
            definite_rejection = False
            failure = None
            delay = None
            try:
                check_cancel()
                admission.enter_context(port.admit(check_cancel,
                    (lambda info: notify({"type": "queued", **info})) if on_stream else None))
                check_cancel()
                for guard in port.budget_guards():
                    if reservation_usd:
                        reservations.append((guard, guard.reserve(reservation_usd)))
                    else:
                        guard.check()
                if on_stream:
                    notify({"type": "start", "api_format": port.api_format})
                request_started = True
                content, prompt_tokens, completion_tokens = port.request(
                    messages, max_tokens=request_max_tokens, temperature=temperature,
                    thinking=thinking, json_mode=json_mode,
                    **({"allow_reasoning_fallback": False} if not allow_reasoning_fallback else {}),
                    **({"response_details": attempt_details} if response_details is not None else {}),
                    **({"reasoning_prefix": reasoning_prefix} if reasoning_prefix is not None else {}),
                    **({"on_stream": notify} if on_stream else {}),
                    **({"cancel_check": check_cancel} if port.cancel_check else {}))
                port.account_usage(prompt_tokens, completion_tokens, reservations, reservation_usd)
                if content:
                    port.commit_details(response_details, attempt_details)
                    return content
                if not allow_reasoning_fallback:
                    raise ValueError("model_visible_output_missing")
                last_err = ValueError("empty completion")
            except BudgetExceeded:
                raise
            except ValueError as error:
                if (error is callback_error or error is cancel_error or not isinstance(error, ModelStreamError)
                        or str(error) not in {"model_stream_idle_timeout", "model_stream_interrupted"}):
                    raise
                # A transport read interruption/idle timeout is not a bad
                # training sample. Unlike malformed/incomplete output, retry
                # it within the existing admission/budget/attempt bounds.
                last_err = error
                failure = port.classify_error(error)
                delay = port.retry_delay(error, attempt)
            except Exception as e:  # noqa: BLE001
                if e is callback_error or e is cancel_error:
                    raise
                if isinstance(e, ModelQueueTimeout):
                    raise ModelRequestError(e.code, e.kind, attempt) from None
                definite_rejection = port.rejected_before_generation(e)
                # 分层降级 L1→L3：json_mode 被 API 拒绝（不支持 response_format）
                # → 记录能力探测结果，同次循环降级重试（不消耗用户配置的重试次数）
                if (reasoning_prefix is None and port.api_format != "anthropic" and json_mode
                        and port.json_supported is not False and port.is_format_unsupported(e)):
                    port.json_supported = False
                    return RetryDirective(error=e, counted=False)
                last_err = e
                failure = port.classify_error(e)
                if failure["kind"] == "transient":
                    delay = port.retry_delay(e, attempt)
                    if failure["code"] in {"service_rate_limited", "service_unavailable"}:
                        # Publish the shared cooldown before releasing this slot.
                        port.defer(delay)
            finally:
                try:
                    if reservations:
                        # A timeout/error may still have incurred provider charges.
                        # Pessimistically book the reserved maximum before retrying.
                        port.settle_all(reservations, reservation_usd
                                         if request_started and not definite_rejection else 0.0)
                finally:
                    admission.close()
            if failure is None:
                failure = {"kind": "invalid", "code": "model_visible_output_missing"}
            retryable = failure["kind"] == "transient"
            if retryable and delay is None:
                delay = port.retry_delay(last_err, attempt)
            return RetryDirective(ModelRequestError(failure["code"], failure["kind"], attempt + 1,
                diagnostics=getattr(last_err, "request_diagnostics", None)), retryable=retryable,
                delay=0.0 if failure["code"] in {"service_rate_limited", "service_unavailable"} else (delay or 0.0))

        return self.retry.run(attempt_request, attempts=retries,
                              wait=lambda delay: port.wait_retry(delay, check_cancel) if delay else None)
