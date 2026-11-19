import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from lib.bootstrap.guided_answers import generate_guided_cases
from lib.contexts.workflows.api import GenerateGuidedAnswer
from lib.contexts.workflows.domain.guided_answers import (
    checked_progress,
    draft_fragment,
    inspect_answer,
    shared_findings,
    validated_review,
    validate_source,
)


def test_list_numbers_stay_attached_and_never_become_evidence():
    text = "1. 连续的 1 最多有两个。\n2. 总数应为 1 模 3。\nFinal answer: 99"
    pieces, _ = draft_fragment(text, 1)
    assert [piece["text"].strip() for piece in pieces] == ["1. 连续的 1 最多有两个。", "2. 总数应为 1 模 3。"]
    old, _ = draft_fragment(text, 1, preserve_list_items=False)
    assert old[0]["text"].strip() == "1."
    offer = [{"segment_id": old[0]["id"], "basis_id": "question", "depends_on": []}]
    assert not shared_findings(offer, old, [], "总数除以3余1", exclude_list_markers=True)
    review = validated_review({"decision": "uncertain:a1.s2", "diagnosis": "Check the congruence",
                               "reusable": []}, pieces, True, anchored=True, single_decision=True)
    assert review["verdict"] == "uncertain" and review["verified_prefix_count"] == 1


