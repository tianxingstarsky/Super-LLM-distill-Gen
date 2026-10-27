"""Bridge admitted request use cases to SDK clients and shared runtime state."""
from __future__ import annotations
from contextlib import contextmanager

from . import request_runtime
from .telemetry import observe_request, observation_update


class ClientRequestAdmission:
    """Shared lifecycle adapter, bound to one request and one runtime namespace."""
    def __init__(self, client, runtime):
        self.client = client
        self.runtime = runtime
        self._observation = None

    @property
    def api_format(self):
        return self.client.api_format

    @property
    def cancel_check(self):
        return self.client.cancel_check

    @contextmanager
    def admit(self, check_cancel, on_wait):
        if self.client.scheduler is not None:
            with self.client.scheduler.acquire(cancel_check=check_cancel, on_wait=on_wait):
                with observe_request(self.client) as observation:
                    self._observation = observation
                    yield
        else:
            self.runtime.SERVICE_COOLDOWNS.wait(self.client._service_identity,
                **({"cancel_check": check_cancel} if self.client.cancel_check else {}))
            with observe_request(self.client) as observation:
                self._observation = observation
                yield

    def budget_guards(self):
        return self.client._budgets()

    def settle_all(self, reservations, amount):
        return self.client._settle_all(reservations, amount)

    def account_usage(self, prompt_tokens, completion_tokens, reservations, reservation_usd):
        observation_update(self._observation, usage_details={"input": prompt_tokens, "output": completion_tokens})
        self.client.usage["calls"] += 1
        self.client.usage["prompt_tokens"] += prompt_tokens
        self.client.usage["completion_tokens"] += completion_tokens
        if self.client._budgets() and (self.client.price_input or self.client.price_output):
            cost = (prompt_tokens * self.client.price_input + completion_tokens * self.client.price_output) / 1e6
            if reservations:
                # Some compatible endpoints omit usage. Charge the bound
                # rather than silently treating a priced call as free.
                self.client._settle_all(reservations,
                                 cost if prompt_tokens or completion_tokens else reservation_usd)
            else:
                first_error = None
                for guard in self.client._budgets():
                    try:
                        guard.add_usd(cost)
                    except Exception as error:
                        if first_error is None:
                            first_error = error
                if first_error is not None:
                    raise first_error

    def commit_details(self, target, details):
        self.runtime.commit_response_details(target, details)
        observation_update(self._observation, output=details)

    def classify_error(self, error):
        return self.runtime.classify_request_error(error)

    def rejected_before_generation(self, error):
        return self.runtime.rejected_before_generation(error)

    def retry_delay(self, error, attempt):
        return self.runtime.retry_delay(error, attempt)

    def defer(self, delay):
        if self.client.scheduler is not None:
            self.client.scheduler.defer(delay)
        else:
            self.runtime.SERVICE_COOLDOWNS.defer(self.client._service_identity, delay)

    def wait_retry(self, delay, check_cancel):
        if self.client.cancel_check:
            # Chat historically resolves this helper at invocation time.
            from lib.model_request_reliability import cancelable_wait
            wait = getattr(self.runtime, "cancelable_wait", cancelable_wait)
            wait(delay, check_cancel)
        else:
            self.runtime.time.sleep(delay)


class SDKModelRequestAdapter(ClientRequestAdmission):
    def __init__(self, client):
        super().__init__(client, request_runtime)

    @property
    def json_supported(self):
        return self.client.json_supported

    @json_supported.setter
    def json_supported(self, value):
        self.client.json_supported = value

    def prepare_request(self, messages, max_tokens, *, reasoning_prefix, thinking):
        context_messages, _ = self.runtime.prepare_reasoning_prefill(messages, reasoning_prefix,
            api_format=self.client.api_format, base_url=getattr(self.client, "base_url", ""),
            reasoning=getattr(self.client, "reasoning", None), thinking=thinking)
        return self.client._budget_request_bound(context_messages, max_tokens)

    def request(self, messages, **options):
        return self.client._request(messages, **options)

    def is_format_unsupported(self, error):
        return self.runtime._is_format_unsupported(error)


class ClientJSONResponseAdapter:
    def __init__(self, client):
        self.client = client

    def chat(self, messages, **options):
        return self.client.chat(messages, **options)

    def parse_json(self, output):
        return request_runtime.parse_json_robust(output)

    def commit_details(self, target, details):
        request_runtime.commit_response_details(target, details)
