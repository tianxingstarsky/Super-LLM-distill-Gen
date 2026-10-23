"""One workflow client for Chat Completions, Responses, and Anthropic Messages."""
from __future__ import annotations

import json
import hashlib
import math
import os
import pathlib
import time
import uuid
from contextlib import ExitStack
from typing import Any, Dict, List
from urllib.parse import urlsplit

import yaml

from lib.domain.backend_config import (validate_backend_url, validate_credential_reference,
                                       validate_token_prices)
from lib.model_protocols import (ANTHROPIC_DEFAULT_MAX_TOKENS, anthropic_request,
                                 anthropic_text, response_text, responses_input,
                                 reasoning_request, validate_api_format)
from lib.domain.model_reasoning import validate_reasoning
from lib.model_streaming import ModelStreamError, StreamCallback, consume_stream
from lib.model_response_details import commit_response_details, extract_response_details
from lib.model_reasoning_prefill import DEEPSEEK_PREFILL_BASE_URL, prepare_reasoning_prefill
from lib.model_request_reliability import (
    ModelJSONError, ModelRequestError, RequestDiagnostics, SERVICE_COOLDOWNS, classify_request_error,
    rejected_before_generation, retry_delay,
)

DEFAULT_NO_PROXY = "127.0.0.1,localhost"
# A priced hard-stop request without an explicit model context is constrained
# to the same conservative default used by workflow node bindings.
DEFAULT_BUDGET_CONTEXT_TOKENS = 131_072
DEFAULT_BUDGET_OUTPUT_TOKENS = 32_768
# The read limit is inactivity, not an overall generation deadline: a long
# token stream keeps its slot while it continues to make progress.
MODEL_CONNECT_TIMEOUT_SECONDS = 15.0
MODEL_READ_IDLE_TIMEOUT_SECONDS = 600.0
MODEL_WRITE_TIMEOUT_SECONDS = 60.0
MODEL_POOL_TIMEOUT_SECONDS = 15.0


from lib.contexts.models.domain.json_response import parse_json_robust


def chat_json(
    client: "ChatClient",
    messages: List[Dict[str, Any]],
    temperature: float = 0.2,
    retries: int = 3,
    thinking: bool | None = None,
    max_tokens: int | None = None,
    on_stream: StreamCallback | None = None,
    allow_reasoning_fallback: bool = True,
    response_details: dict | None = None,
    reasoning_prefix: str | None = None,
) -> Dict[str, Any]:
    """Compatibility entry for the structured response application use case."""
    from lib.bootstrap.model_requests import json_response_request
    return json_response_request(client).execute(messages, temperature=temperature,
        retries=retries, thinking=thinking, max_tokens=max_tokens, on_stream=on_stream,
        allow_reasoning_fallback=allow_reasoning_fallback, response_details=response_details,
        reasoning_prefix=reasoning_prefix)


from lib.contexts.models.domain.request_errors import BudgetExceeded


