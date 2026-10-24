"""Target combinations at both batch boundaries, without model or IO work.

These checks exercise orchestration contracts. Engine, protocol and actual UI
acceptance remain separate: an opaque million-row batch is not a load test.
"""
from itertools import combinations

import pytest

from lib.contexts.workflows.application.generate_batch import GenerateDatasetBatch
from lib.contexts.workflows.application.generate_production_batch import GenerateProductionBatch
from lib.contexts.workflows.domain.execution import BatchGenerationPlan
from lib.contexts.workflows.domain.targets import PREFERENCE_TARGETS, TARGETS
from tests.test_workflow_execution_application import MemoryBatchRuntime, OpaqueBatch


def combinations_with_director_modes():
    for size in range(1, len(TARGETS) + 1):
        for targets in combinations(TARGETS, size):
            plan = BatchGenerationPlan(frozenset(targets), False)
            for director in ((False, True) if plan.needs_candidates else (False,)):
                yield pytest.param(frozenset(targets), director,
                                   id="+".join(targets) + ("-directed" if director else ""))


COMBINATIONS = list(combinations_with_director_modes())


def assert_shared_dependencies(runtime, targets, director):
    needs_sft = bool(targets & (PREFERENCE_TARGETS | {"sft", "cot"}))
    needs_dialogue = needs_sft or "multiturn" in targets
    assert runtime.events.count("director") == int(director and needs_dialogue)
    assert runtime.events.count("sft") == int(needs_sft and not director)
    assert runtime.events.count("multiturn") == int("multiturn" in targets and not director)
    assert runtime.events.count("preference") == int(bool(targets & PREFERENCE_TARGETS))
    assert runtime.events.count("cot") == int("cot" in targets)
    assert runtime.events.count("cpt") == int("cpt" in targets)
    assert runtime.events.count("agent") == int("agent" in targets)
    assert runtime.events.count("gsm8k") == int("gsm8k" in targets)
    if "cot" in targets or targets & PREFERENCE_TARGETS:
        upstream = "director" if director else "sft"
        downstream = [name for name in ("preference", "cot") if name in runtime.events]
        assert all(runtime.events.index(upstream) < runtime.events.index(name) for name in downstream)


@pytest.mark.parametrize("targets,director", COMBINATIONS)
def test_ordinary_target_combinations_share_generation_and_keep_streaming(targets, director):
    runtime = MemoryBatchRuntime(targets, director=director)
    GenerateDatasetBatch(runtime).execute()
    assert_shared_dependencies(runtime, targets, director)
    assert runtime.events[0] == "parse-and-clean"
    assert runtime.events[-2:] == ["finalize-reasoning", "publish"]
    assert runtime.events.count("publish") == 1
    preference_rows = [runtime.published[target] for target in targets & PREFERENCE_TARGETS]
    assert all(rows is preference_rows[0] for rows in preference_rows)


class ProductionRuntime(MemoryBatchRuntime):
    def begin(self):
        self.events.append("begin")

    def arithmetic_records(self, generated):
        assert generated is self.inputs.generation_candidates
        return super().arithmetic_records()

    def trim_reasoning(self, collections):
        self.events.append("trim")
        self.trimmed = collections

    def finalize_reasoning(self, collections, targets):
        self.events.append("finalize-reasoning")
        assert {"sft", "cot"} <= targets
        assert isinstance(collections["sft"], OpaqueBatch)
        assert isinstance(collections["cot"], OpaqueBatch)


@pytest.mark.parametrize("targets,director", COMBINATIONS)
def test_production_target_combinations_do_not_export_hidden_dependencies(targets, director):
    runtime = ProductionRuntime(targets, director=director)
    linked = {"sft", "cot"} <= targets
    recipe = {"targets": list(targets), "sources": ["document"],
              "qa_director": {"enabled": director}, "version": 15}
    collections = GenerateProductionBatch(runtime, recipe).execute(
        runtime.inputs.generation_candidates, runtime.inputs.source_records, targets)
    assert_shared_dependencies(runtime, targets, director)
    assert {target for target, rows in collections.items() if isinstance(rows, OpaqueBatch)} == targets
    assert runtime.events.count("finalize-reasoning") == int(linked)
    assert runtime.events.count("trim") == int(not linked)
    if not linked:
        assert runtime.trimmed is collections
