"""Compose the original Google induction and retrieval with our storage/model ports.

This is a format/provider adapter, not a replacement evolutionary algorithm.
Google's unchanged main, prompts, screening and selection run from the pinned
complete source checkout. Published memory remains model advice, not ground truth.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re

from filelock import FileLock

from lib.contexts.workflows.domain.experience_evolution import FORMAT_VERSION, digest, validate_scope, validate_experience_text
from lib.contexts.workflows.domain.reasoning_bank import validate_memory_context_assignment
from lib.io_utils import atomic_json


def _complete_memory_items(parts):
    """Restore complete upstream Markdown items after its blank-line projection."""
    text = "\n\n".join(parts).strip()
    headings = list(re.finditer(r"(?m)^# Memory Item [^\n]+$", text))
    if not headings or text[:headings[0].start()].strip():
        return [text] if text else []
    return [text[heading.start():headings[index + 1].start() if index + 1 < len(headings) else len(text)].strip()
            for index, heading in enumerate(headings)]


def _dialogue_context_assignments(items):
    """Read only explicit style metadata; untagged historical memories are common."""
    assignments = {}
    for item in items:
        headings = re.findall(r"(?m)^## Contexts(?:[ \t]+([^\r\n]*))?[ \t]*$", item)
        markers = re.findall(r"(?mi)^##[ \t]+contexts(?=[ \t:]|$)[^\r\n]*$", item)
        if len(headings) > 1 or len(markers) != len(headings):
            raise ValueError("reasoning_bank_invalid_dialogue_context_assignment")
        labels = [part.strip() for part in headings[0].split(",")] if headings else ["common"]
        try:
            labels = validate_memory_context_assignment(labels)
        except ValueError:
            raise ValueError("reasoning_bank_invalid_dialogue_context_assignment") from None
        assignments[digest(item)] = labels
    return assignments


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
        parent = self.repository.get_version(parent_id) if parent_id else None
        if parent and parent["scope"] != scope:
            raise ValueError("experience_evolution_parent_scope_mismatch")
        policy = self.inductor.policy_identity
        identity = {"scope": scope, "episode": episode, "binding": self.binding, "parent_id": parent_id}
        if policy:
            identity["memory_policy"] = policy
        source_hash = digest(identity)
        receipt = self.repository.root / "official-editions" / (source_hash + ".json")
        receipt.parent.mkdir(exist_ok=True)
        with FileLock(str(receipt) + ".lock"):
            if receipt.exists():
                saved = json.loads(receipt.read_text("utf-8"))
                if saved.get("source_sha256") != source_hash:
                    raise ValueError("reasoning_bank_learning_receipt_changed")
                if saved.get("status") == "no_update":
                    body = {key: value for key, value in saved.items() if key != "result_sha256"}
                    if saved.get("result_sha256") != digest(body):
                        raise ValueError("reasoning_bank_learning_receipt_changed")
                    return saved
                return self.repository.get_version(saved["edition_id"])
            result = (self.inductor.induce(episode, generation_scope=scope,
                current_memory=parent["text"] if parent else "") if policy else self.inductor.induce(episode))
            raw_items = result["memory"]["memory_items"]
            if policy and "\n\n".join(raw_items).strip() == "<no-new-memory/>":
                outcome = {"status": "no_update", "scope": scope, "parent_id": parent_id,
                    "source_receipt": {"observation_sha256": digest(observation)},
                    "source_sha256": source_hash, "memory_policy": policy,
                    "upstream_result": result, "model_request_receipt": result["model_calls"]}
                outcome["result_sha256"] = digest(outcome)
                atomic_json(receipt, outcome)
                return outcome
            items = (_complete_memory_items(raw_items) if policy else [text for text in raw_items if text.strip()])
            text = validate_experience_text("\n\n".join(items))
            context_assignment = ({"dialogue_context_assignment_version": 1,
                "dialogue_context_assignments": _dialogue_context_assignments(items)}
                if policy and policy["version"] == "1.1.0" else {})
            edition = self.repository.save({"format_version": FORMAT_VERSION, "scope": scope,
                "status": "candidate", "label": "Google ReasoningBank", "parent_id": parent_id,
                "base_text": parent["text"] if policy and parent else "", "text": text,
                "summary": "Generalized upstream memory induction" if policy else "Original upstream memory induction",
                "rules": [], "episodes": [episode], "source_receipt": {"observation_sha256": digest(observation)},
                "instruction": "", "quality": "upstream_model_proposed_not_verified",
                "upstream_memory_items": items, "upstream": result["upstream"],
                "model_request_receipt": result["model_calls"], "upstream_result": result,
                **context_assignment})
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

    def freeze(self, config, task_text, *, excluded_scopes=(), context_types=None):
        excluded = {validate_scope(scope) for scope in excluded_scopes}
        active = {scope: identifier for scope, identifier in self.active()["scopes"].items()
                  if scope != "review" and scope not in excluded}
        context_filter = {"context_types": context_types} if context_types is not None else {}
        return self.bank.freeze(config, {scope: task_text for scope in active},
            edition_ids=list(active.values()), **context_filter) if active else None
