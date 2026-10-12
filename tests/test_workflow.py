"""Exercise durable execution, quality failures, and input/output isolation offline."""
from __future__ import annotations

import json
from pathlib import Path

from filelock import FileLock, Timeout
import pytest

from lib.workflow import Workflow, cancel, create_run, list_runs, read_json, resume, run_path, verify_artifacts
from lib.workflow_quality import conversation_issue, verdict


TEXT = "设备启动前必须检查电源连接。发现故障时应先断电，再检查线路。维护结束后记录检查结果。"


@pytest.fixture(autouse=True)
def mock_llm_server():
    """All requests in this module use injected clients; no TCP mock is needed."""
    yield


class Generator:
    model = "generator-test"

    def __init__(self, hook=None):
        self.calls = []
        self.hook = hook
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        self.usage["calls"] += 1
        instruction = messages[0]["content"]
        # Source memory may precede the request as tool messages.
        data = json.loads(messages[-1]["content"])
        if self.hook:
            self.hook()
        if "规划" in instruction:
            return json.dumps({"tasks": [f"解释维护操作 {i}" for i in range(data["count"])]})
        if "知识训练语料" in instruction:
            return json.dumps({"text": "GOOD 维护前应检查电源。"})
        if "另一个独立" in instruction:
            return json.dumps({"answer": "BAD 不用断电即可检查线路。", "reasoning": "没有必要进行检查。"})
        return json.dumps({"question": "设备故障时应如何检查？", "answer": "GOOD 应先断电，再检查线路。",
                           "reasoning": "原文规定先断电，因此线路检查必须在断电之后。", "quotes": [TEXT]})


class Judge:
    model = "judge-test"

    def __init__(self, invalid=False):
        self.calls = 0
        self.invalid = invalid
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.usage["calls"] += 1
        if self.invalid:
            return json.dumps({"correctness": "unknown"})
        bad = "BAD" in messages[-1]["content"]
        score = 1 if bad else 5
        return json.dumps({"keep": not bad, "grounded": not bad, "reasoning_valid": not bad,
                           "correctness": score,
                           "scores": {"correctness": score, "reasoning": score, "grounding": score,
                                      "instruction": score, "safety": score},
                           "reason": "应先断电再检查；带电操作不符合来源要求。"})


def make_run(tmp_path, **kwargs):
    source = tmp_path / "source.txt"
    source.write_text(TEXT, encoding="utf-8")
    output = tmp_path / "output"
    rid = create_run(output, sources=[source], **kwargs)
    return output, rid, source


def execute(output, rid, generator=None, judge=None):
    return Workflow(output, rid, output.parent, generator=generator or Generator(), jev=judge or Judge()).execute()


def test_document_all_targets_complete_with_evidence_and_training_schemas(tmp_path):
    out, rid, source = make_run(tmp_path)
    gen, judge = Generator(), Judge()
    state = execute(out, rid, gen, judge)
    assert state["status"] == "completed"
    assert state["usage"]["generation"]["calls"] == 2
    assert state["usage"]["jev"]["calls"] == 2  # SFT answer + alternative; the accepted SFT score is reused
    manifest = verify_artifacts(run_path(out, rid))
    assert manifest["counts"] == {"cpt": 1, "sft": 1, "dpo": 1}
    folder = run_path(out, rid) / "artifacts"
    pair = json.loads((folder / "dpo.jsonl").read_text(encoding="utf-8"))
    assert set(pair) == {"prompt", "chosen", "rejected"}
    assert pair["prompt"][0]["role"] == "user"
    evidence = read_json(folder / "dpo.records.json")[0]
    assert evidence["preference"]["chosen"]["correctness"] == 5
    assert evidence["source_id"] == manifest["sources"][0]["sha256"]
    assert read_json(folder / "sft.records.json")[0]["quotes"] == [TEXT]
    sft = json.loads((folder / "sft.jsonl").read_text(encoding="utf-8"))
    assert TEXT in sft["messages"][0]["content"]
    trl_sft = json.loads((folder / "trl_sft.jsonl").read_text(encoding="utf-8"))
    trl_dpo = json.loads((folder / "trl_dpo.jsonl").read_text(encoding="utf-8"))
    assert trl_sft["messages"][-1]["role"] == "assistant"
    assert set(trl_dpo) == {"prompt", "chosen", "rejected"}
    assert manifest["trainer_counts"] == {"sft": 1, "dpo": 1}
    quality = read_json(folder / "quality.json")
    assert quality["trainer_exports"]["sft"]["status"] == "ready"
    assert quality["trainer_exports"]["dpo"]["summary"]["incompatible"] == 0
    assert source.read_text(encoding="utf-8") == TEXT
    assert not (out / "rollout_samples.jsonl").exists()
    assert list_runs(out)[0]["id"] == rid


