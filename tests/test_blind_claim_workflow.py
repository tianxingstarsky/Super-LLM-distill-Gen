"""Version-two production review uses a truly candidate-blind first request."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.domain.reasoning_candidates import assessment_from_verdict, select_reasoning_candidate
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from tests.test_claim_review_workflow import Probe, SOURCE, grade, response, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def blind_response(request, *, partial=False):
    return {"claim_review_protocol_version": 2, "phase": "blind_reference", "scope": deepcopy(request["scope"]),
        "coverage": "partial" if partial else "complete", "limitations": [],
        "reference_points": [{"id": "power-before-inspection", "point": "Power-off is a necessary inspection prerequisite.",
            "severity": "material", "basis": {"kind": "source", "evidence": [
                {"quote": SOURCE, "source_path": ["teacher_evidence"]}],
                "derivation": "The source places power-off before inspection; subsequent recording cannot replace that prerequisite."}}]}


def grade_response(request, *, relation="consistent"):
    value = response(request)
    value["claim_review_protocol_version"] = 2
    value["reference_checks"] = [{"id": point["id"], "relation": relation, "field": "content",
        "quote": request["target_fields"]["content"],
        "reason": "The actual draft's prerequisite is compared with the independently prepared power-off condition."}
        for point in request["reference_review"]["reference_points"]]
    return value


def anchored_grade_response(request, *, bad=False, severity="material"):
    ids = [unit["id"] for unit in request["target_units"]]
    return {"claim_review_protocol_version": 3, "scope_sha256": request["scope"]["scope_sha256"],
        "coverage": "complete", "checked_unit_ids": ids,
        "claim_checks": [{"unit_id": unit["id"], "status": "refuted" if bad else "supported",
            "severity": severity, "basis": {"kind": "source", "evidence": [{"quote": SOURCE,
                "source_path": ["teacher_evidence"]}],
                "derivation": "Power-off is the actual inspection prerequisite; the answer must preserve that condition."}}
            for unit in request["target_units"]],
        "reference_checks": [{"id": point["id"], "relation": "consistent", "unit_id": ids[0],
            "reason": "The current candidate unit is compared with the independently prepared prerequisite."}
            for point in request["reference_review"]["reference_points"]], "verdict": grade()}


def challenge_response(request, *, bad=False, severity="material"):
    units = request["target_units"]
    issues = []
    if bad:
        unit = next((unit for unit in units if "WRONG_DRAFT" in unit["text"]), units[0])
        issues = [{"id": "inspection-prerequisite", "unit_id": unit["id"], "severity": severity,
            "problem": "The candidate permits inspection while the power prerequisite is unmet.",
            "counterexample": "With power on, the proposed inspection contradicts the source's required power-off condition.",
            "basis": {"kind": "source", "evidence": [{"quote": SOURCE, "source_path": ["teacher_evidence"]}],
                "derivation": "The source requires power-off before inspection, so a powered inspection is not a permitted instance."}}]
    return {"claim_review_protocol_version": 4, "phase": "claim_challenge",
        "scope_sha256": request["scope"]["scope_sha256"], "issues": issues, "limitations": []}


def adversarial_grade_response(request, *, decision="confirmed"):
    issues = request["challenge_review"]["issues"]
    challenged = {item["unit_id"] for item in issues} if decision == "confirmed" else set()
    def basis():
        return {"kind": "source", "evidence": [{"quote": SOURCE, "source_path": ["teacher_evidence"]}],
            "derivation": "Power-off is an explicit inspection prerequisite in the source; it must be checked against the candidate action."}
    return {"claim_review_protocol_version": 4, "scope_sha256": request["scope"]["scope_sha256"],
        "coverage": "complete", "checked_unit_ids": [unit["id"] for unit in request["target_units"]],
        "claim_checks": [{"unit_id": unit["id"], "status": "refuted" if unit["id"] in challenged else "supported",
            "severity": next((issue["severity"] for issue in issues if issue["unit_id"] == unit["id"]), "material"),
            "basis": basis()} for unit in request["target_units"]],
        "challenge_checks": [{"id": issue["id"], "unit_id": issue["unit_id"], "decision": decision,
            "basis": basis()} for issue in issues], "verdict": grade()}


class BlindProbe(Probe):
    def __init__(self, *, relation="consistent", partial=False, responses=None, audit=False):
        super().__init__(audit=audit)
        self.recipe["claim_review_protocol_version"] = 2
        self.relation, self.partial, self.responses = relation, partial, responses

    def ask(self, key, role, prompt_id, data, **kwargs):
        self.calls.append({"key": deepcopy(key), "role": role, "prompt_id": prompt_id,
                           "data": deepcopy(data), "kwargs": deepcopy(kwargs)})
        if self.responses is not None:
            return self.responses.pop(0)
        return (blind_response(data, partial=self.partial) if prompt_id == "workflow.review_reference"
                else grade_response(data, relation=self.relation))


def test_blind_request_has_no_current_answer_reasoning_candidate_hash_or_future():
    probe, row = BlindProbe(), sample(True)
    row["messages"][1].update(content="CURRENT_ANSWER_182", reasoning_content="CURRENT_REASONING_182")
    row.update(qa_contract={"guidance": "OLD_PLAN_887"}, judge=grade())
    probe._review_claim_score("multiturn", row, probe._review_evidence(row), ["row", "attempt", 0])
    assert len(probe.calls) == 4
    first = probe.calls[0]
    assert first["prompt_id"] == "workflow.review_reference"
    assert first["data"]["messages"] == row["messages"][:1]
    wire = canonical(first["data"])
    assert all(token not in wire for token in ("CURRENT_ANSWER_182", "CURRENT_REASONING_182", "FUTURE_ONLY_736", "OLD_PLAN_887"))
    assert all(field not in first["data"] for field in ("target_fields", "candidate", "judge", "instruction", "reference_review"))
    assert "candidate_sha256" not in first["data"]["scope"] and "message_sha256" not in first["data"]["scope"]
    assert first["key"] == ["blind_reference", "multiturn", first["data"]["scope"]["scope_sha256"]]
    assert probe.calls[2]["data"]["reference_review"] == blind_response(first["data"])


def test_material_reference_conflict_vetoes_all_five_raw_grade_without_score_edits():
    probe, row = BlindProbe(relation="contradicted"), sample()
    check = probe._review_score("sft", row, probe._review_evidence(row), ["sample"], "IGNORED_OLD_FEEDBACK")
    assert not accepted(check) and not check["keep"]
    audit = row["claim_review"]
    assert audit["version"] == 2 and audit["assessments"][0]["review"]["raw_response"]["verdict"] == grade()
    assert check["correctness"] == 5 and all(value == 5 for value in check["scores"].values())
    assert "IGNORED_OLD_FEEDBACK" not in canonical(probe.calls)
    assert row["messages"][1]["reasoning_content"]


def test_partial_blind_reference_is_unverified_and_never_an_implicit_pass():
    probe, row = BlindProbe(partial=True), sample()
    assert not accepted(probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"]))
    assert row["claim_review"]["blind_reviews"][0]["review"]["raw_response"]["coverage"] == "partial"


def test_invalid_blind_output_stops_before_candidate_assessment_and_retains_both_attempts():
    probe, row = BlindProbe(responses=[grade(), grade()]), sample()
    with pytest.raises(ValueError, match="invalid_blind_review_response"):
        probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"])
    assert len(probe.calls) == 2
    assert all(call["prompt_id"] == "workflow.review_reference" for call in probe.calls)
    assert row["claim_review"]["status"] == "invalid_after_retry"
    assert [failure["phase"] for failure in row["claim_review"]["schema_failures"]] == ["blind_reference"] * 2


def test_configured_reviewer_owns_both_independent_model_calls():
    probe, row = BlindProbe(), sample()
    probe.recipe["package_review"] = {"enabled": True}
    assert accepted(probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"]))
    assert [call["role"] for call in probe.calls] == ["jev", "jev"]


def test_new_candidate_audit_revalidates_both_raw_phases_and_all_bindings():
    probe, row = BlindProbe(audit=True), sample()
    final = probe._review_repair_item({"target": "sft", "record": row})[0]
    assert final["status"] == "eligible" and len(probe.calls) == 2
    assessment = final["reasoning_distillation"]["assessments"][0]
    assert assessment["method"] == "llm_claim_review" and assessment["claim_review"]["version"] == 2
    assert "raw_verdict" not in assessment and "derived_verdict" in assessment
    assert assessment["claim_review"]["blind_reviews"][0]["review"]["semantic_verification"] == "reviewer_reported_not_independently_verified"
    body = probe._review_payload("sft", row)
    assert select_reasoning_candidate([{"candidate": body, "assessment": assessment}])["status"] == "accepted"


@pytest.mark.parametrize("change", [
    lambda a: a["blind_reviews"][0]["checkpoint"].__setitem__("response_sha256", "0" * 64),
    lambda a: a["blind_reviews"][0]["review"]["raw_response"]["reference_points"][0].__setitem__("point", "Changed reference"),
    lambda a: a["assessments"][0]["review"]["request"]["scope"].__setitem__("blind_request_sha256", "0" * 64),
    lambda a: a["assessments"][0]["checkpoint"].__setitem__("checkpoint_sha256", "0" * 64),
    lambda a: a.__setitem__("source_input_sha256", "0" * 64),
    lambda a: a.__setitem__("blind_reviews", []),
])
def test_new_audit_rejects_changed_reference_source_keys_and_missing_blind_checks(change):
    probe, row = BlindProbe(), sample()
    check = probe._review_claim_score("sft", row, probe._review_evidence(row), ["sample"])
    audit = deepcopy(row["claim_review"])
    change(audit)
    with pytest.raises(ValueError):
        assessment_from_verdict(probe._review_payload("sft", row), check, evidence_ref="real-checkpoints",
            method="llm_claim_review", claim_review=audit)


class OfflineBlindClient:
    model = "offline-blind-reference-review"

    def __init__(self, *, interrupt_at=None, repair=False):
        self.calls, self.usage, self.interrupt_at, self.repair = [], {"calls": 0}, interrupt_at, repair

    def chat(self, wire, **kwargs):
        data = model_request_data(wire)
        self.calls.append({"wire": deepcopy(wire), "data": deepcopy(data)})
        self.usage["calls"] += 1
        if self.interrupt_at == len(self.calls):
            raise RuntimeError("offline_blind_interrupt")
        if "review_feedback" in data:
            from tests.test_turn_review_and_reasoning_repair import trace
            body = {"assistant_updates": [{"message_index": index,
                "content": "Confirm the power is off first.", "reasoning_content": "Inspection requires power-off.",
                "reasoning_trace": trace()} for index in data["required_reasoning_indices"]]}
            return json.dumps({"record": body, "quotes": [SOURCE], "uncertain": False})
        if data.get("claim_review_protocol_version") == 4:
            return json.dumps(challenge_response(data,
                bad=self.repair and "WRONG_DRAFT" in canonical(data["target_units"]))
                if data["phase"] == "claim_challenge" else adversarial_grade_response(data))
        if data.get("phase") == "blind_reference":
            return json.dumps(blind_response(data))
        if data.get("claim_review_protocol_version") == 3:
            return json.dumps(anchored_grade_response(data,
                bad=self.repair and "WRONG_DRAFT" in canonical(data["target_fields"])))
        relation = "contradicted" if self.repair and "WRONG_DRAFT" in canonical(data["target_fields"]) else "consistent"
        return json.dumps(grade_response(data, relation=relation))


def worker(tmp_path, client):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Arrange safe equipment inspection.", targets=["sft"],
                               reasoning_required=True, review_repair={"mode": "auto", "max_rounds": 0})
    freeze_pre_anchor(output, run_id)
    result = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client)
    result.stage = "review"
    return result


def freeze_pre_anchor(output, run_id):
    """Replay the completed protocol-two contract, not the new unit protocol."""
    from lib.prompts import get, render
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    recipe.pop("claim_review_anchor_protocol_version", None)
    recipe.pop("claim_review_adversarial_protocol_version", None)
    frozen = get("workflow.review_score", "1.16.0")
    recipe["node_prompt_templates"]["review"]["workflow.review_score"] = render(frozen)
    recipe["prompts"]["workflow.review_score"] = {"version": frozen.version, "sha256": engine.digest(frozen.template)}
    reference = get("workflow.review_reference", "1.0.1")
    recipe["node_prompt_templates"]["review"]["workflow.review_reference"] = render(reference)
    recipe["prompts"]["workflow.review_reference"] = {"version": reference.version, "sha256": engine.digest(reference.template)}
    engine.atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    engine.atomic_json(path / "state.json", state)


def freeze_pre_adversarial(output, run_id):
    """Keep protocol three's previously submitted bodies and wire contract."""
    from lib.prompts import get, render
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    recipe.pop("claim_review_adversarial_protocol_version", None)
    for prompt_id, version in (("workflow.review_score", "1.17.0"), ("workflow.review_reference", "1.0.1")):
        frozen = get(prompt_id, version)
        recipe["node_prompt_templates"]["review"][prompt_id] = render(frozen)
        recipe["prompts"][prompt_id] = {"version": frozen.version, "sha256": engine.digest(frozen.template)}
    engine.atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    engine.atomic_json(path / "state.json", state)


