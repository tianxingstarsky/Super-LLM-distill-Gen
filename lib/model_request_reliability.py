"""Safe request failure categories and bounded, service-local retry pacing.

Provider text and response bodies never leave this module. Cooldowns are an
in-process courtesy between workers; they are not durable task state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import math
import random
import threading
import time


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
})


class ModelRequestError(RuntimeError):
    """A stable, display-safe error, without a provider payload or endpoint."""

    def __init__(self, code: str, kind: str, attempts: int):
        self.code, self.kind, self.attempts = code, kind, attempts
        self.retryable = kind == "transient"
        super().__init__(f"chat failed: {code} ({attempts} attempts)")


class ModelJSONError(RuntimeError):
    code = "model_json_invalid"
    kind = "invalid"

    def __init__(self, attempts: int):
        super().__init__(f"JSON 调用失败（{attempts} 次重试后）: model_json_invalid")


def _status(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    if value is None:
        value = getattr(getattr(error, "response", None), "status_code", None)
    return value if type(value) is int else None


def classify_request_error(error: Exception) -> dict[str, str]:
    """Classify from types/status/known codes, never interpolate provider text."""
    if isinstance(error, (ModelRequestError, ModelJSONError)):
        return {"kind": error.kind, "code": error.code}
    if type(error).__name__ == "BudgetExceeded":
        return {"kind": "fatal", "code": "budget_exhausted"}
    status = _status(error)
    # Quota depletion will not be cured by rapidly retrying a 429. Read only
    # whitelisted structured codes; all other provider fields are ignored.
    code = getattr(error, "code", None)
    body = getattr(error, "body", None)
    if code is None and isinstance(body, dict):
        details = body.get("error", body)
        if isinstance(details, dict):
            code = details.get("code") or details.get("type")
    if isinstance(code, str) and code in {
            "insufficient_quota", "billing_hard_limit_reached", "credit_balance_too_low"}:
        return {"kind": "fatal", "code": "service_quota_exhausted"}
    if status == 401:
        return {"kind": "fatal", "code": "service_authentication_failed"}
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
    names = {item.__name__ for item in type(error).__mro__}
    if names & {"TimeoutError", "TimeoutException", "APITimeoutError", "ReadTimeout", "ConnectTimeout"}:
        return {"kind": "transient", "code": "service_timeout"}
    if names & {"ConnectionError", "APIConnectionError", "NetworkError", "ConnectError", "ReadError",
                "WriteError", "RemoteProtocolError", "ProxyError"}:
        return {"kind": "transient", "code": "service_connection_failed"}
    if names & {"ModelStreamError"}:
        return {"kind": "invalid", "code": "model_stream_invalid"}
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

    def wait(self, identity: str) -> None:
        with self._lock:
            remaining = min(MAX_RETRY_DELAY_SECONDS,
                            max(0., self._until.get(identity, 0.) - time.monotonic()))
        if remaining:
            # One bounded wait, outside locks. Repeated 429s cannot keep one
            # caller waiting forever by extending its deadline in a loop.
            time.sleep(remaining)


SERVICE_COOLDOWNS = ServiceCooldowns()