def test_provider_quota_error_keeps_safe_code_and_existing_sources_for_resume(tmp_path):
    from lib.model_request_reliability import ModelRequestError

    out, rid, source = make_run(tmp_path, targets=["sft"])
    generator = Generator()
    def quota_error():
        raise ModelRequestError("service_quota_exhausted", "fatal", 1)
    generator.hook = quota_error
    state = execute(out, rid, generator)
    assert state["status"] == "failed"
    assert state["error"] == "service_quota_exhausted"
    assert state["stages"]["ingest"]["status"] == "completed"
    assert state["stages"]["sft"]["quarantined"] == 0
    generator.hook = None
    restored = Workflow(out, rid, tmp_path, generator=generator, jev=Judge()).execute(resume_run=True)
    assert restored["status"] == "completed"
    assert source.read_text(encoding="utf-8") == TEXT
    assert verify_artifacts(run_path(out, rid))["counts"]["sft"] == 1


def test_only_verified_completed_sft_artifacts_are_available_for_manual_review(tmp_path):
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver

    out, rid, _ = make_run(tmp_path, targets=["sft"])
    execute(out, rid)
    artifact = run_path(out, rid) / "artifacts" / "sft.jsonl"
    driver = FilesystemWorkflowDriver(tmp_path, out)

    assert driver.reviewable_artifacts() == [str(artifact)]
    artifact.write_text("tampered", encoding="utf-8")
    assert driver.reviewable_artifacts() == []


def test_cpt_is_local_and_preserves_numeric_lines(tmp_path):
    out, rid, _ = make_run(tmp_path, targets=["cpt"])
    state = Workflow(out, rid, tmp_path).execute()
    assert state["status"] == "completed"
    assert state["usage"] == {}
    assert state["stages"]["sft"]["status"] == "skipped"
    src = tmp_path / "numbers.txt"
    src.write_text("# 阈值\n\n1234\n\n5678", encoding="utf-8")
    other = create_run(out, sources=[src], targets=["cpt"])
    execute(out, other)
    assert "1234" in (run_path(out, other) / "artifacts/cpt.jsonl").read_text(encoding="utf-8")


def test_bad_judge_fails_then_resume_reuses_generated_answer(tmp_path):
    out, rid, _ = make_run(tmp_path, targets=["sft"])
    gen = Generator()
    state = execute(out, rid, gen, Judge(invalid=True))
    assert state["status"] == "failed"
    assert state["error"] == "invalid_judge_boolean"
    assert len(gen.calls) == 1
    state = resume(out, rid, tmp_path, generator=gen, judge=Judge())
    assert state["status"] == "completed"
    assert len(gen.calls) == 1
    assert state["attempt"] == 2


def test_cancellation_preserves_model_response_and_resumes(tmp_path):
    out, rid, _ = make_run(tmp_path, targets=["sft"])
    gen = Generator(hook=lambda: cancel(out, rid))
    state = execute(out, rid, gen)
    assert state["status"] == "cancelled"
    gen.hook = None
    state = resume(out, rid, tmp_path, generator=gen, judge=Judge())
    assert state["status"] == "completed"
    assert len(gen.calls) == 1


