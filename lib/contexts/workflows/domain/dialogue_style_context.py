"""Coarse, current-turn dialogue contexts; never a topic or a permanent persona."""
from copy import deepcopy
import json

from .experience_evolution import digest

CONTEXT_TYPES = ("direct", "narrative", "exploratory", "iterative", "emotional")
ALL_CONTEXT_TYPES = ("common", *CONTEXT_TYPES)
_OPEN = "<dialogue_style_context>"
_CLOSE = "</dialogue_style_context>"


def validate_context_types(value):
    if value is None:
        return []
    if (not isinstance(value, list) or any(not isinstance(item, str) or item not in CONTEXT_TYPES for item in value)
            or len(set(value)) != len(value)):
        raise ValueError("invalid_dialogue_context_types")
    return [kind for kind in CONTEXT_TYPES if kind in value]


def freeze_catalog(contexts):
    if not isinstance(contexts, dict) or set(contexts) != set(ALL_CONTEXT_TYPES):
        raise ValueError("invalid_dialogue_context_catalog")
    for item in contexts.values():
        if (not isinstance(item, dict) or set(item) - {"label", "description", "content", "memory_blocks"}
                or not {"label", "description", "content"}.issubset(item)
                or any(not isinstance(text, str) or not text.strip() or len(text) > 6000 or "\x00" in text
                       for text in (item["label"], item["description"], item["content"]))):
            raise ValueError("invalid_dialogue_context_catalog")
        memories = item.get("memory_blocks", [])
        if not isinstance(memories, list):
            raise ValueError("invalid_dialogue_context_catalog")
        identifiers = set()
        for memory in memories:
            if (not isinstance(memory, dict) or set(memory) != {"id", "block"}
                    or not isinstance(memory["id"], str) or not memory["id"] or memory["id"] in identifiers
                    or not isinstance(memory["block"], str) or not memory["block"]
                    or item["content"].count(memory["block"]) != 1):
                raise ValueError("invalid_dialogue_context_catalog")
            identifiers.add(memory["id"])
    body = {"version": 1, "contexts": deepcopy(contexts)}
    return {**body, "sha256": digest(body)}


def catalog_index(recipe):
    if recipe.get("dialogue_style_context_version") != 1:
        return None
    catalog = recipe["dialogue_style_context_catalog"]
    return {"version": 1, "catalog_sha256": catalog["sha256"], "contexts": [
        {"id": kind, "label": catalog["contexts"][kind]["label"],
         "description": catalog["contexts"][kind]["description"]} for kind in CONTEXT_TYPES]}


def context_system_message(recipe, selected):
    if recipe.get("dialogue_style_context_version") != 1:
        return None, None
    selected = validate_context_types(selected)
    catalog = recipe["dialogue_style_context_catalog"]
    metadata = {"version": 1, "catalog_sha256": catalog["sha256"], "context_types": selected}
    blocks, loaded = [], set()
    for kind in ("common", *selected):
        context = catalog["contexts"][kind]
        block = context["content"]
        for memory in context.get("memory_blocks", []):
            if memory["id"] in loaded:
                block = block.replace(memory["block"], "", 1)
            else:
                loaded.add(memory["id"])
        blocks.append(block)
    message = {"role": "system", "content": _OPEN + "\n" + json.dumps(metadata, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")) + "\n" + "\n\n".join(blocks) + "\n" + _CLOSE}
    return message, {**metadata, "message_sha256": digest(message), "message": deepcopy(message),
        "origin": "program_frozen_dialogue_style_context", "visibility": "learner_system_message_exported_verbatim",
        "content_quality": "not_assessed"}


def _note_metadata(message):
    if not isinstance(message, dict) or message.get("role") != "system":
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.startswith(_OPEN + "\n") or not content.endswith("\n" + _CLOSE):
        return None
    try:
        metadata = json.loads(content.split("\n", 2)[1])
        if (not isinstance(metadata, dict) or set(metadata) != {"version", "catalog_sha256", "context_types"}
                or type(metadata["version"]) is not int or metadata["version"] != 1):
            return None
        validate_context_types(metadata["context_types"])
        return metadata
    except (ValueError, IndexError):
        return None


def authenticated_context_positions(messages, recipe):
    if recipe.get("dialogue_style_context_version") != 1 or not isinstance(messages, list):
        return ()
    positions = []
    for index, message in enumerate(messages):
        metadata = _note_metadata(message)
        if metadata is not None and message == context_system_message(recipe, metadata["context_types"])[0]:
            positions.append(index)
    return tuple(positions)


def context_message_provenance(messages, recipe):
    return [{**context_system_message(recipe, _note_metadata(messages[index])["context_types"])[1],
             "message_index": index} for index in authenticated_context_positions(messages, recipe)]


def authenticated_record_context_positions(record):
    """Recognize exact exported notes from saved program receipts, not words alone."""
    messages = record.get("messages", record.get("question", []))
    if not isinstance(messages, list):
        return ()
    receipts = []
    containers = [record.get("native_generation", {}), record.get("public_experience", {})]
    containers.extend(turn.get("native_generation", {}) for turn in record.get("turn_reviews", []) if isinstance(turn, dict))
    for container in containers:
        if not isinstance(container, dict):
            continue
        values = container.get("dialogue_style_contexts", container.get("dialogue_contexts", []))
        receipts.extend(values if isinstance(values, list) else [values])
    positions = set()
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        index = receipt.get("message_index")
        if type(index) is not int or not 0 <= index < len(messages):
            continue
        message = messages[index]
        metadata = _note_metadata(message)
        if (metadata is not None and message == receipt.get("message")
                and digest(message) == receipt.get("message_sha256")
                and receipt.get("origin") == "program_frozen_dialogue_style_context"
                and all(metadata[key] == receipt.get(key) for key in metadata)):
            positions.add(index)
    return tuple(sorted(positions))
