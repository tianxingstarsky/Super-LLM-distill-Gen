"""Bounded, evidence-linked procedural lessons; no storage or model access."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from lib.domain.agent_experience import EXPERIENCE_SCOPES, MAX_EXPERIENCE_BYTES

FORMAT_VERSION = 1
MAX_EPISODES = 8
MAX_EPISODE_CHARS = 60000
MAX_RULES = 5
MAX_HISTORY_CHARS = 16000
_ID = re.compile(r"[a-f0-9]{32}")

EXPERIENCE_PROPOSAL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "rules"],
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 800},
        "rules": {"type": "array", "minItems": 0, "maxItems": MAX_RULES, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["principle", "when", "avoid", "evidence"],
            "properties": {
                "principle": {"type": "string", "minLength": 1, "maxLength": 600},
                "when": {"type": "string", "minLength": 1, "maxLength": 500},
                "avoid": {"type": "string", "minLength": 1, "maxLength": 600},
                "evidence": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["episode_id", "quote"], "properties": {
                        "episode_id": {"type": "string", "minLength": 1, "maxLength": 240},
                        "quote": {"type": "string", "minLength": 1, "maxLength": 1200},
                    }}}
            }}}
    },
}


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError("experience_evolution_invalid_json") from error


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate_identifier(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("experience_evolution_invalid_id")
    return value


def validate_scope(scope):
    if not isinstance(scope, str) or scope not in EXPERIENCE_SCOPES:
        raise ValueError("experience_evolution_invalid_scope")
    return scope


def _text(value, limit, name, *, empty=False):
    if (not isinstance(value, str) or (not empty and not value.strip()) or len(value) > limit
            or "\x00" in value or "\ufffd" in value):
        raise ValueError("experience_evolution_invalid_" + name)
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("experience_evolution_invalid_" + name) from error
    return value


def validate_experience_text(text):
    text = _text(text, MAX_EXPERIENCE_BYTES, "text")
    if len(text.encode("utf-8")) > MAX_EXPERIENCE_BYTES:
        raise ValueError("experience_evolution_invalid_text")
    return text


def prepare_evolution_payload(scope, current_text, episodes, *, instruction="", parent_id=None, source_receipt=None):
    """Validate complete supplied episodes once; never truncate their history."""
    validate_scope(scope)
    validate_experience_text(current_text)
    validate_identifier(parent_id, optional=True)
    _text(instruction, 4000, "instruction", empty=True)
    if not isinstance(episodes, list) or not 1 <= len(episodes) <= MAX_EPISODES:
        raise ValueError("experience_evolution_invalid_episodes")
    seen = set()
    for episode in episodes:
        if not isinstance(episode, dict):
            raise ValueError("experience_evolution_invalid_episode")
        identity = _text(episode.get("id"), 240, "episode_id")
        if identity in seen:
            raise ValueError("experience_evolution_duplicate_episode")
        seen.add(identity)
    if len(canonical(episodes)) > MAX_EPISODE_CHARS:
        raise ValueError("experience_evolution_episodes_too_large")
    if source_receipt is not None and not isinstance(source_receipt, dict):
        raise ValueError("experience_evolution_invalid_source_receipt")
    payload = {"scope": scope, "current_text": current_text, "episodes": episodes,
               "instruction": instruction, "parent_id": parent_id, "source_receipt": source_receipt or {},
               "objective": "compact_conditional_procedural_rules_not_task_answers"}
    canonical(payload)
    return deepcopy(payload)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def validate_proposal(proposal, episodes):
    """Bind cited evidence literally; that does not certify the lesson is true."""
    from jsonschema import Draft202012Validator
    if next(Draft202012Validator(EXPERIENCE_PROPOSAL_SCHEMA).iter_errors(proposal), None) is not None:
        raise ValueError("experience_evolution_invalid_proposal")
    evidence = {episode["id"]: tuple(_strings(episode)) for episode in episodes}
    for rule in proposal["rules"]:
        for field, limit in (("principle", 600), ("when", 500), ("avoid", 600)):
            _text(rule[field], limit, field)
        for reference in rule["evidence"]:
            quote = _text(reference["quote"], 1200, "quote")
            if not any(quote in text for text in evidence.get(reference["episode_id"], ())):
                raise ValueError("experience_evolution_evidence_mismatch")
    _text(proposal["summary"], 800, "summary")
    return deepcopy(proposal)


def render_experience(rules, summary=""):
    """A compact replacement; provenance and raw attempts stay outside prompts."""
    text = "\n\n".join(([summary] if summary else []) + [
        "适用：" + rule["when"] + "\n采用：" + rule["principle"] + "\n避开：" + rule["avoid"] for rule in rules])
    return validate_experience_text(text)


def evolution_history(editions):
    """Keep whole recent edits as preference evidence, without raw attempts."""
    selected, omitted = [], []
    used = 0
    # Prefer the latest actual user edit when the two complete editions do
    # not both fit; present retained entries in chronological order.
    for edition in reversed(editions[-2:]):
        entry = {key: deepcopy(edition[key]) for key in (
            "id", "parent_id", "status", "quality", "summary", "rules", "instruction", "text")}
        size = len(canonical(entry))
        if used + size > MAX_HISTORY_CHARS:
            omitted.append(edition["id"])
        else:
            selected.append(entry)
            used += size
    return {"editions": list(reversed(selected)), "omitted_ids": list(reversed(omitted)),
            "meaning": "user_edits_are_preferences_not_verified_quality_success",
            "raw_attempts": "retained_in_repository_not_repeated_in_prompt"}


def proposal_snapshot(payload, proposal):
    return {"format_version": FORMAT_VERSION, "scope": payload["scope"], "status": "candidate",
            "label": "轨迹提炼候选", "parent_id": payload["parent_id"], "base_text": payload["current_text"],
            "text": render_experience(proposal["rules"], proposal["summary"]), "summary": proposal["summary"],
            "rules": deepcopy(proposal["rules"]), "episodes": deepcopy(payload["episodes"]),
            "source_receipt": deepcopy(payload["source_receipt"]), "instruction": payload["instruction"],
            "evolution_history": deepcopy(payload.get("evolution_history", {})),
            "quality": "model_proposed_not_verified", "proposal_input_sha256": digest(payload)}


def manual_snapshot(scope, text, *, parent_id=None, label="个人风格", status="personal", base_text=""):
    validate_scope(scope)
    validate_experience_text(text)
    validate_identifier(parent_id, optional=True)
    _text(label, 80, "label")
    if status not in {"initial", "personal"}:
        raise ValueError("experience_evolution_invalid_status")
    return {"format_version": FORMAT_VERSION, "scope": scope, "status": status, "label": label,
            "parent_id": parent_id, "base_text": base_text, "text": text, "summary": label, "rules": [],
            "episodes": [], "source_receipt": {}, "instruction": "", "quality": "user_saved_not_verified"}
