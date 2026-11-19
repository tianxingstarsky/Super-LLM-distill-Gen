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


def test_greedy_direct_history_is_not_a_new_sample_and_all_later_calls_use_new_sampling(tmp_path):
    from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases
    from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint

    premise = "four apples in each basket"
    question = "There are three baskets and four apples in each basket. Return only the total."
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps(dict(id="q", question=question, gold={"kind": "number", "value": "12"},
                                     answer_requirement="final_only", direct_answer_syntax="number")))
    original_input = tmp_path / "original-input.jsonl"
    original_input.write_bytes(source.read_bytes())
    sent = []
    first_value = "8"
    def run(directory, version, **options):
        endpoint = GuidedChatEndpoint("http://localhost/v1", "qwen3.5-0.8b", None, 32768, local=True,
                                      context=98304, pool=None)
        endpoint.http = lambda url: [{"n_ctx": 98304}] if url.endswith("/slots") else {"data": [{"id": endpoint.model}]}
        def complete(payload):
            sent.append(payload)
            if len(sent) == 1:
                content = first_value
            elif len(sent) == 2:
                content = dict(decision="offer", question_quote=premise, bridge=premise + ".",
                               hint="Match one basket to the stated group size.", reason="Use equal groups.")
            elif len(sent) == 3:
                packet = json.loads(payload["messages"][-1]["content"])
                content = dict(decision="release", reason="The operation recovers the group size.",
                               citations=[dict(evidence_id="question", quote=premise)],
                               bridge=packet["proposed_next_teacher_disclosure"]["bridge"],
                               pairing=dict(student_fit=True, evidence_support=True, history_fit="first"))
            else:
                content = ""  # Stop after observing the coached student's actual payload.
            return dict(content=json.dumps(content) if isinstance(content, dict) else content,
                        finish_reason="stop" if content else "length", reasoning_content="", model=endpoint.model,
                        usage={"prompt_tokens": 20, "completion_tokens": 1})
        endpoint.complete = complete
        return run_guided_cases(source, directory, {"student": endpoint, "coach": endpoint},
                                {"version": "test"}, 2, protocol_version=version, workers=1,
                                until_correct=False, **options)
    original, child = tmp_path / "old", tmp_path / "child"
    assert run(original, "4.6.0", first_pass_only=True)["first_pass_complete"]
    assert len(sent) == 1 and sent[0]["temperature"] == 0
    summary = run(child, "4.7.0", historical_first_from=original)
    assert summary["model_calls"]["historical_first_inputs"] == 1 and summary["model_calls"]["new_completed"] == 3
    assert summary["historical_first_outcomes"]["new_sampling_first_answers"] == 0
    assert summary["historical_first_usage"] == {"prompt_tokens": 20, "completion_tokens": 1}
    assert [p["chat_template_kwargs"]["enable_thinking"] for p in sent] == [False, True, True, False]
    assert [p["presence_penalty"] for p in sent[1:]] == [1.5, 1.5, 2.0]
    folder = next((child / "cases").iterdir())
    frozen = json.loads((folder / "001-student.json").read_bytes())
    assert frozen["origin"]["kind"] == "historical_greedy_first" and frozen["request"] == sent[0]
    assert frozen["origin"]["current_unsent_wire_sha256"] != frozen["origin"]["wire_request_sha256"]
    assert json.loads((child / "quarantine.jsonl").read_bytes())["first_answer_origin"] == frozen["origin"]
    assert run(child, "4.7.0") == summary and len(sent) == 4
    source.write_bytes(original_input.read_bytes() + b"\n")
    subset = tmp_path / "subset"
    assert run(subset, "4.7.1", historical_first_from=original, historical_first_input=original_input,
               first_pass_only=True)["first_pass_complete"] and len(sent) == 4
    record = json.loads(next(subset.glob("cases/*/001-student.json")).read_bytes())
    assert record["origin"]["original_input_record"]["line"] == 1
    assert record["origin"]["original_input_record"]["original_line_sha256"] == hashlib.sha256(original_input.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="original input bytes differ"):
        run(tmp_path / "unbound-input", "4.7.1", historical_first_from=original, historical_first_input=source,
            first_pass_only=True)
    source.write_text(source.read_text().replace("four apples", "five apples"))
    mismatch = run(tmp_path / "different", "4.7.0", historical_first_from=original, historical_first_input=original_input)
    assert mismatch["first_pass_complete"] is False and len(sent) == 4
    first_value = "12"
    sent.clear()
    source.write_text(source.read_text().replace("five apples", "four apples"))
    correct = tmp_path / "old-correct"
    assert run(correct, "4.6.0", first_pass_only=True)["counts"]["accepted"] == 1
    imported = run(tmp_path / "new-correct", "4.7.1", historical_first_from=correct)
    assert len(sent) == 1 and imported["model_calls"]["new_completed"] == 0
    assert imported["historical_first_outcomes"]["accepted_old_direct_answers"] == 1
    assert imported["historical_first_outcomes"]["new_sampling_first_answers"] == 0


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


