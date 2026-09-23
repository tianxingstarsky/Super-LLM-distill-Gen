"""Backend administration rules must work without YAML, SDK or filesystem access."""
from __future__ import annotations

from copy import deepcopy

import pytest

from lib.application.backend_service import BackendApplication


class MemoryBackendPort:
    def __init__(self):
        self.files = {
            "backends.yaml": {
                "backends": {"cloud": {"base_url": "https://example.test/v1", "models": ["base"],
                                       "api_key_env": "CLOUD_KEY"}},
                "default_backend": "cloud",
                "model_roles": {"generation": {"backend": "cloud", "model": "base"}},
                "budget": {"max_total_usd": 5.0},
            },
            "backends.local.yaml": {},
        }
        self.env = {"CLOUD_KEY"}
        self.spent = 2.5
        self.events = []

    def read_config(self, filename):
        return deepcopy(self.files.get(filename, {}))

    def write_local(self, config):
        self.files["backends.local.yaml"] = deepcopy(config)
        self.events.append("local_saved")

    def spent_usd(self):
        return self.spent

    def write_budget_reset(self, caller, limit):
        self.events.append(("budget_reset", caller, limit))
        self.spent = 0.0

    def audit_budget_reset(self, caller, previous_spent):
        self.events.append(("audit", caller, previous_spent))

    def env_present(self, name):
        return name in self.env

    def probe(self, name, backend):
        self.events.append(("probe", name, deepcopy(backend)))
        return {"backend": name, "base_url": backend["base_url"], "models": ["remote"]}


def test_inventory_and_role_assignment_are_port_driven_and_secret_safe():
    port = MemoryBackendPort()
    app = BackendApplication(port)
    app.save_endpoint("private", "http://127.0.0.1:9000/v1", ["local"], api_key="sk-secret-123456")
    app.set_role("jev", "private", "local")

    inventory = app.list_backends()
    cloud, private = inventory["backends"]
    assert cloud["api_key"] == {"source": "env:CLOUD_KEY", "status": "存在"}
    assert private["api_key"] == {"source": "configs/backends.local.yaml", "status": "sk-***3456"}
    assert "sk-secret-123456" not in str(inventory)
    assert private["roles"] == ["jev"]
    assert inventory["spent"] == 2.5
    assert app.test_backend("private", {"base_url": "http://127.0.0.1:9010/v1"})["models"] == ["remote"]
    assert port.events[-1][2]["base_url"] == "http://127.0.0.1:9010/v1"


def test_duplicate_protection_and_budget_reset_use_explicit_port_operations():
    port = MemoryBackendPort()
    app = BackendApplication(port)
    app.save_endpoint("local", "http://127.0.0.1:9000/v1", ["m"])
    with pytest.raises(FileExistsError):
        app.save_endpoint("local", "http://127.0.0.1:9000/v1", ["m2"])
    with pytest.raises(ValueError, match="角色不合法"):
        app.set_role("unknown", "local", "m")
    assert app.reset_budget("admin", 5.0) == 2.5
    assert port.events[-2:] == [("budget_reset", "admin", 5.0), ("audit", "admin", 2.5)]
    assert app.list_backends()["spent"] == 0.0