def test_real_ask_preserves_one_system_source_and_blind_reuse_after_current_draft_edit(tmp_path):
    client = OfflineBlindClient()
    flow, row = worker(tmp_path, client), sample()
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    assert flow.recipe["claim_review_protocol_version"] == 2
    assert "workflow.review_reference" in flow.recipe["node_prompt_templates"]["review"]
    assert accepted(flow._review_claim_score("sft", row, evidence, ["candidate", 0]))
    assert len(client.calls) == 2
    original_blind_key = row["claim_review"]["blind_reviews"][0]["checkpoint"]["key"]
    row["messages"][1].update(content="Check that power has been switched off.", reasoning_content="Power-off precedes inspection.")
    assert accepted(flow._review_claim_score("sft", row, evidence, ["candidate", 1]))
    assert len(client.calls) == 3 and client.calls[-1]["data"].get("phase") != "blind_reference"
    assert row["claim_review"]["blind_reviews"][0]["checkpoint"]["key"] == original_blind_key
    for call in client.calls:
        source = call["wire"][1]["content"]
        assert "untrusted_source_memory" in source
        assert json.loads(source.removeprefix("<untrusted_source_memory>\n").removesuffix("\n</untrusted_source_memory>"))["source_evidence"] == evidence
        user = json.loads(call["wire"][-1]["content"])
        assert "source_evidence" not in user and "source_evidence_ref" in user
    assert all(call["wire"][1] == client.calls[0]["wire"][1] for call in client.calls)
    for slot in ("blind_reviews", "assessments"):
        for item in row["claim_review"][slot]:
            checkpoint = item["checkpoint"]
            raw = engine.read_json(flow.path / "checkpoints" / checkpoint["stage"] / (checkpoint["checkpoint_sha256"] + ".json"))
            assert raw["data"] == item["review"]["raw_response"] and raw["sha256"] == checkpoint["response_sha256"]