def test_local_answer_continuation_emits_only_tokens_after_the_closed_thinking_block():
    from contextlib import nullcontext
    from types import SimpleNamespace
    from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint

    endpoint = GuidedChatEndpoint("http://localhost/v1", "student", None, 32768,
                                  local=True, context=98304, pool=SimpleNamespace(acquire=nullcontext))
    calls = []
    messages = [{"role": "user", "content": "What is the sum?"},
                {"role": "assistant", "reasoning_content": "The two amounts give seven.", "content": ""}]

    def service(url, data):
        calls.append((url, data))
        if url.endswith("/apply-template"):
            assert data["messages"][0]["role"] == "system" and data["messages"][1:] == messages[:-1]
            assert data["chat_template_kwargs"]["enable_thinking"]
            return {"prompt": "Question\nAssistant\n<think>\n"}
        if url.endswith("/tokenize"):
            assert data["content"] == "Question\nAssistant\n<think>\nThe two amounts give seven.\n</think>\n\n"
            assert data["parse_special"] and not data["add_special"]
            return {"tokens": [12, 13, 14]}
        assert url.endswith("/completion") and data["prompt"] == [12, 13, 14]
        return {"content": "7", "stop_type": "eos", "tokens_evaluated": 3, "tokens_predicted": 2}

    endpoint.http = service
    response = endpoint.complete(endpoint.final_answer_payload(messages))
    assert response["content"] == "7" and response["reasoning_content"] == ""
    assert response["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert len(calls) == 3


def test_three_round_http_history_uses_only_published_clues_and_freezes_resume(
    tmp_path,
    monkeypatch,
):
    from lib.model_request_scheduler import SharedModelScheduler

    # Exercise the frozen 3.6 chat protocol, including replay of its original wire body.
    monkeypatch.setattr("lib.contexts.workflows.infrastructure.guided_answer_records.VERSION", "3.6.0")
    monkeypatch.setattr(
        "lib.bootstrap.guided_answers.SharedModelScheduler",
        lambda _root, identity, **kw: SharedModelScheduler(tmp_path, identity, **kw),
    )
    calls = []

    class Server(BaseHTTPRequestHandler):
        def reply(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/slots":
                return self.reply([{"id": i, "n_ctx": 98304} for i in range(2)])
            assert self.path == "/v1/models"
            self.reply({"data": [{"id": "test-native"}]})

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/apply-template":
                return self.reply(
                    {"prompt": "\n".join(m["content"] for m in data["messages"])}
                )
            if self.path == "/tokenize":
                return self.reply({"tokens": [1, 2, 3]})
            assert self.path == "/v1/chat/completions"
            calls.append(data)
            if "response_format" not in data:
                count = sum(1 for x in calls if "response_format" not in x)
                content = (
                    "The groups have two and three items.\nI subtract and get four.\n3 × 4 = 12.\nFinal answer: 4",
                    "Adding the groups counts each item once.\nI obtain three.\nFinal answer: 3",
                    "The sum is two plus three, which gives five.\nFinal answer: 5",
                )[count - 1]
            else:
                task = json.loads(data["messages"][-1]["content"])
                ok = task["program_check"]["answer_matches"]
                one = not task["disclosed_guidance"]
                content = json.dumps(
                    {
                        "reusable": (
                            [
                                {
                                    "segment_id": "a1.s3",
                                    "basis_id": "arithmetic",
                                    "depends_on": [],
                                },
                                {
                                    "segment_id": "a1.s2",
                                    "basis_id": "question",
                                    "depends_on": ["a1.s2"],
                                },
                            ]
                            if one
                            else []
                        ),
                        "diagnosis": "Private diagnostic label.",
                        "decision": "supported" if ok else "revise:a1.s2" if one else "revise:a2.s2",
                    }
                )
            self.reply(
                {
                    "choices": [
                        {"message": {"content": content}, "finish_reason": "stop"}
                    ],
                    "usage": {"completion_tokens": 12},
                }
            )

        def log_message(self, *unused):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    clues = [
        {"id": "first", "text": "Recheck the grouping.", "source": "editor"},
        {"id": "second", "text": "Try pairing the two sets.", "source": "editor"},
        {"id": "never", "text": "Unpublished clue.", "source": "editor"},
    ]
    source = dict(
        id="q1",
        question="Add two and three.",
        gold={"kind": "number", "value": "5"},
        source_reasoning="Private archived solution.",
        disclosure_hints=[
            {
                "id": "known",
                "text": "3   × 4 = 12.",
                "source": "editor",
                "kind": "worked_step",
            },
            *clues,
        ],
    )
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps(source), encoding="utf-8")
    root = tmp_path / "run"
    args = dict(
        student_url=f"http://127.0.0.1:{server.server_port}/v1",
        student_model="test-native",
        max_attempts=4,
    )
    try:
        summary = generate_guided_cases(path, root, **args)
        assert summary["counts"]["accepted"] == 1 and len(calls) == 6
        assert generate_guided_cases(path, root, **args) == summary and len(calls) == 6
        replay_root = tmp_path / "replay"
        replay = generate_guided_cases(
            path, replay_root, **{**args, "max_attempts": 1}, reuse_first_from=root
        )
        assert replay["model_calls"] == {
            "replayed_first": 1,
            "new_completed": 1,
            "interrupted_or_pending": 0,
        }
        assert len(calls) == 7 and "response_format" in calls.pop()
        checkpoint = json.loads(
            next((replay_root / "cases").glob("*/001-student.json")).read_text("utf-8")
        )
        assert (
            checkpoint["origin"]["kind"] == "replayed_first"
            and checkpoint["request"] == calls[0]
        )
        assert (
            checkpoint["origin"]["source_receipt_sha256"]
            == hashlib.sha256(
                next((root / "cases").glob("*/001-student.json")).read_bytes()
            ).hexdigest()
        )
        different = tmp_path / "different.jsonl"
        different.write_text(
            json.dumps({**source, "question": "A different question."}),
            encoding="utf-8",
        )
        mismatch = generate_guided_cases(
            different, tmp_path / "mismatch", **args, reuse_first_from=root
        )
        assert mismatch["counts"]["interrupted"] == 1 and len(calls) == 6
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    first, second, third = calls[::2]
    assert [m["role"] for m in first["messages"]] == ["system", "user"]
    assert [m["role"] for m in third["messages"]] == [
        "system",
        "user",
        "tool",
    ]
    assert third["messages"][:2] == second["messages"][:2]
    disclosed = [req["messages"][-1]["content"] for req in (second, third)]
    view = json.loads(disclosed[-1].split("\n", 1)[1])
    assert view["previously_disclosed_clues"] == [clues[0]["text"]]
    assert view["new_clue"] == clues[1]["text"]
    assert "The groups have two and three items." in view["retained_solution"]
    assert "3 × 4 = 12." in view["confirmed_findings_from_your_own_work"][0]
    for req in calls[2:]:
        body = json.dumps(req, ensure_ascii=False)
        assert (
            "I subtract and get four." not in body
            and "Private diagnostic label." not in body
        )
    for req in calls[4:]:
        body = json.dumps(req, ensure_ascii=False)
        assert "I obtain three." not in body
    assert (
        json.dumps(calls[-1], ensure_ascii=False).count(
            "The sum is two plus three, which gives five."
        )
        == 1
    )
    for req in calls[::2]:
        serialized = json.dumps(req["messages"])
        assert (
            "Private archived" not in serialized
            and "Private diagnostic" not in serialized
        )
        assert "Unpublished clue" not in serialized and "answer_key" not in serialized
        assert req["max_tokens"] >= 32768 and req["chat_template_kwargs"] == {
            "enable_thinking": False
        }
    teacher = json.loads(calls[-1]["messages"][-1]["content"])
    assert teacher["answer_key"] == source["gold"]
    assert [x["text"] for x in teacher["disclosed_guidance"]] == [
        h["text"] for h in clues[:2]
    ]
    directory = root / "cases" / hashlib.sha256(b"q1").hexdigest()
    case = json.loads((directory / "case.json").read_text("utf-8"))
    assert json.loads((root / "manifest.json").read_text("utf-8"))["concurrency"] == 2
    assert [e["status"] for e in case["clue_ledger"]] == [
        "student_already_derived",
        "disclosed",
        "disclosed",
    ]
    assert case["clue_ledger"][0]["student_segment_id"] == "a1.s3"
    assert teacher["already_known_clues"][0]["student_segment_id"] == "a1.s3"
    assert "3   × 4 = 12." not in disclosed[0]
    assert [d["ordinal"] for d in case["disclosures"]] == [1, 2]
    assert [v["withdrawn"] for v in case["draft_versions"]] == [
        ["a1.s2"],
        ["a2.s2"],
        [],
    ]
    assert case["draft_versions"][0]["reusable"] == ["a1.s3"]
    assert case["shared_student_findings"][0]["author"] == "student"
    assert (
        case["shared_student_findings"][0]["evidence_level"]
        == "program_checked_arithmetic_identity"
    )
    assert "3 × 4 = 12." in disclosed[0] and "I subtract" not in disclosed[0]
    assert case["disclosures"][0]["anchor_after"] == "a1.s1"
    assert case["disclosures"][1]["anchor_after"] == "a2.s1"
    correct = case["messages"][-1]["content"]
    assert (
        correct
        == "The groups have two and three items.\nAdding the groups counts each item once.\nThe sum is two plus three, which gives five.\nFinal answer: 5"
    )
    assert "I subtract" not in correct and "I obtain three" not in correct
    assert (
        case["retained_segments"][0]["text"] == "The groups have two and three items.\n"
    )
    public = (root / "accepted.jsonl").read_text("utf-8")
    assert "Unpublished clue" not in public
    assert len((root / "feedback.jsonl").read_text("utf-8").splitlines()) == 3
    assert generate_guided_cases(path, root, export_only=True) == summary


def test_answer_key_is_mandatory_for_acceptance_and_explanation_has_independent_review():
    solution = "Combining the groups gives the total.\nFinal answer: 5"

    def request(role, index, messages, schema):
        content = (
            solution
            if role == "student"
            else json.dumps(
                {
                    "decision": "supported",
                    "diagnosis": "The source supports the reasoning.",
                    "reusable": [],
                }
            )
        )
        return {"content": content, "finish_reason": "stop", "reasoning_content": ""}

    workflow = GenerateGuidedAnswer(request)
    case = dict(
        id="source-only",
        question="What is the total?",
        source_reasoning="An unfinished source derivation.",
    )
    result = workflow.run(validate_source(case), {"version": "initial"}, 3)
    assert (
        result["status"] == "unverified"
        and result["verification"]["answer_key_check"] is None
    )
    assert len(result["attempts"]) == 1 and not result["disclosures"]
    assert result["source_reasoning"] not in result["messages"][-1]["content"]
    assert (
        result["public_dialogue"][1]["role"] == "tool"
        and result["source_reasoning"] in result["public_dialogue"][1]["content"]
    )
    assert (
        workflow.run(
            validate_source({**case, "gold": {"kind": "number", "value": "5"}}),
            {"version": "v1"},
            3,
            protocol_version="3.6.0",
        )["status"]
        == "accepted"
    )
    assert not inspect_answer("Final answer: 5", {"kind": "number", "value": "5"})[
        "format_ok"
    ]
    with pytest.raises(ValueError, match="answer key"):
        validate_source(
            {
                **case,
                "gold": {"kind": "number", "value": "5"},
                "disclosure_hints": [
                    {"id": "bad", "text": "Use the value 10/2.", "source": "editor"}
                ],
            }
        )
    valid = validate_source(
        {
            **case,
            "gold": {"kind": "number", "value": "0"},
            "disclosure_hints": [
                {
                    "id": "recurrence",
                    "text": "The recurrence starts at f(0)=1.",
                    "source": "editor",
                    "kind": "worked_step",
                }
            ],
        }
    )
    assert valid["disclosure_hints"][0]["kind"] == "worked_step"
    gold = {"kind": "number", "value": "6"}
    assert inspect_answer("二乘三得六。\n**最终答案：**\n6", gold)["answer_matches"]
    assert not inspect_answer("计算。\n**最终答案：**\n6\nFinal answer: 7", gold)[
        "format_ok"
    ]


def test_unaided_direct_answers_export_without_buying_a_tutor_call(tmp_path):
    from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases

    actual = []
    responses = {"How many?": "Final answer: $\\boxed{5}$", "Which option?": "B",
                 "Which route?": "A → D → E", "How many in total?": "The answer is **5**."}

    class Endpoint:
        def validate(self):
            return {"url": "http://localhost:8080/v1", "model": "fixture", "slots": 4, "enable_thinking": False}

        def payload(self, messages, schema):
            assert schema is None and [m["role"] for m in messages] == ["system", "user"]
            return {"messages": messages}

        def complete(self, payload):
            actual.append(payload)
            return {"content": responses[payload["messages"][-1]["content"]], "finish_reason": "stop",
                    "reasoning_content": ""}

    path, output = tmp_path / "in.jsonl", tmp_path / "run"
    sources = [dict(id=str(i), question=q, answer_requirement="final_only", source_reasoning="PRIVATE",
                    gold={"kind": k, "value": v})
               for i, (q, (k, v)) in enumerate(zip(responses, [("number", "5"), ("choice", "B"),
                                                               ("exact", "A → D → E"), ("number", "5")]))]
    path.write_text("\n".join(json.dumps(s) for s in sources), encoding="utf8")
    endpoints = {"student": Endpoint(), "coach": Endpoint()}
    summary = run_guided_cases(path, output, endpoints, {"version": "default"}, 2, workers=1)
    assert summary["accepted_by_route"]["nonthinking_direct"] == sum(summary["accepted_by_route"].values()) == 4
    assert summary["model_calls"]["new_completed"] == len(actual) == 4
    assert all("PRIVATE" not in str(payload) for payload in actual)
    for row in map(json.loads, (output / "accepted.jsonl").read_text("utf8").splitlines()):
        assert row["training_route"] == "nonthinking_direct"
        assert row["messages"][-1] == {"role": "assistant", "content": responses[row["question"]]}
        assert row["verification"] == {"answer_key_check": True, "explanation_review": None,
                                       "correctness": "unaided_direct_key_match", "issue": None}
    assert run_guided_cases(path, output, {}, {}, 2, export_only=True) == summary


def test_direct_admission_does_not_strip_proofs_or_relabel_guided_and_incomplete_answers():
    from lib.contexts.workflows.domain.guided_answers import check_direct_answer

    seed = {"id": "direct", "question": "What is two plus three?", "gold": {"kind": "number", "value": "5"},
            "answer_requirement": "final_only"}
    source = validate_source(seed)
    assert "answer_requirement" not in validate_source(seed, allow_answer_requirement=False)
    for wrong in ("Conflicting count: 3.\nFinal answer: 5", "4 or 5", "<think>secret</think>5", "5 if x>0"):
        assert check_direct_answer(wrong, source["gold"]) is None

    def attempt(replies, assessment, *, entry=source, version="3.6.0", first_pass_only=False):
        calls = []
        def send(role, index, messages, schema):
            calls.append((role, messages))
            if role == "student":
                text, thinking, finish = replies[index - 1]
                return {"content": text, "reasoning_content": thinking, "finish_reason": finish}
            decision = assessment[index - 1]
            return {"content": json.dumps({"decision": decision, "diagnosis": "Compare with the actual sum.",
                                           "reusable": []}), "finish_reason": "stop"}
        return GenerateGuidedAnswer(send).run(entry, {"version": "test"}, len(replies),
                                              protocol_version=version, first_pass_only=first_pass_only), calls

    for output in (("5", "hidden calculation", "stop"), ("5", "", "length")):
        result, calls = attempt([output], [], first_pass_only=True)
        assert result["status"] == "quarantine" and [r for r, _ in calls] == ["student"]
    result, calls = attempt([("4", "", "stop")], [], first_pass_only=True)
    assert result is None and [r for r, _ in calls] == ["student"]
    for frozen, requirement in (("3.5.0", "final_only"), ("3.6.0", "worked_solution")):
        result, calls = attempt([("Final answer: 5", "", "stop")], ["supported"],
                                entry={**source, "answer_requirement": requirement}, version=frozen)
        assert result["status"] == "quarantine" and [r for r, _ in calls] == ["student", "coach"]
    worked = "Counting two items and three further items gives five.\nFinal answer: 5"
    result, calls = attempt([("4", "", "stop"), (worked, "", "stop")], ["revise:a1.s1", "supported"])
    assert result["training_route"] == "reviewed_solution" and result["messages"][-1]["content"] == worked
    assert result["attempts"][0]["student"]["content"] == "4"
    assert [r for r, _ in calls] == ["student", "coach", "student", "coach"]
    assert calls[0][1][0]["content"] != calls[2][1][0]["content"]
    assert [m["role"] for m in calls[2][1]] == ["system", "user", "tool"]


def test_disclosed_attempts_form_the_native_reasoning_then_the_student_delivers_an_independent_answer():
    source = validate_source({
        "id": "guided", "question": "What is 2 plus 3?", "task_system": "Use Arabic numerals.",
        "gold": {"kind": "number", "value": "5"}, "answer_requirement": "final_only",
        "source_reasoning": "Private reference explanation.",
        "disclosure_hints": [
            {"id": "add", "source": "author", "text": "Combine the two groups."},
            {"id": "unused", "source": "author", "text": "A private unused clue."},
        ],
    })
    replies = ["4", "Adding two and three gives five.\nFinal answer: 5",
               "The requested count is the combined total.\nFinal answer: 5"]

    def run(formal_outputs, limit):
        calls = []
        def request(role, index, messages, schema):
            calls.append((role, index, messages))
            finish = "stop"
            if role == "student":
                content = replies[index - 1]
            elif role == "coach":
                content = json.dumps({"decision": "revise:a1.s1" if index == 1 else "supported",
                                      "diagnosis": "Private tutor rationale.", "reusable": []})
            else:
                assert role == "final_answer" and schema is None
                content, finish = formal_outputs[index - 2]
            return {"content": content, "reasoning_content": "", "finish_reason": finish}
        case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, limit, protocol_version="3.7.0")
        return case, calls

    case, calls = run([("7", "stop"), ("5", "stop")], 3)
    assert [r for r, _, _ in calls] == ["student", "coach", "student", "coach", "final_answer",
                                        "student", "coach", "final_answer"]
    assistant = case["messages"][-1]
    trace = assistant["reasoning_content"]
    assert case["training_route"] == "guided_reasoning" and assistant["content"] == "5"
    assert case["messages"][:-1] == [{"role": "system", "content": source["task_system"]},
                                     {"role": "user", "content": source["question"]}]
    assert [record["attempt"] for record in case["finalization"]] == [2, 3]
    assert [record["check"]["answer_matches"] for record in case["finalization"]] == [False, True]
    offsets = [trace.index(piece) for piece in ("\n4\n", "Combine the two groups.", replies[1], "\n7\n",
                                                case["disclosures"][1]["text"], replies[2])]
    assert offsets == sorted(offsets)
    assert not any(s in trace for s in ("Private reference", "Private tutor", "private unused"))
    assert calls[-1][2] == [*case["messages"][:-1], {"role": "assistant", "reasoning_content": trace,
                                                     "content": ""}]
    retry_view = json.loads(calls[5][2][-1]["content"].split("\n", 1)[1])
    assert retry_view["your_last_formal_answer"] == "7" and retry_view["your_previous_conclusion"] == "5"
    for bad, finish in (("5", "length"), ("</think>5", "stop")):
        failed, _ = run([(bad, finish)], 2)
        assert failed["status"] == "quarantine" and failed["verification"]["answer_key_check"] is False
        assert failed["attempts"][-1]["formal_answer"]["response"]["content"] == bad
    for last_value, status in (("5", "accepted"), ("8", "quarantine")):
        late, _ = run([("7", "stop"), (last_value, "stop")], 2)
        assert late["status"] == status and [a["attempt"] for a in late["finalization"]] == [2, 3]
        assert late["finalization"][0]["granted_correction_round"] == 3
        assert "granted_correction_round" not in late["finalization"][1]
        assert late["attempts"][-1]["formal_answer"]["response"]["content"] == last_value


