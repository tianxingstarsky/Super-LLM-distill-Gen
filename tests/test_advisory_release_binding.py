"""Policy-based export never turns a failed opinion into a passing verdict."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from lib.contexts.review.contracts import advisory_release_valid
from lib.contexts.review.domain.candidate_content import candidate_digest
from lib.infrastructure.workflow_qa_director import WorkflowQADirector


@pytest.mark.parametrize("template_kind", ["current", "historical", "custom"])
def test_advisory_template_is_frozen_and_review_without_new_lesson_is_recorded_once(tmp_path, template_kind):
    from lib.contexts.review.infrastructure.advisory_review import review_once
    from lib.infrastructure import training_workflow as engine
    from lib.io_utils import atomic_json
    from lib.prompts import get, render

    historical = render(get("workflow.review_score", version="1.27.0"))
    assert hashlib.sha256(historical.encode()).hexdigest() == (
        "d5a19d5b8ae5d2e4b6153e857ef20d9ed5f980432d95189d2372810737937d86")
    body = (render(get("workflow.review_score", version="1.28.0")) if template_kind == "current" else
            historical if template_kind == "historical" else "Saved custom quality review {literal}.")
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="解释可检验的概念判断标准", targets=["sft"],
        review_repair={"mode": "auto", "max_rounds": 0},
        package_review={"enabled": False, "node": "jev"},
        node_prompts={"review": {"workflow.review_score": body}} if template_kind == "custom" else None)
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["review_advisory_policy_version"] == 1
    if template_kind == "historical":
        recipe["node_prompt_templates"]["review"]["workflow.review_score"] = historical
        recipe["prompts"]["workflow.review_score"] = {
            "version": "1.27.0", "sha256": engine.digest(get("workflow.review_score", version="1.27.0").template)}
        atomic_json(path / "recipe.json", recipe)
        state = engine.read_json(path / "state.json")
        state["recipe_hash"] = engine.digest(recipe)
        atomic_json(path / "state.json", state)
    assert recipe["node_prompt_templates"]["review"]["workflow.review_score"] == body

    class Reviewer:
        model = "offline-reviewer"
        calls = []
        usage = {"calls": 0}

        def chat(self, messages, **kwargs):
            self.calls.append(deepcopy(messages))
            self.usage["calls"] += 1
            return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True,
                "correctness": 5, "scores": {key: 5 for key in (
                    "correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "回答使用了任务的实际判别标准，没有发现实质问题；没有新的可迁移经验。"})

    reviewer = Reviewer()
    row = {"id": "concept-criterion", "messages": [
        {"role": "user", "content": "怎样判断一个问题属于哪类任务？"},
        {"role": "assistant", "content": "应依据任务目标和输出空间的定义判断。",
         "reasoning_content": "先确认任务的实际判别标准，不能用表面形式替代定义。"}]}
    runtime = engine.Workflow(output, run_id, tmp_path, generator=reviewer)
    runtime.stage = "review"
    first = review_once(runtime, "sft", row, None, "")
    assert first["status"] == "observed" and first["verdict"]["keep"] is True
    assert first["verdict"]["reason"].endswith("没有新的可迁移经验。")
    assert len(reviewer.calls) == 1
    assert body in reviewer.calls[0][0]["content"]
    resumed = engine.Workflow(output, run_id, tmp_path, generator=reviewer)
    resumed.stage = "review"
    assert review_once(resumed, "sft", row, None, "") == first
    assert len(reviewer.calls) == 1
    assert engine.read_json(path / "recipe.json") == recipe


def test_failed_advisory_is_durable_and_does_not_purchase_a_second_attempt():
    from lib.contexts.models.contracts import ModelRequestError
    from lib.contexts.review.infrastructure.advisory_review import review_once
    saved, calls = {}, []

    def ask(*args, **kwargs):
        from lib.contexts.workflows.domain.model_request import NodeModelRequest
        NodeModelRequest(*args, **kwargs).validate()
        calls.append(kwargs)
        raise ModelRequestError("upstream_unavailable", "upstream", 1)

    def checkpoint(key, action):
        identity = repr(key)
        if identity not in saved:
            saved[identity] = action()
        return deepcopy(saved[identity])

    runtime = SimpleNamespace(recipe={"package_review": {}}, stage="review", ask=ask,
        _review_payload=lambda target, row: row, check_cancel=lambda: None, checkpoint=checkpoint)
    row = {"messages": [{"role": "user", "content": "Explain it."},
                        {"role": "assistant", "content": "Here is the explanation."}]}
    first = review_once(runtime, "sft", row, None, "")
    assert first["status"] == "unavailable"
    assert review_once(runtime, "sft", row, None, "") == first
    assert len(calls) == 1
    assert calls[0]["structured_response_attempts"] == 1
    assert "native_request_attempts" not in calls[0]


def test_advisory_release_binds_candidate_configuration_and_unchanged_failed_observation():
    config = {"mode": "auto", "max_rounds": 0, "score_threshold": .8}
    payload = {"messages": [{"role": "user", "content": "Can I inspect it?"},
                            {"role": "assistant", "content": "First turn off power.",
                             "reasoning_content": "I must consider the prerequisite."}]}
    observation = {"status": "reviewed", "verdict": {"keep": False, "reason": "Scope needs confirmation."}}
    row = {**deepcopy(payload), "status": "eligible", "review_observation": observation,
        "review_repair": {"status": "released_advisory", "policy_version": 1,
            "candidate_sha256": candidate_digest(payload), "config_sha256": candidate_digest(config),
            "observation_sha256": candidate_digest(observation)}}
    runtime = SimpleNamespace(recipe={"review_advisory_policy_version": 1, "review_repair": config},
                              _review_payload=lambda target, candidate: {"messages": candidate["messages"]})
    assert advisory_release_valid(row, payload, config)
    assert WorkflowQADirector.human_package_issue(runtime, row, "sft") is None
    for field in ("candidate", "observation", "config"):
        changed, changed_config = deepcopy(row), deepcopy(config)
        if field == "candidate":
            changed["messages"][-1]["content"] = "Inspect with power on."
        elif field == "observation":
            changed["review_observation"]["verdict"]["keep"] = True
        else:
            changed_config["max_rounds"] = 1
        actual = {"messages": changed["messages"]}
        assert not advisory_release_valid(changed, actual, changed_config)
        runtime.recipe["review_repair"] = changed_config
        assert WorkflowQADirector.human_package_issue(runtime, changed, "sft") == "review_advisory_receipt_missing_or_changed"
    runtime.recipe = {"review_repair": config}
    assert WorkflowQADirector.human_package_issue(runtime, row, "sft") == "review_repair_assessment_missing"
    assert row["review_observation"]["verdict"]["keep"] is False


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("passing", [False, True])
def test_new_multiturn_local_rejection_survives_advisory_and_incomplete_drafts_spend_nothing(complete, passing):
    from lib.contexts.review.application.review_candidate import ReviewCandidate
    from test_pipeline_use_cases import grade

    turns = [{"role": "user", "content": "Can I inspect the circuit?"},
             {"role": "assistant", "content": "Isolate power first.", "reasoning_content": "Isolation precedes inspection."},
             {"role": "user", "content": "What follows the inspection?"},
             {"role": "assistant", "content": "Record the actual result.", "reasoning_content": "Use observed results only."}]
    row = {"id": "local-failure", "status": "quarantined", "reason": "native_generation_instruction_leakage",
        "messages": turns if complete else turns[:2],
        "failed_turn": 2 if complete else 1, "native_generation": {},
        "multiturn_advisory_review": {"version": 1, "status": "blocked", "model_called": False,
            "review_stage": "review", "local_validation": "rejected"}}
    original, calls = deepcopy(row), []
    observation = {"status": "observed", "verdict": grade(passing)}
    port = SimpleNamespace(payload=lambda target, record: {"messages": record["messages"]},
        evidence=lambda record: {"text": "Actual original source."},
        review_once=lambda *args: calls.append(args) or deepcopy(observation),
        record_advisory_observation=lambda target, record: None)
    recipe = {"review_advisory_policy_version": 1, "multiturn_advisory_review_version": 1,
        "native_dialogue_continuation_version": 1,
        "review_repair": {"mode": "auto", "max_rounds": 0, "score_threshold": .8}}
    reviewed, = ReviewCandidate(port, recipe).execute({"target": "multiturn", "record": row})
    assert reviewed["status"] == "quarantined" and reviewed["reason"] == original["reason"]
    assert reviewed["messages"] == original["messages"] and row == original
    assert reviewed["review_repair"]["status"] == "rejected"
    assert len(calls) == int(complete)
    if complete:
        assert reviewed["review_observation"] == observation
    else:
        assert reviewed["review_observation"]["status"] == "not_called"
        assert reviewed["review_observation"]["model_called"] is False
    resumed, = ReviewCandidate(port, recipe).execute({"target": "multiturn", "record": reviewed})
    assert resumed["review_observation"] == reviewed["review_observation"] and len(calls) == int(complete)


def test_legacy_multiturn_local_failure_keeps_its_frozen_advisory_policy():
    from lib.contexts.review.application.review_candidate import ReviewCandidate
    from test_pipeline_use_cases import grade

    row = {"id": "old-local-failure", "status": "quarantined", "reason": "existing_quality_issue",
        "messages": [{"role": "user", "content": "Can I inspect it?"},
                     {"role": "assistant", "content": "Isolate power first."}]}
    calls = []
    port = SimpleNamespace(payload=lambda target, record: {"messages": record["messages"]},
        evidence=lambda record: None,
        review_once=lambda *args: calls.append(args) or {"status": "observed", "verdict": grade(False)},
        record_advisory_observation=lambda target, record: None)
    reviewed, = ReviewCandidate(port, {"review_advisory_policy_version": 1,
        "review_repair": {"mode": "auto", "max_rounds": 0}}).execute({"target": "multiturn", "record": row})
    assert reviewed["status"] == "eligible" and "reason" not in reviewed and len(calls) == 1
