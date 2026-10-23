"""Execute one bounded production window in dependency order."""
from __future__ import annotations

from typing import Protocol

from ..domain.production import evaluated_quality_stop
from ..domain.targets import PREFERENCE_TARGETS, TARGETS
from lib.domain.workflow_reasoning_route import cot_updates_sft


class ProductionBatchPort(Protocol):
    def begin(self) -> None: ...
    def cpt_records(self, sources): ...
    def run_stage(self, stage, records): ...
    def directed_dialogues(self, records, *, needs_sft, needs_multiturn): ...
    def eligible_records(self, stage, records): ...
    def preference_exports(self, pairs, targets) -> dict: ...
    def arithmetic_records(self, generated): ...
    def finalize_reasoning(self, collections, targets) -> None: ...
    def record_blocked_sft(self, records) -> None: ...
    def trim_reasoning(self, collections) -> None: ...


class GenerateProductionBatch:
    def __init__(self, port: ProductionBatchPort, recipe: dict):
        self._port, self.recipe = port, recipe

    def execute(self, generated, source_rows, active):
        self._port.begin()
        selected = set(active)
        collections = {target: [] for target in TARGETS}
        needs_sft = bool(selected & (PREFERENCE_TARGETS | {"sft", "cot"}))
        if "cpt" in selected:
            collections["cpt"] = self._port.cpt_records(source_rows if self.recipe["sources"] else generated)
        if "agent" in selected:
            collections["agent"] = self._port.run_stage("agent", source_rows)
        directed = (self._port.directed_dialogues(generated, needs_sft=needs_sft,
                    needs_multiturn="multiturn" in selected)
                    if self.recipe.get("qa_director", {}).get("enabled") and (needs_sft or "multiturn" in selected) else None)
        if needs_sft:
            collections["sft"] = directed["sft"] if directed is not None else self._port.run_stage("sft", generated)
        if "multiturn" in selected:
            collections["multiturn"] = directed["multiturn"] if directed is not None else self._port.run_stage("multiturn", generated)
        if selected & PREFERENCE_TARGETS:
            rows = self._port.eligible_records("sft", collections["sft"])
            pairs = self._port.run_stage("preference", rows)
            collections.update(self._port.preference_exports(pairs, selected & PREFERENCE_TARGETS))
        if "cot" in selected or ("sft" in selected and cot_updates_sft(self.recipe)):
            rows = self._port.eligible_records("sft", collections["sft"])
            collections["cot"] = self._port.run_stage("cot", rows)
        if "gsm8k" in selected:
            collections["gsm8k"] = self._port.arithmetic_records(generated)
        if cot_updates_sft(self.recipe):
            self._port.finalize_reasoning(collections, selected)
        if evaluated_quality_stop(self.recipe.get("production") or {}) and selected & (PREFERENCE_TARGETS | {"cot"}):
            self._port.record_blocked_sft(collections["sft"])
        # Intermediate candidates must be processed before hiding unrequested formats.
        for target in set(collections) - selected:
            collections[target] = []
        if not cot_updates_sft(self.recipe):
            self._port.trim_reasoning(collections)
        return collections
