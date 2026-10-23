"""Complete-response tool protocol adapter; tool execution belongs to callers."""
from __future__ import annotations
import math
from urllib.parse import urlsplit
from . import tool_transport
from .request_adapter import ClientRequestAdmission


class SDKToolRequestAdapter(ClientRequestAdmission):
    def __init__(self, client):
        super().__init__(client, tool_transport)

    def prepare_request(self, messages, tools, *, max_tokens, temperature, retries, tool_choice):
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
            raise ValueError("max_tokens must be a positive integer")
        if type(retries) is not int or not 1 <= retries <= 10:
            raise ValueError("invalid_model_request_attempts")
        if type(temperature) not in (int, float) or not math.isfinite(temperature):
            raise ValueError("invalid_model_temperature")
        if not isinstance(tool_choice, str) or tool_choice not in {"auto", "none"}:
            raise ValueError("invalid_model_tool_choice")
        tools = self.runtime._tools(tools)
        messages = self.runtime._history(messages, self.client.api_format)
        if self.client.model_info.get("tools") is False:
            raise ValueError("model_tools_unsupported")
        if urlsplit(self.client.base_url).hostname == "api.deepseek.com" and urlsplit(self.client.base_url).path.rstrip("/") == "/beta":
            raise ValueError("model_tool_turn_native_prefill_unsupported")
        # Legacy chat preflight only counts reasoning used as a beta prefix. This
        # tool channel must additionally charge every historical thought, signature,
        # encrypted item and complete tool schema. Overcount duplicates conservatively.
        accounting = self.runtime.tool_context_accounting(messages, tools)
        request_max, reservation_usd = self.client._budget_request_bound(accounting, max_tokens)
        self._kwargs = self.runtime._request_kwargs(self.client, messages, tools, request_max, temperature, tool_choice=tool_choice)
        self._names = {tool["function"]["name"] for tool in tools}
        return reservation_usd

    def request(self):
        return self.runtime._request(self.client, self._kwargs)

    def response_usage(self, response):
        usage = self.runtime._get(response, "usage")
        prompt = self.runtime._get(usage, "prompt_tokens" if self.client.api_format == "chat" else "input_tokens", 0) or 0
        output = self.runtime._get(usage, "completion_tokens" if self.client.api_format == "chat" else "output_tokens", 0) or 0
        if self.client.api_format == "anthropic":
            prompt += (self.runtime._get(usage, "cache_creation_input_tokens", 0) or 0) + (self.runtime._get(usage, "cache_read_input_tokens", 0) or 0)
        return prompt, output

    def complete_response(self, response, *, tool_choice):
        result = self.runtime._assistant(response, self.client.api_format, self._names)
        if tool_choice == "none" and result["tool_calls"]:
            raise ValueError("model_unexpected_tool_call")
        return result

    def response_details(self, response):
        return self.runtime.extract_response_details(response, self.client.api_format)
