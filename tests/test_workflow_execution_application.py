"""Exercise execution policy with no filesystem, model client or engine object."""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from lib.contexts.workflows.application.execute_run import ExecuteWorkflowRun
from lib.contexts.workflows.application.execution_ports import PreparedBatch
from lib.contexts.workflows.application.generate_batch import GenerateDatasetBatch
from lib.contexts.workflows.domain.execution import (
    BatchGenerationPlan, BatchOutcome, TargetOutcome, WorkflowCancelled, execution_route,
)


class OpaqueBatch:
    """A stage result can be large. The use case must pass it without reading it."""
    def __init__(self, name):
        self.name = name

    def __len__(self):
        return 1_000_000

    def __iter__(self):
        pytest.fail("Orchestration must not materialize stage records")


COMPLETE = BatchOutcome((TargetOutcome("sft", 10),), frozenset(), 0, 0, 0, False)


class MemoryBatchRuntime:
    def __init__(self, targets, *, director=False):
        self.events = []
        self.targets = frozenset(targets)
        self.director = director
        self.inputs = PreparedBatch(OpaqueBatch("sources"), OpaqueBatch("clean-candidates"))

    def plan(self):
        return BatchGenerationPlan(self.targets, self.director)

    def prepare_inputs(self, plan):
        self.events.append("parse-and-clean")
        return self.inputs

    def cpt_records(self, sources):
        assert sources is self.inputs.source_records
        self.events.append("cpt")
        return OpaqueBatch("cpt")

    def run_stage(self, stage, records):
        if stage in {"preference", "cot"}:
            assert records.name == "eligible-sft"
        elif stage in {"sft", "multiturn"}:
            assert records is self.inputs.generation_candidates
        elif stage == "agent":
            assert records is self.inputs.source_records
        self.events.append(stage)
        return OpaqueBatch(stage)

    def skip_stage(self, stage):
        self.events.append("skip:" + stage)

    def directed_dialogues(self, candidates, *, needs_sft, needs_multiturn):
        assert candidates is self.inputs.generation_candidates
        self.events.append("director")
        return {stage: OpaqueBatch(stage) for stage, needed in
                (("sft", needs_sft), ("multiturn", needs_multiturn)) if needed}

    def eligible_records(self, stage, records):
        assert stage == "sft" and records.name == "sft"
        return OpaqueBatch("eligible-sft")

    def preference_exports(self, pairs, targets):
        assert pairs.name == "preference"
        return {target: pairs if target in targets else [] for target in ("dpo", "orpo", "rlaif")}

    def arithmetic_records(self):
        self.events.append("gsm8k")
        return OpaqueBatch("gsm8k")

    def finalize_reasoning(self, collections):
        self.events.append("finalize-reasoning")
        self.finalized = collections

    def publish(self, collections):
        assert collections is self.finalized
        self.events.append("publish")
        self.published = collections

    def outcome(self, collections):
        assert collections is self.published
        return COMPLETE


@pytest.mark.parametrize("targets,expected", [
    ({"cpt"}, ["cpt"]),
    ({"sft"}, ["sft"]),
    ({"multiturn"}, ["multiturn"]),
    ({"agent"}, ["agent"]),
    ({"gsm8k"}, ["gsm8k"]),
    ({"cot"}, ["sft", "cot"]),
    ({"dpo", "orpo", "rlaif"}, ["sft", "preference"]),
    ({"cpt", "sft", "multiturn", "agent", "dpo", "orpo", "rlaif", "gsm8k", "cot"},
     ["cpt", "sft", "multiturn", "agent", "preference", "gsm8k", "cot"]),
])
def test_dependencies_share_streaming_records_and_finalize_before_export(targets, expected):
    runtime = MemoryBatchRuntime(targets)
    assert GenerateDatasetBatch(runtime).execute() is COMPLETE
    executed = [event for event in runtime.events if not event.startswith("skip:")]
    assert executed == ["parse-and-clean", *expected, "finalize-reasoning", "publish"]
    if "dpo" in targets and "orpo" in targets:
        assert runtime.published["dpo"] is runtime.published["orpo"]


def test_director_consumes_clean_candidates_and_suppresses_duplicate_generation():
    runtime = MemoryBatchRuntime({"sft", "multiturn", "cot"}, director=True)
    GenerateDatasetBatch(runtime).execute()
    assert runtime.events.index("parse-and-clean") < runtime.events.index("director") < runtime.events.index("cot")
    assert "sft" not in runtime.events and "multiturn" not in runtime.events
    assert runtime.published["multiturn"].name == "multiturn"


