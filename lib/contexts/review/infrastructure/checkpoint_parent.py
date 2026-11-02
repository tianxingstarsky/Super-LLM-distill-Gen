"""Authenticate stopped-run snapshots without fabricating a release manifest."""
from __future__ import annotations

from copy import deepcopy
import json

from filelock import FileLock

from lib.contexts.workflows.contracts import TARGETS, attach_node_control_text
from lib.infrastructure.human_revision import revision_evidence
from lib.infrastructure.workflow_store import digest, file_hash, read_json, verify_artifacts
from ..domain.checkpoint_parent import validate_checkpoint_context


def _path(parent, relative):
    path = parent / relative
    if not path.resolve().is_relative_to(parent.resolve()):
        raise ValueError("checkpoint_review_source_changed")
    current = path
    while current != parent:
        if current.is_symlink():
            raise ValueError("checkpoint_review_source_changed")
        current = current.parent
    return path


def _read_candidate(parent, stage, candidate_id):
    matches = []
    with _path(parent, f"stage-results/{stage}.jsonl").open(encoding="utf-8") as records:
        while line := records.readline(20_000_001):
            if len(line) > 20_000_000:
                raise ValueError("checkpoint_review_candidate_too_large")
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("checkpoint_review_candidate_invalid")
            if record.get("id") == candidate_id:
                matches.append(record)
                if len(matches) > 1:
                    break
    if len(matches) != 1:
        raise ValueError("checkpoint_review_candidate_not_unique")
    return matches[0]


def _checkpoint_files(parent):
    files = []
    for path in sorted((parent / "checkpoints").rglob("*.json")):
        relative = path.relative_to(parent).as_posix()
        value = read_json(_path(parent, relative))
        if not isinstance(value, dict) or "data" not in value or digest(value["data"]) != value.get("sha256"):
            raise ValueError("checkpoint_integrity_error")
        files.append({"file": relative, "sha256": file_hash(path)})
        if isinstance(value["data"], dict) and value["data"].get("storage") == "rows-v1":
            rows = _path(parent, path.with_suffix(".jsonl").relative_to(parent).as_posix())
            if file_hash(rows) != value["data"].get("file_sha256"):
                raise ValueError("checkpoint_integrity_error")
            files.append({"file": rows.relative_to(parent).as_posix(), "sha256": file_hash(rows)})
        if len(files) > 2000:
            raise ValueError("checkpoint_review_selection_too_large")
    return files


def checkpoint_review_input(output, parent_run_id, target, candidate_id):
    """Read one exact stopped-run candidate and all existing checkpoint hashes.

    The return value is suitable for create_run(repair_inputs=[...]) with
    review_repair={mode: auto, max_rounds: 0}. No parent file is rewritten.
    """
    return _checkpoint_review_input(output, parent_run_id, target, candidate_id, reviewed=False)


def reviewed_checkpoint_input(output, parent_run_id, target, candidate_id):
    """Select an already sealed review result; version 1 semantics stay frozen."""
    return _checkpoint_review_input(output, parent_run_id, target, candidate_id, reviewed=True)


