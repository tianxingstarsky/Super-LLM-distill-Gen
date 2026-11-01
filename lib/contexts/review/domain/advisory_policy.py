"""Authenticate policy-authorized delivery separately from reviewer approval."""
from .candidate_content import candidate_digest


def advisory_release_valid(row, payload, config):
    """Bind an advisory delivery to its exact candidate and unchanged opinion.

    This receipt does not certify a passing grade. Existing structure and file
    integrity checks remain the caller's responsibility. A false/unknown model
    verdict is retained as an observation rather than rewritten into approval.
    """
    if not isinstance(row, dict):
        return False
    receipt, observation = row.get("review_repair"), row.get("review_observation")
    if (not isinstance(receipt, dict) or receipt.get("status") != "released_advisory"
            or type(receipt.get("policy_version")) is not int or receipt["policy_version"] != 1
            or not isinstance(observation, dict) or not observation or not isinstance(config, dict)):
        return False
    try:
        return (receipt.get("candidate_sha256") == candidate_digest(payload)
                and receipt.get("config_sha256") == candidate_digest(config)
                and receipt.get("observation_sha256") == candidate_digest(observation))
    except (TypeError, ValueError, UnicodeError):
        return False
