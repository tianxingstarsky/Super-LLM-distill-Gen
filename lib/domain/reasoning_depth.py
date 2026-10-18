"""Explicit explanation depth and public student-input controls.

These choices describe authored training explanations. They do not change a
teacher's native reasoning effort, promise a token count, or train a student
merely by appearing in sample metadata.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib


REASONING_DEPTH_CONTRACT_VERSION = 1
RESOLVED_REASONING_DEPTHS = ("quick", "standard", "deep")
REASONING_DEPTHS = (*RESOLVED_REASONING_DEPTHS, "mixed")

_INSTRUCTIONS = {
    "quick": (
        "显式推理深度：快速。用紧凑的解释说明决定答案的关键依据与必要推导，"
        "保留会改变结论的条件、否定和不确定性，不把简短当作跳过依据的理由。"
        "无需展开无关备选、重复论证或额外背景；任务确实复杂时仍须保留必要支持关系。"
    ),
    "standard": (
        "显式推理深度：标准。自然解释当前任务需要的依据、关键中间关系和结论成立条件，"
        "遇到会改变答案的歧义、假设或风险时说明其影响，并作相关而具体的核对。"
        "按真实依赖充分说明，不以结论或提纲代替关键推导，不堆叠同义段落。"
    ),
    "deep": (
        "显式推理深度：深入。充分展开当前任务真正需要的依据、前提、依赖关系和关键中间推导，"
        "说明候选判断如何受到具体条件、相关备选或反例及针对性核对的支持或限制。"
        "复杂任务不可用短小结论、提纲或审计字段代替正文中的必要支持桥梁；"
        "简短提问也可能需要深入解释，展开程度由任务依赖而非问句字数决定。"
        "保留假设、证据缺口、结论边界和仍未解决的问题。没有实际需要时不制造备选、"
        "失败尝试、反问、工具调用或检验结果；不强迫固定步骤、段落顺序或最少字数，"
        "不靠重复、无关旁支或全文翻译凑长。"
    ),
}

_PUBLIC_CONTROLS = {
    "quick": (
        "推理深度：快速。简明说明关键依据与必要推导，保留影响结论的条件和不确定性。"
    ),
    "standard": (
        "推理深度：标准。解释必要依据、关键中间关系和结论成立条件，并进行相关核对。"
    ),
    "deep": (
        "推理深度：深入。充分展开相关依据、关键中间推导与必要检验，保留条件和不确定性；"
        "不为篇幅重复，不套固定步骤。"
    ),
}


def _validate_version(version: int) -> None:
    if type(version) is not int or version != REASONING_DEPTH_CONTRACT_VERSION:
        raise ValueError("invalid_reasoning_depth_contract_version")


def validate_reasoning_depth(value: str) -> str:
    if not isinstance(value, str) or value not in REASONING_DEPTHS:
        raise ValueError("invalid_reasoning_depth")
    return value


def resolve_reasoning_depth(value: str, sample_id: str, *, version: int = 1) -> str:
    """Resolve mixed depth once per stable sample, independent of batching."""
    _validate_version(version)
    validate_reasoning_depth(value)
    if not isinstance(sample_id, str) or not sample_id:
        raise ValueError("invalid_generation_sample_id")
    if value != "mixed":
        return value
    seed = ("authored-reasoning-depth-v1\0" + sample_id).encode("utf-8")
    index = int.from_bytes(hashlib.sha256(seed).digest(), "big")
    return RESOLVED_REASONING_DEPTHS[index % len(RESOLVED_REASONING_DEPTHS)]


def reasoning_depth_instruction(depth: str, *, version: int = 1) -> str:
    """Return authoring/review guidance for an already resolved choice."""
    _validate_version(version)
    if validate_reasoning_depth(depth) not in RESOLVED_REASONING_DEPTHS:
        raise ValueError("unresolved_reasoning_depth")
    return _INSTRUCTIONS[depth]


def reasoning_depth_control_message(depth: str, *, version: int = 1) -> dict:
    """Return the short, public instruction that must reach student input."""
    _validate_version(version)
    if validate_reasoning_depth(depth) not in RESOLVED_REASONING_DEPTHS:
        raise ValueError("unresolved_reasoning_depth")
    return {"role": "system", "content": (
        f'<reasoning_depth_control version="1" depth="{depth}">'
        + _PUBLIC_CONTROLS[depth] + "</reasoning_depth_control>"
    )}


_PUBLIC_CONTROL_CONTENTS = frozenset(
    reasoning_depth_control_message(depth)["content"] for depth in RESOLVED_REASONING_DEPTHS
)


def is_reasoning_depth_control_message(message) -> bool:
    """Recognize only our three exact version-one public system controls."""
    return (isinstance(message, dict) and set(message) == {"role", "content"}
            and message.get("role") == "system"
            and isinstance(message.get("content"), str)
            and message["content"] in _PUBLIC_CONTROL_CONTENTS)


def apply_reasoning_depth_control(messages: list[dict], depth: str, *, version: int = 1) -> list[dict]:
    """Copy a transcript and add one control before its first user message.

    Existing user-authored system and user content remains literal. Only exact
    known public controls are replaced, allowing a downstream CoT node to choose
    a different depth without leaving conflicting training instructions. Empty
    or system-only prefixes can be prepared before constructing a dialogue.
    The caller owns explicit opt-in, provenance, and authored-message scope.
    """
    control = reasoning_depth_control_message(depth, version=version)
    if (not isinstance(messages, list)
            or any(not isinstance(message, dict) for message in messages)):
        raise ValueError("invalid_reasoning_depth_messages")
    user_index = next((index for index, message in enumerate(messages)
                       if message.get("role") == "user"), None)
    if user_index is None:
        if any(message.get("role") != "system" for message in messages):
            raise ValueError("reasoning_depth_user_message_missing")
        user_index = len(messages)
    existing = [(index, message) for index, message in enumerate(messages)
                if is_reasoning_depth_control_message(message)]
    if existing:
        if any(index >= user_index for index, _ in existing):
            raise ValueError("reasoning_depth_control_after_user")
        replace_index = existing[0][0]
        existing_indices = {index for index, _ in existing}
        result = [deepcopy(message) for index, message in enumerate(messages)
                  if index not in existing_indices or index == replace_index]
        result[replace_index] = control
        return result
    # Non-leading system instructions are preserved too; an invalid placement
    # cannot be repaired here without changing the original conversation.
    if any(message.get("role") == "system" for message in messages[user_index:]):
        raise ValueError("reasoning_depth_system_message_after_user")
    result = deepcopy(messages)
    result.insert(user_index, control)
    return result
