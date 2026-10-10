"""Backward-compatible backend administration facade for CLI and Streamlit.

Use cases live in :mod:`lib.application.backend_service`; filesystem, environment
and model SDK operations are composed by :mod:`lib.bootstrap.backends`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.bootstrap.backends import backend_application
from lib.domain.backend_config import VALID_NAME, mask_key, validate_endpoint


ROOT = Path(__file__).resolve().parent.parent
_VALID_NAME = VALID_NAME


def _application():
    # ROOT remains patchable for existing CLI/tests that target an isolated tree.
    return backend_application(ROOT)


def _read(base: str = "backends.yaml") -> dict:
    return _application().read_config(base)


def _key_display(backend: dict[str, Any]) -> dict[str, str]:
    return _application().key_display(backend)


def list_backends() -> dict[str, Any]:
    """Show merged backend, role and budget settings without returning full keys."""
    return _application().list_backends()


def _spent() -> float:
    return _application().spent_usd()


def _limit() -> float:
    return float((list_backends().get("budget") or {}).get("max_total_usd", 0.0))


def reset_budget(caller: str) -> float:
    """Reset the local budget and retain a trace of the prior spend."""
    return _application().reset_budget(caller, _limit())


def test_backend(name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe the configured API through its provider SDK model inventory."""
    return _application().test_backend(name, overrides)


def _build_merged() -> dict[str, dict[str, Any]]:
    return _application().merged_backends()


def save_endpoint(name: str, base_url: str, models: list[str],
                  api_key: str = "", api_key_env: str = "", prices: dict[str, float] | None = None,
                  explicit_replace: bool = False, api_format: str = "chat") -> str:
    """Add or replace one backend in gitignored backends.local.yaml."""
    return _application().save_endpoint(name, base_url, models, api_key, api_key_env,
                                        prices, explicit_replace, api_format)


def set_role(role: str, backend: str, model: str) -> None:
    """Assign one model role to a configured backend."""
    _application().set_role(role, backend, model)


def _write_local(config: dict[str, Any]) -> None:
    _application().write_local(config)


def get_model_scheduling(backend: str, model: str) -> dict[str, Any]:
    return _application().get_model_scheduling(backend, model)


def save_model_scheduling(backend: str, model: str, *, max_concurrency: int,
                          request_queue_timeout_seconds: int = 300) -> dict[str, Any]:
    return _application().save_model_scheduling(backend, model, max_concurrency=max_concurrency,
                      request_queue_timeout_seconds=request_queue_timeout_seconds)
