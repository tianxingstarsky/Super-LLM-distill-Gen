"""Offline end-to-end execution of immutable QA contracts and batch feedback."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import gc
from threading import Barrier

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.qa_history import QAHistory


SOURCE = "检查设备前必须先断电。结束后记录检查结果。办公室盆栽每周浇水一次。"


@pytest.fixture
def suspended_gc():
    """A failed worker must release file handles before exception-cycle GC."""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        else:
            gc.disable()
        gc.collect()


def config(qa_type="closed_book", **options):
    return {"enabled": True, "batch_size": 1, "history_limit": 2,
            "type_weights": {key: int(key == qa_type) for key in
                ("closed_book", "grounded", "partial", "multi_source", "distractor")},
            "question_rules": "QUESTION_RULE_SENTINEL 问题必须明确。",
            "answer_rules": "ANSWER_RULE_SENTINEL 回答必须简洁。", **options}


class Writer:
    model = "offline-director-writer"

    def __init__(self, *, policy="answer", duplicate=False, fail_batch=None,
                 malformed=False, invented_context=False, bad_alternative=False):
        self.calls, self.usage = [], {"calls": 0}
        self.policy, self.duplicate, self.fail_batch = policy, duplicate, fail_batch
        self.malformed, self.invented_context = malformed, invented_context
        self.bad_alternative = bad_alternative

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        system, data = messages[0]["content"], json.loads(messages[1]["content"])
        if "candidates" in data:
            if self.fail_batch == data["offset"]:
                raise RuntimeError("offline simulated interruption")
            if self.malformed:
                return json.dumps({"tasks": []})
            tasks = []
            for index, candidate in enumerate(data["candidates"]):
                qa_type = candidate["assigned_type"]
                quote = candidate["teacher_evidence"].split("。")[0] + "。"
                quotes = ([quote, "结束后记录检查结果。"] if qa_type == "multi_source" else [quote])
                visible = "" if qa_type == "closed_book" else "\n\n".join(quotes)
                if qa_type == "distractor":
                    visible += "\n\n办公室盆栽每周浇水一次。"
                if qa_type == "partial" and self.policy != "answer":
                    visible = ""
                if self.invented_context:
                    visible += "\n设备电压必须是999伏。"
                number = 0 if self.duplicate else data["offset"] + index
                tasks.append({"id": candidate["id"], "qa_type": qa_type,
                    "question": f"设备检查任务{number}：操作前应先做什么？",
                    "visible_context": visible, "answer_policy": self.policy,
                    "guidance": "依据断电前提回答，忽略盆栽信息。", "evidence_quotes": quotes})
            return json.dumps({"tasks": tasks}, ensure_ascii=False)
        if "qa_contract" in data and "source_context" in data and "turn" not in data and "messages" not in data:
            contract = data["qa_contract"]
            answer = ("请说明需要检查哪一种设备？" if contract["answer_policy"] == "clarify" else
                      "如果检查对象是所述设备，应先断电。" if contract["answer_policy"] == "conditional" else
                      "缺少设备型号，无法确认具体操作。" if contract["answer_policy"] == "insufficient" else
                      "操作前应先断电。")
            return json.dumps({"question": contract["question"], "answer": answer,
                "reasoning": "先断电可以满足检查的安全前提。", "quotes": ["检查设备前必须先断电。"]}, ensure_ascii=False)
        if "另一个独立" in system:
            return json.dumps({"answer": "BAD 隐藏资料要求先操作再断电。" if self.bad_alternative else "先保持断电。",
                               "reasoning": "操作顺序是安全条件。"})
        if "reference_answer" in data:
            return json.dumps({"reasoning": "先确认安全前提，再执行断电措施。", "answer": "操作前应先断电。"})
        if "original_reasoning" in data:
            return json.dumps({"reasoning": "检查前先断电。"})
        if "自然且可回答的用户提问" in system:
            return json.dumps({"message": "检查结束后如何处理？"})
        if "只回答最新用户消息" in system:
            return json.dumps({"answer": "操作前应先断电。" if len(data["messages"]) == 1 else "结束后记录检查结果。",
                               "quotes": ["检查设备前必须先断电。"]})
        raise AssertionError("unexpected offline writer prompt")


class Reviewer:
    model = "offline-director-reviewer"

    def __init__(self, *, invalid_once=False, reject_policy=False, reject_derived=False,
                 reject_package=False, prefer_bad=False):
        self.calls, self.usage = [], {"calls": 0}
        self.invalid_once, self.reject_policy = invalid_once, reject_policy
        self.reject_derived, self.reject_package, self.prefer_bad = reject_derived, reject_package, prefer_bad

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        if self.invalid_once:
            self.invalid_once = False
            return json.dumps({"keep": "invalid"})
        system = messages[0]["content"]
        data = json.loads(messages[1]["content"])
        if '"adherence"' in system:
            derived = any(text in system for text in ("即将成为 chosen", "待导出的显式推理", "修剪后的"))
            keep = not self.reject_policy and not (self.reject_derived and derived)
            return json.dumps({"keep": keep, "adherence": 5 if keep else 1,
                               "reason": "契约、可见线索和策略已核对。" if keep else "策略或隐藏资料泄漏。"})
        if "BAD" in messages[1]["content"]:
            score = 5 if self.prefer_bad else 1
        elif self.prefer_bad and "alternative" not in system and self.calls:
            score = 3
        else:
            score = 5
        if "最终训练样本" in system and self.reject_package:
            score = 1
        keep = score >= 4
        return json.dumps({"keep": keep, "grounded": keep, "reasoning_valid": keep,
            "correctness": score, "scores": {key: score for key in
                ("correctness", "reasoning", "grounding", "instruction", "safety")}, "reason": "独立核对来源和安全前提。"})


def make_run(tmp_path, *, options=None, **kwargs):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "out"
    run_id = engine.create_run(output, sources=[source], targets=kwargs.pop("targets", ["sft"]),
        qa_director=options or config(), **kwargs)
    return output, run_id, engine.run_path(output, run_id)


def execute(tmp_path, *, writer=None, reviewer=None, options=None, **kwargs):
    output, run_id, path = make_run(tmp_path, options=options, **kwargs)
    writer, reviewer = writer or Writer(), reviewer or Reviewer()
    state = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=reviewer).execute()
    return state, path, output, run_id, writer, reviewer


def test_closed_book_exports_only_question_while_teacher_and_reviewer_get_evidence(tmp_path):
    state, path, output, _, writer, reviewer = execute(tmp_path)
    assert state["status"] == "completed", state.get("error")
    record = json.loads((path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    assert record["messages"][0]["content"] == "设备检查任务0：操作前应先做什么？"
    assert SOURCE not in json.dumps(record, ensure_ascii=False)
    assert SOURCE in writer.calls[1][1]["content"]
    assert SOURCE in reviewer.calls[0][1]["content"]
    assert "QUESTION_RULE_SENTINEL" in writer.calls[1][1]["content"]
    assert "ANSWER_RULE_SENTINEL" in reviewer.calls[-1][1]["content"]
    row = engine.read_json(path / "artifacts/sft.records.json")[0]
    assert row["qa_contract_check"]["keep"] and row["family_id"]
    assert state["stages"]["director"]["done"] == 1
    assert state["quality"]["qa_director"]["coverage"]["closed_book"]["accepted"] == 1
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.coverage(namespace="sft") == {"closed_book": 1}


@pytest.mark.parametrize("qa_type", ["grounded", "multi_source", "distractor"])
def test_clue_types_export_only_planned_literal_context(tmp_path, qa_type):
    state, path, *_ = execute(tmp_path, options=config(qa_type))
    assert state["status"] == "completed", state.get("error")
    record = engine.read_json(path / "artifacts/sft.records.json")[0]
    visible = record["qa_contract"]["visible_context"]
    assert visible in record["messages"][0]["content"]
    assert "检查设备前必须先断电。" in visible
    assert ("结束后记录检查结果。" in visible) == (qa_type == "multi_source")
    assert ("盆栽" in visible) == (qa_type == "distractor")


@pytest.mark.parametrize("policy", ["answer", "clarify", "conditional", "insufficient"])
def test_partial_strategy_is_assigned_and_checked_not_unconditionally_refused(tmp_path, policy):
    state, path, *_ = execute(tmp_path, options=config("partial"), writer=Writer(policy=policy))
    assert state["status"] == "completed", state.get("error")
    row = engine.read_json(path / "artifacts/sft.records.json")[0]
    assert row["qa_contract"]["answer_policy"] == policy
    assert row["qa_contract_check"]["keep"]
    assert ("先断电" in row["messages"][-1]["content"]) == (policy in {"answer", "conditional"})


def test_invalid_or_invented_contract_fails_before_any_generation(tmp_path):
    writer = Writer(invented_context=True)
    state, path, _, _, writer, _ = execute(tmp_path, options=config("grounded"), writer=writer)
    assert state["status"] == "failed"
    assert state["error"] == "qa_director_visible_context_not_in_source"
    assert len(writer.calls) == 1
    assert not (path / "artifacts/sft.jsonl").exists()


def test_next_batch_gets_accepted_feedback_bounded_history_and_fixed_rules(tmp_path):
    state, path, _, _, writer, _ = execute(tmp_path, sample_count=3)
    assert state["status"] == "completed", state.get("error")
    plans = [json.loads(call[1]["content"]) for call in writer.calls if "candidates" in json.loads(call[1]["content"])]
    assert len(plans) == 3
    assert plans[0]["coverage"]["closed_book"]["accepted"] == 0
    assert plans[1]["coverage"]["closed_book"]["accepted"] == 1
    assert plans[2]["coverage"]["closed_book"]["accepted"] == 2
    assert 0 < len(plans[1]["history"]) <= 2
    assert all(plan["history_is_not_evidence"] for plan in plans)
    assert engine.read_json(path / "artifacts/quality.json")["qa_director"]["batches_planned"] == 3


def test_duplicate_contract_registered_before_same_batch_model_calls(tmp_path):
    state, path, _, _, writer, _ = execute(tmp_path, sample_count=3,
        options=config(batch_size=3), writer=Writer(duplicate=True), concurrency=3)
    assert state["status"] == "completed", state.get("error")
    rows = engine.read_json(path / "artifacts/sft.records.json")
    assert sum(row["status"] == "eligible" for row in rows) == 1
    assert sum(row.get("reason") == "duplicate_qa_contract_in_batch" for row in rows) == 2
    assert len(writer.calls) == 2  # one director batch and one worker only


def test_duplicate_previous_run_is_quarantined_without_worker_call(tmp_path):
    first = execute(tmp_path)
    assert first[0]["status"] == "completed"
    second = execute(tmp_path)
    state, path, _, _, writer, _ = second
    assert state["status"] == "needs_attention", state.get("error")
    assert len(writer.calls) == 1
    assert engine.read_json(path / "artifacts/sft.records.json")[0]["reason"] == "duplicate_qa_contract"
    assert (path / "artifacts/sft.jsonl").read_text(encoding="utf-8") == ""


def test_resume_keeps_planned_input_feedback_and_cached_sample(tmp_path):
    writer = Writer(fail_batch=1)
    state, path, output, run_id, _, _ = execute(tmp_path, writer=writer, sample_count=2)
    assert state["status"] == "failed"
    failed_input = json.loads(writer.calls[-1][1]["content"])
    assert failed_input["coverage"]["closed_book"]["accepted"] == 1
    writer.fail_batch = None
    before = len(writer.calls)
    state = engine.resume(output, run_id, tmp_path, generator=writer, jev=Reviewer())
    assert state["status"] == "completed", state.get("error")
    assert len(writer.calls) == before + 2
    assert json.loads(writer.calls[before][1]["content"]) == failed_input
    assert state["stages"]["sft"]["done"] == 2


def test_custom_node_prompts_are_pinned_and_reach_their_actual_calls(tmp_path):
    prompts = {}
    for stage, prompt, sentinel in (
            ("director", "workflow.qa_director", "CUSTOM_DIRECTOR_SENTINEL"),
            ("sft", "workflow.sft_directed", "CUSTOM_WORKER_SENTINEL"),
            ("sft", "workflow.sft_directed_check", "CUSTOM_CHECK_SENTINEL")):
        prompts.setdefault(stage, {})[prompt] = engine.render(engine.get(prompt)) + " " + sentinel
    state, path, _, _, writer, reviewer = execute(tmp_path, node_prompts=prompts)
    assert state["status"] == "completed", state.get("error")
    assert "CUSTOM_DIRECTOR_SENTINEL" in writer.calls[0][0]["content"]
    assert "CUSTOM_WORKER_SENTINEL" in writer.calls[1][0]["content"]
    assert "CUSTOM_CHECK_SENTINEL" in reviewer.calls[-1][0]["content"]
    assert engine.read_json(path / "recipe.json")["node_prompts"] == prompts


def test_multiturn_uses_exact_first_question_and_keeps_hidden_source_out_of_inputs(tmp_path):
    state, path, _, _, writer, _ = execute(tmp_path, targets=["multiturn"], conversation_turns=2)
    assert state["status"] == "completed", state.get("error")
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["messages"][0]["content"] == row["qa_contract"]["question"]
    assert SOURCE not in " ".join(message["content"] for message in row["messages"] if message["role"] == "user")
    assert all(review["qa_contract_check"]["keep"] for review in row["turn_reviews"])
    with QAHistory(path.parent.parent / "qa-history.sqlite3") as history:
        assert history.recent(namespace="multiturn")[0]["answer"] == row["messages"][1]["content"]


def test_failed_contract_review_quarantines_candidate_and_never_publishes_history(tmp_path):
    state, path, output, *_ = execute(tmp_path, reviewer=Reviewer(reject_policy=True))
    assert state["status"] == "needs_attention", state.get("error")
    row = engine.read_json(path / "artifacts/sft.records.json")[0]
    assert row["reason"] == "directed_sft_quality_failed_after_repair"
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.coverage(namespace="sft") == {}


def test_package_rejected_answer_not_added_to_published_history(tmp_path):
    state, path, output, *_ = execute(tmp_path, reviewer=Reviewer(reject_package=True),
        package_review={"enabled": True, "mode": "all"})
    assert state["status"] == "needs_attention", state.get("error")
    assert engine.read_json(path / "artifacts/sft.records.json")[0]["reason"] == "package_ai_review_rejected"
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.coverage(namespace="sft") == {}


@pytest.mark.usefixtures("suspended_gc")
def test_dispatch_is_frozen_before_judge_failure_and_published_history_change(tmp_path):
    state, path, output, run_id, writer, _ = execute(tmp_path, reviewer=Reviewer(invalid_once=True))
    assert state["status"] == "failed"
    contracts = list((path / "checkpoints/director").glob("*.json"))
    contract = next(value for checkpoint in contracts
        if isinstance((value := engine.read_json(checkpoint)["data"]), dict)
        and any(isinstance(item, dict) and "qa_type" in item for item in value.values()))
    task = next(iter(contract.values()))
    with QAHistory(output / "qa-history.sqlite3") as history:
        history.add({"id": "another-run:sample", "run_id": "another-run", "status": "eligible",
            "question": task["question"], "answer": "先断电。", "qa_type": task["qa_type"],
            "visible_context": task["visible_context"], "answer_policy": task["answer_policy"]}, namespace="sft")
    before = len(writer.calls)
    state = engine.resume(output, run_id, tmp_path, generator=writer, jev=Reviewer())
    assert state["status"] == "needs_attention", state.get("error")
    assert len(writer.calls) == before  # fixed dispatch reuses the paid generation call
    stage = list(engine.WorkflowRows(path / "stage-results/sft.jsonl", 1))[0]
    assert stage["status"] == "eligible"
    assert engine.read_json(path / "artifacts/sft.records.json")[0]["reason"] == "released_qa_contract_duplicate"


def test_concurrent_runs_serialize_final_duplicate_check_and_publication(tmp_path):
    output, first, first_path = make_run(tmp_path)
    _, second, second_path = make_run(tmp_path)
    barrier = Barrier(2)
    class RacingWorkflow(engine.Workflow):
        def package(self, collections):
            barrier.wait(timeout=10)
            return super().package(collections)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(RacingWorkflow(output, run_id, tmp_path,
            generator=Writer(), jev=Reviewer()).execute) for run_id in (first, second)]
        states = [future.result(timeout=20) for future in futures]
    assert sorted(state["status"] for state in states) == ["completed", "needs_attention"]
    rows = [engine.read_json(path / "artifacts/sft.records.json")[0] for path in (first_path, second_path)]
    assert sum(row["status"] == "eligible" for row in rows) == 1
    assert sum(row.get("reason") == "released_qa_contract_duplicate" for row in rows) == 1
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.coverage(namespace="sft") == {"closed_book": 1}


def test_preference_negative_can_be_wrong_while_verified_chosen_contract_is_kept(tmp_path):
    state, path, output, *_ = execute(tmp_path, targets=["dpo"], writer=Writer(bad_alternative=True))
    assert state["status"] == "completed", state.get("error")
    row = engine.read_json(path / "artifacts/dpo.records.json")[0]
    assert row["qa_contract_check"]["keep"]
    assert "BAD" in row["rejected"][0]["content"]
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.recent(namespace="sft")[0]["answer"] == row["chosen"][0]["content"]


@pytest.mark.parametrize("target,options,reason", [
    ("dpo", {}, "preference_qa_contract_rejected"),
    ("cot", {"node_generation": {"cot": {"enabled": True, "style": "structured"}}},
     "cot_generation_failed_after_repair"),
    ("sft", {"reasoning_trim": {"enabled": True, "template": "leakage"}},
     "reasoning_trim_failed_after_repair"),
])
def test_derived_and_trimmed_content_must_pass_the_inherited_contract(tmp_path, target, options, reason):
    state, path, output, *_ = execute(tmp_path, targets=[target], writer=Writer(bad_alternative=True),
        reviewer=Reviewer(reject_derived=True), **options)
    assert state["status"] == "needs_attention", state.get("error")
    row = engine.read_json(path / f"artifacts/{target}.records.json")[0]
    assert row["reason"] == reason and row["qa_contract"] and row["family_id"]
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.coverage(namespace="sft") == {}


def test_director_reserves_output_and_rule_bytes_before_sending_large_chinese_batch(tmp_path):
    source = tmp_path / "long.txt"
    source.write_text(SOURCE * 2000, encoding="utf-8")
    output = tmp_path / "out"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], sample_count=20,
        qa_director=config(batch_size=20, question_rules="问题规则。" * 2000, answer_rules="回答规则。" * 2000))
    writer = Writer(fail_batch=0)
    state = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=Reviewer()).execute()
    assert state["status"] == "failed"  # simulated provider stop after request preflight
    assert len(writer.calls) == 1
    data = json.loads(writer.calls[0][1]["content"])
    assert len(data["candidates"]) == 20
    assert sum(len(message["content"].encode("utf-8")) for message in writer.calls[0]) + 32768 <= 131072
    assert all(0 < len(candidate["teacher_evidence"]) < 2000 for candidate in data["candidates"])
