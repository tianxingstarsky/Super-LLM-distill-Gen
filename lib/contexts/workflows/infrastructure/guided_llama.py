"""Native llama-server prompt accounting and OpenAI-compatible chat transport."""

import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MIN_CONTEXT = 90_000
MIN_OUTPUT = 32_768


def wire_body(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class GuidedChatEndpoint:
    def __init__(
        self,
        url,
        model,
        api_key_env,
        output_cap,
        *,
        local,
        context,
        pool,
        thinking=False,
    ):
        self.url = url.rstrip("/")
        parts = urlsplit(self.url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "provide an endpoint URL without embedded credentials or query parameters"
            )
        self.local = local
        self.pool = pool
        self.root = self.url.removesuffix("/v1")
        self.model = model
        self.cap = output_cap
        self.thinking = thinking
        self.context = context
        self.slots = None
        self.headers = {"Content-Type": "application/json"}
        if api_key_env:
            key = os.environ.get(api_key_env)
            if not key:
                raise ValueError(
                    f"missing credential environment variable {api_key_env}"
                )
            self.headers["Authorization"] = "Bearer " + key
        if not model or output_cap < MIN_OUTPUT or context < MIN_CONTEXT:
            raise ValueError(
                "model, per-slot context >=90000 and output cap >=32768 are required"
            )

    def http(self, url, body=None):
        request = Request(
            url,
            data=None if body is None else wire_body(body),
            headers=self.headers,
        )
        with urlopen(request, timeout=3600) as response:
            return json.load(response)

    def validate(self):
        if self.local:
            slots = self.http(self.root + "/slots")
            if (
                not isinstance(slots, list)
                or not slots
                or any(type(s.get("n_ctx")) is not int for s in slots)
            ):
                raise ValueError(
                    "llama-server must expose its real slots and per-slot capacities"
                )
            self.slots = len(slots)
            self.context = min(s["n_ctx"] for s in slots)
            models = self.http(self.root + "/v1/models")
            if self.model not in {entry["id"] for entry in models["data"]}:
                raise ValueError("configured model is not served by llama-server")
            if self.context < MIN_CONTEXT:
                raise ValueError("native context per parallel slot is below 90000")
        if self.context <= self.cap:
            raise ValueError("generation cap leaves no prompt space")
        return dict(
            url=self.url,
            model=self.model,
            context_tokens=self.context,
            max_tokens=self.cap,
            context_evidence="llama_server_slots"
            if self.local
            else "declared_provider_capacity",
            enable_thinking=self.thinking if self.local else None,
            slots=self.slots,
        )

    def payload(self, messages, schema):
        data = dict(
            model=self.model,
            messages=messages,
            max_tokens=self.cap,
            temperature=0,
            stream=False,
        )
        if self.local:
            data["chat_template_kwargs"] = {"enable_thinking": self.thinking}
        if schema:
            data["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "solution_assessment",
                    "strict": True,
                    "schema": schema,
                },
            }
        return data

    def complete(self, data):
        prompt_count = None
        if self.local:
            rendered = self.http(
                self.root + "/apply-template",
                {
                    "messages": data["messages"],
                    "chat_template_kwargs": data["chat_template_kwargs"],
                    "add_generation_prompt": True,
                },
            )["prompt"]
            if any(
                turn["content"] not in rendered
                for turn in data["messages"]
                if turn["role"] == "tool"
            ):
                raise ValueError("native chat template lost a visible revision prompt")
            tokens = self.http(
                self.root + "/tokenize",
                {"content": rendered, "add_special": True, "parse_special": True},
            )
            prompt_count = len(tokens["tokens"])
            if prompt_count + self.cap > self.context:
                raise ValueError(
                    "complete transcript plus output reservation exceeds the native slot"
                )
        with self.pool.acquire():
            raw = self.http(self.root + "/v1/chat/completions", data)
        chosen = raw["choices"][0]
        msg = chosen["message"]
        content = msg.get("content") or ""
        thinking = msg.get("reasoning_content") or msg.get("reasoning") or ""
        if not isinstance(content, str) or not isinstance(thinking, str):
            raise ValueError("invalid text fields in model response")
        return dict(
            content=content,
            reasoning_content=thinking,
            finish_reason=chosen.get("finish_reason"),
            usage=raw.get("usage", {}),
            model=raw.get("model"),
            response_id=raw.get("id"),
            context_tokens=self.context,
            estimated_prompt_tokens=prompt_count,
            max_tokens=self.cap,
        )