def test_lease_timeout_resumes_only_unsent_guided_requests(tmp_path):
    from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases
    from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint
    from lib.model_request_scheduler import SharedModelScheduler, model_pool_identity

    pool = SharedModelScheduler(tmp_path, model_pool_identity("http://localhost/v1", "", "student"),
                                policy={"max_concurrency": 1, "request_queue_timeout_seconds": 1})
    endpoint = GuidedChatEndpoint("http://localhost/v1", "student", None, 32768, local=False,
                                  context=98304, pool=pool)
    source = tmp_path / "questions.jsonl"
    source.write_text(json.dumps({"id": "sum", "question": "What is 2 + 3?", "answer_requirement": "final_only",
                                  "gold": {"kind": "number", "value": "5"}}), "utf8")
    requests = []
    def respond(_url, body):
        requests.append(body)
        return {"choices": [{"message": {"content": "5"}, "finish_reason": "stop"}]}
    endpoint.http = respond

    def run(root, **options):
        return run_guided_cases(source, root, {"student": endpoint, "coach": endpoint}, {"version": "test"}, 2,
                                protocol_version="4.2.1", workers=1, first_pass_only=True, **options)

    root = tmp_path / "original"
    with pool.acquire():
        blocked = run(root)
    assert not requests and not blocked["first_pass_complete"] and not blocked["complete"]
    assert blocked["admission_timeouts"] == blocked["counts"]["awaiting_capacity"] == 1
    assert not any(blocked["model_calls"].values()) and (root / "quarantine.jsonl").read_bytes() == b""
    checkpoint = next(root.glob("cases/*/001-student.json"))
    event = json.loads(checkpoint.read_bytes())
    assert event["attempts"][-1]["model_request_sent"] is False

    # Reproduce the exact pre-fix on-disk receipt issued by the shared scheduler.
    previous = {"status": "interrupted", "error": "ModelQueueTimeout", "http_status": "model_request_queue_timeout"}
    event.update(status="interrupted", attempts=[previous])
    checkpoint.write_text(json.dumps(event), "utf8")
    stop = checkpoint.with_name("interruption.json")
    old = json.loads(stop.read_bytes())
    old.update(status="interrupted", error="ModelQueueTimeout", detail="model_request_queue_timeout")
    old.pop("checkpoint")
    stop.write_text(json.dumps(old), "utf8")
    exported = run(root, export_only=True)
    assert exported["counts"]["awaiting_capacity"] == 1 and not any(exported["model_calls"].values())
    resumed = run(root)
    assert resumed["complete"] and resumed["accepted_by_route"]["nonthinking_direct"] == 1
    assert resumed["model_calls"]["new_completed"] == 1 and resumed["admission_timeouts"] == 1
    assert json.loads(checkpoint.read_bytes())["attempts"][0] == previous and len(requests) == 1
    assert run(root) == resumed and len(requests) == 1

    def lost_response(_url, body):
        requests.append(body)
        raise TimeoutError("response deadline")
    endpoint.http = lost_response
    unknown = tmp_path / "unknown"
    assert run(unknown)["model_calls"]["interrupted_or_pending"] == 1
    endpoint.http = respond
    assert run(unknown)["counts"]["interrupted"] == 1 and len(requests) == 2
    assert run(unknown, retry_interrupted=True)["complete"] and len(requests) == 3


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
        matching = generate_guided_cases(path, tmp_path / "all-matching", **args, reuse_matching_from=root)
        assert matching["model_calls"]["new_completed"] == 0 and matching["model_calls"]["replayed_matching"] == 6
        assert matching["counts"]["accepted"] == 1 and len(calls) == 6
        with pytest.raises(ValueError, match="same input_sha256"):
            generate_guided_cases(different, tmp_path / "wrong-source", **args, reuse_matching_from=root)
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
                    **({"independent_approaches": []} if "independent_approaches" in schema["properties"] else {}),
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
    from lib.contexts.workflows.domain.guided_teaching_steps import verified_steps_digest
    q = sources[0]
    step = {"id": "identify", "kind": "operation", "quote": q["question"], "span": [0, len(q["question"])],
            "operation": "Identify the unit of the requested count.", "verification": "PRIVATE independent certificate"}
    receipt_id = verified_steps_digest(q["id"], q["question"], q["gold"], [step])
    q["verified_teaching_steps"] = {"receipt_id": receipt_id, "steps": [step]}
    path.write_text(json.dumps(q), encoding="utf8")
    registry = tmp_path / "checked.json"
    registry.write_text(json.dumps({"schema": "guided-teaching-verification/v1", "receipts": [receipt_id]}), encoding="utf8")
    catalog_run = tmp_path / "guided-catalog"
    with pytest.raises(ValueError, match="trusted independent"):
        run_guided_cases(path, catalog_run, endpoints, {"version": "default"}, 2, protocol_version="4.1.0")
    catalog = run_guided_cases(path, catalog_run, endpoints, {"version": "default"}, 2,
                               protocol_version="4.1.0", verified_step_receipts=registry)
    assert catalog["accepted_by_route"]["nonthinking_direct"] == 1 and "PRIVATE" not in str(actual[-1])
    registry.unlink()
    assert run_guided_cases(path, catalog_run, {}, {}, 2, export_only=True) == catalog
    (catalog_run / "verified-step-receipts.json").write_text(json.dumps(
        {"schema": "guided-teaching-verification/v1", "receipts": ["0" * 64]}), encoding="utf8")
    with pytest.raises(ValueError, match="registry differs"):
        run_guided_cases(path, catalog_run, {}, {}, 2, export_only=True)


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
        return GenerateGuidedAnswer(call).run(source, {"version": "initial"}, 2, protocol_version="4.0.0"), roles
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


