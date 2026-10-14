"""Offline end-to-end proof of independent question and answer authorship."""
from copy import deepcopy
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.model_request_reliability import ModelRequestError


SOURCE = "检查设备前必须先断电。\n结束后记录检查结果。\n办公室盆栽每周浇水一次。"
QUOTE = "检查设备前必须先断电。"
FIRST_QUESTION = "把设备检查的准备和收尾整理成一张操作卡。"
FOLLOWUP_QUESTION = "第二步再短一些，记录项沿用你刚才的卡片。"
DIRECTOR_QUESTION = "检查之前要先做什么？"
FIRST_ANSWER = "操作卡：一、先断电；二、检查设备；三、记录检查结果。"
FOLLOWUP_ANSWER = "第二步写“检查设备”。记录项为检查结果。"
HIDDEN_REASONING = "PRIVATE_REASONING_SENTINEL 这里是未对用户说出的内部推理。"


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Every model in this module is injected; no HTTP service is needed."""
    yield


def tool_payload(messages, name):
    return next((json.loads(message["content"]) for message in messages
                 if message["role"] == "tool" and message.get("name") == name), None)


def public_messages(messages):
    """Native reasoning is private metadata, never a blind actor's history."""
    return [{"role": message["role"], "content": message["content"]} for message in messages]


def task_payload(messages):
    last_user = next(message["content"] for message in reversed(messages) if message["role"] == "user")
    try:
        value = json.loads(last_user)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def materials(messages):
    """Read only source_memory: dialogue_dispatch is a separate tool result."""
    payload = tool_payload(messages, "source_memory") or {}
    result = []
    for pack in payload.get("teacher_context_pack", []):
        for original in pack["materials"]:
            material = deepcopy(original)
            if "numbered_text" in material:
                lines = material["numbered_text"].splitlines(keepends=True)
                material["text"] = "".join(line.split(": ", 1)[1] for line in lines)
            result.append(material)
    return result


def config(qa_type="closed_book", **changes):
    return {"enabled": True, "question_separation": True, "planning_mode": "adaptive",
            "batch_size": 1, "history_limit": 2,
            "type_weights": {key: int(key == qa_type) for key in
                ("closed_book", "grounded", "partial", "multi_source", "distractor")},
            "question_rules": "按独立情境发出请求，不预写助手答案。",
            "answer_rules": "回应实际请求，不改变发问者的消息。", **changes}


def brief(*, followup=False):
    return {"topic": "设备检查", "communication_goal": "根据已出现的成果提出简洁表达要求。" if followup
            else "请求协助整理可执行的操作卡。", "behaviors": ["practical_request"]}


def design():
    return {"interaction_goal": "协作完成适合实际使用的设备检查操作卡。",
            "user_intent": "整理操作卡。", "context_links": [],
            "success_criteria": ["操作卡符合资料与实际请求。"],
            "turn_guidance": ["按实际成果提出必要的修改请求。"],
            "stop_when": "真实修改完成，没有新的有用活动。"}


class Questioner:
    model = "offline-source-blind-questioner"

    def __init__(self, *, invalid=False, parser_error=False, api_error=False):
        self.calls, self.usage = [], {"calls": 0}
        self.invalid, self.parser_error, self.api_error = invalid, parser_error, api_error

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        assert tool_payload(messages, "source_memory") is None
        dispatch = tool_payload(messages, "dialogue_dispatch")
        assert dispatch is not None and "question_brief" in dispatch
        assert set(dispatch) <= {"question_brief", "visible_dialogue", "retry_code"}
        if self.api_error:
            raise ModelRequestError("service_authentication_failed", "fatal", 1)
        if self.parser_error:
            return "this is not valid JSON"
        if self.invalid:
            return json.dumps({"message": FIRST_QUESTION, "answer": "Unauthorized prewritten answer."})
        message = FOLLOWUP_QUESTION if dispatch.get("visible_dialogue") else FIRST_QUESTION
        return json.dumps({"message": message}, ensure_ascii=False)


