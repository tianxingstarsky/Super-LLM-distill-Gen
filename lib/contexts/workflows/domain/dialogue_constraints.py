"""Bind director constraints to actual user turns, never free paraphrases.

The director selects messages in the same planning call. The application owns
the quoted state and its receipts. This proves who said the words and when;
it does not certify the director's interpretation or the truth of a statement.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

from lib.domain.workflow_qa_director import validate_dialogue_step
from lib.domain.workflow_quality import canonical


class DialogueConstraintReferenceError(ValueError):
    """An invalid reference is a bounded planning defect, not a user constraint."""

    def __init__(self, code, indices):
        self.feedback = {
            "code": code,
            "user_message_indices": list(indices),
            "repair": (
                "Return user_constraint_refs as unique integers from user_message_indices. "
                "Omit dialogue_state.active_constraints; the program copies complete actual user messages. "
                "Assistant suggestions and the proposed next user_message are not existing user statements. "
                "Use an empty list when no existing user turn supplies a current constraint."
            ),
        }
        super().__init__(code)


def _user_message_indices(messages):
    return [index for index, message in enumerate(messages)
            if isinstance(message, dict) and message.get("role") == "user"
            and isinstance(message.get("content"), str) and message["content"].strip()]


def dialogue_constraint_reference_request(recipe, messages):
    """Pin this source protocol only for new tasks which explicitly enable it."""
    if "dialogue_constraint_provenance_version" not in recipe:
        return {}
    version = recipe["dialogue_constraint_provenance_version"]
    if type(version) is not int or version != 1:
        raise ValueError("unsupported_dialogue_constraint_provenance_version")
    return {"dialogue_constraint_provenance_version": 1,
            "user_message_indices": _user_message_indices(messages)}


def validate_referenced_dialogue_step(value, *, messages, **options):
    """Validate the new external boundary, then retain the legacy step rules.

    Full user utterances preserve negation, quotation and conditional scope.
    A quote of an assistant proposal is still only a user utterance: no semantic
    endorsement is inferred. New proposed utterances cannot self-confirm.
    """
    indices = _user_message_indices(messages)
    if not isinstance(value, dict):
        raise DialogueConstraintReferenceError("invalid_dialogue_constraint_refs", indices)
    state = value.get("dialogue_state")
    if isinstance(state, dict) and "active_constraints" in state:
        raise DialogueConstraintReferenceError("dialogue_constraints_are_program_owned", indices)
    refs = value.get("user_constraint_refs")
    if (not isinstance(refs, list) or len(refs) > 12
            or any(type(index) is not int or index not in indices for index in refs)
            or len(set(refs)) != len(refs)):
        raise DialogueConstraintReferenceError("invalid_dialogue_constraint_refs", indices)
    transported = deepcopy(value)
    transported.pop("user_constraint_refs")
    step = validate_dialogue_step(transported, **options)
    ordered_refs = sorted(refs)
    step["dialogue_state"]["active_constraints"] = [messages[index]["content"] for index in ordered_refs]
    step["constraint_provenance"] = {
        "version": 1,
        "authority": "actual_user_message_source_only",
        "semantic_status": "not_certified",
        "scope": "existing_messages_before_next_user_turn",
        "messages_sha256": hashlib.sha256(canonical(messages).encode("utf-8")).hexdigest(),
        "sources": [{"constraint_index": position, "message_index": index, "role": "user",
                     "content_sha256": hashlib.sha256(messages[index]["content"].encode("utf-8")).hexdigest()}
                    for position, index in enumerate(ordered_refs)],
    }
    return step
