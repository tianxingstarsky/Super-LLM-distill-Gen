"""Use cases for backend inventory, settings, role assignment and budget reset."""
from __future__ import annotations

from typing import Any
from copy import deepcopy

from lib.application.backend_ports import BackendConfigPort
from lib.domain.backend_config import (key_display, merged_backends, validate_credential_reference,
                                       validate_endpoint, validate_role, validate_token_prices)
from lib.model_protocols import validate_api_format
from lib.domain.backend_config import validate_backend_url
from lib.domain.model_capabilities import (INFO_FIELDS, connection_signature, empty_model_info,
                                          merge_model_info, normalize_model_info,
                                          validate_manual_capabilities)

_UNSET = object()


class BackendApplication:
    def __init__(self, port: BackendConfigPort):
        self._port = port

    def read_config(self, filename: str = "backends.yaml") -> dict[str, Any]:
        return self._port.read_config(filename)

    def merged_backends(self) -> dict[str, dict[str, Any]]:
        endpoints = merged_backends(self.read_config(), self.read_config("backends.local.yaml"))
        for name, endpoint in endpoints.items():
            declarations = deepcopy(endpoint.get("model_capabilities") or {})
            for model, declaration in declarations.items():
                if not isinstance(declaration, dict):
                    continue
                if declaration.get("vision_source") == "service":
                    declaration.pop("vision", None)
                if (declaration.get("vision_source") == "probe"
                        and declaration.get("probe_connection_sha256") != self._signature(endpoint, model)):
                    declaration.pop("vision", None)
                probe = declaration.get("probes")
                if isinstance(probe, dict) and probe.get("connection_sha256") != self._signature(endpoint, model):
                    declaration.pop("probes", None)
            endpoint["model_capabilities"] = declarations
            cache = self._model_cache(name, endpoint)
            models = list(dict.fromkeys([*(endpoint.get("models") or []), *(cache.get("models") or []),
                                        *declarations]))
            infos = {model: self._model_info(model, endpoint, cache) for model in models}
            endpoint["model_info"] = infos
            from lib.domain.document_parser import vision_connection_signature
            for model, info in infos.items():
                # The endpoint's own model declaration can establish image
                # input. Community metadata remains a suggestion to test.
                source = info.get("sources", {}).get("vision")
                if info.get("vision") is not None and source in {"manual", "probe", "service"}:
                    declaration = declarations.setdefault(model, {})
                    declaration.update(vision=info["vision"], vision_source=source,
                                       connection_sha256=vision_connection_signature(endpoint))
                    if source == "probe":
                        declaration["probe_connection_sha256"] = self._signature(endpoint, model)
        return endpoints

    def _signature(self, endpoint: dict, model: str = "") -> str:
        method = getattr(self._port, "model_connection_signature", None)
        if callable(method):
            return method(endpoint, model)
        # Ports without provider access can still bind metadata to their stored
        # credential reference; the filesystem adapter additionally sees env changes.
        credential = str(endpoint.get("api_key") or "env:" + str(endpoint.get("api_key_env", "")))
        return connection_signature(endpoint, credential, model)

    def _model_cache(self, name: str, endpoint: dict) -> dict:
        method = getattr(self._port, "read_model_cache", None)
        return method(name, endpoint) if callable(method) else {}

    def _endpoint(self, name: str, overrides: dict[str, Any] | None = None) -> dict:
        endpoint = dict(self.merged_backends().get(name) or {})
        if overrides:
            if "api_key_env" in overrides and "api_key" not in overrides:
                endpoint.pop("api_key", None)
            elif "api_key" in overrides and "api_key_env" not in overrides:
                endpoint.pop("api_key_env", None)
            endpoint.update(overrides)
        if not endpoint.get("base_url"):
            raise ValueError("缺少 base_url")
        validate_backend_url(endpoint["base_url"])
        endpoint["api_format"] = validate_api_format(endpoint.get("api_format", "chat"))
        return endpoint

    def _model_info(self, model: str, endpoint: dict, cache: dict) -> dict:
        declaration = (endpoint.get("model_capabilities") or {}).get(model) or {}
        if not isinstance(declaration, dict):
            declaration = {}
        manual = declaration.get("manual")
        if not isinstance(manual, dict):
            manual = {field: declaration[field] for field in INFO_FIELDS if field in declaration
                      and not (field == "vision" and declaration.get("vision_source") in {"probe", "service"})}
        else:
            manual = deepcopy(manual)
        from lib.domain.document_parser import vision_connection_signature
        if ("connection_sha256" in declaration
                and declaration["connection_sha256"] != vision_connection_signature(endpoint)):
            manual.pop("vision", None)
        probe = declaration.get("probes") or {}
        tested = empty_model_info(model)
        if isinstance(probe, dict) and probe.get("connection_sha256") == self._signature(endpoint, model):
            for field in ("vision", "pdf", "tools"):
                test = (probe.get("tests") or {}).get(field) or {}
                if test.get("status") in {"passed", "unsupported"}:
                    tested[field] = test["status"] == "passed"
                    tested["sources"][field] = "probe"
        else:
            probe = {}
        observed = (cache.get("model_info") or {}).get(model) or {}
        negative = empty_model_info(model)
        for field in ("vision", "pdf", "tools"):
            if tested[field] is False:
                negative[field] = False
                negative["sources"][field] = "probe"
        result = merge_model_info(model, negative, normalize_model_info(model, manual, "manual"), tested, observed)
        result["manual"] = {field: manual.get(field) for field in INFO_FIELDS}
        result["probes"] = deepcopy(probe)
        return result

    def key_display(self, backend: dict[str, Any]) -> dict[str, str]:
        env_name = str(backend.get("api_key_env") or "")
        return key_display(backend, env_present=bool(env_name and self._port.env_present(env_name)))

    def list_backends(self) -> dict[str, Any]:
        base, local = self.read_config(), self.read_config("backends.local.yaml")
        backends = self.merged_backends()
        roles = {**(base.get("model_roles") or {}), **(local.get("model_roles") or {})}
        default = local.get("default_backend") or base.get("default_backend", "deepseek")
        rows = []
        for name, backend in backends.items():
            users = sorted(role for role, slot in roles.items() if (slot.get("backend") or "") == name)
            cache = self._model_cache(name, backend)
            discovered = cache.get("models") or []
            names = list(dict.fromkeys([*(backend.get("models") or []), *discovered,
                                       *(backend.get("model_capabilities") or {})]))
            rows.append({"name": name, "base_url": backend.get("base_url", ""),
                         "models": backend.get("models") or [],
                         "discovered_models": discovered,
                         "model_info": {model: self._model_info(model, backend, cache) for model in names},
                         "discovery_checked_at": cache.get("checked_at"),
                         "api_format": backend["api_format"], "api_key": self.key_display(backend),
                         "roles": users, "is_default": name == default,
                         "model_capabilities": backend.get("model_capabilities") or {},
                         "prices": backend.get("prices") or {}})
        return {"backends": rows, "default_backend": default,
                "default_model": local.get("default_model") or base.get("default_model", ""),
                "roles": roles, "budget": local.get("budget") or base.get("budget") or {},
                "spent": self._port.spent_usd()}

    def test_backend(self, name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        backend = self._endpoint(name, overrides)
        return self._port.probe(name, backend)

    def discover_models(self, name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist discovered candidates without changing configured model pins."""
        endpoint = self._endpoint(name, overrides)
        try:
            result = self._port.discover_models(name, endpoint)
        except Exception:
            # SDK exceptions may embed headers, request bodies or credentials.
            # Discovery failures never replace the previous usable candidates.
            raise ValueError("model_discovery_failed") from None
        if overrides is None:
            self._port.write_model_cache(name, endpoint, result)
        return result

    def get_model_info(self, backend: str, model: str) -> dict[str, Any]:
        self._validate_model_name(model)
        endpoint = self._endpoint(backend)
        return self._model_info(model, endpoint, self._model_cache(backend, endpoint))

    @staticmethod
    def _validate_model_name(model: str) -> None:
        if (type(model) is not str or not model.strip() or len(model) > 200
                or any(ord(character) < 32 for character in model)):
            raise ValueError("invalid_model_capability")

    def save_model_capabilities(self, backend: str, model: str, *, context_window_tokens=_UNSET,
                                max_output_tokens=_UNSET, vision=_UNSET, pdf=_UNSET, tools=_UNSET) -> dict:
        """Save operator declarations separately from observed test evidence."""
        self._validate_model_name(model)
        endpoint = self._endpoint(backend)
        patch = {"context_window_tokens": context_window_tokens, "max_output_tokens": max_output_tokens,
                 "vision": vision, "pdf": pdf, "tools": tools}
        patch = {field: value for field, value in patch.items() if value is not _UNSET}
        previous = (endpoint.get("model_capabilities") or {}).get(model) or {}
        prior_manual = previous.get("manual")
        if not isinstance(prior_manual, dict):
            prior_manual = {field: previous[field] for field in INFO_FIELDS if field in previous
                            and not (field == "vision" and previous.get("vision_source") in {"probe", "service"})}
        manual = validate_manual_capabilities({**prior_manual, **patch})
        local = self.read_config("backends.local.yaml")
        entry = local.setdefault("backends", {}).setdefault(backend, {})
        declarations = deepcopy(endpoint.get("model_capabilities") or {})
        declaration = declarations.setdefault(model, {})
        declaration["manual"] = manual
        from lib.domain.document_parser import vision_connection_signature
        if vision is None:
            if declaration.get("vision_source") != "probe":
                declaration.pop("vision", None)
                declaration.pop("vision_source", None)
        elif vision is not _UNSET:
            declaration.update(vision=vision, vision_source="manual",
                               connection_sha256=vision_connection_signature(endpoint))
        entry["model_capabilities"] = declarations
        self._port.write_local(local)
        return self.get_model_info(backend, model)

    def test_model(self, name: str, model: str, features=None) -> dict[str, Any]:
        """Test one selected model and persist route-bound per-feature evidence."""
        self._validate_model_name(model)
        endpoint = self._endpoint(name)
        base, local = self.read_config(), self.read_config("backends.local.yaml")
        budget = {**(base.get("budget") or {}), **(local.get("budget") or {})}
        signature = self._signature(endpoint, model)
        result = self._port.probe_model(endpoint, model, features, budget)
        # Reload after a network wait, preserving unrelated capability edits.
        latest = self._endpoint(name)
        if self._signature(latest, model) != signature:
            raise ValueError("model_connection_changed")
        local = self.read_config("backends.local.yaml")
        entry = local.setdefault("backends", {}).setdefault(name, {})
        declarations = deepcopy(latest.get("model_capabilities") or {})
        declaration = declarations.setdefault(model, {})
        previous = declaration.get("probes") or {}
        if previous.get("connection_sha256") != signature:
            previous = {}
        tests = deepcopy(previous.get("tests") or {})
        for feature, test in (result.get("tests") or {}).items():
            status = test.get("status")
            prior_status = (tests.get(feature) or {}).get("status")
            # A timeout or skipped request does not refute established model
            # capability. The latest attempt remains visible in last_tests.
            if status == "skipped" or (status == "failed" and prior_status in {"passed", "unsupported"}):
                continue
            tests[feature] = {**test, "tested_at": result.get("tested_at")}
        evidence = {**result, "connection_sha256": signature, "last_tests": deepcopy(result.get("tests") or {}),
                    "tests": tests}
        declaration["probes"] = evidence
        vision_test = (result.get("tests") or {}).get("vision") or {}
        if vision_test.get("status") in {"passed", "unsupported"}:
            from lib.domain.document_parser import vision_connection_signature
            declaration.update(vision=vision_test["status"] == "passed", vision_source="probe",
                               connection_sha256=vision_connection_signature(latest),
                               probe_connection_sha256=signature)
        entry["model_capabilities"] = declarations
        self._port.write_local(local)
        return result

    def save_endpoint(self, name: str, base_url: str, models: list[str],
                      api_key: str = "", api_key_env: str = "", prices: dict[str, float] | None = None,
                      explicit_replace: bool = False, api_format: str = "chat") -> str:
        validate_endpoint(name, base_url, models, api_format)
        if api_key and api_key_env:
            raise ValueError("api_key 与 api_key_env 二选一；推荐环境变量方式")
        validate_credential_reference(api_key_env)
        if prices is not None:
            prices = validate_token_prices(prices)
        local = self.read_config("backends.local.yaml")
        backends = dict(local.get("backends") or {})
        if name in backends and not explicit_replace:
            raise FileExistsError(f"后端 {name} 已存在（explicit_replace=True 才覆盖；或换名字）")
        entry: dict[str, Any] = {"base_url": base_url, "models": list(models),
                                 "api_format": api_format, "model_capabilities": {}}
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

    def confirm_model_vision(self, backend: str, model: str, supported: bool) -> None:
        """Save an explicit per-model image-input declaration, never a name guess."""
        if type(supported) is not bool or not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("invalid_model_capability")
        endpoint = self.merged_backends().get(backend)
        if endpoint is None:
            raise ValueError("workflow_node_service_not_configured")
        local = self.read_config("backends.local.yaml")
        entries = local.setdefault("backends", {})
        entry = entries.setdefault(backend, {})
        capabilities = dict(endpoint.get("model_capabilities") or {})
        from lib.domain.document_parser import vision_connection_signature
        capabilities[model] = {**(capabilities.get(model) or {}), "vision": supported,
                               "vision_source": "manual",
                               "manual": {**((capabilities.get(model) or {}).get("manual") or {}),
                                          "vision": supported},
                               "connection_sha256": vision_connection_signature(endpoint)}
        entry["model_capabilities"] = capabilities
        self._port.write_local(local)

    def reset_budget(self, caller: str, limit: float | None = None) -> float:
        if limit is None:
            limit = float((self.list_backends().get("budget") or {}).get("max_total_usd") or 0)
        spent = self._port.spent_usd()
        actual_spent = self._port.write_budget_reset(caller, limit)
        if actual_spent is not None:
            spent = actual_spent
        self._port.audit_budget_reset(caller, spent)
        return spent

    def spent_usd(self) -> float:
        return self._port.spent_usd()

    def write_local(self, config: dict[str, Any]) -> None:
        self._port.write_local(config)