def test_final_count_sentence_tracks_the_question_subject_unit_and_last_assertion():
    from lib.contexts.workflows.domain.guided_answers import inspect_formal_answer

    question = "社团有27人，13人参加读书会，16人参加观影，两个都参加的有7人。两项中恰好只参加一项的人数是多少？"
    subject = "两项中恰好只参加一项的人数"
    observed = "两项中恰好只参加一项的人数是 **15** 人。"
    def check(content, *, direct=False):
        return inspect_formal_answer({"content": content, "finish_reason": "stop", "reasoning_content": ""},
                                     {"kind": "number", "value": "15"}, question, final_only=direct)
    assert check("6 + 9 = 15。\n**结论：**\n" + observed)["answer_matches"] is True
    assert check(observed, direct=True)["answer_matches"] is True
    assert check("15")["answer_matches"] is True
    assert check("14")["answer_matches"] is False
    assert check("15。另有三组都没参加。")["answer_matches"] is not True
    assert check("计算见上。\n" + subject + "是14人。")["answer_matches"] is False
    for bad in (observed.replace("恰好只", "至少"), observed.replace("人。", "组。"), observed.replace("15", "14或15"),
                "若条件变动，" + observed, observed + "\n该条件尚未确认。", "Final answer: 4\n" + observed,
                subject + "是 **14** 人。\n" + observed):
        assert check(bad)["answer_matches"] is not True


