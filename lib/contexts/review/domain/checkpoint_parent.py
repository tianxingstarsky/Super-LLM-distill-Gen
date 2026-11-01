"""Immutable review-only selection from a stopped, unsealed generation run.

This provenance is not a review verdict or a published artifact. It cannot
authorize repair, a manual approval, or promotion of an old checkpoint score.
"""
from copy import deepcopy
import re

from .candidate_content import candidate_digest
from lib.contexts.workflows.contracts import TARGETS


def validate_checkpoint_context(value):
    fields = {"version", "parent_run_id", "target", "candidate_id", "recipe_sha256",
              "stage_file", "stage_sha256", "candidate_sha256", "source_files",
              "checkpoint_files", "evidence", "evidence_sha256", "author_control",
              "snapshot_sha256"}
    if isinstance(value, dict) and value.get("version") == 2:
        fields |= {"source_stage", "parent_manifest_sha256"}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value.get("version")) is not int or value["version"] not in {1, 2}):
        raise ValueError("invalid_checkpoint_review_context")
    stage = value["target"] if value["version"] == 1 else "review-" + str(value["target"])
    if (not isinstance(value["parent_run_id"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", value["parent_run_id"])
            or not isinstance(value["candidate_id"], str) or not value["candidate_id"]
            or value["target"] not in TARGETS
            or value["stage_file"] != f"stage-results/{stage}.jsonl"
            or value["version"] == 2 and value["source_stage"] != stage):
        raise ValueError("invalid_checkpoint_review_context")
    for key in ("recipe_sha256", "stage_sha256", "candidate_sha256", "evidence_sha256", "snapshot_sha256"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{64}", value[key]):
            raise ValueError("invalid_checkpoint_review_context")
    if value["version"] == 2 and (not isinstance(value["parent_manifest_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["parent_manifest_sha256"])):
        raise ValueError("invalid_checkpoint_review_context")
    if not isinstance(value["evidence"], dict) or not isinstance(value["author_control"], dict):
        raise ValueError("invalid_checkpoint_review_context")
    for key, prefix in (("source_files", "inputs/"), ("checkpoint_files", "checkpoints/")):
        rows = value[key]
        if not isinstance(rows, list) or not (0 if key == "source_files" else 1) <= len(rows) <= 2000:
            raise ValueError("invalid_checkpoint_review_context")
        seen = set()
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"file", "sha256"}
                    or not isinstance(row["file"], str) or not row["file"].startswith(prefix)
                    or any(part in {"", ".", ".."} for part in row["file"].split("/"))
                    or any(char in row["file"] for char in "\\:\x00")
                    or row["file"] in seen or not isinstance(row["sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
                raise ValueError("invalid_checkpoint_review_context")
            seen.add(row["file"])
    if (candidate_digest(value["evidence"]) != value["evidence_sha256"]
            or candidate_digest({key: item for key, item in value.items() if key != "snapshot_sha256"})
            != value["snapshot_sha256"]):
        raise ValueError("checkpoint_review_snapshot_changed")
    return deepcopy(value)
