"""Native llama-server prompt accounting and OpenAI-compatible chat transport."""

import json
import os
import re
from contextlib import ExitStack
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator

from lib.contexts.models.contracts import ModelQueueTimeout
from ..domain.guided_teacher_delivery import QWEN_SAMPLING_VERSIONS, TEACHER_REASONING_VERSIONS
from lib.prompts.workflow_guided_answers import FINAL_DELIVERY

MIN_CONTEXT = 90_000
MIN_OUTPUT = 32_768
SAMPLING_PROFILES = ("auto", "server", "qwen3.5-0.8b", "qwen3.5-9b")


def qwen_text_sampling(profile, thinking):
    """Qwen's published text sampling settings, using llama.cpp's parameter names."""
    small_instruct = profile == "qwen3.5-0.8b" and not thinking
    return {
        "temperature": 1.0,
        "top_p": 1.0 if small_instruct else 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 2.0 if small_instruct else 1.5,
        "repeat_penalty": 1.0,
        "repeat_last_n": 64,
        "frequency_penalty": 0.0,
        "samplers": ["penalties", "top_k", "top_p", "min_p", "temperature"],
    }

# These describe a requested answer surface. All values in that surface remain possible.
DIRECT_ANSWER_GRAMMARS = {
    "number": 'root ::= [+-]? [0-9]+ (("." [0-9]+) | ("/" [0-9]+))? ([eE] [+-]? [0-9]+)?',
    "uppercase_letters": 'root ::= [A-Z]+',
    "fact_ids": 'root ::= "F" [0-9]+ (",F" [0-9]+)*',
    # A result made from distinct A–Z identifiers can contain at most 26 letters.
    "distinct_letter_ids": 'root ::= [A-Z]{1,26}',
    # The F00–F99 vocabulary has one hundred possible distinct identifiers.
    "distinct_fact_ids": 'root ::= "F" [0-9]{2} (",F" [0-9]{2}){0,99}',
    # The exact-rational verifier accepts up to 256 input characters and a two-digit exponent.
    "keyed_rational": 'root ::= [+-]? [0-9]{1,256} (("." [0-9]{1,256}) | ("/" [0-9]{1,256}))? ([eE] [+-]? [0-9]{1,2})?',
}


