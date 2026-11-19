import json

import pytest

from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases
from lib.contexts.workflows.domain.guided_teacher_delivery import inspect_teacher_delivery, requested_delivery


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


@pytest.mark.parametrize("teacher_answer,accepted,protocol", [
    ("5", True, "4.4.0"), ("5", True, "4.4.1"), ("1", False, "4.4.1"),
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
                text = json.dumps({"decision": "deliver", "explanation": "Adding two and three gives five.",
                                   "conclusion": teacher_answer, "evidence_ids": ["a1.s1"], "reason": ""})
            return {"content": text, "model": self.name, "reasoning_content": "", "finish_reason": "stop"}

    root = tmp_path / "run"
    endpoints = {name: Endpoint(name) for name in ("student", "coach")}
    summary = run_guided_cases(source, root, endpoints, {"version": "example"}, 1,
                               workers=1, protocol_version=protocol)
    assert [name for name, _ in calls] == ["student", "coach", "coach"]
    final_messages = calls[-1][1]["messages"]
    assert ("the candidate attains" in final_messages[0]["content"]) == (protocol == "4.4.1")
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
        assert record["messages"][-1]["reasoning_content"] == reasoning
        assert "Adding two and three gives five." in record["messages"][-1]["content"]
        assert record["messages"][-1]["content"].rstrip().endswith("5")
    else:
        assert not exported
