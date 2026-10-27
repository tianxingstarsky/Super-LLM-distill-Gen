"""Structured response attempts do not retry authentication or cancellation."""
from __future__ import annotations
from .retry_port import RetryDirective, RetryPort
from typing import Any, Dict, List
import json
from .request_ports import JSONResponsePort, StreamCallback
from ..domain.request_errors import ModelJSONError, ModelRequestError
from ..domain.json_response import JSONResponseDecodeError


class RequestJSONResponse:
    def __init__(self, port: JSONResponsePort, *, retry: RetryPort):
        self.port = port
        self.retry = retry

    def execute(
        self,
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
        """Decode JSON, retrying only invalid JSON or completed invisible output."""
        if response_details is not None:
            response_details.clear()
            allow_reasoning_fallback = False
        if reasoning_prefix is not None:
            allow_reasoning_fallback = False
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
            raise ValueError("max_tokens must be a positive integer")
        if type(retries) is not int or not 1 <= retries <= 10:
            raise ValueError("invalid_model_json_attempts")
        request_messages = messages
        previous_invalid_output = None
        def attempt_request(attempt):
            nonlocal request_messages, previous_invalid_output
            attempt_details = {}
            temp = temperature if attempt == 0 else min(temperature, 0.3)
            callback_error = None
            def forward_stream(event):
                nonlocal callback_error
                try:
                    on_stream(event)
                except BaseException as error:
                    callback_error = error
                    raise
            try:
                out = self.port.chat(request_messages, max_tokens=max_tokens, temperature=temp,
                                  thinking=thinking, json_mode=True,
                                  **({"allow_reasoning_fallback": False} if not allow_reasoning_fallback else {}),
                                  **({"response_details": attempt_details} if response_details is not None else {}),
                                  **({"reasoning_prefix": reasoning_prefix} if reasoning_prefix is not None else {}),
                                  **({"on_stream": forward_stream} if on_stream else {}))
            except (ValueError, ModelRequestError) as error:
                if error is callback_error:
                    raise
                missing_visible = (type(error) is ValueError and str(error) == "model_visible_output_missing")
                if isinstance(error, ModelRequestError):
                    missing_visible = error.code == "model_visible_output_missing" and error.kind == "invalid"
                if not missing_visible:
                    raise
                # A completed reasoning-only answer cannot fulfill the JSON task.
                # Retry only this safe code; never retry auth/network/cancellation
                # here or copy the hidden reasoning into the visible JSON body.
                return RetryDirective(ModelJSONError(attempt + 1))
            try:
                result = self.port.parse_json(out)
            except (JSONResponseDecodeError, json.JSONDecodeError) as error:
                if out == previous_invalid_output:
                    # Identical invalid bytes after precise feedback are no
                    # progress. Do not purchase a third blind resampling.
                    raise ModelJSONError(attempt + 1) from None
                previous_invalid_output = out
                diagnostic = ({"line": error.lineno, "column": error.colno, "reason": error.msg}
                              if isinstance(error, json.JSONDecodeError) else
                              {"line": error.line, "column": error.column, "reason": error.reason})
                # Rebuild from the original context, not an ever-growing retry
                # conversation. Never replace a learner/source/system role or
                # turn private provider reasoning into visible model output.
                request_messages = [*messages, {"role": "assistant", "content": out},
                    {"role": "system", "content": (
                        "JSON transport validation failed: " + json.dumps(diagnostic, ensure_ascii=False)
                        + ". Correct the visible JSON syntax in the preceding response. "
                        "Return the complete JSON value, retaining all content, reasoning, numbers and claims. "
                        "Do not add premises, change quality decisions, summarize or omit fields. "
                        "This is parser feedback, not a new learner task or a semantic verdict.")}]
                return RetryDirective(ModelJSONError(attempt + 1))
            # Publication errors are program/storage failures, not invalid
            # model JSON. They must never purchase another model response.
            self.port.commit_details(response_details, attempt_details)
            return result
        return self.retry.run(attempt_request, attempts=retries, wait=lambda delay: None)
