"""One first-draft review, one optional native replacement, no re-review."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib.contexts.review.application.review_candidate import ReviewCandidate
from lib.contexts.review.contracts import (
    native_sft_review_evolution_release_valid, review_evolution_step_snapshot,
    review_evolution_system_instruction,
)
from lib.contexts.workflows.application.generate_directed_sft import GenerateDirectedSft
from lib.contexts.workflows.application.optimize_native_sft import OptimizeNativeSft
from lib.contexts.workflows.domain.dialogue import dialogue_digest
from lib.contexts.workflows.domain.production_rounds import quality_assessment_valid
from test_generation_node_use_cases import MemoryGenerationPort, native_recipe, score


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield  # Injected ports and actual application code; no provider or listener.


SOURCE = "Maintenance requires isolating power before inspecting the circuit."
MEMORY = "# Memory Item 1\n## Title Preserve prerequisites\n## Description Apply when an action has a required condition.\n## Content Verify the condition before recommending the action."


def original():
    return {"id": "candidate", "source_id": "source", "status": "eligible",
        "messages": [{"role": "user", "content": "What must happen before inspection?"},
            {"role": "assistant", "content": "Inspect the live circuit.", "reasoning_content": "First native reasoning remains archived."}],
        "source_context": {"kind": "document", "text": SOURCE},
        "provided_source_context": {"scope": "prepared_source", "text_sha256": dialogue_digest(SOURCE)},
        "native_generation": {"version": 1}, "generation_style": {"instruction": "Follow actual task conditions."},
        "qa_contract": {"question": "What must happen before inspection?", "visible_context": "",
            "qa_type": "closed_book", "answer_policy": "answer", "guidance": "Use actual source conditions."}}


def recipe():
    return {**native_recipe("sft"), "native_sft_review_evolution_version": 1,
        "review_advisory_policy_version": 1, "review_repair": {"mode": "auto", "max_rounds": 0, "score_threshold": .8}}


def observation(*, passing=False, notes=True):
    verdict = score()
    if not passing:
        verdict.update(keep=False, reason="The answer omits the required isolation condition.")
    return {"status": "observed", "verdict": verdict,
        "task_notes": ["Retain the required isolation condition."] if notes else [],
        "memory_proposal": {"status": "proposed", "memory_items": [MEMORY]}}


class ReviewPort:
    def __init__(self, response, *, invalid_revision=False, unavailable_revision=False):
        self.response = response
        self.calls = []
        self.invalid_revision, self.unavailable_revision = invalid_revision, unavailable_revision

    def payload(self, target, row):
        return {"messages": row["messages"]}

    def evidence(self, row):
        return {"teacher_evidence": SOURCE}

    def review_once(self, *args):
        self.calls.append("review")
        return deepcopy(self.response)

    def record_review_memory(self, *args):
        self.calls.append("persist_memory_without_model")
        return {"status": "candidate", "model_calls": 0}

    def optimize_native_sft(self, row, evidence, response, memory):
        self.calls.append("native_author")
        snapshot = review_evolution_step_snapshot(response, recipe())
        if self.unavailable_revision:
            return {"record": None, "derived_snapshot": snapshot, "issue": "native_sft_optimization_request_failed"}
        revised = deepcopy(row)
        revised["messages"][-1] = {"role": "assistant", "content": "Isolate power before inspecting.",
            "reasoning_content": "New native reasoning keeps the required condition."}
        if self.invalid_revision:
            revised.update(status="quarantined", reason="native_answer_missing_or_invalid")
            revised["messages"][-1]["content"] = ""
        return {"record": revised, "task_identity": {"original": "fixed-task", "current": "fixed-task"},
            "derived_snapshot": snapshot}


@pytest.mark.parametrize("passing", [False, True])
def test_application_reviews_once_and_only_rejected_actionable_draft_gets_one_author(passing):
    first, frozen = original(), recipe()
    before = deepcopy(first)
    port = ReviewPort(observation(passing=passing))
    row, = ReviewCandidate(port, frozen).execute({"target": "sft", "record": first})
    assert port.calls == (["review", "persist_memory_without_model"] if passing else
        ["review", "persist_memory_without_model", "native_author"])
    assert first == before and row["reviewed_first_draft"]["record"] == before
    assert row["semantic_status"] == ("model_assessed" if passing else "not_assessed")
    assert row["review_repair"]["status"] == ("accepted" if passing else "not_assessed")
    assert quality_assessment_valid(row) is passing
    assert native_sft_review_evolution_release_valid(row, port.payload("sft", row), frozen["review_repair"])
    resumed, = ReviewCandidate(port, frozen).execute({"target": "sft", "record": row})
    assert resumed == row and port.calls.count("review") == 1 and port.calls.count("native_author") == int(not passing)


@pytest.mark.parametrize("mode", ["missing_notes", "score_only", "unavailable"])
def test_missing_notes_scores_and_unavailable_review_do_not_buy_blind_rewrites(mode):
    response = observation(notes=False) if mode == "missing_notes" else observation()
    if mode == "score_only":
        response["verdict"]["keep"] = True
        response["verdict"]["scores"]["correctness"] = 2
    if mode == "unavailable":
        response = {"status": "unavailable", "error": "upstream_unavailable"}
    port = ReviewPort(response)
    row, = ReviewCandidate(port, recipe()).execute({"target": "sft", "record": original()})
    assert "native_author" not in port.calls and port.calls.count("review") == 1
    assert row["status"] == "quarantined" and row["semantic_status"] == "not_assessed"


@pytest.mark.parametrize("unavailable", [False, True])
def test_invalid_or_unavailable_replacement_preserves_every_draft_without_semantic_retry(unavailable):
    port = ReviewPort(observation(), invalid_revision=not unavailable, unavailable_revision=unavailable)
    row, = ReviewCandidate(port, recipe()).execute({"target": "sft", "record": original()})
    assert row["status"] == "quarantined" and row["semantic_status"] == "not_assessed"
    assert row["reviewed_first_draft"]["record"]["messages"][-1]["reasoning_content"] == original()["messages"][-1]["reasoning_content"]
    assert row["reviewed_first_draft"]["observation"] == observation()
    ReviewCandidate(port, recipe()).execute({"target": "sft", "record": row})
    assert port.calls.count("review") == port.calls.count("native_author") == 1


def test_missing_native_channels_stop_before_review_and_rewrite():
    first = original()
    first["messages"][-1]["reasoning_content"] = ""
    port = ReviewPort(observation())
    row, = ReviewCandidate(port, recipe()).execute({"target": "sft", "record": first})
    assert port.calls == [] and row["status"] == "quarantined"
    assert row["reviewed_first_draft"]["record"] == first


def test_passing_review_never_revives_a_locally_blocked_first_draft():
    first = original()
    first.update(status="quarantined", reason="native_training_surface_invalid")
    port = ReviewPort(observation(passing=True))
    row, = ReviewCandidate(port, recipe()).execute({"target": "sft", "record": first})
    assert row["status"] == "quarantined" and row["reason"] == first["reason"]
    assert port.calls == ["review", "persist_memory_without_model"]


@pytest.mark.parametrize("tamper", ["body", "null_receipt"])
def test_terminal_receipt_rejects_tampering_without_another_model_call(tamper):
    port = ReviewPort(observation())
    row, = ReviewCandidate(port, recipe()).execute({"target": "sft", "record": original()})
    if tamper == "body":
        row["messages"][-1]["content"] = "Changed outside the saved revision."
    else:
        row["native_sft_review_evolution"] = None
    with pytest.raises(ValueError, match="native_sft_review_evolution_receipt_changed"):
        ReviewCandidate(port, recipe()).execute({"target": "sft", "record": row})
    assert port.calls.count("review") == port.calls.count("native_author") == 1


def test_recorded_import_in_native_batch_keeps_one_advisory_review_and_original_body():
    from lib.contexts.review.application.review_collections import ReviewGeneratedCollections
    from lib.contexts.review.contracts import native_sft_review_evolution_enabled
    from lib.infrastructure.workflow_qa_director import WorkflowQADirector

    class ImportedPort(ReviewPort):
        finalizing = False

        def record_advisory_observation(self, *args):
            return {"status": "recorded", "model_calls": 0}

        def check_cancel(self):
            pass

        def review_items(self, items, process):
            return [row for item in items for row in process(item)]

        def split_reviewed(self, reviewed, collections, *, targets):
            return {"sft": reviewed}

        def review_item(self, item):
            pytest.fail("A recorded import's existing advisory receipt must be reused")

    first, frozen = original(), {**recipe(), "targets": ["sft"]}
    first.pop("native_generation")
    first.pop("qa_contract")
    first["reasoning_origin"] = "source"
    first["source_context"] = {"kind": "conversation", "messages": deepcopy(first["messages"])}
    before = deepcopy(first["messages"])
    port = ImportedPort(observation())
    row, = ReviewCandidate(port, frozen).execute({"target": "sft", "record": first})
    assert row["messages"] == before and row["review_repair"]["status"] == "released_advisory"
    assert row["review_observation"]["task_notes"] == observation()["task_notes"]
    assert not native_sft_review_evolution_enabled(frozen, row)
    assert "native_sft_review_evolution" not in row and port.calls == ["review"]
    runtime = SimpleNamespace(recipe=frozen, _review_payload=port.payload)
    assert WorkflowQADirector.human_package_issue(runtime, row, "sft") is None
    result = ReviewGeneratedCollections(port, frozen).execute({"sft": [row]})
    assert result["sft"][0]["messages"] == before and port.calls == ["review"]


def test_native_optimizer_reuses_original_task_and_sends_both_guidance_kinds_in_one_request():
    class AuthorPort(MemoryGenerationPort):
        author_checks = 0

        def authorize_native_sft_optimization(self, first):
            self.author_checks += 1

        def assess(self, *args, **kwargs):
            pytest.fail("A replacement must not purchase an independent review")

    first, frozen, response = original(), recipe(), observation()
    initial_port = MemoryGenerationPort([{"answer": first["messages"][-1]["content"],
        "reasoning": first["messages"][-1]["reasoning_content"]}])
    initial_port.styles["sft"] = deepcopy(first["generation_style"])
    GenerateDirectedSft(initial_port, frozen).execute({**first["source_context"], "id": first["id"],
        "source_id": first["source_id"], "family_id": "original-family", "qa_contract": first["qa_contract"]})
    port = AuthorPort([{"answer": "Isolate power before inspecting.", "reasoning": " Keep the required condition.\n"}])
    use_case = OptimizeNativeSft(port, frozen, "new-run")
    result = use_case.execute(first, {"teacher_evidence": SOURCE}, response, {"status": "candidate", "model_calls": 0})
    assert port.author_checks == 1 and len(port.calls) == 1
    _, _, prompt, payload, options = port.calls[0]
    assert prompt == "workflow.cot_native_joint"
    assert "<proposed_general_experience>" in options["instruction"] and MEMORY in options["instruction"]
    assert "<review_task_notes>" in options["instruction"] and response["task_notes"][0] in options["instruction"]
    assert options["instruction"].replace("\n" + review_evolution_system_instruction(response), "", 1) == initial_port.calls[0][4]["instruction"]
    assert "First native reasoning" not in str(payload) and "Inspect the live circuit." not in str(payload)
    assert result["record"]["qa_contract"] == first["qa_contract"]
    assert result["record"]["messages"][:-1] == first["messages"][:-1]
    assert result["task_identity"]["original"] == result["task_identity"]["current"]
    again = use_case.execute(first, {"teacher_evidence": SOURCE}, response, {"status": "candidate", "model_calls": 0})
    assert again == result and len(port.calls) == 1
