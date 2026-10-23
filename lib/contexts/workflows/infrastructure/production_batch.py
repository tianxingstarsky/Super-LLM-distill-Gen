"""Checkpointed stage operations and persisted quality evidence for one round."""
from __future__ import annotations

from copy import deepcopy

from .execution_runtime import CheckpointedBatchRuntime
from ..domain.production_rounds import quality_assessment_valid
from lib.infrastructure.workflow_rows import RowSpool


class CheckpointedProductionBatch(CheckpointedBatchRuntime):
    def begin(self):
        self._runtime._production_blocked_sft_quality = None

    def run_stage(self, stage, records):
        runtime = self._runtime
        return runtime.stage_items(stage, records, getattr(runtime, stage))

    def arithmetic_records(self, generated):
        from lib.infrastructure.workflow_arithmetic import legacy_arithmetic_guard
        runtime = self._runtime
        arithmetic = (getattr(runtime, "_production_arithmetic_inputs", generated)
                      if runtime.independent_arithmetic() else generated)
        with legacy_arithmetic_guard(runtime, arithmetic):
            return runtime.stage_items("gsm8k", arithmetic, runtime.gsm8k)

    def finalize_reasoning(self, collections, targets):
        self._runtime.finalize_reasoning_outputs(collections, delivery_targets=targets)

    def record_blocked_sft(self, records):
        runtime = self._runtime
        blocked = RowSpool(runtime._production_destination.parent / "sft.upstream-quality.jsonl")
        assessed = unassessed = 0
        for row in records:
            if row.get("status") == "eligible":
                continue
            evidence = {key: deepcopy(row[key]) for key in (
                "id", "status", "reason", "request_error", "generation_failure_kind", "judge", "checks",
                "review_repair", "package_review") if key in row}
            blocked.append(evidence)
            valid = quality_assessment_valid(evidence)
            assessed += int(valid)
            unassessed += int(not valid)
        blocked.close()
        runtime._production_blocked_sft_quality = {"upstream_stage": "sft", "upstream_assessed": assessed,
                                                 "upstream_unassessed": unassessed}

    def trim_reasoning(self, collections):
        self._runtime.apply_reasoning_trim(collections)
