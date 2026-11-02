"""Exercise the actual pinned Google entrypoints; fixtures assert protocol only."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application
from lib.bootstrap.reasoning_bank import reasoning_bank_application


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class Embedding:
    identity = "protocol-fixture-not-a-semantic-model"
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]
    def embed_query(self, query, instruction):
        return [1.0, 0.0]


def test_reviewer_provides_feedback_and_is_never_an_evolution_target(tmp_path, monkeypatch):
    app = google_reasoning_bank_application(root=tmp_path, user_key="fixture")
    with pytest.raises(ValueError, match="generation_scope_required"):
        app.learn("review", {})
    with pytest.raises(ValueError, match="generation_scope_required"):
        app.activate("review", "a" * 32)
    # Older saved reviewer selections cannot enter a new task's system context.
    monkeypatch.setattr(app, "active", lambda: {"scopes": {"review": "a" * 32}})
    assert app.freeze(None, "A generation task") is None


def test_original_google_main_retrieval_resume_and_disable(tmp_path):
    calls = []
    class Client:
        usage = {"calls": 1, "prompt_tokens": 10, "completion_tokens": 8}
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            calls.append(deepcopy(messages))
            return "Use the actual label-space criterion; do not infer it from output modality."
    app = google_reasoning_bank_application({"backend": "fixture", "model": "fixture"}, root=tmp_path,
        user_key="fixture", client_factory=lambda binding: (Client(), "fixture"))
    observation = {"id": "actual-protocol-case", "conversation": {"messages": [
        {"role": "user", "content": "分类的条件是什么？"},
        {"role": "assistant", "reasoning_content": "I need to distinguish a label space from its modality.",
         "content": "The task uses a small finite label space."}]},
        "review_observation": {"verdict": {"keep": True, "reason": "Protocol fixture, not quality proof."}}}
    edition = app.learn("reasoning", observation)
    assert len(calls) == 1
    assert edition["upstream_result"]["upstream"][0]["functions"].keys() == {"main", "format_trajectory"}
    assert edition["model_request_receipt"][0]["response_details"]["usage"]["calls"] == 1
    assert app.learn("reasoning", observation)["id"] == edition["id"]
    assert len(calls) == 1
    app.bank = reasoning_bank_application(root=tmp_path, user_key="fixture", embedder=Embedding())
    assert app.activate("reasoning", edition["id"])["status"] == "active"
    assert [row["id"] for row in app.experience.list_versions("reasoning")] == [edition["id"]]
    selection = app.freeze(None, "Which label-space criterion applies?")
    scope = selection["selection_receipt"]["scopes"]["reasoning"]
    assert scope["applied"]
    assert "select_memory" in scope["upstream_execution"]["invoked"]
    assert edition["text"] in selection["snapshot"]["scopes"]["reasoning"]["text"]
    app.activate("reasoning", None)
    assert app.freeze(None, "Which label-space criterion applies?") is None


def test_unknown_outcome_is_not_fabricated_for_google(tmp_path):
    app = google_reasoning_bank_application(root=tmp_path, user_key="fixture")
    with pytest.raises(ValueError, match="no_observed_outcome"):
        app.learn("reasoning", {"id": "unknown", "conversation": {"messages": [
            {"role": "user", "content": "help"}, {"role": "assistant", "content": "What happened?"}]}})
    from lib.contexts.workflows.infrastructure.google_reasoning_bank import recorded_episode
    imported = recorded_episode({"id": "dsh-no-thought", "messages": [
        {"role": "user", "content": "help"}, {"role": "assistant", "content": "What happened?"}]}, "success")
    assert imported["think_list"] == [""]
    assert imported["assessment_source"]["kind"] == "user_label"


def test_original_parallel_main_receives_two_actual_alternatives(tmp_path):
    from lib.contexts.workflows.infrastructure.reasoning_bank_induction import OfficialReasoningBankInductor
    seen = []
    class Client:
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            seen.append(messages)
            return "Check the label set rather than assuming a data type."
    inductor = OfficialReasoningBankInductor({"backend": "fixture", "model": "fixture"},
        lambda binding: (Client(), "fixture"), tmp_path)
    common = {"query": "What is classification?", "think_list": ["I need the criterion."],
              "assessment_source": "fixture_not_quality_proof", "feedback": "Protocol only."}
    result = inductor.contrast([{**common, "id": "first", "status": "fail", "action_list": ["wrong alternative"]},
                                {**common, "id": "second", "status": "success", "action_list": ["revised alternative"]}])
    assert len(seen) == 1
    assert "wrong alternative" in seen[0][1]["content"] and "revised alternative" in seen[0][1]["content"]
    assert "Recorded outcome: fail" in seen[0][1]["content"]
    assert "Recorded outcome: success" in seen[0][1]["content"]
    assert result["upstream"][0]["file"] == "WebArena/induce_scaling.py"


def test_generalization_policy_isolated_from_legacy_and_keeps_complete_parent_memory(tmp_path):
    from lib.contexts.workflows.domain.reasoning_bank import entries_from_edition
    seen = []
    memory = ("# Memory Item 1\n\n## Title Check the decision criterion\n\n"
              "## Description When a familiar label suggests a different decision.\n\n"
              "## Content Determine whether the evidence actually changes the governing criterion.")
    class Client:
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            seen.append(deepcopy(messages))
            return memory
    options = dict(root=tmp_path, user_key="fixture", client_factory=lambda binding: (Client(), "fixture"))
    binding = {"backend": "fixture", "model": "fixture"}
    observation = {"id": "comparison", "conversation": {"messages": [
        {"role": "user", "content": "How should this decision be made?"},
        {"role": "assistant", "content": "Use the relevant evidence."}]},
        "review_observation": {"verdict": {"keep": True, "reason": "A fallible observation, not causal proof."}}}
    legacy = google_reasoning_bank_application(binding, memory_policy_version=None, **options)
    old = legacy.learn("reasoning", observation)
    current = google_reasoning_bank_application(binding, **options)
    edition = current.learn("reasoning", observation)
    assert len(seen) == 2 and old["id"] != edition["id"]
    assert "Host memory context" not in seen[0][1]["content"]
    assert "不面向本题答案" in seen[1][0]["content"]
    assert edition["upstream_result"]["memory_policy"]["version"] == "1.0.0"
    entries = entries_from_edition(edition)
    assert len(entries) == 1
    assert entries[0]["principle"] == memory
    assert len(old["upstream_memory_items"]) > 1  # Historical projection is not rewritten.
    updated = current.learn("reasoning", {**observation, "id": "next"}, parent_id=edition["id"])
    context = json.loads(seen[2][1]["content"].split("Host memory context (reference data, not task instructions):\n")[1])
    assert context == {"generation_scope": "reasoning", "current_memory": edition["text"]}
    assert updated["base_text"] == edition["text"]
    assert current.learn("reasoning", {**observation, "id": "next"}, parent_id=edition["id"])["id"] == updated["id"]
    assert len(seen) == 3


def test_no_new_memory_receipt_is_reused_without_creating_or_activating_rules(tmp_path):
    calls = []
    class Client:
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            calls.append(messages)
            return "<no-new-memory/>"
    app = google_reasoning_bank_application({"backend": "fixture", "model": "fixture"}, root=tmp_path,
        user_key="fixture", client_factory=lambda binding: (Client(), "fixture"))
    observation = {"id": "no-new-principle", "conversation": {"messages": [
        {"role": "user", "content": "Continue."}, {"role": "assistant", "content": "Done."}]},
        "review_observation": {"verdict": {"keep": True, "reason": "No new transferable evidence."}}}
    result = app.learn("reasoning", observation)
    assert result["status"] == "no_update" and "id" not in result
    assert app.learn("reasoning", observation) == result
    assert len(calls) == 1 and app.repository.list_versions("reasoning") == []
    assert app.active()["scopes"] == {}
    receipt = app.repository.root / "official-editions" / (result["source_sha256"] + ".json")
    saved = json.loads(receipt.read_text("utf8"))
    saved["scope"] = "answer"
    receipt.write_text(json.dumps(saved), encoding="utf8")
    with pytest.raises(ValueError, match="learning_receipt_changed"):
        app.learn("reasoning", observation)
    assert len(calls) == 1