def test_private_judge_self_references_get_one_evidence_repair_and_never_become_learner_difficulties():
    from jsonschema import Draft202012Validator
    from lib.contexts.workflows.domain.guided_student_evidence import normalize_student_findings
    from lib.prompts.workflow_guided_answers import ordered_finding_schema

    task = validate_source(dict(id="dinner", question="Four meals cost $20 each. One appetizer costs $10. "
                                "What is the bill with a 20% tip?", gold=dict(kind="number", value="108"),
                                answer_requirement="final_only"))
    # Eighteen actual solution segments reproduce the failure mode seen on the dinner receipt.
    student = ("List the orders.\nThere are four meals.\nEach costs twenty dollars.\n"
               "The meal subtotal is four times twenty.\nThat is eighty dollars.\nThere is one appetizer.\n"
               "Its price is ten dollars.\nAdd it to the meal subtotal.\nThe pre-tip bill is ninety dollars.\n"
               "The tip rate is twenty percent.\nTwenty percent is one fifth.\nUse the pre-tip bill as the base.\n"
               "A fifth of ninety is eighteen.\nThe gratuity is eighteen dollars.\nInclude that sum in the total.\n"
               "Ninety plus eighteen gives one hundred eight.\nThe bill includes the appetizer.\n"
               "It also includes the gratuity.\nFinal answer: 108")
    ids = [f"a1.s{i}" for i in range(1, 19)]
    def finding(sid, basis, dependencies=()):
        return dict(segment_id=sid, basis_id=basis, depends_on=list(dependencies))
    old = [finding(sid, sid) for sid in ids]
    fixed = [finding(sid, "question" if i == 0 else i) for i, sid in enumerate(ids)]
    grammar = Draft202012Validator(ordered_finding_schema(ids))
    assert grammar.is_valid(fixed) and not grammar.is_valid(old)
    assert not grammar.is_valid([finding(ids[1], 2)]) and not grammar.is_valid([finding(ids[1], 3)])
    assert not grammar.is_valid([finding(ids[0], "question", [2])])
    assert not normalize_student_findings([finding(ids[2], 4), finding(ids[1], 1, [3])], ids)[0]
    normalized, errors = normalize_student_findings([finding(ids[2], 1, [2])], ids)
    assert not errors and normalized == [finding(ids[2], "a1.s1", ["a1.s2"])]

    def run(mode):
        events = []
        def request(role, index, messages, schema):
            events.append(role)
            if role == "student":
                content = student if mode != "bad_reasoning" else "Four times twenty is one hundred.\nFinal answer: 108"
            elif role == "coach":
                packet = json.loads(messages[-1]["content"])
                assert packet["program_check"]["answer_matches"] is True
                if mode == "bad_reasoning":
                    content = dict(decision="revise:a1.s1", diagnosis="Four times twenty is eighty.", reusable=[])
                else:
                    assert packet["ordered_segment_ids"] == ids
                    content = dict(decision="supported", diagnosis="The pre-tip subtotal is 90 and the tip is 18.",
                                   reusable=old)
                content = json.dumps(content)
            elif role == "coach_evidence_repair":
                p = json.loads(messages[-1]["content"])
                assert len(p["evidence_protocol_errors"]) == 18 and p["prior_private_assessment"]["reusable"] == old
                content = json.dumps(dict(decision="correct_evidence", reason="I traced these claims to their inputs.",
                                          reusable={"fixed": fixed, "repeat": old, "discard": []}[mode]))
            elif role == "disclosure_draft":
                content = json.dumps(dict(decision="insufficient", bridge="", hint="", question_quote="",
                                          reason="No new publishable clue was proposed."))
            else:
                assert role == "final_answer"
                content = "108"
            return dict(content=content, reasoning_content="", finish_reason="stop")
        result = GenerateGuidedAnswer(request).run(task, {"version": "test"}, 2, protocol_version="4.2.1")
        return result, events
    case, events = run("fixed")
    assert events == ["student", "coach", "coach_evidence_repair", "final_answer"] and case["status"] == "accepted"
    assert case["attempts"][0]["coach"]["reusable"][-1]["basis_id"] == ids[-2]
    assert json.loads(case["attempts"][0]["coach_response"]["content"])["reusable"] == old
    assert student.split("\nFinal answer:")[0] == case["messages"][-1]["reasoning_content"]
    for mode in ("repeat", "discard"):
        case, events = run(mode)
        assert case["status"] == "awaiting_guidance" and case["continuation"]["reason"].startswith(
            ("invalid_coach_evidence", "coach_evidence_changed"))
        assert events == ["student", "coach", "coach_evidence_repair"] and not case["disclosures"]
    case, events = run("bad_reasoning")
    assert events == ["student", "coach", "disclosure_draft"] and case["status"] != "accepted"