def test_teacher_reviews_proposed_disclosure_and_its_connection_before_student_can_receive_it():
    source = validate_source({"id": "hint", "question": "There are 2 red and 3 blue counters. How many in total?",
                              "gold": {"kind": "number", "value": "5"}, "answer_requirement": "final_only",
                              "source_reasoning": "Private gold derivation.",
                              "disclosure_hints": [{"id": "merge", "kind": "idea", "source": "editor",
                                                   "text": "Combine the two groups."}]})
    bridge = "The two colours distinguish disjoint parts of one collection."
    def run(fault=None):
        calls = []
        def request(role, index, messages, schema):
            calls.append((role, index, messages))
            if role == "student":
                content = ("4", "Adding counts gives five.\nFinal answer: 5",
                           "The combined quantity is in counters.\nFinal answer: 5")[index - 1]
            elif role == "coach":
                content = json.dumps({"decision": "revise:a1.s1" if index == 1 else "supported",
                                      "diagnosis": "Private diagnosis of the student's draft.", "reusable": []})
            elif role == "final_answer":
                content = "7" if index == 2 else "5"
            else:
                assert role == "disclosure"
                material = json.loads(messages[1]["content"])
                assert "Private" not in messages[1]["content"] and "answer_key" not in messages[1]["content"]
                assert [m["role"] for m in messages] == ["system", "user"]
                citations = [{"evidence_id": "question", "quote": "2 red and 3 blue"}]
                decision, shown = "release", bridge
                if index == 2:
                    history, = material["ordered_previous_teacher_disclosures"]
                    assert material["latest_disclosure_id"] == history["evidence_id"] == "disclosed.1"
                    assert history["text"] == bridge + "\nCombine the two groups."
                    assert material["current_difficulty"]["observed_text_end"] == "7"
                    if fault != "break_previous_link":
                        citations.append({"evidence_id": "disclosed.1", "quote": "Combine the two groups."})
                    shown = "Carry the combined count from that grouping into the quantity asked for."
                if fault == "quote":
                    citations[0]["quote"] = "not present"
                if fault == "answer":
                    shown = "The answer is 5."
                if fault == "veto":
                    decision, shown = "withhold", ""
                content = json.dumps({"decision": decision, "citations": citations, "bridge": shown,
                                      "reason": "Private assessment of the proposed hint."})
            return {"content": content, "finish_reason": "stop", "reasoning_content": ""}
        return GenerateGuidedAnswer(request).run(source, {"version": "test"}, 3, protocol_version="3.8.0"), calls

    passed, calls = run()
    assert passed["training_route"] == "guided_reasoning"
    assert [r for r, _, _ in calls] == ["student", "coach", "disclosure", "student", "coach", "final_answer",
                                        "disclosure", "student", "coach", "final_answer"]
    first = passed["attempts"][0]["disclosure_review"]
    ref, = first["validated_citations"]
    assert source["question"][slice(*ref["span"])] == ref["quote"]
    assert first["candidate"]["text"] != passed["disclosures"][0]["text"]
    trace = passed["messages"][-1]["reasoning_content"]
    assert bridge in trace and "Private" not in trace
    for fault, count in (("quote", 0), ("answer", 0), ("veto", 0), ("break_previous_link", 1)):
        failed, seen = run(fault)
        assert failed["status"] == "quarantine" and len(failed["disclosures"]) == count
        assert seen[-1][0] == "disclosure" and "disclosure" not in failed["attempts"][-1]
        assert failed["attempts"][-1]["disclosure_review"]["issue"] == failed["verification"]["issue"]


