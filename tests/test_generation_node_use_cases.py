"""Exercise node algorithms through memory ports without a workflow or model SDK."""
from __future__ import annotations
from copy import deepcopy
from types import SimpleNamespace
import pytest

from lib.contexts.workflows.application.generate_sft import GenerateSft
from lib.contexts.workflows.application.generate_directed_sft import GenerateDirectedSft
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.application.trim_reasoning import TrimReasoning
from lib.contexts.workflows.domain.dialogue import dialogue_digest
from lib.contexts.workflows.domain.generation import EmptyVisibleAnswer


TEXT = "设备维护前必须断电，断电后检查线路，完成维护后记录检查结果。"
ANSWER = "先断电，再检查线路，最后记录检查结果。"
REASONING = "检查线路需要先切断电源；记录依赖检查完成，所以最后记录结果。"


def score():
    return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "前提、结果与操作顺序一致。"}


class MemoryGenerationPort:
    """Only the effects used by each test exist; accidental effects fail loudly."""
    current_stage = "cot"

    def __init__(self, replies=()):
        self.replies = iter(replies)
        self.calls = []
        self.checkpoints = {}
        self.events = []
        self.styles = {}

    def generation_style(self, stage, sample_id):
        return deepcopy(self.styles.get(stage))

    def reject(self, record, reason):
        return {"id": record["id"], "source_id": record["source_id"],
                "status": "quarantined", "reason": reason}

    def request(self, key, role, prompt_id, payload, **options):
        self.calls.append((deepcopy(key), role, prompt_id, deepcopy(payload), deepcopy(options)))
        return deepcopy(next(self.replies))

    def assess(self, key, context, candidate, **options):
        self.calls.append((deepcopy(key), "assessment", deepcopy(candidate)))
        return score()

    def check_adherence(self, *args, **kwargs):
        return {"keep": True, "adherence": 5, "reason": "保留实际任务与必要条件。"}

    check_style = check_adherence
    check_contract = check_adherence

    def prepare_source_request(self, payload, source, contract):
        return payload

    def separated_questions(self):
        return True

    def checkpoint(self, key, action):
        digest = dialogue_digest(key)
        if digest not in self.checkpoints:
            self.checkpoints[digest] = action()
        return deepcopy(self.checkpoints[digest])

    def checkpoint_has(self, key):
        return dialogue_digest(key) in self.checkpoints

    def event(self, kind, **fields):
        self.events.append((kind, fields))

    def independent_cot_assessment(self, record):
        return {}


def source():
    return {"id": "sample", "source_id": "source", "kind": "document", "text": TEXT}


def conversation():
    return {"id": "sample", "source_id": "source", "kind": "conversation",
            "messages": [{"role": "user", "content": "维护时先做什么？"},
                         {"role": "assistant", "content": ANSWER, "reasoning_content": REASONING}],
            "evidence_level": "recorded_context_model_assessed"}


def test_sft_retries_schema_without_spending_on_review_and_keeps_request_keys():
    port = MemoryGenerationPort([{"answer": "incomplete"},
        {"question": "维护时先做什么？", "answer": ANSWER, "reasoning": REASONING, "quotes": [TEXT]}])
    unit = source()
    before = deepcopy(unit)
    candidate = GenerateSft(port, {"brief": "", "review_repair": {}}).execute(unit)[0]
    assert candidate["status"] == "eligible" and candidate["repair_attempts"] == 1
    assert candidate["messages"][-1]["reasoning_content"] == REASONING
    assert [call[0] for call in port.calls] == [["sample", "sft", 0], ["sample", "sft", 1]]
    assert port.calls[1][3]["feedback"] == "回答结构不合格或存在敏感数据"
    assert candidate["generation_check"] == "structure_and_source_quotes"
    assert candidate["quote_spans"][0]["source_quote"] == TEXT
    assert unit == before


def test_imported_conversation_is_assessed_literally_without_generation():
    port = MemoryGenerationPort()
    unit = conversation()
    candidate = GenerateSft(port, {"brief": ""}).execute(unit)[0]
    assert candidate["messages"] == unit["messages"]
    assert candidate["reasoning_origin"] == "source"
    assert len(port.calls) == 1 and port.calls[0][1] == "assessment"
    assert port.calls[0][0] == ["sample", "recorded_sft_judge_v1", dialogue_digest(unit["messages"])]


def test_independent_cot_input_skips_unconsumed_draft_review_and_keeps_original():
    port = MemoryGenerationPort()
    port.styles["cot"] = {"instruction": "独立推导"}
    unit = conversation()
    candidate = GenerateSft(port, {"independent_cot_input_version": 1,
                                 "targets": ["cot"]}).execute(unit)[0]
    assert not port.calls
    assert "reasoning_content" not in candidate["messages"][-1]
    assert unit["messages"][-1]["reasoning_content"] == REASONING
    assert candidate["source_context"] == unit
    assert candidate["generation_check"] == "learner_structure_only_for_independent_cot"


