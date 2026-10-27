"""Complete-response tool protocol adapter; tool execution belongs to callers."""
from __future__ import annotations
import math
from urllib.parse import urlsplit
from . import tool_transport
from .request_adapter import ClientRequestAdmission


class SDKToolRequestAdapter(ClientRequestAdmission):
    def __init__(self, client):
        super().__init__(client, tool_transport)

    def prepare_request(self, messages, tools, *, max_tokens, temperature, retries, tool_choice, strict=False):
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
            raise ValueError("max_tokens must be a positive integer")
        if type(retries) is not int or not 1 <= retries <= 10:
            raise ValueError("invalid_model_request_attempts")
        if type(temperature) not in (int, float) or not math.isfinite(temperature):
            raise ValueError("invalid_model_temperature")
        if not isinstance(tool_choice, str) or tool_choice not in {"auto", "none"}:
            raise ValueError("invalid_model_tool_choice")
        tools = self.runtime._tools(tools)
        self._strict = strict
        if strict:
            if self.client.api_format != "chat" or not all(t["function"].get("strict") is True for t in tools):
                raise ValueError("model_strict_tools_unsupported")
        messages = self.runtime._history(messages, self.client.api_format)
        if self.client.model_info.get("tools") is False:
            raise ValueError("model_tools_unsupported")
        if not strict and urlsplit(self.client.base_url).hostname == "api.deepseek.com" and urlsplit(self.client.base_url).path.rstrip("/") == "/beta":
            raise ValueError("model_tool_turn_native_prefill_unsupported")
        # Legacy chat preflight only counts reasoning used as a beta prefix. This
        # tool channel must additionally charge every historical thought, signature,
        # encrypted item and complete tool schema. Overcount duplicates conservatively.
        accounting = self.runtime.tool_context_accounting(messages, tools)
        request_max, reservation_usd = self.client._budget_request_bound(accounting, max_tokens)
        self._kwargs = self.runtime._request_kwargs(self.client, messages, tools, request_max, temperature, tool_choice=tool_choice)
        self._names = {tool["function"]["name"] for tool in tools}
        return reservation_usd

    def request(self, *, on_stream=None):
        if self._strict:
            from .strict_tool_stream import request_strict_tool_stream
            return request_strict_tool_stream(self.client, self._kwargs, on_stream=on_stream)
        return self.runtime._request(self.client, self._kwargs)

    def response_usage(self, response):
        usage = self.runtime._get(response, "usage")
        prompt = self.runtime._get(usage, "prompt_tokens" if self.client.api_format == "chat" else "input_tokens", 0) or 0
        output = self.runtime._get(usage, "completion_tokens" if self.client.api_format == "chat" else "output_tokens", 0) or 0
        if self.client.api_format == "anthropic":
            prompt += (self.runtime._get(usage, "cache_creation_input_tokens", 0) or 0) + (self.runtime._get(usage, "cache_read_input_tokens", 0) or 0)
        return prompt, output

    def complete_response(self, response, *, tool_choice, recover_arguments=False):
        if self.runtime._get(response, "stream_incomplete"):
            from ..domain.request_errors import ModelStreamError
            raise ModelStreamError("model_stream_incomplete")
        result = self.runtime._assistant(response, self.client.api_format, self._names,
            **({"recover_arguments": True} if recover_arguments else {}))
        if self._strict:
            # The SDK accumulator adds index/parsed_arguments bookkeeping.
            # Keep that in strict_tool_response diagnostics, but replay only
            # protocol fields; argument bytes and native reasoning are unchanged.
            result["tool_calls"] = [{"id": call["id"], "type": call["type"], "function": {
                "name": call["function"]["name"], "arguments": call["function"]["arguments"]}}
                for call in result["tool_calls"]]
        if tool_choice == "none" and result["tool_calls"]:
            raise ValueError("model_unexpected_tool_call")
        return result

    def response_details(self, response):
        details = self.runtime.extract_response_details(response, self.client.api_format)
        if self._strict:
            details["strict_tool_response"] = self.runtime._plain(response)
            details["strict_tool_protocol_version"] = 1
        api, get = self.client.api_format, self.runtime._get
        choices = get(response, "choices", []) or []
        incomplete = (api == "chat" and choices and get(choices[0], "finish_reason") == "length"
                      or api == "responses" and get(response, "status") == "incomplete"
                      or api == "anthropic" and get(response, "stop_reason") == "max_tokens")
        if incomplete:
            details["incomplete_provider_response"] = self.runtime._plain(response)
        return details