def test_direct_output_syntax_is_frozen_only_for_compatible_answer_only_sources():
    row = {"id": "f", "question": "Which fact IDs?", "answer_requirement": "final_only",
           "gold": {"kind": "exact", "value": "F02,F12"}, "direct_answer_syntax": "fact_ids"}
    assert validate_source(row)["direct_answer_syntax"] == "fact_ids"
    assert "direct_answer_syntax" not in validate_source(row, allow_direct_syntax=False)
    for invalid in ({**row, "gold": {"kind": "exact", "value": "F02, F12"}},
                    {**row, "direct_answer_syntax": "uppercase_letters"},
                    {**row, "answer_requirement": "worked_solution"}):
        with pytest.raises(ValueError, match="direct_answer_syntax"):
            validate_source(invalid)


def test_authored_disclosures_can_continue_after_source_hints_and_preserve_rejected_teacher_drafts():
    source = validate_source({"id": "authored", "question": "Combine the two disjoint groups of two and three.",
                              "gold": {"kind": "number", "value": "5"}, "answer_requirement": "final_only",
                              "disclosure_hints": [{"id": "seed", "source": "curator",
                                                   "text": "Try summing both disjoint groups."}]})
    experience = {"version": "experience-test", "disclosure_teacher": "Teacher-only experience."}
    def run(reject_again=False, stall=False):
        roles = []
        def request(role, index, messages, schema):
            roles.append(role)
            if role.startswith("disclosure"):
                packet = json.loads(messages[-1]["content"])
                if index == 2:
                    assert packet["private_reference_ceiling"] is None
                    assert packet["ordered_previous_teacher_disclosures"][-1]["evidence_id"] == "disclosed.1"
                if role in ("disclosure_draft", "disclosure_rewrite"):
                    assert experience["disclosure_teacher"] in messages[0]["content"]
                    if role == "disclosure_rewrite":
                        assert "too strong" in packet["requested_revision"]["private_reviewer_reason"]
                    bridge = ("Disjoint groups have no member in common." if index == 1 else "The sets can be counted separately.")
                    hint = ("Try tracking one group." if index == 1 else "Carry the second count into the sum.")
                    if role == "disclosure_draft" and index == 1:
                        hint = "Jump straight to the subtotal."
                    result = {"decision": "offer", "bridge": bridge, "hint": hint, "reason": "A private draft basis."}
                else:
                    assert experience["disclosure_teacher"] not in messages[0]["content"]
                    proposed = packet["proposed_next_teacher_disclosure"]
                    citations = [{"evidence_id": "question", "quote": "disjoint groups"}]
                    if index == 2:
                        citations.append({"evidence_id": "disclosed.1", "quote": "tracking one group"})
                    release = index > 1 or (role == "disclosure_recheck" and not reject_again)
                    result = {"decision": "release" if release else "withhold", "citations": citations,
                              "bridge": proposed["bridge"] if release else "",
                              "reason": "The published fact supports the step." if release else "Too large: too strong."}
                content = json.dumps(result)
            elif role == "student":
                assert experience["disclosure_teacher"] not in messages[0]["content"]
                content = ("4", "The two groups do not overlap.\nI subtract.\nFinal answer: 4",
                           "Adding the two groups now gives five.\nFinal answer: 5")[index - 1]
            elif role == "coach":
                decision = ("revise:a1.s1", "revise:a2.s1" if stall else "revise:a2.s2", "supported")[index - 1]
                content = json.dumps({"decision": decision,
                                      "diagnosis": "Assessing the student.", "reusable": []})
            else:
                assert role == "final_answer"
                content = "5"
            return {"finish_reason": "stop", "reasoning_content": "", "content": content}
        return GenerateGuidedAnswer(request).run(source, experience, 1, protocol_version="3.9.0", until_correct=True), roles

    case, roles = run()
    assert case["training_route"] == "guided_reasoning" and len(case["disclosures"]) == 2
    assert roles.count("disclosure_draft") == 2 and roles.count("disclosure_rewrite") == 1
    assert case["clue_ledger"][-1]["bank_position"] is None
    assert "Jump straight" not in case["messages"][-1]["reasoning_content"]
    assert case["attempts"][0]["disclosure_work"][0]["assessment"]["decision"] == "withhold"
    rejected, roles = run(reject_again=True)
    assert rejected["status"] == "awaiting_guidance" and rejected["disclosures"] == []
    assert [a["revision"] for a in rejected["attempts"][0]["disclosure_work"]] == [0, 1]
    assert roles[-1] == "disclosure_recheck" and rejected["continuation"]["last_student_round"] == 1
    stalled, roles = run(stall=True)
    assert stalled["status"] == "awaiting_guidance"
    assert stalled["continuation"]["reason"] == "no_new_verified_student_progress" and roles[-1] == "coach"


