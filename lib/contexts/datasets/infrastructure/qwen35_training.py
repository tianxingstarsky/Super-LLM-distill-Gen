"""Map canonical efforts to Qwen3.5's binary thinking switch at load time.

All five levels enable thinking by default; an explicit mapping can disable it
for selected levels. No strength instruction is injected into the conversation.
This adapter never loads weights, writes a dataset or changes source messages.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json

from lib.contexts.models.contracts import REASONING_EFFORT_REFERENCE

from ..domain.training_control import TRAINING_EFFORTS, validate_training_effort
from ..domain.trainer_formats import _has_media
from .deepseek_control_text import MARKER as VERBOSITY_REFERENCE
from .deepseek_control_text import PROFILE as DEEPSEEK_CONTROL_PROFILE
from .deepseek_control_text import normalize_deepseek_control_text


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Qwen35TrainingEncoder:
    """Encode text turns using a binary mapping and an explicit loss mask.

    ``tokenizer`` must be a local, fast Hugging Face tokenizer with its official
    Qwen3.5 template. Training dependencies are supplied by the caller. Custom
    mappings must cover every canonical effort and be reused at inference.
    """

    def __init__(self, tokenizer, *, max_length: int | None = None,
                 effort_mapping: Mapping[str, bool | int] | None = None,
                 reasoning_reference_text: str = "思考已开启",
                 source_control_profile: str | None = None,
                 verbosity_reference_text: str | None = None):
        if max_length is not None and (type(max_length) is not int or max_length < 1):
            raise ValueError("invalid_training_max_length")
        if not getattr(tokenizer, "is_fast", False):
            raise ValueError("qwen35_fast_tokenizer_required")
        template = tokenizer.get_chat_template()
        if (not isinstance(template, str) or "reasoning_content" not in template
                or "enable_thinking" not in template):
            raise ValueError("qwen35_reasoning_template_required")
        if effort_mapping is None:
            effort_mapping = dict.fromkeys(TRAINING_EFFORTS, True)
        if (not isinstance(effort_mapping, Mapping) or set(effort_mapping) != set(TRAINING_EFFORTS)
                or any(type(value) not in {bool, int} or value not in (0, 1)
                       for value in effort_mapping.values())):
            raise ValueError("invalid_qwen35_effort_mapping")
        self._tokenizer = tokenizer
        self._template = template
        self._max_length = max_length
        self._effort_mapping = {effort: bool(effort_mapping[effort]) for effort in TRAINING_EFFORTS}
        if (not isinstance(reasoning_reference_text, str) or not reasoning_reference_text.strip()
                or REASONING_EFFORT_REFERENCE in reasoning_reference_text):
            raise ValueError("invalid_reasoning_reference_text")
        self._reasoning_reference_text = reasoning_reference_text
        if source_control_profile not in (None, DEEPSEEK_CONTROL_PROFILE):
            raise ValueError("unsupported_source_control_profile")
        if verbosity_reference_text is not None and (
                not isinstance(verbosity_reference_text, str) or not verbosity_reference_text.strip()
                or VERBOSITY_REFERENCE in verbosity_reference_text
                or REASONING_EFFORT_REFERENCE in verbosity_reference_text):
            raise ValueError("invalid_verbosity_reference_text")
        self._source_control_profile = source_control_profile
        self._verbosity_reference_text = verbosity_reference_text
        self._provenance = {
            "adapter": "shujian.qwen35-thinking-map/v1",
            "control_kind": "enable_thinking",
            "mapping_kind": "five_efforts_to_binary",
            "native_effort_levels": False,
            "reasoning_reference_text": reasoning_reference_text,
            "effort_mapping": dict(self._effort_mapping),
            "template_sha256": _sha256(template),
            "tokenizer_sha256": _sha256(tokenizer.backend_tokenizer.to_str()),
            "control_mapping_sha256": _sha256(json.dumps(self._effort_mapping, sort_keys=True)),
        }
        # Optional source-text adaptation has separate provenance; legacy encoder
        # metadata and native thinking-switch semantics stay unchanged by default.
        if source_control_profile is not None or verbosity_reference_text is not None:
            self._provenance.update({
                "source_control_profile": source_control_profile,
                "verbosity_reference_text": verbosity_reference_text,
                "control_text_mapping_sha256": _sha256(json.dumps({
                    "source_control_profile": source_control_profile,
                    "verbosity_reference_text": verbosity_reference_text,
                }, ensure_ascii=False, sort_keys=True)),
            })

    @property
    def provenance(self) -> dict:
        return deepcopy(self._provenance)

    def _prepare_messages(self, messages: list[dict], tools=None) -> list[dict]:
        if (not isinstance(messages, list) or not messages
                or any(not isinstance(message, Mapping) for message in messages)):
            raise ValueError("qwen35_messages_required")
        # Native Qwen tool replies do not preserve arbitrary tool-call IDs. Until
        # that mapping is separately supported, reject instead of losing meaning.
        if tools or any(m.get("tool_calls") or m.get("toolCalls") or m.get("function_call")
                        or m.get("role") in {"tool", "function"} for m in messages):
            raise ValueError("qwen35_tool_training_not_supported")
        if any(_has_media(message) for message in messages):
            raise ValueError("qwen35_media_not_supported")
        copied = deepcopy(messages)
        systems = []
        conversation = []
        for message in copied:
            role = message.get("role")
            if role == "system" and not conversation:
                if not isinstance(message.get("content"), str):
                    raise ValueError("qwen35_text_content_required")
                systems.append(message["content"])
                continue
            if role not in {"user", "assistant"}:
                raise ValueError("qwen35_unsupported_role_or_system_position")
            if not isinstance(message.get("content", ""), str):
                raise ValueError("qwen35_text_content_required")
            reasoning_values = [message[key] for key in ("reasoning_content", "thinking", "reasoning")
                                if message.get(key) not in (None, "")]
            if reasoning_values:
                if any(not isinstance(value, str) for value in reasoning_values):
                    raise ValueError("qwen35_invalid_reasoning")
                if any(value != reasoning_values[0] for value in reasoning_values[1:]):
                    raise ValueError("qwen35_conflicting_reasoning")
                message["reasoning_content"] = reasoning_values[0]
            message.pop("thinking", None)
            message.pop("reasoning", None)
            conversation.append(message)
        if not conversation or not any(m["role"] == "user" for m in conversation):
            raise ValueError("qwen35_user_message_required")
        prefix = [{"role": "system", "content": "\n\n".join(systems)}] if systems else []
        return [*prefix, *conversation]

    def _render(self, messages: list[dict], *, generation: bool, enable_thinking: bool) -> str:
        return self._tokenizer.apply_chat_template(
            messages, chat_template=self._template, tokenize=False,
            add_generation_prompt=generation, enable_thinking=enable_thinking,
        )

    def render_prompt(self, messages: list[dict], effort: str) -> str:
        """Render the same binary mapping for inference, without added rules."""
        enable_thinking = self._effort_mapping[validate_training_effort(effort)]
        return self._render(self._prepare_messages(messages), generation=True,
                            enable_thinking=enable_thinking)

    def encode_turn(self, messages: list[dict], control: str, *, tools=None) -> dict:
        enable_thinking = self._effort_mapping[validate_training_effort(control)]
        controlled = self._prepare_messages(messages, tools)
        target = controlled[-1]
        if target["role"] != "assistant":
            raise ValueError("qwen35_assistant_target_required")
        reasoning = target.get("reasoning_content")
        if enable_thinking and (not isinstance(reasoning, str) or not reasoning.strip()):
            raise ValueError("qwen35_target_reasoning_missing")
        answer = target.get("content")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("qwen35_target_answer_missing")
        # Mixed inline and dedicated reasoning is ambiguous; never duplicate it.
        if "<think>" in answer or "</think>" in answer:
            raise ValueError("qwen35_inline_and_separate_reasoning_conflict")
        control_text_metadata = {}
        if enable_thinking and (self._source_control_profile or self._verbosity_reference_text is not None):
            original_reasoning = reasoning
            edits = []
            if self._source_control_profile == DEEPSEEK_CONTROL_PROFILE:
                reasoning, edits = normalize_deepseek_control_text(reasoning)
            if edits or VERBOSITY_REFERENCE in reasoning:
                # Literal matching cannot distinguish a quoted field from a
                # self-reference. Refuse tasks that discuss the matched text.
                if any(VERBOSITY_REFERENCE in m.get("content", "") or
                       normalize_deepseek_control_text(m.get("content", ""))[1]
                       for m in controlled if m["role"] != "system"):
                    raise ValueError("verbosity_reference_in_task_content")
            replacement_count = 0
            if self._verbosity_reference_text is not None:
                replacement_count = reasoning.count(VERBOSITY_REFERENCE)
                reasoning = reasoning.replace(VERBOSITY_REFERENCE, self._verbosity_reference_text)
            target["reasoning_content"] = reasoning
            control_text_metadata = {
                "source_control_edits": edits,
                "source_reasoning_sha256": _sha256(original_reasoning),
                "verbosity_reference_replacements": replacement_count,
            }
        reference_count = 0
        if enable_thinking and REASONING_EFFORT_REFERENCE in reasoning:
            # Only the reserved self-reference is adapted. A task quoting the
            # same token is ambiguous; do not rewrite quotations or free prose.
            if any(REASONING_EFFORT_REFERENCE in m.get("content", "")
                   for m in controlled if m["role"] != "system"):
                raise ValueError("reasoning_reference_in_task_content")
            reference_count = reasoning.count(REASONING_EFFORT_REFERENCE)
            reasoning = reasoning.replace(REASONING_EFFORT_REFERENCE, self._reasoning_reference_text)
            target["reasoning_content"] = reasoning
        if not enable_thinking:
            # This is an explicit target projection. The original source record
            # still retains its complete reasoning; only this copied target changes.
            target["reasoning_content"] = ""
        prompt = self._render(controlled[:-1], generation=True, enable_thinking=enable_thinking)
        full_text = self._render(controlled, generation=False, enable_thinking=enable_thinking)
        thinking_prefix = "<think>\n" if enable_thinking else "<think>\n\n</think>\n\n"
        if not prompt.endswith(thinking_prefix) or not full_text.startswith(prompt):
            raise ValueError("qwen35_template_prefix_mismatch")
        # Thinking mode supervises the full reasoning envelope. Disabled mode
        # masks the official empty-think generation prefix and supervises only
        # the answer and its end marker. The template has no generation block.
        if enable_thinking:
            prompt = prompt[:-len(thinking_prefix)]
        prompt_ids = self._tokenizer.encode(prompt, add_special_tokens=False)
        input_ids = self._tokenizer.encode(full_text, add_special_tokens=False)
        if input_ids[:len(prompt_ids)] != prompt_ids:
            raise ValueError("qwen35_token_boundary_mismatch")
        completion = self._tokenizer.decode(input_ids[len(prompt_ids):], skip_special_tokens=False)
        if ((enable_thinking and reasoning.strip() not in completion)
                or answer.strip() not in completion):
            raise ValueError("qwen35_target_content_lost")
        if self._max_length is not None and len(input_ids) > self._max_length:
            raise ValueError(f"training_example_too_long:{len(input_ids)}>{self._max_length}")
        labels = [-100] * len(prompt_ids) + input_ids[len(prompt_ids):]
        return {
            "input_ids": input_ids,
            "attention_mask": [1] * len(input_ids),
            "labels": labels,
            "metadata": {
                **self.provenance,
                "effort": control,
                "enable_thinking": enable_thinking,
                "supervision": "reasoning_and_answer" if enable_thinking else "answer_only",
                "reasoning_reference_replacements": reference_count,
                **control_text_metadata,
                "input_tokens": len(input_ids),
                "supervised_tokens": len(input_ids) - len(prompt_ids),
            },
        }
