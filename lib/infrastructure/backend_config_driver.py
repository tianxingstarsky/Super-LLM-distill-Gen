"""YAML, environment and provider SDK adapters for backend administration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

import yaml

from lib.model_protocols import validate_api_format


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
