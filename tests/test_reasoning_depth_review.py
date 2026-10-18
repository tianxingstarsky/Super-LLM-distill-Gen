"""Depth is a trusted node requirement, not token count or truth certification."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.domain.claim_review import claim_review_requests
from lib.domain.reasoning_depth import (
    apply_reasoning_depth_control, reasoning_depth_control_message,
    reasoning_depth_instruction, resolve_reasoning_depth,
)
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_review_repair import (
    _current_review_generation_style, current_claim_review_requirements,
)
from lib.prompts import get, render
from lib.prompts.workflow_adversarial_review import (
    CONDITION_ADJUDICATION_BODY, CONDITION_DEPTH_ADJUDICATION_BODY,
)
from tests.test_blind_claim_workflow import OfflineBlindClient
from tests.test_claim_review_workflow import Probe, SOURCE, grade, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


ROOT = Path(__file__).resolve().parents[1]


def pure_worker(depth="deep", *, version=1):
    worker = Probe()
    worker.recipe.update(authored_reasoning_warmup_version=1, reasoning_language_contract_version=1,
        reasoning_depth_contract_version=version,
        node_generation={"sft": {"enabled": True, "style": "concise", "instruction": "Keep the task constraints.",
                                  "reasoning_depth": depth}})
    return worker


def workflow(tmp_path, client, *, depth="deep", max_rounds=0, node_prompts=None):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Arrange a safe inspection.", targets=["sft"],
        reasoning_required=True, sft_output_style="separated", node_prompts=node_prompts,
        node_generation={"sft": {"enabled": True, "style": "concise", "reasoning_depth": depth}},
        review_repair={"mode": "auto", "max_rounds": max_rounds})
    result = engine.Workflow(output, run_id, ROOT, generator=client)
    result.stage = "review"
    return result


def sourced_sample():
    row = sample()
    row.update(source_id="paper", source_context={"kind": "document", "text": SOURCE})
    return row


def controlled(row, depth="deep"):
    row = deepcopy(row)
    old = row["messages"]
    row["messages"] = apply_reasoning_depth_control(old, resolve_reasoning_depth(depth, row["id"]))
    shift = len(row["messages"]) - len(old)
    for turn in row.get("turn_reviews", []):
        turn["end_message_index"] += shift
    return row


def evidence():
    return {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}


@pytest.mark.parametrize("depth", ["quick", "standard", "deep", "mixed"])
def test_actual_depth_and_separate_instruction_come_from_current_frozen_node(depth):
    worker, row = pure_worker(depth), controlled(sample(), depth)
    row["generation_style"] = {"preset": "custom", "instruction": "Untrusted historical style.",
                               "reasoning_depth": "quick", "reasoning_depth_instruction": "SHORTEN EVERYTHING"}
    before = deepcopy(row)
    actual = resolve_reasoning_depth(depth, row["id"])
    requirements = current_claim_review_requirements(worker, "sft", row)
    style = requirements["generation_style"]
    assert style["reasoning_depth"] == actual
    assert style["reasoning_depth_instruction"] == reasoning_depth_instruction(actual)
    assert style["assistant_indices"] == [2]
    assert "Keep the task constraints." in style["instruction"]
    assert style["instruction"].count(style["reasoning_depth_instruction"]) == 1
    assert "SHORTEN EVERYTHING" not in canonical(requirements) and "Untrusted historical" not in canonical(requirements)
    assert row == before


def test_fallback_preserves_pinned_user_style_while_appending_depth_once():
    worker, row = pure_worker(), sample()
    pinned = "SAVED_USER_STYLE_{literal}"
    worker.recipe["node_generation_prompts"] = {"sft": {"concise": pinned}}
    style = _current_review_generation_style(worker, "sft", row, [])
    assert style["instruction"].startswith(pinned + "\n")
    assert style["instruction"].count(reasoning_depth_instruction("deep")) == 1
    assert style["reasoning_depth_instruction"] == reasoning_depth_instruction("deep")


@pytest.mark.parametrize("origin", ["provider_reasoning", "human_authored", "synthetic_explanation", "none"])
def test_node_depth_does_not_relabel_native_human_or_unscoped_historical_reasoning(origin):
    worker, row = pure_worker(), sample()
    row["reasoning_origin"] = origin
    row["generation_style"] = {"reasoning_depth": "deep", "instruction": "A candidate cannot grant authoring scope."}
    before = deepcopy(row)
    assert "generation_style" not in current_claim_review_requirements(worker, "sft", row)
    assert row == before


def test_per_turn_origin_keeps_provider_reasoning_out_of_authored_depth_scope():
    worker, row = pure_worker(), sample(True)
    row["turn_reviews"] = [{"end_message_index": 1, "reasoning_origin": "provider_reasoning"},
                           {"end_message_index": 3, "reasoning_origin": "prompt_styled_generation"}]
    row = controlled(row)
    style = current_claim_review_requirements(worker, "multiturn", row)["generation_style"]
    assert style["assistant_indices"] == [4]
    assert style["stage"] == "sft" and style["reasoning_depth"] == "deep"


def test_missing_marker_replays_old_style_without_depth_fields_or_new_instruction():
    worker, row = pure_worker(), sample()
    worker.recipe.pop("reasoning_depth_contract_version")
    style = current_claim_review_requirements(worker, "sft", row)["generation_style"]
    assert "reasoning_depth" not in style and "reasoning_depth_instruction" not in style
    assert reasoning_depth_instruction("deep") not in style["instruction"]


def test_marker_without_an_explicit_depth_choice_does_not_invent_a_depth():
    worker, row = pure_worker(), sample()
    worker.recipe["node_generation"]["sft"].pop("reasoning_depth")
    style = current_claim_review_requirements(worker, "sft", row)["generation_style"]
    assert "reasoning_depth" not in style and "reasoning_depth_instruction" not in style


@pytest.mark.parametrize("bad", ["mixed", "long", "", None, True, []])
def test_claim_contract_accepts_only_resolved_finite_depth_values(bad):
    requirements = {"generation_style": {"reasoning_depth": bad, "reasoning_depth_instruction": "Explain the relevant relationships."}}
    with pytest.raises(ValueError, match="invalid_claim_review_requirements"):
        claim_review_requests("sft", {"messages": sample()["messages"]}, evidence(), True, requirements)


@pytest.mark.parametrize("style", [
    {"reasoning_depth": "deep"}, {"reasoning_depth_instruction": "Explain the relevant relationships."},
    {"reasoning_depth": "deep", "reasoning_depth_instruction": ""},
    {"reasoning_depth": "deep", "reasoning_depth_instruction": "x\x00y"},
    {"reasoning_depth": "deep", "reasoning_depth_instruction": "x" * 16001},
])
def test_claim_contract_rejects_missing_unbounded_or_malformed_depth_instruction(style):
    with pytest.raises(ValueError, match="invalid_claim_review_requirements"):
        claim_review_requests("sft", {"messages": sample()["messages"]}, evidence(), True, {"generation_style": style})


def test_new_default_adds_depth_rubric_without_mutating_historical_condition_template():
    assert CONDITION_DEPTH_ADJUDICATION_BODY.startswith(CONDITION_ADJUDICATION_BODY + "\n")
    assert "任务适配的解释深度验收" not in CONDITION_ADJUDICATION_BODY
    for boundary in ("只对assistant_indices", "相关替代解释", "不要求虚构", "不规定字数", "关键桥梁缺失",
                     "不能为了表达", "原生文本和摘要", "insufficient等待复核", "不新增模型调用"):
        assert boundary in CONDITION_DEPTH_ADJUDICATION_BODY
    assert render(get("workflow.review_score", "1.19.0")) == CONDITION_ADJUDICATION_BODY


@pytest.mark.parametrize("depth", ["quick", "standard", "deep", "mixed"])
def test_real_claim_wire_gets_current_depth_and_complete_rubric_in_two_existing_calls(tmp_path, depth):
    client, row = OfflineBlindClient(), controlled(sample(), depth)
    worker = workflow(tmp_path, client, depth=depth)
    assert worker.recipe["reasoning_depth_contract_version"] == 1
    assert worker.recipe["prompts"]["workflow.review_score"]["version"] == "1.20.0"
    original = deepcopy(row)
    result = worker._review_claim_score("sft", row, evidence(), ["candidate"])
    assert accepted(result) and len(client.calls) == 2
    actual = resolve_reasoning_depth(depth, row["id"])
    for call in client.calls:
        style = call["data"]["review_requirements"]["generation_style"]
        assert style["reasoning_depth"] == actual
        assert style["reasoning_depth_instruction"] == reasoning_depth_instruction(actual)
        assert style["assistant_indices"] == [2]
        assert call["data"]["target_fields"]["reasoning_content"] == original["messages"][2]["reasoning_content"]
        if call["data"]["phase"] == "claim_adjudication":
            assert "任务适配的解释深度验收" in call["wire"][0]["content"]
            assert "关键桥梁缺失" in call["wire"][0]["content"]
    # In particular, the deep setting contains no local minimum-length gate.
    assert row["messages"] == original["messages"]
    assert len(row["messages"][2]["reasoning_content"]) < 100
    before = len(client.calls)
    assert accepted(worker._review_claim_score("sft", row, evidence(), ["candidate"]))
    assert len(client.calls) == before


def test_custom_review_prompt_stays_literal_and_is_not_replaced_by_hidden_default_rubric(tmp_path):
    custom = "USER_REVIEW_RULE_{literal}"
    client = OfflineBlindClient()
    worker = workflow(tmp_path, client, node_prompts={"review": {"workflow.review_score": custom}})
    assert accepted(worker._review_claim_score("sft", controlled(sample()), evidence(), ["candidate"]))
    scoring, = [call for call in client.calls if call["data"]["phase"] == "claim_adjudication"]
    assert custom in scoring["wire"][0]["content"]
    assert "任务适配的解释深度验收" not in scoring["wire"][0]["content"]
    assert scoring["data"]["review_requirements"]["generation_style"]["reasoning_depth"] == "deep"
    assert worker.recipe["node_prompt_templates"]["review"]["workflow.review_score"] == custom


class MissingBridgeClient(OfflineBlindClient):
    def chat(self, wire, **kwargs):
        raw = json.loads(super().chat(wire, **kwargs))
        data = model_request_data(wire)
        if (data.get("phase") == "claim_adjudication"
                and "BRIDGE_MISSING" in canonical(data["target_fields"])):
            # Every stated proposition can be true while the requested
            # explanation omits a decisive relation. Do not fake a refutation.
            raw["verdict"].update(keep=False, reasoning_valid=False,
                reason="The inspection conclusion omits the power-off prerequisite's supporting relation.")
            raw["verdict"]["scores"]["reasoning"] = 3
        return json.dumps(raw)


def test_missing_bridge_uses_same_depth_in_bounded_repair_without_fake_refuted_or_pending(tmp_path):
    client = MissingBridgeClient()
    worker = workflow(tmp_path, client, depth="deep", max_rounds=1)
    row = sourced_sample()
    row["messages"][1]["reasoning_content"] = "BRIDGE_MISSING: Inspection is the next action."
    row = controlled(row)
    user = deepcopy(row["messages"][1])
    final, = worker._review_repair_item({"target": "sft", "record": row})
    assert final["review_repair"]["status"] == "accepted" and final["review_repair"]["attempts"] == 1
    assert len(client.calls) == 5
    assert final["messages"][1] == user and final["messages"][2]["reasoning_content"]
    first_grade = next(call for call in client.calls if call["data"].get("phase") == "claim_adjudication")
    assert all(item["premises"] == [] for item in first_grade["data"]["challenge_review"]["condition_ledger"])
    repair, = [call for call in client.calls if "review_feedback" in call["data"]]
    assert repair["data"]["generation_style"]["reasoning_depth"] == "deep"
    assert repair["data"]["generation_style"]["reasoning_depth_instruction"] == reasoning_depth_instruction("deep")
    rejected = final["reasoning_distillation"]["assessments"][0]
    original_grade = rejected["claim_review"]["assessments"][0]["review"]
    assert all(claim["status"] == "supported" for claim in original_grade["raw_response"]["claim_checks"])
    assert not original_grade["condition_guard"]["needs_review"]
    assert original_grade["raw_response"]["verdict"]["scores"]["reasoning"] == 3
    assert rejected["process"]["status"] == "fail" and "review_status" not in rejected


@pytest.mark.parametrize("version", [True, False, "1", 2, [], None])
def test_invalid_recipe_depth_version_stops_before_model_calls(tmp_path, version):
    client = OfflineBlindClient()
    worker = workflow(tmp_path, client)
    worker.recipe["reasoning_depth_contract_version"] = version
    with pytest.raises(ValueError, match="invalid_reasoning_depth_contract_version"):
        worker._review_claim_score("sft", sample(), evidence(), ["candidate"])
    assert not client.calls


@pytest.mark.parametrize("mismatch", ["missing", "wrong", "duplicate", "late", "near_match"])
@pytest.mark.parametrize("entry", ["requirements", "claim", "aggregate_entry", "repair"])
def test_sealed_input_depth_mismatch_fails_before_any_model_call_or_input_change(tmp_path, mismatch, entry):
    client = OfflineBlindClient()
    worker = workflow(tmp_path, client, depth="deep", max_rounds=3)
    row = sourced_sample()
    if mismatch == "wrong":
        row = controlled(row, "quick")
    elif mismatch == "duplicate":
        row = controlled(row)
        row["messages"].insert(0, reasoning_depth_control_message("deep"))
    elif mismatch == "late":
        row["messages"].insert(1, reasoning_depth_control_message("deep"))
    elif mismatch == "near_match":
        message = reasoning_depth_control_message("deep")
        message["content"] += " "
        row["messages"].insert(0, message)
    row["generation_style"] = {"preset": "custom", "instruction": "The row tries to override current settings.",
                               "reasoning_depth": "quick"}
    before = deepcopy(row["messages"])
    body_hash = engine.digest(worker._review_payload("sft", row))
    with pytest.raises(ValueError, match="reasoning_depth_input_control_mismatch"):
        if entry == "requirements":
            current_claim_review_requirements(worker, "sft", row)
        elif entry == "claim":
            worker._review_claim_score("sft", row, evidence(), ["candidate"])
        elif entry == "aggregate_entry":
            worker._review_score("sft", row, evidence(), ["candidate"], "")
        else:
            worker._repair_content("sft", row, evidence(), ["candidate"], grade(False), "")
    assert not client.calls
    assert row["messages"] == before
    assert engine.digest(worker._review_payload("sft", row)) == body_hash


def test_review_and_direct_repair_accept_an_already_bound_control_without_changing_original_system(tmp_path):
    client = OfflineBlindClient()
    worker = workflow(tmp_path, client, depth="deep")
    row = sourced_sample()
    original_system = {"role": "system", "content": "Keep this original learner instruction literal."}
    row["messages"].insert(0, original_system)
    row = controlled(row)
    original_prefix = deepcopy(row["messages"][:-1])
    assert accepted(worker._review_claim_score("sft", row, evidence(), ["candidate"]))
    assert len(client.calls) == 2
    repaired, issue = worker._repair_content("sft", row, evidence(), ["candidate"], grade(False), "")
    assert issue is None and len(client.calls) == 3
    assert repaired["messages"][:-1] == original_prefix
    assert repaired["messages"][0] == original_system


def test_legacy_or_unconfigured_depth_does_not_require_rewriting_a_sealed_learner_prefix(tmp_path):
    for legacy in (True, False):
        client = OfflineBlindClient()
        worker = workflow(tmp_path / str(legacy), client, depth="deep")
        if legacy:
            worker.recipe.pop("reasoning_depth_contract_version")
        else:
            worker.recipe["node_generation"]["sft"].pop("reasoning_depth")
        row = sourced_sample()
        before = deepcopy(row["messages"])
        assert accepted(worker._review_claim_score("sft", row, evidence(), ["candidate"]))
        assert len(client.calls) == 2 and row["messages"] == before


def test_mixed_control_is_checked_against_the_same_stable_sample_id_before_calling(tmp_path):
    client = OfflineBlindClient()
    worker = workflow(tmp_path, client, depth="mixed")
    row = controlled(sourced_sample(), "mixed")
    assert accepted(worker._review_claim_score("sft", row, evidence(), ["candidate"]))
    assert len(client.calls) == 2
    wrong = deepcopy(row)
    wrong["messages"][0] = reasoning_depth_control_message(
        next(depth for depth in ("quick", "standard", "deep") if depth != resolve_reasoning_depth("mixed", row["id"])))
    with pytest.raises(ValueError, match="reasoning_depth_input_control_mismatch"):
        worker._review_claim_score("sft", wrong, evidence(), ["other-candidate"])
    assert len(client.calls) == 2


def prefix_cot_row():
    from tests.test_review_authored_reasoning import authored_row
    row = authored_row()
    row.update(reasoning_origin="authored_prefix_native_answer",
               reasoning_scope="authored_prefix_not_complete_provider_internal_reasoning")
    row["question"] = apply_reasoning_depth_control(
        [{"role": "user", "content": row["question"]}], "deep")
    return row


def prefix_worker(*, version=1):
    worker = pure_worker(version=version)
    worker.recipe["node_generation"]["cot"] = worker.recipe["node_generation"].pop("sft")
    return worker


@pytest.mark.parametrize("version", [None, 0, True])
def test_prefix_author_scope_is_not_retroactively_enabled_for_old_or_invalid_markers(version):
    worker, row = prefix_worker(version=version), prefix_cot_row()
    if version is None:
        worker.recipe.pop("reasoning_depth_contract_version")
    frozen = deepcopy(row)
    assert "generation_style" not in current_claim_review_requirements(worker, "cot", row)
    assert row == frozen


@pytest.mark.parametrize("origin", ["provider_reasoning", "source", "human_authored", "synthetic_explanation"])
def test_prefix_exception_does_not_expand_ordinary_provider_summary_or_other_origin_scope(origin):
    worker, row = prefix_worker(), prefix_cot_row()
    row.update(reasoning_origin=origin, reasoning_summary="A provider summary is not a complete author explanation.")
    frozen = deepcopy(row)
    assert "generation_style" not in current_claim_review_requirements(worker, "cot", row)
    assert row == frozen


def test_prefix_scope_still_requires_the_authored_protocol_and_a_cot_route():
    worker, row = prefix_worker(), prefix_cot_row()
    row.pop("reasoning_protocol_version")
    assert "generation_style" not in current_claim_review_requirements(worker, "cot", row)
    plain_sft = controlled(sample())
    plain_sft["reasoning_origin"] = "authored_prefix_native_answer"
    assert "generation_style" not in current_claim_review_requirements(pure_worker(), "sft", plain_sft)


def test_linked_prefix_scope_is_only_the_final_rewritten_assistant_not_upstream_native_turns():
    worker, row = prefix_worker(), prefix_cot_row()
    row.update(reasoning_route="sft_via_cot", messages=[
        reasoning_depth_control_message("deep"),
        {"role": "user", "content": "Explain the earlier topic."},
        {"role": "assistant", "content": "Earlier visible answer.", "reasoning_content": "EARLIER_NATIVE_TEXT"},
        {"role": "user", "content": "求解2x+3=13。"},
        {"role": "assistant", "content": row["answer"], "reasoning_content": "\n".join(row["reasoning"])},
    ], turn_reviews=[{"end_message_index": 2, "reasoning_origin": "provider_reasoning"},
                     {"end_message_index": 4, "reasoning_origin": "provider_reasoning"}])
    frozen = deepcopy(row)
    style = current_claim_review_requirements(worker, "sft", row)["generation_style"]
    assert style["assistant_indices"] == [4]
    assert style["stage"] == "cot" and style["reasoning_depth"] == "deep"
    assert row == frozen


def test_real_offline_native_prefix_row_receives_depth_scope_and_same_input_guard(tmp_path, monkeypatch):
    from tests.test_workflow_native_prefill import PREFIX, setup_workflow
    from lib.domain.workflow_reasoning_route import cot_with_sft_context
    (tmp_path / "sdk").mkdir()
    _, seed, client, prefix_reviewer, sent, _ = setup_workflow(tmp_path / "sdk", monkeypatch)
    output = tmp_path / "prefix-output"
    run_id = engine.create_run(output, targets=["cot"], brief="Explain maintenance order.",
        reasoning_required=True, node_generation={"cot": {"enabled": True, "style": "default",
            "answer_mode": "native_prefill", "reasoning_depth": "deep"}})
    worker = engine.Workflow(output, run_id, ROOT, generator=client, jev=prefix_reviewer)
    worker.stage = "cot"
    try:
        row, = worker.styled_cot(seed, worker.generation_style("cot", seed["id"]))
        assert row["status"] == "eligible" and len(sent) == 2
        assert row["reasoning"] == [PREFIX] and row["reasoning_origin"] == "authored_prefix_native_answer"
        assert row["reasoning_scope"] == "authored_prefix_not_complete_provider_internal_reasoning"
        assert row["answer_response_details"]["reasoning_content"] == "provider-only continuation"
        frozen = deepcopy(row)
        style = current_claim_review_requirements(worker, "cot", row)["generation_style"]
        assert style["reasoning_depth"] == "deep" and style["assistant_indices"] == [2]
        assert style["reasoning_depth_instruction"] == reasoning_depth_instruction("deep")
        assert row == frozen  # Neither prefix provenance nor native receipt is rewritten.
        linked = cot_with_sft_context(seed, row)
        linked_style = current_claim_review_requirements(worker, "sft", linked)["generation_style"]
        assert linked_style["assistant_indices"] == [2] and linked_style["reasoning_depth"] == "deep"
        before_calls = (len(sent), len(prefix_reviewer.calls))
        missing = deepcopy(row)
        missing["question"] = [message for message in missing["question"] if message["role"] != "system"]
        with pytest.raises(ValueError, match="reasoning_depth_input_control_mismatch"):
            worker._review_claim_score("cot", missing, evidence(), ["sealed-prefix"])
        assert (len(sent), len(prefix_reviewer.calls)) == before_calls
        worker.recipe.pop("reasoning_depth_contract_version")
        assert "generation_style" not in current_claim_review_requirements(worker, "cot", row)
    finally:
        client.client.close()