def test_directed_sft_keeps_exact_assigned_question_and_rejects_foreign_evidence():
    port = MemoryGenerationPort([
        {"answer": ANSWER, "reasoning": REASONING, "quotes": ["unavailable source text"]},
        {"answer": ANSWER, "reasoning": REASONING, "quotes": [TEXT]},
    ])
    unit = {**source(), "family_id": "family", "qa_contract": {
        "question": "维护时先做什么？", "visible_context": "", "answer_policy": "answer"}}
    candidate = GenerateDirectedSft(port, {"qa_director": {"question_rules": "Q", "answer_rules": "A"},
                                          "review_repair": {}}).execute(unit)[0]
    assert candidate["status"] == "eligible"
    assert candidate["messages"][0] == {"role": "user", "content": unit["qa_contract"]["question"]}
    assert candidate["repair_attempts"] == 1 and candidate["quotes"] == [TEXT]
    assert [call[2] for call in port.calls] == ["workflow.sft_answer", "workflow.sft_answer"]
    assert port.calls[1][3]["feedback"] == "证据必须是教师来源中的逐字片段；不能把历史答案当作事实依据。"


def test_native_cot_retains_provider_bytes_including_whitespace():
    port = MemoryGenerationPort()
    unit = conversation()
    unit["reasoning_origin"] = "provider_reasoning"
    unit["messages"][-1]["reasoning_content"] = "  " + REASONING + "\n"
    unit["messages"][-1]["content"] = " " + ANSWER + "\n"
    candidate = GenerateCot(port, {"review_repair": {}}).extract(unit)[0]
    assert candidate["reasoning"] == ["  " + REASONING + "\n"]
    assert candidate["answer"] == " " + ANSWER + "\n"
    assert candidate["reasoning_origin"] == "provider_reasoning" and not port.calls


def test_styled_cot_keeps_unassessed_answer_out_of_request_without_erasing_input():
    port = MemoryGenerationPort([{"reasoning": REASONING, "answer": ANSWER}])
    unit = conversation()
    unit["source_context"] = source()
    before = deepcopy(unit)
    candidate = GenerateCot(port, {"review_repair": {}, "training_surface_protocol_version": 1}).generate_styled(
        unit, {"instruction": "自然解释条件与结论。"})[0]
    payload = port.calls[0][3]
    assert "reference_answer" not in payload
    assert payload["reference_answer_status"] == "unassessed_not_supplied"
    assert payload["prompt"] == unit["messages"][:-1]
    assert candidate["reasoning"] == [REASONING] and candidate["answer"] == ANSWER
    assert unit == before


def test_native_empty_answer_receipts_prevent_repurchasing_on_resume():
    rationale = {"reasoning": REASONING, "reasoning_trace": {
        "task": "说明维护时断电、检查和记录的先后关系。",
        "premises": ["检查线路必须先切断电源。"],
        "steps": [{"basis": "检查线路必须先切断电源。", "inference": "先决条件应先于依赖它的操作满足。",
                   "result": "断电应安排在检查线路之前。"}],
        "checks": [{"check": "核对检查线路是否先于断电。", "result": "所给顺序将断电放在检查之前，保留先决条件。"}],
        "limitations": []}}
    port = MemoryGenerationPort([rationale, rationale])
    attempts = []
    def empty_answer(key, payload, *, catch_empty, **options):
        attempts.append((key, options))
        assert catch_empty
        raise EmptyVisibleAnswer()
    port.request_native_answer = empty_answer
    generator = GenerateCot(port, {"native_answer_retry_version": 1})
    task = {"prompt": conversation()["messages"][:-1], "source": source()}
    first = generator.generate_native_answer(source(), task, {"instruction": "自然推导"}, None, 0)
    second = generator.generate_native_answer(source(), task, {"instruction": "自然推导"}, None, 0)
    assert first == second and first[0] is None
    assert len(attempts) == 2 and len(port.events) == 2
    assert attempts[0][0] == ["sample", "cot_native_answer_v1", 0]
    assert attempts[1][0] == ["sample", "cot_native_answer_v1", 0, "visible_retry_v1", 1]
    assert "native_request_attempts" not in attempts[0][1]
    assert attempts[1][1]["native_request_attempts"] == 1
    assert len(first[1]["native_answer_terminal"]["empty_response_receipts"]) == 2


