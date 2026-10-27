"""Offline contracts: author once, keep the full body, never fabricate a review."""
from copy import deepcopy
import hashlib

import pytest

from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.application.check_reasoning import CheckReasoning
from lib.contexts.workflows.domain.integrated_reflection import (
    default_integrated_reflection, reflection_instruction, answer_reflection_instruction,
)
from lib.contexts.workflows.domain.reasoning_checklist import validate_reasoning_checklist
from lib.contexts.workflows.contracts import training_record
from lib.domain.workflow_node_prompts import active_node_prompt_ids
from lib.domain.workflow_generation import validate_node_generation
from test_generation_node_use_cases import MemoryGenerationPort, conversation, REASONING, ANSWER
from test_workflow_styled_generation import maintenance_reasoning_trace


def recipe(mode="joint"):
    return {"authored_reasoning_protocol_version": 1, "authored_trace_transport_version": 1,
            "training_surface_protocol_version": 2,
            "node_generation": {"cot": {"enabled": True, "answer_mode": mode,
                                        "self_check": default_integrated_reflection()}}}


def body():
    return {"reasoning": REASONING, "answer": ANSWER, "reasoning_trace": maintenance_reasoning_trace()}


class NoReviewPort(MemoryGenerationPort):
    def assess(self, *args, **kwargs):
        pytest.fail("generation-time reflection must not dispatch an external review")
    check_style = assess
    check_contract = assess
    check_adherence = assess
    reasoning_tool_turn = assess


def test_joint_generation_one_request_and_unchanged_training_body():
    port = NoReviewPort([body()])
    config = recipe()
    row, = GenerateCot(port, config).generate_styled(conversation(), {"instruction": "自然解释实际依赖。"})
    assert row["status"] == "eligible" and row["repair_attempts"] == 0
    assert len(port.calls) == 1 and port.calls[0][2] == "workflow.cot_generate"
    assert port.calls[0][4]["structured_response_attempts"] == 1
    assert "generation_style" not in port.calls[0][3]
    assert port.calls[0][4]["instruction"].count("自然解释实际依赖。") == 1
    assert "judge" not in row and "reasoning_checklist" not in row and "style_check" not in row
    assert row["reasoning_reflection"]["independent_review"] is False
    assert "passed" not in row["reasoning_reflection"]
    assert training_record("cot", row) == {
        "question": conversation()["messages"][:-1], "reasoning": [REASONING], "answer": ANSWER}


def test_invalid_draft_stays_quarantined_without_whole_draft_regeneration():
    draft = {**body(), "reasoning_trace": {}}
    before = deepcopy(draft)
    port = NoReviewPort([draft])
    row, = GenerateCot(port, recipe()).generate_styled(conversation(), {"instruction": "自然解释。"})
    assert row["status"] == "quarantined" and len(port.calls) == 1
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    assert draft == before and "reasoning_reflection" not in row


def test_native_answer_reuses_full_authored_prefix_without_prefix_or_final_review():
    rationale = body(); rationale.pop("answer")
    port = NoReviewPort([rationale])
    port.validate_native_prefill = lambda: None
    requests = []
    class Answer(dict):
        response_details = {"reasoning_prefill": {"transport": "deepseek_chat_beta",
            "prefix_sha256": hashlib.sha256(REASONING.encode()).hexdigest()},
            "reasoning_content": "provider continuation retained in its receipt"}
    def answer(key, payload, **options):
        requests.append((key, payload, options))
        return Answer(answer=ANSWER)
    port.request_native_answer = answer
    config = recipe("native_prefill")
    config["node_generation"]["cot"]["self_check"]["rules"].append(
        {"id": "final", "requirement": "FINAL ONLY", "allow_na": False, "phases": ["final"]})
    usecase = GenerateCot(port, config)
    port.generate_native_answer = usecase.generate_native_answer
    row, = usecase.generate_styled(conversation(), {"instruction": "自然解释。"})
    assert row["status"] == "eligible"
    assert len(port.calls) == len(requests) == 1
    assert requests[0][2]["reasoning_prefix"] == REASONING
    assert requests[0][2]["native_request_attempts"] == 1
    assert "FINAL ONLY" in requests[0][2]["instruction"]
    assert "FINAL ONLY" not in port.calls[0][4]["instruction"]
    assert port.calls[0][4]["structured_response_attempts"] == 1
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    assert "prefix_review" not in row and "reasoning_checklist" not in row
    assert row["reasoning_reflection"]["independent_review"] is False
    assert row["answer_response_details"]["reasoning_content"] == Answer.response_details["reasoning_content"]


