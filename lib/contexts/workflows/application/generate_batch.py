"""Run one bounded batch in dependency order, using replaceable stage operations."""
from __future__ import annotations

from .execution_ports import BatchRuntimePort, PreparedBatch, RecordBatch, StageCollections
from ..domain.execution import BatchGenerationPlan, BatchOutcome
from ..domain.targets import PREFERENCE_TARGETS


class GenerateDatasetBatch:
    def __init__(self, runtime: BatchRuntimePort):
        self._runtime = runtime

    def execute(self) -> BatchOutcome:
        plan = self._runtime.plan()
        inputs = self._runtime.prepare_inputs(plan)
        collections: StageCollections = {}
        collections["cpt"] = (self._runtime.cpt_records(inputs.source_records)
                              if "cpt" in plan.targets else [])
        self._generate_dialogues(plan, inputs, collections)
        collections["agent"] = self._optional_stage(
            "agent", inputs.source_records, selected="agent" in plan.targets)
        self._generate_preferences(plan, collections)
        if "gsm8k" in plan.targets:
            collections["gsm8k"] = self._runtime.arithmetic_records()
        else:
            collections["gsm8k"] = []
            self._runtime.skip_stage("gsm8k")
        if "cot" in plan.targets:
            accepted_sft = self._runtime.eligible_records("sft", collections["sft"])
            collections["cot"] = self._runtime.run_stage("cot", accepted_sft)
        else:
            collections["cot"] = []
            self._runtime.skip_stage("cot")
        self._runtime.finalize_reasoning(collections)
        # Rebuild delivery after every interrupted attempt. A cached generation
        # checkpoint does not establish that its exported files are complete.
        self._runtime.publish(collections)
        return self._runtime.outcome(collections)

    def _generate_dialogues(self, plan: BatchGenerationPlan, inputs: PreparedBatch,
                            collections: StageCollections) -> None:
        directed = (self._runtime.directed_dialogues(
            inputs.generation_candidates, needs_sft=plan.needs_sft,
            needs_multiturn="multiturn" in plan.targets) if plan.director_enabled else None)
        if directed is None:
            self._runtime.skip_stage("director")
        for stage, selected in (("sft", plan.needs_sft), ("multiturn", "multiturn" in plan.targets)):
            if directed is None:
                collections[stage] = self._optional_stage(
                    stage, inputs.generation_candidates, selected=selected)
            else:
                collections[stage] = directed.get(stage, [])
                if not selected:
                    self._runtime.skip_stage(stage)

    def _generate_preferences(self, plan: BatchGenerationPlan, collections: StageCollections) -> None:
        if plan.needs_preferences:
            accepted_sft = self._runtime.eligible_records("sft", collections["sft"])
            pairs = self._runtime.run_stage("preference", accepted_sft)
            collections.update(self._runtime.preference_exports(pairs, plan.targets))
        else:
            collections.update({target: [] for target in PREFERENCE_TARGETS})
            self._runtime.skip_stage("preference")

    def _optional_stage(self, stage: str, records: RecordBatch, *, selected: bool) -> RecordBatch:
        if selected:
            return self._runtime.run_stage(stage, records)
        self._runtime.skip_stage(stage)
        return []