class DirectorResponder:
    model = "offline-source-aware-director-answerer"

    def __init__(self, *, qa_type="closed_book", reject_question=False,
                 extra_question=False, fail_answers=False, followup_policy=None,
                 initial_brief=None, followup_brief=None, source_text=SOURCE, source_quote=QUOTE,
                 reject_brief=False, brief_parser_error=False, brief_timeout=False,
                 public_fact_in_answer=False, hidden_reasoning=False):
        self.calls, self.usage = [], {"calls": 0}
        self.qa_type, self.reject_question = qa_type, reject_question
        self.extra_question, self.fail_answers = extra_question, fail_answers
        self.followup_policy = followup_policy
        self.initial_brief, self.followup_brief = initial_brief, followup_brief
        self.source_text, self.source_quote = source_text, source_quote
        self.reject_brief, self.brief_parser_error, self.brief_timeout = reject_brief, brief_parser_error, brief_timeout
        self.public_fact_in_answer, self.hidden_reasoning = public_fact_in_answer, hidden_reasoning
        self.plans, self.fits, self.answers, self.steps = [], [], [], []
        self.brief_checks = []

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        data = task_payload(messages)
        if data and "candidates" in data:
            self.plans.append(deepcopy(messages))
            tasks = []
            source_materials = materials(messages)
            for candidate in data["candidates"]:
                identity = candidate.get("source_line_material_id") or candidate["source_line_anchor"]["material_id"]
                material = next(item for item in source_materials if item.get("material_id") == identity)
                assert material["text"] == self.source_text
                task = {"id": candidate["id"], "qa_type": self.qa_type,
                        "question": "内部设计占位：不可作为真实无线索用户问题。" if self.qa_type == "closed_book"
                            else DIRECTOR_QUESTION,
                        "visible_context": "" if self.qa_type == "closed_book" else self.source_quote,
                        "answer_policy": "answer", "guidance": "协助完成实际操作卡目标。",
                        "evidence_quotes": [self.source_quote], "dialogue_design": design(),
                        "source_line_ranges": [{"material_id": identity, "start_line": 1, "end_line": 2}]}
                if self.qa_type == "closed_book":
                    task["question_brief"] = deepcopy(self.initial_brief) if self.initial_brief is not None else brief()
                tasks.append(task)
            return json.dumps({"tasks": tasks}, ensure_ascii=False)
        if data and "question_brief" in data and "qa_contract" in data and "question" not in data:
            self.brief_checks.append(deepcopy(messages))
            assert tool_payload(messages, "source_memory") is not None
            if self.brief_timeout:
                raise ModelRequestError("service_timeout", "transient", 1)
            if self.brief_parser_error:
                return "this is not valid brief assessment JSON"
            return json.dumps({"keep": not self.reject_brief,
                "reason": "只有宽泛主题与实际可见历史，没有隐藏资料事实。" if not self.reject_brief
                    else "PRIVATE_BRIEF_REVIEW 资料中的断电事实被改写放进简报。"}, ensure_ascii=False)
        if data and "question_brief" in data and "question" in data and "qa_contract" in data:
            self.fits.append(deepcopy(messages))
            assert tool_payload(messages, "source_memory") is not None
            assert data["question"] in {FIRST_QUESTION, FOLLOWUP_QUESTION}
            policy = self.followup_policy if data["question"] == FOLLOWUP_QUESTION and self.followup_policy else "answer"
            return json.dumps({"keep": not self.reject_question, "answer_policy": policy,
                "reason": "所选知识块能支持这个实际请求。" if not self.reject_question
                    else "不适配的内部原因包含断电资料，不得发送给提问者。"}, ensure_ascii=False)
        if data and data.get("request_phase") == "next_turn":
            self.steps.append(deepcopy(messages))
            completed = data["completed_turns"]
            if completed >= 2:
                return json.dumps({"continue": False, "user_message": "", "reason": "实际修改已经完成。",
                    "dialogue_state": {"progress": "卡片已经完成必要修改。"}}, ensure_ascii=False)
            return json.dumps({"continue": True,
                "user_message": "内部续轮占位：不可冒充独立发问者。",
                "reason": "实际卡片已经给出，可以按真实成果继续修改。",
                "dialogue_state": {"progress": "初版已完成，待调整表达。",
                    "shared_understanding": [data["dialogue_messages"][-1]["content"]],
                    "open_issues": ["调整卡片表达。"]},
                "question_brief": deepcopy(self.followup_brief) if self.followup_brief is not None
                    else brief(followup=True)}, ensure_ascii=False)
        dispatch = tool_payload(messages, "dialogue_dispatch")
        if dispatch is not None and "qa_type" in dispatch:
            self.answers.append(deepcopy(messages))
            if self.fail_answers:
                raise RuntimeError("offline interruption after question checkpoint")
            actual = messages[-1]
            assert actual["role"] == "user"
            assert "内部设计占位" not in actual["content"]
            assert "内部续轮占位" not in actual["content"]
            answer = FOLLOWUP_ANSWER if actual["content"] == FOLLOWUP_QUESTION else FIRST_ANSWER
            if self.public_fact_in_answer and actual["content"] != FOLLOWUP_QUESTION:
                answer += self.source_quote
            if actual["content"] == FOLLOWUP_QUESTION and dispatch["answer_policy"] == "conditional":
                answer = "如果沿用刚才的卡片，第二步写“检查设备”，记录项保留检查结果。"
            response = {"answer": answer, "reasoning": HIDDEN_REASONING if self.hidden_reasoning
                else "操作顺序与检查前的必要条件一致。", "quotes": [self.source_quote]}
            if self.extra_question:
                response["question"] = "回答模型擅自替换的新问题。"
            return json.dumps(response, ensure_ascii=False)
        raise AssertionError(f"unexpected offline request: {list(data or {})}")


