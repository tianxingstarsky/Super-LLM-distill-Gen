"""Use cases for backend inventory, settings, role assignment and budget reset."""
from __future__ import annotations

from typing import Any

from lib.application.backend_ports import BackendConfigPort
from lib.domain.backend_config import key_display, merged_backends, validate_endpoint, validate_role


class BackendApplication:
    def __init__(self, port: BackendConfigPort):
        self._port = port

    def read_config(self, filename: str = "backends.yaml") -> dict[str, Any]:
        return self._port.read_config(filename)

    def merged_backends(self) -> dict[str, dict[str, Any]]:
        return merged_backends(self.read_config(), self.read_config("backends.local.yaml"))

    def key_display(self, backend: dict[str, Any]) -> dict[str, str]:
        env_name = str(backend.get("api_key_env") or "")
        return key_display(backend, env_present=bool(env_name and self._port.env_present(env_name)))

    def list_backends(self) -> dict[str, Any]:
        base, local = self.read_config(), self.read_config("backends.local.yaml")
        backends = merged_backends(base, local)
        roles = {**(base.get("model_roles") or {}), **(local.get("model_roles") or {})}
        default = local.get("default_backend") or base.get("default_backend", "deepseek")
        rows = []
        for name, backend in backends.items():
            users = sorted(role for role, slot in roles.items() if (slot.get("backend") or "") == name)
            rows.append({"name": name, "base_url": backend.get("base_url", ""),
                         "models": backend.get("models") or [], "api_key": self.key_display(backend),
                         "roles": users, "is_default": name == default,
                         "prices": backend.get("prices") or {}})
        return {"backends": rows, "default_backend": default,
                "default_model": local.get("default_model") or base.get("default_model", ""),
                "roles": roles, "budget": local.get("budget") or base.get("budget") or {},
                "spent": self._port.spent_usd()}

    def test_backend(self, name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        backend = dict(self.merged_backends().get(name) or {})
        if overrides:
            backend = {**backend, **overrides}
        if not backend.get("base_url"):
            raise ValueError("缺少 base_url")
        return self._port.probe(name, backend)

    def save_endpoint(self, name: str, base_url: str, models: list[str],
                      api_key: str = "", api_key_env: str = "", prices: dict[str, float] | None = None,
                      explicit_replace: bool = False) -> str:
        validate_endpoint(name, base_url, models)
        if api_key and api_key_env:
            raise ValueError("api_key 与 api_key_env 二选一；推荐环境变量方式")
        local = self.read_config("backends.local.yaml")
        backends = dict(local.get("backends") or {})
        if name in backends and not explicit_replace:
            raise FileExistsError(f"后端 {name} 已存在（explicit_replace=True 才覆盖；或换名字）")
        entry: dict[str, Any] = {"base_url": base_url, "models": list(models)}
        if api_key:
            entry["api_key"] = api_key
        else:
            entry["api_key_env"] = api_key_env
        if prices:
            entry["prices"] = prices
        backends[name] = entry
        local["backends"] = backends
        self._port.write_local(local)
        return name

    def set_role(self, role: str, backend: str, model: str) -> None:
        validate_role(role)
        if not self.merged_backends().get(backend):
            raise ValueError(f"后端不存在：{backend}")
        local = self.read_config("backends.local.yaml")
        roles = dict(local.get("model_roles") or {})
        roles[role] = {"backend": backend, "model": model}
        local["model_roles"] = roles
        self._port.write_local(local)

    def reset_budget(self, caller: str, limit: float) -> float:
        spent = self._port.spent_usd()
        self._port.write_budget_reset(caller, limit)
        self._port.audit_budget_reset(caller, spent)
        return spent

    def spent_usd(self) -> float:
        return self._port.spent_usd()

    def write_local(self, config: dict[str, Any]) -> None:
        self._port.write_local(config)
