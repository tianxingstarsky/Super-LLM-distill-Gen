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
    validate_source,
)


def test_three_round_http_history_uses_only_published_clues_and_freezes_resume(
    tmp_path,
    monkeypatch,
):
    from lib.model_request_scheduler import SharedModelScheduler

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
                        "first_unresolved_id": "a1.s2"
                        if one
                        else ("complete" if ok else "a2.s2"),
                        "verdict": "supported" if ok else "revise",
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
                    "verdict": "supported",
                    "diagnosis": "The source supports the reasoning.",
                    "first_unresolved_id": "complete",
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
                    "verdict": "supported",
                    "diagnosis": "Delivers both artifacts.",
                    "first_unresolved_id": "complete",
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
                    verdict="revise" if index == 1 else "supported",
                    first_unresolved_id="a1.s1" if index == 1 else "complete",
                    diagnosis="Check the filter.",
                    reusable=[],
                )
            )
        return {"content": content, "finish_reason": "stop", "reasoning_content": ""}

    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 2)
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
