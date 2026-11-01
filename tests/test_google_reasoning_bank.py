"""Exercise the actual pinned Google entrypoints; fixtures assert protocol only."""
from copy import deepcopy
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