@pytest.mark.parametrize("evidence_policy", [0, 1, 2])
def test_rejected_native_trace_retains_last_draft_without_buying_an_answer(evidence_policy):
    from lib.contexts.workflows.domain.reasoning_checklist import default_reasoning_checklist
    first = {"reasoning": "  第一稿：" + REASONING + "\n", "reasoning_trace": {}}
    last = {"reasoning": "  修订稿：" + REASONING + "\n", "reasoning_trace": {
        "task": "解释维护条件与先后关系。", "premises": [TEXT],
        "steps": [{"basis": [TEXT], "inference": "前提需先于依赖的动作满足。", "result": ANSWER}],
        "checks": [{"check": "检查先决条件。", "result": "先断电再检查，保留了依赖。"}], "limitations": []}}
    port = MemoryGenerationPort([first, last])
    port.validate_native_prefill = lambda: None
    checklist = {**default_reasoning_checklist(), "version": 6, "evidence_policy": evidence_policy}
    usecase = GenerateCot(port, {"node_generation": {"cot": {
        "answer_mode": "native_prefill", "self_check": checklist}}})
    port.generate_native_answer = usecase.generate_native_answer
    candidate = usecase.generate_styled(conversation(), {"instruction": "解释实际前提。"})[0]
    assert candidate["status"] == "quarantined"
    assert candidate["reason"] == "cot_generation_failed_after_repair"
    assert candidate["feedback"]["reasoning_protocol"] == "authored_reasoning_vacuous_step"
    assert [c[2] for c in port.calls] == ["workflow.cot_rationale", "workflow.cot_rationale"]
    if evidence_policy == 2:
        assert candidate["reasoning"] == [last["reasoning"]] and candidate["answer"] == ""
        assert candidate["feedback"]["reasoning_trace"] == last["reasoning_trace"]
        candidate["feedback"]["reasoning_trace"]["steps"][0]["basis"].append("mutated receipt")
        assert last["reasoning_trace"]["steps"][0]["basis"] == [TEXT]
    else:
        assert "reasoning" not in candidate and "reasoning_trace" not in candidate["feedback"]


def test_native_surface_rejection_preserves_raw_draft_without_certifying_it(monkeypatch):
    from lib.contexts.workflows.domain.reasoning_checklist import default_reasoning_checklist
    from lib.domain import authored_reasoning, training_surface
    rationale = {"reasoning": REASONING, "reasoning_trace": {"raw": "unmodified"}}
    port = MemoryGenerationPort([rationale])
    checklist = {**default_reasoning_checklist(), "version": 6, "evidence_policy": 2}
    usecase = GenerateCot(port, {"node_generation": {"cot": {"self_check": checklist}}})
    monkeypatch.setattr(authored_reasoning, "authored_rationale_issue", lambda candidate: None)
    monkeypatch.setattr(training_surface, "training_surface_issue", lambda *a, **k: "hidden_source_leakage")
    value, failure = usecase.generate_native_answer(source(), {
        "prompt": conversation()["messages"][:-1], "source": source()}, {"instruction": "自然解释"}, None, 0)
    assert value is None and failure["training_surface"] == "hidden_source_leakage"
    assert failure["reasoning"] == REASONING and failure["reasoning_trace"] == rationale["reasoning_trace"]
    assert len(port.calls) == 1  # No quality review or native-answer request.


def test_trim_preserves_answers_and_original_reasoning_through_memory_port():
    port = MemoryGenerationPort([{"reasoning": "先满足断电条件，再检查线路；检查结束后记录结果。"}])
    unit = {**conversation(), "status": "eligible"}
    before = deepcopy(unit)
    candidate = TrimReasoning(port, {"reasoning_trim": {"template": "custom"},
                                   "reasoning_trim_prompt": "只改解释，不修改答案。"}).execute(
        {"target": "sft", "sample": unit})[0]
    assert candidate["messages"][-1]["content"] == ANSWER
    assert candidate["messages"][-1]["reasoning_content"] != REASONING
    assert candidate["reasoning_trim"]["status"] == "applied"
    assert candidate["reasoning_trim"]["fields"][0]["input_sha256"] == dialogue_digest(REASONING)
    assert unit == before


def test_reasoning_delivery_does_not_iterate_collections_in_application():
    class OpaqueCollection:
        def __iter__(self):
            raise AssertionError("The application must not materialize dataset rows")
    calls = []
    port = SimpleNamespace(
        prepare_trim_stage=lambda: calls.append("prepare"),
        trim_collections=lambda collections, targets: calls.append(("trim", tuple(targets))),
        apply_reasoning_trim=lambda collections, **options: calls.append(("apply", options)),
        publish_finalized_sft=lambda collections: calls.append("publish"),
    )
    usecase = TrimReasoning(port, {"version": 15, "targets": ["sft", "cot"],
                                 "reasoning_trim": {"enabled": True}})
    collections = {"sft": OpaqueCollection(), "cot": OpaqueCollection()}
    usecase.apply(collections)
    usecase.finalize(collections)
    assert calls == ["prepare", ("trim", ("sft", "cot")),
                     ("apply", {"targets": ("cot",)}), "publish"]
