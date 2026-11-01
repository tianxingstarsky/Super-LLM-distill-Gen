"""Optional review advice is evidence-bound, pending, and uses the same call."""
from copy import deepcopy
import json

import pytest

from lib.bootstrap.review_control_text import control_text_suggestion_application
from lib.contexts.datasets.contracts import default_control_text_config
from lib.contexts.review.domain.candidate_content import candidate_digest
from lib.contexts.review.domain.control_text_suggestions import (
    build_control_text_review_context, split_control_text_review_response,
    validate_control_text_suggestions, scope_control_text_review_context,
)
from lib.domain.reasoning_candidates import assessment_from_verdict
from lib.contexts.review.infrastructure.control_text_suggestions import (
    request_with_control_suggestions,
)
from tests.test_claim_review_workflow import sample
from tests.test_condition_review_workflow import flow, evidence
from tests.test_claim_review_windows import WindowClient
from tests.test_workflow_production import model_request_data

QUOTE = "The desired verbosity is five."


def candidate():
    row = sample()
    row["control_text_config"] = default_control_text_config()
    row["control_text_node"] = "sft"
    row["messages"][1]["reasoning_content"] = QUOTE + " Inspection depends on the power being off."
    return row


def context_for(row):
    return build_control_text_review_context("sft", row, {"messages": row["messages"]})


def proposal(context):
    return {"source_id": context["sources"][0]["source_id"],
            "rule_id": "deepseek-output-verbosity-5", "quote": QUOTE,
            "reason": "The observed prefix describes the same output verbosity value in a different wording."}


def test_program_binds_hash_and_span_and_only_adds_literal():
    row = candidate()
    context = context_for(row)
    original = deepcopy(row)
    valid, invalid = validate_control_text_suggestions([proposal(context)], context)
    assert invalid == []
    item = valid[0]
    assert item["status"] == "pending"
    assert item["validation"] == "literal_evidence_verified_semantics_unverified"
    assert item["proposal"]["candidate_sha256"] == candidate_digest({"messages": row["messages"]})
    assert (item["proposal"]["start"], item["proposal"]["end"]) == (0, len(QUOTE))
    expected = deepcopy(item["base_configuration"])
    expected["rules"][0]["matches"].append(QUOTE)
    expected["rules"][0].pop("field", None)
    expected["rules"][0].pop("observed_value", None)
    assert item["proposed_configuration"] == expected
    assert row == original


@pytest.mark.parametrize("quote", ["我", "low", "123456789", "verbosity", "oververbosity 5"])
def test_generic_fragments_cannot_expand_literal_matching(quote):
    row = candidate()
    row["messages"][1]["reasoning_content"] = quote + " I will inspect the actual prerequisite."
    context = context_for(row)
    value = {**proposal(context), "quote": quote}
    valid, invalid = validate_control_text_suggestions([value], context)
    assert not valid and invalid == [{"index": 0, "code": "control_text_suggestion_too_broad"}]


@pytest.mark.parametrize("defect", ["stale_source", "outside_prefix", "user_quote", "duplicate_quote",
                                    "replacement_rule", "extra_configuration", "disabled_rule"])
def test_unsafe_advice_never_changes_original_quality(defect):
    row = candidate()
    if defect == "outside_prefix":
        row["messages"][1]["reasoning_content"] = "x" * 512 + QUOTE
    elif defect == "user_quote":
        row["messages"][0]["content"] += " " + QUOTE
    elif defect == "duplicate_quote":
        row["messages"][1]["reasoning_content"] += " " + QUOTE
    elif defect == "replacement_rule":
        row["control_text_config"]["rules"][0]["replacement"] = "Different control text"
    elif defect == "disabled_rule":
        row["control_text_config"]["rules"][0]["enabled"] = False
    context = context_for(row)
    advice = proposal(context)
    if defect == "stale_source":
        advice["source_id"] = "missing"
    elif defect == "extra_configuration":
        advice["replacement"] = "Discard reasoning"
    quality = {"keep": False, "reason": "The actual factual defect remains."}
    extracted, accepted, invalid = split_control_text_review_response(
        {"review": quality, "control_text_suggestions": [advice]}, context)
    assert extracted is quality
    assert accepted == [] and invalid


def test_existing_disabled_and_legacy_configurations_are_not_enrolled():
    row = candidate()
    for config in ({}, {**default_control_text_config(), "enabled": False},
                   {**default_control_text_config(), "review_suggestions": False},
                   {key: value for key, value in default_control_text_config().items() if key != "review_suggestions"}):
        row["control_text_config"] = config
        assert context_for(row) is None


def test_prefix_isolated_review_advice_does_not_disclose_future_reasoning():
    row = candidate()
    row["messages"].extend([{"role": "user", "content": "What next?"},
                            {"role": "assistant", "content": "Record the result.",
                             "reasoning_content": "FUTURE_PRIVATE_REASONING_918"}])
    scoped = scope_control_text_review_context(context_for(row), {"scope": {"message_index": 1}})
    assert len(scoped["sources"]) == 1
    assert "FUTURE_PRIVATE_REASONING_918" not in json.dumps(scoped)


