"""Admit a function-calling decision without executing any returned tool."""
from __future__ import annotations
from contextlib import ExitStack
from .request_ports import ToolRequestPort
from ..domain.request_errors import BudgetExceeded, ModelQueueTimeout, ModelRequestError


class ExecuteToolTurn:
    def __init__(self, port: ToolRequestPort):
        self.port = port

    def execute(self, messages, tools, *, max_tokens=1024, temperature=0.7, retries=3,
                  response_details=None, on_stream=None, tool_choice="auto"):
        """Return a complete assistant/tool decision; never execute a function.

        SDK requests are nonstreaming. After the entire response arrives, the
        observer receives each complete text channel once, then completion. These
        events carry ``delivery=complete_response`` and do not simulate token timing.
        """
        port = self.port
        if response_details is not None:
            response_details.clear()
        reservation_usd = port.prepare_request(messages, tools, max_tokens=max_tokens,
            temperature=temperature, retries=retries, tool_choice=tool_choice)
        callback_error = cancel_error = None

        def check_cancel():
            nonlocal cancel_error
            if port.cancel_check:
                try:
                    port.cancel_check()
                except BaseException as error:
                    cancel_error = error
                    raise

        def notify(event):
            nonlocal callback_error
            if on_stream:
                try:
                    on_stream(event)
                except BaseException as error:
                    callback_error = error
                    raise

        failure = {"kind": "invalid", "code": "model_output_invalid"}
        for attempt in range(retries):
            reservations, admission = [], ExitStack()
            request_started = definite_rejection = False
            delay = None
            try:
                check_cancel()
                admission.enter_context(port.admit(check_cancel,
                    (lambda info: notify({"type": "queued", **info})) if on_stream else None))
                check_cancel()
                for guard in port.budget_guards():
                    if reservation_usd:
                        reservations.append((guard, guard.reserve(reservation_usd)))
                    else:
                        guard.check()
                notify({"type": "start", "api_format": port.api_format})
                request_started = True
                response = port.request()
                prompt, output = port.response_usage(response)
                port.account_usage(prompt, output, reservations, reservation_usd)
                # Bill completed provider responses before validating their content.
                result = port.complete_response(response, tool_choice=tool_choice)
                check_cancel()
                details = port.response_details(response)
                reasoning = "".join(details.get(key, "") for key in ("reasoning_content", "reasoning_summary"))
                for channel, text in (("reasoning", reasoning), ("text", result["content"])):
                    if text:
                        notify({"type": "delta", "channel": channel, "text": text,
                                "delivery": "complete_response"})
                notify({"type": "response_completed", "prompt_tokens": prompt, "completion_tokens": output,
                        "content": result["content"], "tool_calls": len(result["tool_calls"]),
                        "delivery": "complete_response"})
                port.commit_details(response_details, details)
                return result
            except BudgetExceeded:
                raise
            except ValueError as error:
                definite_rejection = getattr(error, "definite_rejection", False)
                raise
            except Exception as error:
                if error is callback_error or error is cancel_error:
                    raise
                if isinstance(error, ModelQueueTimeout):
                    raise ModelRequestError(error.code, error.kind, attempt) from None
                definite_rejection = port.rejected_before_generation(error)
                failure = port.classify_error(error)
                if failure["kind"] == "transient":
                    delay = port.retry_delay(error, attempt)
                    if failure["code"] in {"service_rate_limited", "service_unavailable"}:
                        port.defer(delay)
            finally:
                try:
                    if reservations:
                        port.settle_all(reservations,
                            reservation_usd if request_started and not definite_rejection else 0.0)
                finally:
                    admission.close()
            if failure["kind"] != "transient" or attempt + 1 >= retries:
                break
            if failure["code"] not in {"service_rate_limited", "service_unavailable"}:
                port.wait_retry(delay, check_cancel)
        raise ModelRequestError(failure["code"], failure["kind"], attempt + 1) from None