def test_wrong_direct_value_stays_an_observation_and_unchanged_withheld_hint_is_not_reviewed_twice():
    question = ("Facts: F01: Anne is quiet. F02: Anne is smart. F04: Erin is green. F05: Erin is smart. "
                "Red, round people are quiet. Return all fact IDs whose withdrawal breaks the target.")
    source = validate_source({"id": "withdrawal", "question": question, "answer_requirement": "final_only",
                              "gold": {"kind": "exact", "value": "F04,F05"}, "direct_answer_syntax": "fact_ids"})
    def run(release, quote="Red, round people are quiet."):
        roles = []
        def call(role, index, messages, schema):
            roles.append(role)
            if role == "student":
                if index == 1:
                    content = "F01,F03,F04,F05,F06,F07"
                else:
                    view = json.loads(messages[-1]["content"].split("\n", 1)[1])
                    assert not view["retained_solution"] and not view["confirmed_findings_from_your_own_work"]
                    assert "Look for the premises" in view["new_clue"]
                    return {"content": "", "reasoning_content": "", "finish_reason": "length"}
            else:
                packet = json.loads(messages[-1]["content"])
                assert [e["evidence_id"] for e in packet["established_evidence"]] == ["question"]
                assert packet["current_difficulty"]["observed_text_end"] == "F01,F03,F04,F05,F06,F07"
                if role in ("disclosure_draft", "disclosure_rewrite"):
                    assert "question_quote" in schema["required"]
                    data = {"decision": "offer", "bridge": "A rule ties two conditions to the target.",
                            "hint": "Look for the premises of that rule.", "question_quote": quote,
                            "reason": "The conditions can be examined even though no student reasoning was provided."}
                else:
                    assert role == "disclosure"
                    data = {"decision": "release" if release else "withhold",
                            "bridge": "A rule ties two conditions to the target." if release else "",
                            "citations": [{"evidence_id": "question", "quote": quote}],
                            "reason": "The quoted premises identify a local check." if release else "Needs a smaller action."}
                content = json.dumps(data)
            return {"content": content, "reasoning_content": "", "finish_reason": "stop"}
        return GenerateGuidedAnswer(call).run(source, {"version": "initial"}, 2), roles
    blocked, roles = run(False)
    assert roles == ["student", "disclosure_draft", "disclosure", "disclosure_rewrite"]
    attempt = blocked["attempts"][0]
    assert attempt["check"]["answer_matches"] is False and attempt["check"]["issue"] == "answer_key_mismatch"
    assert attempt["student_assessment_source"] == "independent_answer_key" and "coach_response" not in attempt
    assert not blocked["retained_segments"] and not blocked["shared_student_findings"] and not blocked["disclosures"]
    assert attempt["disclosure_work"][1]["reused_prior_review_revision"] == 0
    published, roles = run(True)
    assert len(published["disclosures"]) == 1 and roles == ["student", "disclosure_draft", "disclosure", "student"]
    weak, roles = run(True, "the")
    assert not weak["disclosures"] and "disclosure" not in roles


def test_reusable_steps_cannot_self_authenticate_or_form_a_cycle_in_a_judged_prefix():
    from lib.contexts.workflows.domain.guided_answers import rooted_prefix_count

    segments = [{"id": f"a1.s{i}", "text": s} for i, s in enumerate(
        ("The source says there are two groups.", "4 × 3 = 12.", "That makes twelve.", "Next."), 1)]
    def claim(index, basis, dependencies=()):
        return {"segment_id": f"a1.s{index}", "basis_id": basis, "depends_on": list(dependencies)}
    assert rooted_prefix_count([claim(2, "a1.s2")], segments, 3) == 1
    assert rooted_prefix_count([claim(3, "a1.s2"), claim(2, "a1.s3")], segments, 3) == 1
    assert rooted_prefix_count([claim(2, "arithmetic", ["a1.s3"])], segments, 3) == 1
    assert rooted_prefix_count([claim(1, "question"), claim(2, "arithmetic"), claim(3, "a1.s2", ["a1.s1"])],
                               segments, 3) == 3
    assert shared_findings([claim(2, "arithmetic")], segments, segments[:1], "The source says there are two groups.")[
        "a1.s2"]["evidence_level"] == "program_checked_arithmetic_identity"


def test_unaided_visible_derivation_becomes_thinking_and_only_real_corrections_change_its_route():
    source = validate_source({"id": "self-solution", "question": "What is three plus four?",
                              "answer_requirement": "final_only", "gold": {"kind": "number", "value": "7"}})
    proof = "I add four to three to obtain seven."
    def run(finals):
        events = []
        def request(role, index, messages, schema):
            events.append((role, messages))
            if role == "student":
                text = (proof if index == 1 else "Checking the count again gives seven.") + "\nFinal answer: 7"
            elif role == "coach":
                text = json.dumps({"decision": "supported", "diagnosis": "Checked the addition.", "reusable": []})
            else:
                text = finals[index - 1]
            return {"finish_reason": "stop", "reasoning_content": "", "content": text}
        return GenerateGuidedAnswer(request).run(source, {"version": "test"}, len(finals), protocol_version="3.7.0"), events

    case, events = run(["7"])
    assert [r for r, _ in events] == ["student", "coach", "final_answer"]
    assert case["training_route"] == "unaided_reasoning" and case["disclosures"] == []
    assert events[-1][1][-1] == {"role": "assistant", "reasoning_content": proof, "content": ""}
    assert case["messages"][-1] == {"role": "assistant", "reasoning_content": proof, "content": "7"}
    corrected, events = run(["6", "7"])
    assert corrected["training_route"] == "guided_reasoning" and len(corrected["disclosures"]) == 1
    assert [event["attempt"] for event in corrected["finalization"]] == [1, 2]
    assert "\n6\n" in corrected["messages"][-1]["reasoning_content"]
    assert corrected["messages"][-1]["content"] == "7"


