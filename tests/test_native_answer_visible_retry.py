"""Completed empty native answers do not invent data or stop other rows."""
from copy import deepcopy
import json
from types import SimpleNamespace as Obj

import pytest

from lib.infrastructure import training_workflow as engine
from lib.model_request_reliability import ModelRequestError
from test_workflow_native_prefill import setup_workflow, PREFIX, ANSWER


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


EMPTY_REASONING = "EMPTY_PROVIDER_REASONING_ONLY_DO_NOT_USE_AS_ANSWER"


def controlled_sdk(client, sent, monkeypatch, outcomes):
    original = client.client.chat.completions.create
    queue = iter(outcomes)

    def create(**kwargs):
        if kwargs["messages"][-1].get("prefix"):
            outcome = next(queue, "success")
            if outcome != "success":
                sent.append(deepcopy(kwargs))
                if isinstance(outcome, BaseException):
                    raise outcome
                assert outcome == "empty"
                return Obj(choices=[Obj(message=Obj(content="", reasoning_content=EMPTY_REASONING),
                                         finish_reason="stop")],
                           usage=Obj(prompt_tokens=17, completion_tokens=23))
        return original(**kwargs)

    client.client.chat.completions.create = create
    monkeypatch.setattr(client.client, "with_options", lambda **kwargs: Obj(chat=Obj(completions=Obj(create=create))))


def prefix_review_calls(reviewer):
    return [request for request in reviewer.calls if
            "独立核验待作为原生API推理前缀" in request[0]["content"]
            or "把作者推理作为原生通道前缀" in request[0]["content"]]


def test_completed_empty_then_real_answer_uses_identical_roles_prefix_and_final_only_metadata(tmp_path, monkeypatch):
    workflow, sample, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    workflow.recipe["native_answer_retry_version"] = 1
    controlled_sdk(client, sent, monkeypatch, ["empty", "success"])
    try:
        row = workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))[0]
        assert row["status"] == "eligible" and row["answer"] == ANSWER
        assert row["reasoning"] == [PREFIX]
        assert len(sent) == 3 and sent[1] == sent[2]
        assert len(prefix_review_calls(reviewer)) == 1
        assert sum(not request["messages"][-1].get("prefix") for request in sent) == 1
        assert row["native_answer_retry"]["answer_attempts"] == 2
        empty, = row["native_answer_retry"]["empty_response_receipts"]
        assert empty["code"] == "model_visible_output_missing" and empty["eligible"] is False
        assert row["answer_response_details"]["reasoning_content"] == "provider-only continuation"
        assert EMPTY_REASONING not in json.dumps(row, ensure_ascii=False)
        assert client.usage == {"calls": 3, "prompt_tokens": 41, "completion_tokens": 39}
        assert workflow.state["usage"]["generation"] == client.usage
        empty_events = [event for event in workflow.state["events"] if event["kind"] == "native_answer_empty"]
        assert len(empty_events) == 1 and empty_events[0]["code"] == "model_visible_output_missing"
        assert EMPTY_REASONING not in json.dumps(empty_events)
        interrupted = [json.loads(path.read_text("utf-8")) for path in (workflow.path / "streams").glob("*.interrupted.json")]
        assert any(EMPTY_REASONING in item.get("reasoning", "") and not item.get("text") for item in interrupted)
    finally:
        client.client.close()