def test_verified_source_operations_require_external_identity_and_independent_contextual_release():
    from lib.contexts.workflows.domain.guided_teaching_steps import verified_steps_digest
    question = "Projects A and D conflict. Each costs one unit. Which selection produces the greatest return?"
    source = {"id": "portfolio", "question": question, "gold": {"kind": "exact", "value": "BC"},
              "answer_requirement": "final_only"}
    def step(sid, quote, operation):
        start = question.index(quote)
        return {"id": sid, "kind": "operation", "quote": quote, "span": [start, start + len(quote)],
                "operation": operation, "verification": "Private independent exhaustive oracle evidence."}
    ops = [step("conflict", "Projects A and D conflict.", "Check whether the proposed pair contains both named projects."),
           step("cost", "Each costs one unit.", "Count the units used by the remaining projects.")]
    def signed(operations):
        digest = verified_steps_digest(source["id"], question, source["gold"], operations)
        return {**source, "verified_teaching_steps": {"receipt_id": digest, "steps": operations}}, digest
    row, digest = signed(ops)
    assert "verified_teaching_steps" not in validate_source(row)
    with pytest.raises(ValueError, match="trusted"):
        validate_source(row, allow_verified_teaching_steps=True)
    altered = {**row, "question": question + "!"}
    with pytest.raises(ValueError, match="does not match"):
        validate_source(altered, allow_verified_teaching_steps=True, trusted_step_receipts={digest})
    for bad_step in ({**ops[0], "span": [1, 6]}, {**ops[0], "operation": "The answer is BC."}):
        bad, bad_digest = signed([bad_step])
        with pytest.raises(ValueError):
            validate_source(bad, allow_verified_teaching_steps=True, trusted_step_receipts={bad_digest})
    good = validate_source(row, allow_verified_teaching_steps=True, trusted_step_receipts={digest})

    def run(previous_quote=True, withhold_first=False, version="4.1.1", second_result="AD"):
        calls = []
        def request(role, index, messages, schema):
            calls.append((role, index))
            if role == "student":
                if index == 1:
                    content = "AD"
                else:
                    view = json.loads(messages[-1]["content"].split("\n", 1)[1])
                    assert "Private" not in messages[-1]["content"] and "task-span" not in messages[-1]["content"]
                    assert view["new_clue"] == ops[index - 2]["operation"]
                    content = (("These two projects conflict.\nI still select the pair." +
                                (f"\nFinal answer: {second_result}" if second_result else "")) if index == 2
                               else "Rechecking the remaining candidates gives the eligible pair.\nFinal answer: BC")
            elif role == "coach":
                content = json.dumps({"decision": "revise:a2.s2" if index == 2 else "supported",
                                      "reusable": [], "diagnosis": "Check the revised selection."})
            elif role == "final_answer":
                content = "BC"
            else:
                packet = json.loads(messages[-1]["content"])
                assert "Private independent" not in messages[-1]["content"]
                difficulty = packet["current_difficulty"]
                if version == "4.1.1":
                    expected = {"AD": "incorrect", "BC": "correct", None: "not_checked"}
                    assert difficulty["submitted_answer_status"] == ("incorrect" if index == 1 else expected[second_result])
                    assert difficulty["derivation_available"] is (index > 1)
                    assert "answer_key" not in packet and "BC" not in json.dumps(packet)
                else:
                    assert "submitted_answer_status" not in difficulty and "derivation_available" not in difficulty
                sid = ("conflict", "cost")[index - 1]
                if role in ("disclosure_draft", "disclosure_rewrite"):
                    assert sid in [op["id"] for op in packet["available_operations"]]
                    if index == 2:
                        assert len(packet["available_operations"]) == 1
                        assert packet["ordered_previous_teacher_disclosures"][0]["text"] == ops[0]["operation"]
                    content = json.dumps({"decision": "select:" + sid, "reason": "Apply the new constraint here."})
                else:
                    assert packet["proposed_next_teacher_disclosure"]["operation"] == ops[index - 1]["operation"]
                    references = ["task-span:" + sid] + (["disclosed.1"] if index == 2 and previous_quote else [])
                    content = json.dumps({"decision": "withhold" if withhold_first else "release",
                                          "evidence_ids": references, "reason": "I checked this move against the current work."})
            return {"content": content, "reasoning_content": "", "finish_reason": "stop"}
        return GenerateGuidedAnswer(request).run(good, {"version": "initial"}, 3, protocol_version=version), calls
    passed, events = run()
    assert passed["status"] == "accepted" and [d["text"] for d in passed["disclosures"]] == [s["operation"] for s in ops]
    assert [d["source_step_id"] for d in passed["disclosures"]] == ["conflict", "cost"]
    assert passed["disclosures"][0]["citations"] == [{"evidence_id": "question", "quote": ops[0]["quote"],
                                                      "span": ops[0]["span"]}]
    assert events.count(("disclosure", 1)) == events.count(("disclosure", 2)) == 1
    withheld, events = run(withhold_first=True)
    assert not withheld["disclosures"] and ("student", 2) not in events and ("disclosure_recheck", 1) not in events
    broken, _ = run(previous_quote=False)
    assert len(broken["disclosures"]) == 1 and broken["status"] == "awaiting_guidance"
    frozen, _ = run(version="4.1.0")
    assert frozen["status"] == "accepted"
    for second in ("BC", None):
        reviewed, _ = run(second_result=second)
        assert reviewed["status"] == "accepted"


