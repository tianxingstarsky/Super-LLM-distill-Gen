"""Pure application ports exercise request order, stop behavior and bounded delivery."""
from contextlib import contextmanager
from copy import deepcopy

import pytest

from lib.contexts.workflows.application.execute_production import ExecuteProductionRounds
from lib.contexts.workflows.application.generate_dialogue import GenerateMultiturnDialogue
from lib.contexts.workflows.application.production_ports import PreparedProductionRound
from lib.contexts.workflows.domain.dialogue import DialogueSource
from lib.contexts.workflows.domain.execution import WorkflowCancelled
from lib.contexts.workflows.domain.production import validate_production, production_completion_status
from lib.contexts.workflows.domain.production_rounds import ProductionProgress, build_production_state
from lib.contexts.review.application.review_candidate import ReviewCandidate
from lib.contexts.review.domain.candidate_content import candidate_digest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield  # Pure injected ports: no TCP listener, model or filesystem adapter.


def grade(passing=True):
    return {"keep": passing, "grounded": True, "reasoning_valid": passing, "correctness": 5,
            "scores": {name: 5 if passing else 2 for name in
                       ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "Conditions are satisfied." if passing else "The proposed conclusion does not follow."}


class DialogueMemory:
    def __init__(self, *, stop=False):
        self.calls, self.stop = [], stop

    def prepare_source(self, unit, style):
        return DialogueSource({"task": unit["text"]})

    def reject(self, unit, reason):
        return {**unit, "status": "quarantined", "reason": reason}

    def request(self, key, role, prompt_id, data, **options):
        self.calls.append((prompt_id, deepcopy(key), deepcopy(data)))
        if prompt_id == "workflow.multiturn_user":
            if self.stop and data["turn"] > 1:
                return {"end": True, "reason": "The requested explanation is complete."}
            return {"message": "How should I start?" if data["turn"] == 1 else "What if the check fails?"}
        return {"answer": "First isolate power before inspecting the circuit.", "quotes": [],
                "reasoning": "Inspection could expose live conductors. Isolation removes that hazard."}

    def assess(self, key, context, message, prompt_id="workflow.jev_score", **options):
        self.calls.append((prompt_id, deepcopy(key), deepcopy(message)))
        return grade()

    def separated_questions(self):
        return False


def test_dialogue_port_keeps_separate_actors_and_every_turn_reasoning():
    port = DialogueMemory()
    recipe = {"version": 9, "brief": "Maintenance guidance", "conversation_turns": 2}
    source = {"id": "task", "source_id": "brief", "kind": "brief", "text": "Maintenance guidance"}
    result, = GenerateMultiturnDialogue(port, recipe).execute(source)
    assert result["status"] == "eligible" and result["turn_count"] == 2
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"] * 2
    assert all(m.get("reasoning_content") for m in result["messages"] if m["role"] == "assistant")
    assert [call[0] for call in port.calls] == ["workflow.multiturn_user", "workflow.multiturn_assistant",
        "workflow.jev_score", "workflow.multiturn_user", "workflow.multiturn_assistant",
        "workflow.jev_score", "workflow.multiturn_consistency"]
    assert "messages" not in source


def test_natural_end_cannot_purchase_an_answer_or_promote_a_single_turn():
    port = DialogueMemory(stop=True)
    result, = GenerateMultiturnDialogue(port, {"version": 9, "conversation_turns": 5,
        "multiturn_stop_protocol_version": 1}).execute(
            {"id": "task", "source_id": "brief", "kind": "brief", "text": "Maintenance"})
    assert result["status"] == "quarantined"
    assert result["reason"] == "multiturn_natural_end_insufficient_turns"
    assert sum(call[0] == "workflow.multiturn_assistant" for call in port.calls) == 1
    assert not any(call[0] == "workflow.multiturn_consistency" for call in port.calls)


class OpaqueRows:
    def __init__(self, count):
        self.count = count

    def __len__(self):
        return self.count

    def __iter__(self):
        raise AssertionError("Production orchestration must not materialize record batches")


class ProductionMemory:
    def __init__(self, *, cancelled=False, fail_commit=False):
        self.cancelled, self.fail_commit = cancelled, fail_commit
        self.closed, self.committed, self.events = False, [], []
        self.recipe = {"targets": ["sft"], "sources": [], "tasks": 1}
        self.config = validate_production({"version": 2, "quantity_policy": "quality_first",
            "goals": {"sft": 1_000_000}, "round_size": 100_000}, ["sft"])

    @contextmanager
    def open_session(self):
        state = build_production_state(self.recipe, self.config, self.committed)
        self.progress = ProductionProgress(self.recipe, self.config, state, self.committed, 0, 0, False)
        try:
            yield self.progress
        finally:
            self.closed = True

    def reset_replenishment(self):
        pass

    def needs_replenishment(self):
        return False

    def check_cancel(self):
        if self.cancelled:
            raise WorkflowCancelled()

    def prepare_round(self, progress, plan):
        assert plan.size <= 100_000
        return PreparedProductionRound(OpaqueRows(plan.size), OpaqueRows(plan.size), OpaqueRows(0), 0, plan.size, None)

    def execute_and_commit(self, progress, plan, batch):
        if self.fail_commit:
            raise OSError("receipt write failed")
        self.events.append(("commit", plan.number))
        return {"round": plan.number, "attempted": plan.size, "attempts": {"sft": plan.size},
                "eligible": {"sft": plan.size}, "rows": {"sft": plan.size}}

    def refresh_progress(self, progress):
        assert len(progress.receipts) == len(self.events)
        progress.production = build_production_state(self.recipe, self.config, progress.receipts)

    def record_progress(self, progress, receipt):
        pass

    def begin_finalization(self, progress):
        assert len(progress.receipts) == 10

    def completion_status(self, progress, counts, stop):
        return production_completion_status(progress.config, counts, self.recipe["targets"])

    def publish_final(self, progress):
        return dict(progress.production["counts"])

    def complete(self, progress):
        return progress.production


def test_million_row_production_use_case_only_handles_bounded_windows_and_receipts():
    port = ProductionMemory()
    state = ExecuteProductionRounds(port).execute()
    assert state["counts"] == {"sft": 1_000_000}
    assert state["status"] == "completed" and state["stop_reason"] == "expectation_reached"
    assert len(port.committed) == 10 and port.closed


@pytest.mark.parametrize("cancelled,fail_commit,error", [(True, False, WorkflowCancelled), (False, True, OSError)])
def test_cancel_or_failed_commit_never_advances_receipts_and_always_closes(cancelled, fail_commit, error):
    port = ProductionMemory(cancelled=cancelled, fail_commit=fail_commit)
    with pytest.raises(error):
        ExecuteProductionRounds(port).execute()
    assert not port.committed and port.closed


class ReviewMemory:
    run_id, authenticated_revision = "run", False

    def __init__(self, *, unchanged=False, pending=False):
        self.unchanged, self.pending, self.scores, self.repairs = unchanged, pending, 0, 0

    def payload(self, target, row):
        return {"messages": row["messages"]}

    def evidence(self, row):
        return {"teacher_evidence": "Equality has zero difference."}

    def repair(self, target, row, evidence, key, feedback, instruction):
        self.repairs += 1
        result = deepcopy(row)
        if not self.unchanged:
            result["messages"][-1].update(content="Equality does not show improvement.",
                reasoning_content="Improvement requires a positive difference. Equality has zero difference.")
        return result, None

    def score(self, target, row, evidence, key, instruction):
        self.scores += 1
        if self.pending:
            row["claim_review"] = {"assessments": [{"review": {"condition_guard": {"needs_review": True}}}]}
        return grade(self.scores > 1)

    def record_revision_style(self, target, original, updated):
        pass


def review_input():
    return {"target": "sft", "record": {"id": "candidate", "status": "eligible", "messages": [
        {"role": "user", "content": "Does the unchanged result show improvement?"},
        {"role": "assistant", "content": "Yes, equality proves improvement.",
         "reasoning_content": "I assume that equality proves improvement."}]}}


def test_review_repairs_and_rescores_exact_body_without_changing_task():
    item, port = review_input(), ReviewMemory()
    result, = ReviewCandidate(port, {"review_repair": {"mode": "auto", "max_rounds": 2}}).execute(item)
    assert result["status"] == "eligible" and (port.scores, port.repairs) == (2, 1)
    assert result["messages"][0] == item["record"]["messages"][0]
    assert result["messages"][1]["reasoning_content"] != item["record"]["messages"][1]["reasoning_content"]
    assert result["review_repair"]["candidate_sha256"] == candidate_digest(port.payload("sft", result))
    assert "review_repair" not in item["record"]


@pytest.mark.parametrize("pending", [False, True])
def test_repeated_or_unresolved_candidate_does_not_buy_additional_scores(pending):
    port = ReviewMemory(unchanged=True, pending=pending)
    result, = ReviewCandidate(port, {"claim_review_condition_contract_version": 1,
        "review_repair": {"mode": "auto", "max_rounds": 5}}).execute(review_input())
    assert port.scores == 1 and port.repairs == (0 if pending else 1)
    expected = "condition_support_pending_review" if pending else "review_repair_repeated_candidate"
    assert result["review_repair"]["stop_reason"] == expected
    assert result["messages"] == review_input()["record"]["messages"]