def test_interrupted_real_grade_reuses_completed_blind_reference_on_resume(tmp_path):
    client = OfflineBlindClient(interrupt_at=2)
    flow, row = worker(tmp_path, client), sample()
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    with pytest.raises(RuntimeError, match="offline_blind_interrupt"):
        flow._review_claim_score("sft", row, evidence, ["sample"])
    resumed_client = OfflineBlindClient()
    resumed = engine.Workflow(flow.path.parent.parent, flow.state["id"], flow.root, generator=resumed_client)
    resumed.stage = "review"
    assert accepted(resumed._review_claim_score("sft", row, evidence, ["sample"]))
    assert len(resumed_client.calls) == 1 and resumed_client.calls[0]["data"].get("phase") != "blind_reference"


def test_protocol_catalog_keeps_v1_wire_and_new_reference_only_with_v2():
    old = snapshot_node_prompts(None, recipe_version=22, learner_context_review_version=1)
    one = snapshot_node_prompts(None, recipe_version=22, learner_context_review_version=1, claim_review_protocol_version=1)
    two = snapshot_node_prompts(None, recipe_version=22, learner_context_review_version=1, claim_review_protocol_version=2)
    assert "workflow.review_reference" not in old["review"] and "workflow.review_reference" not in one["review"]
    assert "workflow.review_reference" in two["review"]
    from lib.prompts import get, render
    assert old["review"]["workflow.review_score"] == render(get("workflow.review_score", version="1.11.0"))
    assert one["review"]["workflow.review_score"] == render(get("workflow.review_score", version="1.12.0"))
    assert two["review"]["workflow.review_score"] == render(get("workflow.review_score", version="1.13.0"))


