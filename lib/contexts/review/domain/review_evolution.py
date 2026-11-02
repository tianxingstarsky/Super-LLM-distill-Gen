"""One review's quality decision, optional general proposal and bounded rewrite."""
from copy import deepcopy
import re

from lib.domain.workflow_quality import accepted, verdict
from lib.contexts.workflows.contracts import validate_proposed_memory_items, validate_memory_context_assignment
from .candidate_content import candidate_digest as digest

_DIMENSIONS = ("correctness", "reasoning", "grounding", "instruction", "safety")


def native_sft_review_evolution_enabled(recipe, row):
    """Recorded imports keep their ordinary advisory path in mixed batches."""
    native = row.get("native_generation")
    return (recipe.get("native_sft_review_evolution_version") == 1
        and (isinstance(native, dict) and native.get("version") == 1
             or "native_sft_review_evolution" in row))


def native_sft_review_evolution_summary(records):
    """Separate the reviewed first draft from its unreviewed replacement."""
    summary = {"policy": "single_review_with_optional_native_optimization",
        "assessed_first_drafts": 0, "accepted_first_drafts": 0,
        "optimized_drafts": 0, "current_assessed": 0}
    for row in records:
        receipt = row.get("native_sft_review_evolution")
        if not isinstance(receipt, dict) or receipt.get("version") != 1:
            continue
        observation = (row.get("reviewed_first_draft") or {}).get("observation") or {}
        assessed = observation.get("status") == "observed" and isinstance(observation.get("verdict"), dict)
        summary["assessed_first_drafts"] += assessed
        summary["accepted_first_drafts"] += assessed and receipt.get("action") == "accepted"
        summary["optimized_drafts"] += receipt.get("action") == "revised"
        summary["current_assessed"] += (assessed and receipt.get("reviewed_current_candidate") is True
            and row.get("semantic_status") == "model_assessed")
    return summary


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def review_evolution_schema():
    # DeepSeek strict tools omit minLength/minItems. The unchanged domain
    # boundary below still rejects blank text and empty context assignments.
    text = {"type": "string", "description": "Nonblank literal text without NUL."}
    score = {"type": "integer", "minimum": 1, "maximum": 5}
    quality = _object({"keep": {"type": "boolean"}, "grounded": {"type": "boolean"},
        "reasoning_valid": {"type": "boolean"}, "correctness": score,
        "scores": _object({name: score for name in _DIMENSIONS}), "reason": text})
    memory = _object({"title": text, "description": text, "content": text,
        "context_types": {"type": "array", "items": {"type": "string",
            "enum": ["common", "direct", "narrative", "exploratory", "iterative", "emotional"]}}})
    return deepcopy(_object({"verdict": quality, "task_notes": {"type": "array", "items": text},
        "memory_proposal": _object({"status": {"type": "string", "enum": ["no_update", "proposed"]},
            "memory_items": {"type": "array", "items": memory}})}))


def review_evolution_submission_schema():
    """Flat provider submission; the program assembles nested business objects."""
    nested = review_evolution_schema()["properties"]
    quality = nested["verdict"]["properties"]
    proposal = nested["memory_proposal"]["properties"]
    properties = {name: quality[name] for name in ("keep", "grounded", "reasoning_valid")}
    properties.update(quality["scores"]["properties"])
    properties.update(reason=quality["reason"], task_notes=nested["task_notes"],
        memory_status=proposal["status"], memory_items=proposal["memory_items"])
    return deepcopy(_object(properties))


def parse_review_evolution_submission(value):
    if not isinstance(value, dict) or set(value) != set(review_evolution_submission_schema()["required"]):
        raise ValueError("invalid_review_evolution_submission")
    quality = {name: value[name] for name in ("keep", "grounded", "reasoning_valid", "correctness", "reason")}
    quality["scores"] = {name: value[name] for name in _DIMENSIONS}
    return parse_review_evolution({"verdict": quality, "task_notes": value["task_notes"],
        "memory_proposal": {"status": value["memory_status"], "memory_items": value["memory_items"]}})


def _literal_text(text):
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise ValueError("invalid_review_evolution_text")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid_review_evolution_text") from None
    return text


