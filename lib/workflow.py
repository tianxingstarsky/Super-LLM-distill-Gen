"""Compatibility facade for the moved filesystem-backed workflow engine.

New presentation entry points use ``WorkflowApplication``. This module remains
for older callers and tests while remaining modules migrate to the inward ports.
"""
from lib.domain.workflow_targets import PREFERENCE_TARGETS, STAGES, TARGETS
from lib.infrastructure.training_workflow import (
    EXTENSIONS, MAX_FILE_BYTES, RECIPE_VERSION, Cancelled, Workflow, cancel,
    create_run, digest, file_hash, is_active, list_runs, now, prompt_versions,
    read_json, resume, run_path, verify_artifacts,
)

__all__ = [
    "EXTENSIONS", "MAX_FILE_BYTES", "PREFERENCE_TARGETS", "RECIPE_VERSION",
    "STAGES", "TARGETS", "Cancelled", "Workflow", "cancel", "create_run",
    "digest", "file_hash", "is_active", "list_runs", "now", "prompt_versions",
    "read_json", "resume", "run_path", "verify_artifacts",
]