@pytest.mark.parametrize("with_sft", [False, True])
def test_reading_session_uses_clean_source_without_duplicate_director(with_sft):
    from lib.contexts.workflows.application.generate_production_batch import GenerateProductionBatch
    targets = {"multiturn", *({"sft"} if with_sft else set())}
    runtime = MemoryBatchRuntime(targets, director=True)
    runtime.plan = lambda: BatchGenerationPlan(frozenset(targets), True, reading_session=True)
    GenerateDatasetBatch(runtime).execute()
    assert runtime.events.count("multiturn") == 1
    assert runtime.events.count("director") == int(with_sft)
    assert "sft" not in runtime.events
    runtime.events.clear()
    runtime.begin = lambda: None
    runtime.trim_reasoning = lambda collections: None
    recipe = {"version": 41, "targets": list(targets), "qa_director": {"enabled": True},
        "node_generation": {"multiturn": {"enabled": True, "reading_session": True}}}
    collections = GenerateProductionBatch(runtime, recipe).execute(runtime.inputs.generation_candidates,
        runtime.inputs.source_records, targets)
    assert runtime.events.count("multiturn") == 1 and runtime.events.count("director") == int(with_sft)
    assert collections["multiturn"].name == "multiturn"


class MemoryRunSession:
    def __init__(self, *, status="pending", route="batch", error=None):
        self.state = {"status": status}
        self.events = []
        self._route = route
        self.error = error
        self.locked = False

    @contextmanager
    def exclusive(self):
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def reload_status(self):
        assert self.locked
        self.events.append("reload")
        return self.state["status"]

    def verify_existing_delivery(self):
        assert self.locked
        self.events.append("verify-delivery")

    def start_attempt(self, *, resume):
        assert self.locked
        self.events.append(("start", resume))

    def verify_pinned_inputs(self):
        self.events.append("verify-inputs")
        if self.error:
            raise self.error

    def route(self):
        return self._route

    def run_repair(self):
        self.events.append("repair")
        return self.state

    def run_production(self):
        self.events.append("production")
        return self.state

    def complete(self, outcome):
        self.state["status"] = outcome.status

    def record_cancellation(self):
        self.state["status"] = "cancelled"

    def record_failure(self, error):
        assert error is self.error
        self.state["status"] = "failed"

    def result(self):
        return self.state


class ForbiddenBatch:
    def execute(self):
        pytest.fail("This run must not start a new generation batch")


@pytest.mark.parametrize("status", ["completed", "needs_attention"])
def test_finished_runs_only_verify_their_existing_delivery(status):
    session = MemoryRunSession(status=status)
    result = ExecuteWorkflowRun(session, ForbiddenBatch()).execute(resume=True)
    assert result is session.state
    assert session.events == ["reload", "verify-delivery"]
    assert not session.locked


@pytest.mark.parametrize("route", ["repair", "production"])
def test_special_routes_validate_snapshot_and_never_execute_original_batch(route):
    session = MemoryRunSession(route=route)
    assert ExecuteWorkflowRun(session, ForbiddenBatch()).execute(resume=True) is session.state
    assert session.events == ["reload", ("start", True), "verify-inputs", route]


@pytest.mark.parametrize("error,status", [(WorkflowCancelled(), "cancelled"),
                                         (ValueError("source_snapshot_changed"), "failed")])
def test_failed_or_cancelled_verification_cannot_generate_and_always_releases_lock(error, status):
    session = MemoryRunSession(error=error)
    assert ExecuteWorkflowRun(session, ForbiddenBatch()).execute()["status"] == status
    assert not session.locked


def test_completed_batch_propagates_attention_instead_of_claiming_success():
    class Batch:
        def execute(self):
            return replace(COMPLETE, deferred_inputs=1)
    session = MemoryRunSession()
    assert ExecuteWorkflowRun(session, Batch()).execute()["status"] == "needs_attention"


@pytest.mark.parametrize("change", [
    {"targets": (TargetOutcome("sft", 0),)},
    {"targets": (TargetOutcome("sft", 1, negative=1),)},
    {"targets": (TargetOutcome("sft", 1, review_rejected=1),)},
    {"quarantined_targets": frozenset({"cpt"})}, {"source_quarantined": 1},
    {"input_quarantined": 1}, {"deferred_inputs": 1},
])
def test_completion_rejects_each_unresolved_quality_condition(change):
    assert replace(COMPLETE, **change).status == "needs_attention"


def test_exhausted_unique_arithmetic_space_is_not_a_false_failure():
    exhausted = replace(COMPLETE, targets=(TargetOutcome("gsm8k", 0),), arithmetic_exhausted_cleanly=True)
    assert exhausted.status == "completed"
    assert replace(exhausted, input_quarantined=1).status == "needs_attention"
    assert replace(exhausted, targets=(TargetOutcome("sft", 0),)).status == "needs_attention"


def test_repair_route_has_precedence_over_production():
    assert execution_route(repair_requested=True, production_enabled=True) == "repair"


def test_production_cancellation_is_typed_and_never_retried_as_a_bad_candidate():
    from lib.infrastructure.workflow_production import WorkflowProduction
    from lib.infrastructure.training_workflow import Cancelled

    class Production(WorkflowProduction):
        recipe = {"production": {"item_retries": 2}}
        def production_enabled(self):
            return True
        def check_cancel(self):
            pass

    class UserStoppedRun(WorkflowCancelled):
        pass

    calls = []
    def stop(record):
        calls.append(record)
        raise UserStoppedRun()

    assert Cancelled is WorkflowCancelled
    with pytest.raises(UserStoppedRun):
        Production().run_item_safely("sft", {"id": "candidate"}, stop)
    assert len(calls) == 1
