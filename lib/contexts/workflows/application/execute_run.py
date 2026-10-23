"""Exclusive run lifecycle without a filesystem, framework or model dependency."""
from __future__ import annotations

from .execution_ports import RunSessionPort
from .generate_batch import GenerateDatasetBatch
from ..domain.execution import WorkflowCancelled


class ExecuteWorkflowRun:
    def __init__(self, session: RunSessionPort, batch: GenerateDatasetBatch):
        self._session = session
        self._batch = batch

    def execute(self, *, resume: bool = False) -> dict:
        with self._session.exclusive():
            status = self._session.reload_status()
            if status in {"completed", "needs_attention"}:
                self._session.verify_existing_delivery()
                return self._session.result()
            self._session.start_attempt(resume=resume)
            try:
                self._session.verify_pinned_inputs()
                route = self._session.route()
                if route == "repair":
                    return self._session.run_repair()
                if route == "production":
                    return self._session.run_production()
                self._session.complete(self._batch.execute())
            except WorkflowCancelled:
                self._session.record_cancellation()
            except Exception as error:
                # Adapters classify and redact provider errors before storage.
                self._session.record_failure(error)
            return self._session.result()