def test_public_disclosure_quotes_are_paired_exact_and_separate_from_teacher_inferences():
    from lib.contexts.workflows.domain.guided_disclosure_text import locate_authored_task_citations, render_teacher_text
    question = 'The sign reads "left at dusk". 两组成员之间互不重叠。标签为“入口”。'
    text = 'Compare the route with “The sign reads "left at dusk"”. 两组成员之间互不重叠，先分别记数。'
    rendered, quotes = render_teacher_text(text, question)
    assert '“两组成员之间互不重叠”，先分别记数' in rendered
    assert [c["quote"] for c in quotes] == ['The sign reads "left at dusk"', '两组成员之间互不重叠']
    nested, citations = render_teacher_text('"标签为“入口”"并不代表出口。', question)
    assert nested.startswith('"标签为“入口”"') and citations[0]["quote"] == '标签为“入口”'
    for c in quotes:
        assert question[slice(*c["source_span"])] == rendered[slice(*c["public_span"])] == c["quote"]
    for malformed in ('“left at noon”', '”left at dusk“', '"left at dusk', '题面写道：两个社团重复。'):
        with pytest.raises(ValueError):
            render_teacher_text(malformed, question)
    assert render_teacher_text("Draw a diagram.", question) == ("Draw a diagram.", [])
    repeated = "Gate A: mark one blue item. Gate B: mark one blue item."
    anchor = "one blue item"
    second = repeated.rindex(anchor)
    span = [second, second + len(anchor)]
    for body in ("blue item", anchor, "mark one blue item"):
        _, positions = render_teacher_text(f'Count “{body}”.', repeated, anchor, anchor_span=span)
        start = repeated.rindex(body)
        assert positions[0]["source_span"] == [start, start + len(body)]
    with pytest.raises(ValueError, match="ambiguous"):
        render_teacher_text('Count “blue item”.', repeated, anchor)
    premise = "Gate B: mark one blue item"
    _, positions = render_teacher_text('Count “blue item”.', repeated, premise)
    assert positions[0]["source_span"][0] == repeated.rindex("blue item")
    old = dict(evidence_id="question", quote="blue item", span=[17, 26])
    assert locate_authored_task_citations([old], repeated, premise)[0]["span"] == positions[0]["source_span"]
    with pytest.raises(ValueError, match="ambiguous"):
        locate_authored_task_citations([old], repeated, anchor)


def test_freewritten_teacher_quote_is_checked_before_review_and_delivery():
    question = "The first basket contains two red apples. How many red apples are there?"
    premise = "The first basket contains two red apples"
    source = validate_source(dict(id="baskets", question=question, gold=dict(kind="number", value="2"),
                                  answer_requirement="final_only"))
    def run(bad, version="4.2.0", citation=premise):
        delivered, reviewed = [], []
        def request(role, index, messages, schema):
            if role == "student" and index == 1:
                return dict(content="8", reasoning_content="", finish_reason="stop")
            if role == "student":
                delivered.append(json.loads(messages[-1]["content"].split("\n", 1)[1])["new_clue"])
                return dict(content="", reasoning_content="", finish_reason="length")
            packet = json.loads(messages[-1]["content"])
            if role in {"disclosure_draft", "disclosure_rewrite"}:
                qualifier = '“there are eight red apples”' if bad else premise
                value = dict(decision="offer", question_quote=citation, bridge=qualifier + ".",
                             hint="Mark only the apples of the requested colour.", reason="Count the relevant items.")
            else:
                assert role == "disclosure" and packet["student_attempt"] == 1
                candidate = packet["proposed_next_teacher_disclosure"]
                reviewed.append(candidate["public_text"])
                value = dict(decision="release", citations=[dict(evidence_id="question", quote=citation)],
                             bridge=candidate["bridge"], reason="The action checks the colour the student must count.",
                             pairing=dict(student_fit=True, evidence_support=True, history_fit="first"))
            return dict(content=json.dumps(value), reasoning_content="", finish_reason="stop")
        out = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 2, protocol_version=version)
        return out, reviewed, delivered
    out, reviewed, delivered = run(False)
    assert reviewed == delivered == [out["disclosures"][0]["text"]]
    assert reviewed[0].startswith("“" + premise + "”.")
    out, reviewed, delivered = run(True)
    assert out["status"] == "awaiting_guidance" and reviewed == delivered == []
    wrapped = '"' + premise + '"'
    old, reviewed, delivered = run(False, "4.7.0", wrapped)
    assert not old["disclosures"] and reviewed == delivered == []
    current, reviewed, delivered = run(False, "4.7.1", wrapped)
    assert reviewed == delivered == [current["disclosures"][0]["text"]]
    draft = current["attempts"][0]["disclosure_work"][0]
    assert draft["draft"]["question_quote"] == draft["assessment"]["citations"][0]["quote"] == wrapped
    assert draft["question_premise"] == {"quote": premise, "source_span": [0, len(premise)]}
    assert current["disclosures"][0]["citations"] == [
        {"evidence_id": "question", "quote": premise, "span": [0, len(premise)]}]


def test_teacher_premise_matches_literal_delimiters_first_and_only_unwraps_a_unique_verbatim_span():
    from lib.contexts.workflows.domain.guided_disclosure_text import locate_teacher_premise
    question = 'The signs read “do not enter” and do not enter. Pressure ≤ 12 kPa.'
    for submitted, expected in [('“do not enter”', '“do not enter”'), ('"Pressure ≤ 12 kPa"', 'Pressure ≤ 12 kPa')]:
        start = question.index(expected)
        assert locate_teacher_premise(question, submitted) == {"quote": expected, "source_span": [start, start + len(expected)]}
    for invalid in ('"do not enter"', '“do enter”', '"Pressure < 12 kPa"', '“Pressure ≤ 21 kPa”',
                    '"pressure ≤ 12 kPa"', '“Pressure ≤ 12 kPa"', '“"Pressure ≤ 12 kPa"”', '""'):
        with pytest.raises(ValueError, match="specific_verbatim_task_premise"):
            locate_teacher_premise(question, invalid)


