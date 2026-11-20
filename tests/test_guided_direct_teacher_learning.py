import hashlib
import json
import sys

import pytest

from lib import guided_experience as cli
from lib.contexts.workflows.domain.experience_evolution import digest
from lib.contexts.workflows.infrastructure import guided_answer_records as records


def test_direct_learning_uses_the_bound_registry_and_replays_the_selected_teacher_checkpoints(tmp_path, monkeypatch):
    def save(path, value):
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(json.dumps(value), encoding="utf8")
        return path

    ident = "changed-position"
    source_run, run = tmp_path / "parent", tmp_path / "child"
    registry = {"schema": "guided-teaching-verification/v1", "receipts": ["c" * 64]}
    parent = {"template_version": "4.10.12", "verified_step_registry_id": digest(registry)}
    source_manifest = save(source_run / "manifest.json", parent)
    manifest = {"template_version": "4.10.13", "verified_step_registry_id": digest(registry),
                "reuse_matching": {"source_binding": digest(parent),
                                   "source_manifest_sha256": hashlib.sha256(source_manifest.read_bytes()).hexdigest()}}
    save(run / "manifest.json", manifest)
    save(run / "verified-step-receipts.json", registry)
    folder = records.location(run, ident)
    role = "disclosure_draft"
    request = {"messages": [{"role": "user", "content": "Draft the operation."}]}
    response = {"content": "The author's original wording.", "finish_reason": "stop"}
    record = {"status": "complete", "role": role, "attempt": 1, "attempts": [{"status": "complete"}],
              "identity": digest({"binding": digest(parent), "id": ident, "payload": request}), "request": request, "response": response}
    origin_path = save(records.location(source_run, ident) / "001-disclosure_draft.json", record)
    _, origin = records.replay_matching_call(source_run, ident, role, 1, request, digest(parent))
    save(folder / "001-disclosure_draft.json", {**record, "attempts": [{"status": "replayed", "new_model_calls": 0}],
         "origin": origin, "identity": digest({"binding": digest(manifest), "id": ident, "payload": request})})
    review = {"content": "Independent decision.", "finish_reason": "stop"}
    review_request = {"messages": [{"role": "user", "content": "The wording submitted for review."}]}
    save(folder / "001-disclosure.json", {**record, "role": "disclosure", "request": review_request, "response": review,
         "identity": digest({"binding": digest(manifest), "id": ident, "payload": review_request})})
    work = {"revision": 0, "draft_response": response, "review_response": review, "review_checkpoint": {"role": "disclosure", "index": 1}}
    case = {"id": ident, "binding": digest(manifest), "protocol": "guided-draft-snapshot/v4.10.13",
            "attempts": [{"attempt": 1, "disclosure_work": [work]}]}
    path = save(folder / "case.json", case)
    received, inductions = [], []

    def verify(selected, attempt, entry, directory, trusted):
        assert (selected, attempt, entry, directory, trusted) == (case, case["attempts"][0], work, folder, {"c" * 64})
        return review_request["messages"][0]["content"]

    def learn(bank, selected, number, query, parent, **kwargs):
        received.append(kwargs["reviewed_public_text"])
        return {"version": "fixture", "receipt": {"learning_status": "fixture"}}

    monkeypatch.setattr(records, "verify_teacher_rendering", verify, raising=False)
    monkeypatch.setattr(cli, "google_reasoning_bank_application", lambda *a, **kw: inductions.append(True))
    monkeypatch.setattr(cli, "learn_guided_experience", learn)
    argv = ["guided_experience", "--role", "disclosure_teacher", "--case", str(path), "--attempt", "1", "--query", "guidance",
            "--library", str(tmp_path / "library"), "--model", "offline", "--backend", "fixture", "--output", str(tmp_path / "output.json")]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(ValueError, match="original run"):
        cli.main()
    assert not inductions
    monkeypatch.setattr(sys, "argv", [*argv, "--source-run", str(source_run)])
    cli.main()
    assert received == [review_request["messages"][0]["content"]]
    local = {"revision": 0, "draft_response": response, "issue": "teacher_disclosure_arithmetic_contradiction"}
    local_case = {**case, "attempts": [{"attempt": 1, "disclosure_work": [local]}]}
    with monkeypatch.context() as patch:
        patch.setattr(records, "verify_teacher_rendering", lambda *args: "The locally checked public wording.")
        assert cli.reviewed_disclosure_for_learning(path, local_case, 1, 0, [source_run]) == "The locally checked public wording."
        local["assessment"] = {"decision": "release"}
        with pytest.raises(ValueError, match="missing its independent review"):
            cli.reviewed_disclosure_for_learning(path, local_case, 1, 0, [source_run])
    save(origin_path, {**record, "response": {"content": "Changed after authentication."}})
    with pytest.raises(ValueError, match="has changed"):
        cli.main()
    save(path, {**case, "protocol": "guided-draft-snapshot/v4.10.12"})
    with pytest.raises(ValueError, match="bound guided-run manifest"):
        cli.main()
    assert len(inductions) == 1
