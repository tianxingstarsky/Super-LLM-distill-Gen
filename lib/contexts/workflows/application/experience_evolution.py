"""Propose or save a user-owned experience edition without applying it to runs."""
from __future__ import annotations

from typing import Protocol

from ..domain.experience_evolution import (evolution_history, manual_snapshot, prepare_evolution_payload, proposal_snapshot,
    validate_identifier, validate_proposal, validate_scope)


class ExperienceEvolutionPort(Protocol):
    def propose(self, payload: dict) -> dict: ...
    def save(self, snapshot: dict) -> dict: ...
    def list_versions(self, scope: str) -> list[dict]: ...
    def get_version(self, identifier: str) -> dict: ...


class EvolveExperience:
    def __init__(self, port: ExperienceEvolutionPort):
        self._port = port

    def _parent(self, identifier, scope):
        if identifier is None:
            return None
        parent = self._port.get_version(validate_identifier(identifier))
        if parent["scope"] != scope:
            raise ValueError("experience_evolution_parent_scope_mismatch")
        return parent

    def propose(self, scope, current_text, episodes, *, instruction="", parent_id=None, source_receipt=None):
        payload = prepare_evolution_payload(scope, current_text, episodes, instruction=instruction,
            parent_id=parent_id, source_receipt=source_receipt)
        # Resolve lineage before purchasing a model proposal.
        parent = self._parent(parent_id, scope)
        editions = [parent] if parent else []
        if parent and parent["status"] == "personal" and parent["parent_id"]:
            prior = self._parent(parent["parent_id"], scope)
            if prior["status"] == "candidate":
                editions.insert(0, prior)
        payload["evolution_history"] = evolution_history(editions)
        proposal = validate_proposal(self._port.propose(payload), payload["episodes"])
        return self._port.save(proposal_snapshot(payload, proposal))

    def save_version(self, scope, text, *, parent_id=None, label="个人风格", status="personal"):
        validate_scope(scope)
        parent = self._parent(parent_id, scope)
        snapshot = manual_snapshot(scope, text, parent_id=parent_id, label=label, status=status,
                                   base_text=parent["text"] if parent else "")
        return self._port.save(snapshot)

    def list_versions(self, scope):
        return self._port.list_versions(validate_scope(scope))

    def get_version(self, identifier):
        return self._port.get_version(validate_identifier(identifier))
