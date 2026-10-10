"""Operator limits for a shared service model, separate from model capabilities."""
from __future__ import annotations


DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_REQUEST_QUEUE_TIMEOUT_SECONDS = 300


def validate_model_scheduling(value: dict | None = None) -> dict:
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise ValueError("invalid_model_scheduling")
    limit = value.get("max_concurrency", DEFAULT_MAX_CONCURRENCY)
    timeout = value.get("request_queue_timeout_seconds", DEFAULT_REQUEST_QUEUE_TIMEOUT_SECONDS)
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError("invalid_model_max_concurrency")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("invalid_model_queue_timeout")
    return {"max_concurrency": limit, "request_queue_timeout_seconds": timeout}
