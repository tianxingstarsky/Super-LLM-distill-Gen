"""All supervised replies and their replacement reasoning survive honest review."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.domain.assistant_message_delta import apply_assistant_message_delta
from lib.domain.review_context import project_review_request
from lib.domain.turn_review import supervised_assistant_turns, turn_review_verdict
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SOURCE = "Power must be turned off before inspection. The result is recorded after inspection."
NATIVE = "The system prompt requires returning JSON with answer and quotes. Need follow generation_style."
REASON = "断电是检查前置条件，因此先确认电源关闭才能检查；检查完成后记录结果，不把记录误当检查前提。"


def grade(keep=True):
    score = 5 if keep else 1
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": score,
            "scores": {name: score for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "来源规定先断电，之后检查再记录。" if keep else "早期助手颠倒断电前提，末轮纠正不能替它通过。"}


def messages(native=True):
    return [{"role": "user", "content": "我要检查设备，先干嘛？"},
            {"role": "assistant", "content": "先带电检查。", "reasoning_content": NATIVE if native else REASON},
            {"role": "user", "content": "检查完呢？"},
            {"role": "assistant", "content": "检查之后记录结果。", "reasoning_content": NATIVE if native else REASON}]


def trace():
    return {"task": "安排设备检查前后顺序。", "premises": ["检查前必须切断电源。", "检查后记录结果。"],
            "steps": [{"basis": "检查以断电为前置条件。", "inference": "只有满足必要条件才能开始检查。",
                       "result": "先断电，之后检查，最后记录结果。"}],
            "checks": [{"check": "核对记录与检查的先后。", "result": "记录发生在检查之后，不取代断电前提。"}],
            "limitations": []}


def reviewed(candidate, *, first_bad=False):
    turns = supervised_assistant_turns(candidate)
    return {**grade(), "turn_verdicts": [{**grade(not (first_bad and offset == 0)),
            "message_index": turn["message_index"], "content_sha256": turn["content_sha256"],
            "leakage_free": True, "critical_error": first_bad and offset == 0}
            for offset, turn in enumerate(turns)]}


def updates():
    return {"assistant_updates": [{"message_index": index,
            "content": "先确认断电，再检查。" if index == 1 else "检查完成后记录结果。",
            "reasoning_content": REASON, "reasoning_trace": trace()} for index in (1, 3)]}


def test_each_earlier_reply_can_fail_despite_perfect_aggregate_and_later_correction():
    candidate = {"messages": messages(False)}
    raw = reviewed(candidate, first_bad=True)
    original = deepcopy(raw)
    result = turn_review_verdict(raw, supervised_assistant_turns(candidate))
    assert not accepted(result) and not result["keep"]
    assert result["scores"]["correctness"] == 1 and result["aggregate_original"]["correctness"] == 5
    assert "assistant[1]" in result["reason"]
    assert raw == original


@pytest.mark.parametrize("change,error", [
    (lambda v: v.pop("turn_verdicts"), "missing_assistant_turn_reviews"),
    (lambda v: v["turn_verdicts"].pop(), "incomplete_assistant_turn_reviews"),
    (lambda v: v["turn_verdicts"].__setitem__(1, deepcopy(v["turn_verdicts"][0])), "invalid_assistant_turn_review_index"),
    (lambda v: v["turn_verdicts"][0].__setitem__("message_index", True), "invalid_assistant_turn_review_index"),
    (lambda v: v["turn_verdicts"][0].__setitem__("content_sha256", "0" * 64), "stale_assistant_turn_review"),
    (lambda v: v["turn_verdicts"][0].__setitem__("critical_error", "false"), "invalid_assistant_turn_review_boolean"),
])
def test_turn_receipts_cannot_omit_repeat_or_reuse_old_content(change, error):
    candidate = {"messages": messages(False)}
    raw = reviewed(candidate)
    change(raw)
    with pytest.raises(ValueError, match=error):
        turn_review_verdict(raw, supervised_assistant_turns(candidate))


def test_hash_binds_prefix_context_and_every_leak_flag_is_fatal():
    candidate = {"messages": messages(False)}
    turns = supervised_assistant_turns(candidate)
    changed = deepcopy(candidate)
    changed["messages"][0]["content"] = "能不能直接带电检查？"
    assert [item["content_sha256"] for item in supervised_assistant_turns(changed)] != [
        item["content_sha256"] for item in turns]
    raw = reviewed(candidate)
    raw["turn_verdicts"][0]["leakage_free"] = False
    assert not accepted(turn_review_verdict(raw, turns))
    assert accepted(turn_review_verdict(reviewed(candidate), turns))


def test_delta_requires_real_replacement_rationale_and_keeps_inputs_exact():
    old = messages()
    old[0]["content"] += " literal \\n\n"
    revised = apply_assistant_message_delta(old, updates(), required_reasoning_indices=[1, 3])
    assert revised[0] == old[0] and revised[2] == old[2]
    assert all(revised[index]["reasoning_content"] == REASON for index in (1, 3))
    assert all("reasoning_trace" not in message for message in revised)
    missing = updates()
    missing["assistant_updates"].pop()
    with pytest.raises(ValueError, match="review_repair_reasoning_coverage_missing"):
        apply_assistant_message_delta(old, missing, required_reasoning_indices=[1, 3])
    empty = updates()
    empty["assistant_updates"][0]["reasoning_content"] = "检查通过"
    with pytest.raises(ValueError, match="authored_reasoning_invalid_text"):
        apply_assistant_message_delta(old, empty, required_reasoning_indices=[1, 3])


def test_legacy_delta_and_review_projection_replay_their_old_behavior():
    old = messages()
    delta = {"assistant_updates": [{"message_index": 1, "content": "先断电。"}]}
    assert "reasoning_content" not in apply_assistant_message_delta(old, delta)[1]
    data = {"target": "multiturn", "candidate": {"messages": old},
            "qa_contract": {"question": "检查", "provider_reasoning_history": [NATIVE]}}
    legacy = project_review_request({"review_context_protocol_version": 1}, data, phase="repair")
    current = project_review_request({"review_context_protocol_version": 2}, data, phase="repair")
    assert "reasoning_content" not in legacy["candidate"]["messages"][1]
    assert current["candidate"]["messages"][1]["reasoning_content"] == NATIVE
    assert "provider_reasoning_history" not in current["qa_contract"]


class Probe(WorkflowReviewRepair):
    def __init__(self, responses):
        self.recipe = {"review_context_protocol_version": 2, "turn_review_protocol_version": 1,
            "reasoning_revision_protocol_version": 1, "training_surface_protocol_version": 2,
            "review_repair": {"mode": "auto", "max_rounds": 1}, "reasoning_required": True}
        self.state = {"stages": {"review": {}}}
        self.responses, self.calls = list(responses), []
    def save(self, **kwargs):
        pass
    def ask(self, key, role, prompt_id, data, **kwargs):
        self.calls.append(deepcopy(data))
        return self.responses.pop(0)
    def invalidate_checkpoint(self, key):
        self.invalidated = key
    def _review_evidence(self, row):
        return {"teacher_evidence": SOURCE, "source_kind": "document", "source_id": "paper"}


def row():
    return {"id": "sample", "source_id": "paper", "status": "eligible", "messages": messages(),
            "training_surface_protocol_version": 2,
            "reasoning_origin": "provider_reasoning", "reasoning_summary": {"tokens": 123},
            "turn_reviews": [{"end_message_index": index, "reasoning_origin": "provider_reasoning"}
                             for index in (1, 3)]}


def test_repair_preserves_native_audit_but_exports_new_model_authored_reasoning():
    original = row()
    frozen = canonical(original)
    probe = Probe([{"record": updates(), "quotes": [SOURCE], "uncertain": False}])
    repaired, issue = probe._repair_content("multiturn", original, probe._review_evidence(original),
                                           ["sample", "fix"], grade(False), "")
    assert issue is None and canonical(original) == frozen
    assert repaired["reasoning_origin"] == "prompt_revision"
    assert all(repaired["messages"][index]["reasoning_content"] == REASON for index in (1, 3))
    assert NATIVE in canonical(repaired["provider_reasoning_history"])
    assert len(repaired["reasoning_revision_audits"]) == 2
    assert probe.calls[0]["required_reasoning_indices"] == [1, 3]
    assert probe.calls[0]["candidate"]["messages"][1]["reasoning_content"] == NATIVE


@pytest.mark.parametrize("target", ["sft", "multiturn"])
def test_sft_and_multiturn_refuse_the_old_answer_only_repair(target):
    delta = {"assistant_updates": [{"message_index": index, "content": "先断电再检查。"} for index in (1, 3)]}
    probe = Probe([{"record": delta, "quotes": [SOURCE], "uncertain": False}])
    original = row()
    repaired, issue = probe._repair_content(target, original, probe._review_evidence(original), ["sample"], grade(False), "")
    assert issue == "authored_reasoning_invalid_text" and repaired == original


def test_drop_export_preference_cannot_erase_the_original_repair_obligation():
    delta = {"assistant_updates": [{"message_index": index, "content": "先断电再检查。"} for index in (1, 3)]}
    probe = Probe([{"record": delta, "quotes": [SOURCE], "uncertain": False}])
    probe.recipe.update(sft_output_style="drop", reasoning_required=False)
    original = row()
    assert all("reasoning_content" not in message for message in probe._review_payload("sft", original)["messages"])
    repaired, issue = probe._repair_content("sft", original, probe._review_evidence(original), ["sample"], grade(False), "")
    assert issue == "authored_reasoning_invalid_text" and repaired == original
    assert probe.calls[0]["required_reasoning_indices"] == [1, 3]


def test_standard_generation_leak_enters_bounded_repair_and_gets_full_per_turn_assessment():
    original = row()
    new_payload = {"messages": apply_assistant_message_delta(original["messages"], updates(), required_reasoning_indices=[1, 3])}
    probe = Probe([{"record": updates(), "quotes": [SOURCE], "uncertain": False}, reviewed(new_payload)])
    result = probe._review_repair_item({"target": "multiturn", "record": original})[0]
    assert result["status"] == "eligible"
    assert result["review_repair"]["attempts"] == 1
    assert result["review_repair"]["initial_surface_issue"] == "training_surface_internal_process_reference"
    assert len(probe.calls) == 2 and len(probe.calls[-1]["supervised_assistant_turns"]) == 2
    assert all(result["messages"][index]["reasoning_content"] == REASON for index in (1, 3))


def test_required_reasoning_cannot_be_approved_without_reading_any():
    original = row()
    for message in original["messages"]:
        message.pop("reasoning_content", None)
    probe = Probe([])
    result = probe._review_score("multiturn", original, probe._review_evidence(original), ["sample"], "")
    assert not accepted(result) and result["reason"] == "review_repair_reasoning_missing" and not probe.calls


def test_incomplete_turn_assessment_has_one_format_retry_and_preserves_raw_receipt():
    original = row()
    original["messages"] = messages(False)
    probe = Probe([grade(), reviewed({"messages": original["messages"]})])
    result = probe._review_score("multiturn", original, probe._review_evidence(original), ["sample"], "")
    assert accepted(result) and len(probe.calls) == 2
    retry = original["assistant_turn_review_retry"]
    assert retry["attempts"] == 2 and retry["status"] == "schema_valid"
    assert retry["schema_failures"][0]["code"] == "missing_assistant_turn_reviews"
    assert len(retry["schema_failures"][0]["response_sha256"]) == 64
    assert probe.calls[1]["format_repair"]["code"] == "missing_assistant_turn_reviews"
    assert not hasattr(probe, "invalidated")


def test_bad_turn_format_cannot_loop_or_silently_become_approval():
    original = row()
    original["messages"] = messages(False)
    probe = Probe([grade(), grade()])
    with pytest.raises(ValueError, match="missing_assistant_turn_reviews"):
        probe._review_score("multiturn", original, probe._review_evidence(original), ["sample"], "")
    assert len(probe.calls) == 2 and original["assistant_turn_review_retry"]["status"] == "invalid_after_retry"
    assert not hasattr(probe, "invalidated")


def test_revision_refuses_unchanged_native_reasoning_on_a_changed_answer():
    value = updates()
    for update in value["assistant_updates"]:
        update["reasoning_content"] = NATIVE
    probe = Probe([{"record": value, "quotes": [SOURCE], "uncertain": False}])
    original = row()
    result, issue = probe._repair_content("multiturn", original, probe._review_evidence(original), ["sample"], grade(False), "")
    assert issue == "review_repair_reasoning_missing" and result == original


def test_agent_generic_repair_cannot_drop_existing_reasoning_to_bypass_leak_checks():
    original = row()
    candidate = {"messages": deepcopy(original["messages"])}
    for message in candidate["messages"]:
        if message["role"] == "assistant":
            message.update(content="先断电再检查。")
            message.pop("reasoning_content", None)
    probe = Probe([{"record": candidate, "quotes": [SOURCE], "uncertain": False}])
    probe.recipe["reasoning_required"] = False
    repaired, issue = probe._repair_content("agent", original, probe._review_evidence(original), ["sample"], grade(False), "")
    assert repaired == original and issue == "review_repair_reasoning_missing"


def test_terminal_single_turn_keeps_original_reason_and_native_without_requesting_review():
    from lib.infrastructure.workflow_production import _quality_assessment_valid
    original = row()
    original.update(status="quarantined", reason="multiturn_natural_end_insufficient_turns", completed_turns=1)
    original["messages"] = original["messages"][:2]
    before = deepcopy(original)
    probe = Probe([])
    probe.recipe["multiturn_terminal_review_version"] = 1
    probe._review_evidence = lambda _: pytest.fail("A terminal structure cannot be repaired from evidence.")
    reviewed, = probe._review_repair_item({"target": "multiturn", "record": original})
    assert original == before and reviewed["messages"] == original["messages"]
    assert reviewed["reason"] == "multiturn_natural_end_insufficient_turns"
    assert reviewed["review_repair"]["assessment"] == "not_performed_terminal_generation"
    assert reviewed["review_repair"]["original_reason"] == original["reason"]
    assert reviewed["review_repair"]["attempts"] == 0 and reviewed["review_repair"]["score"] is None
    assert "verdict" not in reviewed["review_repair"] and not probe.calls
    assert not _quality_assessment_valid(reviewed)


def test_old_frozen_terminal_single_turn_still_uses_the_old_review_path():
    original = row()
    original.update(status="quarantined", reason="multiturn_natural_end_insufficient_turns", completed_turns=1)
    probe = Probe([])
    def old_evidence(_):
        raise ValueError("legacy_evidence_path_unchanged")
    probe._review_evidence = old_evidence
    with pytest.raises(ValueError, match="legacy_evidence_path_unchanged"):
        probe._review_repair_item({"target": "multiturn", "record": original})


def test_protocol_one_real_engine_keeps_revised_reasoning_and_reuses_paid_checkpoints(tmp_path, monkeypatch):
    from lib.infrastructure import training_workflow as engine
    from tests.test_workflow_production import model_request_data

    class OfflineClient:
        model = "offline-complete-turn-review"
        def __init__(self):
            self.calls, self.usage = [], {"calls": 0}
        def chat(self, wire, **kwargs):
            data = model_request_data(wire)
            self.calls.append(deepcopy(data))
            self.usage["calls"] += 1
            if "review_feedback" in data:
                assert data["required_reasoning_indices"] == [1, 3]
                return json.dumps({"record": updates(), "quotes": [SOURCE], "uncertain": False})
            assert data["claim_review_protocol_version"] == 1
            value = {"claim_review_protocol_version": 1, "scope": data["scope"],
                "coverage": "complete", "checked_fields": list(data["target_fields"]),
                "claim_checks": [{"field": field, "quote": text, "status": "supported",
                    "severity": "material", "basis": {"kind": "logic", "evidence": [],
                        "derivation": "断电是检查的实际前置条件；记录发生在检查之后，当前解释保持这些依赖。"}}
                    for field, text in data["target_fields"].items()], "verdict": grade()}
            return json.dumps(value)

    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], reasoning_required=True,
        review_repair={"mode": "auto", "max_rounds": 1}, sft_output_style="separated")
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    from lib.prompts import get, render
    recipe["claim_review_protocol_version"] = 1
    recipe.pop("claim_review_quote_matching_version", None)
    recipe.pop("claim_reference_policy_version", None)
    recipe["node_prompt_templates"]["review"].pop("workflow.review_reference", None)
    frozen_score = get("workflow.review_score", version="1.12.0")
    recipe["node_prompt_templates"]["review"]["workflow.review_score"] = render(frozen_score)
    recipe["prompts"]["workflow.review_score"] = {"version": frozen_score.version,
                                                 "sha256": engine.digest(frozen_score.template)}
    engine.atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    engine.atomic_json(path / "state.json", state)
    def generate(self, unit):
        return [{**row(), "id": unit["id"], "source_id": unit["source_id"], "source_context": deepcopy(unit),
                 "quotes": [SOURCE], "evidence_level": "source_and_model_assessed"}]
    monkeypatch.setattr(engine.Workflow, "sft", generate)
    client = OfflineClient()
    root = Path(__file__).resolve().parents[1]
    worker = engine.Workflow(output, run_id, root, generator=client)
    assert worker.recipe["review_context_protocol_version"] == 2
    assert worker.recipe["turn_review_protocol_version"] == 1
    assert worker.recipe["claim_review_protocol_version"] == 1
    result = worker.execute()
    assert result["status"] == "completed", result.get("error")
    records = engine.read_json(worker.path / "artifacts/sft.records.json")
    assert len(records) == 1 and records[0]["status"] == "eligible"
    assert records[0]["review_repair"]["attempts"] == 1 and len(client.calls) == 3
    assert [call["scope"]["message_index"] for call in client.calls[1:]] == [1, 3]
    assert client.calls[1]["messages"] == records[0]["messages"][:2]
    assert client.calls[2]["messages"] == records[0]["messages"]
    for item in records[0]["claim_review"]["assessments"]:
        reference = item["checkpoint"]
        checkpoint = engine.read_json(worker.path / "checkpoints" / reference["stage"] /
                                      (reference["checkpoint_sha256"] + ".json"))
        assert checkpoint["data"] == item["review"]["raw_response"]
        assert checkpoint["sha256"] == reference["response_sha256"]
    exported = json.loads((worker.path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    assert all(exported["messages"][index]["reasoning_content"] == REASON for index in (1, 3))
    assert NATIVE not in canonical(exported) and NATIVE in canonical(records[0]["provider_reasoning_history"])
    assert records[0]["reasoning_origin"] == "prompt_revision"
    resumed = OfflineClient()
    assert engine.Workflow(output, run_id, root, generator=resumed).execute(resume_run=True)["status"] == "completed"
    assert not resumed.calls
