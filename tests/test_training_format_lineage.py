"""Distinct formats share authenticated task identity, never a dedupe bypass."""
from copy import deepcopy
from itertools import permutations
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.contexts.review.application.authorize_format_view import AuthorizeTrainingFormatView
from lib.contexts.review.domain.training_lineage import (
    repaired_candidate_identity, same_training_task_view, validate_format_view_selections,
)
from lib.contexts.workflows.contracts import actual_qa_input_identity
from lib.infrastructure.workflow_qa_director import WorkflowQADirector


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def candidate(target="dpo", *, root="root", question="What should I verify before opening the case?"):
    context = {"candidate_id": "old-" + target, "parent_run_id": "parent",
        "target": target, "ancestors": [{"run_id": "original", "candidate_id": root},
                                         {"run_id": "parent", "candidate_id": "old-" + target}]}
    return {"id": "old-" + target, "family_id": "same-source-family", "source_id": "document",
        "status": "eligible", "revision_context": context,
        "qa_contract": {"question": question, "visible_context": "", "qa_type": "closed_book",
                        "answer_policy": "answer", "revision_context": deepcopy(context)},
        "prompt": [{"role": "user", "content": question}],
        "chosen": [{"role": "assistant", "content": "Verify that power is isolated.",
                    "reasoning_content": "Isolation prevents unexpected activation; check it before opening the case."}],
        "rejected": [{"role": "assistant", "content": "Leave power connected.",
                      "reasoning_content": "Opening the case while connected will save time."}]}


@pytest.mark.parametrize("first,second", list(permutations(("dpo", "orpo", "rlaif"), 2)))
def test_authenticated_preference_views_share_one_input_identity(first, second):
    one, two = candidate(first), candidate(second)
    before = deepcopy((one, two))
    assert repaired_candidate_identity("child", first, one, one["revision_context"], format_lineage=True) == (
        repaired_candidate_identity("child", second, two, two["revision_context"], format_lineage=True))
    assert same_training_task_view(one, first, two, second)
    assert (one, two) == before, "Neither rationale nor answer may be rewritten by identity rules."


@pytest.mark.parametrize("target", ["sft", "cot"])
def test_single_turn_reasoning_and_preference_views_do_not_create_independent_task_ids(target):
    preference = candidate("dpo")
    projected = deepcopy(preference)
    if target == "sft":
        projected["messages"] = [*projected["prompt"], *projected["chosen"]]
    else:
        projected.update(question=projected["prompt"], reasoning=["Check isolation before opening."],
                         answer=projected["chosen"][0]["content"])
    assert repaired_candidate_identity("child", target, projected, projected["revision_context"], format_lineage=True) == (
        repaired_candidate_identity("child", "dpo", preference, preference["revision_context"], format_lineage=True))
    assert same_training_task_view(projected, target, preference, "dpo")
    assert not same_training_task_view(projected, "multiturn", preference, "dpo")


def test_review_candidate_requires_authenticated_branch_before_changing_format_identity():
    from lib.contexts.review.application.review_candidate import ReviewCandidate
    from lib.contexts.workflows.contracts import training_record
    recipe = {"training_format_lineage_version": 1, "review_repair": {"mode": "human"}}
    port = Mock(run_id="child", authenticated_revision=True)
    port.payload.side_effect = training_record
    port.evidence.return_value = {"source_kind": "brief", "teacher_evidence": "Keep the power isolated."}
    reviewed = []
    for target in ("dpo", "orpo"):
        row = candidate(target)
        reviewed.append(ReviewCandidate(port, recipe).execute({"target": target, "record": row,
            "revision_context": row["revision_context"], "score": 1.0, "approved": True})[0])
    assert reviewed[0]["id"] == reviewed[1]["id"]
    assert all(row["status"] == "eligible" for row in reviewed)
    assert all(row[side][0]["reasoning_content"] for row in reviewed for side in ("chosen", "rejected"))
    port.authenticated_revision = False
    row = candidate("orpo")
    ordinary, = ReviewCandidate(port, recipe).execute({"target": "orpo", "record": row,
        "revision_context": row["revision_context"], "score": 1.0, "approved": True})
    assert ordinary["id"] != reviewed[0]["id"]


@pytest.mark.parametrize("change", ["root", "question", "source", "tool", "same_format"])
def test_same_source_or_same_question_does_not_authorize_unrelated_duplicates(change):
    one, two, target = candidate("orpo"), candidate("dpo"), "dpo"
    if change == "root":
        two["revision_context"]["ancestors"][0]["candidate_id"] = "unrelated-root"
    elif change == "question":
        two["prompt"][0]["content"] += " After repairs?"
    elif change == "source":
        two["source_id"] = "different-document"
    elif change == "tool":
        two["prompt"].append({"role": "tool", "content": "Different measured condition.", "tool_call_id": "t"})
    else:
        target = "orpo"
    assert not same_training_task_view(one, "orpo", two, target)


