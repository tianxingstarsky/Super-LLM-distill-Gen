from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from lib.contexts.workflows.domain.guided_answers import validate_source
from lib.contexts.workflows.domain.guided_teaching_steps import complete_solution_digest
from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases
from lib.contexts.workflows.infrastructure.guided_llama import DIRECT_ANSWER_GRAMMARS, GuidedChatEndpoint


def endpoint(model, port):
    return GuidedChatEndpoint(f"http://localhost:{port}/v1", model, None, 32768, local=True, context=98304,
                              pool=SimpleNamespace(acquire=nullcontext))


@pytest.mark.parametrize("handoff", [False, True])
def test_native_final_receipt_follows_the_reviewed_solver_and_requested_format(tmp_path, monkeypatch, handoff):
    premise = "The six jobs are A, B, C, D, E and F"
    question = premise + (". Return only the number of jobs." if handoff else ". Show why the count is six.")
    task = {"id": "jobs", "question": question, "answer_requirement": "final_only", "direct_answer_syntax": "number",
            "source_reasoning": "Count each of the six distinct job names exactly once.",
            "gold": {"kind": "number", "value": "6"}}
    source = tmp_path / "tasks.jsonl"
    source.write_text(json.dumps(task), encoding="utf8")
    registry = tmp_path / "verified.json"
    registry.write_text(json.dumps({"schema": "guided-teaching-verification/v1",
                                    "receipts": [complete_solution_digest(validate_source(task))]}), encoding="utf8")

    small, large = "qwen3.5-0.8b", "qwen3.5-9b"
    # The larger solver and the private reviewer share the same model server.
    endpoints = {"student": endpoint(small, 8121), "coach": endpoint(large, 8122)}
    if handoff:
        endpoints["continuation"] = endpoint(large, 8122)
    solved = "Counting each of the six distinct entries gives six."
    accepted = {"decision": "supported", "diagnosis": "The count uses all six entries.",
                "reusable": [], "independent_approaches": []}
    wrong = "There are five jobs.\nFinal answer: 5"
    revise = {**accepted, "decision": "revise:final_answer", "diagnosis": "Check the count."}
    hint = "Cross off one name for each distinct job."
    bridge = premise + "."
    script = [
        (small, "4"),
        (large, {"decision": "offer", "question_quote": premise, "bridge": bridge, "hint": hint,
                 "reason": "Check the membership of each name."}),
        (large, {"decision": "release", "citations": [{"evidence_id": "question", "quote": premise}],
                 "reason": "This checks membership.",
                 "pairing": {"student_fit": True, "evidence_support": True, "history_fit": "first"}}),
        (small, wrong), (large, revise), (small, wrong), (large, revise),
        (large, solved + "\nFinal answer: 6"), (large, {**accepted, "anchored_contradiction": []}),
    ] if handoff else [(small, solved + "\nFinal answer: 6"), (large, accepted)]
    chats, finals = [], []
    public = "6" if handoff else solved + "\nFinal answer: 6"

    def serve(ep, url, body=None):
        if url.endswith("/slots"):
            return [{"n_ctx": 98304}]
        if url.endswith("/models"):
            return {"data": [{"id": ep.model}]}
        if url.endswith("/apply-template"):
            if "model" in body:
                assert body["model"] == ep.model
            return {"prompt": "\n".join(m["content"] for m in body["messages"]) + "\nAssistant\n<think>\n"}
        if url.endswith("/tokenize"):
            return {"tokens": list(body["content"].encode("utf8"))}
        if url.endswith("/completion"):
            finals.append((ep, body))
            return {"content": public, "stop_type": "eos", "tokens_evaluated": len(body["prompt"]), "tokens_predicted": 5}
        assert url.endswith("/v1/chat/completions")
        model, reply = script[len(chats)]
        assert model == body["model"]
        chats.append(body)
        if isinstance(reply, dict) and reply["decision"] == "release":
            proposed = json.loads(body["messages"][-1]["content"])["proposed_next_teacher_disclosure"]
            reply = {**reply, "bridge": proposed["bridge"]}
        return {"model": model, "choices": [{"message": {"content": reply if isinstance(reply, str) else json.dumps(reply)},
                                             "finish_reason": "stop"}]}

    monkeypatch.setattr(GuidedChatEndpoint, "http", serve)
    output = tmp_path / "run"
    options = dict(workers=1, until_correct=False, verified_step_receipts=registry)
    if handoff:
        options["protocol_version"] = "4.9.0"
    summary = run_guided_cases(source, output, endpoints, {"version": "test"}, 2, **options)
    assert summary["counts"]["accepted"] == 1
    assert json.loads((output / "manifest.json").read_bytes())["template_version"] == "4.9.0"
    assert len(chats) == len(script) and len(finals) == 1
    solver = endpoints["continuation" if handoff else "student"]
    ep, body = finals[0]
    assert ep is solver and body["model"] == solver.model
    assert body.get("grammar") == (DIRECT_ANSWER_GRAMMARS["number"] if handoff else None)
    row = json.loads((output / "accepted.jsonl").read_bytes())
    author = "strong_continuation" if handoff else "student"
    assert row["final_answer_provenance"]["author"] == author
    assert row["final_answer_provenance"]["model"] == solver.model
    if handoff:
        assert row["continuation_provenance"]["formal_answer_author"] == author
        assert row["continuation_provenance"]["independent_solution"] is False
    trace = row["messages"][-1]["reasoning_content"]
    assert bytes(body["prompt"]).decode("utf8").endswith("<think>\n" + trace + "\n</think>\n\n")
    assert row["messages"][-1]["content"] == public
    expected_file = "004-strong_final_answer.json" if handoff else "001-final_answer.json"
    assert row["final_answer_provenance"]["receipt"] == expected_file
    case_dir = (output / row["case_archive"]).parent
    receipt = json.loads((case_dir / expected_file).read_bytes())
    assert receipt["request"] == body and receipt["response"]["model"] == solver.model
    assert receipt["response"]["model_identity_source"] == "single_served_model"
    assert not list(case_dir.glob("*-teacher_final_*.json"))
    assert run_guided_cases(source, output, endpoints, {"version": "test"}, 2, **options) == summary
    assert len(chats) == len(script) and len(finals) == 1