def render_review_memory_item(value, position=1):
    """The model supplies semantic fields; the program owns stored Markdown."""
    if not isinstance(value, dict) or set(value) != {"title", "description", "content", "context_types"}:
        raise ValueError("invalid_review_evolution_memory_item")
    fields = {key: _literal_text(value[key]) for key in ("title", "description", "content")}
    contexts = validate_memory_context_assignment(value["context_types"])
    # A heading quoted inside content cannot become another metadata field.
    fields = {key: re.sub(r"(?m)^(#{1,2} (?:Memory Item|Title|Description|Content|Contexts)(?:[ \t]|$))",
        lambda match: "\\" + match.group(1), text) for key, text in fields.items()}
    return (f"# Memory Item {position}\n\n## Title\n{fields['title']}\n\n## Description\n{fields['description']}"
        f"\n\n## Content\n{fields['content']}\n\n## Contexts " + ",".join(contexts))


def parse_review_evolution(value):
    if not isinstance(value, dict) or set(value) != {"verdict", "task_notes", "memory_proposal"}:
        raise ValueError("invalid_review_evolution_response")
    quality = value["verdict"]
    if not isinstance(quality, dict) or set(quality) != set(review_evolution_schema()["properties"]["verdict"]["required"]):
        raise ValueError("invalid_review_evolution_verdict")
    checked = verdict(quality)
    _literal_text(checked["reason"])
    notes = value["task_notes"]
    proposal = value["memory_proposal"]
    if not isinstance(notes, list):
        raise ValueError("invalid_review_evolution_task_notes")
    notes = [_literal_text(note) for note in notes]
    if (not isinstance(proposal, dict) or set(proposal) != {"status", "memory_items"}
            or not isinstance(proposal["status"], str) or proposal["status"] not in {"no_update", "proposed"}):
        raise ValueError("invalid_review_evolution_memory_proposal")
    if not isinstance(proposal["memory_items"], list):
        raise ValueError("invalid_review_evolution_memory_proposal")
    items = validate_proposed_memory_items([render_review_memory_item(item, index + 1)
        for index, item in enumerate(proposal["memory_items"])])
    if (proposal["status"] == "no_update") != (not items):
        raise ValueError("invalid_review_evolution_memory_proposal")
    return {"verdict": deepcopy(checked), "task_notes": notes,
        "memory_proposal": {"status": proposal["status"], "memory_items": items}}


def review_evolution_system_instruction(observation):
    blocks = []
    proposal = observation.get("memory_proposal", {"status": "no_update", "memory_items": []})
    if proposal["status"] == "proposed":
        blocks.append("<proposed_general_experience>\n以下是本次同一评审提出的可迁移处理原则，"
            "作为本次派生回答的候选通用指导；尚未独立验证，也未成为用户事实或全局采用版本。"
            "保留原有有效经验，仅在适用条件成立时使用，不将本题例子、专名或结论推广为规律。\n"
            + "\n\n".join(proposal["memory_items"]) + "\n</proposed_general_experience>")
    notes = observation.get("task_notes", [])
    if notes:
        blocks.append("<review_task_notes>\n以下是针对当前原问题和首稿的临时修正要求，仅用于这一条重新作答。"
            "它们来自可错的评审观察，不是用户的新发言，也不证明其中假设已成立；"
            "依据真实资料和用户要求核对后修正，不改任务、不补造条件，不播报内部审查过程。\n"
            + "\n\n".join(notes) + "\n</review_task_notes>")
    return "\n\n".join(blocks)


def review_evolution_step_snapshot(observation, recipe):
    body = {"version": 1, "origin": "same_review_derived_generation_step",
        "memory_proposal": deepcopy(observation.get("memory_proposal", {"status": "no_update", "memory_items": []})),
        "task_notes": deepcopy(observation.get("task_notes", [])),
        "base_experience_sha256": recipe.get("agent_experience", {}).get("sha256"),
        "base_catalog_sha256": recipe.get("dialogue_style_context_catalog", {}).get("sha256"),
        "system_instruction": review_evolution_system_instruction(observation), "globally_adopted": False}
    return {**body, "sha256": digest(body)}


