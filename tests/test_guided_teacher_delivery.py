import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from lib.contexts.workflows.application.compose_guided_final_answer import compose_guided_final_answer
from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases
from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint
from lib.contexts.workflows.domain.guided_teacher_delivery import inspect_teacher_delivery, requested_delivery
from lib.contexts.workflows.domain.guided_teaching_steps import (
    certified_complete_disclosure, complete_solution_digest, verified_steps_digest,
)

def test_worked_delivery_cannot_emit_the_empty_explanation_and_evidence_seen_in_a_live_response():
    incomplete = {"decision": "deliver", "explanation": "", "conclusion": "1250 ounces",
                  "evidence_ids": [], "reason": ""}
    worked = {**incomplete, "explanation": "150 packets give 37500 ml. Dividing by 30 gives 1250 ounces.",
              "evidence_ids": ["a1.s6", "a1.s15"]}
    insufficient = {"decision": "insufficient", "explanation": "", "conclusion": "", "evidence_ids": [],
                    "reason": "The conversion factor is missing from the reviewed steps."}
    steps = [{"id": "a1.s6", "text": "150 packets of 250 ml give 37500 ml."},
             {"id": "a1.s15", "text": "37500 ml divided by 30 ml per ounce gives 1250 ounces."}]
    schemas = []

    def capture(role, attempt, messages, schema):
        assert role == "teacher_final_answer"
        assert len(json.loads(messages[-1]["content"])["reviewed_student_steps"]) == 2
        schemas.append(Draft202012Validator(schema))
        return {"content": json.dumps(incomplete), "finish_reason": "stop"}

    source = {"gold": {"kind": "number", "value": "1250"}}
    for version, instruction in (("4.8.0", "Show why the result follows."),
                                 ("4.8.1", "Show why the result follows."),
                                 ("4.8.1", "Give the number only.")):
        outcome = compose_guided_final_answer(capture, {**source, "question": instruction}, 1, steps,
                                              "1250", protocol_version=version)
        assert outcome["check"]["issue"] == "teacher_final_unbound_evidence"
    old, explained, final_only = schemas
    assert old.is_valid(incomplete) and not explained.is_valid(incomplete)
    assert explained.is_valid(worked) and not explained.is_valid({**worked, "evidence_ids": []})
    assert not explained.is_valid({**worked, "evidence_ids": ["unreviewed"]})
    assert not explained.is_valid({**worked, "explanation": ""})
    assert not final_only.is_valid(worked) and final_only.is_valid({**worked, "explanation": ""})
    assert explained.is_valid(insufficient) and final_only.is_valid(insufficient)
    assert not explained.is_valid({**insufficient, "evidence_ids": ["a1.s6"]})
    assert not explained.is_valid({**insufficient, "reason": ""})


def test_full_disclosure_needs_its_own_attestation_for_this_task_and_worked_solution():
    source = {"id": "count", "question": "How many pairs are in six items?", "gold": {"kind": "number", "value": "3"},
              "task_system": "Use disjoint pairs.", "source_reasoning": "Partition the six distinct items into pairs of two."}
    step = verified_steps_digest(source["id"], source["question"], source["gold"], [])
    assert certified_complete_disclosure(source, {step}) is None
    receipt = complete_solution_digest(source)
    assert certified_complete_disclosure(source, {receipt}) == {
        "id": receipt, "worked_solution": source["source_reasoning"], "answer": "3"}
    for change in ({"source_reasoning": "Pair every item with every other item."}, {"task_system": "Count all pairings."}):
        assert certified_complete_disclosure({**source, **change}, {receipt}) is None
    key_only = {**source, "source_reasoning": "3"}
    assert certified_complete_disclosure(key_only, {complete_solution_digest(key_only)}) is None


def test_bill_subtotal_is_distinct_from_explicit_final_total():
    task = {"question": "Explain how the dinner bill includes a 20% tip.",
            "gold": {"kind": "number", "value": "108"}}
    evidence = {"s1": "The four meals cost 80 and the appetizer costs 10. Add a tip of 18."}
    form = requested_delivery(task)
    submission = {"decision": "deliver", "evidence_ids": ["s1"], "reason": "", "conclusion": "108",
                  "explanation": "The total is 90 before tip. Adding the 18 tip gives 108."}
    check, public = inspect_teacher_delivery(submission, task, evidence, form)
    assert check["format_ok"] and submission["explanation"] in public
    wrong = {**submission, "explanation": "The final answer is 90."}
    check, public = inspect_teacher_delivery(wrong, task, evidence, form)
    assert not check["format_ok"] and not public