def test_recommend_remaining_verified_steps_until_correct_with_paired_reviews_and_distinct_checkpoints():
    from lib.contexts.workflows.domain.guided_teaching_steps import verified_steps_digest
    q = "两组成员之间互不重叠。第一组有一项，第二组有两项。求这两组的总数。"
    gold = {"kind": "number", "value": "3"}
    operations = []
    for sid, quote, action in (
        ("irrelevant", "第一组有一项", "检查第一组的标签。"),
        ("first", "第一组有一项", "第一组有一项，先给这一组画圈。"),
        ("second", "第二组有两项", "把另一组的成员分开标记。"),
        ("delivery", "求这两组的总数", "核对最终填入的数是否统计了所有圈出的成员。"),
    ):
        start = q.index(quote)
        operations.append(dict(id=sid, kind="operation", quote=quote, span=[start, start + len(quote)],
                               operation=action, verification="Independent fixture check of the set counts."))
    receipt = verified_steps_digest("groups", q, gold, operations)
    source = validate_source(dict(id="groups", question=q, gold=gold, answer_requirement="final_only",
                                  verified_teaching_steps=dict(receipt_id=receipt, steps=operations)),
                             allow_verified_teaching_steps=True, trusted_step_receipts={receipt})
    def run(veto=False):
        calls, received, proposals = [], [], []
        def request(role, index, messages, schema):
            calls.append((role, index))
            if role == "student":
                if index > 1:
                    public = json.loads(messages[-1]["content"].split("\n", 1)[1])
                    received.append(public["new_clue"])
                    if index == 4:
                        assert public["your_last_formal_answer"] == "2"
                text = ("5", "Still working on the group totals.\nFinal answer: 4",
                        "1 + 2 = 3.\nFinal answer: 3", "I count both groups together.\nFinal answer: 3")[index - 1]
            elif role == "coach":
                text = json.dumps(dict(decision="revise:a2.s1" if index == 2 else "supported",
                                       reusable=[], diagnosis="Check the group totals."))
            elif role == "final_answer":
                text = {3: "2", 4: "3"}[index]
            else:
                packet = json.loads(messages[-1]["content"])
                if role == "disclosure_draft":
                    text = json.dumps(dict(decision="insufficient", reason="I cannot choose between these operations."))
                else:
                    assert role == "disclosure_candidate_review"
                    candidate = packet["proposed_next_teacher_disclosure"]
                    assert candidate["step_id"] == operations[index - 1]["id"]
                    assert packet["student_attempt"] == (1 if veto else (1, 1, 2, 3)[index - 1])
                    if index == 4 and not veto:
                        assert packet["current_difficulty"]["observed_text_end"] == "2"
                        assert packet["current_difficulty"]["submitted_answer_status"] == "incorrect"
                    previous = packet["latest_disclosure_id"]
                    proposals.append(candidate["public_text"])
                    text = json.dumps(dict(decision="release", reason="Checked the premise, next action and current obstacle.",
                                           evidence_ids=[candidate["task_evidence_id"], *([previous] if previous else [])],
                                           pairing=dict(student_fit=not veto and index != 1, evidence_support=True,
                                                        history_fit="continuous" if previous else "first")))
            return dict(content=text, reasoning_content="", finish_reason="stop")
        case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 1, protocol_version="4.2.0",
                                                 until_correct=True)
        return case, calls, received, proposals
    result, calls, received, reviewed = run()
    assert result["status"] == "accepted", (result["issue"], calls)
    assert [i for role, i in calls if role == "disclosure_candidate_review"] == [1, 2, 3, 4]
    assert received == reviewed[1:] == [item["text"] for item in result["disclosures"]]
    assert received[0].startswith("“第一组有一项”")
    assert [item["review_checkpoint"] for item in result["disclosures"]] == [
        dict(role="disclosure_candidate_review", index=ordinal, student_attempt=ordinal-1) for ordinal in (2, 3, 4)]
    assert [p["attempt"] for p in result["finalization"]] == [3, 4]
    assert result["messages"][-1]["content"] == "3" and received[-1] in result["messages"][-1]["reasoning_content"]
    rejected, calls, received, _ = run(veto=True)
    assert rejected["status"] == "awaiting_guidance" and not received and not rejected["disclosures"]
    assert calls[-4:] == [("disclosure_candidate_review", i) for i in range(1, 5)]


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
                    "independent_approaches": [],
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


