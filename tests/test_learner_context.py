"""Private teacher facts cannot rescue an incomplete exported training input."""
from copy import deepcopy

import pytest

from lib.domain.learner_context import learner_contract, learner_messages
from lib.infrastructure.workflow_qa_director import WorkflowQADirector
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair
from lib.prompts import get, render


def verdict(keep=True):
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": 5 if keep else 1,
            "scores": {name: 5 if keep else 1 for name in
                       ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "Complete learner context." if keep else "The answer cites material absent from the user input."}


def test_blinded_contract_cannot_supply_missing_planned_question_or_context():
    contract = {"qa_type": "closed_book", "answer_policy": "answer", "question": "PRIVATE_PLANNED_OBJECT",
                "visible_context": "PRIVATE_PLANNED_BACKGROUND", "guidance": "PRIVATE_TEACHER_GUIDANCE",
                "evidence_quotes": ["PRIVATE_QUOTE"], "dialogue_design": {"context_links": ["PRIVATE_LINK"]}}
    assert learner_contract(contract) == {"qa_type": "closed_book", "answer_policy": "answer"}
    assert "PRIVATE" not in str(learner_contract(contract))


def test_preference_context_projection_excludes_negative_and_private_metadata():
    payload = {"prompt": [{"role": "user", "content": "Inspect ACME A2 equipment."}],
               "chosen": [{"role": "assistant", "content": "Turn the power off first."}],
               "rejected": [{"role": "assistant", "content": "PRIVATE_NEGATIVE"}],
               "teacher_evidence": "PRIVATE_TEACHER"}
    before = deepcopy(payload)
    projected = learner_messages("dpo", payload)
    assert projected == [*payload["prompt"], *payload["chosen"]]
    assert "PRIVATE" not in str(projected)
    projected[0]["content"] = "changed"
    assert payload == before


@pytest.mark.parametrize("prompt_id,version", [("workflow.sft_directed_check", "1.2.0"),
    ("workflow.multiturn_directed_check", "1.2.0"), ("workflow.preference_directed_check", "1.1.0"),
    ("workflow.cot_directed_check", "1.1.0"), ("workflow.trim_directed_check", "1.1.0")])
def test_contract_reviewer_receives_only_actual_messages_and_task_rules(prompt_id, version):
    class Harness(WorkflowQADirector):
        recipe = {"prompts": {prompt_id: {"version": version}},
                  "qa_director": {"question_rules": "Q rules", "answer_rules": "A rules"}}
        def ask(self, key, role, name, data, **kwargs):
            self.data = deepcopy(data)
            return {"keep": True, "adherence": 5, "reason": "Actual context checked."}
    workflow = Harness()
    messages = [{"role": "user", "content": "How does ACME A2 work?"},
                {"role": "assistant", "content": "It checks the power state."}]
    unit = {"text": "PRIVATE_TEACHER", "kind": "document", "source_context": {"text": "PRIVATE_SOURCE"},
            "qa_contract": {"qa_type": "closed_book", "answer_policy": "answer", "guidance": "PRIVATE_GUIDANCE"}}
    result = workflow.qa_contract_check(["test"], unit, messages, prompt_id=prompt_id,
        dialogue_state={"open_issues": ["PRIVATE_STATE"]}, dialogue_steps=[{"source": "PRIVATE_STEPS"}])
    assert result["keep"] and workflow.data["learner_messages"] == messages
    assert workflow.data["learner_context_only"]
    assert "PRIVATE" not in str(workflow.data)
    assert workflow.data["question_rules"] == "Q rules"


def test_historical_contract_review_request_keeps_original_source_payload():
    class Harness(WorkflowQADirector):
        recipe = {"prompts": {"workflow.sft_directed_check": {"version": "1.1.0"}},
                  "qa_director": {"question_rules": "Q", "answer_rules": "A"}}
        def ask(self, key, role, name, data, **kwargs):
            self.data = data
            return {"keep": True, "adherence": 5, "reason": "Historical contract."}
    workflow = Harness()
    unit = {"text": "PINNED SOURCE", "kind": "document", "qa_contract": {"qa_type": "closed_book"}}
    workflow.qa_contract_check(["old"], unit, [{"role": "user", "content": "Question"},
                                               {"role": "assistant", "content": "Answer"}])
    assert workflow.data["teacher_evidence"] == "PINNED SOURCE"
    assert "learner_context_only" not in workflow.data


