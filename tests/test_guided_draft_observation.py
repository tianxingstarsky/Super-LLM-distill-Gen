"""Keep learner conjectures visible to the tutor without turning them into submitted answers."""

import json

from lib.contexts.workflows.domain.guided_answers import answer_tail, latest_marked_draft_candidate
from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases
from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint
from test_guided_solver_receipts import endpoint


def test_candidate_offsets_match_original_markdown_and_exclude_fenced_examples():
    text = ("答案：甲\r\n再看一次。\r\n## **最终答案**\r\n  \r\n乙\r\n等等。\r\n"
            "```text\r\n答案：示例\r\n```")
    candidate = latest_marked_draft_candidate(text)
    left, right = candidate["span"]
    assert text[left:right] == candidate["quote"] == "## **最终答案**\r\n  \r\n乙"
    assert candidate["status"] == "unconfirmed_draft_candidate"
    assert latest_marked_draft_candidate("```\nFinal Answer: false\n```") is None
    assert answer_tail(text) == (None, None)


def test_default_batch_without_registry_preserves_claim_and_old_frozen_contract(tmp_path, monkeypatch):
    source = tmp_path / "proof.jsonl"
    source.write_text(json.dumps({"id": "deduction", "question": "A is red. All red things are warm. "
                                 "Is A warm? Show your work.", "gold": {"kind": "exact", "value": "True"},
                                 "source_reasoning": "A is red. Apply the warm rule to A.",
                                 "answer_requirement": "worked_solution"}), encoding="utf-8")
    written = "The rule mentions warm.\n**Final Answer:** Unknown.\nI need to check that."
    packets = []

    def reply(ep, payload):
        if ep.model == "solver":
            content = written
        else:
            packet = json.loads(payload["messages"][-1]["content"])
            if "current_difficulty" in packet:
                packets.append(packet)
                content = {"decision": "insufficient", "bridge": "", "hint": "", "question_quote": "",
                           "reason": "I cannot identify a next step."}
            else:
                content = {"decision": "supported", "diagnosis": "The student has answered Unknown.",
                           "reusable": [], "independent_approaches": []}
        return {"content": content if isinstance(content, str) else json.dumps(content), "reasoning_content": "",
                "finish_reason": "stop", "model": ep.model, "model_identity_source": "response_model", "usage": {}}

    endpoints = {"student": endpoint("solver", 8121), "coach": endpoint("teacher", 8122)}
    monkeypatch.setattr(GuidedChatEndpoint, "complete", reply)
    monkeypatch.setattr(GuidedChatEndpoint, "http", lambda ep, url: [{"n_ctx": 98304}]
                        if url.endswith("/slots") else {"data": [{"id": ep.model}]})

    def execute(folder):
        summary = run_guided_cases(source, folder, endpoints, {"version": "initial"}, 3,
                                   workers=1, protocol_version="4.10.0", until_correct=True)
        assert summary["counts"]["awaiting_guidance"] == 1
        assert (folder / "training-targets.jsonl").read_bytes() == b""
        return json.loads(next(folder.glob("cases/*/case.json")).read_bytes())

    fresh = tmp_path / "fresh"
    case = execute(fresh)
    manifest = json.loads((fresh / "manifest.json").read_bytes())
    assert manifest["draft_candidate_observation"] == "marked-student-draft/v1"
    assert "verified_step_registry_id" not in manifest
    difficulty = packets[-1]["current_difficulty"]
    claim = difficulty["latest_marked_student_candidate"]
    a, b = claim["span"]
    assert written[a:b] == claim["quote"] == "**Final Answer:** Unknown."
    assert difficulty["student_response_tail"] == written and difficulty["submitted_answer_status"] == "not_checked"
    assert all(claim["quote"] not in item["text"] for item in packets[-1]["established_evidence"])
    assert case["verification"]["raw_teacher_explanation_verdict"] == "supported"
    assert case["verification"]["explanation_review"] == "unconfirmed"

    legacy = tmp_path / "frozen"
    legacy.mkdir()
    del manifest["draft_candidate_observation"]
    (legacy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    original = execute(legacy)
    assert "latest_marked_student_candidate" not in packets[-1]["current_difficulty"]
    assert original["verification"]["explanation_review"] == "supported"
    assert json.loads((legacy / "manifest.json").read_bytes()) == manifest
