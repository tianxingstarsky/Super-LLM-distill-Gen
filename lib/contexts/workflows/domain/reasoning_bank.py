"""Source-bound procedural memories and immutable, per-task experience selection.

Retrieval executes the original Google ReasoningBank source through an adapter.
This module owns only our edition provenance and frozen experience contract;
embedding models, upstream execution and storage are ports.
"""
from copy import deepcopy

from lib.domain.agent_experience import (
    MAX_EXPERIENCE_BYTES, snapshot_agent_experience, validate_agent_experience,
)
from .experience_evolution import digest, validate_scope
from .dialogue_style_context import ALL_CONTEXT_TYPES

FORMAT_VERSION = 1
MAX_RETRIEVAL = 10
RETRIEVAL_INSTRUCTION = (
    "Retrieve prior procedural experiences applicable to the current task. "
    "Match the task's intent and conditions, including relevant failures and exceptions."
)


class ReasoningBankUnavailable(RuntimeError):
    """A memory dependency is unavailable; generation may continue without it."""


def validate_memory_context_types(value):
    """Exact source filters; None preserves the original unfiltered protocol."""
    if value is None:
        return None
    if (not isinstance(value, list) or any(not isinstance(kind, str) or kind not in ALL_CONTEXT_TYPES for kind in value)
            or len(set(value)) != len(value)):
        raise ValueError("reasoning_bank_invalid_dialogue_context_types")
    return [kind for kind in ALL_CONTEXT_TYPES if kind in value]


def validate_memory_context_assignment(value):
    contexts = validate_memory_context_types(value)
    if not contexts or "common" in contexts and len(contexts) != 1:
        raise ValueError("reasoning_bank_invalid_dialogue_context_assignment")
    return contexts


def memory_source_matches(source, edition_ids=None, context_types=None):
    if edition_ids is not None and source["edition"]["id"] not in edition_ids:
        return False
    if context_types is None:
        return True
    assigned = validate_memory_context_assignment(source.get("context_types", ["common"]))
    return bool(set(assigned).intersection(context_types))


def edition_reference(edition):
    return {key: deepcopy(edition.get(key)) for key in
            ("id", "sha256", "parent_id", "scope", "status", "quality", "created_at")}


def entries_from_edition(edition):
    """Keep rule applicability, counterexamples and evidence distinct from truth."""
    scope = validate_scope(edition["scope"])
    episodes = {item["id"]: item for item in edition.get("episodes", [])}
    entries = []
    context_version = edition.get("dialogue_context_assignment_version")
    if context_version is not None and (type(context_version) is not int or context_version != 1):
        raise ValueError("reasoning_bank_invalid_dialogue_context_assignment")
    assignments = edition.get("dialogue_context_assignments", {}) if context_version == 1 else {}
    if not isinstance(assignments, dict):
        raise ValueError("reasoning_bank_invalid_dialogue_context_assignment")
    for position, text in enumerate(edition.get("upstream_memory_items", [])):
        identity = {"scope": scope, "principle": text, "when": "", "avoid": ""}
        contexts = ({"context_types": validate_memory_context_assignment(assignments.get(digest(text), ["common"]))}
                    if context_version == 1 else {})
        entries.append({"format_version": FORMAT_VERSION, "id": digest(identity), **identity,
            "sources": [{"edition": edition_reference(edition), "rule_index": position,
                "observations": [], "upstream": edition.get("upstream", []),
                "source_receipt_sha256": digest(edition.get("source_receipt", {})), **contexts}]})
    for position, rule in enumerate(edition.get("rules", [])):
        content = {key: rule[key] for key in ("principle", "when", "avoid")}
        identity = {"scope": scope, **content}
        observations = []
        for evidence in rule.get("evidence", []):
            episode = episodes.get(evidence["episode_id"], {})
            outcome = episode.get("observed_outcome", "unknown")
            if outcome not in {"success", "failure", "unknown"}:
                outcome = "unknown"
            confidence_source = episode.get("observed_evidence_status", "unverified")
            observations.append({"episode_id": evidence["episode_id"], "outcome": outcome,
                "confidence_source": confidence_source, "quote": evidence["quote"]})
        source = {"edition": edition_reference(edition), "rule_index": position,
                  "observations": observations,
                  "source_receipt_sha256": digest(edition.get("source_receipt", {}))}
        entries.append({"format_version": FORMAT_VERSION, "id": digest(identity),
                        **identity, "sources": [source]})
    return entries


def merge_entries(existing, incoming):
    """Consolidate identical rules only; differing conditions never overwrite."""
    if existing is None:
        return deepcopy(incoming)
    if existing["id"] != incoming["id"]:
        raise ValueError("reasoning_bank_rule_identity_mismatch")
    merged = deepcopy(existing)
    sources = {digest(source): source for source in existing["sources"]}
    sources.update({digest(source): source for source in incoming["sources"]})
    merged["sources"] = [sources[key] for key in sorted(sources)]
    return merged


def memory_text(entry):
    if not entry["when"] and not entry["avoid"]:
        return entry["principle"]
    return (f"Applicable when: {entry['when']}\n"
            f"Principle: {entry['principle']}\nCounterexample / avoid: {entry['avoid']}")


def freeze_selection(config, selections):
    """Freeze complete selected rules in the existing system-experience contract."""
    if isinstance(config, dict) and "sha256" in config and "scopes" in config:
        raise ValueError("reasoning_bank_existing_snapshot_is_immutable")
    settings = validate_agent_experience(config)
    scopes = {}
    for scope, selection in selections.items():
        validate_scope(scope)
        receipt = deepcopy(selection["receipt"])
        receipt["applied"] = []
        receipt["omitted"] = []
        current = settings[scope]["text"]
        for match in selection.get("matches", []):
            entry = match["entry"]
            reference = {"memory_id": entry["id"], "memory_sha256": digest(entry),
                         "score": match["score"], "sources": deepcopy(entry["sources"])}
            if not settings[scope]["enabled"]:
                receipt["omitted"].append({**reference, "reason": "scope_disabled"})
                continue
            if entry["principle"] in current:
                receipt["applied"].append({**reference, "already_in_base_experience": True})
                continue
            block = ("\n\n<retrieved_procedural_experience>\n" + memory_text(entry)
                     + "\nThis is conditional guidance, not task evidence or a guarantee of success."
                     + "\n</retrieved_procedural_experience>")
            if len((current + block).encode("utf-8")) > MAX_EXPERIENCE_BYTES:
                receipt["omitted"].append({**reference, "reason": "experience_capacity"})
                continue
            current += block
            receipt["applied"].append(reference)
        settings[scope]["text"] = current
        scopes[scope] = receipt
    snapshot = snapshot_agent_experience(settings)
    receipt = {"format_version": FORMAT_VERSION, "kind": "reasoning_bank_selection",
               "scopes": scopes, "experience_sha256": snapshot["sha256"]}
    return {"config": settings, "snapshot": snapshot,
            "selection_receipt": {**receipt, "sha256": digest(receipt)}}