def test_new_review_repair_rejects_hidden_reference_without_using_teacher_to_rescue_it():
    class Harness(WorkflowReviewRepair):
        recipe = {"prompts": {"workflow.review_score": {"version": "1.1.0"}}, "package_review": {"enabled": True}}
        state = {"stages": {"review": {}}}
        def __init__(self): self.calls = []
        def save(self, **kwargs): pass
        def _review_payload(self, target, row): return {"messages": deepcopy(row["messages"])}
        def ask(self, key, role, prompt_id, data, **kwargs):
            self.calls.append(deepcopy(data))
            # Simulate a factual grader passing a sourced answer while a
            # separate context grader notices its unsupported source reference.
            return verdict(not data.get("learner_context_only"))
    workflow = Harness()
    row = {"qa_contract": {"qa_type": "closed_book", "answer_policy": "answer", "guidance": "PRIVATE_GUIDANCE"},
           "messages": [{"role": "user", "content": "How does ACME A2 work?"},
                        {"role": "assistant", "content": "According to the provided material, it checks the power."}]}
    result = workflow._review_score("sft", row, {"teacher_evidence": "PRIVATE_FACT"}, ["sample"], "PRIVATE_INSTRUCTION")
    assert not result["keep"] and not row["learner_context_check"]["keep"]
    blind = workflow.calls[-1]
    assert blind["source_evidence"] is None and "PRIVATE" not in str(blind)
    assert blind["candidate"]["messages"] == row["messages"]


def test_prompt_upgrade_preserves_explicit_legacy_lookup_and_adds_semantic_context_rules():
    for name, old, new in (("sft", "1.0.0", "1.1.0"), ("qa_director", "1.1.0", "1.2.0"),
                          ("sft_directed", "1.1.0", "1.2.0"), ("review_score", "1.0.0", "1.1.0")):
        previous, current = get("workflow." + name, version=old), get("workflow." + name)
        assert previous.version == old and current.version == new
        assert render(current).startswith(render(previous))
        assert "https://aclanthology.org/2021.tacl-1.27/" in current.source
        assert "去语境化" in render(current) or "上下文完整性" in render(current)


def test_same_run_review_and_repair_get_full_verified_neighbor_context(tmp_path):
    from test_workflow_source_context import cleaned, rows, workflow
    from lib.infrastructure.workflow_source_context import prepare_source_context_packs
    worker = workflow(tmp_path)
    prepared = list(prepare_source_context_packs(worker, rows(tmp_path, [
        cleaned(0, "Preparation: switch off the pump."),
        cleaned(1, "Restart: wait for the supervisor's permission.") ])))
    class Harness(WorkflowReviewRepair):
        path = tmp_path
        recipe = {"source_context_pack_version": 1, "sources": [{"sha256": "manual", "kind": "document"}]}
    row = {"source_id": "manual", "source_context": prepared[0]}
    evidence = Harness()._review_evidence(row)
    assert "Preparation:" in evidence["teacher_evidence"] and "Restart:" in evidence["teacher_evidence"]
    assert evidence["source_context_pack"] == prepared[0]["source_context_pack"]
    row["source_context"]["source_context_pack"]["relative_path"] = "source-context/packs/missing.json"
    with pytest.raises(ValueError):
        Harness()._review_evidence(row)  # never silently review against the anchor


def test_director_default_designs_roles_common_ground_and_progress_without_quotas():
    current = get("workflow.qa_director")
    assert "conversational_frame" in render(current)
    assert "真实messages" in render(current) and "只换人称" in render(current)
    assert "不是固定类别" in render(current)
    assert "https://aclanthology.org/2022.emnlp-main.714/" in current.source
    assert "conversational_frame" not in render(get("workflow.qa_director", version="1.1.0"))
