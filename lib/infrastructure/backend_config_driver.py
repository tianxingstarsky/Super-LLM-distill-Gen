"""YAML, environment and provider SDK adapters for backend administration."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

import yaml

from lib.model_protocols import validate_api_format
from lib.domain.model_capabilities import connection_signature


class FilesystemBackendConfigDriver:
    def __init__(self, root: Path):
        self.root = Path(root)

    def read_config(self, filename: str) -> dict[str, Any]:
        path = self.root / "configs" / filename
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def write_local(self, config: dict[str, Any]) -> None:
        target = self.root / "configs" / "backends.local.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = target.with_name(f"{target.name}.{time.strftime('%Y%m%dT%H%M%S')}.bak")
            shutil.copy2(target, backup)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".pending-", suffix=".yaml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def spent_usd(self) -> float:
        path = self.root / "data" / "output" / "budget.json"
        if path.exists():
            try:
                return float(json.loads(path.read_text(encoding="utf-8")).get("spent_usd", 0.0))
            except (json.JSONDecodeError, OSError):
                pass
        return 0.0

    def write_budget_reset(self, caller: str, limit: float) -> float:
        from lib.llm_client import BudgetGuard

        return BudgetGuard(self.root, limit).reset(caller)

    def audit_budget_reset(self, caller: str, previous_spent: float) -> None:
        from lib.monitor import trace_run

        trace_run(self.root, "budget_reset", {"from_usd": previous_spent, "by": caller})

    def env_present(self, name: str) -> bool:
        return bool(os.environ.get(name, ""))

    def probe(self, name: str, backend: dict[str, Any]) -> dict[str, Any]:
        api_format = validate_api_format(backend.get("api_format", "chat"))
        default_env = "ANTHROPIC_API_KEY" if api_format == "anthropic" else "OPENAI_API_KEY"
        configured_env = backend.get("api_key_env")
        env_name = default_env if configured_env is None else str(configured_env)
        api_key = backend.get("api_key") or os.environ.get(env_name, "") or "sk-local"
        if api_format == "anthropic":
            from anthropic import Anthropic
            client = Anthropic(base_url=backend["base_url"], api_key=api_key)
        else:
            from openai import OpenAI
            client = OpenAI(base_url=backend["base_url"], api_key=api_key)
        models = client.models.list().data
        return {"backend": name, "base_url": backend["base_url"],
                "api_format": api_format, "models": sorted(model.id for model in models)}

    @property
    def model_cache_dir(self) -> Path:
        return self.root / "data" / "output" / ".model-capabilities"

    def model_connection_signature(self, backend: dict[str, Any], model: str = "") -> str:
        from lib.infrastructure.model_discovery import resolved_credential
        return connection_signature(backend, resolved_credential(backend), model)

    def _model_cache_path(self, name: str) -> Path:
        return self.model_cache_dir / (hashlib.sha256(name.encode()).hexdigest() + ".json")

    def read_model_cache(self, name: str, backend: dict[str, Any]) -> dict[str, Any]:
        path = self._model_cache_path(name)
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                return {}
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if (not isinstance(record, dict)
                or record.get("connection_sha256") != self.model_connection_signature(backend)):
            return {}
        value = record.get("discovery")
        return value if isinstance(value, dict) else {}

    def write_model_cache(self, name: str, backend: dict[str, Any], value: dict[str, Any]) -> None:
        from filelock import FileLock
        from lib.infrastructure.model_discovery import write_json_atomic
        path = self._model_cache_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path) + ".lock", timeout=5):
            write_json_atomic(path, {"connection_sha256": self.model_connection_signature(backend),
                                    "discovery": value})

    def discover_models(self, name: str, backend: dict[str, Any]) -> dict[str, Any]:
        from lib.infrastructure.model_discovery import discover_models
        return {"backend": name, "base_url": backend["base_url"],
                "api_format": validate_api_format(backend.get("api_format", "chat")),
                **discover_models(backend, self.model_cache_dir)}

    def probe_model(self, backend: dict[str, Any], model: str, features=None,
                    budget: dict[str, Any] | None = None) -> dict[str, Any]:
        from lib.infrastructure.model_capability_probe import probe_model
        from lib.domain.backend_config import validate_token_prices
        from lib.llm_client import BudgetGuard
        from lib.infrastructure.model_capability_probe import MAX_OUTPUT_TOKENS
        allowed = {"text", "vision", "pdf", "tools"}
        selected = allowed if features is None else set(features) if isinstance(features, (list, tuple)) else None
        if selected is None or selected - allowed:
            raise ValueError("invalid_model_probe_features")
        if not selected:
            return probe_model(backend, model, features)
        config = budget or {}
        limit = float(config.get("max_total_usd") or 0)
        hard = bool(config.get("hard_stop", True)) and limit > 0
        raw_prices = backend.get("prices")
        if not raw_prices:
            if hard:
                raise ValueError("model_budget_prices_required")
            result = probe_model(backend, model, features)
            return {**result, "cost_usd": None, "cost_status": "unknown_prices"}
        prices = validate_token_prices(raw_prices)
        input_rate, output_rate = prices["input_per_1m_usd"], prices["output_per_1m_usd"]
        # Fixtures are a 96px image and one tiny PDF page. A short test cannot
        # establish the model's true context boundary; this is billing admission.
        calls_bound = 1 + len(selected - {"text"})
        per_call_ceiling = (4096 * input_rate + MAX_OUTPUT_TOKENS * output_rate) / 1e6
        ceiling = calls_bound * per_call_ceiling
        guard = BudgetGuard(self.root, limit, hard_stop=hard)
        token = guard.reserve(ceiling) if hard and ceiling > 0 else None
        if token is None and hard:
            guard.check()
        try:
            result = probe_model(backend, model, features)
        except BaseException:
            # After interruption the service may already have billed a call.
            if token is not None:
                guard.settle(token, ceiling)
            elif not hard:
                guard.add_usd(ceiling)
            raise
        usage = result.get("usage") or {}
        count = lambda name: max(0, usage.get(name, 0)) if type(usage.get(name, 0)) is int else 0
        input_tokens, output_tokens = count("input_tokens"), count("output_tokens")
        calls, reported = count("calls"), count("reported_calls")
        missing = max(0, calls - reported)
        charge = (input_tokens * input_rate + output_tokens * output_rate) / 1e6 + missing * per_call_ceiling
        if token is not None:
            guard.settle(token, charge)
        else:
            guard.add_usd(charge)
        return {**result, "cost_usd": charge,
                "cost_status": "reserved_for_missing_usage" if missing else "reported"}