class FakeRuntime:
    def __init__(self, output, response):
        self.state = {"id": "a" * 32}
        self.path = output / "workflows" / (self.state["id"] + ".json")
        self.response, self.calls, self.events = response, [], []

    def ask(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return deepcopy(self.response)

    def event(self, *args, **kwargs):
        self.events.append((args, kwargs))


def test_same_call_pending_queue_deduplicates_and_reauthenticates(tmp_path):
    row = candidate()
    context = context_for(row)
    grade = {"keep": False, "reason": "Task condition is still missing."}
    runtime = FakeRuntime(tmp_path, {"review": grade, "control_text_suggestions": [proposal(context)]})
    for key in (["window", 1], ["window", 2]):
        assert request_with_control_suggestions(runtime, key, "review", "review_score", {},
                   control_context=context) == grade
    assert len(runtime.calls) == 2
    app = control_text_suggestion_application(tmp_path)
    pending = app.list_pending("sft")
    assert len(pending) == 1
    item = app.get(pending[0]["id"])
    assert item["base_configuration"] == row["control_text_config"]
    assert item["proposal"]["quote"] == QUOTE
    assert "path" not in item
    app.resolve(item["id"], "applied")
    assert app.list_pending("sft") == []
    assert app.get(item["id"])["status"] == "applied"
    receipt = min((tmp_path / "workflows" / runtime.state["id"] / "control-text-suggestions").glob("*.json"),
                  key=lambda path: json.loads(path.read_text(encoding="utf-8"))["created_at"])
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["raw_response"] == runtime.response
    saved["raw_response"]["review"]["keep"] = True
    receipt.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_changed"):
        app.get(item["id"])


def test_optional_storage_problem_does_not_trigger_another_review(tmp_path):
    context = context_for(candidate())
    grade = {"keep": False}
    runtime = FakeRuntime(tmp_path, {"review": grade, "control_text_suggestions": [proposal(context)]})
    (tmp_path / ".control-text-suggestions.sqlite3").write_text("not a database", encoding="utf-8")
    assert request_with_control_suggestions(runtime, ["score"], "review", "review_score", {},
               control_context=context) == grade
    assert len(runtime.calls) == 1 and runtime.events
    with pytest.raises(ValueError, match="storage_unavailable"):
        control_text_suggestion_application(tmp_path).list_pending("sft")


def test_identical_rule_alias_is_one_pending_item_across_candidates_and_runs(tmp_path):
    first = candidate()
    context = context_for(first)
    reply = {"review": {"keep": True}, "control_text_suggestions": [proposal(context)]}
    runtime = FakeRuntime(tmp_path, reply)
    request_with_control_suggestions(runtime, ["first"], "review", "review_score", {}, control_context=context)
    app = control_text_suggestion_application(tmp_path)
    original, = app.list_pending("sft")
    app.resolve(original["id"], "dismissed")
    second = candidate()
    second["messages"][0]["content"] = "How should I inspect the second machine?"
    runtime.state["id"] = "b" * 32
    runtime.path = tmp_path / "workflows" / runtime.state["id"]
    request_with_control_suggestions(runtime, ["second"], "review", "review_score", {},
                                    control_context=context_for(second))
    assert app.list_pending("sft") == []
    assert app.get(original["id"])["run_id"] == "a" * 32
    assert len(list((tmp_path / "workflows").rglob("control-text-suggestions/*.json"))) == 1


def test_legacy_answer_judge_reuses_explicit_owner_but_never_guesses(tmp_path):
    from lib.infrastructure.training_workflow import Workflow
    from tests.test_claim_review_workflow import grade
    row = candidate()
    message = {**row["messages"][1], "control_text_config": row["control_text_config"], "control_text_node": "sft"}
    response = {"review": grade(), "control_text_suggestions": [proposal(context_for(row))]}
    runtime = FakeRuntime(tmp_path, response)
    check = Workflow.judge_answer(runtime, ["legacy"], {"prompt": row["messages"][:1]}, message)
    assert check["keep"] and len(runtime.calls) == 1
    pending, = control_text_suggestion_application(tmp_path).list_pending("sft")
    assert pending["node"] == "sft"
    assert pending["source"]["path"] == ["reasoning_content"]
    assert pending["source"]["message_index"] is None
    assert "control_text_config" not in runtime.calls[0][0][3]["answer"]
    runtime.response = grade()
    Workflow.judge_answer(runtime, ["unowned"], {}, {"content": "Plain answer", "reasoning": QUOTE})
    assert "Control-text rule suggestions" not in runtime.calls[-1][1].get("instruction", "")


class SuggestionClient(WindowClient):
    def chat(self, wire, **kwargs):
        response = json.loads(super().chat(wire, **kwargs))
        request = model_request_data(wire)
        instructions = request.get("node_task_instructions", "")
        if "[Control-text rule suggestions, protocol 1]" not in instructions:
            return json.dumps(response)
        context = json.loads(instructions.splitlines()[-1])
        return json.dumps({"review": response, "control_text_suggestions": [proposal(context)]})


def test_production_claim_windows_preserve_quality_contract_without_extra_calls(tmp_path):
    row = candidate()
    reference = deepcopy(row)
    reference["control_text_config"].pop("review_suggestions")
    old_client, new_client = WindowClient(), SuggestionClient()
    old, new = flow(tmp_path / "old", old_client), flow(tmp_path / "new", new_client)
    old_grade = old._review_claim_score("sft", reference, evidence(), ["candidate"])
    new_grade = new._review_claim_score("sft", row, evidence(), ["candidate"])
    assert new_grade == old_grade
    assert len(new_client.calls) == len(old_client.calls)
    pending = control_text_suggestion_application(tmp_path / "new" / "output").list_pending("sft")
    assert len(pending) == 1
    assert row["messages"] == reference["messages"]
    assert assessment_from_verdict(new._review_payload("sft", row), new_grade,
        evidence_ref="offline-control-advice", method="llm_claim_review", claim_review=row["claim_review"])["accepted"]
    count = len(new_client.calls)
    assert new._review_claim_score("sft", row, evidence(), ["candidate"]) == new_grade
    assert len(new_client.calls) == count