@pytest.mark.parametrize("repeating", [False, True])
def test_completed_length_draft_returns_to_teaching_before_training_admission(repeating):
    from lib.contexts.workflows.infrastructure.guided_answer_records import guided_port_response

    premise = "four apples in each basket"
    question = "There are three baskets and four apples in each basket. How many apples are there? Explain."
    source = validate_source(dict(id="baskets", question=question, gold=dict(kind="number", value="12"),
                                  answer_requirement="worked_solution", source_reasoning="Three groups of four give twelve."))
    unsupported = "This condition is not met."
    draft = (unsupported + "\n") * (48 if repeating else 1) + "I should check the count of"
    hint = "Match the number of baskets to the amount in one basket."
    solution = "Three groups of four give 3 × 4 = 12."
    roles = []
    def request(role, index, messages, schema):
        roles.append(role)
        if role == "student" and index == 1:
            response = dict(content=draft, reasoning_content="", finish_reason="length")
            return guided_port_response(response, {"chat_template_kwargs": {"enable_thinking": False}}, role, "4.6.0")
        if role == "student":
            assert unsupported not in str(messages) and hint in str(messages)
            return dict(content=solution + "\nFinal answer: 12", reasoning_content="", finish_reason="stop")
        packet = json.loads(messages[-1]["content"])
        if role in {"disclosure_draft", "disclosure"}:
            observation = packet["current_difficulty"]
            assert observation["receipt"] == "001-student.json" and observation["submitted_answer_status"] == "not_checked"
            assert all(draft[s["start"]:s["end"]] == s["text"] for s in observation["unverified_excerpts"])
            assert sum(len(s["text"]) for s in observation["unverified_excerpts"]) <= 1200
            assert packet["private_source_reference"]["attributed_source_reasoning"] == source["source_reasoning"]
            assert unsupported not in str(packet["established_evidence"])
        if role == "disclosure_draft":
            value = dict(decision="offer", question_quote=premise, bridge=premise + ".", hint=hint,
                         reason="Identify one group before multiplying.")
        elif role == "disclosure":
            value = dict(decision="release", citations=[dict(evidence_id="question", quote=premise)],
                         bridge=packet["proposed_next_teacher_disclosure"]["bridge"], reason="The count per group is explicit.",
                         pairing=dict(student_fit=True, evidence_support=True, history_fit="first"))
        elif role == "coach":
            value = dict(decision="supported", diagnosis="The product uses the stated sizes.", reusable=[],
                         independent_approaches=[])
        else:
            assert role == "teacher_final_answer" and unsupported not in str(packet)
            value = dict(decision="deliver", explanation=solution, conclusion="12", evidence_ids=["a2.s1"], reason="")
        return dict(content=json.dumps(value), reasoning_content="", finish_reason="stop")
    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 2, protocol_version="4.6.0")
    assert roles == ["student", "disclosure_draft", "disclosure", "student", "coach", "teacher_final_answer"]
    assert case["attempts"][0]["student"]["content"] == draft and draft in str(case["public_dialogue"][1]["content"])
    assert case["verification"]["answer_key_check"] is True and case["final_answer_provenance"]["attempt"] == 2
    assert case["messages"][-1]["content"].endswith("12")
    trace = case["messages"][-1]["reasoning_content"]
    assert case["status"] == ("unverified" if repeating else "accepted")
    assert (unsupported in trace) == repeating and hint in trace and solution in trace
    if repeating:
        assert case["verification"]["issue"] == "repetitive_truncated_trajectory_requires_review"
        assert case["attempts"][0]["truncated_observation"]["repetition"]["count"] == 48


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


