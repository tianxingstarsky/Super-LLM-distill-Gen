"""Structured response attempts do not retry authentication or cancellation."""
from __future__ import annotations
from typing import Any, Dict, List
from .request_ports import JSONResponsePort, StreamCallback
from ..domain.request_errors import ModelJSONError, ModelRequestError


class RequestJSONResponse:
    def __init__(self, port: JSONResponsePort):
        self.port = port

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
        last_err: Exception | None = None
        for attempt in range(retries):
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
                out = self.port.chat(messages, max_tokens=max_tokens, temperature=temp,
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
                last_err = error
                continue
            try:
                result = self.port.parse_json(out)
                self.port.commit_details(response_details, attempt_details)
                return result
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise ModelJSONError(retries) from None
