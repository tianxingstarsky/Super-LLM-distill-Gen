"""Missing model rates must not turn a hard dollar limit into a no-op."""
from __future__ import annotations

import yaml
import pytest

import lib.llm_client as llm
from lib.domain.backend_config import validate_token_prices
from lib.presentation.streamlit.workflow_page import _missing_budget_prices


def _config(root, *, prices=None, budget=True):
    entry = {"base_url": "https://models.example.test/v1", "api_key_env": "TEST_KEY",
             "models": ["example"]}
    if prices is not None:
        entry["prices"] = prices
    config = {"backends": {"provider": entry}, "default_backend": "provider"}
    if budget:
        config["budget"] = {"max_total_usd": 5.0, "hard_stop": True}
    folder = root / "configs"
    folder.mkdir()
    (folder / "backends.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")


def test_unpriced_hard_budget_rejects_at_snapshot_and_before_client_construction(tmp_path, monkeypatch):
    _config(tmp_path)
    constructed = []
    monkeypatch.setattr(llm, "ChatClient", lambda **kwargs: constructed.append(kwargs))
    with pytest.raises(ValueError, match="model_budget_prices_required"):
        llm.snapshot_backend_endpoint(tmp_path, "provider", "example")
    with pytest.raises(ValueError, match="model_budget_prices_required"):
        llm.load_backend(tmp_path, backend="provider", model="example")
    assert constructed == []


def test_partial_or_invalid_rates_cannot_enter_budgeted_request(tmp_path):
    for prices in ({"input_per_1m_usd": 0.2},
                   {"input_per_1m_usd": 0.2, "output_per_1m_usd": -1},
                   {"input_per_1m_usd": 0.2, "output_per_1m_usd": float("nan")}):
        with pytest.raises(ValueError, match="model_budget_prices_required"):
            validate_token_prices(prices)


def test_explicit_zero_rates_remain_allowed_for_free_service(tmp_path, monkeypatch):
    _config(tmp_path, prices={"input_per_1m_usd": 0, "output_per_1m_usd": 0})
    captured = []
    monkeypatch.setattr(llm, "ChatClient", lambda **kwargs: captured.append(kwargs))
    llm.snapshot_backend_endpoint(tmp_path, "provider", "example")
    llm.load_backend(tmp_path, backend="provider", model="example")
    assert captured[0]["price_input_per_1m"] == 0
    assert captured[0]["price_output_per_1m"] == 0
    assert captured[0]["budget"].hard_stop


def test_unpriced_service_is_permitted_without_a_dollar_budget(tmp_path, monkeypatch):
    _config(tmp_path, budget=False)
    captured = []
    monkeypatch.setattr(llm, "ChatClient", lambda **kwargs: captured.append(kwargs))
    llm.load_backend(tmp_path, backend="provider", model="example")
    assert captured[0]["budget"] is None
    assert captured[0]["price_input_per_1m"] == 0


def test_workbench_identifies_unpriced_node_before_submission():
    nodes = ["ingest", "sft", "package"]
    bindings = {"sft": {"generation": {"backend": "unpriced", "model": "writer"},
                        "jev": {"backend": "free", "model": "judge"}}}
    endpoints = {"unpriced": {"prices": {}},
                 "free": {"prices": {"input_per_1m_usd": 0,
                                      "output_per_1m_usd": 0}}}
    budget = {"max_total_usd": 5, "hard_stop": True}
    assert _missing_budget_prices(nodes, "文档资料", bindings, endpoints, budget) == [
        ("sft", "generation")]
    assert _missing_budget_prices(nodes, "文档资料", bindings, endpoints,
                                  {"max_total_usd": 5, "hard_stop": False}) == []


def test_budgeted_run_pins_rates_so_a_queued_call_cannot_adopt_changed_prices(
        tmp_path, monkeypatch):
    _config(tmp_path, prices={"input_per_1m_usd": 0.25,
                              "output_per_1m_usd": 1.0})
    pin = llm.snapshot_backend_endpoint(tmp_path, "provider", "example")
    assert pin["version"] == 2
    assert pin["prices"] == {"input_per_1m_usd": 0.25,
                             "output_per_1m_usd": 1.0}
    changed = yaml.safe_load((tmp_path / "configs/backends.yaml").read_text(encoding="utf-8"))
    changed["backends"]["provider"]["prices"]["input_per_1m_usd"] = 0.01
    (tmp_path / "configs/backends.yaml").write_text(yaml.safe_dump(changed), encoding="utf-8")
    constructed = []
    monkeypatch.setattr(llm, "ChatClient", lambda **kwargs: constructed.append(kwargs))
    with pytest.raises(ValueError, match="workflow_endpoint_changed_create_new_run"):
        llm.load_backend(tmp_path, backend="provider", model="example",
                         allow_global_endpoint_override=False, expected_endpoint_pin=pin)
    assert constructed == []


def test_malformed_endpoint_pin_is_rejected_without_calling_provider(tmp_path, monkeypatch):
    _config(tmp_path, prices={"input_per_1m_usd": 0.25,
                              "output_per_1m_usd": 1.0})
    constructed = []
    monkeypatch.setattr(llm, "ChatClient", lambda **kwargs: constructed.append(kwargs))
    with pytest.raises(ValueError, match="workflow_endpoint_pin_invalid"):
        llm.load_backend(tmp_path, backend="provider", model="example",
                         allow_global_endpoint_override=False, expected_endpoint_pin="bad")
    assert constructed == []
