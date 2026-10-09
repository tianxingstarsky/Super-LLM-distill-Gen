"""SDK model discovery enriched from the maintained models.dev public API."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from filelock import FileLock

from lib.domain.model_capabilities import merge_model_info, normalize_model_info
from lib.domain.model_capabilities import INFO_FIELDS
from lib.domain.backend_config import validate_backend_url
from lib.model_protocols import validate_api_format


CATALOG_URL = "https://models.dev/api.json"
CATALOG_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CATALOG_BYTES = 32 * 1024 * 1024
MAX_LIST_MODELS = 10_000
MAX_LIST_PAGES = 5
LIST_TIMEOUT_SECONDS = 15.0
# Service identities, not a model or capability directory. All model facts come
# from the live endpoint or upstream's provider-specific records.
OFFICIAL_PROVIDER_HOSTS = {
    "api.openai.com": "openai", "api.anthropic.com": "anthropic",
    "api.deepseek.com": "deepseek", "openrouter.ai": "openrouter",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolved_credential(backend: dict) -> str:
    protocol = validate_api_format(backend.get("api_format", "chat"))
    default = "ANTHROPIC_API_KEY" if protocol == "anthropic" else "OPENAI_API_KEY"
    reference = default if backend.get("api_key_env") is None else str(backend["api_key_env"])
    return str(backend.get("api_key") or os.environ.get(reference, "") or "sk-local")


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_catalog(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_CATALOG_BYTES:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        fetched = value.get("fetched_at") if isinstance(value, dict) else None
        return value if (isinstance(value, dict) and isinstance(value.get("providers"), dict)
                         and type(fetched) in (int, float) and math.isfinite(fetched) and fetched >= 0) else {}
    except (OSError, ValueError):
        return {}


def load_catalog(cache_dir: Path) -> tuple[dict, str]:
    """Refresh at most weekly, retaining the last usable catalog on failure."""
    path = cache_dir / "models-dev.json"
    cache_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=5):
        saved = _read_catalog(path)
        now = time.time()
        if saved and now - saved.get("fetched_at", 0) < CATALOG_TTL_SECONDS:
            return saved["providers"], "cached"
        # This public request has no account headers or service credentials.
        request = Request(CATALOG_URL, headers={"Accept": "application/json",
                                               "User-Agent": "DataForge-model-discovery"})
        try:
            with closing(urlopen(request, timeout=5)) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > MAX_CATALOG_BYTES:
                    raise ValueError("model_catalog_too_large")
                payload = response.read(MAX_CATALOG_BYTES + 1)
                if len(payload) > MAX_CATALOG_BYTES:
                    raise ValueError("model_catalog_too_large")
            providers = json.loads(payload)
            if not isinstance(providers, dict) or not any(
                    isinstance(row, dict) and isinstance(row.get("models"), dict)
                    for row in providers.values()):
                raise ValueError("invalid_model_catalog")
            write_json_atomic(path, {"fetched_at": now, "providers": providers})
            return providers, "refreshed"
        except (OSError, ValueError, TypeError):
            return saved.get("providers", {}), "stale" if saved else "unavailable"


def match_provider(backend: dict, providers: dict) -> str | None:
    """Match the actual service identity; unknown gateways never borrow names."""
    try:
        endpoint = urlsplit(validate_backend_url(backend.get("base_url", "")))
        port = endpoint.port
    except ValueError:
        return None
    if endpoint.scheme != "https" or not endpoint.hostname or port not in (None, 443):
        return None
    host = endpoint.hostname.lower()
    provider = OFFICIAL_PROVIDER_HOSTS.get(host)
    if provider in providers:
        if provider == "anthropic" and backend.get("api_format", "chat") != "anthropic":
            return None
        return provider
    candidates = []
    for name, row in providers.items():
        if not isinstance(row, dict) or not isinstance(row.get("api"), str):
            continue
        try:
            configured = urlsplit(validate_backend_url(row["api"]))
            configured_port = configured.port
        except ValueError:
            continue
        if (configured.scheme == "https" and configured.hostname == endpoint.hostname
                and configured_port in (None, 443)
                and endpoint.path.rstrip("/") == configured.path.rstrip("/")):
            candidates.append(name)
    return candidates[0] if len(candidates) == 1 else None


def _record(value) -> dict:
    if isinstance(value, dict):
        return value
    if callable(getattr(value, "model_dump", None)):
        result = value.model_dump()
        return result if isinstance(result, dict) else {}
    return vars(value) if hasattr(value, "__dict__") else {}


def discover_models(backend: dict, cache_dir: Path) -> dict:
    """List only this endpoint's models; catalog entries never add availability."""
    protocol = validate_api_format(backend.get("api_format", "chat"))
    validate_backend_url(backend["base_url"])
    if protocol == "anthropic":
        from anthropic import Anthropic
        factory = Anthropic
    else:
        from openai import OpenAI
        factory = OpenAI
    client = factory(base_url=backend["base_url"], api_key=resolved_credential(backend),
                     timeout=12.0, max_retries=0)
    service_info, truncated = {}, False
    started = time.monotonic()
    next_query = {}
    seen_cursors = set()
    try:
        for page_index in range(MAX_LIST_PAGES):
            remaining = LIST_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                truncated = True
                break
            kwargs = {"timeout": min(12.0, remaining)}
            if protocol == "anthropic":
                kwargs.update(limit=1000, **next_query)
            elif next_query:
                kwargs["extra_query"] = next_query
            page = client.models.list(**kwargs)
            values = getattr(page, "data", None)
            if not isinstance(values, list):
                raise ValueError("invalid_model_list")
            for value in values:
                if len(service_info) >= MAX_LIST_MODELS:
                    truncated = True
                    break
                record = _record(value)
                model = record.get("id")
                if not isinstance(model, str) or not model.strip() or len(model) > 200:
                    continue
                if any(ord(character) < 32 for character in model):
                    continue
                model = model.strip()
                service_info[model] = normalize_model_info(model, record, "service")
            if truncated:
                break
            has_next = bool(getattr(page, "has_more", False))
            method = getattr(page, "has_next_page", None)
            if callable(method):
                has_next = bool(method())
            if not has_next:
                break
            if protocol == "anthropic":
                cursor = getattr(page, "last_id", None) or (_record(values[-1]).get("id") if values else None)
                next_query = {"after_id": cursor} if isinstance(cursor, str) and cursor else {}
            else:
                info_method = getattr(page, "next_page_info", None)
                info = info_method() if callable(info_method) else None
                params = getattr(info, "params", None)
                next_query = params if isinstance(params, dict) else {}
            cursor_key = json.dumps(next_query, sort_keys=True)
            if not next_query or cursor_key in seen_cursors or page_index == MAX_LIST_PAGES - 1:
                truncated = True
                break
            seen_cursors.add(cursor_key)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    needs_catalog = any(any(info[field] is None for field in INFO_FIELDS)
                        for info in service_info.values())
    providers, catalog_status = load_catalog(cache_dir) if needs_catalog else ({}, "not_needed")
    provider = match_provider(backend, providers)
    directory = (providers.get(provider) or {}).get("models", {}) if provider else {}
    checked_at = utc_now()
    infos = {}
    for model, service in service_info.items():
        entry = directory.get(model) if isinstance(directory, dict) else None
        catalog = normalize_model_info(model, entry or {}, "models.dev")
        infos[model] = merge_model_info(model, service, catalog)
        infos[model].update(checked_at=checked_at, catalog_status=catalog_status)
        if provider:
            infos[model].update(provider=provider, catalog_source=CATALOG_URL)
    return {"models": sorted(infos), "model_info": infos, "checked_at": checked_at,
            "catalog_status": catalog_status, "provider": provider, "truncated": truncated}