@pytest.mark.parametrize("reported,expected,origin", [
    ("solver", "solver", "response_model"), ("other", "other", "response_model"), (None, None, "unreported"),
])
def test_multi_model_native_response_records_the_served_identity(reported, expected, origin):
    ep = endpoint("solver", 8121)
    ep.configure_sampling("4.9.0")

    def serve(url, body=None):
        if url.endswith("/slots"):
            return [{"n_ctx": 98304}]
        if url.endswith("/models"):
            return {"data": [{"id": "solver"}, {"id": "other"}]}
        assert url.endswith("/completion")
        return {"content": "6", "stop_type": "eos", "tokens_evaluated": 1, "tokens_predicted": 1,
                **({"model": reported} if reported else {})}

    ep.http = serve
    ep.validate()
    response = ep.complete({"model": "solver", "prompt": [123]})
    assert response["model"] == expected and response["model_identity_source"] == origin


def test_solver_continuation_requires_native_transport_before_any_generation(tmp_path, monkeypatch):
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps({"id": "sum", "question": "Add 2 and 3.", "gold": {"kind": "number", "value": "5"}}),
                      encoding="utf8")
    solver = GuidedChatEndpoint("https://provider.example/v1", "solver", None, 32768,
                                local=False, context=98304, pool=None)
    monkeypatch.setattr(GuidedChatEndpoint, "http", lambda *args: pytest.fail("model service was contacted"))
    with pytest.raises(ValueError, match="student endpoint requires a native llama.cpp server"):
        run_guided_cases(source, tmp_path / "run", {"student": solver, "coach": solver},
                         {"version": "test"}, 1, protocol_version="4.9.0")
