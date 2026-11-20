"""Exercise tutoring, final readout and offline training admission through the batch runner."""

import json
from pathlib import Path

import pytest

from lib.contexts.workflows.domain.guided_calculation_plan import calculation_plan_digest
from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases
from lib.contexts.workflows.infrastructure.guided_llama import DIRECT_ANSWER_GRAMMARS, GuidedChatEndpoint
from test_guided_solver_receipts import endpoint


CONFIGS = Path(__file__).resolve().parents[1] / "configs"


@pytest.fixture
def run_ribbon(tmp_path, monkeypatch):
    def run(readouts, *, worked=False, handoff=False, reported_continuation=None, protocol="4.10.0"):
        task = json.loads((CONFIGS / "guided-calculation-plan.example.jsonl").read_bytes())
        registry = CONFIGS / "guided-calculation-plan.receipts.json"
        if worked:
            task["question"] = task["question"].replace("Give only the number.", "Show your work.")
            task["answer_requirement"] = "worked_solution"
            del task["direct_answer_syntax"]
            plan = task["verified_calculation_plan"]
            plan["receipt_id"] = calculation_plan_digest(task, plan["steps"])
            registry = tmp_path / "worked-receipts.json"
            registry.write_text(json.dumps({"schema": "guided-teaching-verification/v1",
                                            "receipts": [plan["receipt_id"]]}), encoding="utf-8")
        source = tmp_path / "tasks.jsonl"
        source.write_text(json.dumps(task), encoding="utf-8")
        # The estimate is deliberately right for the worked task: a number alone cannot satisfy it.
        script = [("12" if worked else "99", "stop"), (json.dumps("3*2;18-6"), "stop"), *readouts]
        sent = []

        def serve(ep, url, body=None):
            if url.endswith("/slots"):
                return [{"n_ctx": 98304}]
            if url.endswith("/models"):
                return {"data": [{"id": ep.model}]}
            if url.endswith("/apply-template"):
                return {"prompt": "\n".join(m["content"] for m in body["messages"])}
            if url.endswith("/tokenize"):
                return {"tokens": list(body["content"].encode("utf-8"))}
            assert url.endswith("/v1/chat/completions")
            assert ep.model == ("strong-reader" if handoff and len(sent) == 4 else "student")
            content, finish = script[len(sent)]
            sent.append(body)
            reported = reported_continuation if ep.model == "strong-reader" and reported_continuation else ep.model
            return {"model": reported, "choices": [{"message": {"content": content}, "finish_reason": finish}]}

        monkeypatch.setattr(GuidedChatEndpoint, "http", serve)
        output = tmp_path / "run"
        endpoints = {"student": endpoint("student", 8121), "coach": endpoint("coach", 8122)}
        if handoff:
            endpoints["continuation"] = endpoint("strong-reader", 8123)
        summary = run_guided_cases(source, output, endpoints, {"version": "test"}, 3, workers=1,
                                   until_correct=False, protocol_version=protocol, verified_step_receipts=registry)
        assert len(sent) == len(script)
        assert all(packet["grammar"] == DIRECT_ANSWER_GRAMMARS["keyed_rational"] for packet in sent[2:])
        return source, output, summary, sent

    return run


@pytest.mark.parametrize("protocol", ["4.10.0", "4.10.7"])
def test_wrong_readout_then_corrected_by_same_solver_preserves_both_receipts(run_ribbon, protocol):
    source, output, summary, sent = run_ribbon([("6", "stop"), ("12", "stop")], protocol=protocol)
    assert summary["counts"]["accepted"] == 1
    receipt_folder = (output / json.loads((output / "accepted.jsonl").read_bytes())["case_archive"]).parent
    if protocol == "4.10.7":
        assert summary["behavior_predictions"]["observed_student_turns"] == 0
        assert not list(receipt_folder.glob("*-behavior_prediction.json"))
    case = json.loads((receipt_folder / "case.json").read_bytes())
    delivery = case["calculation_tutoring"]["final_delivery"]
    assert delivery["prior"]["check"]["issue"] == "calculation_readout_mismatch"
    assert delivery["check"]["answer_matches"] is True
    assert sent[3]["messages"][-2] == {"role": "assistant", "content": "6"}
    for packet, item in zip(sent[2:], [delivery["prior"], delivery]):
        raw = json.loads((receipt_folder / item["receipt"]).read_bytes())
        assert raw["request"] == packet and raw["response"]["content"] == item["response"]["content"]
    target_path = output / "training-targets.jsonl"
    frozen = target_path.read_bytes()
    target = json.loads(frozen)
    assert target["messages"][-1]["content"] == "12"
    assert target["guided_training_target"]["answer_assembly"] == "student_readout"
    assert target["guided_training_target"]["answer_receipt"] == delivery["receipt"]
    assert target["guided_training_target"]["independent_solution"] is False
    assert run_guided_cases(source, output, {}, {}, 3, export_only=True) == summary
    assert target_path.read_bytes() == frozen and len(sent) == 4