def _checkpoint_review_input(output, parent_run_id, target, candidate_id, *, reviewed):
    from lib.infrastructure.training_workflow import run_path
    if target not in TARGETS:
        raise ValueError("invalid_checkpoint_review_target")
    parent = run_path(output, parent_run_id)
    if parent.is_symlink():
        raise ValueError("checkpoint_review_source_changed")
    with FileLock(str(parent / ".run.lock"), timeout=0):
        recipe = read_json(_path(parent, "recipe.json"))
        state = read_json(_path(parent, "state.json"))
        if state.get("status") not in ({"completed", "needs_attention"} if reviewed else {"failed", "cancelled"}):
            raise ValueError("checkpoint_review_parent_not_stopped")
        if (state.get("id") != parent_run_id or digest(recipe) != state.get("recipe_hash")
                or not reviewed and (parent / "artifacts/manifest.json").exists()):
            raise ValueError("checkpoint_review_parent_identity_changed")
        if reviewed:
            manifest = verify_artifacts(parent)
            if manifest.get("run_id") != parent_run_id or manifest.get("recipe_hash") != digest(recipe):
                raise ValueError("checkpoint_review_parent_identity_changed")
        source_files = []
        for source in recipe["sources"]:
            relative = "inputs/" + source["file"]
            path = _path(parent, relative)
            if file_hash(path) != source["sha256"]:
                raise ValueError("checkpoint_review_source_changed")
            source_files.append({"file": relative, "sha256": source["sha256"]})
        stage = "review-" + target if reviewed else target
        record = _read_candidate(parent, stage, candidate_id)
        prior = record.get("checkpoint_context") if reviewed else None
        if reviewed and not prior and recipe.get("sft_reanswer_version") == 1:
            prior = (record.get("sft_reanswer") or {}).get("parent_context")
        if prior:
            selected = [item for item in recipe.get("repair_inputs", [])
                        if item.get("checkpoint_context") == prior and item.get("target") == target]
            if len(selected) != 1 or prior.get("parent_run_id") == parent_run_id:
                raise ValueError("checkpoint_review_parent_identity_changed")
            # The sealed child binds the exact previous selection. Authenticate
            # its real source again; never invent evidence from empty sources.
            authenticated = verify_checkpoint_review_input(output, selected[0])
            evidence = deepcopy(authenticated["checkpoint_context"]["evidence"])
            attached = deepcopy(authenticated["checkpoint_context"]["author_control"])
        else:
            evidence = revision_evidence(record, recipe, parent, with_blocks=True, include_source_ids=True)
            attached = attach_node_control_text({}, target, recipe)
        context = {"version": 2 if reviewed else 1, "parent_run_id": parent_run_id, "target": target,
            "candidate_id": candidate_id, "recipe_sha256": digest(recipe),
            "stage_file": f"stage-results/{stage}.jsonl",
            "stage_sha256": file_hash(parent / "stage-results" / f"{stage}.jsonl"),
            "candidate_sha256": digest(record), "source_files": source_files,
            "checkpoint_files": _checkpoint_files(parent), "evidence": evidence,
            "evidence_sha256": digest(evidence), "author_control": attached}
        if reviewed:
            context.update(source_stage=stage, parent_manifest_sha256=file_hash(parent / "artifacts/manifest.json"))
        context["snapshot_sha256"] = digest(context)
        return {"target": target, "record": deepcopy(record),
                "checkpoint_context": validate_checkpoint_context(context)}


def verify_checkpoint_review_input(output, item):
    """Reauthenticate an immutable child selection before its first model call."""
    context = validate_checkpoint_context(item["checkpoint_context"])
    if digest(item["record"]) != context["candidate_sha256"] or item["target"] != context["target"]:
        raise ValueError("checkpoint_review_candidate_changed")
    # Build from the actual stopped parent again, never from caller-provided
    # source text or hashes. Added/changed checkpoints invalidate this selection.
    select = reviewed_checkpoint_input if context["version"] == 2 else checkpoint_review_input
    actual = select(output, context["parent_run_id"], context["target"], context["candidate_id"])
    if actual["checkpoint_context"] != context or digest(actual["record"]) != context["candidate_sha256"]:
        raise ValueError("checkpoint_review_parent_changed")
    return actual


def checkpoint_review_hints(output, context):
    """Load authenticated old review observations, explicitly without approval.

    Call after verify_checkpoint_review_input. The checks here still bind each
    selected file, so a later edit cannot quietly alter the evidence hints.
    """
    from lib.infrastructure.training_workflow import run_path
    checked = validate_checkpoint_context(context)
    parent = run_path(output, checked["parent_run_id"])
    hints = []
    for receipt in checked["checkpoint_files"]:
        if not receipt["file"].startswith("checkpoints/review/") or not receipt["file"].endswith(".json"):
            continue
        path = _path(parent, receipt["file"])
        if file_hash(path) != receipt["sha256"]:
            raise ValueError("checkpoint_integrity_error")
        saved = read_json(path)
        if digest(saved["data"]) != saved["sha256"]:
            raise ValueError("checkpoint_integrity_error")
        hints.append({"source_file": receipt["file"], "data_sha256": saved["sha256"],
            "usage": "prior_observation_only_not_release_approval", "observation": deepcopy(saved["data"])})
    return hints