def test_legacy_candidate_ids_are_literal_and_new_task_identity_preserves_role_context():
    one = candidate()
    from lib.contexts.review.domain.candidate_content import candidate_digest
    assert repaired_candidate_identity("child", "dpo", one, one["revision_context"]) == candidate_digest(
        ["child", "dpo", one["revision_context"]["candidate_id"]])
    same = deepcopy(one)
    same["prompt"][0]["reasoning_content"] = "Private historical reasoning, not a different question."
    assert actual_qa_input_identity(one, "dpo") == actual_qa_input_identity(same, "dpo")
    same["prompt"].insert(0, {"role": "system", "content": "A different task constraint."})
    assert actual_qa_input_identity(one, "dpo") != actual_qa_input_identity(same, "dpo")


def test_multiple_versions_of_one_format_fail_before_any_conflicting_repair():
    one, two = candidate("orpo"), candidate("orpo")
    two["revision_context"]["parent_run_id"] = "another-version"
    items = [{"target": "orpo", "record": row, "revision_context": row["revision_context"]} for row in (one, two)]
    with pytest.raises(ValueError, match="duplicate_review_repair_task_view"):
        validate_format_view_selections(items, "child")
    other = candidate("dpo")
    validate_format_view_selections([items[0], {"target": "dpo", "record": other,
        "revision_context": other["revision_context"]}], "child")


def test_historical_sibling_is_authenticated_on_both_sides_before_publication():
    current, owner = candidate("orpo"), candidate("dpo")
    port = Mock()
    port.publication_owner.return_value = ("dpo", owner)
    duplicate = {"id": "old-run:old-dpo", "run_id": "old-run"}
    assert AuthorizeTrainingFormatView(port).execute(current, "orpo", duplicate)
    assert [call[0] for call in port.mock_calls] == ["authenticate_current", "publication_owner", "authenticate_owner"]
    port.authenticate_owner.assert_called_once_with("old-run", owner)
    port.reset_mock()
    owner["revision_context"]["ancestors"][0]["candidate_id"] = "unrelated"
    assert not AuthorizeTrainingFormatView(port).execute(current, "orpo", duplicate)
    port.authenticate_owner.assert_not_called()


@pytest.mark.parametrize("which", ["authenticate_current", "authenticate_owner"])
def test_tampered_authorization_cannot_use_format_identity_to_pass(which):
    port = Mock()
    port.publication_owner.return_value = ("dpo", candidate("dpo"))
    getattr(port, which).side_effect = ValueError("human_session_result_changed")
    with pytest.raises(ValueError, match="human_session_result_changed"):
        AuthorizeTrainingFormatView(port).execute(candidate("orpo"), "orpo", {"run_id": "old"})


def test_pending_publication_accepts_three_views_and_counts_one_task_but_still_isolates_new_root(tmp_path):
    class Workflow(WorkflowQADirector):
        recipe = {"qa_agent_memory_version": 1, "qa_director": {"enabled": True}}
        state = {"id": "child"}
        path = tmp_path / "workflows" / "child"
        def check_cancel(self): pass
    workflow = Workflow()
    workflow.path.mkdir(parents=True)
    with workflow.qa_publication_guard() as published:
        for target in ("dpo", "orpo", "rlaif"):
            row = candidate(target)
            row["id"] = repaired_candidate_identity("child", target, row, row["revision_context"], format_lineage=True)
            assert workflow.final_qa_duplicate(row, target, published) is None
            workflow.register_final_qa_input(row, target)
            assert published.add(workflow.qa_history_row(row, target), namespace="sft")
        assert published.coverage(namespace="sft") == {"closed_book": 1}
        other = candidate("orpo", root="different-original-task")
        other["id"] = repaired_candidate_identity("child", "orpo", other, other["revision_context"], format_lineage=True)
        duplicate = workflow.final_qa_duplicate(other, "orpo", published)
        assert duplicate["duplicate_scope"] == "current_publication"


def test_sealed_owner_lookup_checks_native_artifacts_and_fails_on_changed_bytes(tmp_path):
    from tests.test_review_rework import _run
    from lib.contexts.review.infrastructure.format_lineage import SealedFormatLineageAdapter
    row = candidate("dpo")
    run, run_id, _, _ = _run(tmp_path, "dpo", records=[row])
    runtime = SimpleNamespace(path=tmp_path / "workflows" / ("b" * 32), state={"id": "b" * 32})
    adapter = SealedFormatLineageAdapter(runtime)
    duplicate = {"id": run_id + ":" + row["id"], "run_id": run_id}
    assert adapter.publication_owner(duplicate) == ("dpo", row)
    (run / "artifacts/dpo.jsonl").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        adapter.publication_owner(duplicate)
