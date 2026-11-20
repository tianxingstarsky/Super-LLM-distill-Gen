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
from lib.contexts.workflows.domain.reasoning_bank import memory_context_assignments, no_new_memory
from ..domain.guided_final_experience import FINAL_TEACHER_ROLE, is_final_teacher_edition
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
    return memory_context_assignments(items)


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
    agent_trace = episode.get("agent_episode_projection_version") == 1 and episode.get("target") == "agent"
    guided_trace = episode.get("guided_answer_projection_version") == 1
    teacher_draft = episode.get("guided_disclosure_projection_version") == 1
    teacher_final = episode.get("guided_final_projection_version") == 1
    teacher_followup = episode.get("guided_teacher_followup_projection_version") == 1
    if (teacher_final or teacher_followup) and user_outcome is not None:
        raise ValueError("final_teacher_assessment_must_be_bound_to_the_recorded_proposal")
    chronological = agent_trace or guided_trace or teacher_draft or teacher_final or teacher_followup
    thoughts, actions = [], []
    assistant_indexes = [index for index, message in enumerate(messages) if message.get("role") == "assistant"]
    for position, index in enumerate(assistant_indexes):
        message = messages[index]
        if message.get("role") != "assistant":
            continue
        reasoning = message.get("reasoning_content", message.get("reasoning", ""))
        thoughts.append("\n".join(reasoning) if isinstance(reasoning, list) else reasoning or "")
        action = {key: message[key] for key in (("role", "content", "tool_calls", "toolCalls")
                  if chronological else ("role", "content", "tool_calls")) if key in message}
        if chronological:
            end = assistant_indexes[position + 1] if position + 1 < len(assistant_indexes) else len(messages)
            action = {"assistant_action": action, "following_recorded_observations": deepcopy(messages[index + 1:end])}
        actions.append(json.dumps(action, ensure_ascii=False))
    query = (messages[:assistant_indexes[0]] if chronological and assistant_indexes else
             [message for message in messages if message.get("role") != "assistant"])
    return {"id": episode["id"], "query": json.dumps(query, ensure_ascii=False),
        "think_list": thoughts, "action_list": actions,
        "status": "success" if check["keep"] else "fail",
        "assessment_source": {"kind": ("reviewed_teacher_final_answer" if teacher_final else "user_label" if user_outcome else
                                       "observed_student_followup" if teacher_followup else "reviewed_teacher_disclosure" if teacher_draft else
                                       "program_key_and_tutor" if guided_trace else "recorded_model_autoeval"),
                              "independently_verified": teacher_final and episode["final_assessment"]["kind"] != "human",
                              **({"scope": episode["teacher_followup_assessment_scope"]}
                                 if teacher_followup and "teacher_followup_assessment_scope" in episode else {})},
        "feedback": (("Assessment of the teacher's public answer.\n" if teacher_final else
                     "This is a fallible reviewer observation, not established truth.\n") + check.get("reason", "")),
        **({"guided_final_projection_version": 1, "final_assessment": deepcopy(episode["final_assessment"])} if teacher_final else {}),
        **({"guided_teacher_followup_projection_version": 1, **({"teacher_action_features_version":
            episode["teacher_action_features_version"]} if "teacher_action_features_version" in episode else {})}
           if teacher_followup else {}),
        **({"agent_episode_projection_version": 1, "source_outcome": episode.get("source_outcome", "unknown")}
           if agent_trace else {}),
        **({"guided_answer_projection_version": 1} if guided_trace else {}),
        **({"guided_disclosure_projection_version": 1} if teacher_draft else {})}


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
        if parent and no_new_memory(parent.get("text")):
            raise ValueError("reasoning_bank_no_memory_to_adopt")
        if parent and parent["scope"] != scope:
            raise ValueError("experience_evolution_parent_scope_mismatch")
        final_teacher = episode.get("guided_final_projection_version") == 1
        teacher_followup = bool(episode.get("guided_teacher_followup_projection_version"))
        if parent and teacher_followup != any(e.get("guided_teacher_followup_projection_version") == 1 for e in parent["episodes"]):
            raise ValueError("disclosure_teacher_experience_parent_role_mismatch")
        if parent and is_final_teacher_edition(parent) != final_teacher:
            raise ValueError("final_teacher_experience_parent_role_mismatch")
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
                original = self.repository.get_version(saved["edition_id"])
                if policy and no_new_memory(original["text"]):
                    # Older builds persisted fenced no-change replies as editions. Preserve the original
                    # evidence, and replay its meaning without another inference or modifying that edition.
                    outcome = {"status": "no_update", "scope": scope, "parent_id": parent_id,
                        "source_receipt": original["source_receipt"], "source_sha256": source_hash,
                        "previous_edition_id": original["id"], "memory_policy": policy,
                        "upstream_result": original["upstream_result"], "model_request_receipt":
                            [{**call, "reused": True} for call in original["model_request_receipt"]]}
                    return {**outcome, "result_sha256": digest(outcome)}
                return original
            generation_scope = FINAL_TEACHER_ROLE if final_teacher else "disclosure_teacher" if teacher_followup else scope
            result = (self.inductor.induce(episode, generation_scope=generation_scope,
                current_memory=parent["text"] if parent else "") if policy else self.inductor.induce(episode))
            raw_items = result["memory"]["memory_items"]
            if policy and no_new_memory("\n\n".join(raw_items)):
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
            if no_new_memory(edition.get("text")):
                raise ValueError("reasoning_bank_no_memory_to_adopt")
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