def test_snapshot_is_independent_of_original_but_cannot_be_modified(tmp_path):
    out, rid, original = make_run(tmp_path, targets=["cpt"])
    original.write_text("changed original", encoding="utf-8")
    assert execute(out, rid)["status"] == "completed"
    other = create_run(out, sources=[original], targets=["cpt"])
    path = run_path(out, other)
    snapshot = next((path / "inputs").iterdir())
    snapshot.write_text("tampered snapshot", encoding="utf-8")
    state = execute(out, other)
    assert state["status"] == "failed"
    assert state["error"] == "source_snapshot_changed"


def test_run_lock_blocks_resume_without_removing_cancel(tmp_path):
    out, rid, _ = make_run(tmp_path)
    path = run_path(out, rid)
    cancel(out, rid)
    with FileLock(str(path / ".run.lock")):
        with pytest.raises(Timeout):
            resume(out, rid, tmp_path)
    assert (path / "cancel.json").exists()


def test_invalid_and_secret_records_are_counted_and_not_sent_to_model(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text('broken JSON\n' + json.dumps({"text": "api_key=supersecretcredential"}) + '\n' + json.dumps({"text": TEXT}), encoding="utf-8")
    out = tmp_path / "out"
    rid = create_run(out, sources=[source], targets=["cpt"])
    state = execute(out, rid)
    assert state["input_summary"]["quarantined"] == 2
    records = read_json(run_path(out, rid) / "input_records.json")
    assert {r.get("reason") for r in records} >= {"invalid_json_record", "potential_secret"}
    assert state["quality"]["targets"]["cpt"]["eligible"] == 1
    assert "supersecret" not in json.dumps(state)


def test_empty_targets_and_missing_inputs_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        create_run(tmp_path, brief="topic", targets=[])
    with pytest.raises(ValueError):
        create_run(tmp_path)
    with pytest.raises(ValueError):
        run_path(tmp_path, "../somewhere")


def test_open_requirement_outputs_are_marked_synthetic(tmp_path):
    out = tmp_path / "out"
    rid = create_run(out, brief="维护培训", tasks=2, targets=["cpt"])
    state = execute(out, rid)
    assert state["status"] == "completed"
    records = read_json(run_path(out, rid) / "artifacts/cpt.records.json")
    assert all(r["synthetic"] and r["evidence_level"] == "model_assessed_synthetic" for r in records)
    assert len(records) == 2


def test_no_preference_is_not_fabricated_and_not_success(tmp_path):
    class TiedGenerator(Generator):
        def chat(self, messages, **kwargs):
            result = super().chat(messages, **kwargs)
            if "另一个独立" in messages[0]["content"]:
                return json.dumps({"answer": "GOOD 完整的另一种正确解释。", "reasoning": "先断电的要求符合原文。"})
            return result
    out, rid, _ = make_run(tmp_path, targets=["dpo"])
    state = execute(out, rid, TiedGenerator())
    assert state["status"] == "needs_attention"
    assert state["quality"]["targets"]["dpo"]["reasons"] == {"insufficient_preference_evidence": 1}
    assert not (run_path(out, rid) / "artifacts/sft.jsonl").exists()


def test_rlaif_quarantines_conflicting_ai_score_evidence(tmp_path):
    class InconsistentJudge(Judge):
        def chat(self, messages, **kwargs):
            result = json.loads(super().chat(messages, **kwargs))
            if "BAD" in messages[-1]["content"]:
                result["scores"]["correctness"] = 2
            return json.dumps(result)

    out, rid, _ = make_run(tmp_path, targets=["rlaif"])
    state = execute(out, rid, judge=InconsistentJudge())
    artifacts = run_path(out, rid) / "artifacts"
    assert state["status"] == "needs_attention"
    assert state["quality"]["targets"]["rlaif"]["reasons"] == {"rlaif_correctness_score_mismatch": 1}
    assert (artifacts / "rlaif.jsonl").read_text(encoding="utf-8") == ""
    assert not (artifacts / "trl_rlaif.jsonl").exists()
    assert read_json(artifacts / "rlaif.records.json")[0]["status"] == "quarantined"


@pytest.mark.parametrize("target", ["dpo", "orpo"])
@pytest.mark.parametrize("conflicting_response", ["chosen", "rejected"])
def test_preference_exports_quarantine_contradictory_correctness_dimensions(
    tmp_path, target, conflicting_response,
):
    class InconsistentJudge(Judge):
        def chat(self, messages, **kwargs):
            result = json.loads(super().chat(messages, **kwargs))
            bad = "BAD" in messages[-1]["content"]
            if bad is (conflicting_response == "rejected"):
                result["scores"]["correctness"] = 2 if bad else 4
            return json.dumps(result)

    out, rid, _ = make_run(tmp_path, targets=[target])
    state = execute(out, rid, judge=InconsistentJudge())
    artifacts = run_path(out, rid) / "artifacts"
    assert state["status"] == "needs_attention", state.get("error")
    assert state["quality"]["targets"][target]["reasons"] == {"preference_correctness_score_mismatch": 1}
    assert (artifacts / f"{target}.jsonl").read_text(encoding="utf-8") == ""
    assert not (artifacts / f"trl_{target}.jsonl").exists()
    evidence = read_json(artifacts / f"{target}.records.json")[0]
    assert evidence["status"] == "quarantined"
    assert evidence["preference"][conflicting_response]["correctness"] != evidence["preference"][conflicting_response]["scores"]["correctness"]


def test_limit_exposes_unprocessed_units(tmp_path):
    src = tmp_path / "source.jsonl"
    src.write_text('\n'.join(json.dumps({"text": str(i)}) for i in range(3)), encoding="utf-8")
    rid = create_run(tmp_path, sources=[src], max_units=1, targets=["cpt"])
    state = execute(tmp_path, rid)
    assert state["status"] == "needs_attention"
    assert state["input_summary"]["deferred"] == 2


def test_completed_bundle_cannot_be_silently_tampered(tmp_path):
    out, rid, _ = make_run(tmp_path, targets=["cpt"])
    execute(out, rid)
    (run_path(out, rid) / "artifacts/cpt.jsonl").write_text("bad", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_integrity"):
        resume(out, rid, tmp_path)


def test_unlisted_artifact_cannot_be_included_in_verified_bundle(tmp_path):
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver

    out, rid, _ = make_run(tmp_path, targets=["cpt"])
    execute(out, rid)
    artifacts = run_path(out, rid) / "artifacts"
    (artifacts / "unexpected.txt").write_text("not in manifest", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_inventory_mismatch"):
        FilesystemWorkflowDriver(tmp_path, out).bundle(rid)


def test_context_input_keeps_tool_history_and_metadata(tmp_path):
    source = tmp_path / "agent.jsonl"
    messages = [{"role": "user", "content": "检查电源"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "read_power", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "call1", "content": "电源已断开"},
                {"role": "assistant", "content": "GOOD 可以检查线路", "reasoning_content": "工具已确认断电。"}]
    source.write_text(json.dumps({"messages": messages, "tools": [{"type": "function", "function": {"name": "read_power"}}]}), encoding="utf-8")
    rid = create_run(tmp_path, sources=[source], targets=["sft"])
    gen = Generator()
    state = execute(tmp_path, rid, gen)
    assert state["status"] == "completed"
    result = json.loads((run_path(tmp_path, rid) / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    assert result["messages"] == messages
    assert result["tools"][0]["function"]["name"] == "read_power"
    assert not gen.calls


@pytest.mark.parametrize("with_tools", [False, True])
def test_recorded_answer_without_reasoning_is_preserved_without_regeneration(tmp_path, with_tools):
    messages = [{"role": "user", "content": "设备可以检查了吗？"}]
    tools = []
    if with_tools:
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call1", "type": "function",
                "function": {"name": "read_power", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call1", "content": "电源已断开"},
        ])
        tools = [{"type": "function", "function": {"name": "read_power"}}]
    messages.append({"role": "assistant", "content": "GOOD 电源已断开，可以检查。", "source_note": "original"})
    source = tmp_path / "recorded.jsonl"
    source.write_text(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False), encoding="utf-8")
    rid = create_run(tmp_path, sources=[source], targets=["sft"])
    gen, judge = Generator(), Judge()
    state = execute(tmp_path, rid, gen, judge)
    assert state["status"] == "completed", state.get("error")
    assert not gen.calls
    assert judge.calls == 1
    artifacts = run_path(tmp_path, rid) / "artifacts"
    payload = json.loads((artifacts / "sft.jsonl").read_text(encoding="utf-8"))
    assert payload["messages"] == messages
    assert "reasoning_content" not in payload["messages"][-1]
    row = read_json(artifacts / "sft.records.json")[0]
    assert row["evidence_level"] == "recorded_context_model_assessed"


@pytest.mark.parametrize("with_reasoning", [False, True])
def test_unified_review_preserves_imported_conversation_source_and_actual_tool_evidence(tmp_path, with_reasoning):
    messages = [{"role": "user", "content": "设备可以检查了吗？"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "power-check", "type": "function",
                    "function": {"name": "read_power", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "power-check", "content": "电源已断开"},
                {"role": "assistant", "content": "GOOD 电源已断开，可以检查。"}]
    if with_reasoning:
        messages[-1]["reasoning_content"] = "工具返回已断电，满足检查线路的前提。"
    tools = [{"type": "function", "function": {"name": "read_power"}}]
    source = tmp_path / "recorded.jsonl"
    source.write_text(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False), encoding="utf-8")
    rid = create_run(tmp_path, sources=[source], targets=["sft"],
        review_repair={"mode": "auto", "max_rounds": 0},
        package_review={"enabled": True, "mode": "all", "node": "jev"})

    class RecordReviewer(Judge):
        def __init__(self):
            super().__init__()
            self.requests = []

        def chat(self, request_messages, **kwargs):
            self.requests.append(json.loads(request_messages[-1]["content"]))
            return super().chat(request_messages, **kwargs)

    gen, judge = Generator(), RecordReviewer()
    state = execute(tmp_path, rid, gen, judge)
    artifacts = run_path(tmp_path, rid) / "artifacts"
    row = read_json(artifacts / "sft.records.json")[0]
    assert state["status"] == "completed", row.get("reason") or state.get("error")
    assert not gen.calls
    assert judge.calls == 1
    assert row["messages"] == messages and row["tools"] == tools
    assert row["reasoning_origin"] == "source"
    assert row["evidence_level"] == "recorded_context_model_assessed"
    assert row["review_repair"]["status"] == "accepted"
    assert judge.requests[0]["candidate"]["messages"] == messages
    evidence = json.dumps(judge.requests[0]["source_evidence"], ensure_ascii=False)
    assert "电源已断开" in evidence and "read_power" in evidence
    assert "GOOD 电源已断开，可以检查。" not in evidence
    assert "工具返回已断电，满足检查线路的前提。" not in evidence
    assert judge.requests[0]["source_evidence"]["source_kind"] == "human_provided"
    assert "recorded_not_independently_verified" in evidence
    exported = json.loads((artifacts / "sft.jsonl").read_text(encoding="utf-8"))
    assert exported["messages"] == messages and exported["tools"] == tools


def test_rejected_recorded_answer_is_quarantined_without_silent_rewriting(tmp_path):
    messages = [{"role": "user", "content": "带电时可以检查线路吗？"},
                {"role": "assistant", "content": "BAD 可以直接检查。"}]
    source = tmp_path / "recorded.jsonl"
    source.write_text(json.dumps({"messages": messages}, ensure_ascii=False), encoding="utf-8")
    rid = create_run(tmp_path, sources=[source], targets=["sft"])
    gen, judge = Generator(), Judge()
    state = execute(tmp_path, rid, gen, judge)
    assert state["status"] == "needs_attention", state.get("error")
    assert not gen.calls
    assert judge.calls == 1
    artifacts = run_path(tmp_path, rid) / "artifacts"
    assert (artifacts / "sft.jsonl").read_text(encoding="utf-8") == ""
    row = read_json(artifacts / "sft.records.json")[0]
    assert row["reason"] == "recorded_sft_judge_rejected"
    assert row["messages"] == messages


def test_document_question_carries_evidence_into_actual_training_prompt(tmp_path):
    out, rid, _ = make_run(tmp_path, targets=["sft"])
    state = execute(out, rid, Generator())
    assert state["status"] == "completed"
    row = json.loads((run_path(out, rid) / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    prompt = row["messages"][0]["content"]
    assert TEXT in prompt
    assert "设备故障时应如何检查？" in prompt
    assert row["messages"][-1]["role"] == "assistant"


@pytest.mark.parametrize("messages,reason", [
    ([{"role": "tool", "tool_call_id": "x", "content": "result"}], "orphan_tool_result"),
    ([{"role": "user", "content": "question"}], "missing_final_answer"),
    ([{"role": "user", "content": "question", "isError": True}], "unresolved_tool_error"),
])
def test_conversation_contract(messages, reason):
    assert conversation_issue(messages) == reason


@pytest.mark.parametrize("score", [None, True, "5", 0, 6, 4.5])
def test_judge_requires_real_integer_score(score):
    with pytest.raises(ValueError):
        verdict({"keep": True, "grounded": True, "reasoning_valid": True, "correctness": score,
                 "scores": {}, "reason": "explanation"})


def test_all_seven_objectives_have_validated_exports(tmp_path):
    out, rid, _ = make_run(tmp_path, targets=["cpt", "sft", "dpo", "rlaif", "gsm8k", "cot", "orpo"], tasks=4)
    state = execute(out, rid)
    assert state["status"] == "completed", state.get("error")
    manifest = verify_artifacts(run_path(out, rid))
    assert set(manifest["counts"]) == {"cpt", "sft", "dpo", "rlaif", "gsm8k", "cot", "orpo"}
    artifacts = run_path(out, rid) / "artifacts"
    assert set(json.loads((artifacts / "rlaif.jsonl").read_text(encoding="utf-8"))) == {"prompt", "responses", "criterion"}
    reward_pair = json.loads((artifacts / "trl_rlaif.jsonl").read_text(encoding="utf-8"))
    assert set(reward_pair) == {"prompt", "chosen", "rejected"}
    assert reward_pair == json.loads((artifacts / "trl_dpo.jsonl").read_text(encoding="utf-8"))
    assert manifest["trainer_counts"]["rlaif"] == 1
    assert read_json(artifacts / "quality.json")["trainer_exports"]["rlaif"]["status"] == "ready"
    pair = json.loads((artifacts / "orpo.jsonl").read_text(encoding="utf-8"))
    assert set(pair) == {"prompt", "chosen", "rejected"}
    cot = json.loads((artifacts / "cot.jsonl").read_text(encoding="utf-8"))
    assert cot["reasoning"] and cot["answer"]
    from lib.math_tasks import evaluate_integer_expression
    gsm = json.loads((artifacts / "gsm8k.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "#### " in gsm["answer"]
    record = read_json(artifacts / "gsm8k.records.json")[0]
    assert evaluate_integer_expression(record["arithmetic_expression"]) == record["verified_result"]
    assert record["generator_version"] == 2 and len(record["calculation_steps"]) >= 2
    assert read_json(run_path(out, rid) / "recipe.json")["math_generator_version"] == 2
    assert state["models"]["jev"]["model"] == "judge-test"


def test_math_expression_checker_rejects_executable_or_unbounded_input():
    from lib.math_tasks import build_gsm8k, evaluate_integer_expression, validate_gsm8k
    for invalid in ["__import__('os').system('whoami')", "1 ** 999999", "1 // 0", "-1 + 2", "1 / 2"]:
        with pytest.raises((ValueError, SyntaxError)):
            evaluate_integer_expression(invalid)
    for seed in range(100):
        assert validate_gsm8k(build_gsm8k("设备维护", seed))


def test_math_step_mismatch_is_quarantined_before_export(tmp_path, monkeypatch):
    from lib.domain.math_tasks import validate_gsm8k
    from lib.infrastructure import workflow_arithmetic
    original = workflow_arithmetic.registered_arithmetic_sample
    injected = []

    def mismatched(*args, **kwargs):
        sample = original(*args, **kwargs)
        assert validate_gsm8k(sample)
        first = sample["_steps"][0]
        original_answer = sample["answer"]
        sample["answer"] = sample["answer"].replace(
            f"<<{first['expression']}={first['result']}>>",
            f"<<0 + {first['result']}={first['result']}>>")
        assert sample["answer"] != original_answer and not validate_gsm8k(sample)
        injected.append(sample)
        return sample

    monkeypatch.setattr(workflow_arithmetic, "registered_arithmetic_sample", mismatched)
    out, rid, _ = make_run(tmp_path, targets=["gsm8k"], tasks=4)
    state = execute(out, rid)
    assert len(injected) == 4
    assert state["status"] == "needs_attention"
    manifest = verify_artifacts(run_path(out, rid))
    assert manifest["counts"]["gsm8k"] == 0
    report = read_json(run_path(out, rid) / "artifacts/quality.json")
    assert report["targets"]["gsm8k"]["reasons"]["gsm8k_arithmetic_verification_failed"] == len(injected)


def test_package_rechecks_persisted_math_step_evidence(tmp_path):
    out, rid, _ = make_run(tmp_path, targets=["gsm8k"], tasks=4)
    execute(out, rid)
    path = run_path(out, rid)
    record = read_json(path / "artifacts/gsm8k.records.json")[0]
    record["calculation_steps"][0]["result"] += 1
    Workflow(out, rid, out.parent).package({"gsm8k": [record]})
    manifest = verify_artifacts(path)
    assert manifest["counts"]["gsm8k"] == 0
    report = read_json(path / "artifacts/quality.json")
    assert report["targets"]["gsm8k"]["reasons"]["gsm8k_arithmetic_verification_failed"] == 1


def test_unversioned_math_recipe_keeps_legacy_generator_on_execution(tmp_path):
    from lib.workflow import digest
    from lib.io_utils import atomic_json
    from lib.math_tasks import build_gsm8k
    out, rid, _ = make_run(tmp_path, targets=["gsm8k"], tasks=4)
    path = run_path(out, rid)
    recipe = read_json(path / "recipe.json")
    recipe.pop("math_generator_version")
    recipe.pop("arithmetic_seed_protocol_version", None)
    atomic_json(path / "recipe.json", recipe)
    state = read_json(path / "state.json")
    state["recipe_hash"] = digest(recipe)
    atomic_json(path / "state.json", state)
    generator = Generator()
    execute(out, rid, generator=generator)
    assert not generator.calls
    record = read_json(path / "artifacts/gsm8k.records.json")[0]
    sample = build_gsm8k(recipe["sources"][0]["name"], int(record["id"][:8], 16), version=1)
    assert record["question"] == sample["question"] and record["answer"] == sample["answer"]
    assert record["generator_version"] == 1


def test_recipe_changed_after_executor_construction_is_rejected_before_calls(tmp_path):
    from lib.infrastructure.training_workflow import atomic_json
    out, rid, _ = make_run(tmp_path, targets=['sft'])
    generator, judge = Generator(), Judge()
    workflow = Workflow(out, rid, tmp_path, generator=generator, judge=judge)
    recipe = read_json(workflow.path / 'recipe.json')
    recipe['sample_count'] = 50000
    atomic_json(workflow.path / 'recipe.json', recipe)
    state = workflow.execute()
    assert state['status'] == 'failed'
    assert state['error'] == 'recipe_changed_create_new_run'
    assert not generator.calls
    assert not (workflow.path / 'input_records.json').exists()


def test_unchanged_recipe_execution_uses_locked_disk_snapshot(tmp_path):
    out, rid, _ = make_run(tmp_path, targets=['cpt'])
    workflow = Workflow(out, rid, tmp_path)
    workflow.recipe['targets'] = ['sft']
    state = workflow.execute()
    assert state['status'] == 'completed'
    assert workflow.recipe['targets'] == ['cpt']
    assert (workflow.path / 'artifacts' / 'cpt.jsonl').exists()
