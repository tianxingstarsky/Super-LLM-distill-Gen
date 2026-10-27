"""Ephemeral node-call policy; no new task or checkpoint representation."""
from dataclasses import dataclass
from typing import Any
from .reading_session import READING_PROMPTS


@dataclass(frozen=True)
class NodeModelRequest:
    key: Any
    role: str
    prompt_id: str
    data: Any
    image: Any = None
    instruction: Any = None
    allow_reasoning_fallback: bool = True
    model_stage: str | None = None
    prompt_stage: str | None = None
    capture: bool = False
    reasoning_prefix: Any = None
    native_request_attempts: int | None = None
    structured_response_attempts: int | None = None

    @property
    def image_values(self):
        return ([self.image] if isinstance(self.image, str) else self.image) if self.image is not None else []

    def validate(self):
        images = self.image_values
        if (not isinstance(images, list) or len(images) > 8
                or any(not isinstance(value, str) or not value for value in images)):
            raise ValueError("invalid_document_images")
        attempts = self.native_request_attempts
        if attempts is not None and (self.prompt_id not in {"workflow.cot_native_answer", "workflow.cot_native_joint", *READING_PROMPTS}
                or type(attempts) is not int or not 1 <= attempts <= 10):
            raise ValueError("invalid_model_request_attempts")
        attempts = self.structured_response_attempts
        if attempts is not None and (self.prompt_id in {"workflow.cot_native_answer", "workflow.cot_native_joint", *READING_PROMPTS}
                or type(attempts) is not int or not 1 <= attempts <= 10):
            raise ValueError("invalid_structured_response_attempts")


def request_role(recipe, stage, role):
    if (recipe.get("review_repair") is not None and role == "jev"
            and stage in {"sft", "multiturn", "preference", "cot", "trim"}):
        return "generation"
    return role


def validate_captured_response(value):
    if (not isinstance(value, dict) or value.get("model_json_response_version") != 1
            or set(value) != {"model_json_response_version", "data", "response_details"}
            or not isinstance(value["response_details"], dict)):
        raise ValueError("checkpoint_integrity_error")
