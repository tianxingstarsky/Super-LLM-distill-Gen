"""Propose or save a user-owned experience edition without applying it to runs."""
from __future__ import annotations

from typing import Protocol

from ..domain.experience_evolution import (digest, evolution_history, manual_snapshot, prepare_evolution_payload,
    prepare_experience_observation, proposal_snapshot, validate_identifier, validate_proposal, validate_scope)


class ExperienceEvolutionPort(Protocol):
    def propose(self, payload: dict) -> dict: ...
    def save(self, snapshot: dict) -> dict: ...
    def list_versions(self, scope: str) -> list[dict]: ...
    def get_version(self, identifier: str) -> dict: ...
    def record_observation(self, observation: dict) -> dict: ...
    def get_observation(self, identifier: str) -> dict: ...
    def claim_observation_batch(self, scope: str, **options) -> dict: ...
    def get_observation_batch(self, identifier: str) -> dict: ...
    def release_observation_batch(self, identifier: str) -> dict: ...
    def observation_progress(self, scope: str, **options) -> dict: ...
    def observation_batch_guard(self, identifier: str): ...
    def bind_observation_batch_request(self, identifier: str, request_hash: str): ...
    def complete_observation_batch(self, identifier: str, edition_id: str, request_hash: str): ...
    def complete_official_observation_batch(self, identifier: str, outcome: dict, request_hash: str): ...
    def fail_observation_batch(self, identifier: str, error_type: str): ...


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

    def record_observation(self, scope, episode, *, run_id, outcome="unknown", evidence_status="unverified",
                           source_record=None, source_receipt=None):
        observation = prepare_experience_observation(scope, episode, run_id=run_id, outcome=outcome,
            evidence_status=evidence_status, source_record=source_record, source_receipt=source_receipt)
        return self._port.record_observation(observation)

    def get_observation(self, identifier):
        return self._port.get_observation(identifier)

    def complete_official_observation_batch(self, identifier, edition_id):
        """Consume the exact upstream input, including a durable no-update result."""
        validate_identifier(identifier)
        if isinstance(edition_id, dict):
            if edition_id.get("status") != "no_update":
                raise ValueError("experience_evolution_official_batch_mismatch")
            outcome = edition_id
        else:
            outcome = self._port.get_version(validate_identifier(edition_id))
        batch = self._port.get_observation_batch(identifier)
        if len(batch["records"]) != 1:
            raise ValueError("experience_evolution_official_batch_mismatch")
        request_hash = digest({"engine": "google_reasoning_bank", "source": batch["records"]})
        return self._port.complete_official_observation_batch(identifier, outcome, request_hash)

    def claim_observation_batch(self, scope, *, max_records=8, max_chars=60000, run_id=None):
        validate_scope(scope)
        validate_identifier(run_id, optional=True)
        if (type(max_records) is not int or not 1 <= max_records <= 8
                or type(max_chars) is not int or not 1 <= max_chars <= 60000):
            raise ValueError("experience_evolution_invalid_observation_limits")
        return self._port.claim_observation_batch(scope, max_records=max_records, max_chars=max_chars, run_id=run_id)

    def get_observation_batch(self, identifier):
        return self._port.get_observation_batch(validate_identifier(identifier))

    def release_observation_batch(self, identifier):
        return self._port.release_observation_batch(validate_identifier(identifier))

    def observation_progress(self, scope, *, run_id=None):
        validate_scope(scope)
        validate_identifier(run_id, optional=True)
        return self._port.observation_progress(scope, run_id=run_id)

    def propose_observation_batch(self, identifier, current_text, *, instruction="", parent_id=None):
        """Explicitly distil one claimed batch; never called by sample recording."""
        validate_identifier(identifier)
        with self._port.observation_batch_guard(identifier):
            batch = self._port.get_observation_batch(identifier)
            request = {"current_text": current_text, "instruction": instruction, "parent_id": parent_id}
            request_hash = digest(request)
            if batch["status"] == "proposed":
                if batch["request_sha256"] != request_hash:
                    raise ValueError("experience_evolution_batch_request_changed")
                return self._port.get_version(batch["edition_id"])
            self._port.bind_observation_batch_request(identifier, request_hash)
            try:
                edition = self.propose(batch["scope"], current_text, batch["records"], instruction=instruction,
                    parent_id=parent_id, source_receipt=batch["source_receipt"])
            except Exception as error:
                self._port.fail_observation_batch(identifier, type(error).__name__)
                raise
            self._port.complete_observation_batch(identifier, edition["id"], request_hash)
            return edition