class BudgetGuard:
    """Cross-process budget with durable, live-locked request reservations.

    A reservation remains in budget.json while its own OS file lock is held.
    After a process dies the lock is released, so the next admission charges
    that stale reservation at its full ceiling without expiring an active call.
    """

    def __init__(self, root: pathlib.Path, limit_usd: float, hard_stop: bool = True):
        self.path = pathlib.Path(root) / "data" / "output" / "budget.json"
        self.limit = float(limit_usd)
        if not math.isfinite(self.limit) or self.limit < 0:
            raise ValueError("invalid_budget_limit")
        self.hard_stop = hard_stop
        self.spent = 0.0
        self._held_reservations = {}
        if self.path.exists():
            try:
                self.spent = float(json.loads(self.path.read_text(encoding="utf-8")).get("spent_usd", 0.0))
            except (json.JSONDecodeError, OSError):
                self.spent = 0.0

    def _state(self) -> dict:
        state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(state, dict):
            raise ValueError("invalid_budget_state")
        spent = float(state.get("spent_usd", 0))
        reservations = state.get("reservations", {})
        if (not math.isfinite(spent) or spent < 0 or not isinstance(reservations, dict)
                or any(not isinstance(key, str) or len(key) != 32 or
                       any(char not in "0123456789abcdef" for char in key) or
                       type(value) not in (int, float) or not math.isfinite(value) or value < 0
                       for key, value in reservations.items())):
            raise ValueError("invalid_budget_state")
        self.spent = spent
        state["reservations"] = reservations
        return state

    def _reservation_path(self, token: str) -> pathlib.Path:
        return self.path.with_name(f".{self.path.name}.{token}.lock")

    def _prune_stale(self, state: dict) -> bool:
        from filelock import FileLock, Timeout

        changed = False
        for token in tuple(state["reservations"]):
            lock_path = self._reservation_path(token)
            probe = FileLock(str(lock_path), timeout=0)
            try:
                probe.acquire()
            except Timeout:
                continue
            else:
                probe.release()
                # A dead process may have sent a billable request before it
                # crashed. Charge its full reserved ceiling; only a normal
                # settlement can release unused capacity.
                state["spent_usd"] = float(state.get("spent_usd", 0)) + state["reservations"].pop(token)
                self.spent = state["spent_usd"]
                lock_path.unlink(missing_ok=True)
                changed = True
        return changed

    def check(self) -> None:
        from filelock import FileLock

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=30):
            state = self._state()
            changed = self._prune_stale(state)
            if changed:
                from lib.io_utils import atomic_json
                atomic_json(self.path, state)
            if self.hard_stop and (state.get("budget_bound_exceeded") or
                                   self.spent + math.fsum(state["reservations"].values()) >= self.limit):
                raise BudgetExceeded("Budget exhausted; no request sent")

    def reserve(self, amount: float) -> str:
        """Atomically admit one priced request; returns its durable token."""
        from filelock import FileLock
        from lib.io_utils import atomic_json

        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("invalid_budget_reservation")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        lock_path = self._reservation_path(token)
        live_lock = FileLock(str(lock_path), timeout=0)
        live_lock.acquire()
        try:
            with FileLock(str(self.path) + ".lock", timeout=30):
                state = self._state()
                changed = self._prune_stale(state)
                reserved = math.fsum(state["reservations"].values())
                if (self.hard_stop and (state.get("budget_bound_exceeded") or
                                        self.spent + reserved + amount > self.limit)):
                    if changed:
                        atomic_json(self.path, state)
                    raise BudgetExceeded("Budget exhausted; no request sent")
                state["reservations"][token] = amount
                state["limit_usd"] = self.limit
                atomic_json(self.path, state)
            self._held_reservations[token] = live_lock
            return token
        except BaseException:
            live_lock.release()
            lock_path.unlink(missing_ok=True)
            raise

    def settle(self, token: str, amount: float) -> None:
        """Record actual cost and release capacity, including on failed calls."""
        from filelock import FileLock
        from lib.io_utils import atomic_json

        if not math.isfinite(amount) or amount < 0:
            raise ValueError("invalid_budget_settlement")
        live_lock = self._held_reservations.pop(token)
        try:
            with FileLock(str(self.path) + ".lock", timeout=30):
                state = self._state()
                reserved = state["reservations"].pop(token, None)
                state["spent_usd"] = self.spent + amount
                state["limit_usd"] = self.limit
                if (reserved is None or amount > reserved + 1e-9 or
                        state["spent_usd"] + math.fsum(state["reservations"].values()) > self.limit + 1e-9):
                    # The provider reported more than our configured upper bound,
                    # or an external budget reset removed a live reservation.
                    # Keep the charge and close admission until explicitly reset.
                    state["budget_bound_exceeded"] = True
                atomic_json(self.path, state)
                self.spent = state["spent_usd"]
                if state.get("budget_bound_exceeded"):
                    raise BudgetExceeded("Budget estimate exceeded; future requests stopped")
        finally:
            live_lock.release()
            self._reservation_path(token).unlink(missing_ok=True)

    def add_usd(self, amount: float) -> None:
        from filelock import FileLock
        from lib.io_utils import atomic_json
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("invalid_budget_settlement")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + '.lock', timeout=30):
            state = self._state()
            self._prune_stale(state)
            self.spent += amount
            state.update(spent_usd=self.spent, limit_usd=self.limit)
            if self.hard_stop and self.spent + math.fsum(state["reservations"].values()) > self.limit:
                state["budget_bound_exceeded"] = True
            atomic_json(self.path, state)
        if self.hard_stop and (self.spent >= self.limit or state.get("budget_bound_exceeded")):
            raise BudgetExceeded(
                f"预算上限已到：累计 ${self.spent:.4f} ≥ ${self.limit}（data/output/budget.json 可查看/清零）"
            )

    def save(self) -> None:
        """Persist a legacy manual balance only when no request is in flight."""
        from filelock import FileLock
        from lib.io_utils import atomic_json

        spent = self.spent
        if not math.isfinite(spent) or spent < 0:
            raise ValueError("invalid_budget_state")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=30):
            state = self._state()
            self._prune_stale(state)
            if state["reservations"]:
                raise BudgetExceeded("Budget has active requests; cannot save balance")
            if spent < self.spent:
                raise BudgetExceeded("Budget balance reduction requires an audited reset")
            state.update(spent_usd=spent, limit_usd=self.limit)
            if self.hard_stop and spent > self.limit:
                state["budget_bound_exceeded"] = True
            atomic_json(self.path, state)
            self.spent = spent

    def reset(self, caller: str) -> float:
        """Clear completed charges only after all live requests have settled."""
        from filelock import FileLock
        from lib.io_utils import atomic_json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=30):
            state = self._state()
            self._prune_stale(state)
            if state["reservations"]:
                raise BudgetExceeded("budget_reset_in_flight")
            previous = self.spent
            atomic_json(self.path, {"spent_usd": 0.0, "limit_usd": self.limit,
                                    "reset_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "reset_by": caller})
            self.spent = 0.0
            return previous


class ChatClient:
    """Text client with bounded retries, normalized output and token accounting."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        price_input_per_1m: float = 0.0,
        price_output_per_1m: float = 0.0,
        budget: BudgetGuard | None = None,
        api_format: str = "chat",
        context_window_tokens: int | None = None,
        additional_budget: BudgetGuard | None = None,
        scheduler=None,
        cancel_check=None,
        reasoning: dict | None = None,
        model_info: dict | None = None,
        network_route: str = "system",
    ):
        # 本地端点绕代理（spike 报告 F2）：httpx 在 OpenAI 客户端构造时快照代理
        # 环境变量，必须在构造前设置；构造后再 setdefault 对本客户端无效。
        from lib.domain.model_network import validate_network_route
        from lib.model_network import sdk_network_options
        self.network_route = validate_network_route(network_route)
        if self.network_route == "system":
            os.environ.setdefault("NO_PROXY", DEFAULT_NO_PROXY)
            os.environ.setdefault("no_proxy", DEFAULT_NO_PROXY)
        self.api_format = validate_api_format(api_format)
        self.base_url = base_url
        self.reasoning = validate_reasoning(reasoning)
        self.model_info = model_info or {}
        if context_window_tokens is not None and (type(context_window_tokens) is not int or context_window_tokens <= 0):
            raise ValueError("context_window_tokens must be a positive integer")
        self.context_window_tokens = context_window_tokens
        if self.api_format == "anthropic":
            from anthropic import Anthropic, Timeout

            self.client = Anthropic(base_url=base_url, api_key=api_key, max_retries=0,
                                    timeout=Timeout(MODEL_READ_IDLE_TIMEOUT_SECONDS,
                                        connect=MODEL_CONNECT_TIMEOUT_SECONDS,
                                        write=MODEL_WRITE_TIMEOUT_SECONDS, pool=MODEL_POOL_TIMEOUT_SECONDS),
                                    **sdk_network_options(self.network_route, api_format=self.api_format))
        else:
            from openai import OpenAI, Timeout  # delayed import keeps offline domain tests lightweight

            self.client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                                 timeout=Timeout(MODEL_READ_IDLE_TIMEOUT_SECONDS,
                                     connect=MODEL_CONNECT_TIMEOUT_SECONDS,
                                     write=MODEL_WRITE_TIMEOUT_SECONDS, pool=MODEL_POOL_TIMEOUT_SECONDS),
                                 **sdk_network_options(self.network_route, api_format=self.api_format))
        self.model = model
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        self.price_input = price_input_per_1m
        self.price_output = price_output_per_1m
        if any(not math.isfinite(price) or price < 0 for price in (self.price_input, self.price_output)):
            raise ValueError("invalid_model_token_price")
        self.budget = budget
        self.additional_budget = additional_budget
        self.scheduler = scheduler
        self.cancel_check = cancel_check
        self._service_identity = SERVICE_COOLDOWNS.identity(base_url, api_key)
        # response_format 能力探测结果：None=未知 / True=支持 / False=不支持。
        # 首次 json_mode 调用被 API 拒绝后置 False，同会话后续自动降级为
        # "提示词要求 JSON + 容错解析"路径，不再硬重试（分层降级 L1→L3）。
        self.json_supported: bool | None = None

    def _check_context(self, messages: List[Dict[str, Any]], max_tokens: int | None,
                       *, context_limit: int | None = None) -> None:
        """Conservative text-only preflight; providers remain the final token authority."""
        context_limit = self.context_window_tokens if context_limit is None else context_limit
        if context_limit is None:
            return
        output = max_tokens or (ANTHROPIC_DEFAULT_MAX_TOKENS if self.api_format == "anthropic" else 0)
        if output >= context_limit:
            raise ValueError("max_output_tokens_exceeds_context_window")
        # UTF-8 bytes upper-bound byte-level text tokenization. Image payloads
        # have provider-specific token costs, so do not miscount base64 bytes.
        text_parts: list[str] = []
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                text_parts.extend(part.get("text", "") for part in content
                                  if isinstance(part, dict) and isinstance(part.get("text"), str))
            if message.get("tool_calls"):
                text_parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))
            if message.get("prefix") is True:
                text_parts.append(message["reasoning_content"])
        tool_overhead = 1024 if any(message.get("role") == "tool" for message in messages) else 0
        if sum(len(part.encode("utf-8")) for part in text_parts) + tool_overhead + output > context_limit:
            raise ValueError("model_context_window_exceeded")

    def _budget_request_bound(self, messages: List[Dict[str, Any]],
                              max_tokens: int | None) -> tuple[int | None, float]:
        """Reserve a full input context plus the explicit output ceiling.

        The configured context must upper-bound provider-billed input tokens.
        Unknown multimodal tokenization requires an explicit context setting.
        A provider reporting usage beyond either bound closes future admission.
        """
        if not any(guard.hard_stop for guard in self._budgets()) or not (self.price_input or self.price_output):
            self._check_context(messages, max_tokens)
            return max_tokens, 0.0
        context = self.context_window_tokens or DEFAULT_BUDGET_CONTEXT_TOKENS
        output = max_tokens or (ANTHROPIC_DEFAULT_MAX_TOKENS if self.api_format == "anthropic"
                                else DEFAULT_BUDGET_OUTPUT_TOKENS)
        if self.context_window_tokens is None and any(
                not isinstance(message.get("content", ""), str) for message in messages):
            raise ValueError("budget_multimodal_context_required")
        self._check_context(messages, output, context_limit=context)
        return output, (context * self.price_input + output * self.price_output) / 1e6

    def _budgets(self) -> list[BudgetGuard]:
        """Apply both global and task limits, charging shared paths only once."""
        unique = {}
        for guard in (self.budget, getattr(self, "additional_budget", None)):
            if guard is None:
                continue
            identity = os.path.normcase(str(guard.path.absolute()))
            current = unique.get(identity)
            if current is None or (guard.hard_stop and
                    (not current.hard_stop or guard.limit < current.limit)):
                unique[identity] = guard
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _settle_all(reservations: list[tuple[BudgetGuard, str]], amount: float) -> None:
        """Settle every ledger even when one detects an overrun."""
        first_error = None
        pending, reservations[:] = list(reservations), []
        for guard, token in pending:
            try:
                guard.settle(token, amount)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _request(self, messages: List[Dict[str, Any]], *, max_tokens: int | None,
                 temperature: float, thinking: bool | None, json_mode: bool,
                 on_stream: StreamCallback | None = None,
                 allow_reasoning_fallback: bool = True,
                 response_details: dict | None = None,
                 reasoning_prefix: str | None = None,
                 cancel_check=None) -> tuple[str, int, int]:
        if response_details is not None:
            response_details.clear()
            allow_reasoning_fallback = False
        messages, prefill_metadata = prepare_reasoning_prefill(messages, reasoning_prefix,
            api_format=self.api_format, base_url=getattr(self, "base_url", ""),
            reasoning=getattr(self, "reasoning", None), thinking=thinking)
        capture_details = response_details is not None or reasoning_prefix is not None
        transport_diagnostics = RequestDiagnostics()
        def create_request(create, kwargs):
            transport_diagnostics.start()
            try:
                response = self._create(create, kwargs)
            except Exception as error:
                transport_diagnostics.failure(error, "headers")
                raise
            transport_diagnostics.received(response, streaming=bool(kwargs.get("stream")))
            return response
        if reasoning_prefix is not None:
            allow_reasoning_fallback = False
        replayed_tools = any(message.get("role") == "tool" for message in messages)
        def get_field(value, name, default=None):
            return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)
        def reject_tool_action(response, *, event=False):
            unexpected = False
            if self.api_format == "chat":
                for choice in get_field(response, "choices", []) or []:
                    body = get_field(choice, "delta" if event else "message")
                    unexpected = unexpected or bool(get_field(body, "tool_calls") or get_field(body, "function_call"))
            elif self.api_format == "responses":
                kind = get_field(response, "type", "")
                item = get_field(response, "item")
                if event:
                    unexpected = ("function_call_arguments" in kind
                                  or get_field(item, "type", "").endswith("_call"))
                    response = get_field(response, "response")
                unexpected = unexpected or any(get_field(item, "type", "").endswith("_call")
                    for item in get_field(response, "output", []) or [])
            elif self.api_format == "anthropic":
                unexpected = (get_field(get_field(response, "content_block"), "type") == "tool_use"
                              if event else any(get_field(block, "type") == "tool_use"
                                                for block in get_field(response, "content", []) or []))
            if unexpected:
                from lib.model_streaming import ModelStreamError
                raise ModelStreamError("model_unexpected_tool_call")
        def stream_result(response):
            stream = response
            if replayed_tools:
                class TextOnlyReplayStream:
                    @property
                    def response(self):
                        return getattr(response, "response", None)
                    def __iter__(self):
                        for event in response:
                            reject_tool_action(event, event=True)
                            yield event
                    def close(self):
                        response.close()
                stream = TextOnlyReplayStream()
            request_details = {}
            def emit(event):
                transport_diagnostics.output(event)
                on_stream(event)
            try:
                result = consume_stream(stream, self.api_format, emit,
                    allow_reasoning_fallback=allow_reasoning_fallback,
                    cancel_check=cancel_check if cancel_check is not None else self.cancel_check,
                    **({"response_details": request_details} if capture_details else {}))
            except Exception as error:
                transport_diagnostics.failure(error, "stream")
                raise
            commit_response_details(response_details, {**request_details, **prefill_metadata})
            return result
        def full_result(response, result, stop_reason, allowed):
            if replayed_tools:
                reject_tool_action(response)
            details = (extract_response_details(response, self.api_format)
                       if capture_details else {})
            result = self._full_result(result, on_stream, stop_reason, allowed,
                                      verify_complete=capture_details, details=details)
            commit_response_details(response_details, {**details, **prefill_metadata})
            return result
        choice = getattr(self, "reasoning", {"mode": "auto"})
        explicit_reasoning = choice.get("mode", "auto") != "auto"
        # Preserve the old per-call opt-out, but its absence must never turn
        # thinking off. Node choices take priority over legacy call defaults.
        legacy_disable = not explicit_reasoning and thinking is False and self.api_format == "chat"
        reasoning_kwargs = reasoning_request(choice, api_format=self.api_format,
            base_url=getattr(self, "base_url", ""), model_info=getattr(self, "model_info", {}),
            max_tokens=max_tokens or (ANTHROPIC_DEFAULT_MAX_TOKENS if self.api_format == "anthropic" else None))
        def check_reasoning_rejection(error):
            if explicit_reasoning and any(_is_parameter_unsupported(error, parameter) for parameter in
                    ("thinking", "reasoning_effort", "reasoning.effort", "output_config", "budget_tokens")):
                raise ValueError("model_reasoning_parameter_unsupported") from None
        if self.api_format == "anthropic":
            kwargs = anthropic_request(messages, json_mode=json_mode)
            kwargs.update(model=self.model, temperature=temperature,
                          max_tokens=max_tokens or ANTHROPIC_DEFAULT_MAX_TOKENS)
            kwargs.update(reasoning_kwargs)
            if choice.get("mode") == "enabled":
                # Anthropic thinking APIs require their own sampling default.
                kwargs.pop("temperature", None)
            if on_stream and getattr(self, "stream_supported", None) is not False:
                kwargs["stream"] = True
            try:
                response = create_request(self.client.messages.create, kwargs)
            except Exception as error:
                check_reasoning_rejection(error)
                if not _is_temperature_unsupported(error):
                    raise
                kwargs.pop("temperature")
                response = create_request(self.client.messages.create, kwargs)
            if on_stream and kwargs.get("stream"):
                return stream_result(response)
            usage = getattr(response, "usage", None)
            return full_result(response, (anthropic_text(response), getattr(usage, "input_tokens", 0) or 0,
                                          getattr(usage, "output_tokens", 0) or 0),
                               getattr(response, "stop_reason", None), {"end_turn", "stop_sequence"})
        if self.api_format == "responses":
            kwargs: Dict[str, Any] = {"model": self.model, "input": responses_input(messages),
                                      "temperature": temperature, "store": False}
            kwargs.update(reasoning_kwargs)
            if replayed_tools:
                kwargs["tool_choice"] = "none"
            if max_tokens is not None:
                kwargs["max_output_tokens"] = max_tokens
            if json_mode and self.json_supported is not False:
                kwargs["text"] = {"format": {"type": "json_object"}}
            if on_stream and getattr(self, "stream_supported", None) is not False:
                kwargs["stream"] = True
            try:
                response = create_request(self.client.responses.create, kwargs)
            except Exception as error:
                check_reasoning_rejection(error)
                if not _is_temperature_unsupported(error):
                    raise
                kwargs.pop("temperature")
                response = create_request(self.client.responses.create, kwargs)
            if json_mode and self.json_supported is None:
                self.json_supported = True
            if on_stream and kwargs.get("stream"):
                return stream_result(response)
            usage = getattr(response, "usage", None)
            return full_result(response, (response_text(response), getattr(usage, "input_tokens", 0) or 0,
                                          getattr(usage, "output_tokens", 0) or 0),
                               getattr(response, "status", None), {"completed"})
        from lib.model_protocols import _paired_tools
        _paired_tools(messages)
        kwargs = {"model": self.model, "messages": messages, "temperature": temperature}
        kwargs.update(reasoning_kwargs)
        create_completion = self.client.chat.completions.create
        if reasoning_prefix is not None:
            # The SDK copy shares the configured credentials and HTTP pool.
            # Keep the original URL, model identity and scheduling pool intact.
            try:
                create_completion = self.client.with_options(
                    base_url=DEEPSEEK_PREFILL_BASE_URL).chat.completions.create
            except (AttributeError, TypeError):
                raise ValueError("model_reasoning_prefill_transport_unsupported") from None
            kwargs["extra_body"] = {**kwargs.get("extra_body", {}),
                                    "thinking": {"type": "enabled"}}
        if replayed_tools:
            kwargs["tool_choice"] = "none"
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        # DeepSeek beta rejects JSON response_format combined with prefix.
        # Keep the caller's JSON contract/parser without weakening the prefix;
        # this special request says nothing about ordinary JSON capability.
        if json_mode and reasoning_prefix is None and self.json_supported is not False:
            kwargs["response_format"] = {"type": "json_object"}
        if legacy_disable:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        if on_stream and getattr(self, "stream_supported", None) is not False:
            kwargs.update(stream=True, stream_options={"include_usage": True})
        for _ in range(5):
            try:
                response = create_request(create_completion, kwargs)
                break
            except Exception as error:
                if reasoning_prefix is not None and getattr(error, "status_code", None) in {400, 404, 405, 422}:
                    # A rejected beta request must not lose its prefix or be
                    # retried as an ordinary dialogue/JSON fallback.
                    raise ValueError("model_reasoning_prefill_rejected") from None
                check_reasoning_rejection(error)
                if "max_tokens" in kwargs and _is_parameter_unsupported(error, "max_tokens"):
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                elif "temperature" in kwargs and _is_temperature_unsupported(error):
                    kwargs.pop("temperature")
                elif legacy_disable and "extra_body" in kwargs and _is_parameter_unsupported(error, "thinking"):
                    kwargs.pop("extra_body")
                elif "stream_options" in kwargs and _is_parameter_unsupported(error, "stream_options"):
                    kwargs.pop("stream_options")
                else:
                    raise
        else:
            raise RuntimeError("chat parameter negotiation failed")
        if json_mode and reasoning_prefix is None and self.json_supported is None:
            self.json_supported = True
        if on_stream and kwargs.get("stream"):
            return stream_result(response)
        usage = getattr(response, "usage", None)
        message = response.choices[0].message
        content = (message.content or "").strip()
        if not content and allow_reasoning_fallback:
            reasoning = getattr(message, "reasoning_content", None)
            if isinstance(reasoning, str):
                content = reasoning.strip()
        return full_result(response, (content, getattr(usage, "prompt_tokens", 0) or 0,
                                      getattr(usage, "completion_tokens", 0) or 0),
                           getattr(response.choices[0], "finish_reason", None), {"stop"})

    def _create(self, create, kwargs):
        """Negotiate only an explicit pre-stream capability rejection."""
        try:
            return create(**kwargs)
        except Exception as error:
            status = getattr(error, "status_code", None)
            if (kwargs.get("stream") and status in {400, 422}
                    and "stream_options" not in str(error).lower()
                    and _is_parameter_unsupported(error, "stream")):
                kwargs.pop("stream")
                kwargs.pop("stream_options", None)
                self.stream_supported = False
                return create(**kwargs)
            raise

    @staticmethod
    def _full_result(result, on_stream, stop_reason, allowed, *, verify_complete=False, details=None):
        if on_stream or verify_complete:
            from lib.model_streaming import ModelStreamError
            if stop_reason not in allowed:
                raise ModelStreamError("model_stream_incomplete")
        if on_stream:
            for field in ("reasoning_content", "reasoning_summary"):
                if (details or {}).get(field):
                    on_stream({"type": "delta", "channel": "reasoning", "text": details[field]})
            if result[0]:
                on_stream({"type": "delta", "channel": "text", "text": result[0]})
            on_stream({"type": "response_completed", "prompt_tokens": result[1],
                       "completion_tokens": result[2]})
        return result

    def tool_turn(self, messages, tools, *, max_tokens=1024, temperature=0.7, retries=3,
                  response_details=None, on_stream=None, tool_choice="auto"):
        """One admitted function-calling turn; callers execute and record tools."""
        from lib.model_tool_calls import tool_turn
        return tool_turn(self, messages, tools, max_tokens=max_tokens,
                         temperature=temperature, retries=retries,
                         response_details=response_details, on_stream=on_stream,
                         tool_choice=tool_choice)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int | None = 1024,
        temperature: float = 0.7,
        retries: int = 3,
        thinking: bool | None = None,
        json_mode: bool = False,
        on_stream: StreamCallback | None = None,
        allow_reasoning_fallback: bool = True,
        response_details: dict | None = None,
        reasoning_prefix: str | None = None,
    ) -> str:
        """Compatibility entry for the admitted text request application use case."""
        from lib.bootstrap.model_requests import model_request
        return model_request(self).execute(messages, max_tokens=max_tokens,
            temperature=temperature, retries=retries, thinking=thinking, json_mode=json_mode,
            on_stream=on_stream, allow_reasoning_fallback=allow_reasoning_fallback,
            response_details=response_details, reasoning_prefix=reasoning_prefix)


def _is_format_unsupported(err: Exception) -> bool:
    """判断异常是否为 'response_format 不支持' 类（400/BadRequest + 关键字）。"""
    if getattr(err, "status_code", None) not in {None, 400, 422}:
        return False
    text = str(err).lower()
    rejection = any(value in text for value in ("unsupported", "not supported", "unavailable",
                                                "invalid", "unknown parameter"))
    if rejection and any(value in text for value in ("response_format", "text.format", "json_object")):
        return True
    if "badrequest" in text and ("unsupported" in text or "unavailable" in text or "not supported" in text):
        return True
    return False


def _is_temperature_unsupported(err: Exception) -> bool:
    return _is_parameter_unsupported(err, "temperature")


def _is_parameter_unsupported(err: Exception, parameter: str) -> bool:
    if getattr(err, "status_code", None) not in {None, 400, 422}:
        return False
    text = str(err).lower()
    return parameter in text and any(value in text for value in
                                     ("unsupported", "not supported", "unavailable", "unknown parameter",
                                      "unexpected keyword argument"))


def _configured_backends(root: pathlib.Path) -> Dict[str, Any]:
    """Read the same effective configuration for snapshots and requests."""
    local = root / "configs" / "backends.local.yaml"
    base = root / "configs" / "backends.yaml"
    cfg: Dict[str, Any] = {}
    if base.exists():
        cfg = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    if local.exists():
        local_cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        merged = dict(cfg.get("backends", {}))
        for key, value in local_cfg.get("backends", {}).items():
            base_backend = merged.get(key, {})
            merged[key] = {**base_backend, **value, "prices": {**base_backend.get("prices", {}),
                                                               **value.get("prices", {})}}
        cfg["backends"] = merged
        cfg.update({k: v for k, v in local_cfg.items() if k != "backends"})
    return cfg


def _configured_token_prices(backend: dict, budget: dict | None = None) -> dict[str, float]:
    """Optional historical rates never control admission to a provider."""
    raw = backend.get("prices")
    if not raw:
        return {"input_per_1m_usd": 0.0, "output_per_1m_usd": 0.0}
    try:
        return validate_token_prices(raw)
    except ValueError:
        # An obsolete or partial billing field is not a model connection error.
        return {"input_per_1m_usd": 0.0, "output_per_1m_usd": 0.0}


def _endpoint_pin(name: str, model: str, backend: dict,
                  prices: dict[str, float] | None = None) -> dict:
    """Keep only safe transport metadata and a digest of those public fields."""
    if not isinstance(backend, dict) or not backend.get("base_url"):
        raise ValueError("workflow_node_service_not_configured")
    try:
        base_url = validate_backend_url(backend["base_url"])
        api_format = validate_api_format(backend.get("api_format", "chat"))
    except ValueError:
        raise ValueError("workflow_endpoint_url_or_protocol_invalid") from None
    models = backend.get("models") or []
    if not isinstance(models, list) or any(not isinstance(item, str) for item in models):
        raise ValueError("workflow_endpoint_models_invalid")
    default_env = "ANTHROPIC_API_KEY" if api_format == "anthropic" else "OPENAI_API_KEY"
    if backend.get("api_key"):
        credential_ref = "inline"
    else:
        try:
            env_name = validate_credential_reference(
                backend.get("api_key_env") if "api_key_env" in backend else default_env)
        except ValueError:
            raise ValueError("workflow_endpoint_credential_ref_invalid") from None
        credential_ref = "env:" + env_name
    parsed = urlsplit(base_url)
    payload = {"version": 1, "backend": name, "model": model,
               "endpoint_origin": parsed.scheme + "://" + parsed.netloc,
               "base_url_sha256": hashlib.sha256(base_url.encode("utf-8")).hexdigest(),
               "api_format": api_format, "models": sorted(set(models)),
               "credential_ref": credential_ref}
    if prices is not None:
        payload.update(version=2, prices=prices)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**payload, "config_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest()}


def snapshot_backend_endpoint(root: pathlib.Path, backend: str, model: str) -> dict:
    """Pin a node's selected service before its queued run is created."""
    config = _configured_backends(pathlib.Path(root))
    selected = (config.get("backends") or {}).get(backend)
    prices = None
    if isinstance(selected, dict) and selected.get("prices"):
        # Retain version-2 identity for existing endpoint snapshots. Rates are
        # optional metadata; neither their absence nor old budgets block calls.
        try:
            prices = validate_token_prices(selected["prices"])
        except ValueError:
            prices = None
    return _endpoint_pin(backend, model, selected, prices)


def load_backend(
    root: pathlib.Path,
    backend: str | None = None,
    model: str | None = None,
    judge: bool = False,
    base_url: str | None = None,
    role: str | None = None,
    allow_global_endpoint_override: bool = True,
    context_window_tokens: int | None = None,
    expected_endpoint_pin: dict | None = None,
    additional_budget: BudgetGuard | None = None,
    reasoning: dict | None = None,
) -> tuple[ChatClient, str]:
    """按 backends.local.yaml（覆盖）→ backends.yaml 顺序加载后端配置。

    解析优先级：显式 backend/model/base_url > 角色专属环境变量 > role 槽位 > 默认配置。
    role ∈ {generation, judge, jev, vision, refine, simulate, translation}。JEV 可用 JEV_BACKEND/JEV_MODEL 环境变量独立配置；
    judge=True 等价 role='judge'。base_url 自定义端点（本地 Ollama/llama.cpp 等）
    时 api_key 走 OPENAI_API_KEY 环境变量。
    旧配置中的 budget 不再限制请求；服务额度由上游平台管理。"""
    cfg = _configured_backends(root)

    if judge and role is None:
        role = "judge"
    if role and base_url is None:
        slot = (cfg.get("model_roles") or {}).get(role) or {}
        role_backend = os.environ.get(f"{role.upper()}_BACKEND")
        role_model = os.environ.get(f"{role.upper()}_MODEL")
        backend = backend or role_backend or slot.get("backend") or (cfg.get("judge_backend") if role == "judge" else None)
        global_model = os.environ.get("LLM_MODEL") if role != "jev" else None
        model = model or role_model or global_model or slot.get("model") or (cfg.get("judge_model") if role == "judge" else None)
    if judge and backend is None and base_url is None:
        backend = cfg.get("judge_backend")
        model = model or cfg.get("judge_model")
    if base_url:
        # 自定义端点：无配置条目，key 走 OPENAI_API_KEY 环境变量
        b = {"base_url": base_url, "api_key_env": "OPENAI_API_KEY", "models": []}
    else:
        name = backend or cfg.get("default_backend", "deepseek")
        b = (cfg.get("backends") or {}).get(name) or {}
        if not allow_global_endpoint_override and not b.get("base_url"):
            raise ValueError("workflow_node_service_not_configured")
    # 环境变量级全局覆盖（任意命令的临时操作空间，无需加 CLI 参数）
    if allow_global_endpoint_override and not base_url and os.environ.get("LLM_BASE_URL"):
        b = {"base_url": os.environ["LLM_BASE_URL"], "api_key_env": "OPENAI_API_KEY", "models": []}
    role_model = os.environ.get(f"{role.upper()}_MODEL") if role else None
    global_model = os.environ.get("LLM_MODEL") if role != "jev" else None
    model = model or role_model or global_model or (b.get("models", [""])[0] if b.get("models") else "") or cfg.get("default_model", "")
    prices = _configured_token_prices(b)
    if expected_endpoint_pin is not None:
        if type(expected_endpoint_pin) is not dict:
            raise ValueError("workflow_endpoint_pin_invalid")
        if base_url or allow_global_endpoint_override:
            raise ValueError("workflow_endpoint_pin_invalid")
        try:
            current_pin = _endpoint_pin(
                name, model, b, prices if expected_endpoint_pin.get("version") == 2 else None)
        except ValueError:
            raise ValueError("workflow_endpoint_changed_create_new_run") from None
        if current_pin != expected_endpoint_pin:
            raise ValueError("workflow_endpoint_changed_create_new_run")
    api_format = validate_api_format(b.get("api_format", "chat"))
    default_key_env = "ANTHROPIC_API_KEY" if api_format == "anthropic" else "OPENAI_API_KEY"
    configured_key_env = b.get("api_key_env") if "api_key_env" in b else default_key_env
    api_key = b.get("api_key") or os.environ.get(configured_key_env or "", "")

    reasoning = validate_reasoning(reasoning)
    model_info = {}
    if (reasoning["mode"] != "auto" and not base_url
            and not (allow_global_endpoint_override and os.environ.get("LLM_BASE_URL"))):
        from lib.bootstrap.backends import backend_application
        # Reads the credential-bound discovery cache only; no model request.
        model_info = backend_application(root).get_model_info(name, model)
    client = ChatClient(
        base_url=b.get("base_url", ""),
        api_key=api_key,
        model=model,
        api_format=api_format,
        context_window_tokens=context_window_tokens,
        reasoning=reasoning,
        model_info=model_info,
        network_route=b.get("network_route", "system"),
        price_input_per_1m=float(prices.get("input_per_1m_usd", 0.0)),
        price_output_per_1m=float(prices.get("output_per_1m_usd", 0.0)),
        # Keep the legacy argument callable for old integrations, but product
        # clients must not enforce either a global or per-run dollar ceiling.
        budget=None,
        additional_budget=None,
    )
    from lib.model_request_scheduler import SharedModelScheduler, model_pool_identity
    scheduler = SharedModelScheduler(root, model_pool_identity(b.get("base_url", ""), api_key, model))
    scheduler.synchronize(model)
    client.scheduler = scheduler
    # A declared lack of tool support uses a labelled internal memory message.
    # Unknown capabilities try the real protocol; errors are never disguised as
    # successful tool use, nor silently rewritten into a user-supplied document.
    declaration = (b.get("model_capabilities") or {}).get(model, {})
    client.source_memory_transport = "system" if declaration.get("tools") is False else "tool"
    return client, model
