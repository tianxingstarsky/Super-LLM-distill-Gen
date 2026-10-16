"""Real workflow boundaries for opt-in prefix-isolated claim assessment."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.domain.claim_review import claim_review_requests
from lib.domain.reasoning_candidates import assessment_from_verdict, select_reasoning_candidate
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure.training_workflow import digest
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair, current_claim_review_requirements
from lib.infrastructure import training_workflow as engine
from lib.domain.workflow_node_prompts import snapshot_node_prompts, validate_node_prompt_snapshot
from lib.prompts import get, render
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SOURCE = "Power must be off before inspection. Record the result after inspection."


def grade(keep=True):
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": 5,
            "scores": {name: 5 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "The actual prerequisite is preserved."}


def sample(multi=False):
    result = {"id": "sample", "status": "eligible", "reasoning_origin": "prompt_styled_generation",
              "messages": [{"role": "user", "content": "Can I inspect it now?"},
                           {"role": "assistant", "content": "Confirm the power is off first.",
                            "reasoning_content": "Inspection depends on the power being off."}]}
    if multi:
        result["messages"].extend([
            {"role": "user", "content": "Inspection is complete. FUTURE_ONLY_736"},
            {"role": "assistant", "content": "Record the result.",
             "reasoning_content": "Recording follows the inspection that you confirmed complete."}])
    return result


def response(request, *, bad=False):
    claims = [{"field": field, "quote": text, "status": "refuted" if bad else "supported",
               "severity": "material", "basis": {"kind": "source",
                   "evidence": [{"quote": SOURCE, "source_path": ["teacher_evidence"]}],
                   "derivation": "The source defines the prerequisite before inspection, not after it."}}
              for field, text in request["target_fields"].items()]
    return {"claim_review_protocol_version": 1, "scope": deepcopy(request["scope"]),
            "coverage": "complete", "checked_fields": list(request["target_fields"]),
            "claim_checks": claims, "verdict": grade()}


class Probe(WorkflowReviewRepair):
    def __init__(self, *, enabled=True, bad=False, responses=None, audit=False):
        self.recipe = {"review_context_protocol_version": 2, "turn_review_protocol_version": 1,
            "review_repair": {"mode": "auto", "max_rounds": 0}, "reasoning_revision_protocol_version": 1,
            "reasoning_required": True}
        if enabled:
            self.recipe["claim_review_protocol_version"] = 1
        if audit:
            self.recipe.update(reasoning_distillation_audit_version=1, authored_reasoning_warmup_version=1,
                               node_generation={"sft": {"enabled": True}})
        self.state = {"id": "run", "stages": {"review": {}}}
        self.stage, self.calls, self.bad, self.responses = "review", [], bad, responses

    def save(self, **kwargs):
        pass

    def _review_evidence(self, row):
        return {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}

    def ask(self, key, role, prompt_id, data, **kwargs):
        self.calls.append({"key": deepcopy(key), "role": role, "prompt_id": prompt_id,
                           "data": deepcopy(data), "kwargs": deepcopy(kwargs)})
        if self.responses is not None:
            return self.responses.pop(0)
        if data.get("claim_review_protocol_version") == 1:
            return response(data, bad=self.bad)
        if data.get("learner_context_only"):
            return {"keep": True, "reason": "The actual user request is understandable."}
        return {**grade(), "turn_verdicts": [{**grade(), "message_index": item["message_index"],
            "content_sha256": item["content_sha256"], "leakage_free": True, "critical_error": False}
            for item in data.get("supervised_assistant_turns", [])]}


def test_new_single_turn_replaces_aggregate_instead_of_doubling_calls():
    probe, row = Probe(), sample()
    check = probe._review_score("sft", row, probe._review_evidence(row), ["sample", "score"], "OLD_FEEDBACK")
    assert accepted(check) and len(probe.calls) == 1
    call = probe.calls[0]
    assert call["role"] == "generation" and call["prompt_id"] == "workflow.review_score"
    assert {name: value for name, value in call["kwargs"].items() if name != "instruction"} == {
        "allow_reasoning_fallback": False, "model_stage": "review", "prompt_stage": "review"}
    assert "shape" in call["kwargs"]["instruction"] and "OLD_FEEDBACK" not in call["kwargs"]["instruction"]
    assert "OLD_FEEDBACK" not in canonical(call["data"])
    assert row["claim_review"]["status"] == "complete"


def test_multiturn_model_requests_physically_exclude_future_and_plan_metadata():
    probe, row = Probe(), sample(True)
    row.update(qa_contract={"guidance": "PRIVATE_PLAN", "next_steps": ["FUTURE_PLAN"]},
               judge=grade(), revision_context={"design_requirements": {"answer": "OLD_GRADE"}})
    original = deepcopy(row["messages"])
    evidence = {**probe._review_evidence(row), "judge": grade(), "qa_contract": "SOURCE_PLAN"}
    check = probe._review_claim_score("multiturn", row, evidence, ["sample", "score"])
    assert accepted(check) and len(probe.calls) == 2 and row["messages"] == original
    first, second = (call["data"] for call in probe.calls)
    assert "FUTURE_ONLY_736" not in canonical(first) and "FUTURE_ONLY_736" in canonical(second)
    assert first["messages"] == original[:2] and second["messages"] == original
    for call in probe.calls:
        wire = canonical(call["data"])
        assert all(token not in wire for token in ("PRIVATE_PLAN", "FUTURE_PLAN", "OLD_GRADE", "SOURCE_PLAN"))


def test_review_keeps_only_current_frozen_style_and_learning_goal_without_old_plans():
    probe, row = Probe(audit=True), sample(True)
    probe.recipe.update(qa_director={"enabled": True, "learning_goal": "knowledge_application"},
        reasoning_language_contract_version=3,
        node_generation={"sft": {"enabled": True, "style": "custom",
            "instruction": "CURRENT_NODE_STYLE_884", "reasoning_language": "zh_en"}})
    row["generation_style"] = {"instruction": "OLD_CANDIDATE_STYLE_623"}
    row["qa_contract"] = {"guidance": "PRIVATE_DIRECTOR_PLAN_672", "user_language": "OLD_PLAN_LANGUAGE"}
    row["turn_reviews"] = [{"end_message_index": 3, "reasoning_origin": "provider_reasoning"}]
    requirements = current_claim_review_requirements(probe, "multiturn", row)
    assert requirements["generation_style"]["assistant_indices"] == [1]
    assert requirements["generation_style"]["reasoning_language"] == "zh_en"
    assert requirements["learning_goal"] == "knowledge_application"
    probe._review_claim_score("multiturn", row, probe._review_evidence(row), ["sample"])
    for call in probe.calls:
        wire = canonical(call["data"])
        assert "CURRENT_NODE_STYLE_884" in wire
        assert "OLD_CANDIDATE_STYLE_623" not in wire and "PRIVATE_DIRECTOR_PLAN_672" not in wire
        assert "OLD_PLAN_LANGUAGE" not in wire
        assert call["data"]["review_requirements"] == requirements
    row["reasoning_origin"] = "human_revision"
    assert "generation_style" not in current_claim_review_requirements(probe, "multiturn", row)


def test_changed_current_style_invalidates_exact_scope_even_when_body_is_unchanged():
    probe, row = Probe(audit=True), sample()
    probe.recipe["node_generation"] = {"sft": {"enabled": True, "style": "custom", "instruction": "STYLE_A"}}
    probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"])
    first = probe.calls[-1]
    probe.recipe["node_generation"]["sft"]["instruction"] = "STYLE_B"
    probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"])
    second = probe.calls[-1]
    assert first["data"]["scope"]["candidate_sha256"] == second["data"]["scope"]["candidate_sha256"]
    assert first["data"]["scope"]["scope_sha256"] != second["data"]["scope"]["scope_sha256"]
    assert first["key"] != second["key"]


def test_all_five_raw_grades_cannot_override_material_claim_failure():
    probe, row = Probe(bad=True), sample()
    check = probe._review_score("sft", row, probe._review_evidence(row), ["sample"], "")
    assert not accepted(check) and not check["keep"] and check["scores"]["correctness"] == 5
    audit = row["claim_review"]
    assert audit["assessments"][0]["review"]["raw_response"]["verdict"] == grade()
    assert audit["assessments"][0]["review"]["turn_verdict"]["critical_error"]
    assert row["messages"][1]["reasoning_content"]


def test_absent_marker_keeps_old_single_aggregate_payload_and_call_key():
    probe, row = Probe(enabled=False), sample(True)
    check = probe._review_score("multiturn", row, probe._review_evidence(row), ["old", 0], "USER_FEEDBACK")
    assert accepted(check) and len(probe.calls) == 1 and "claim_review" not in row
    call = probe.calls[0]
    assert call["key"] == ["old", 0, "score"]
    assert call["data"]["candidate"]["messages"] == row["messages"]
    assert call["data"]["instruction"] == "USER_FEEDBACK"
    assert "claim_review_protocol_version" not in call["data"]


def test_configured_jev_role_is_preserved_for_structured_claim_checks():
    probe, row = Probe(), sample()
    probe.recipe["package_review"] = {"enabled": True}
    probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"])
    assert probe.calls[0]["role"] == "jev"


def test_missing_fields_fail_closed_after_one_format_retry_without_erasing_raw_checkpoints():
    probe, row = Probe(responses=[grade(), grade()]), sample()
    with pytest.raises(ValueError, match="invalid_claim_review_response"):
        probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"])
    assert len(probe.calls) == 2 and row["claim_review"]["status"] == "invalid_after_retry"
    assert len(row["claim_review"]["schema_failures"]) == 2
    assert probe.calls[1]["key"] == [*probe.calls[0]["key"], "format_repair", 1]
    assert not hasattr(probe, "invalidated")


def test_invalid_marker_and_missing_reasoning_do_not_reach_model():
    probe, row = Probe(), sample()
    probe.recipe["claim_review_protocol_version"] = True
    with pytest.raises(ValueError, match="invalid_claim_review_protocol_version"):
        probe._review_score("sft", row, probe._review_evidence(row), ["sample"], "")
    assert not probe.calls
    probe.recipe["claim_review_protocol_version"] = 1
    row["messages"][1].pop("reasoning_content")
    assert not accepted(probe._review_score("sft", row, probe._review_evidence(row), ["sample"], ""))
    assert not probe.calls


def test_cot_full_reasoning_lists_are_checked_without_rewriting_training_payload():
    probe = Probe()
    row = {"id": "cot", "question": "What comes before inspection?", "answer": "Power off.",
           "reasoning": ["Power-off is necessary.", "Inspection follows power-off."]}
    frozen = deepcopy(row)
    check = probe._review_claim_score("cot", row, probe._review_evidence(row), ["cot"])
    assert accepted(check) and len(probe.calls) == 1
    assert probe.calls[0]["data"]["target_fields"]["reasoning_content"] == "\n".join(frozen["reasoning"])
    assert row["reasoning"] == frozen["reasoning"] and "turn_verdicts" not in check


def test_candidate_audit_names_raw_claim_responses_separately_from_derived_aggregate():
    probe, row = Probe(audit=True), sample()
    final = probe._review_repair_item({"target": "sft", "record": row})[0]
    assert final["status"] == "eligible" and len(probe.calls) == 1
    assessment = final["reasoning_distillation"]["assessments"][0]
    assert assessment["method"] == "llm_claim_review"
    assert "derived_verdict" in assessment and "raw_verdict" not in assessment
    assert assessment["claim_review"] == final["claim_review"]
    assert assessment["claim_review"]["assessments"][0]["review"]["raw_response"]["verdict"] == grade()
    assert final["reasoning_assessment_reference"]["checkpoints"] == [
        item["checkpoint"] for item in final["claim_review"]["assessments"]]


@pytest.mark.parametrize("mutation", [
    lambda a: a["claim_review"]["assessments"][0]["checkpoint"].__setitem__("response_sha256", "0" * 64),
    lambda a: a["claim_review"]["assessments"][0]["checkpoint"].__setitem__("checkpoint_sha256", "0" * 64),
    lambda a: a["claim_review"]["assessments"][0]["review"]["raw_response"]["verdict"].__setitem__("reason", "Changed grade"),
    lambda a: a["claim_review"].__setitem__("source_evidence_sha256", "0" * 64),
    lambda a: a["derived_verdict"].__setitem__("keep", False),
])
def test_claim_selection_rejects_altered_grades_sources_and_checkpoint_bindings(mutation):
    probe, row = Probe(), sample()
    check = probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"])
    payload = probe._review_payload("sft", row)
    receipt = assessment_from_verdict(payload, check, evidence_ref="actual-checkpoints",
        method="llm_claim_review", claim_review=row["claim_review"])
    changed = deepcopy(receipt)
    mutation(changed)
    with pytest.raises(ValueError):
        select_reasoning_candidate([{"candidate": payload, "assessment": changed}])
    assert select_reasoning_candidate([{"candidate": payload, "assessment": receipt}])["status"] == "accepted"


def test_payload_or_future_edit_invalidates_original_full_candidate_audit():
    probe, row = Probe(), sample(True)
    check = probe._review_claim_score("multiturn", row, probe._review_evidence(row), ["sample"])
    payload = probe._review_payload("multiturn", row)
    receipt = assessment_from_verdict(payload, check, evidence_ref="actual-checkpoints",
        method="llm_claim_review", claim_review=row["claim_review"])
    changed = deepcopy(payload)
    changed["messages"][2]["content"] += " New condition."
    with pytest.raises(ValueError, match="stale_reasoning_candidate_assessment"):
        select_reasoning_candidate([{"candidate": changed, "assessment": receipt}])


def test_successful_format_retry_keeps_both_real_checkpoint_references():
    row = sample()
    probe = Probe()
    request = claim_review_requests("sft", probe._review_payload("sft", row), probe._review_evidence(row), True)[0]
    probe.responses = [{}, response(request)]
    check = probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"])
    assert accepted(check) and len(probe.calls) == 2
    audit = row["claim_review"]
    assert len(audit["schema_failures"]) == 1
    assert audit["schema_failures"][0]["checkpoint"]["response_sha256"] == digest({})
    checkpoint = audit["assessments"][0]["checkpoint"]
    assert checkpoint["key"] == probe.calls[1]["key"]
    assert checkpoint["checkpoint_sha256"] == digest(["call", checkpoint["key"]])
    assert assessment_from_verdict(probe._review_payload("sft", row), check,
        evidence_ref="actual-checkpoints", method="llm_claim_review", claim_review=audit)["accepted"]


def test_catalog_selects_explicit_wire_contract_and_preserves_custom_literal_body():
    old = snapshot_node_prompts(None, recipe_version=22, learner_context_review_version=1)
    new = snapshot_node_prompts(None, recipe_version=22, learner_context_review_version=1,
                                claim_review_protocol_version=1)
    assert old["review"]["workflow.review_score"] == render(get("workflow.review_score", version="1.11.0"))
    assert new["review"]["workflow.review_score"] == render(get("workflow.review_score", version="1.12.0"))
    for stage, templates in old.items():
        for prompt_id, text in templates.items():
            if prompt_id != "workflow.review_score":
                assert text == new[stage][prompt_id]
    custom = "CUSTOM CLAIM POLICY {literal}: return the configured contract."
    pinned = snapshot_node_prompts({"review": {"workflow.review_score": custom}}, recipe_version=22,
                                  learner_context_review_version=1, claim_review_protocol_version=1)
    assert pinned["review"]["workflow.review_score"] == custom
    assert validate_node_prompt_snapshot(old, "SYSTEM", recipe_version=22, learner_context_review_version=1) == old
    assert validate_node_prompt_snapshot(new, "SYSTEM", recipe_version=22,
        learner_context_review_version=1, claim_review_protocol_version=1) == new
    with pytest.raises(ValueError, match="invalid_claim_review_protocol_version"):
        snapshot_node_prompts(None, recipe_version=22, claim_review_protocol_version=True)


class OfflineClaimClient:
    model = "offline-prefix-claim-review"

    def __init__(self, *, interrupt_at=None):
        self.usage, self.calls, self.interrupt_at = {"calls": 0}, [], interrupt_at

    def chat(self, wire, **kwargs):
        data = model_request_data(wire)
        self.calls.append({"wire": deepcopy(wire), "data": data})
        self.usage["calls"] += 1
        if self.interrupt_at == len(self.calls):
            raise RuntimeError("offline_claim_interrupt")
        return json.dumps(response(data))


def real_worker(tmp_path, client, *, jev=None, package_review=None):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Arrange safe equipment inspection.", targets=["multiturn"],
                               reasoning_required=True, review_repair={"mode": "auto", "max_rounds": 0},
                               package_review=package_review)
    # This file freezes protocol-one wire behavior. The new factory's blind
    # protocol is exercised independently in test_blind_claim_workflow.py.
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    recipe["claim_review_protocol_version"] = 1
    recipe.pop("claim_review_quote_matching_version", None)
    recipe.pop("claim_reference_policy_version", None)
    recipe["node_prompt_templates"]["review"].pop("workflow.review_reference", None)
    old_score = get("workflow.review_score", version="1.12.0")
    recipe["node_prompt_templates"]["review"]["workflow.review_score"] = render(old_score)
    recipe["prompts"]["workflow.review_score"] = {"version": old_score.version, "sha256": digest(old_score.template)}
    engine.atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = digest(recipe)
    engine.atomic_json(path / "state.json", state)
    worker = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client, jev=jev)
    worker.stage = "review"
    return worker


def test_real_factory_ask_preserves_source_memory_and_raw_checkpoints_without_double_score(tmp_path):
    client = OfflineClaimClient()
    worker = real_worker(tmp_path, client)
    assert worker.recipe["claim_review_protocol_version"] == 1
    row = sample(True)
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    value = worker._review_claim_score("multiturn", row, evidence, ["sample"])
    assert accepted(value) and len(client.calls) == 2
    assert "FUTURE_ONLY_736" not in canonical(client.calls[0]["wire"])
    for call, item in zip(client.calls, row["claim_review"]["assessments"]):
        assert "claim_checks" in call["wire"][0]["content"]
        assert any("untrusted_source_memory" in message.get("content", "") for message in call["wire"])
        # The memory remains in its real system role instead of being copied
        # back into a fake user/source reconstruction for this assertion.
        user = json.loads(call["wire"][-1]["content"])
        assert SOURCE not in canonical(user)
        memory = json.loads(call["wire"][1]["content"].removeprefix("<untrusted_source_memory>\n")
                            .removesuffix("\n</untrusted_source_memory>"))
        assert memory["source_evidence"] == evidence
        assert user["source_evidence_ref"]["sha256"] == digest(evidence)
        checkpoint = item["checkpoint"]
        saved = engine.read_json(worker.path / "checkpoints" / checkpoint["stage"] /
                                 (checkpoint["checkpoint_sha256"] + ".json"))
        assert saved["data"] == item["review"]["raw_response"]
        assert saved["sha256"] == checkpoint["response_sha256"]
    again = worker._review_claim_score("multiturn", row, evidence, ["sample"])
    assert again == value and len(client.calls) == 2
    assert client.calls[0]["wire"][1] == client.calls[1]["wire"][1]


def test_interrupted_real_prefix_review_resumes_paid_prefix_without_repeating_it(tmp_path):
    client = OfflineClaimClient(interrupt_at=2)
    worker = real_worker(tmp_path, client)
    row = sample(True)
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    with pytest.raises(RuntimeError, match="offline_claim_interrupt"):
        worker._review_claim_score("multiturn", row, evidence, ["sample"])
    assert row["claim_review"]["status"] == "pending" and len(row["claim_review"]["assessments"]) == 1
    resumed_client = OfflineClaimClient()
    resumed = engine.Workflow(worker.path.parent.parent, worker.state["id"], worker.root, generator=resumed_client)
    resumed.stage = "review"
    assert accepted(resumed._review_claim_score("multiturn", row, evidence, ["sample"]))
    assert len(resumed_client.calls) == 1
    assert resumed_client.calls[0]["data"]["scope"]["message_index"] == 3


def test_real_cancel_is_respected_before_any_claim_request(tmp_path):
    client = OfflineClaimClient()
    worker = real_worker(tmp_path, client)
    (worker.path / "cancel.json").write_text("{}", encoding="utf-8")
    with pytest.raises(engine.Cancelled):
        worker._review_claim_score("multiturn", sample(True),
            {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}, ["sample"])
    assert not client.calls


def test_real_workflow_uses_configured_scoring_client_instead_of_correction_client(tmp_path):
    class UnusedCorrection:
        model = "unused-correction"
        usage = {}
        def chat(self, *args, **kwargs):
            raise AssertionError("The selected scoring model must own claim assessment.")
    reviewer = OfflineClaimClient()
    worker = real_worker(tmp_path, UnusedCorrection(), jev=reviewer,
                         package_review={"enabled": True, "node": "jev"})
    row = sample()
    check = worker._review_claim_score("sft", row,
        {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}, ["sample"])
    assert accepted(check) and len(reviewer.calls) == 1
    assert worker.state["usage"]["jev"]["calls"] == 1 and "generation" not in worker.state["usage"]
