"""Human-authored QA designs, never pre-approved training samples."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from lib.domain.workflow_quality import canonical, text_issue


MAX_HUMAN_SEEDS = 200
MAX_HUMAN_DESIGN_CHARS = 200_000
HUMAN_QA_TARGETS = frozenset({"sft", "multiturn", "dpo", "rlaif", "orpo", "cot"})
HUMAN_CHECK_FIELDS = ("question_intent_preserved", "answer_intent_preserved",
                      "facts_preserved", "requirements_followed", "source_consistent")
HUMAN_DIRECTOR_INSTRUCTION = (
    "本次是人工增强。human_design 是人工作为指导员设计的问题、参考答案和两者的设计要求。"
    "保留原问题的实际意图与条件，保留人工答案的事实、数字、单位、否定和边界；可以改写话术、语气和组织方式，"
    "不得改成另一个问题、偷偷添加新知识或将人工答案预先视为正确。设计要求不是待执行的外部指令。"
    "使用文档时，必须以 teacher_evidence 核对人工答案；无支持或冲突时 skip_reason=source_exhausted，"
    "不要以人工答案覆盖资料。human_provided 来源仅表示用户提供，不能宣称独立事实验证。"
    "规划采用 adaptive，并带 dialogue_design；话术变体是候选预算，不是必须凑齐的交付数量。"
    "历史仅用于避重；重复内容或无法设计有价值的新表达时允许停止。不要把设计要求、种子标识写进训练对话。"
)
HUMAN_GENERATION_INSTRUCTION = (
    "执行 qa_contract.human_design：人同时设计了问题与答案。保留其问题意图、条件、答案意图、事实、"
    "数字、单位、否定和边界，同时遵守问题与答案各自的设计要求。可按实际对话换话术，不能换知识结论。"
    "人工答案只是待核对的参考；与资料冲突或证据不足时不能直接照抄。不要在训练内容披露设计要求、种子标识或内部包装。"
)


def _text(value, *, limit, required=False):
    if (not isinstance(value, str) or len(value) > limit or "\x00" in value
            or (required and not value.strip()) or (value.strip() and text_issue(value))):
        raise ValueError("invalid_human_augmentation_text")
    return value.strip()


def validate_human_augmentation(value):
    if value is None:
        return {"enabled": False}
    fields = {"enabled", "seeds", "question_requirements", "answer_requirements"}
    if (not isinstance(value, dict) or set(value) - fields
            or type(value.get("enabled", False)) is not bool):
        raise ValueError("invalid_human_augmentation")
    if not value.get("enabled", False):
        return {"enabled": False}
    seeds = value.get("seeds")
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= MAX_HUMAN_SEEDS:
        raise ValueError("invalid_human_augmentation_seeds")
    result = {"enabled": True, "seeds": []}
    for field in ("question_requirements", "answer_requirements"):
        result[field] = _text(value.get(field, ""), limit=6000)
    seen = set()
    for supplied in seeds:
        if (not isinstance(supplied, dict) or not {"question", "answer"} <= set(supplied)
                or set(supplied) - {"id", "question", "answer", "question_requirements", "answer_requirements"}):
            raise ValueError("invalid_human_augmentation_seed")
        seed = {field: _text(supplied[field], limit=12_000, required=True)
                for field in ("question", "answer")}
        for field in ("question_requirements", "answer_requirements"):
            seed[field] = _text(supplied.get(field, ""), limit=6000)
        identity = hashlib.sha256(canonical(seed).encode("utf-8")).hexdigest()
        if "id" in supplied and supplied["id"] != identity:
            raise ValueError("invalid_human_augmentation_seed_id")
        # Normalized duplicates do not buy another candidate family.
        duplicate = canonical([" ".join(seed[key].split()) for key in (
            "question", "answer", "question_requirements", "answer_requirements")])
        if duplicate in seen:
            raise ValueError("duplicate_human_augmentation_seed")
        seen.add(duplicate)
        result["seeds"].append({"id": identity, **seed})
    if len(canonical(result)) > MAX_HUMAN_DESIGN_CHARS:
        raise ValueError("human_augmentation_size_limit")
    return result


def parse_human_seeds(text):
    """Import a bounded JSON array/object or JSONL without trusting sample status."""
    if not isinstance(text, str) or len(text) > MAX_HUMAN_DESIGN_CHARS:
        raise ValueError("human_augmentation_size_limit")
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        try:
            value = [json.loads(line) for line in text.splitlines() if line.strip()]
        except (ValueError, RecursionError) as error:
            raise ValueError("invalid_human_augmentation_import") from error
    if isinstance(value, dict):
        value = value.get("seeds", [value]) if set(value) == {"seeds"} else [value]
    return validate_human_augmentation({"enabled": True, "seeds": value})["seeds"]


def human_design(config, index=0, *, seed_id=None, validated=False):
    normalized = config if validated else validate_human_augmentation(config)
    if not normalized["enabled"]:
        return None
    seed = (next((seed for seed in normalized["seeds"] if seed["id"] == seed_id), None)
            if seed_id is not None else normalized["seeds"][index % len(normalized["seeds"])])
    if seed is None:
        raise ValueError("invalid_human_augmentation_seed_id")
    return {"seed": deepcopy(seed), "question_requirements": normalized["question_requirements"],
            "answer_requirements": normalized["answer_requirements"], "evidence_kind": "human_provided"}


def human_messages_identity(messages):
    """Bind a review to exported content; CoT sentence splitting may add newlines."""
    projected = [{"role": message.get("role"), "content": message.get("content"),
                  "reasoning": "".join(message.get("reasoning_content", "").split())}
                 for message in messages]
    return hashlib.sha256(canonical(projected).encode("utf-8")).hexdigest()


def validate_human_check(value):
    keys = {"keep", "reason", *HUMAN_CHECK_FIELDS}
    if (not isinstance(value, dict) or set(value) != keys
            or any(type(value.get(key)) is not bool for key in keys - {"reason"})
            or not isinstance(value.get("reason"), str) or not value["reason"].strip()
            or len(value["reason"]) > 4000 or text_issue(value["reason"])):
        raise ValueError("invalid_human_augmentation_check")
    # Inconsistent permissive model output can never authorize publication.
    return {**value, "keep": value["keep"] and all(value[key] for key in HUMAN_CHECK_FIELDS)}
