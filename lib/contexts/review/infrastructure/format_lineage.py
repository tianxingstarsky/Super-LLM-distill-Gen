"""Read sealed candidate indexes; never infer lineage from history snippets."""
from __future__ import annotations

from lib.domain.human_session import identifier
from lib.infrastructure.human_revision import verified_revision_parent
from lib.infrastructure.rework_candidate_index import rework_candidate_index
from lib.infrastructure.workflow_store import run_path, verify_artifacts


class SealedFormatLineageAdapter:
    def __init__(self, runtime):
        self._runtime = runtime
        self._output = runtime.path.parent.parent

    def authenticate_current(self, context):
        verified_revision_parent(self._output, self._runtime.state["id"], context)

    def publication_owner(self, duplicate):
        run_id = identifier(duplicate["run_id"], "invalid_workflow_id")
        prefix = run_id + ":"
        if not isinstance(duplicate.get("id"), str) or not duplicate["id"].startswith(prefix):
            raise ValueError("qa_publication_owner_invalid")
        candidate_id = duplicate["id"][len(prefix):]
        path = run_path(self._output, run_id)
        verify_artifacts(path)
        # Use the same deterministic publication order as the existing ledger.
        # A shared ID can exist in several files; its first view owns history.
        for target in ("sft", "dpo", "orpo", "rlaif", "cot"):
            if not (path / "artifacts" / f"{target}.records.json").exists():
                continue
            with rework_candidate_index(path, target) as index:
                try:
                    row = index.candidates([candidate_id])[candidate_id]
                except ValueError as error:
                    if str(error) == "human_session_candidate_not_found":
                        continue
                    raise
                if row.get("status") == "eligible":
                    return target, row
        return None

    def authenticate_owner(self, run_id, record):
        context = record.get("revision_context")
        if not context:
            raise ValueError("qa_publication_owner_lineage_missing")
        verified_revision_parent(self._output, run_id, context)