def test_strong_reader_only_delivers_after_two_checked_weak_mismatches(run_ribbon):
    source, output, summary, sent = run_ribbon([("6", "stop"), ("10", "stop"), ("12", "stop")], handoff=True)
    assert summary["counts"]["accepted"] == 1 and len(sent) == 5
    trained = json.loads((output / "training-targets.jsonl").read_bytes())
    archive = output / trained["guided_training_target"]["case_archive"]
    case = json.loads(archive.read_bytes())
    delivery = case["calculation_tutoring"]["final_delivery"]
    assert delivery["role"] == "verified_strong_answer" and delivery["expected_reader"] == "strong-reader"
    assert delivery["prior"]["prior"]["response"]["content"] == "6"
    assert delivery["prior"]["response"]["content"] == "10" and delivery["public_answer"] == "12"
    assert [m["role"] for m in sent[-1]["messages"]] == ["system", "user", "tool"]
    for packet, item in zip(sent[2:], (delivery["prior"]["prior"], delivery["prior"], delivery)):
        raw = json.loads((archive.parent / item["receipt"]).read_bytes())
        assert raw["request"] == packet and raw["response"]["model"] == item["response"]["model"]
    # The two weak failures both remain prerequisites for the strong result on export.
    path = archive.parent / delivery["prior"]["prior"]["receipt"]
    raw = json.loads(path.read_bytes())
    raw["response"]["content"] = "12"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="archived model receipt"):
        run_guided_cases(source, output, {}, {}, 3, export_only=True)


@pytest.mark.parametrize("readouts,reported,issue", [
    ([("6", "stop"), ("10", "stop"), ("12", "stop")], "wrong-reader", "solver_identity_unverified"),
    ([("6", "stop"), ("10", "stop"), ("6", "stop")], None, "calculation_readout_mismatch"),
    ([("6", "stop"), ("10", "length")], None, "incomplete_calculation_readout"),
])
def test_configured_strong_reader_never_recurses_or_takes_over_an_incomplete_response(run_ribbon, readouts, reported, issue):
    _, output, summary, sent = run_ribbon(readouts, handoff=True, reported_continuation=reported)
    assert summary["counts"]["awaiting_guidance"] == 1 and len(sent) == len(readouts) + 2
    row = json.loads((output / "awaiting_guidance.jsonl").read_bytes())
    assert row["continuation"]["reason"] == issue and not (output / "training-targets.jsonl").read_bytes()


@pytest.mark.parametrize("readouts,issue", [
    ([("6", "stop"), ("10", "stop")], "calculation_readout_mismatch"),
    ([("6", "stop"), ("12", "length")], "incomplete_calculation_readout"),
    ([("12", "length")], "incomplete_calculation_readout"),
])
def test_failed_or_unfinished_readout_cannot_train_even_with_checked_steps(run_ribbon, readouts, issue):
    _, output, summary, _ = run_ribbon(readouts)
    assert summary["counts"]["accepted"] == 0 and summary["counts"]["awaiting_guidance"] == 1
    assert (output / "training-targets.jsonl").read_bytes() == b""
    held = json.loads((output / "awaiting_guidance.jsonl").read_bytes())
    case = json.loads((output / held["case_archive"]).read_bytes())
    course = case["calculation_tutoring"]
    assert [step["value"] for step in course["steps"]] == ["6", "12"]
    assert course["final_delivery"]["check"]["issue"] == issue
    assert course["final_delivery"]["public_answer"] == ""


def test_worked_answer_records_verified_operations_and_numeric_readout_separately(run_ribbon):
    _, output, summary, _ = run_ribbon([("12", "stop")], worked=True)
    assert summary["counts"]["accepted"] == 1
    training = json.loads((output / "training-targets.jsonl").read_bytes())
    info = training["guided_training_target"]
    case = json.loads((output / info["case_archive"]).read_bytes())
    course = case["calculation_tutoring"]
    assert case["attempts"][0]["student"]["content"] == "12"
    assert course["final_delivery"]["response"]["content"] == "12"
    assert info["answer_assembly"] == "verified_steps_with_student_readout"
    assert info["operations"] == [
        {"id": key, "student_receipt": course["actions"][0]["receipt"], "operation_disclosed": False,
         "operation_origin": "student_proposal", "value_source": "exact_calculator"}
        for key in ("cut", "remaining")]
    public = training["messages"][-1]
    assert public["content"] == ("ribbon used for strips: 3*2 = 6 metres.\n"
                                 "remaining ribbon: 18-6 = 12 metres.")
    assert public["reasoning_content"] == "\n".join(step["text"] for step in course["steps"])
    assert public["content"] == course["final_delivery"]["public_answer"]


@pytest.mark.parametrize("mutate,expected", [
    ("step", "reviewed operations"), ("packet", "archived model receipt"), ("receipt", "archived model receipt"),
])
def test_reexport_rejects_altered_accepted_step_request_or_readout_receipt(run_ribbon, mutate, expected):
    source, output, _, _ = run_ribbon([("12", "stop")])
    targets = output / "training-targets.jsonl"
    before = targets.read_bytes()
    archive = output / json.loads(before)["guided_training_target"]["case_archive"]
    case = json.loads(archive.read_bytes())
    course = case["calculation_tutoring"]
    if mutate == "step":
        course["steps"][0]["unit"] = "centimetres"
        archive.write_text(json.dumps(case), encoding="utf-8")
    else:
        name = course["actions"][0]["receipt"] if mutate == "packet" else course["final_delivery"]["receipt"]
        path = archive.parent / name
        event = json.loads(path.read_bytes())
        if mutate == "packet":
            event["request"]["messages"][-1]["content"] = "The result is twelve."
        else:
            event["response"]["content"] = "6"
        path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        run_guided_cases(source, output, {}, {}, 3, export_only=True)
    assert targets.read_bytes() == before