def test_native_invalid_prefix_is_preserved_and_never_dispatched():
    rationale = {"reasoning": REASONING, "reasoning_trace": {}}
    port = NoReviewPort([rationale])
    port.validate_native_prefill = lambda: None
    port.request_native_answer = lambda *a, **kw: pytest.fail("invalid prefix dispatched")
    usecase = GenerateCot(port, recipe("native_prefill"))
    port.generate_native_answer = usecase.generate_native_answer
    row, = usecase.generate_styled(conversation(), {"instruction": "自然解释。"})
    assert row["status"] == "quarantined" and row["reasoning"] == [REASONING]
    assert len(port.calls) == 1


def test_guidance_is_phase_specific_literal_and_not_a_review_protocol():
    config = default_integrated_reflection()
    config["rules"] = [{"id": "user", "requirement": "CUSTOM RULE", "allow_na": False},
                       {"id": "final", "requirement": "FINAL RULE", "allow_na": True, "phases": ["final"]}]
    assert validate_reasoning_checklist(config) == config
    assert validate_node_generation({"cot": {"self_check": config}})["cot"]["self_check"] == config
    assert "CUSTOM RULE" in reflection_instruction(config, phase="prefix")
    assert "FINAL RULE" not in reflection_instruction(config, phase="prefix")
    assert "FINAL RULE" in reflection_instruction(config)
    with pytest.raises(ValueError, match="generation_reflection_is_not_independent_review"):
        CheckReasoning(NoReviewPort(), config)
    with pytest.raises(ValueError):
        validate_reasoning_checklist({**config, "evidence_policy": 2})


def test_default_guidance_centers_real_user_requests_and_checks_final_delivery_only_once():
    config = default_integrated_reflection()
    prefix = reflection_instruction(config, phase="prefix")
    final_only = answer_reflection_instruction(config)
    joint = reflection_instruction(config)
    rules = {r["id"]: r for r in config["rules"]}
    assert rules["task"]["requirement"] in prefix
    assert rules["intent"]["requirement"] in prefix
    for role_rule in ("perspective", "user_words"):
        # Role guidance belongs to authored reasoning on both routes. It is
        # not repeated on answer continuation or added to frozen user rules.
        assert prefix.count(rules[role_rule]["requirement"]) == 1
        assert joint.count(rules[role_rule]["requirement"]) == 1
        assert rules[role_rule]["requirement"] not in final_only
    assert rules["delivery"]["requirement"] not in prefix
    assert final_only.count(rules["delivery"]["requirement"]) == 1
    assert joint.count(rules["delivery"]["requirement"]) == 1
    assert rules["task"]["requirement"] not in final_only
    # Custom and frozen guidance remain literal; new defaults do not migrate it.
    frozen = {"version": 7, "rules": [{"id": "custom", "requirement": "MY SAVED REQUIREMENT", "allow_na": False}]}
    assert "MY SAVED REQUIREMENT" in reflection_instruction(frozen)
    assert rules["task"]["requirement"] not in reflection_instruction(frozen)
    assert validate_reasoning_checklist(frozen) == frozen
    assert all(rules[k]["requirement"] not in reflection_instruction(frozen)
               for k in ("perspective", "user_words"))


@pytest.mark.parametrize("mode,expected", [
    ("joint", ("workflow.cot_generate",)),
    ("native_prefill", ("workflow.cot_rationale", "workflow.cot_native_answer")),
])
def test_active_node_prompts_only_offer_requests_that_will_run(mode, expected):
    assert active_node_prompt_ids("cot", "文档", node_generation=recipe(mode)["node_generation"],
                                  qa_director={"enabled": True}) == expected