class Reviewer:
    model = "offline-independent-reviewer"

    def __init__(self):
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        if '"adherence"' in messages[0]["content"]:
            return json.dumps({"keep": True, "adherence": 5, "reason": "已按实际可见对话核对。"})
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True,
            "correctness": 5, "scores": {key: 5 for key in
                ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "已独立核对事实与实际交往行动。"})


def make_run(tmp_path, *, writer=None, questioner=None, targets=None, **changes):
    writer, questioner, reviewer = writer or DirectorResponder(), questioner or Questioner(), Reviewer()
    source = tmp_path / "source.txt"
    source.write_text(writer.source_text, encoding="utf-8")
    output = tmp_path / "out"
    options = changes.pop("qa_director", config(writer.qa_type))
    run_id = engine.create_run(output, sources=[source], targets=targets or ["sft"],
        qa_director=options, **changes)
    workflow = engine.Workflow(output, run_id, tmp_path, generator=writer, questioner=questioner, jev=reviewer)
    return workflow, writer, questioner, reviewer


def run(tmp_path, **kwargs):
    workflow, writer, questioner, reviewer = make_run(tmp_path, **kwargs)
    state = workflow.execute()
    return state, workflow.path, writer, questioner, reviewer


def records(path, target="sft"):
    return engine.read_json(path / f"artifacts/{target}.records.json")


