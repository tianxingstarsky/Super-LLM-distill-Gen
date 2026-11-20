import hashlib
import json

import pytest

from lib.bootstrap.guided_answers import generate_guided_cases
from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint


@pytest.mark.parametrize("legacy", [False, True])
def test_recover_exact_unsubmitted_coach_message_after_replaying_student(tmp_path, monkeypatch, legacy):
    old = "http://127.0.0.1:17001"
    large = "http://127.0.0.1:17002"
    calls = []
    identities = {old: "original-weights", large: "original-weights"}

    def identify(endpoint, url, model):
        root = url.removesuffix("/v1")
        return {"weights_sha256": identities[root], "model": model}, 0, 98304 if root == old else 131072

    monkeypatch.setattr(
        "lib.contexts.workflows.infrastructure.guided_context_recovery.native_instance", identify
    )

    def transport(endpoint, url, data=None):
        if url.endswith("/slots"):
            return [{"id": 0, "n_ctx": 98304 if url.startswith(old) else 131072}]
        if url.endswith("/v1/models"):
            return {"data": [{"id": "solver"}]}
        if url.endswith("/apply-template"):
            return {"prompt": json.dumps(data["messages"], ensure_ascii=False)}
        if url.endswith("/tokenize"):
            return {"tokens": [1] * (70000 if "program_check" in data["content"] else 5)}
        assert url.endswith("/v1/chat/completions")
        calls.append(data)
        answer = (
            json.dumps({"decision": "supported", "reusable": [], "diagnosis": "The sum is correct."})
            if "response_format" in data
            else "Adding two and three gives five.\nFinal answer: 5"
        )
        return {
            "model": "solver",
            "choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
            "usage": {},
        }

    monkeypatch.setattr(GuidedChatEndpoint, "http", transport)
    path = tmp_path / "input.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "q",
                "question": "Add two and three and show the calculation.",
                "gold": {"kind": "number", "value": "5"},
                "source_reasoning": "2 + 3 = 5.",
            }
        ),
        encoding="utf-8",
    )
    args = dict(student_url=old + "/v1", student_model="solver", protocol_version="3.6.0")
    parent, child = tmp_path / "parent", tmp_path / "child"
    stopped = generate_guided_cases(path, parent, **args)
    assert stopped["counts"]["interrupted"] == 1 and len(calls) == 1
    folder = parent / "cases" / hashlib.sha256(b"q").hexdigest()
    failed = folder / "001-coach.json"
    original = json.loads(failed.read_bytes())
    assert original["attempts"][-1]["model_request_sent"] is False
    if legacy:
        stopfile = folder / "interruption.json"
        stopfile.write_text(json.dumps({**json.loads(stopfile.read_bytes()), "error": "ValueError"}))
        original["attempts"] = [
            {"status": "interrupted", "error": "ValueError", "http_status": None, "dispatch": "unknown"}
        ]
        failed.write_text(json.dumps(original))
    runtime = {**args, "student_url": large + "/v1", "case_ids": ["q"], "recover_context_from": parent}
    identities[large] = "different-weights"
    with pytest.raises(ValueError, match="weights"):
        generate_guided_cases(path, child, **runtime)
    identities[large] = identities[old]
    failed.write_text(
        json.dumps(
            {
                **original,
                "attempts": [{"status": "interrupted", "error": "TimeoutError", "dispatch": "unknown"}],
            }
        )
    )
    with pytest.raises(ValueError, match="not proven unsent"):
        generate_guided_cases(path, child, **runtime)
    failed.write_text(json.dumps(original))
    summary = generate_guided_cases(path, child, **runtime)
    assert summary["counts"]["accepted"] == 1 and summary["model_calls"]["replayed_matching"] == 1
    assert len(calls) == 2 and calls[-1] == original["request"]
    prior = json.loads((folder / "001-student.json").read_bytes())
    reused = json.loads((child / "cases" / folder.name / "001-student.json").read_bytes())
    assert reused["response"] == prior["response"]
    assert (folder / "interruption.json").is_file() and not (folder / "case.json").exists()