def wire_body(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class GuidedAdmissionTimeout(ModelQueueTimeout):
    """The generation request was never sent because a lease was not granted."""


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
        thinking=None,
        slot_pool_factory=None,
        sampling=None,
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
        self.slot_pool_factory = slot_pool_factory
        self.slot_pool = None
        self.root = self.url.removesuffix("/v1")
        self.model = model
        self.cap = output_cap
        self.requested_thinking = thinking
        self.thinking = bool(thinking)
        self.record_teacher_reasoning = False
        self.requested_sampling = sampling
        self.sampling_profile = None
        self.sampling_parameters = {"temperature": 0}
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
        # Native loopback chat sends its first response bytes after decoding.
        # A 32K generation on CPU can exceed an hour without a network fault.
        loopback_chat = (self.local and urlsplit(self.url).hostname in {"localhost", "127.0.0.1", "::1"}
                         and url in {self.root + "/v1/chat/completions", self.root + "/completion"})
        with urlopen(request, timeout=None if loopback_chat else 3600) as response:
            return json.load(response)

    def configure_teacher_reasoning(self, version, frozen_endpoint=None):
        """Resolve a tutor's optional override only after the run version is known."""
        self.record_teacher_reasoning = version in TEACHER_REASONING_VERSIONS
        if self.record_teacher_reasoning and not self.local and self.requested_thinking is not None:
            raise ValueError("the explicit native teacher thinking switch requires a llama.cpp endpoint")
        if self.requested_thinking is not None:
            self.thinking = self.requested_thinking
        elif frozen_endpoint is not None:
            self.thinking = bool(frozen_endpoint.get("enable_thinking"))
        else:
            self.thinking = self.local and self.record_teacher_reasoning

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
        if self.local and self.slot_pool_factory is not None:
            self.slot_pool = self.slot_pool_factory(self.root, self.slots)
        spec = dict(
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
        if self.sampling_profile is not None:
            spec.update(sampling_profile=self.sampling_profile, sampling_parameters=self.sampling_parameters)
        return spec

    def configure_sampling(self, version, frozen_endpoint=None):
        self.sampling_profile = None
        self.sampling_parameters = {"temperature": 0}
        if version not in QWEN_SAMPLING_VERSIONS:
            if self.requested_sampling is not None:
                raise ValueError("explicit guided sampling profiles require protocol 4.7")
            return
        requested = self.requested_sampling or (frozen_endpoint or {}).get("sampling_profile") or "auto"
        if not self.local and requested == "auto":
            return
        if requested not in SAMPLING_PROFILES:
            raise ValueError("unknown guided sampling profile")
        if requested == "auto":
            # Arbitrary model aliases and derivatives can select a profile explicitly.
            identified = re.fullmatch(r"(?:.*/)?(qwen3\.5-(?:0\.8b|9b))(?:[-.](?:q\d.*|bf16|gguf))?", self.model, re.I)
            requested = identified.group(1).lower() if identified and self.local else "server"
        if requested != "server" and not self.local:
            raise ValueError("Qwen native sampling profiles require a llama.cpp endpoint")
        self.sampling_profile = requested
        self.sampling_parameters = {} if requested == "server" else qwen_text_sampling(requested, self.thinking)

    def payload(self, messages, schema):
        data = dict(
            model=self.model,
            messages=messages,
            max_tokens=self.cap,
            **self.sampling_parameters,
            stream=False,
        )
        if self.local:
            data["chat_template_kwargs"] = {"enable_thinking": self.thinking}
            if self.record_teacher_reasoning and self.thinking:
                # The native parser extracts the thinking channel before matching the JSON grammar.
                data["reasoning_format"] = "deepseek"
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

    def final_answer_payload(self, messages):
        """Freeze the native token prefix ending immediately after the program's thinking block."""
        last = messages[-1]
        if not self.local or last.get("role") != "assistant" or last.get("content") != "":
            raise ValueError("answer continuation requires a local assistant reasoning prefix")
        trace = last.get("reasoning_content")
        if not isinstance(trace, str) or not trace.strip():
            raise ValueError("missing guided reasoning before the final answer")
        if any(mark in trace for mark in ("<think>", "</think>", "<|im_start|>", "<|im_end|>", "<|endoftext|>")):
            raise ValueError("guided reasoning contains a chat control delimiter")
        task = list(messages[:-1])
        if task and task[0]["role"] == "system":
            task[0] = {**task[0], "content": task[0]["content"] + "\n\n" + FINAL_DELIVERY}
        else:
            task.insert(0, {"role": "system", "content": FINAL_DELIVERY})
        rendered = self.http(self.root + "/apply-template", {
            "messages": task, "chat_template_kwargs": {"enable_thinking": True},
            "add_generation_prompt": True,
        })["prompt"]
        if not rendered.endswith("<think>\n"):
            raise ValueError("the local template did not open a native Qwen thinking block")
        prefix = rendered + trace + "\n</think>\n\n"
        tokens = self.http(self.root + "/tokenize", {
            "content": prefix, "add_special": False, "parse_special": True,
        })["tokens"]
        if not tokens or len(tokens) + self.cap > self.context:
            raise ValueError("the complete guided reasoning and answer reservation must fit the native slot")
        return {"model": self.model, "prompt": tokens, "n_predict": self.cap,
                **self.sampling_parameters, "cache_prompt": True, "stream": False}

    def _generate(self, path, data):
        # A full native server must not consume the global allowance while queuing.
        with ExitStack() as leases:
            try:
                if self.slot_pool is not None:
                    leases.enter_context(self.slot_pool.acquire())
                leases.enter_context(self.pool.acquire())
            except ModelQueueTimeout as error:
                raise GuidedAdmissionTimeout(ModelQueueTimeout.code) from error
            return self.http(self.root + path, data)

    def complete(self, data):
        if "prompt" in data:
            raw = self._generate("/completion", data)
            if raw.get("truncated") or raw.get("tokens_evaluated") != len(data["prompt"]):
                raise ValueError("llama-server changed the supplied answer prefix")
            if raw.get("stop_type") not in {"eos", "limit"} or not isinstance(raw.get("content"), str):
                raise ValueError("unexpected llama-server answer continuation")
            predicted = raw["tokens_predicted"]
            return dict(content=raw["content"], reasoning_content="",
                        finish_reason="stop" if raw["stop_type"] == "eos" else "length",
                        usage=dict(prompt_tokens=len(data["prompt"]), completion_tokens=predicted,
                                   total_tokens=len(data["prompt"]) + predicted),
                        model=raw.get("model", self.model), context_tokens=self.context,
                        estimated_prompt_tokens=len(data["prompt"]), max_tokens=self.cap,
                        server_timing=raw.get("timings"), transport="llama.cpp/completion")
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
        raw = self._generate("/v1/chat/completions", data)
        chosen = raw["choices"][0]
        msg = chosen["message"]
        content = msg.get("content") or ""
        thinking = msg.get("reasoning_content") or msg.get("reasoning") or ""
        if not isinstance(content, str) or not isinstance(thinking, str):
            raise ValueError("invalid text fields in model response")
        result = dict(
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
        if self.record_teacher_reasoning:
            form = data.get("response_format", {}).get("json_schema", {}).get("schema")
            structured = "not_requested"
            if form is not None:
                try:
                    submitted = json.loads(content)
                except (ValueError, TypeError):
                    structured = "invalid_json"
                else:
                    structured = "valid" if Draft202012Validator(form).is_valid(submitted) else "schema_mismatch"
            field = next((k for k in ("reasoning_content", "reasoning") if msg.get(k)), None)
            result["teacher_reasoning"] = {
                "requested": data.get("chat_template_kwargs", {}).get("enable_thinking"),
                "observed": bool(thinking.strip()), "source_field": field, "characters": len(thinking),
                "structured_output": structured, "finish_reason": chosen.get("finish_reason"),
            }
        return result