def test_guided_worked_solution_carries_a_grounded_method_through_review_to_the_export(tmp_path, monkeypatch):
    from lib.contexts.workflows.domain.guided_teaching_steps import verified_steps_digest
    from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint
    from lib.model_request_scheduler import SharedModelScheduler
    from lib.prompts.workflow_guided_answers import REVIEW_TASK_ANALYSIS, STUDENT_TASK_ANALYSIS, TUTOR_TASK_ANALYSIS

    question = "There are three baskets and four apples in each basket. How many apples are there? Explain your calculation."
    gold = {"kind": "number", "value": "12"}
    quote = "four apples in each basket"
    task_instruction = "Use the same group size for every basket."
    hint = "Match one group to its stated size before you count all the groups."
    step = dict(id="size", kind="operation", quote=quote, span=[question.index(quote), question.index(quote) + len(quote)],
                operation=hint, verification="Each of the three groups has the same stated size.")
    receipt = verified_steps_digest("baskets", question, gold, [step])
    source = dict(id="baskets", question=question, gold=gold, task_system=task_instruction,
                  answer_requirement="worked_solution", verified_teaching_steps=dict(receipt_id=receipt, steps=[step]))
    source_file, registry = tmp_path / "sources.jsonl", tmp_path / "verification.json"
    source_file.write_text(json.dumps(source), "utf8")
    registry.write_text(json.dumps({"schema": "guided-teaching-verification/v1", "receipts": [receipt]}), "utf8")
    monkeypatch.setattr("lib.bootstrap.guided_answers.SharedModelScheduler",
                        lambda _root, identity, **kw: SharedModelScheduler(tmp_path, identity, **kw))

    method = "I can draw one group per basket."
    mistaken = "I put ten apples in each basket."
    private = "Private examiner note about the mistaken group size."
    private_basis = "This plan follows from the number of baskets independently of the wrong count."
    corrected = "Three groups of four give 3 × 4 = 12."
    formal = "Each basket holds four apples, and there are three baskets. Multiplying 3 by 4 gives 12.\nFinal answer: 12"
    proposal = dict(segment_id="a1.s2", question_quote="three baskets", justification=private_basis)
    scripts = [
        mistaken + "\n" + method + "\nFinal answer: 30",
        dict(decision="revise:a1.s1", diagnosis=private, reusable=[], independent_approaches=[proposal]),
        dict(decision="insufficient", reason="An independently verified operation would help here."),
        dict(decision="release", reason="The proposed action corrects the wrong group size.",
             evidence_ids=["task-span:size"], pairing=dict(student_fit=True, evidence_support=True, history_fit="first")),
        corrected + "\nFinal answer: 12",
        dict(decision="supported", diagnosis="The multiplication checks.", reusable=[], independent_approaches=[]),
    ]
    chats, final_prefixes = [], []

    def serve(endpoint, url, body=None):
        if url.endswith("/slots"):
            return [{"n_ctx": 98304}]
        if url.endswith("/models"):
            return {"data": [{"id": endpoint.model}]}
        if url.endswith("/apply-template"):
            return {"prompt": "\n".join(m["content"] for m in body["messages"]) + "\nAssistant\n<think>\n"}
        if url.endswith("/tokenize"):
            return {"tokens": list(body["content"].encode("utf8"))}
        if url.endswith("/completion"):
            final_prefixes.append(bytes(body["prompt"]).decode("utf8"))
            return dict(content=formal, stop_type="eos", tokens_evaluated=len(body["prompt"]), tokens_predicted=30)
        assert url.endswith("/v1/chat/completions")
        value = scripts[len(chats)]
        chats.append(body)
        return {"choices": [{"message": {"content": value if isinstance(value, str) else json.dumps(value)},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(GuidedChatEndpoint, "http", serve)
    run = tmp_path / "guided"
    summary = generate_guided_cases(source_file, run, student_url="http://localhost:8121/v1", student_model="student",
                                    coach_url="http://localhost:8122/v1", coach_model="coach",
                                    protocol_version="4.3.0", verified_step_receipts=registry, max_attempts=2)
    assert summary["accepted_by_route"]["guided_reasoning"] == 1
    assert [req["model"] for req in chats] == ["student", "coach", "coach", "coach", "student", "coach"]
    instructions = [req["messages"][0]["content"] for req in chats]
    for idx in (0, 4):
        assert STUDENT_TASK_ANALYSIS in instructions[idx] and task_instruction in instructions[idx]
    for idx in (1, 5):
        assert REVIEW_TASK_ANALYSIS in instructions[idx]
        assert json.loads(chats[idx]["messages"][-1]["content"])["task_system"] == task_instruction
    for idx in (2, 3):
        assert TUTOR_TASK_ANALYSIS in instructions[idx]
        packet = json.loads(chats[idx]["messages"][-1]["content"])
        assert packet["task_instructions"] == task_instruction
        assert packet["supported_student_approaches"] == [{"text": method + "\n", "question_quote": "three baskets"}]
        assert mistaken not in json.dumps(packet["established_evidence"])
    student_view = json.loads(chats[4]["messages"][-1]["content"].split("\n", 1)[1])
    assert student_view["supported_approaches_from_your_own_work"] == [method + "\n"]
    assert student_view["confirmed_findings_from_your_own_work"] == []
    assert student_view["new_clue"] == hint
    assert all(s not in str(chats[4]["messages"]) for s in (private, private_basis, mistaken, "30"))

    case_dir = next((run / "cases").iterdir())
    case = json.loads((case_dir / "case.json").read_bytes())
    assert (case_dir / "001-disclosure_candidate_review.json").is_file()
    public = json.loads((run / "accepted.jsonl").read_bytes())["messages"][-1]
    assert case["messages"][-1] == public and public["content"] == formal
    trace = public["reasoning_content"]
    assert trace.index(mistaken) < trace.index(hint) < trace.index(corrected)
    assert private not in trace and private_basis not in trace
    assert len(final_prefixes) == 1 and final_prefixes[0].endswith("<think>\n" + trace + "\n</think>\n\n")


def test_worked_delivery_requires_a_visible_explanation_after_closing_think_in_43():
    from lib.prompts.workflow_guided_answers import REVIEW_TASK_ANALYSIS, STUDENT_TASK_ANALYSIS, WORKED_ANSWER_DELIVERY

    source = validate_source({"id": "sum", "question": "Compute 2 + 3 and explain your answer.",
                              "gold": {"kind": "number", "value": "5"}, "answer_requirement": "worked_solution"})

    def run(version):
        calls = {}
        def send(role, index, messages, schema):
            calls[role] = messages
            if role == "student":
                text = "Combining two and three yields five.\nFinal answer: 5"
            elif role == "coach":
                assessment = dict(decision="supported", diagnosis="Both the operation and result follow.", reusable=[])
                if "independent_approaches" in schema["properties"]:
                    assessment["independent_approaches"] = []
                text = json.dumps(assessment)
            else:
                assert role == "final_answer"
                text = "5"
            return dict(content=text, reasoning_content="", finish_reason="stop")

        case = GenerateGuidedAnswer(send).run(source, {"version": "test"}, 1, protocol_version=version)
        return case, calls

    new, calls = run("4.3.0")
    assert new["status"] != "accepted" and new["answer"] == "5"
    check = new["attempts"][0]["formal_answer"]["check"]
    assert check["answer_matches"] and check["issue"] == "formal_answer_missing_requested_explanation"
    assert WORKED_ANSWER_DELIVERY in calls["final_answer"][0]["content"]
    old, calls = run("4.2.1")
    assert old["status"] == "accepted" and old["messages"][-1]["content"] == "5"
    assert STUDENT_TASK_ANALYSIS not in calls["student"][0]["content"]
    assert REVIEW_TASK_ANALYSIS not in calls["coach"][0]["content"]
    assert all(WORKED_ANSWER_DELIVERY not in m["content"] for m in calls["final_answer"])