def test_balanced_separated_document_answers_require_source_quotes_before_review(tmp_path):
    class NoQuoteWriter(DirectorResponder):
        def chat(self, messages, **kwargs):
            value = super().chat(messages, **kwargs)
            dispatch = tool_payload(messages, "dialogue_dispatch")
            if dispatch is not None and "qa_type" in dispatch:
                candidate = json.loads(value)
                candidate["quotes"] = []
                return json.dumps(candidate, ensure_ascii=False)
            return value

    state, path, writer, _, reviewer = run(tmp_path, writer=NoQuoteWriter(),
        targets=["multiturn"], conversation_turns=2,
        qa_director=config(planning_mode="balanced"))
    assert state["status"] == "needs_attention", state.get("error")
    assert state["quality"]["targets"]["multiturn"]["eligible"] == 0
    row = records(path, "multiturn")[0]
    assert row["reason"] == "multiturn_turn_failed_after_repair"
    assert row["failed_turn"] == 1
    assert len(writer.answers) == 2
    # A permissive model judge must not bypass the deterministic evidence
    # requirement merely because later turns use the separate questioner.
    assert not reviewer.calls


def test_balanced_followup_does_not_reuse_answer_to_a_legacy_generated_question(tmp_path):
    workflow, writer, _, _ = make_run(tmp_path, targets=["multiturn"], conversation_turns=2,
        qa_director=config(planning_mode="balanced"))
    original_multiturn = workflow.multiturn

    def with_old_answer(unit):
        # A previously interrupted run answered a different second question
        # through the old source-aware user generator.
        workflow.checkpoint(["call", [unit["id"], "multiturn_answer", 1, 0]],
            lambda: {"answer": "STALE_ANSWER_FOR_A_DIFFERENT_QUESTION", "quotes": [QUOTE]})
        return original_multiturn(unit)

    workflow.multiturn = with_old_answer
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    row = records(workflow.path, "multiturn")[0]
    assert row["messages"][2]["content"] == FOLLOWUP_QUESTION
    assert len(writer.answers) == 2
    assert "STALE_ANSWER" not in json.dumps(row, ensure_ascii=False)


def test_closed_book_user_is_authored_by_blind_questioner_then_answered_separately(tmp_path):
    state, path, writer, questioner, _ = run(tmp_path)
    assert state["status"] == "completed", state.get("error")
    assert engine.read_json(path / "recipe.json")["version"] == 20
    row = records(path)[0]
    assert row["messages"][0] == {"role": "user", "content": FIRST_QUESTION}
    assert row["messages"][1]["content"] == FIRST_ANSWER
    assert len(questioner.calls) == len(writer.plans) == len(writer.fits) == len(writer.answers) == 1
    assert len(writer.brief_checks) == 1
    assert materials(writer.brief_checks[0]) == materials(writer.answers[0])
    assert writer.answers[0][-1] == {"role": "user", "content": FIRST_QUESTION}
    assert "内部设计占位" not in json.dumps(row["messages"], ensure_ascii=False)
    assert SOURCE not in json.dumps(row["messages"], ensure_ascii=False)


def test_grounded_user_comes_from_director_without_blind_questioner_call(tmp_path):
    state, path, writer, questioner, _ = run(tmp_path, writer=DirectorResponder(qa_type="grounded"))
    assert state["status"] == "completed", state.get("error")
    assert not questioner.calls and not writer.fits
    row = records(path)[0]
    assert row["qa_contract"]["question"] == DIRECTOR_QUESTION
    assert DIRECTOR_QUESTION in row["messages"][0]["content"]
    assert QUOTE in row["messages"][0]["content"]
    assert writer.answers[0][-1] == row["messages"][0]
    assert "盆栽" not in json.dumps(row["messages"], ensure_ascii=False)