def test_reasoning_only_source_receives_missing_natural_artifact_without_numeric_format():
    source = validate_source(
        dict(
            id="script",
            question="Give a checklist and demo script.",
            source_reasoning="Original author's missing-answer draft.",
        )
    )
    calls = []
    body = "## Checklist\n- Choose the project.\n\n## Demo\nOpen the project and show the saved result."

    def request(role, n, messages, schema):
        calls.append((role, messages))
        reply = (
            body
            if role == "student"
            else json.dumps(
                {
                    "decision": "supported",
                    "diagnosis": "Delivers both artifacts.",
                    "reusable": [],
                }
            )
        )
        return dict(content=reply, finish_reason="stop", reasoning_content="")

    result = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 2)
    assert (
        result["status"] == "unverified"
        and result["verification"]["answer_key_check"] is None
    )
    assert result["answer"] == body and result["messages"][-1]["content"] == body
    assert "Final answer:" not in calls[0][1][0]["content"]
    assert source["source_reasoning"] in calls[0][1][-1]["content"]


def test_numeric_sentence_is_read_only_as_an_unqualified_single_declared_final():
    gold = {"kind": "number", "value": "99"}
    heading = "按分类计数求和。\n**最终答案：**\n"
    observed = "满足条件的不同记录共有 **274** 个。"
    checked = inspect_answer(heading + observed, gold)
    assert (
        checked["answer"],
        checked["answer_surface"],
        checked["answer_matches"],
        checked["issue"],
    ) == ("274", observed, False, "answer_key_mismatch")
    assert inspect_answer(heading + "满足条件的不同记录共有 **99** 个。", gold)[
        "answer_matches"
    ]
    for ambiguous in (
        "可能共有 99 个。",
        "若相同则共有 99 个。",
        "共有 98 或 99 个。",
        "不是 99 个。",
        "先举例说明 99 个。",
    ):
        result = inspect_answer(heading + ambiguous, gold)
        assert result["answer"] is None and result["answer_matches"] is None


def test_v34_recognizes_both_recorded_terminal_answers_without_certifying_the_steps():
    question = "How many positive three-digit integers with each digit greater than 4 are divisible by 6?"
    statement = r"The number of positive three-digit integers with each digit greater than 4 that are divisible by 6 is \boxed{16}."
    original = "Counting these, we get 16 numbers.\n\n### Final Answer:\n" + statement
    bold = "Let me present the final answer clearly.\n\n**Final Answer: 16**"
    gold = {"kind": "number", "value": "16"}
    for text, surface in ((original, statement), (bold, "16")):
        check = inspect_answer(text, gold, question=question)
        assert (check["answer"], check["answer_surface"], check["answer_matches"]) == ("16", surface, True)
        assert inspect_answer(text, {"kind": "number", "value": "15"}, question=question)["answer_matches"] is False
        assert not inspect_answer(text, gold, question=question, marked_finals=False)["format_ok"]

    def request(role, index, messages, schema):
        if role == "student":
            return {"content": bold, "reasoning_content": "", "finish_reason": "stop"}
        return {"content": json.dumps({"decision": "a1.s1", "diagnosis": "The cited step does not prove the count.",
                                       "reusable": []}), "reasoning_content": "", "finish_reason": "stop"}

    source = validate_source({"id": "count", "question": question, "gold": gold})
    for version, expected in (("3.3.0", None), ("3.4.0", True)):
        result = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 1, protocol_version=version)
        assert result["attempts"][0]["check"]["answer_matches"] is expected
        assert result["status"] == "quarantine"


def test_v34_leaves_nonterminal_conditional_negated_or_competing_numeric_answers_ungraded():
    question = "How many records are divisible by 6?"
    heading = "We enumerated the records.\n### Final Answer:\n"
    for statement in (
        r"If the list is complete, the answer is \boxed{16}.",
        r"The number of records that are divisible by 6 is not \boxed{16}.",
        r"The number of records that are divisible by 6 is either \boxed{16} or 17.",
        r"The number of records that are divisible by 6 is \boxed{16} if repetitions are allowed.",
        r"The number of records that are divisible by 5 is \boxed{16}.",
        r"The answer is \boxed{15} or \boxed{16}.",
        "**Final Answer: 16**\nFinal Answer: 17",
    ):
        check = inspect_answer(heading + statement, {"kind": "number", "value": "16"}, question=question)
        assert not check["format_ok"] and check["answer_matches"] is None
    assert inspect_answer("A code example follows.\n```text\n**Final Answer: 16**",
                          {"kind": "number", "value": "16"}, question=question)["answer_matches"] is None


