"""Compose the original Google induction and retrieval with our storage/model ports.

This is a format/provider adapter, not a replacement evolutionary algorithm.
Google's unchanged main, prompts, screening and selection run from the pinned
complete source checkout. Published memory remains model advice, not ground truth.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

from filelock import FileLock

from lib.contexts.workflows.domain.experience_evolution import FORMAT_VERSION, digest, validate_scope, validate_experience_text
from lib.io_utils import atomic_json


def recorded_episode(observation, user_outcome=None):
    """Project actual dialogue and the one recorded review; never invent thought."""
    episode = observation.get("episode", observation)
    messages = episode.get("conversation", episode).get("messages", [])
    if not messages:
        raise ValueError("reasoning_bank_conversation_required")
    check = (episode.get("review_observation", {}).get("verdict")
             or episode.get("review", {}).get("verdict"))
    if user_outcome is not None:
        if user_outcome not in {"success", "failure"}:
            raise ValueError("reasoning_bank_invalid_user_outcome")
        check = {"keep": user_outcome == "success", "reason": "Outcome explicitly labelled by the operator."}
    if not isinstance(check, dict) or type(check.get("keep")) is not bool:
        raise ValueError("reasoning_bank_no_observed_outcome")
    thoughts, actions = [], []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        reasoning = message.get("reasoning_content", message.get("reasoning", ""))
        thoughts.append("\n".join(reasoning) if isinstance(reasoning, list) else reasoning or "")
        actions.append(json.dumps({key: message[key] for key in ("role", "content", "tool_calls")
                                  if key in message}, ensure_ascii=False))
    return {"id": episode["id"], "query": json.dumps([message for message in messages
            if message.get("role") != "assistant"], ensure_ascii=False),
        "think_list": thoughts, "action_list": actions,
        "status": "success" if check["keep"] else "fail",
        "assessment_source": {"kind": "user_label" if user_outcome else "recorded_model_autoeval", "independently_verified": False},
        "feedback": "This is a fallible reviewer observation, not established truth.\n" + check.get("reason", "")}


class GoogleReasoningBank:
    def __init__(self, repository, bank, experience, inductor, binding):
        self.repository, self.bank, self.experience = repository, bank, experience
        self.inductor, self.binding = inductor, deepcopy(binding)

    def learn(self, scope, observation, *, parent_id=None, user_outcome=None):
        validate_scope(scope)
        if scope == "review":
            raise ValueError("reasoning_bank_generation_scope_required")
        episode = recorded_episode(observation, user_outcome)
        source_hash = digest({"scope": scope, "episode": episode, "binding": self.binding, "parent_id": parent_id})
        receipt = self.repository.root / "official-editions" / (source_hash + ".json")
        receipt.parent.mkdir(exist_ok=True)
        with FileLock(str(receipt) + ".lock"):
            if receipt.exists():
                return self.repository.get_version(json.loads(receipt.read_text("utf-8"))["edition_id"])
            if parent_id:
                if self.repository.get_version(parent_id)["scope"] != scope:
                    raise ValueError("experience_evolution_parent_scope_mismatch")
            result = self.inductor.induce(episode)
            items = [text for text in result["memory"]["memory_items"] if text.strip()]
            text = validate_experience_text("\n\n".join(items))
            edition = self.repository.save({"format_version": FORMAT_VERSION, "scope": scope,
                "status": "candidate", "label": "Google ReasoningBank", "parent_id": parent_id,
                "base_text": "", "text": text, "summary": "Original upstream memory induction",
                "rules": [], "episodes": [episode], "source_receipt": {"observation_sha256": digest(observation)},
                "instruction": "", "quality": "upstream_model_proposed_not_verified",
                "upstream_memory_items": items, "upstream": result["upstream"],
                "model_request_receipt": result["model_calls"], "upstream_result": result})
            atomic_json(receipt, {"edition_id": edition["id"], "source_sha256": source_hash})
            return edition

    def active(self):
        path = self.repository.root / "reasoning-bank-active.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else {"scopes": {}, "history": []}

    def activate(self, scope, edition_id):
        validate_scope(scope)
        if scope == "review" and edition_id is not None:
            raise ValueError("reasoning_bank_generation_scope_required")
        path = self.repository.root / "reasoning-bank-active.json"
        if edition_id:
            edition = self.repository.get_version(edition_id)
            if edition["scope"] != scope:
                raise ValueError("experience_evolution_parent_scope_mismatch")
            indexed = self.bank.index_edition(edition_id)
            if indexed["status"] != "indexed":
                return indexed
        with FileLock(str(path) + ".lock"):
            state = self.active()
            previous = state["scopes"].get(scope)
            if edition_id:
                state["scopes"][scope] = edition_id
            else:
                state["scopes"].pop(scope, None)
            state["history"].append({"scope": scope, "previous": previous, "selected": edition_id,
                "at": datetime.now(timezone.utc).isoformat()})
            atomic_json(path, state)
        return {"status": "active" if edition_id else "disabled", "scope": scope, "edition_id": edition_id}

    def freeze(self, config, task_text):
        active = {scope: identifier for scope, identifier in self.active()["scopes"].items()
                  if scope != "review"}
        return self.bank.freeze(config, {scope: task_text for scope in active},
            edition_ids=list(active.values())) if active else None
