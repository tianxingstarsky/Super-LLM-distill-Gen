"""Offline execution of the dedicated review/correction branch."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from lib.domain.review_repair import validate_review_repair, validate_repair_inputs
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.human_revision import record_messages, revision_evidence
from lib.io_utils import atomic_json
from tests.test_workflow_production import model_request_data
from tests.test_workflow_qa_director_engine import source_materials
from source_memory_fixtures import memory_protocol_versions
from review_legacy_fixtures import legacy_review_runs  # noqa: F401 -- historical scoring/delta contract


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SOURCE = "Inspect the equipment only after turning off the power. Record the result after inspection."
WRONG = "Inspect with the power on."
RIGHT = "Turn off the power before inspection."
ROOT = Path(__file__).resolve().parents[1]


def check(keep=True):
    score = 5 if keep else 1
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": score,
            "scores": {name: score for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "The answer matches the source." if keep else "The answer reverses the power-off condition."}


class Client:
    model = "review-branch-offline"

    def __init__(self, *, repair_ok=True, fail_score_once=False):
        self.calls, self.usage = [], {"calls": 0}
        self.message_history = []
        self.repair_ok, self.fail_score_once = repair_ok, fail_score_once

    def chat(self, messages, **kwargs):
        self.message_history.append(deepcopy(messages))
        data = model_request_data(messages)
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if "review_feedback" in data:
            payload = deepcopy(data["candidate"])
            payload["messages"][-1]["content"] = RIGHT if self.repair_ok else WRONG
            return json.dumps({"record": payload, "quotes": [SOURCE], "uncertain": False})
        assert "source_evidence" in data, "Only review-node prompts may run in this branch."
        if self.fail_score_once:
            self.fail_score_once = False
            raise ValueError("offline_interruption")
        return json.dumps(check(WRONG not in json.dumps(data["candidate"])))


class NoCalls:
    model = "unused-offline"
    usage = {}

    def chat(self, *args, **kwargs):
        raise AssertionError("Manual review must not call a model.")


def generated(unit):
    return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible",
             "messages": [{"role": "user", "content": "When should the power be turned off?"},
                          {"role": "assistant", "content": WRONG, "reasoning_content": "Check the safety condition."}],
             "quotes": [SOURCE], "source_context": unit, "evidence_level": "source_and_model_assessed"}]


def create_parent(tmp_path, monkeypatch, config=None, **kwargs):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"],
        review_repair=config or {"mode": "human"}, **kwargs)
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    client = NoCalls() if not config or config.get("mode") == "human" else Client()
    state = engine.Workflow(output, run_id, ROOT, generator=client, judge=NoCalls()).execute()
    assert state["status"] in {"completed", "needs_attention"}, state.get("error")
    path = engine.run_path(output, run_id)
    record = json.loads((path / "artifacts/sft.records.json").read_text(encoding="utf-8"))[0]
    return output, run_id, record, client


def create_child(output, parent_id, record, *, mode="auto", max_rounds=2, target="sft", create_options=None, **fields):
    session_id, round_id = uuid.uuid4().hex, uuid.uuid4().hex
    parent_recipe = engine.read_json(engine.run_path(output, parent_id) / "recipe.json")
    context = {"session_id": session_id, "round_id": round_id, "parent_run_id": parent_id,
        "target": target, "candidate_id": record["id"], "content_sha256": engine.digest(record),
        "messages": record_messages(record), "instruction": "Correct the unsafe power-on instruction.",
        "depth": 1, "ancestors": [{"run_id": parent_id, "candidate_id": record["id"]}],
        **revision_evidence(record, parent_recipe, engine.run_path(output, parent_id))}
    item = {"target": target, "record": record, "revision_context": context,
            "instruction": context["instruction"], **fields}
    child = engine.create_run(output, targets=[target], review_repair={"mode": mode, "max_rounds": max_rounds},
        repair_inputs=[item], **(create_options or {}))
    atomic_json(output / "human-sessions" / session_id / "session.json", {"rounds": [{"run_id": child,
        "kind": "manual_review" if mode == "human" else "revision", "status": "prepared",
        "parent_results": [{"run_id": parent_id, "round_id": round_id, "target": target,
                            "candidate_id": record["id"], "content_sha256": engine.digest(record)}]}]})
    return child


def test_automatic_scoring_and_repair_share_one_node_and_one_model(tmp_path, monkeypatch):
    output, run_id, record, client = create_parent(tmp_path, monkeypatch, {"mode": "auto", "max_rounds": 1})
    assert record["status"] == "eligible"
    assert record["messages"][-1]["content"] == RIGHT
    assert record["review_repair"]["attempts"] == 1
    assert ["review_feedback" in data for data in client.calls] == [False, True, False]
    state = engine.read_json(engine.run_path(output, run_id) / "state.json")
    assert state["models"]["generation"]["model"] == client.model
    assert "jev" not in state["usage"]
    assert state["stages"]["review"]["status"] == "completed"
    assert state["quality"]["review_repair"]["targets"]["sft"]["statuses"] == {"accepted": 1}


def test_optional_jev_scores_while_correction_uses_other_role_in_same_node(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], review_repair={"mode": "auto"},
        package_review={"enabled": True, "mode": "all", "node": "jev"})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    correction, scoring = Client(), Client()
    state = engine.Workflow(output, run_id, ROOT, generator=correction, jev=scoring).execute()
    assert state["status"] == "completed", state.get("error")
    assert all("review_feedback" in data for data in correction.calls)
    assert all("review_feedback" not in data for data in scoring.calls)
    assert len(correction.calls) == 1 and len(scoring.calls) == 2
    assert "jev" not in state["stages"]


def test_manual_initial_candidates_remain_visible_but_do_not_enter_training(tmp_path, monkeypatch):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    assert record["status"] == "quarantined"
    assert record["messages"][-1]["content"] == WRONG
    assert record["review_repair"]["status"] == "waiting_manual_review"
    assert (engine.run_path(output, parent_id) / "artifacts/sft.jsonl").read_text(encoding="utf-8") == ""


def test_repair_child_never_reenters_sft_and_preserves_origin(tmp_path, monkeypatch):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    parent_bytes = (engine.run_path(output, parent_id) / "artifacts/sft.records.json").read_bytes()
    child = create_child(output, parent_id, record)
    monkeypatch.setattr(engine.Workflow, "sft", lambda *_: (_ for _ in ()).throw(AssertionError("Wrong branch")))
    client = Client()
    state = engine.Workflow(output, child, ROOT, generator=client, judge=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["stages"]["sft"]["status"] == "skipped"
    repaired = engine.read_json(engine.run_path(output, child) / "artifacts/sft.records.json")[0]
    assert repaired["revision_context"]["parent_run_id"] == parent_id
    assert repaired["source_id"] == record["source_id"]
    assert repaired["messages"][-1]["content"] == RIGHT
    materials = source_materials(client.message_history[0])
    assert [material["text"] for material in materials] == [SOURCE]
    assert all(SOURCE not in message["content"] for history in client.message_history
               for message in history if message["role"] == "user")
    assert "candidates" not in client.calls[0] and "source" not in client.calls[0]
    assert (engine.run_path(output, parent_id) / "artifacts/sft.records.json").read_bytes() == parent_bytes


def test_manual_score_and_correction_are_one_zero_api_operation(tmp_path, monkeypatch):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    patch = deepcopy(record["messages"])
    patch[-1]["content"] = RIGHT
    child = create_child(output, parent_id, record, mode="human", score=.95, approved=True,
                         corrected_record={"messages": patch})
    state = engine.Workflow(output, child, ROOT, generator=NoCalls(), judge=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    repaired = engine.read_json(engine.run_path(output, child) / "artifacts/sft.records.json")[0]
    assert repaired["messages"][-1]["content"] == RIGHT
    assert repaired["review_repair"]["score"] == .95
    assert repaired["review_repair"]["status"] == "accepted"
    assert not state["usage"]
    assert state["quality"]["human_review"] == "performed"


@pytest.mark.parametrize("with_tools", [False, True])
def test_recorded_conversation_rework_uses_original_inputs_not_candidate_answers(tmp_path, with_tools):
    messages = [{"role": "user", "content": "We need to inspect the equipment."},
                {"role": "assistant", "content": "Earlier candidate claim.",
                 "reasoning_content": "Earlier candidate reasoning must not prove itself."},
                {"role": "user", "content": "When should the power be turned off?"}]
    tools = []
    if with_tools:
        tools = [{"type": "function", "function": {"name": "read_safety_rule"}}]
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": "safety-check", "type": "function",
                "function": {"name": "read_safety_rule", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "safety-check", "content": SOURCE},
        ])
    messages.append({"role": "assistant", "content": WRONG,
                     "reasoning_content": "Final candidate reasoning must not prove itself."})
    source = tmp_path / "recorded.jsonl"
    source.write_text(json.dumps({"messages": messages, "tools": tools}), encoding="utf-8")
    output = tmp_path / "output"
    parent_id = engine.create_run(output, sources=[source], targets=["sft"], review_repair={"mode": "human"})
    parent = engine.Workflow(output, parent_id, ROOT, generator=NoCalls(), judge=NoCalls())
    state = parent.execute()
    assert state["status"] == "needs_attention", state.get("error")
    row = engine.read_json(parent.path / "artifacts/sft.records.json")[0]
    assert row["review_repair"]["status"] == "waiting_manual_review"
    evidence = revision_evidence(row, parent.recipe, parent.path)
    proof = json.loads(evidence["teacher_evidence"])
    assert evidence["source_kind"] == "human_provided"
    assert proof["verification"] == "recorded_not_independently_verified"
    assert proof["tools"] == tools
    assert len(proof["messages"]) == len(messages) - 1
    assert WRONG not in evidence["teacher_evidence"]
    assert "Earlier candidate claim." not in evidence["teacher_evidence"]
    assert "candidate reasoning" not in evidence["teacher_evidence"]
    assert (SOURCE in evidence["teacher_evidence"]) is with_tools
    if with_tools:
        assert proof["messages"][-1] == messages[-2]

    child_id = create_child(output, parent_id, row, mode="human", score=.95, approved=True, answer=RIGHT)
    child = engine.Workflow(output, child_id, ROOT, generator=NoCalls(), judge=NoCalls())
    state = child.execute()
    assert state["status"] == "completed", state.get("error")
    repaired = engine.read_json(child.path / "artifacts/sft.records.json")[0]
    assert repaired["messages"][-1]["content"] == RIGHT
    assert repaired["messages"][:-1] == messages[:-1] and repaired["tools"] == tools
    # A later rework continues to use original observations, never the edited answer.
    assert revision_evidence(repaired, child.recipe, child.path) == evidence
    assert not state["usage"]


@pytest.mark.parametrize("invalid", ["synthetic", "tool_error", "orphan_tool"])
@pytest.mark.parametrize("legacy_task", [False, True])
def test_recorded_review_evidence_rejects_fabricated_or_invalid_observations(tmp_path, invalid, legacy_task):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="An unrelated task must not rescue bad source evidence.",
        targets=["sft"], review_repair={"mode": "auto", "max_rounds": 1})
    workflow = engine.Workflow(output, run_id, ROOT, generator=NoCalls(), judge=NoCalls())
    messages = [{"role": "user", "content": "Can I inspect the equipment?"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "read", "type": "function",
                    "function": {"name": "read_safety_rule", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "read", "content": SOURCE},
                {"role": "assistant", "content": RIGHT}]
    context = {"kind": "conversation", "messages": deepcopy(messages), "tools": []}
    if legacy_task:
        context["task"] = json.dumps(messages)
    if invalid == "synthetic":
        context["verification_status"] = "synthetic_unverified"
    elif invalid == "tool_error":
        context["messages"][-2]["isError"] = True
    else:
        context["messages"][-2]["tool_call_id"] = "invented"
    row = {"id": "recorded-candidate", "source_id": "0" * 64, "status": "eligible",
           "messages": messages, "source_context": context, "quotes": [SOURCE]}
    with pytest.raises(ValueError, match="human_session_source_evidence_unavailable"):
        revision_evidence(row, workflow.recipe)
    checked = workflow._review_repair_item({"target": "sft", "record": row})[0]
    assert checked["status"] == "quarantined"
    assert checked["reason"] == "review_repair_source_evidence_unavailable"


def test_agent_unified_review_and_legacy_task_keep_candidate_separate_from_tool_evidence(tmp_path):
    tools = [{"type": "function", "function": {"name": "calculator", "parameters": {
        "type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}]
    messages = [{"role": "user", "content": "Compute 2 + 2."},
                {"role": "assistant", "content": "", "reasoning_content": "Private tool-choice rationale.",
                 "tool_calls": [{"id": "calculation", "type": "function", "function": {
                    "name": "calculator", "arguments": '{"expression":"2+2"}'}}]},
                {"role": "tool", "tool_call_id": "calculation", "content": "4"},
                {"role": "assistant", "content": "4", "reasoning_content": "Private final-answer rationale."}]
    source = tmp_path / "trajectory.jsonl"
    source.write_text(json.dumps({"messages": messages, "tools": tools}), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["agent"],
        review_repair={"mode": "auto", "max_rounds": 0},
        package_review={"enabled": True, "mode": "all", "node": "jev"})
    scoring = Client()
    workflow = engine.Workflow(output, run_id, ROOT, generator=NoCalls(), jev=scoring)
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(scoring.calls) == 1
    record = engine.read_json(workflow.path / "artifacts/agent.records.json")[0]
    assert record["review_repair"]["status"] == "accepted"
    assert "task" not in record["source_context"]
    assert record["source_context"]["tools"] == tools
    assert "verification_status" in record["source_context"]
    wire_evidence = scoring.calls[0]["source_evidence"]
    evidence = workflow._review_evidence(record)
    if workflow.recipe["source_memory_protocol_version"] == 2:
        assert "teacher_evidence" not in wire_evidence
        assert wire_evidence == {key: value for key, value in evidence.items() if key != "teacher_evidence"}
        actual_text = "\n\n".join(material["text"] for material in source_materials(scoring.message_history[0]))
        assert actual_text == evidence["teacher_evidence"]
        proof = json.loads(actual_text)
    else:
        assert wire_evidence == evidence
        proof = json.loads(evidence["teacher_evidence"])
    assert evidence["source_kind"] == "human_provided"
    assert proof["tools"] == tools
    assert proof["messages"][-1] == messages[-2]
    assert len(proof["messages"]) == len(messages) - 1
    assert "rationale" not in evidence["teacher_evidence"]
    assert "recorded_not_independently_verified" in evidence["teacher_evidence"]
    assert record["verification"]["verified_call_ids"] == ["calculation"]

    # Legacy stored Agent rows had the answer and rationale duplicated in task.
    legacy = deepcopy(record)
    legacy["source_context"]["task"] = json.dumps(messages)
    assert revision_evidence(legacy, workflow.recipe) == evidence
    assert workflow._review_evidence(legacy) == evidence


@pytest.mark.parametrize("mode", ["auto", "human"])
@pytest.mark.parametrize("parser_mode", ["vision", "model"])
def test_repair_only_ignores_inherited_document_and_cpt_model_requirements(tmp_path, monkeypatch, mode, parser_mode):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    binding = {"backend": "removed-source-reader", "model": "retired-vision-model"}
    inherited = {"document_parser": {"mode": parser_mode, "binding": binding},
        "cpt_processing": {"mode": "model", "review_mode": "vision"},
        "node_models": {"ingest": {"vision": binding}, "cpt": {"jev": binding}}, "settings_root": ROOT}

    def unused(*args, **kwargs):
        raise AssertionError("Repair-only execution must never require the old parsing or cleaning model.")

    monkeypatch.setattr("lib.infrastructure.document_vision.require_vision_model", unused)
    monkeypatch.setattr(engine, "snapshot_backend_endpoint", unused)
    child = create_child(output, parent_id, record, mode=mode, create_options=inherited,
        **({"score": .95, "approved": True, "answer": RIGHT} if mode == "human" else {}))
    recipe = engine.read_json(engine.run_path(output, child) / "recipe.json")
    assert recipe["document_parser"]["mode"] == parser_mode
    assert recipe["cpt_processing"]["review_mode"] == "vision"
    assert recipe["node_models"] == {}
    state = engine.Workflow(output, child, ROOT, generator=NoCalls() if mode == "human" else Client()).execute()
    assert state["status"] == "completed", state.get("error")


def test_manual_agent_review_does_not_require_an_unused_replay_sandbox(tmp_path, monkeypatch):
    messages = [{"role": "user", "content": "Calculate 2+2."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "type": "function", "function":
            {"name": "calculator", "arguments": json.dumps({"expression": "2+2"})}}]},
        {"role": "tool", "tool_call_id": "a", "content": "4"}, {"role": "assistant", "content": "4"}]
    source = tmp_path / "trajectory.jsonl"
    source.write_text(json.dumps({"messages": messages}) + "\n", encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["agent"], agent_replay_mode="local",
        review_repair={"mode": "human"})
    state = engine.Workflow(output, run_id, ROOT, generator=NoCalls()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    original = engine.read_json(engine.run_path(output, run_id) / "artifacts/agent.records.json")[0]

    def unused(*args, **kwargs):
        raise AssertionError("Manual candidate review must not start or require Agent replay.")

    monkeypatch.setattr(engine, "validate_sandbox_image", unused)
    child = create_child(output, run_id, original, target="agent", mode="human", score=.95, approved=True,
        create_options={"agent_replay_mode": "isolated"})
    state = engine.Workflow(output, child, ROOT, generator=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    record = engine.read_json(engine.run_path(output, child) / "artifacts/agent.records.json")[0]
    assert record["messages"] == original["messages"]
    assert record["verification"] == original["verification"]


def test_manual_approval_below_threshold_cannot_release(tmp_path, monkeypatch):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    child = create_child(output, parent_id, record, mode="human", score=.4, approved=True, answer=RIGHT)
    state = engine.Workflow(output, child, ROOT, generator=NoCalls()).execute()
    assert state["status"] == "needs_attention"
    assert state["quality"]["targets"]["sft"]["eligible"] == 0


def test_failed_repair_is_bounded_and_never_exported(tmp_path, monkeypatch):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    child = create_child(output, parent_id, record, max_rounds=2)
    client = Client(repair_ok=False)
    state = engine.Workflow(output, child, ROOT, generator=client).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert sum("review_feedback" in data for data in client.calls) == 2
    repaired = engine.read_json(engine.run_path(output, child) / "artifacts/sft.records.json")[0]
    assert repaired["review_repair"]["attempts"] == 2
    assert repaired["review_repair"]["status"] == "rejected"
    assert state["quality"]["targets"]["sft"]["eligible"] == 0


def test_unchanged_repair_stops_without_paying_for_the_same_failed_score_again(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"],
        review_repair={"mode": "auto", "max_rounds": 5})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    client = Client(repair_ok=False)
    state = engine.Workflow(output, run_id, ROOT, generator=client).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert ["review_feedback" in data for data in client.calls] == [False, True]
    row = engine.read_json(engine.run_path(output, run_id) / "artifacts/sft.records.json")[0]
    assert row["review_repair"]["attempts"] == 1
    assert row["review_repair"]["stop_reason"] == "review_repair_repeated_candidate"
    assert row["review_repair"]["history"][-1]["reused_assessment"] is True
    assert state["quality"]["targets"]["sft"]["eligible"] == 0
    assert (engine.run_path(output, run_id) / "artifacts/sft.jsonl").read_text(encoding="utf-8") == ""


def test_repair_cycle_stops_at_a_previously_rejected_body(tmp_path, monkeypatch):
    class CyclingClient(Client):
        def chat(self, messages, **kwargs):
            data = model_request_data(messages)
            if "review_feedback" not in data:
                return super().chat(messages, **kwargs)
            self.calls.append(deepcopy(data))
            self.usage["calls"] += 1
            payload = deepcopy(data["candidate"])
            current = payload["messages"][-1]["content"]
            payload["messages"][-1]["content"] = WRONG + " Always." if current == WRONG else WRONG
            return json.dumps({"record": payload, "quotes": [SOURCE], "uncertain": False})

    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"],
        review_repair={"mode": "auto", "max_rounds": 5})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    client = CyclingClient()
    state = engine.Workflow(output, run_id, ROOT, generator=client).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert ["review_feedback" in data for data in client.calls] == [False, True, False, True]
    row = engine.read_json(engine.run_path(output, run_id) / "artifacts/sft.records.json")[0]
    assert row["messages"][-1]["content"] == WRONG
    assert row["review_repair"]["attempts"] == 2
    assert row["review_repair"]["stop_reason"] == "review_repair_repeated_candidate"
    assert state["quality"]["targets"]["sft"]["eligible"] == 0


def test_unscored_revision_still_gets_one_real_score_before_stopping(tmp_path, monkeypatch):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    child = create_child(output, parent_id, record, max_rounds=5)
    client = Client(repair_ok=False)
    state = engine.Workflow(output, child, ROOT, generator=client).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert ["review_feedback" in data for data in client.calls] == [True, False, True]
    row = engine.read_json(engine.run_path(output, child) / "artifacts/sft.records.json")[0]
    assert row["review_repair"]["stop_reason"] == "review_repair_repeated_candidate"


def test_resume_rebuilds_no_progress_detection_from_the_paid_score_checkpoint(tmp_path, monkeypatch):
    class InterruptedRepair(Client):
        def chat(self, messages, **kwargs):
            if "review_feedback" in model_request_data(messages):
                raise ValueError("offline_interruption")
            return super().chat(messages, **kwargs)

    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"],
        review_repair={"mode": "auto", "max_rounds": 5})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    first = InterruptedRepair()
    assert engine.Workflow(output, run_id, ROOT, generator=first).execute()["status"] == "failed"
    assert len(first.calls) == 1 and "review_feedback" not in first.calls[0]
    resumed = Client(repair_ok=False)
    state = engine.Workflow(output, run_id, ROOT, generator=resumed).execute(resume_run=True)
    assert state["status"] == "needs_attention", state.get("error")
    assert len(resumed.calls) == 1 and "review_feedback" in resumed.calls[0]
    assert state["quality"]["targets"]["sft"]["eligible"] == 0


def test_zero_rounds_scores_existing_correction_without_model_repair(tmp_path, monkeypatch):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    child = create_child(output, parent_id, record, max_rounds=0, answer=RIGHT)
    client = Client()
    state = engine.Workflow(output, child, ROOT, generator=client).execute()
    assert state["status"] == "completed", state.get("error")
    assert len(client.calls) == 1 and "review_feedback" not in client.calls[0]
    row = engine.read_json(engine.run_path(output, child) / "artifacts/sft.records.json")[0]
    assert row["review_repair"]["attempts"] == 0


def test_resume_reuses_completed_correction_call(tmp_path, monkeypatch):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    child = create_child(output, parent_id, record)
    first = Client(fail_score_once=True)
    state = engine.Workflow(output, child, ROOT, generator=first).execute()
    assert state["status"] == "failed"
    second = Client()
    state = engine.Workflow(output, child, ROOT, generator=second).execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert len(second.calls) == 1 and "review_feedback" not in second.calls[0]
    third = NoCalls()
    assert engine.Workflow(output, child, ROOT, generator=third).execute()["status"] == "completed"


def test_changed_parent_context_fails_before_any_model_call(tmp_path, monkeypatch):
    output, parent_id, record, _ = create_parent(tmp_path, monkeypatch)
    child = create_child(output, parent_id, record)
    path = engine.run_path(output, child)
    recipe = engine.read_json(path / "recipe.json")
    recipe["repair_inputs"][0]["revision_context"]["teacher_evidence"] = WRONG
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    result = engine.Workflow(output, child, ROOT, generator=NoCalls()).execute()
    assert result["status"] == "failed" and result["error"] == "human_session_result_changed"


def test_real_generation_pipeline_sft_cot_and_dpo_grade_only_in_review_node(tmp_path):
    class PipelineClient(Client):
        def chat(self, messages, **kwargs):
            data = model_request_data(messages)
            if "source_evidence" in data:
                self.calls.append(deepcopy(data))
                self.usage["calls"] += 1
                native = data["candidate"]
                chosen = native.get("chosen", native.get("messages", [{}]))
                keep = WRONG not in json.dumps(chosen)
                return json.dumps(check(keep))
            self.calls.append(deepcopy(data))
            self.usage["calls"] += 1
            if "prompt" in data:
                return json.dumps({"answer": WRONG, "reasoning": "A deliberately weak alternative."})
            assert "source" in data
            return json.dumps({"question": "When should power be turned off?", "answer": RIGHT,
                "reasoning": "The original source makes power-off a prerequisite.", "quotes": [SOURCE]})
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft", "cot", "dpo"],
                               review_repair={"mode": "auto"})
    client = PipelineClient()
    state = engine.Workflow(output, run_id, ROOT, generator=client, judge=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    assert all(state["quality"]["targets"][target]["eligible"] == 1 for target in ("sft", "cot", "dpo"))
    assert not state["usage"].get("jev")
    # CoT is the canonical answer; SFT is a format projection of that assessment.
    assert state["stages"]["review"]["total"] == 2


def test_production_batches_and_finalization_do_not_duplicate_scoring(tmp_path, monkeypatch):
    output, run_id, record, client = create_parent(tmp_path, monkeypatch, {"mode": "auto"},
        production={"goals": {"sft": 1}, "round_size": 1, "max_rounds": 2, "min_acceptance_rate": 0})
    assert record["status"] == "eligible"
    assert ["review_feedback" in data for data in client.calls] == [False, True, False]
    state = engine.read_json(engine.run_path(output, run_id) / "state.json")
    assert state["quality"]["targets"]["sft"]["eligible"] == 1


@pytest.mark.parametrize("repair_ok", [True, False])
def test_canonical_cot_is_repaired_once_and_sft_inherits_the_exact_assessment(tmp_path, monkeypatch, repair_ok):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft", "cot"],
        review_repair={"mode": "auto", "max_rounds": 1})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    monkeypatch.setattr(engine.Workflow, "cot", lambda self, sample: [engine.cot_with_sft_context(sample,
        {"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
         "question": sample["messages"][:-1], "reasoning": ["An unsafe initial interpretation."], "answer": WRONG})])

    class CanonicalClient(Client):
        def chat(self, messages, **kwargs):
            data = model_request_data(messages)
            self.calls.append(deepcopy(data))
            self.usage["calls"] += 1
            assert data["target"] == "cot", "The derived SFT format must never be independently repaired."
            if "review_feedback" in data:
                payload = deepcopy(data["candidate"])
                payload["answer"] = RIGHT if repair_ok else WRONG
                payload["reasoning"] = ["Power-off is required before equipment inspection."]
                return json.dumps({"record": payload, "quotes": [SOURCE], "uncertain": False})
            return json.dumps(check(WRONG != data["candidate"]["answer"]))

    client = CanonicalClient()
    state = engine.Workflow(output, run_id, ROOT, generator=client, judge=NoCalls()).execute()
    assert state["status"] == ("completed" if repair_ok else "needs_attention"), state.get("error")
    assert len(client.calls) == 3
    assert state["stages"]["review"]["total"] == 1
    path = engine.run_path(output, run_id)
    cot = engine.read_json(path / "artifacts/cot.records.json")[0]
    sft = engine.read_json(path / "artifacts/sft.records.json")[0]
    assert record_messages(cot)[-1]["content"] == cot["answer"] == sft["messages"][-1]["content"]
    assert "\n".join(cot["reasoning"]) == sft["messages"][-1]["reasoning_content"]
    assert sft["review_repair"]["status"] == cot["review_repair"]["status"]
    assert sft["review_repair"]["derived_from"] == {"target": "cot", "candidate_id": cot["id"]}
    assert sft["review_repair"]["candidate_sha256"] == engine.digest(engine.preferred_training_record(
        "sft", sft, engine.read_json(path / "recipe.json")["generation_preferences"],
        sft_output_style=engine.read_json(path / "recipe.json")["sft_output_style"]))
    assert state["quality"]["targets"]["sft"]["eligible"] == int(repair_ok)
    assert state["quality"]["targets"]["cot"]["eligible"] == int(repair_ok)


def test_production_cot_and_sft_with_unequal_goals_reuse_reviewed_canonical_rows(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft", "cot"],
        review_repair={"mode": "auto", "max_rounds": 0},
        production={"goals": {"sft": 2, "cot": 1}, "round_size": 1, "max_rounds": 3, "min_acceptance_rate": 0})

    def distinct_sample(self, unit):
        row = generated(unit)[0]
        row["messages"][0]["content"] += f" In scenario {unit['id']}?"
        row["messages"][-1]["content"] = RIGHT
        return [row]

    monkeypatch.setattr(engine.Workflow, "sft", distinct_sample)
    monkeypatch.setattr(engine.Workflow, "cot", lambda self, sample: [engine.cot_with_sft_context(sample,
        {"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
         "question": sample["messages"][:-1], "reasoning": ["Inspect only with power off."], "answer": RIGHT})])
    client = Client()
    state = engine.Workflow(output, run_id, ROOT, generator=client, judge=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["quality"]["targets"]["sft"]["eligible"] == 2
    assert state["quality"]["targets"]["cot"]["eligible"] == 1
    assert len(client.calls) == 2, "Final export must reuse existing receipts without scoring again."


def test_manual_cpt_correction_uses_exact_selected_source_and_no_model(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"], review_repair={"mode": "human"})
    monkeypatch.setattr(engine.Workflow, "cpt", lambda self, unit: [{**unit, "status": "eligible", "source_context": unit}])
    state = engine.Workflow(output, run_id, ROOT, generator=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    record = engine.read_json(engine.run_path(output, run_id) / "artifacts/cpt.records.json")[0]
    child = create_child(output, run_id, record, target="cpt", mode="human", score=.9, approved=True,
        corrected_record={"text": SOURCE})
    state = engine.Workflow(output, child, ROOT, generator=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["quality"]["targets"]["cpt"]["eligible"] == 1


def test_manual_preference_approval_is_not_fabricated_ai_dimensions(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["dpo"], review_repair={"mode": "human"})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    def pair(self, sample):
        return [{"id": sample["id"], "source_id": sample["source_id"], "source_context": sample["source_context"],
            "status": "eligible", "prompt": sample["messages"][:-1],
            "chosen": [{"role": "assistant", "content": WRONG}],
            "rejected": [{"role": "assistant", "content": "Ignore all safety conditions."}]}]
    monkeypatch.setattr(engine.Workflow, "preference", pair)
    state = engine.Workflow(output, run_id, ROOT, generator=NoCalls()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    record = engine.read_json(engine.run_path(output, run_id) / "artifacts/dpo.records.json")[0]
    child = create_child(output, run_id, record, target="dpo", mode="human", score=.95, approved=True,
        corrected_record={"chosen": [{"role": "assistant", "content": RIGHT}]})
    state = engine.Workflow(output, child, ROOT, generator=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    row = engine.read_json(engine.run_path(output, child) / "artifacts/dpo.records.json")[0]
    assert row["status"] == "eligible" and "preference" not in row
    assert row["review_repair"]["mode"] == "human"


@pytest.mark.parametrize("target", ["dpo", "orpo", "rlaif"])
def test_rejected_preference_does_not_score_negative_or_retain_old_labels(tmp_path, monkeypatch, target):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=[target],
        review_repair={"mode": "auto", "max_rounds": 0})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    monkeypatch.setattr(engine.Workflow, "preference", lambda self, sample: [{
        "id": sample["id"], "source_id": sample["source_id"], "source_context": sample["source_context"],
        "status": "eligible", "prompt": sample["messages"][:-1],
        "chosen": [{"role": "assistant", "content": WRONG}],
        "rejected": [{"role": "assistant", "content": "No inspection is needed."}],
        "preference": {"dimension": "correctness", "minimum_gap": 2,
                       "chosen": check(True), "rejected": check(False)},
        "rlaif": {"criterion": "correctness", "chosen_feedback": "Old body."}}])
    client = Client()
    state = engine.Workflow(output, run_id, ROOT, generator=client).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert len(client.calls) == 1
    assert client.calls[0]["target"] == target
    row = engine.read_json(engine.run_path(output, run_id) / f"artifacts/{target}.records.json")[0]
    assert "preference" not in row and "rlaif" not in row
    assert state["quality"]["targets"][target]["eligible"] == 0


def test_repaired_preference_gets_both_fresh_scores_only_after_chosen_passes(tmp_path, monkeypatch):
    class RepairPreference(Client):
        def chat(self, messages, **kwargs):
            data = model_request_data(messages)
            self.calls.append(deepcopy(data))
            self.usage["calls"] += 1
            if "review_feedback" in data:
                payload = deepcopy(data["candidate"])
                payload["chosen"][-1]["content"] = RIGHT
                return json.dumps({"record": payload, "quotes": [SOURCE], "uncertain": False})
            candidate = data["candidate"]
            answer = candidate.get("chosen", candidate.get("messages"))[-1]["content"]
            return json.dumps(check(answer == RIGHT))

    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["dpo"],
        review_repair={"mode": "auto", "max_rounds": 1})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    monkeypatch.setattr(engine.Workflow, "preference", lambda self, sample: [{
        "id": sample["id"], "source_id": sample["source_id"], "source_context": sample["source_context"],
        "status": "eligible", "prompt": sample["messages"][:-1],
        "chosen": [{"role": "assistant", "content": WRONG}],
        "rejected": [{"role": "assistant", "content": "No inspection is needed."}]}])
    client = RepairPreference()
    state = engine.Workflow(output, run_id, ROOT, generator=client).execute()
    assert state["status"] == "completed", state.get("error")
    assert ["review_feedback" in data for data in client.calls] == [False, True, False, False]
    assert [data["target"] for data in client.calls] == ["dpo", "dpo", "dpo", "sft"]
    row = engine.read_json(engine.run_path(output, run_id) / "artifacts/dpo.records.json")[0]
    assert row["preference"]["chosen"]["correctness"] == 5
    assert row["preference"]["rejected"]["correctness"] == 1


@pytest.mark.parametrize("changed", [False, True])
def test_manual_rlaif_retains_existing_ai_evidence_but_requires_ai_rescoring_after_edits(tmp_path, monkeypatch, changed):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["rlaif"],
        review_repair={"mode": "auto", "max_rounds": 0})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: generated(unit))
    monkeypatch.setattr(engine.Workflow, "preference", lambda self, sample: [{
        "id": sample["id"], "source_id": sample["source_id"], "source_context": sample["source_context"],
        "status": "eligible", "prompt": sample["messages"][:-1],
        "chosen": [{"role": "assistant", "content": RIGHT}], "rejected": [{"role": "assistant", "content": WRONG}]}])

    class PreferenceClient(Client):
        def chat(self, messages, **kwargs):
            data = model_request_data(messages)
            self.calls.append(deepcopy(data))
            self.usage["calls"] += 1
            native = data["candidate"]
            answer = native.get("chosen", native.get("messages"))[-1]["content"]
            return json.dumps(check(answer != WRONG))

    client = PreferenceClient()
    state = engine.Workflow(output, run_id, ROOT, generator=client, judge=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    assert len(client.calls) == 2
    original = engine.read_json(engine.run_path(output, run_id) / "artifacts/rlaif.records.json")[0]
    assert original["rlaif"]["judge_role"] == "generation"
    payload = deepcopy(engine.preferred_training_record("rlaif", original))
    if changed:
        payload["responses"][0]["response"][-1]["content"] += " Record the inspection result."
    child = create_child(output, run_id, original, target="rlaif", mode="human", score=.95,
        approved=True, corrected_record=payload)
    state = engine.Workflow(output, child, ROOT, generator=NoCalls()).execute()
    assert state["status"] == ("needs_attention" if changed else "completed"), state.get("error")
    row = engine.read_json(engine.run_path(output, child) / "artifacts/rlaif.records.json")[0]
    assert row["review_repair"]["status"] == ("rejected" if changed else "accepted")
    if changed:
        assert row["reason"] == "manual_rlaif_change_requires_ai_review"


@pytest.mark.parametrize("value", [{"mode": "other"}, {"max_rounds": -1}, {"max_rounds": True},
    {"max_rounds": 6}, {"score_threshold": float("nan")}, {"score_threshold": 2}, {"unexpected": 1}])
def test_review_configuration_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="invalid_review_repair"):
        validate_review_repair(value)