def test_explicit_review_node_keeps_rejection_without_restarting_reflection_as_a_reviewer():
    from lib.contexts.review.application.review_candidate import ReviewCandidate
    from test_pipeline_use_cases import ReviewMemory
    class Port(ReviewMemory):
        def payload(self, target, candidate):
            return {key: candidate[key] for key in ("question", "reasoning", "answer")}
        def check_reasoning(self, *a, **kw):
            pytest.fail("integrated guidance is not another review round")
    row = {"id": "sample", "status": "eligible", "question": conversation()["messages"][:-1],
           "reasoning": [REASONING], "answer": ANSWER}
    port = Port()
    reviewed, = ReviewCandidate(port, {**recipe(), "review_repair": {"mode": "auto", "max_rounds": 0},
        "reasoning_distillation_audit_version": 1}).execute({"target": "cot", "record": row})
    assert reviewed["status"] == "quarantined" and reviewed.get("review_repair")
    assert port.scores == 1 and port.repairs == 0


def test_durable_workflow_freezes_reflection_exports_full_body_and_resumes_without_calls(tmp_path):
    import json
    from test_workflow_styled_generation import run, Generator, Reviewer
    from lib.infrastructure import training_workflow as engine
    output, run_id, path = run(tmp_path, targets=["cot"], conversation=True,
        node_generation=recipe()["node_generation"])
    frozen = engine.read_json(path / "recipe.json")
    assert frozen["version"] == 35 and frozen["node_generation"]["cot"]["self_check"]["version"] == 7
    assert frozen["authored_stage_contract_version"] == 1
    assert frozen["reflection_guidance_placement_version"] == 1
    generator, reviewer = Generator(), Reviewer()
    reviewer.chat = lambda *a, **kw: pytest.fail("unexpected independent review in durable workflow")
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "completed" and len(generator.calls) == 1
    messages = generator.calls[0]
    assert messages[-1]["role"] == "system"
    for rule in frozen["node_generation"]["cot"]["self_check"]["rules"]:
        assert messages[-1]["content"].count(rule["requirement"]) == 1
        assert all(rule["requirement"] not in m["content"] for m in messages[:-1])
    record = engine.read_json(path / "artifacts/cot.records.json")[0]
    training = path / "artifacts/cot.jsonl"
    before = training.read_bytes()
    exported = json.loads(before)
    assert exported["reasoning"] == record["reasoning"] and exported["answer"] == record["answer"]
    assert set(exported) == {"question", "reasoning", "answer"}
    assert record["reasoning_reflection"]["independent_review"] is False
    assert "judge" not in record and "reasoning_checklist" not in record
    engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert len(generator.calls) == 1 and training.read_bytes() == before


def test_invalid_json_does_not_trigger_hidden_structured_regeneration(tmp_path):
    from test_workflow_styled_generation import run
    from lib.infrastructure import training_workflow as engine
    output, run_id, path = run(tmp_path, targets=["cot"], conversation=True,
        node_generation=recipe()["node_generation"])
    class InvalidJSON:
        calls = 0
        def chat(self, *args, **kwargs):
            self.calls += 1
            return 'This is not a JSON response.'
    client = InvalidJSON()
    state = engine.Workflow(output, run_id, tmp_path, generator=client).execute()
    assert state["status"] == "failed" and state["error"] == "ModelJSONError"
    assert client.calls == 1


def test_empty_native_answer_keeps_full_prefix_without_hidden_retry():
    from lib.contexts.workflows.domain.generation import EmptyVisibleAnswer
    rationale = body(); rationale.pop("answer")
    port = NoReviewPort([rationale])
    port.validate_native_prefill = lambda: None
    calls = []
    def empty_answer(*args, **kwargs):
        calls.append(kwargs)
        raise EmptyVisibleAnswer()
    port.request_native_answer = empty_answer
    config = {**recipe("native_prefill"), "native_answer_retry_version": 1}
    usecase = GenerateCot(port, config)
    port.generate_native_answer = usecase.generate_native_answer
    row, = usecase.generate_styled(conversation(), {"instruction": "自然解释。"})
    assert len(calls) == len(port.calls) == 1
    assert row["status"] == "quarantined" and row["reasoning"] == [REASONING]
    assert row["native_answer_terminal"]["answer_attempts"] == 1
    assert len(row["native_answer_terminal"]["empty_response_receipts"]) == 1


def test_report_exposes_reflection_as_provenance_without_fabricating_verification():
    from lib.domain.workflow_report import report_sample
    port = NoReviewPort([body()])
    row, = GenerateCot(port, recipe()).generate_styled(conversation(), {"instruction": "自然解释。"})
    report = report_sample("cot", row, training_record("cot", row), released=True, position=1)
    assert report["provenance"]["reasoning_reflection"] == row["reasoning_reflection"]
    assert not any(report["reviews"].values())