def test_two_completed_empty_answers_isolate_one_row_and_process_next_without_new_prefix(tmp_path, monkeypatch):
    workflow, sample, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    workflow.recipe.update(native_answer_retry_version=1, concurrency=1)
    controlled_sdk(client, sent, monkeypatch, ["empty", "empty", "success"])
    second = {**deepcopy(sample), "id": "next-independent-row"}
    try:
        rows = list(workflow.stage_items("cot", [sample, second],
            lambda candidate: workflow.styled_cot(candidate, workflow.generation_style("cot", candidate["id"]))))
        failed, succeeded = rows
        assert failed["status"] == "quarantined" and failed["reason"] == "native_answer_missing_after_retry"
        assert failed["question"] == sample["messages"][:-1]
        assert failed["source_context"] == sample["source_context"]
        assert failed["input_digest"] == engine.digest({"question": failed["question"], "source_context": failed["source_context"]})
        assert "answer" not in failed and "reasoning" not in failed and "reasoning_prefill" not in failed
        terminal = failed["native_answer_terminal"]
        assert terminal["answer_attempts"] == 2 and len(terminal["empty_response_receipts"]) == 2
        for key in ("rationale_checkpoint", "prefix_review_checkpoint"):
            assert (workflow.path / "checkpoints" / "cot" / (terminal[key]["sha256"] + ".json")).exists()
        assert succeeded["status"] == "eligible" and succeeded["answer"] == ANSWER
        assert workflow.state["stages"]["cot"]["done"] == 2
        assert workflow.state["stages"]["cot"]["eligible"] == 1
        assert workflow.state["stages"]["cot"]["quarantined"] == 1
        assert len(sent) == 5 and sum(not request["messages"][-1].get("prefix") for request in sent) == 2
        assert len(prefix_review_calls(reviewer)) == 2  # One per row, including the isolated one.
    finally:
        client.client.close()


def test_recipe_without_retry_marker_still_raises_on_first_empty_answer(tmp_path, monkeypatch):
    workflow, sample, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    workflow.recipe.pop("native_answer_retry_version", None)
    controlled_sdk(client, sent, monkeypatch, ["empty", "success"])
    try:
        with pytest.raises(ValueError, match="^model_visible_output_missing$"):
            workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))
        assert len(sent) == 2 and len(prefix_review_calls(reviewer)) == 1
        assert not any(event["kind"] == "native_answer_empty" for event in workflow.state["events"])
    finally:
        client.client.close()


@pytest.mark.parametrize("error", [ValueError("model_reasoning_prefill_rejected"),
    ValueError("model_reasoning_prefill_tools_unsupported"),
    ModelRequestError("service_authentication_failed", "fatal", 1),
    ModelRequestError("service_quota_exhausted", "fatal", 1),
    ModelRequestError("service_timeout", "transient", 3)])
def test_nonempty_output_codes_never_enter_sample_retry_or_isolation_path(tmp_path, monkeypatch, error):
    workflow, sample, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    workflow.recipe["native_answer_retry_version"] = 1
    transient = isinstance(error, ModelRequestError) and error.kind == "transient"
    controlled_sdk(client, sent, monkeypatch, [error] * (3 if transient else 1))
    try:
        with pytest.raises((ValueError, ModelRequestError)):
            workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))
        assert len(sent) == (4 if transient else 2)
        assert not any(event["kind"] == "native_answer_empty" for event in workflow.state["events"])
    finally:
        client.client.close()


def test_recorded_empty_is_not_purchased_again_when_following_upstream_failure_is_resumed(tmp_path, monkeypatch):
    workflow, sample, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    workflow.recipe["native_answer_retry_version"] = 1
    controlled_sdk(client, sent, monkeypatch, ["empty", TimeoutError("UPSTREAM_SECRET_TEXT"), "success"])
    try:
        with pytest.raises(ModelRequestError, match="service_timeout"):
            workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))
        # The first request retains network retries, but this supplemental
        # request gets one transport attempt, not another 3-attempt loop.
        assert len(sent) == 3
        row = workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))[0]
        assert row["status"] == "eligible" and row["answer"] == ANSWER
        assert len(sent) == 4 and sent[2] == sent[3]
        assert len(prefix_review_calls(reviewer)) == 1
        assert sum(not request["messages"][-1].get("prefix") for request in sent) == 1
        assert len(row["native_answer_retry"]["empty_response_receipts"]) == 1
        assert len([event for event in workflow.state["events"] if event["kind"] == "native_answer_empty"]) == 1
        assert "UPSTREAM_SECRET_TEXT" not in json.dumps(workflow.state["events"])
    finally:
        client.client.close()
