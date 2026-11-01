"""Policy-based export never turns a failed opinion into a passing verdict."""
from copy import deepcopy
from types import SimpleNamespace

from lib.contexts.review.contracts import advisory_release_valid
from lib.contexts.review.domain.candidate_content import candidate_digest
from lib.infrastructure.workflow_qa_director import WorkflowQADirector


def test_failed_advisory_is_durable_and_does_not_purchase_a_second_attempt():
    from lib.contexts.models.contracts import ModelRequestError
    from lib.contexts.review.infrastructure.advisory_review import review_once
    saved, calls = {}, []

    def ask(*args, **kwargs):
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
    assert calls[0]["structured_response_attempts"] == calls[0]["native_request_attempts"] == 1


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
