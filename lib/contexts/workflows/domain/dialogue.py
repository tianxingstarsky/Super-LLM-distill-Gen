"""Pure dialogue controls and pinned route decisions; no model or file access."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
from lib.domain.workflow_quality import canonical


def dialogue_digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DialogueSource:
    context: dict
    evidence_text: str | None = None
    evidence_blocks: list[str] | None = None

class DialoguePlanError(ValueError):
    code = "invalid_dialogue_step"

    def __init__(self, validation_error=None):
        self.feedback = {"code": self.code}
        if validation_error is not None:
            self.code = str(validation_error)
            self.feedback = deepcopy(getattr(validation_error, "feedback", {"code": self.code}))
        super().__init__(self.code)


def controlled_training_messages(messages, style):
    """Establish the learner's public control before review and hashing."""
    if not style or not style.get("reasoning_depth"):
        return messages
    from lib.domain.reasoning_depth import apply_reasoning_depth_control
    controlled = apply_reasoning_depth_control(messages, style["reasoning_depth"])
    history = messages[:-1] if messages and messages[-1].get("role") == "assistant" else messages
    if any(message.get("role") == "assistant" for message in history) and controlled != messages:
        # Chat exports supervise prior assistant turns too. Do not relabel
        # their existing explanations by changing a global system control
        # while authoring only the latest response.
        raise ValueError("reasoning_depth_history_control_mismatch")
    return controlled


def without_source_reasoning(value):
    """Keep source facts without supplying an assistant's existing chain."""
    if isinstance(value, list):
        return [without_source_reasoning(item) for item in value]
    if isinstance(value, dict):
        return {key: without_source_reasoning(item) for key, item in value.items()
                if not (value.get("role") == "assistant" and
                        key in {"reasoning_content", "reasoning", "thinking"})}
    return value


def document_evidence_level(unit, *, assessed=False):
    reading = unit.get("document_reading") or {}
    mode = reading.get("mode") or ("vision" if reading.get("image_sha256") else None)
    if mode=="docling" and reading.get("evidence_level")=="model_transcribed_visual_source":mode="vision"
    level = {"model": "model_restructured_source_text",
             "vision": "model_transcribed_visual_source"}.get(mode, "source_text")
    if assessed:
        return "source_and_model_assessed" if level == "source_text" else level + "_and_model_assessed"
    return level


@dataclass
class DialogueProgress:
    """Ephemeral state for one conversation; never serialized as a new schema."""
    source_context: dict = field(default_factory=dict)
    pack_evidence: str | None = None
    selected_evidence_blocks: list | None = None
    contract: dict | None = None
    evidence_level: str = ""
    source_verification: str = ""
    reviews: list = field(default_factory=list)
    quotes_by_turn: list = field(default_factory=list)
    dialogue_design: dict = field(default_factory=dict)
    legacy_adaptive_route: bool = False
    separated_step_route: bool = False
    adaptive_dialogue: bool = False
    ordinary_stop_mode: bool = False
    ordinary_stop_audit: dict = field(default_factory=dict)
    dialogue_steps: list = field(default_factory=list)
    dialogue_state: dict | None = None
    dialogue_end_reason: str = "turn_limit"
    dialogue_end_guidance: str = ""
    dialogue_state_after_turn: int = 0
    messages: list = field(default_factory=list)
    raw_user_messages: list = field(default_factory=list)
    finished_dialogue: bool = False
    ends: list = field(default_factory=list)