def test_teacher_cannot_cite_private_steps_that_are_absent_from_the_question():
    source = {"question": "Reconstruct the entire sequence from the position constraints.",
              "gold": {"kind": "exact", "value": "E → C → B → D → A"}}
    steps = {"archive.s1": "C is before D.", "archive.s2": "The unique order is E → C → B → D → A."}
    submission = {"decision": "deliver", "reason": "", "conclusion": source["gold"]["value"],
                  "evidence_ids": ["archive.s1"],
                  "explanation": "This matches the final conclusion in archive.s2."}
    original, original_public = inspect_teacher_delivery(submission, source, steps, "worked_solution")
    assert original["format_ok"] and submission["explanation"] in original_public
    check, public = inspect_teacher_delivery(submission, source, steps, "worked_solution", protect_evidence_ids=True)
    assert check["issue"] == "teacher_final_contains_private_evidence_id" and public == ""


def test_teacher_can_repeat_the_users_identifiers_without_matching_a_private_prefix():
    source = {"question": "Read the public symbols archive.s1 and archive.s20. Return only archive.s20.",
              "gold": {"kind": "exact", "value": "archive.s20"}}
    steps = {"archive.s1": "The public symbols are printed on the label.",
             "archive.s2": "The selected public symbol ends in 20."}
    submission = {"decision": "deliver", "reason": "", "conclusion": "archive.s20",
                  "evidence_ids": ["archive.s1"], "explanation": ""}
    check, public = inspect_teacher_delivery(submission, source, steps, "final_only", protect_evidence_ids=True)
    assert check["answer_matches"] and public == "archive.s20"
    source["question"] = "In archive.s1, explain why the public symbol archive.s20 was selected."
    submission["explanation"] = "The public label in archive.s1 specifies the symbol archive.s20."
    check, public = inspect_teacher_delivery(submission, source, steps, "worked_solution", protect_evidence_ids=True)
    assert check["format_ok"] and submission["explanation"] in public


@pytest.mark.parametrize("teacher_answer,accepted,protocol", [
    ("5", True, "4.4.0"), ("5", True, "4.4.1"), ("1", False, "4.4.1"),
    ("1", True, "4.8.0"),
])
def test_same_reviewed_work_reaches_teacher_for_final_delivery(tmp_path, teacher_answer, accepted, protocol):
    question = "Add two and three and show why."
    instruction = "Use ordinary arithmetic."
    reasoning = "Combining two and three gives five."
    source = tmp_path / "source.jsonl"
    task = {"id": "sum", "question": question, "task_system": instruction,
            "gold": {"kind": "number", "value": "5"}, "answer_requirement": "worked_solution"}
    if accepted:
        # An imported final-only flag must not erase the real user's "show why".
        task.update(answer_requirement="final_only", direct_answer_syntax="keyed_rational")
    source.write_text(json.dumps(task), encoding="utf8")
    calls = []

    class Endpoint:
        def __init__(self, name):
            self.name = name

        def validate(self):
            return {"model": self.name, "url": f"http://localhost/{self.name}", "slots": 1, "enable_thinking": False}

        def payload(self, messages, schema):
            return {"messages": messages, "schema": schema, "model": self.name}

        def complete(self, payload):
            fields = (payload["schema"] or {}).get("properties", {})
            calls.append((self.name, payload))
            if self.name == "student":
                assert not fields
                assert "grammar" not in payload
                text = reasoning + "\nFinal answer: 5"
            elif "independent_approaches" in fields:
                text = json.dumps({"decision": "supported", "diagnosis": "The sum is justified.",
                                   "reusable": [], "independent_approaches": []})
            else:
                correction = json.loads(payload["messages"][-1]["content"]).get("previous_delivery_check")
                if correction:
                    assert correction["previous_answer_author"] == "teacher" and correction["previous_conclusion"] == "1"
                text = json.dumps({"decision": "deliver", "explanation": "Adding two and three gives five.",
                                   "conclusion": "5" if correction else teacher_answer, "evidence_ids": ["a1.s1"], "reason": ""})
            return {"content": text, "model": self.name, "reasoning_content": "", "finish_reason": "stop"}

    root = tmp_path / "run"
    endpoints = {name: Endpoint(name) for name in ("student", "coach")}
    summary = run_guided_cases(source, root, endpoints, {"version": "example"}, 1,
                               workers=1, protocol_version=protocol)
    corrected = protocol == "4.8.0"
    assert [name for name, _ in calls] == ["student", "coach", "coach"] + (["coach"] if corrected else [])
    final_messages = calls[-1][1]["messages"]
    assert ("the candidate attains" in final_messages[0]["content"]) == (protocol != "4.4.0")
    assert {"role": "user", "content": question} in final_messages
    assert any(turn["role"] == "system" and instruction in turn["content"] for turn in final_messages)
    assert any(reasoning in turn["content"] for turn in final_messages)
    archive = next((root / "cases").iterdir())
    assert (archive / "001-teacher_final_answer.json").is_file()
    assert not (archive / "001-final_answer.json").exists()
    assert summary["counts"]["accepted"] == int(accepted)
    exported = (root / "accepted.jsonl").read_text("utf8").strip()
    if accepted:
        record = json.loads(exported)
        assert record["final_answer_provenance"]["author"] == "teacher"
        assert record["final_answer_provenance"]["model"] == "coach"
        if corrected:
            from lib.contexts.workflows.api import recorded_final_proposal
            assert (archive / "001-teacher_final_correction.json").is_file()
            audit = json.loads((archive / "case.json").read_text("utf8"))
            assert [f["check"]["answer_matches"] for f in audit["finalization"]] == [False, True]
            observed_note = audit["attempts"][-1]["delivery_correction"]["guidance"]
            assert record["messages"][-1]["reasoning_content"] == reasoning + "\n\n" + observed_note
            for stage, value in (("teacher_final_answer", "1"), ("teacher_final_correction", "5")):
                receipt = json.loads((archive / f"001-{stage}.json").read_text("utf8"))
                assert recorded_final_proposal(audit, 1, receipt)[1]["conclusion"] == value
        else:
            assert record["messages"][-1]["reasoning_content"] == reasoning
        assert "Adding two and three gives five." in record["messages"][-1]["content"]
        assert record["messages"][-1]["content"].rstrip().endswith("5")
    else:
        assert not exported