def test_source_and_dispatch_are_distinct_completed_tools_not_user_envelopes(tmp_path):
    state, _, writer, questioner, _ = run(tmp_path)
    assert state["status"] == "completed", state.get("error")
    messages = writer.answers[0]
    tools = {message["name"]: message for message in messages if message["role"] == "tool"}
    assert set(tools) == {"source_memory", "dialogue_dispatch"}
    source = tools["source_memory"]
    dispatch = tools["dialogue_dispatch"]
    assert QUOTE in source["content"] and "盆栽" not in source["content"]
    assert "qa_type" in dispatch["content"] and QUOTE not in dispatch["content"]
    for result in tools.values():
        call = next(call for message in messages if message["role"] == "assistant"
                    for call in message.get("tool_calls", []) if call["id"] == result["tool_call_id"])
        assert call["function"]["name"] == result["name"]
    for message in messages:
        if message["role"] == "user":
            assert QUOTE not in message["content"]
            assert "qa_contract" not in message["content"]
            assert "source_line_ranges" not in message["content"]
    initial_question_request = questioner.calls[0]
    dispatch = tool_payload(initial_question_request, "dialogue_dispatch")
    assert set(dispatch) == {"question_brief"}
    assert dispatch["question_brief"] == brief()
    assert QUOTE not in json.dumps(initial_question_request, ensure_ascii=False)
    assert "盆栽" not in json.dumps(initial_question_request, ensure_ascii=False)


def test_partial_question_keeps_only_planned_clues_while_answerer_has_complete_selected_evidence(tmp_path):
    state, path, writer, questioner, _ = run(tmp_path, writer=DirectorResponder(qa_type="partial"))
    assert state["status"] == "completed", state.get("error")
    row = records(path)[0]
    assert row["qa_contract"]["visible_context"] == QUOTE
    assert QUOTE in row["messages"][0]["content"]
    assert "结束后记录检查结果。" not in row["messages"][0]["content"]
    teacher = materials(writer.answers[0])
    assert teacher[0]["text"] == QUOTE + "\n结束后记录检查结果。\n"
    assert writer.answers[0][-1] == row["messages"][0]
    assert not questioner.calls


def test_answerer_cannot_add_a_new_question_field_or_rewrite_user(tmp_path):
    state, path, writer, questioner, _ = run(tmp_path, writer=DirectorResponder(extra_question=True))
    assert state["status"] == "needs_attention", state.get("error")
    rows = records(path)
    assert all(row["status"] != "eligible" for row in rows)
    assert len(writer.answers) == 2 and len(questioner.calls) == 1
    assert all(call[-1]["content"] == FIRST_QUESTION for call in writer.answers)
    assert (path / "artifacts/sft.jsonl").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("failure", ["invalid", "parser_error"])
def test_invalid_question_is_quarantined_without_answer_or_saturation(tmp_path, failure):
    state, path, writer, _, _ = run(tmp_path, questioner=Questioner(**{failure: True}))
    assert state["status"] == "needs_attention", state.get("error")
    assert not writer.answers and not writer.fits
    rows = records(path)
    assert rows and all(row["status"] != "eligible" for row in rows)
    assert all("saturat" not in row.get("reason", "") for row in rows)
    assert "saturat" not in state.get("production", {}).get("stop_reason", "")
    assert (path / "artifacts/sft.jsonl").read_text(encoding="utf-8") == ""


def test_unsuitable_question_retries_without_leaking_source_aware_feedback(tmp_path):
    state, path, writer, questioner, _ = run(tmp_path, writer=DirectorResponder(reject_question=True))
    assert state["status"] == "needs_attention", state.get("error")
    assert not writer.answers and len(writer.fits) == len(questioner.calls) == 2
    retry = tool_payload(questioner.calls[1], "dialogue_dispatch")
    assert retry["retry_code"] == "question_not_suitable"
    assert "不适配的内部原因" not in json.dumps(questioner.calls, ensure_ascii=False)
    assert all(row["status"] != "eligible" for row in records(path))
    assert "saturat" not in state.get("production", {}).get("stop_reason", "")