def finalize_native_sft_review_evolution(original, observation, memory_receipt, *, config,
        original_payload, action, revised=None, revised_payload=None, task_identity=None,
        derived_snapshot=None, issue=None):
    if action not in {"accepted", "revised", "not_optimized", "invalid"}:
        raise ValueError("invalid_native_sft_review_evolution_action")
    check = observation.get("verdict")
    passed = bool(check and accepted(check) and min(check["scores"].values()) / 5 >= config["score_threshold"])
    if action == "accepted" and not passed or action == "revised" and (passed or revised is None
            or revised_payload is None or not observation.get("task_notes")
            or not isinstance(task_identity, dict) or set(task_identity) != {"original", "current"}
            or not task_identity["original"] or task_identity["original"] != task_identity["current"]):
        raise ValueError("invalid_native_sft_review_evolution_transition")
    if action == "revised" and (revised.get("qa_contract") != original.get("qa_contract")
            or revised.get("source_id") != original.get("source_id")
            or revised.get("messages", [])[:-1] != original.get("messages", [])[:-1]
            or any((revised.get("provided_source_context") or {}).get(key) !=
                   (original.get("provided_source_context") or {}).get(key) for key in ("scope", "text_sha256"))):
        raise ValueError("native_sft_review_evolution_task_changed")
    if derived_snapshot is not None and (derived_snapshot.get("sha256") != digest({
            key: value for key, value in derived_snapshot.items() if key != "sha256"})
            or derived_snapshot.get("system_instruction") != review_evolution_system_instruction(observation)):
        raise ValueError("native_sft_review_evolution_snapshot_mismatch")
    row = deepcopy(revised if action == "revised" else original)
    for field in ("judge", "style_check", "score", "scores", "review_observation", "review_repair", "experience_observation"):
        row.pop(field, None)
    archive = {"record": deepcopy(original), "payload": deepcopy(original_payload),
        "observation": deepcopy(observation), "memory_receipt": deepcopy(memory_receipt),
        "derived_snapshot": deepcopy(derived_snapshot)}
    payload = revised_payload if action == "revised" else original_payload
    receipt = {"version": 1, "action": action, "archive_sha256": digest(archive),
        "first_draft_candidate_sha256": digest(original_payload), "candidate_sha256": digest(payload),
        "observation_sha256": digest(observation), "config_sha256": digest(config),
        "task_identity": deepcopy(task_identity), "semantic_status": "model_assessed" if action == "accepted" else "not_assessed",
        "reviewed_current_candidate": action == "accepted", "issue": issue}
    status = ("quarantined" if action not in {"accepted", "revised"}
        or action == "revised" and row.get("status") == "quarantined" else "eligible")
    row.update(reviewed_first_draft=archive, native_sft_review_evolution=receipt,
        semantic_status=receipt["semantic_status"], status=status,
        review_repair={"mode": "auto", "node": "review", "status": "accepted" if action == "accepted" else "not_assessed",
            "candidate_sha256": receipt["candidate_sha256"], "config_sha256": receipt["config_sha256"],
            "attempts": int(action == "revised"), "score": min(check["scores"].values()) / 5 if action == "accepted" else None,
            "assessment": "single_review_first_draft_then_at_most_one_unreviewed_answer", "history": []})
    if action == "accepted":
        row["review_observation"] = deepcopy(observation)
    return row


def native_sft_review_evolution_release_valid(row, payload, config):
    if not isinstance(row, dict):
        return False
    receipt, archive = row.get("native_sft_review_evolution"), row.get("reviewed_first_draft")
    if not isinstance(receipt, dict) or not isinstance(archive, dict) or receipt.get("version") != 1:
        return False
    try:
        projected = row.get("reasoning_route") == "sft_native_projection"
        canonical_payload = payload
        if projected:
            messages = row["messages"]
            if (not messages or row.get("question") != messages[:-1]
                    or row.get("reasoning") != [messages[-1].get("reasoning_content", "")]
                    or row.get("answer") != messages[-1].get("content")
                    or payload != {key: row[key] for key in ("question", "reasoning", "answer")}):
                return False
            canonical_payload = {"messages": messages}
        if (receipt["archive_sha256"] != digest(archive) or receipt["candidate_sha256"] != digest(canonical_payload)
                or receipt["config_sha256"] != digest(config)
                or receipt["first_draft_candidate_sha256"] != digest(archive["payload"])
                or receipt["observation_sha256"] != digest(archive["observation"])):
            return False
        rebuilt = finalize_native_sft_review_evolution(archive["record"], archive["observation"], archive["memory_receipt"],
            config=config, original_payload=archive["payload"], action=receipt["action"],
            revised=row if receipt["action"] == "revised" else None,
            revised_payload=canonical_payload if receipt["action"] == "revised" else None,
            task_identity=receipt["task_identity"], derived_snapshot=archive["derived_snapshot"], issue=receipt.get("issue"))
        expected_repair = rebuilt["review_repair"]
        if projected:
            expected_repair["candidate_sha256"] = digest(payload)
            expected_repair["derived_from"] = {"target": "sft", "candidate_id": row["id"]}
        return (rebuilt["native_sft_review_evolution"] == receipt and expected_repair == row.get("review_repair")
            and rebuilt["semantic_status"] == row.get("semantic_status") and rebuilt["status"] == row.get("status")
            and (receipt["action"] != "revised" or not any(field in row for field in ("judge", "score", "scores", "review_observation"))))
    except (KeyError, TypeError, ValueError, UnicodeError):
        return False