def test_reviewed_set_survives_a_zero_prefix_with_its_source_and_no_unfound_checks():
    rule = {
        "id": "counts",
        "kind": "finite_set",
        "variable": "k",
        "values": [1, 4, 7],
        "meaning": "k 表示序列中 1 的个数。",
        "source": "independent enumeration",
    }
    secret = {
        **rule,
        "id": "unfound",
        "variable": "z",
        "meaning": "Unfound private assertion.",
    }
    source = validate_source(
        dict(
            id="counts",
            question="计算满足条件的记录总数。",
            gold={"kind": "number", "value": "99"},
            progress_checks=[rule, secret],
        )
    )
    body = r"2. 1 的总数除以 3 余 1（即 1 的总数 $k \in \{1, 4, 7\}$，因为最大长度为 9，且 $k \le 9$）。"
    calls = []

    def request(role, index, messages, schema):
        calls.append((role, messages))
        if role == "student":
            content = (
                body + "\n误将其余情况一起计数。\nFinal answer: 274"
                if index == 1
                else "将已确认的情形分别计数并相加。\nFinal answer: 99"
            )
        else:
            content = json.dumps(
                dict(
                    decision="revise:a1.s1" if index == 1 else "supported",
                    diagnosis="Check the filter.",
                    reusable=[],
                )
            )
        return {"content": content, "finish_reason": "stop", "reasoning_content": ""}

    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 2, protocol_version="3.5.0")
    assert (
        case["status"] == "accepted"
        and case["draft_versions"][0]["retained_after"] == []
    )
    (fact,) = case["shared_student_findings"]
    start, end = fact["source_span"]
    assert body[start:end] == fact["text"] == r"k \in \{1, 4, 7\}"
    raw = case["attempts"][0]["student"]["content"]
    piece = next(
        s
        for s in case["draft_versions"][0]["student_suffix"]
        if s["id"] == fact["segment_id"]
    )
    assert (
        raw[piece["start"] : piece["end"]] == piece["text"]
        and fact["text"] in piece["text"]
    )
    assert (
        case["draft_versions"][0]["program_confirmed"] == [fact]
        and fact["author"] == "student"
    )
    assert calls[0][1][-1]["content"] == source["question"]
    material = json.loads(calls[2][1][-1]["content"].split("\n", 1)[1])
    assert material["confirmed_meanings"] == [
        {"student_excerpt": fact["text"], "reviewed_meaning": rule["meaning"]}
    ]
    assert "274" not in json.dumps(calls[2], ensure_ascii=False)
    assert "Unfound private assertion" not in json.dumps(calls, ensure_ascii=False)
    for wrong in (
        "仅有 1,4,7。",
        "j ∈ {1,4,7}。",
        "k ∈ {1,4}。",
        "k ∈ {1,4,7,8}。",
        "若 k ∈ {1,4,7} 则总数是 2。",
        "可能 k ∈ {1,4,7}。",
        "并非 k ∈ {1,4,7}。",
        "This is a guess:\nk ∈ {1,4,7}.",
        "2k ∈ {1,4,7}.",
        "k ∈ {1,4,7} ∪ {9}.",
        "k∈{1,4,7}。\nk∈{1,4,9}。",
        "k={1,4,7}.",
    ):
        assert not checked_progress([rule], draft_fragment(wrong, 1)[0], wrong), wrong
    reordered = "所以 k ∈ {7,1,4}。"
    assert checked_progress([rule], draft_fragment(reordered, 1)[0], reordered)


def test_length_limit_archives_only_declared_progress_and_attributes_this_occurrence():
    rule = {"id": "ones-residue", "kind": "finite_set", "source": "independent enumeration",
            "variable": "k", "values": [1, 4, 7], "meaning": "k 的候选总数已由枚举核对。"}
    source = validate_source({"id": "sequences", "question": "计算满足条件的序列个数。",
                              "gold": {"kind": "number", "value": "99"}, "progress_checks": [rule]})
    first = r"所以，$k \in \{1, 4, 7\}$。" + "\n没有验证其余情况。\nFinal answer: 274"
    truncated = (
        r"2. 1 的总数除以 3 余 1（即 1 的总数 $k \in \{1, 4, 7\}$）。"
        "\n- **小计：9 种**。\n- **小计：90 种**。\n如果 0 的位置是"
    )
    calls = []

    def request(role, index, messages, schema):
        calls.append((role, index))
        if role == "coach":
            assert index == 1
            content = json.dumps({"decision": "revise:a1.s1", "diagnosis": "Check the count.", "reusable": []})
        else:
            content = first if index == 1 else truncated
        return {"content": content, "reasoning_content": "", "finish_reason": "length" if index == 2 else "stop"}

    for version in ("3.4.0", "3.5.0"):
        calls.clear()
        row = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 4, protocol_version=version)
        assert calls == [("student", 1), ("coach", 1), ("student", 2)]
        assert row["status"] == "quarantine" and row["answer"] is None
        attempt = row["attempts"][-1]
        assert attempt["coach"] is None and attempt["check"]["answer_matches"] is None
        first_fact, = row["shared_student_findings"]
        assert first_fact["segment_id"].startswith("a1.") and attempt["student"]["content"] == truncated
        if version == "3.4.0":
            assert "program_confirmed" not in attempt
            continue
        fact, = attempt["program_confirmed"]
        start, end = fact["source_span"]
        assert truncated[start:end] == fact["text"] == r"k \in \{1, 4, 7\}"
        assert (fact["source_attempt"], fact["check_id"], fact["reviewed_source"]) == (2, rule["id"], rule["source"])
        assert fact["previously_confirmed_segment_id"] == first_fact["segment_id"]
        assert fact["previous_disclosure_ids"] == [clue["id"] for clue in row["disclosures"]]


def test_length_limit_never_promotes_native_thinking_or_unchecked_statements():
    rule = {"id": "k-set", "kind": "finite_set", "source": "enumeration",
            "variable": "k", "values": [1, 4, 7], "meaning": "该变量的取值经过枚举核对。"}
    source = validate_source({"id": "partial", "question": "计算序列个数。", "gold": {"kind": "number", "value": "99"},
                              "progress_checks": [rule]})
    positive = "所以，k ∈ {1,4,7}。\n其余情况待核对。"
    for content, reasoning, finish, expected in (
        (positive, "", "length", 1), (positive, "private analysis", "length", 0),
        ("<think>\n" + positive, "", "length", 0), ("", "", "length", 0),
        ("Example:\n```text\n" + positive + "\n```", "", "length", 0),
        ("若 k ∈ {1,4,7}，则……", "", "length", 0), ("所以，k ∈ {1,4,7}", "", "length", 0),
        ("两项小计是 9 和 90。", "", "length", 0), (positive, "", "cancelled", 0),
    ):
        def request(role, index, messages, schema):
            assert role == "student" and index == 1
            return {"content": content, "reasoning_content": reasoning, "finish_reason": finish}

        row = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 4)
        assert row["status"] == "quarantine" and row["answer"] is None
        assert row["attempts"][0]["coach"] is None and len(row["shared_student_findings"]) == expected


def test_only_native_loopback_chat_waits_for_the_full_decode(monkeypatch):
    import io
    from lib.contexts.workflows.infrastructure import guided_llama

    received = []
    def open_request(request, timeout):
        received.append((request.full_url, timeout))
        return io.BytesIO(b'{}')

    monkeypatch.setattr(guided_llama, "urlopen", open_request)
    for host, local in (("127.0.0.1", True), ("example.test", False), ("example.test", True)):
        base = f"http://{host}/v1"
        endpoint = guided_llama.GuidedChatEndpoint(base, "example", None, 32768,
                                                  local=local, context=131072, pool=None)
        endpoint.http(endpoint.root + "/slots")
        endpoint.http(base + "/chat/completions", {"messages": []})
    assert [timeout for _, timeout in received] == [3600, None, 3600, 3600, 3600, 3600]