def test_provider_failure_pauses_production_as_failure_not_knowledge_exhaustion(tmp_path):
    state, path, writer, _, _ = run(tmp_path, questioner=Questioner(api_error=True),
        production={"goals": {"sft": 3}, "round_size": 1, "max_attempts": 3,
                    "min_acceptance_rate": 0})
    assert state["status"] == "failed", state.get("error")
    assert state["production"]["status"] == "paused"
    assert state["production"]["stop_reason"] == "service_authentication_failed"
    assert not writer.answers
    assert not (path / "artifacts/sft.jsonl").exists() or not (path / "artifacts/sft.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("failure", ["invalid", "parser_error"])
def test_broken_question_outputs_do_not_count_as_quality_saturation(tmp_path, failure):
    from lib.infrastructure.workflow_production import _quality_assessment_valid

    state, path, writer, _, _ = run(tmp_path, questioner=Questioner(**{failure: True}),
        production={"version": 2, "quantity_policy": "quality_first", "quality_stop_version": 1,
                    "goals": {"sft": 3}, "round_size": 1, "max_attempts": 3,
                    "max_rounds": 3, "min_acceptance_rate": 1, "low_acceptance_rounds": 1})
    assert not writer.answers
    assert state["production"]["stop_reason"] not in {"quality_saturation", "director_saturation",
        "low_acceptance_rate", "source_exhausted"}
    receipts = [engine.read_json(item) for item in (path / "production").glob("round-*/receipt.json")]
    assert receipts
    assert all(not receipt["quality_assessments"]["sft"]["valid_for_saturation"] for receipt in receipts)
    for item in (path / "production").glob("round-*/sft.jsonl"):
        for line in item.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            assert row["status"] != "eligible"
            assert row["generation_failure_kind"] == "invalid_output"
            assert not _quality_assessment_valid(row)


def test_resume_uses_checkpointed_question_and_fit_without_reauthoring(tmp_path):
    writer = DirectorResponder(fail_answers=True)
    workflow, writer, questioner, reviewer = make_run(tmp_path, writer=writer)
    state = workflow.execute()
    assert state["status"] == "failed", state.get("error")
    assert len(questioner.calls) == len(writer.fits) == len(writer.plans) == 1
    counts = len(questioner.calls), len(writer.fits), len(writer.plans), len(writer.brief_checks)
    writer.fail_answers = False
    state = engine.resume(tmp_path / "out", workflow.path.name, tmp_path,
        generator=writer, questioner=questioner, jev=reviewer)
    assert state["status"] == "completed", state.get("error")
    assert (len(questioner.calls), len(writer.fits), len(writer.plans), len(writer.brief_checks)) == counts
    assert records(workflow.path)[0]["messages"][0]["content"] == FIRST_QUESTION


def test_multiturn_closed_followup_comes_from_blind_questioner_actual_visible_history(tmp_path):
    state, path, writer, questioner, _ = run(tmp_path, targets=["multiturn"], conversation_turns=6)
    assert state["status"] == "completed", state.get("error")
    row = records(path, "multiturn")[0]
    assert row["dialogue_end_reason"] == "natural_completion"
    assert [message["content"] for message in row["messages"]] == [
        FIRST_QUESTION, FIRST_ANSWER, FOLLOWUP_QUESTION, FOLLOWUP_ANSWER]
    assert len(questioner.calls) == len(writer.fits) == len(writer.answers) == 2
    initial = tool_payload(questioner.calls[0], "dialogue_dispatch")
    followup = tool_payload(questioner.calls[1], "dialogue_dispatch")
    assert "visible_dialogue" not in initial
    assert followup["visible_dialogue"] == public_messages(row["messages"][:2])
    assert all(tool_payload(call, "source_memory") is None for call in questioner.calls)
    assert all("盆栽" not in json.dumps(call, ensure_ascii=False) for call in questioner.calls)
    assert writer.answers[1][-3:] == public_messages(row["messages"][:3])
    assert writer.answers[1][-1]["content"] == FOLLOWUP_QUESTION
    assert "内部续轮占位" not in json.dumps(row["messages"], ensure_ascii=False)


def test_balanced_multiturn_without_design_keeps_blind_questioner_for_every_turn(tmp_path):
    class BalancedDirector(DirectorResponder):
        def chat(self, messages, **kwargs):
            response = super().chat(messages, **kwargs)
            data = task_payload(messages)
            if data and "candidates" in data:
                planned = json.loads(response)
                for task in planned["tasks"]:
                    task.pop("dialogue_design")
                return json.dumps(planned, ensure_ascii=False)
            return response

    state, path, writer, questioner, _ = run(tmp_path, writer=BalancedDirector(),
        targets=["multiturn"], conversation_turns=6,
        qa_director=config(planning_mode="balanced"))
    assert state["status"] == "completed", state.get("error")
    row = records(path, "multiturn")[0]
    assert "dialogue_design" not in row["qa_contract"]
    assert [message["content"] for message in row["messages"]] == [
        FIRST_QUESTION, FIRST_ANSWER, FOLLOWUP_QUESTION, FOLLOWUP_ANSWER]
    assert row["dialogue_end_reason"] == "natural_completion"
    assert len(questioner.calls) == len(writer.fits) == len(writer.answers) == 2
    assert all(tool_payload(call, "source_memory") is None for call in questioner.calls)
    assert tool_payload(questioner.calls[1], "dialogue_dispatch")["visible_dialogue"] == public_messages(row["messages"][:2])
    assert all("盆栽" not in json.dumps(call, ensure_ascii=False) for call in questioner.calls)


def test_followup_fit_policy_applies_to_current_answer_without_overwriting_original_contract(tmp_path):
    state, path, writer, _, _ = run(tmp_path, targets=["multiturn"], conversation_turns=6,
        writer=DirectorResponder(followup_policy="conditional"))
    assert state["status"] == "completed", state.get("error")
    dispatch = tool_payload(writer.answers[1], "dialogue_dispatch")
    assert dispatch["answer_policy"] == "conditional"
    row = records(path, "multiturn")[0]
    assert row["qa_contract"]["answer_policy"] == "answer"
    assert any(step.get("response_policy") == "conditional" for step in row["dialogue_steps"])
    assert row["messages"][-1]["content"].startswith("如果沿用刚才的卡片")


def test_short_complete_private_fact_is_blocked_before_any_question_request(tmp_path):
    private_fact = "阈值是20伏。"
    writer = DirectorResponder(source_text=private_fact + "\n设备检查结束后需要记录结果和完成交接。",
        source_quote=private_fact, initial_brief={"topic": private_fact,
            "communication_goal": "请求解释上述阈值的作用。"})
    state, path, writer, questioner, _ = run(tmp_path, writer=writer)
    assert state["status"] == "needs_attention", state.get("error")
    assert not questioner.calls and not writer.answers
    assert all(row["status"] != "eligible" for row in records(path))
    assert (path / "artifacts/sft.jsonl").read_text(encoding="utf-8") == ""


def test_paraphrased_private_fact_is_rejected_by_preflight_before_questioner_sees_it(tmp_path):
    writer = DirectorResponder(initial_brief={"topic": "设备检查",
        "communication_goal": "请围绕必须先切断电源这个具体要求设计问题。"}, reject_brief=True)
    state, path, writer, questioner, _ = run(tmp_path, writer=writer)
    assert state["status"] == "needs_attention", state.get("error")
    assert writer.brief_checks and not questioner.calls and not writer.answers
    rows = records(path)
    assert all(row["status"] != "eligible" for row in rows)
    assert "PRIVATE_BRIEF_REVIEW" not in json.dumps(rows, ensure_ascii=False)
    assert (path / "artifacts/sft.jsonl").read_text(encoding="utf-8") == ""


def test_broken_preflight_assessment_is_operational_invalidity_not_saturation(tmp_path):
    writer = DirectorResponder(brief_parser_error=True)
    state, path, writer, questioner, _ = run(tmp_path, writer=writer,
        production={"version": 2, "quantity_policy": "quality_first", "quality_stop_version": 1,
            "goals": {"sft": 3}, "round_size": 1, "max_attempts": 3, "max_rounds": 3,
            "min_acceptance_rate": 1, "low_acceptance_rounds": 1})
    assert not questioner.calls and not writer.answers and writer.brief_checks
    assert state["production"]["stop_reason"] not in {"quality_saturation", "director_saturation",
        "low_acceptance_rate", "source_exhausted"}
    receipts = [engine.read_json(item) for item in (path / "production").glob("round-*/receipt.json")]
    assert receipts
    assert all(not receipt["quality_assessments"]["sft"]["valid_for_saturation"] for receipt in receipts)


def test_preflight_timeout_pauses_as_service_failure_without_question_or_saturation(tmp_path):
    state, _, writer, questioner, _ = run(tmp_path, writer=DirectorResponder(brief_timeout=True),
        production={"version": 2, "quantity_policy": "quality_first", "quality_stop_version": 1,
            "goals": {"sft": 3}, "round_size": 1, "max_attempts": 3, "max_rounds": 3,
            "min_acceptance_rate": 1, "low_acceptance_rounds": 1})
    assert state["status"] == "failed", state.get("error")
    assert state["production"]["status"] == "paused"
    assert state["production"]["stop_reason"] == "service_timeout"
    assert writer.brief_checks and not questioner.calls and not writer.answers


def test_followup_brief_may_reference_public_fact_but_never_hidden_reasoning(tmp_path):
    writer = DirectorResponder(public_fact_in_answer=True, hidden_reasoning=True,
        followup_brief={"topic": QUOTE, "communication_goal": "就已经实际说明过的条件调整操作卡表达。"})
    state, path, writer, questioner, _ = run(tmp_path, targets=["multiturn"], conversation_turns=6, writer=writer)
    assert state["status"] == "completed", state.get("error")
    assert len(questioner.calls) == len(writer.brief_checks) == 2
    row = records(path, "multiturn")[0]
    assert QUOTE in row["messages"][1]["content"]
    followup = tool_payload(questioner.calls[1], "dialogue_dispatch")
    assert followup["question_brief"]["topic"] == QUOTE
    assert followup["visible_dialogue"] == public_messages(row["messages"][:2])
    assert all(set(message) == {"role", "content"} for message in followup["visible_dialogue"])
    assert HIDDEN_REASONING not in json.dumps(questioner.calls, ensure_ascii=False)
    assert "reasoning_content" not in json.dumps(questioner.calls, ensure_ascii=False)
    assert "盆栽" not in json.dumps(questioner.calls, ensure_ascii=False)


def test_stored_reasoning_metadata_is_removed_before_preflight_and_question_dispatch(tmp_path):
    workflow, writer, questioner, _ = make_run(tmp_path,
        writer=DirectorResponder(public_fact_in_answer=True, hidden_reasoning=True))
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    row = records(workflow.path)[0]
    assert row["messages"][1]["reasoning_content"] == HIDDEN_REASONING
    public_brief = {"topic": QUOTE, "communication_goal": "按照已公开的条件继续修改操作卡。"}
    result = workflow._closed_book_question(row["source_context"], row["qa_contract"],
        key=["direct-public-history-check"], visible_messages=row["messages"], brief=public_brief)
    assert result["question"] == FOLLOWUP_QUESTION
    public = [{"role": item["role"], "content": item["content"]} for item in row["messages"]]
    assert task_payload(writer.brief_checks[-1])["visible_dialogue"] == public
    assert tool_payload(questioner.calls[-1], "dialogue_dispatch")["visible_dialogue"] == public
    assert HIDDEN_REASONING not in json.dumps(writer.brief_checks[-1], ensure_ascii=False)
    assert HIDDEN_REASONING not in json.dumps(questioner.calls[-1], ensure_ascii=False)