def test_protocol_two_real_execute_repairs_conflict_and_reuses_blind_before_export(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], reasoning_required=True,
                               review_repair={"mode": "auto", "max_rounds": 1}, sft_output_style="separated")
    freeze_pre_anchor(output, run_id)
    def generate(self, unit):
        row = sample()
        row["messages"][1]["content"] = "WRONG_DRAFT: Inspect with power on."
        return [{**row, "id": unit["id"], "source_id": unit["source_id"], "source_context": deepcopy(unit), "quotes": [SOURCE]}]
    monkeypatch.setattr(engine.Workflow, "sft", generate)
    client = OfflineBlindClient(repair=True)
    flow = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client)
    result = flow.execute()
    assert result["status"] == "completed", result.get("error")
    assert len(client.calls) == 4
    assert sum(call["data"].get("phase") == "blind_reference" for call in client.calls) == 1
    record, = engine.read_json(flow.path / "artifacts/sft.records.json")
    assert record["review_repair"]["attempts"] == 1 and record["claim_review"]["version"] == 2
    assert record["review_repair"]["history"][0]["verdict"]["keep"] is False
    exported = json.loads((flow.path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    assert exported["messages"][1]["reasoning_content"] == "Inspection requires power-off."
    assert "WRONG_DRAFT" not in canonical(exported)
    no_new_calls = OfflineBlindClient()
    assert engine.Workflow(output, run_id, flow.root, generator=no_new_calls).execute(resume_run=True)["status"] == "completed"
    assert not no_new_calls.calls
