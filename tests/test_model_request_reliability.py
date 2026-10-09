"""No-network request admission, safe retry and dual-ledger accounting."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from types import SimpleNamespace

import pytest

from lib import llm_client
from lib.llm_client import BudgetExceeded, BudgetGuard, ChatClient
from lib import model_request_reliability as reliability


class ProviderError(Exception):
    def __init__(self, status, *, headers=None, code=None):
        super().__init__("PRIVATE-API-KEY private source contents")
        self.status_code = status
        self.code = code
        self.response = SimpleNamespace(headers=headers or {})


@pytest.fixture(autouse=True)
def fresh_cooldowns(monkeypatch):
    monkeypatch.setattr(llm_client, "SERVICE_COOLDOWNS", reliability.ServiceCooldowns())


def client(tmp_path, *, global_limit=10., task_limit=10.):
    return ChatClient("https://offline.invalid/v1", "PRIVATE-KEY", "offline",
        context_window_tokens=10, budget=BudgetGuard(tmp_path / "global", global_limit),
        additional_budget=BudgetGuard(tmp_path / "task", task_limit),
        price_input_per_1m=50_000, price_output_per_1m=100_000)


def state(guard):
    return json.loads(guard.path.read_text(encoding="utf-8"))


def call(item, **kwargs):
    return item.chat([{"role": "user", "content": "x"}], max_tokens=1, **kwargs)


@pytest.mark.parametrize("status,kind,code", [
    (401, "fatal", "service_authentication_failed"),
    (403, "fatal", "service_permission_denied"),
    (404, "fatal", "service_model_or_endpoint_missing"),
    (422, "fatal", "service_request_rejected"),
    (429, "transient", "service_rate_limited"),
    (408, "transient", "service_temporarily_unavailable"),
    (503, "transient", "service_unavailable"),
])
def test_status_classification_never_contains_provider_text(status, kind, code):
    result = reliability.classify_request_error(ProviderError(status))
    assert result == {"kind": kind, "code": code}
    assert "PRIVATE" not in json.dumps(result)


def test_quota_is_fatal_and_network_timeout_is_transient():
    assert reliability.classify_request_error(ProviderError(429, code="insufficient_quota")) == {
        "kind": "fatal", "code": "service_quota_exhausted"}
    assert reliability.classify_request_error(TimeoutError("PRIVATE"))["kind"] == "transient"
    assert reliability.classify_request_error(ConnectionError("PRIVATE"))["kind"] == "transient"
    assert reliability.classify_request_error(BudgetExceeded("PRIVATE"))["kind"] == "fatal"


@pytest.mark.parametrize("code", sorted(reliability.FATAL_VALIDATION_CODES))
def test_configuration_and_integrity_failures_are_fatal(code):
    assert reliability.classify_request_error(ValueError(code)) == {"kind": "fatal", "code": code}


def test_unknown_invalid_output_does_not_echo_its_message():
    assert reliability.classify_request_error(ValueError("PRIVATE source")) == {
        "kind": "invalid", "code": "model_output_invalid"}


def test_retry_after_is_bounded_and_accepts_http_date(monkeypatch):
    assert reliability.retry_delay(ProviderError(429, headers={"Retry-After": "100000"}), 0) == 60
    assert reliability.retry_delay(ProviderError(429, headers={"retry-after": "2.5"}), 0) == 2.5
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
    assert 18 <= reliability.retry_delay(ProviderError(429, headers={"retry-after": future}), 0) <= 20
    monkeypatch.setattr(reliability.random, "uniform", lambda lower, upper: upper)
    assert reliability.retry_delay(ProviderError(429, headers={"retry-after": "NaN"}), 2) == 2
    assert reliability.retry_delay(ProviderError(429, headers={"retry-after": "invalid"}), 500) == 30


def test_success_settles_global_and_task_once(tmp_path):
    item = client(tmp_path)
    item._request = lambda *a, **kw: ("ok", 1, 1)
    assert call(item) == "ok"
    for guard in item._budgets():
        assert state(guard)["spent_usd"] == pytest.approx(.15)
        assert state(guard)["reservations"] == {}


def test_second_ledger_denial_releases_first_without_a_request(tmp_path):
    item = client(tmp_path, task_limit=.5)
    item._request = lambda *a, **kw: pytest.fail("No provider request after budget denial")
    with pytest.raises(BudgetExceeded):
        call(item)
    assert state(item.budget)["reservations"] == {}
    assert state(item.budget)["spent_usd"] == 0


def test_auth_does_not_retry_or_charge_output(tmp_path, monkeypatch):
    item, calls = client(tmp_path), []
    def fail(*a, **kw):
        calls.append(True)
        raise ProviderError(401)
    item._request = fail
    monkeypatch.setattr(llm_client.time, "sleep", lambda *_: pytest.fail("Fatal errors do not wait"))
    with pytest.raises(reliability.ModelRequestError) as caught:
        call(item)
    assert len(calls) == 1 and caught.value.kind == "fatal"
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__
    for guard in item._budgets():
        assert state(guard)["spent_usd"] == 0
        assert state(guard)["reservations"] == {}


def test_timeout_retries_charge_unknown_usage_and_then_actual_once(tmp_path, monkeypatch):
    item, calls, sleeps = client(tmp_path), [], []
    def request(*a, **kw):
        calls.append(True)
        if len(calls) < 3:
            raise TimeoutError("PRIVATE")
        return "ok", 1, 1
    item._request = request
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    monkeypatch.setattr(reliability.random, "uniform", lambda lower, upper: upper)
    assert call(item) == "ok"
    assert sleeps == [.5, 1.]
    assert len(calls) == 3
    for guard in item._budgets():
        assert state(guard)["spent_usd"] == pytest.approx(1.35)
        assert state(guard)["reservations"] == {}


def test_final_retry_does_not_wait_and_preserves_safe_transient_category(tmp_path, monkeypatch):
    item, sleeps = client(tmp_path), []
    item._request = lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("PRIVATE"))
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    with pytest.raises(reliability.ModelRequestError) as caught:
        call(item, retries=1)
    assert sleeps == [] and caught.value.retryable
    assert caught.value.code == "service_timeout"


def test_overrun_still_settles_every_ledger(tmp_path):
    item = client(tmp_path)
    item._request = lambda *a, **kw: ("ok", 20, 1)
    with pytest.raises(BudgetExceeded):
        call(item)
    for guard in item._budgets():
        assert state(guard)["spent_usd"] == pytest.approx(1.1)
        assert state(guard)["reservations"] == {}
        assert state(guard)["budget_bound_exceeded"]


def test_same_ledger_is_not_charged_twice(tmp_path):
    item = client(tmp_path)
    item.additional_budget = BudgetGuard(tmp_path / "global", 9.)
    item._request = lambda *a, **kw: ("ok", 1, 1)
    assert call(item) == "ok"
    assert len(item._budgets()) == 1
    assert state(item.budget)["spent_usd"] == pytest.approx(.15)


def test_shared_service_cooldown_does_not_block_unrelated_service(monkeypatch):
    cooldowns, sleeps = reliability.ServiceCooldowns(), []
    monkeypatch.setattr(reliability.time, "monotonic", lambda: 100.)
    monkeypatch.setattr(reliability.time, "sleep", sleeps.append)
    one = cooldowns.identity("https://same.invalid/v1", "secret-a")
    two = cooldowns.identity("https://same.invalid/v1", "secret-b")
    cooldowns.defer(one, 5)
    cooldowns.wait(two)
    cooldowns.wait(one)
    assert sleeps == [5]
    assert "secret" not in one
    for index in range(300):
        cooldowns.defer(str(index), 1)
    assert len(cooldowns._until) == reliability.MAX_SHARED_SERVICES


def test_sdk_retry_is_disabled_to_avoid_unaccounted_attempts(tmp_path):
    item = client(tmp_path)
    assert item.client.max_retries == 0
