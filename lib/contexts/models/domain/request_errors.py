"""Stable, display-safe failures shared by request policies and transports."""
from __future__ import annotations


class BudgetExceeded(RuntimeError):
    """累计成本超过配置上限（budget.hard_stop 时抛出）。"""


class ModelRequestError(RuntimeError):
    """A stable, display-safe error, without a provider payload or endpoint."""

    def __init__(self, code: str, kind: str, attempts: int, *, diagnostics: dict | None = None):
        self.code, self.kind, self.attempts = code, kind, attempts
        self.retryable = kind == "transient"
        self.diagnostics = diagnostics
        super().__init__(f"chat failed: {code} ({attempts} attempts)")


class ModelJSONError(RuntimeError):
    code = "model_json_invalid"
    kind = "invalid"

    def __init__(self, attempts: int):
        super().__init__(f"JSON 调用失败（{attempts} 次重试后）: model_json_invalid")


class ModelQueueTimeout(RuntimeError):
    code = "model_request_queue_timeout"
    kind = "transient"


class ModelStreamError(ValueError):
    """A static, safe failure code: partial text must never become a sample."""
