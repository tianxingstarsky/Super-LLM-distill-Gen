"""Discovery, manual declarations and paid test evidence do not alter run pins."""
import json
import sys
from types import ModuleType

import pytest
import yaml

from lib.application.backend_service import BackendApplication
from lib.domain.document_parser import supports_vision
from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver
from lib.llm_client import BudgetExceeded, snapshot_backend_endpoint


def _application(tmp_path, *, prices=None, budget=None):
    endpoint = {"base_url": "https://service.test/v1", "models": ["saved"],
                "api_format": "chat", "api_key_env": "UNIT_MODEL_KEY"}
    if prices is not None:
        endpoint["prices"] = prices
    config = {"backends": {"chosen": endpoint}}
    if budget:
        config["budget"] = budget
    folder = tmp_path / "configs"
    folder.mkdir()
    (folder / "backends.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    driver = FilesystemBackendConfigDriver(tmp_path)
    return BackendApplication(driver), driver


def _probe(monkeypatch, statuses=None, *, usage=None, action=None):
    calls = []
    module = ModuleType("lib.infrastructure.model_capability_probe")
    module.MAX_OUTPUT_TOKENS = 256
    def probe(endpoint, model, features=None):
        calls.append((endpoint, model, features))
        if action:
            action()
        return {"model": model, "api_format": endpoint["api_format"],
                "tested_at": "2026-10-09T07:00:00+00:00", "elapsed_ms": 1,
                "tests": {key: {"status": (statuses or {}).get(key, "passed"), "reason_code": "verified"}
                          for key in ("text", "vision", "pdf", "tools")},
                "usage": usage or {"input_tokens": 100, "output_tokens": 12, "calls": 1, "reported_calls": 1}}
    module.probe_model = probe
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls


@pytest.mark.parametrize("models", [[""], ["  "], ["valid", ""], ["x" * 201], ["model\nname"], [None], [7], "model"])
def test_invalid_models_are_rejected_before_writing_a_connection(tmp_path, models):
    app, driver = _application(tmp_path)
    before = app.read_config("backends.local.yaml")
    with pytest.raises(ValueError):
        app.save_endpoint("invalid", "https://models.example.test/v1", models)
    assert app.read_config("backends.local.yaml") == before


def test_discovered_models_enrich_inventory_without_changing_endpoint_or_pin(tmp_path, monkeypatch):
    app, driver = _application(tmp_path)
    original = app.merged_backends()["chosen"]
    before = snapshot_backend_endpoint(tmp_path, "chosen", "saved")
    result = {"models": ["discovered"], "checked_at": "now", "model_info": {
        "discovered": {"model": "discovered", "context_window_tokens": 64000, "vision": True,
                       "sources": {"context_window_tokens": "service", "vision": "service"}}}}
    monkeypatch.setattr(driver, "discover_models", lambda name, endpoint: result)
    assert app.discover_models("chosen") == result
    assert app.merged_backends()["chosen"]["models"] == original["models"]
    assert snapshot_backend_endpoint(tmp_path, "chosen", "saved") == before
    row = app.list_backends()["backends"][0]
    assert row["models"] == ["saved"] and row["discovered_models"] == ["discovered"]
    assert row["model_info"]["discovered"]["vision"] is True
    assert supports_vision(row, "discovered")  # This claim came from the actual service.
    assert not (tmp_path / "configs" / "backends.local.yaml").exists()


def test_discovery_api_failure_is_safe_and_preserves_previous_cache(tmp_path, monkeypatch):
    app, driver = _application(tmp_path)
    endpoint = app.merged_backends()["chosen"]
    driver.write_model_cache("chosen", endpoint, {"models": ["cached"]})
    class APIError(Exception):
        pass
    def broken(name, endpoint):
        raise APIError("authorization bearer VERY_PRIVATE")
    monkeypatch.setattr(driver, "discover_models", broken)
    with pytest.raises(ValueError, match="^model_discovery_failed$") as error:
        app.discover_models("chosen")
    assert error.value.__cause__ is None
    assert "VERY_PRIVATE" not in str(error.value)
    assert app.list_backends()["backends"][0]["discovered_models"] == ["cached"]


def test_unsaved_form_discovery_does_not_persist_config_or_cache(tmp_path, monkeypatch):
    app, driver = _application(tmp_path)
    monkeypatch.setattr(driver, "discover_models", lambda name, endpoint: {"models": ["preview"]})
    assert app.discover_models("preview", {"base_url": "https://preview.test/v1", "api_key": "secret"})["models"] == ["preview"]
    assert not driver._model_cache_path("preview").exists()
    assert "preview" not in app.merged_backends()


def test_preview_credential_mode_switch_uses_the_draft_without_mutating_saved_secrets(tmp_path, monkeypatch):
    from lib.infrastructure.model_discovery import resolved_credential
    app, driver = _application(tmp_path)
    app.save_endpoint("chosen", "https://service.test/v1", ["saved"], api_key="stored-inline", explicit_replace=True)
    seen = []
    def discover(name, endpoint):
        seen.append(endpoint)
        return {"models": []}
    monkeypatch.setattr(driver, "discover_models", discover)
    monkeypatch.setenv("NEW_UNIT_KEY", "draft-env")
    app.discover_models("chosen", {"api_key_env": "NEW_UNIT_KEY"})
    assert resolved_credential(seen[-1]) == "draft-env" and "api_key" not in seen[-1]
    app.save_endpoint("chosen", "https://service.test/v1", ["saved"], api_key_env="NEW_UNIT_KEY", explicit_replace=True)
    app.discover_models("chosen", {"api_key": "draft-inline"})
    assert resolved_credential(seen[-1]) == "draft-inline" and "api_key_env" not in seen[-1]
    assert "draft-inline" not in (tmp_path / "configs" / "backends.local.yaml").read_text()


def test_old_manual_vision_signature_is_not_rebound_on_route_change(tmp_path):
    app, driver = _application(tmp_path)
    app.confirm_model_vision("chosen", "saved", True)
    local = app.read_config("backends.local.yaml")
    signature = local["backends"]["chosen"]["model_capabilities"]["saved"]["connection_sha256"]
    local["backends"]["chosen"]["base_url"] = "https://replacement.test/v1"
    driver.write_local(local)
    endpoint = app.merged_backends()["chosen"]
    assert not supports_vision(endpoint, "saved")
    assert endpoint["model_capabilities"]["saved"]["connection_sha256"] == signature
    assert app.get_model_info("chosen", "saved")["vision"] is None


def test_cache_credentials_and_routes_are_isolated_and_never_saved_in_plaintext(tmp_path, monkeypatch):
    app, driver = _application(tmp_path)
    monkeypatch.setenv("UNIT_MODEL_KEY", "secret-one")
    driver.write_model_cache("chosen", app.merged_backends()["chosen"], {"models": ["found"]})
    payload = driver._model_cache_path("chosen").read_text()
    assert "secret-one" not in payload and "UNIT_MODEL_KEY" not in payload
    assert app.list_backends()["backends"][0]["discovered_models"] == ["found"]
    monkeypatch.setenv("UNIT_MODEL_KEY", "secret-two")
    assert app.list_backends()["backends"][0]["discovered_models"] == []


def test_community_vision_does_not_unlock_and_service_vision_expires_with_cache(tmp_path, monkeypatch):
    app, driver = _application(tmp_path)
    endpoint = app.merged_backends()["chosen"]
    result = {"models": ["saved"], "model_info": {"saved": {
        "vision": True, "sources": {"vision": "models.dev"}}}}
    driver.write_model_cache("chosen", endpoint, result)
    assert app.get_model_info("chosen", "saved")["vision"] is True
    assert not supports_vision(app.merged_backends()["chosen"], "saved")
    result["model_info"]["saved"]["sources"]["vision"] = "service"
    driver.write_model_cache("chosen", endpoint, result)
    assert supports_vision(app.merged_backends()["chosen"], "saved")
    assert app.get_model_info("chosen", "saved")["sources"]["vision"] == "service"
    # Saving another field must not turn a temporary service observation into
    # a permanent manual image declaration.
    app.save_model_capabilities("chosen", "saved", tools=True)
    monkeypatch.setenv("UNIT_MODEL_KEY", "changed-account")
    assert not supports_vision(app.merged_backends()["chosen"], "saved")


def test_manual_updates_preserve_other_fields_and_none_restores_unknown(tmp_path):
    app, driver = _application(tmp_path)
    app.save_model_capabilities("chosen", "saved", context_window_tokens=64000,
                                max_output_tokens=8000, pdf=False, tools=True)
    info = app.save_model_capabilities("chosen", "saved", vision=True)
    assert info["context_window_tokens"] == 64000 and info["pdf"] is False and info["tools"] is True
    assert supports_vision(app.merged_backends()["chosen"], "saved")
    info = app.save_model_capabilities("chosen", "saved", vision=None)
    assert info["vision"] is None and info["tools"] is True
    assert not supports_vision(app.merged_backends()["chosen"], "saved")
    with pytest.raises(ValueError):
        app.save_model_capabilities("chosen", "saved", vision="true")
    with pytest.raises(ValueError):
        app.save_model_capabilities("chosen", "saved", context_window_tokens=4000)


def test_probe_evidence_is_separate_per_model_and_invalidates_after_key_change(tmp_path, monkeypatch):
    app, driver = _application(tmp_path, prices={"input_per_1m_usd": 0, "output_per_1m_usd": 0})
    monkeypatch.setenv("UNIT_MODEL_KEY", "secret-one")
    _probe(monkeypatch)
    app.test_model("chosen", "saved")
    endpoint = app.merged_backends()["chosen"]
    assert supports_vision(endpoint, "saved")
    assert app.get_model_info("chosen", "saved")["sources"]["vision"] == "probe"
    assert app.get_model_info("chosen", "other")["vision"] is None
    config_text = (tmp_path / "configs" / "backends.local.yaml").read_text()
    assert "secret-one" not in config_text
    monkeypatch.setenv("UNIT_MODEL_KEY", "secret-two")
    assert not supports_vision(app.merged_backends()["chosen"], "saved")
    assert app.get_model_info("chosen", "saved")["probes"] == {}


def test_probe_does_not_claim_failure_is_unsupported_or_replace_manual_override(tmp_path, monkeypatch):
    app, driver = _application(tmp_path)
    _probe(monkeypatch, {"vision": "failed", "pdf": "unsupported", "tools": "passed"})
    app.save_model_capabilities("chosen", "saved", tools=False)
    result = app.test_model("chosen", "saved")
    info = app.get_model_info("chosen", "saved")
    assert info["vision"] is None and info["pdf"] is False
    assert info["tools"] is False and info["sources"]["tools"] == "manual"
    assert info["probes"]["tests"]["tools"]["status"] == "passed"
    assert result["cost_status"] == "unknown_prices"


def test_failed_retest_preserves_capability_evidence_and_reports_latest_attempt(tmp_path, monkeypatch):
    app, driver = _application(tmp_path)
    _probe(monkeypatch)
    app.test_model("chosen", "saved")
    _probe(monkeypatch, {"vision": "failed", "pdf": "skipped", "tools": "failed"})
    app.test_model("chosen", "saved")
    info = app.get_model_info("chosen", "saved")
    assert info["vision"] is True and info["sources"]["vision"] == "probe"
    assert info["tools"] is True and info["pdf"] is True
    assert info["probes"]["tests"]["vision"]["status"] == "passed"
    assert info["probes"]["last_tests"]["vision"]["status"] == "failed"
    assert info["probes"]["last_tests"]["pdf"]["status"] == "skipped"
    assert supports_vision(app.merged_backends()["chosen"], "saved")


def test_connection_change_during_probe_does_not_save_stale_evidence(tmp_path, monkeypatch):
    app, driver = _application(tmp_path)
    def change():
        app.save_endpoint("chosen", "https://changed.test/v1", ["saved"],
                          api_key_env="UNIT_MODEL_KEY", explicit_replace=True)
    _probe(monkeypatch, action=change)
    with pytest.raises(ValueError, match="model_connection_changed"):
        app.test_model("chosen", "saved")
    assert app.get_model_info("chosen", "saved")["probes"] == {}


def test_capability_test_cost_uses_shared_budget_and_missing_usage_is_not_free(tmp_path, monkeypatch):
    prices = {"input_per_1m_usd": 1.0, "output_per_1m_usd": 2.0}
    app, driver = _application(tmp_path, prices=prices, budget={"max_total_usd": 1.0, "hard_stop": True})
    _probe(monkeypatch)
    result = app.test_model("chosen", "saved", ["text"])
    assert result["cost_usd"] == pytest.approx(124 / 1e6)
    ledger = tmp_path / "data" / "output" / "budget.json"
    assert json.loads(ledger.read_text())["spent_usd"] == pytest.approx(result["cost_usd"])
    _probe(monkeypatch, usage={"input_tokens": 0, "output_tokens": 0, "calls": 1, "reported_calls": 0})
    second = app.test_model("chosen", "saved", ["vision"])
    assert second["cost_status"] == "reserved_for_missing_usage"
    assert second["cost_usd"] == pytest.approx((4096 + 512) / 1e6)


@pytest.mark.parametrize("budget", [None, {"max_total_usd": 0, "hard_stop": False},
                                  {"max_total_usd": 0.00001, "hard_stop": False}])
def test_priced_probe_without_hard_limit_records_cost_without_closing_admission(tmp_path, monkeypatch, budget):
    app, driver = _application(tmp_path, prices={"input_per_1m_usd": 1, "output_per_1m_usd": 2}, budget=budget)
    _probe(monkeypatch)
    result = app.test_model("chosen", "saved", ["text"])
    assert result["cost_usd"] == pytest.approx(124 / 1e6)
    assert app.get_model_info("chosen", "saved")["probes"]["last_tests"]["text"]["status"] == "passed"
    ledger = json.loads((tmp_path / "data" / "output" / "budget.json").read_text())
    assert ledger["spent_usd"] == pytest.approx(result["cost_usd"])
    assert not ledger.get("budget_bound_exceeded")
    assert ledger["reservations"] == {}


def test_hard_budget_rejects_unpriced_or_unaffordable_probe_before_call(tmp_path, monkeypatch):
    app, driver = _application(tmp_path, budget={"max_total_usd": 0.001, "hard_stop": True})
    calls = _probe(monkeypatch)
    with pytest.raises(ValueError, match="model_budget_prices_required"):
        app.test_model("chosen", "saved")
    assert calls == []
    app.save_endpoint("chosen", "https://service.test/v1", ["saved"],
                      prices={"input_per_1m_usd": 1, "output_per_1m_usd": 2},
                      explicit_replace=True, api_key_env="UNIT_MODEL_KEY")
    with pytest.raises(BudgetExceeded):
        app.test_model("chosen", "saved")
    assert calls == []
