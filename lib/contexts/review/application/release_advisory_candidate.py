"""One review observation, followed by delivery with its original findings."""
from copy import deepcopy

from lib.domain.workflow_quality import accepted, verdict
from ..domain.candidate_content import candidate_digest


class ReleaseAdvisoryCandidate:
    def __init__(self, port, config):
        self.port, self.config = port, config

    def execute(self, target, row):
        payload = self.port.payload(target, row)
        candidate_hash = candidate_digest(payload)
        previous = row.get("review_repair", {})
        observation = None
        if previous.get("candidate_sha256") == candidate_hash and isinstance(previous.get("verdict"), dict):
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
        receipt = {"mode": "auto", "node": "review", "status": "released_advisory", "policy_version": 1,
            "attempts": 0, "candidate_sha256": candidate_hash, "config_sha256": candidate_digest(self.config),
            "observation_sha256": candidate_digest(observation),
            "review_status": ("accepted" if accepted(check) else "rejected") if check else observation["status"],
            "prior_status": row.get("status"), "prior_reason": row.get("reason"),
            "score": min(check["scores"].values()) / 5 if check else None,
            "history": [], **({"verdict": deepcopy(check)} if check else {})}
        row.update(status="eligible", review_repair=receipt)
        row.pop("reason", None)
        row["experience_observation"] = self.port.record_advisory_observation(target, row)
        row["_review_target"] = target
        return [row]
