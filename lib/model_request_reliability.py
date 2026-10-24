"""Safe request failure categories and bounded, service-local retry pacing.

Provider text and response bodies never leave this module. Direct clients have
an in-process cooldown; configured clients use durable shared model admission.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import math
import random
import re
import threading
import time

from lib.contexts.models.domain.request_errors import ModelOutputLimitError


MAX_RETRY_DELAY_SECONDS = 60.0
MAX_SHARED_SERVICES = 256
FATAL_VALIDATION_CODES = frozenset({
    "checkpoint_integrity_error", "production_integrity_error", "production_review_integrity_error",
    "production_round_integrity_error",
    "artifact_integrity_error", "artifact_inventory_mismatch", "incomplete_artifact_manifest",
    "web_research_integrity_error", "package_review_checkpoint_mismatch",
    "workflow_endpoint_pin_invalid", "workflow_endpoint_changed_create_new_run",
    "model_configuration_changed_create_new_run", "source_snapshot_changed",
    "recipe_changed_create_new_run", "prompts_changed_create_new_run", "invalid_node_prompt_snapshot",
    "workflow_node_service_not_configured", "workflow_endpoint_url_or_protocol_invalid",
    "workflow_endpoint_models_invalid", "workflow_endpoint_credential_ref_invalid",
    "invalid_budget_state", "invalid_budget_price", "invalid_model_token_price",
    "model_budget_prices_required", "invalid_budget_limit", "invalid_budget_reservation",
    "invalid_budget_settlement", "budget_multimodal_context_required",
    "max_output_tokens_exceeds_context_window", "model_context_window_exceeded",
    "document_model_vision_not_confirmed", "invalid_api_format", "invalid_model_request_attempts",
    "invalid_model_scheduling", "invalid_model_max_concurrency", "invalid_model_queue_timeout",
    "invalid_model_scheduler_identity", "invalid_model_scheduler_state",
    "invalid_model_reasoning", "invalid_model_reasoning_effort", "invalid_model_reasoning_budget",
    "invalid_model_network_route",
    "model_reasoning_budget_exceeds_output", "model_reasoning_effort_unsupported",
    "model_reasoning_disable_unsupported", "model_reasoning_budget_unsupported",
    "model_reasoning_effort_required", "model_reasoning_format_unknown",
    "model_reasoning_parameter_unsupported",
    "model_reasoning_prefill_unsupported", "model_reasoning_prefill_thinking_disabled",
    "model_reasoning_prefill_explicit_required", "model_reasoning_prefill_transport_unsupported",
    "model_reasoning_prefill_rejected", "invalid_model_reasoning_prefix",
    "invalid_model_reasoning_prefill_messages", "native_prefill_invalid_answer",
    "native_prefill_receipt_missing",
    "model_reasoning_prefill_tools_unsupported",
})


from lib.contexts.models.domain.request_errors import ModelRequestError


from lib.contexts.models.domain.request_errors import ModelJSONError


def _status(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    if value is None:
        value = getattr(getattr(error, "response", None), "status_code", None)
    return value if type(value) is int else None


_DIAGNOSTIC_ERROR_TYPES = frozenset({
    "APIError", "APIConnectionError", "APITimeoutError", "APIStatusError", "AuthenticationError",
    "BadRequestError", "PermissionDeniedError", "NotFoundError", "RateLimitError",
    "InternalServerError", "UnprocessableEntityError", "ConflictError",
    "TimeoutError", "TimeoutException", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
    "ConnectionError", "NetworkError", "ConnectError", "ReadError", "WriteError", "CloseError",
    "ProtocolError", "LocalProtocolError", "RemoteProtocolError", "ProxyError",
    "OSError", "SSLError", "SSLCertVerificationError", "CertificateError", "gaierror",
    "ConnectionResetError", "ConnectionAbortedError", "BrokenPipeError", "ModelStreamError",
    "ModelRequestError", "ValueError", "RuntimeError",
    "StreamClosed", "StreamConsumed", "ResponseNotRead", "RequestNotRead",
})
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_PROVIDER_CODE_FAILURES = {
    **{code: ("fatal", "service_quota_exhausted") for code in (
        "insufficient_quota", "billing_hard_limit_reached", "credit_balance_too_low", "insufficient_balance")},
    **{code: ("fatal", "service_authentication_failed") for code in (
        "authentication_error", "invalid_api_key")},
    **{code: ("fatal", "service_permission_denied") for code in (
        "permission_error", "permission_denied")},
    "model_not_found": ("fatal", "service_model_or_endpoint_missing"),
    **{code: ("fatal", "service_request_rejected") for code in (
        "invalid_request_error", "invalid_request", "invalid_parameter", "invalid_parameter_error",
        "unsupported_parameter", "context_length_exceeded", "max_tokens_exceeded", "max_output_tokens_exceeded")},
    **{code: ("transient", "service_rate_limited") for code in (
        "rate_limit_exceeded", "rate_limit_error")},
    **{code: ("transient", "service_unavailable") for code in (
        "server_error", "internal_server_error", "internal_error", "service_unavailable", "overloaded_error")},
    **{code: ("transient", "service_timeout") for code in ("request_timeout", "timeout_error")},
}


def _exception_chain(error):
    seen, current = set(), error
    while isinstance(current, BaseException) and id(current) not in seen and len(seen) < 6:
        seen.add(id(current))
        yield current
        current = current.__cause__ if current.__cause__ is not None else current.__context__


def _provider_code(error):
    """Read only exact known structured enum values, never provider messages."""
    candidates = [getattr(error, "code", None), getattr(error, "type", None)]
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        details = body.get("error", body)
        if isinstance(details, dict):
            candidates.extend((details.get("code"), details.get("type")))
    return next((value for value in candidates if isinstance(value, str) and value in _PROVIDER_CODE_FAILURES), None)


def safe_request_diagnostic(error: Exception, *, phase: str, elapsed_seconds: float,
                            headers_received: bool = False, response=None,
                            headers_elapsed_seconds: float | None = None,
                            first_output_elapsed_seconds: float | None = None) -> dict:
    """Only structural transport facts; never exception text, URLs or full headers.

    SDK return time is an upper bound for response-header arrival, not a socket
    first-byte measurement. A missing duration means it was not observed.
    """
    def duration(value):
        return round(value, 6) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    errors = list(_exception_chain(error))
    chain = [type(item).__name__ if type(item).__name__ in _DIAGNOSTIC_ERROR_TYPES else "OtherError"
             for item in errors]
    response = response if response is not None else getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if type(status) is not int:
        status = _status(error)
    result = {"phase": phase if phase in {"headers", "stream", "response"} else "unknown",
              "exception_types": chain, "elapsed_seconds": duration(elapsed_seconds),
              "headers_received": headers_received is True or response is not None,
              "headers_elapsed_seconds": duration(headers_elapsed_seconds),
              "first_output_elapsed_seconds": duration(first_output_elapsed_seconds)}
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    provider_code = next((code for item in errors if (code := _provider_code(item)) is not None), None)
    if provider_code is not None:
        result["provider_error_code"] = provider_code
    headers = getattr(response, "headers", None)
    if hasattr(headers, "get"):
        for name in ("x-request-id", "request-id", "x-ds-request-id"):
            value = headers.get(name)
            if isinstance(value, str) and _REQUEST_ID.fullmatch(value):
                result["provider_request_id"] = value
                break
    return result


def filter_request_diagnostic(value) -> dict | None:
    """Validate a plain JSON diagnostic before persisting a caller's error.

    Exception attributes are not a trusted serialization boundary. No nested
    payload, extra field, custom object or arbitrary provider enum is copied.
    """
    if type(value) is not dict:
        return None
    phase, chain = value.get("phase"), value.get("exception_types")
    if (type(phase) is not str or phase not in {"headers", "stream", "response", "unknown"}
            or type(chain) is not list or not 1 <= len(chain) <= 6
            or any(type(item) is not str or item not in _DIAGNOSTIC_ERROR_TYPES | {"OtherError"}
                   for item in chain)):
        return None
    result = {"phase": phase, "exception_types": list(chain)}
    if type(value.get("headers_received")) is bool:
        result["headers_received"] = value["headers_received"]
    for name in ("elapsed_seconds", "headers_elapsed_seconds", "first_output_elapsed_seconds"):
        number = value.get(name)
        if number is None:
            result[name] = None
        elif type(number) in (int, float) and 0 <= number <= 31_536_000 and math.isfinite(number):
            result[name] = round(number, 6)
    status = value.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    provider_code = value.get("provider_error_code")
    if type(provider_code) is str and provider_code in _PROVIDER_CODE_FAILURES:
        result["provider_error_code"] = provider_code
    request_id = value.get("provider_request_id")
    if type(request_id) is str and _REQUEST_ID.fullmatch(request_id):
        result["provider_request_id"] = request_id
    return result


class RequestDiagnostics:
    """Per SDK dispatch observation; it changes no request or retry policy."""
    def __init__(self):
        self.started_at = time.monotonic()
        self.response = None
        self.headers_elapsed = self.first_output_elapsed = None

    def start(self):
        self.started_at = time.monotonic()
        self.response = None
        self.headers_elapsed = self.first_output_elapsed = None

    def received(self, response, *, streaming: bool):
        self.response = getattr(response, "response", None) if streaming else None
        # Full SDK responses do not expose header timing independently of the
        # body. Do not label their complete-response latency as header latency.
        if streaming:
            self.headers_elapsed = time.monotonic() - self.started_at

    def output(self, event):
        if event.get("type") == "delta" and event.get("text") and self.first_output_elapsed is None:
            self.first_output_elapsed = time.monotonic() - self.started_at

    def failure(self, error: Exception, phase: str):
        diagnostic = safe_request_diagnostic(error, phase=phase,
            elapsed_seconds=time.monotonic() - self.started_at,
            headers_received=self.headers_elapsed is not None or phase in {"stream", "response"},
            response=self.response, headers_elapsed_seconds=self.headers_elapsed,
            first_output_elapsed_seconds=self.first_output_elapsed)
        # Built-in/provider exception objects normally allow attributes. A
        # restrictive third-party exception must still propagate unchanged.
        try:
            error.request_diagnostics = diagnostic
        except (AttributeError, TypeError):
            pass
        return diagnostic


def classify_request_error(error: Exception) -> dict[str, str]:
    """Classify from types/status/known codes, never interpolate provider text."""
    if isinstance(error, ModelOutputLimitError):
        return {"kind": "invalid", "code": "model_output_limit"}
    if isinstance(error, (ModelRequestError, ModelJSONError)):
        return {"kind": error.kind, "code": error.code}
    if type(error).__name__ == "ModelQueueTimeout":
        return {"kind": "transient", "code": "model_request_queue_timeout"}
    if type(error).__name__ == "BudgetExceeded":
        return {"kind": "fatal", "code": "budget_exhausted"}
    status = _status(error)
    # Quota depletion will not be cured by rapidly retrying a 429. Read only
    # whitelisted structured codes; all other provider fields are ignored.
    code = _provider_code(error)
    if code is not None and _PROVIDER_CODE_FAILURES[code][1] == "service_quota_exhausted":
        return {"kind": "fatal", "code": "service_quota_exhausted"}
    if status == 401:
        return {"kind": "fatal", "code": "service_authentication_failed"}
    if status == 402:
        return {"kind": "fatal", "code": "service_quota_exhausted"}
    if status == 403:
        return {"kind": "fatal", "code": "service_permission_denied"}
    if status == 404:
        return {"kind": "fatal", "code": "service_model_or_endpoint_missing"}
    if status == 429:
        return {"kind": "transient", "code": "service_rate_limited"}
    if status in {408, 409, 425}:
        return {"kind": "transient", "code": "service_temporarily_unavailable"}
    if status is not None and status >= 500:
        return {"kind": "transient", "code": "service_unavailable"}
    if status is not None and 400 <= status < 500:
        return {"kind": "fatal", "code": "service_request_rejected"}
    if code is not None:
        kind, failure_code = _PROVIDER_CODE_FAILURES[code]
        return {"kind": kind, "code": failure_code}
    names = {item.__name__ for item in type(error).__mro__}
    if names & {"TimeoutError", "TimeoutException", "APITimeoutError", "ReadTimeout", "ConnectTimeout"}:
        return {"kind": "transient", "code": "service_timeout"}
    if names & {"ConnectionError", "APIConnectionError", "NetworkError", "ConnectError", "ReadError",
                "WriteError", "RemoteProtocolError", "ProxyError"}:
        return {"kind": "transient", "code": "service_connection_failed"}
    if names & {"ModelStreamError"}:
        if str(error) in {"model_stream_idle_timeout", "model_stream_interrupted"}:
            if str(error) == "model_stream_interrupted":
                # HTTP 200 can still contain an API SSE error. The SDK retains
                # its structured code in APIError; it is not a broken socket.
                for cause in list(_exception_chain(error))[1:]:
                    cause_names = {item.__name__ for item in type(cause).__mro__}
                    if "APIError" in cause_names and "APIConnectionError" not in cause_names:
                        return classify_request_error(cause)
                    if cause_names & {"StreamClosed", "StreamConsumed", "ResponseNotRead", "RequestNotRead"}:
                        return {"kind": "fatal", "code": "model_stream_state_error"}
                    if type(cause) in {RuntimeError, TypeError, ValueError, AssertionError, KeyError}:
                        return {"kind": "fatal", "code": "model_stream_state_error"}
            return {"kind": "transient", "code": str(error)}
        return {"kind": "invalid", "code": "model_stream_invalid"}
    if "APIError" in names:
        # Unknown provider SSE errors need inspection; do not pay repeatedly
        # for a permanent error whose text is intentionally not retained.
        return {"kind": "fatal", "code": "service_stream_error"}
    if isinstance(error, ValueError):
        message = str(error)
        if message in FATAL_VALIDATION_CODES:
            return {"kind": "fatal", "code": message}
        return {"kind": "invalid", "code": "model_output_invalid"}
    # Unknown failures do not justify repeated paid calls. Callers can still
    # report the stable category without exposing a provider's exception text.
    return {"kind": "fatal", "code": "model_request_failed"}


def rejected_before_generation(error: Exception) -> bool:
    """An explicit client/rate-limit rejection has not produced billable output."""
    return _status(error) in {400, 401, 403, 404, 405, 409, 413, 415, 422, 429}


def retry_delay(error: Exception, attempt: int) -> float:
    """Bound Retry-After (seconds or HTTP date), then exponential full jitter."""
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        headers = getattr(error, "headers", None)
    value = headers.get("retry-after") if hasattr(headers, "get") else None
    if value is None and hasattr(headers, "get"):
        value = headers.get("Retry-After")
    if value is not None:
        try:
            delay = float(value)
        except (TypeError, ValueError, OverflowError):
            try:
                stamp = parsedate_to_datetime(str(value))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                delay = (stamp - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = -1.0
        if math.isfinite(delay) and delay >= 0:
            return min(MAX_RETRY_DELAY_SECONDS, delay)
    ceiling = min(30.0, .5 * (2 ** min(max(0, attempt), 6)))
    return random.uniform(ceiling / 2, ceiling)


def cancelable_wait(seconds: float, cancel_check) -> None:
    """Check cancellation during quiet queue/cooldown/backoff intervals."""
    deadline = time.monotonic() + max(0., seconds)
    while True:
        cancel_check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(.15, remaining))


class ServiceCooldowns:
    """A bounded shared cooldown table; unrelated services never wait on it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._until: dict[str, float] = {}

    @staticmethod
    def identity(base_url: str, credential: str) -> str:
        return hashlib.sha256((base_url.rstrip("/") + "\0" + credential).encode("utf-8")).hexdigest()

    def defer(self, identity: str, seconds: float) -> None:
        with self._lock:
            now = time.monotonic()
            self._until = {key: until for key, until in self._until.items() if until > now}
            if identity not in self._until and len(self._until) >= MAX_SHARED_SERVICES:
                self._until.pop(min(self._until, key=self._until.get))
            self._until[identity] = max(self._until.get(identity, now),
                                       now + min(MAX_RETRY_DELAY_SECONDS, max(0., seconds)))

    def wait(self, identity: str, *, cancel_check=None) -> None:
        with self._lock:
            remaining = min(MAX_RETRY_DELAY_SECONDS,
                            max(0., self._until.get(identity, 0.) - time.monotonic()))
        if remaining:
            # One bounded wait, outside locks. Repeated 429s cannot keep one
            # caller waiting forever by extending its deadline in a loop.
            if cancel_check:
                cancelable_wait(remaining, cancel_check)
            else:
                time.sleep(remaining)


SERVICE_COOLDOWNS = ServiceCooldowns()
