"""One review observation, followed by delivery with its original findings."""
from copy import deepcopy

from lib.domain.workflow_quality import accepted, verdict
from lib.domain.multiturn import completed_turn_ends
from lib.contexts.workflows.contracts import (
    authenticated_record_context_positions, authenticated_review_guidance_positions,
)
from ..domain.candidate_content import candidate_digest


class ReleaseAdvisoryCandidate:
    def __init__(self, port, config):
        self.port, self.config = port, config

    def execute(self, target, row):
        marker = row.get("multiturn_advisory_review") or {}
        blocked = (target == "multiturn" and type(marker.get("version")) is int
            and marker["version"] == 1 and marker.get("local_validation") == "rejected")
        incomplete = False
        if blocked:
            positions = tuple(sorted(set(authenticated_record_context_positions(row)) |
                set(authenticated_review_guidance_positions(row))))
            _, issue = completed_turn_ends(row.get("messages"), public_system_positions=positions)
            incomplete = bool(issue or row.get("reason") in {
                "native_reasoning_missing_or_invalid", "native_answer_missing_or_invalid"})
        try:
            candidate_hash = candidate_digest(self.port.payload(target, row))
        except (KeyError, TypeError, ValueError):
            if not blocked:
                raise
            candidate_hash, incomplete = None, True
        previous = row.get("review_repair", {})
        observation = ({"status": "not_called", "model_called": False,
            "reason": "incomplete_multiturn_candidate", "local_reason": row.get("reason")}
            if incomplete else None)
        if (blocked and previous.get("status") == "rejected"
                and previous.get("candidate_sha256") == candidate_hash
                and previous.get("config_sha256") == candidate_digest(self.config)
                and isinstance(row.get("review_observation"), dict)
                and previous.get("observation_sha256") == candidate_digest(row["review_observation"])):
            observation = deepcopy(row["review_observation"])
        if observation is None and previous.get("candidate_sha256") == candidate_hash and isinstance(previous.get("verdict"), dict):
            try:
                checked = verdict(previous["verdict"])
            except ValueError:
                pass
            else:
                observation = {"status": "observed", "verdict": deepcopy(checked),
                    "origin": "reused_bound_review", "candidate_sha256": candidate_hash,
                    "prior_receipt_sha256": candidate_digest(previous)}
        if observation is None:
            observation = self.port.review_once(target, row, self.port.evidence(row), "")
        row["review_observation"] = deepcopy(observation)
        check = observation.get("verdict")
        receipt = {"mode": "auto", "node": "review", "status": "rejected" if blocked else "released_advisory", "policy_version": 1,
            "attempts": 0, "candidate_sha256": candidate_hash, "config_sha256": candidate_digest(self.config),
            "observation_sha256": candidate_digest(observation),
            "review_status": ("accepted" if accepted(check) else "rejected") if check else observation["status"],
            "prior_status": row.get("status"), "prior_reason": row.get("reason"),
            "score": min(check["scores"].values()) / 5 if check else None,
            "history": [], **({"verdict": deepcopy(check)} if check else {}),
            **({"terminal": True, "assessment": "not_performed_incomplete_multiturn" if incomplete else
                "advisory_observed_local_validation_rejected", "original_reason": row.get("reason")}
               if blocked else {})}
        row.update(status="quarantined" if blocked else "eligible", review_repair=receipt)
        if not blocked:
            row.pop("reason", None)
        row["experience_observation"] = self.port.record_advisory_observation(target, row)
        row["_review_target"] = target
        return [row]