def test_same_local_model_separates_student_and_teacher_reasoning_and_resumes(tmp_path):
    case = {"id": "sum", "question": "Add two and three and show why.",
            "gold": {"kind": "number", "value": "5"}, "answer_requirement": "worked_solution"}
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(case), encoding="utf8")
    sent = []
    endpoint = GuidedChatEndpoint("http://localhost/v1", "one-model", None, 32768, local=True,
                                  context=98304, pool=SimpleNamespace(acquire=nullcontext), sampling="qwen3.5-0.8b")

    def http(url, body=None):
        if url.endswith("/slots"):
            return [{"n_ctx": 98304}]
        if url.endswith("/models"):
            return {"data": [{"id": "one-model"}]}
        if url.endswith("/apply-template"):
            return {"prompt": "\n".join(m["content"] for m in body["messages"])}
        if url.endswith("/tokenize"):
            return {"tokens": [1, 2, 3]}
        sent.append(body)
        if "response_format" not in body:
            msg = {"content": "Combining two and three gives five.\nFinal answer: 5"}
        elif "independent_approaches" in body["response_format"]["json_schema"]["schema"]["properties"]:
            msg = {"content": json.dumps({"decision": "supported", "diagnosis": "The sum is justified.",
                                          "reusable": [], "independent_approaches": []})}
        else:
            msg = {"reasoning_content": "private teacher work",
                   "content": json.dumps({"decision": "deliver", "explanation": "Combining the groups gives five.",
                                          "conclusion": "5", "evidence_ids": ["a1.s1"], "reason": ""})}
        return {"choices": [{"message": msg, "finish_reason": "stop"}], "model": "one-model"}

    endpoint.http = http
    root = tmp_path / "run"
    def run():
        return run_guided_cases(source, root, {"student": endpoint, "coach": endpoint},
                                {"version": "example"}, 1, workers=1)

    summary = run()
    assert summary["counts"]["accepted"] == 1
    assert [req["chat_template_kwargs"]["enable_thinking"] for req in sent] == [False, True, True]
    assert [req["presence_penalty"] for req in sent] == [2.0, 1.5, 1.5]
    assert [req["top_p"] for req in sent] == [1.0, 0.95, 0.95]
    assert all(req["temperature"] == 1.0 and req["top_k"] == 20
               and req["min_p"] == 0 and req["repeat_penalty"] == 1.0
               and "penalties" in req["samplers"] for req in sent)
    assert "reasoning_format" not in sent[0] and all(req["reasoning_format"] == "deepseek" for req in sent[1:])
    folder = next((root / "cases").iterdir())
    assessment = json.loads((folder / "001-coach.json").read_bytes())["response"]
    final = json.loads((folder / "001-teacher_final_answer.json").read_bytes())["response"]
    assert not assessment["teacher_reasoning"]["observed"]
    assert final["teacher_reasoning"]["observed"] and final["teacher_reasoning"]["structured_output"] == "valid"
    assert final["reasoning_content"] == "private teacher work"
    assert "private teacher work" not in (root / "accepted.jsonl").read_text("utf8")
    assert run() == summary and len(sent) == 3


def test_native_q5_sampling_and_legacy_request_replay():
    ep = GuidedChatEndpoint("http://localhost/v1", "qwen3.5-9b-q5-k-m", None, 32768,
                            local=True, context=131072, pool=None)
    messages = [{"role": "user", "content": "Explain the rule."}]
    ep.configure_teacher_reasoning("4.6.0")
    ep.configure_sampling("4.6.0")
    old = ep.payload(messages, None)
    assert list(old) == ["model", "messages", "max_tokens", "temperature", "stream",
                         "chat_template_kwargs", "reasoning_format"]
    assert old["temperature"] == 0 and "presence_penalty" not in old
    ep.configure_teacher_reasoning("4.7.0")
    ep.configure_sampling("4.7.0")
    current = ep.payload(messages, None)
    assert ep.sampling_profile == "qwen3.5-9b"
    assert current["temperature"] == 1.0 and current["presence_penalty"] == 1.5
    assert current["chat_template_kwargs"] == {"enable_thinking": True}
