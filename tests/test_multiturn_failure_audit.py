"""Unaccepted generated turns remain inspectable without becoming training rows."""
from copy import deepcopy

import pytest

from lib.infrastructure.training_workflow import Workflow, digest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def grade(passing):
    return {"keep": passing, "grounded": passing, "reasoning_valid": passing,
        "correctness": 5 if passing else 2,
        "scores": {name: 5 if passing else 2 for name in
                   ("correctness", "reasoning", "grounding", "instruction", "safety")},
        "reason": "The draft conflicts with the actual constraints."}


def worker(*, active=True, accepted_prefix=False):
    value = object.__new__(Workflow)
    value.recipe = {"brief": "", "conversation_turns": 2,
        "multiturn_failure_audit_version": 1 if active else 0}
    value.separated_questions = lambda: False
    value.participant_utterances = lambda: False
    value.ask = lambda key, role, prompt, data, **kwargs: (
        {"message": ("How do I draft the first proposal?" if data["turn"] == 1 else
                     "Now the storage limit is lower; revise the proposal.")}
        if prompt == "workflow.multiturn_user" else
        {"answer": "The proposal must respect the stated constraints.", "quotes": [],
         "reasoning": "I compare the proposed change with the constraint and find the unsupported assumption."})
    checks = iter(([True, False, False] if accepted_prefix else [False, False]))
    value.judge_answer = lambda *args, **kwargs: grade(next(checks))
    value.style_check = lambda *args, **kwargs: {"keep": True, "adherence": 5, "reason": "Suitable style."}
    return value


def unit():
    return {"id": "task", "source_id": "brief", "kind": "brief", "text": "Design a feasible proposal."}


@pytest.mark.parametrize("prefix", [False, True])
def test_failed_turn_retains_exact_full_draft_and_accepted_prefix_for_diagnostics(prefix):
    source = unit()
    frozen = deepcopy(source)
    row = worker(accepted_prefix=prefix)._multiturn(source,
        style={"preset": "default", "instruction": "Keep the actual dependencies."})[0]
    assert row["status"] == "quarantined" and row["reason"] == "multiturn_turn_failed_after_repair"
    assert "messages" not in row  # Diagnostic drafts cannot silently reach trainer projections.
    draft = row["failed_generation"]
    assert draft["training_eligible"] is False
    assert draft["candidate_sha256"] == digest({"messages": draft["messages"], "quotes": draft["quotes"]})
    assert draft["messages"][-1]["reasoning_content"].startswith("I compare")
    assert len(draft["messages"]) == (4 if prefix else 2)
    assert len(row["accepted_prefix"]) == (2 if prefix else 0)
    assert row["source_context"] == source == frozen


def test_frozen_old_failure_record_does_not_gain_a_new_body_projection():
    row = worker(active=False)._multiturn(unit(),
        style={"preset": "default", "instruction": "Keep dependencies."})[0]
    assert row["status"] == "quarantined"
    assert not {"failed_generation", "accepted_prefix", "source_context"}.intersection(row)


def contract_worker(monkeypatch, error, *, active=True):
    from lib.infrastructure import source_memory_messages
    monkeypatch.setattr(source_memory_messages, "directed_source_request", lambda recipe, context, source, contract: context)
    value = worker(active=active)
    value.recipe["qa_director"] = {"question_rules": "Question constraints", "answer_rules": "Answer constraints"}
    value.judge_answer = lambda *args, **kwargs: grade(True)
    def fail(*args, **kwargs):
        raise ValueError(error)
    value.qa_contract_check = fail
    source = {**unit(), "qa_contract": {"question": "How do I draft the first proposal?", "visible_context": ""}}
    return value, source


def test_invalid_contract_schema_preserves_draft_only_for_new_audit(monkeypatch):
    value, source = contract_worker(monkeypatch, "invalid_qa_director_judge_schema")
    row = value._multiturn(source, style={"preset": "default", "instruction": "Keep dependencies."})[0]
    assert row["status"] == "quarantined"
    assert row["reason"] == "multiturn_contract_output_invalid"
    assert row["feedback"] == "invalid_qa_director_judge_schema"
    assert "messages" not in row and row["failed_generation"]["training_eligible"] is False
    assert row["failed_generation"]["messages"][-1]["reasoning_content"].startswith("I compare")


@pytest.mark.parametrize("error", ["checkpoint_integrity_error", "prompts_changed_create_new_run",
    "model_configuration_changed_create_new_run", "workflow_endpoint_pin_invalid", "unexpected_internal_error"])
def test_integrity_and_configuration_errors_still_stop_execution(monkeypatch, error):
    value, source = contract_worker(monkeypatch, error)
    with pytest.raises(ValueError, match="^" + error + "$"):
        value._multiturn(source, style={"preset": "default", "instruction": "Keep dependencies."})


def test_legacy_invalid_contract_schema_still_raises(monkeypatch):
    value, source = contract_worker(monkeypatch, "invalid_qa_director_judge_schema", active=False)
    with pytest.raises(ValueError, match="^invalid_qa_director_judge_schema$"):
        value._multiturn(source, style={"preset": "default", "instruction": "Keep dependencies."})
