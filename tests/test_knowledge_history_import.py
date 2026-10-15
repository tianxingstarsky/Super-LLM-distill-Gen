"""Actual sealed parents, exact knowledge identity and no summary trust."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from lib.domain.source_line_ranges import number_source_material
from lib.domain.workflow_quality import canonical
from lib.infrastructure.knowledge_agent_session import knowledge_identity
from lib.infrastructure.knowledge_history_import import verified_knowledge_history
from lib.infrastructure.workflow_qa_director import learner_prompt
from lib.infrastructure.workflow_source_context import _pack_payload


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def make_pack(run, text="Only knowledge A.\r\nExact source facts.", identity="material-a"):
    item = number_source_material({"unit_id": identity, "source_id": "shared-original-PDF",
        "source_name": "paper.pdf", "text": text, "cleaning_status": "accepted",
        "cleaned_text_sha256": sha(canonical(text))})
    pack = _pack_payload([item])
    path = run / "source-context/packs" / (pack["id"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(pack), encoding="utf-8")
    reference = {key: pack[key] for key in ("version", "id", "prefix_sha256")}
    reference.update(relative_path=f"source-context/packs/{pack['id']}.json", source_line_protocol_version=1)
    return reference, knowledge_identity([item])


def record(reference, identity="candidate", question="What can be concluded?", visible="", target="sft"):
    contract = {"question": question, "visible_context": visible,
                "qa_type": "grounded" if visible else "closed_book"}
    prompt = [{"role": "user", "content": learner_prompt(contract)}]
    result = {"id": identity, "status": "eligible", "qa_contract": contract,
              "messages": [*prompt, {"role": "assistant", "content": "PRIVATE OLD ANSWER"}],
              "source_id": "shared-original-PDF", "source_context": {"source_context": {
                  "source_context_pack": reference}}}
    if target == "cot":
        result.update(question=deepcopy(prompt), reasoning=["PRIVATE REASONING"], answer="PRIVATE OLD ANSWER")
    elif target in {"dpo", "orpo", "rlaif"}:
        result["prompt"] = deepcopy(prompt)
    return result


def seal(run, records):
    artifacts = run / "artifacts"
    artifacts.mkdir(exist_ok=True)
    hashes = {}
    for target, rows in records.items():
        path = artifacts / f"{target}.records.json"
        path.write_text(canonical(rows), encoding="utf-8")
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (artifacts / "manifest.json").write_text(canonical({"status": "complete", "run_id": run.name,
        "sha256": hashes, "counts": {target: sum(row.get('status') == 'eligible' for row in rows)
                                       for target, rows in records.items()}}), encoding="utf-8")


def example(run, identity="candidate", namespace="sft"):
    return {"id": run.name + ":" + identity, "run_id": run.name, "namespace": namespace,
            "question": "FORGED OR TRUNCATED SUMMARY", "visible_context": "FORGED CONTEXT",
            "qa_type": "multi_source", "answer": "PRIVATE SUMMARY ANSWER", "source_id": "FORGED SOURCE"}


@pytest.fixture
def parent(tmp_path):
    run = tmp_path / "workflows" / ("a" * 32)
    run.mkdir(parents=True)
    reference, knowledge = make_pack(run)
    return tmp_path, run, reference, knowledge


def test_summary_is_only_locator_full_question_and_context_come_from_actual_record(parent):
    output, run, reference, knowledge = parent
    question = "Original full question " * 100
    row = record(reference, question=question, visible="Actual learner-visible article")
    seal(run, {"sft": [row]})
    before = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    results = verified_knowledge_history(output, [example(run)], knowledge)
    assert len(results) == 1
    result = results[0]
    assert result["question"] == question and result["visible_context"] == "Actual learner-visible article"
    assert result["qa_type"] == "grounded" and result["status"] == "final_eligible"
    assert result["id"] == run.name + ":candidate" and result["candidate_id"] == "candidate"
    assert result["knowledge_id"] == knowledge and result["history_is_not_evidence"] is True
    assert result["verification"]["record_sha256"] == sha(canonical(row))
    assert "PRIVATE" not in canonical(result) and "Only knowledge" not in canonical(result)
    after = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    assert after == before and not (run / "human-review/.indexes").exists()


def test_same_pdf_but_different_clean_material_does_not_migrate(parent):
    output, run, reference, knowledge = parent
    other, _ = make_pack(run, "Different chapter B of exactly the same PDF", "material-b")
    seal(run, {"sft": [record(other)]})
    assert verified_knowledge_history(output, [example(run)], knowledge) == []


@pytest.mark.parametrize("target", ["cot", "dpo", "orpo", "rlaif"])
def test_sft_history_namespace_can_authenticate_other_real_target_artifacts(parent, target):
    output, run, reference, knowledge = parent
    seal(run, {target: [record(reference, target=target)]})
    result = verified_knowledge_history(output, [example(run)], knowledge)
    assert len(result) == 1 and result[0]["stage"] == target and result[0]["namespace"] == "sft"


def test_multiturn_namespace_does_not_lookup_an_sft_artifact(parent):
    output, run, reference, knowledge = parent
    seal(run, {"sft": [record(reference)]})
    assert verified_knowledge_history(output, [example(run, namespace="multiturn")], knowledge) == []
    seal(run, {"sft": [record(reference)], "multiturn": [record(reference)]})
    result = verified_knowledge_history(output, [example(run, namespace="multiturn")], knowledge)
    assert result[0]["namespace"] == result[0]["stage"] == "multiturn"


def test_imported_multiturn_identity_equals_native_design_and_does_not_count_twice(parent):
    from lib.infrastructure.knowledge_agent_session import KnowledgeAgentSession
    from lib.infrastructure.workflow_knowledge_agent import WorkflowKnowledgeAgent
    output, run, reference, knowledge = parent
    row = record(reference)
    row["qa_contract"]["dialogue_design"] = {
        "interaction_goal": " Solve this task together. ", "user_intent": "Understand the condition",
        "context_links": ["Keep the actual preceding constraint"],
        "conversational_frame": {"speaker_role": "learner", "register": "natural"}}
    seal(run, {"multiturn": [row]})
    imported = verified_knowledge_history(output, [example(run, namespace="multiturn")], knowledge)[0]
    identity = WorkflowKnowledgeAgent._knowledge_task_identity(row["qa_contract"], "multiturn")
    assert imported["dialogue_design"] == row["qa_contract"]["dialogue_design"]
    assert imported["task_identity"] == identity
    assert WorkflowKnowledgeAgent._knowledge_task_identity(imported, "multiturn") == identity
    store = KnowledgeAgentSession(output / "session.sqlite3")
    assert store.claim(knowledge, identity, run_id=run.name, sample_id="candidate",
        question=imported["question"], qa_type=imported["qa_type"]) is None
    store.outcome(knowledge, identity, run_id=run.name, sample_id="candidate",
                  stage="multiturn", status="final_eligible")
    duplicate = store.claim(knowledge, WorkflowKnowledgeAgent._knowledge_task_identity(imported, "multiturn"),
        run_id="b" * 32, sample_id="second", question=imported["question"], qa_type=imported["qa_type"])
    assert duplicate["id"] == "candidate"
    assert store.coverage(knowledge)["counts"] == [{"qa_type": "closed_book", "status": "final_eligible", "count": 1}]


def test_different_multiturn_design_with_same_opening_does_not_false_reject(parent):
    from lib.infrastructure.knowledge_agent_session import KnowledgeAgentSession
    from lib.infrastructure.workflow_knowledge_agent import WorkflowKnowledgeAgent
    output, run, reference, knowledge = parent
    row = record(reference)
    row["qa_contract"]["dialogue_design"] = {"interaction_goal": "Diagnose an unclear symptom"}
    seal(run, {"multiturn": [row]})
    imported = verified_knowledge_history(output, [example(run, namespace="multiturn")], knowledge)[0]
    other = {**deepcopy(row["qa_contract"]), "dialogue_design": {"interaction_goal": "Compare two repair options"}}
    first_id = WorkflowKnowledgeAgent._knowledge_task_identity(imported, "multiturn")
    second_id = WorkflowKnowledgeAgent._knowledge_task_identity(other, "multiturn")
    assert first_id != second_id
    store = KnowledgeAgentSession(output / "session.sqlite3")
    assert store.claim(knowledge, first_id, run_id=run.name, sample_id="first",
        question=imported["question"], qa_type=imported["qa_type"]) is None
    assert store.claim(knowledge, second_id, run_id="b" * 32, sample_id="second",
        question=other["question"], qa_type=other["qa_type"]) is None


def test_history_design_is_a_strict_procedural_whitelist_not_an_answer_channel(parent):
    output, run, reference, knowledge = parent
    row = record(reference)
    row["qa_contract"]["dialogue_design"] = {"interaction_goal": "Use the source", "answer": "FORBIDDEN"}
    seal(run, {"multiturn": [row]})
    assert verified_knowledge_history(output, [example(run, namespace="multiturn")], knowledge) == []


@pytest.mark.parametrize("placement", ["revision_context", "human_seed"])
def test_revision_identity_matches_native_without_returning_private_lineage(parent, placement):
    from lib.infrastructure.knowledge_agent_session import KnowledgeAgentSession, knowledge_task_identity
    output, run, reference, knowledge = parent
    row = record(reference)
    revision = {"parent_run_id": "b" * 32, "parent_id": "parent-row", "depth": 2,
                "editorial_feedback": "PRIVATE FEEDBACK", "old_answer": "PRIVATE OLD ANSWER"}
    if placement == "revision_context":
        row["qa_contract"]["revision_context"] = revision
    else:
        row["qa_contract"]["human_design"] = {"seed": {"revision_context": revision}}
    seal(run, {"sft": [row]})
    imported = verified_knowledge_history(output, [example(run)], knowledge)[0]
    identity = knowledge_task_identity(row["qa_contract"], "sft")
    assert imported["task_identity"] == identity
    assert identity != knowledge_task_identity({key: row["qa_contract"][key]
        for key in ("question", "visible_context", "qa_type")}, "sft")
    assert "revision_context" not in imported and "human_design" not in imported
    assert "PRIVATE" not in canonical(imported)
    store = KnowledgeAgentSession(output / "session.sqlite3")
    arguments = dict(run_id=run.name, sample_id="candidate", question=imported["question"],
                     qa_type=imported["qa_type"])
    assert store.claim(knowledge, identity, **arguments) is None
    store.outcome(knowledge, identity, run_id=run.name, sample_id="candidate",
                  stage="sft", status="final_eligible")
    assert store.claim(knowledge, imported["task_identity"], **arguments) is None
    assert store.coverage(knowledge)["counts"] == [
        {"qa_type": "closed_book", "status": "final_eligible", "count": 1}]


def test_cross_target_same_question_with_conflicting_revision_identity_is_not_imported(parent):
    output, run, reference, knowledge = parent
    sft, cot = record(reference), record(reference, target="cot")
    cot["qa_contract"]["revision_context"] = {"parent_id": "different-authenticated-revision"}
    seal(run, {"sft": [sft], "cot": [cot]})
    assert verified_knowledge_history(output, [example(run)], knowledge) == []


def test_shared_sft_cot_projection_is_one_identity_and_run_is_verified_once(parent, monkeypatch):
    from lib.infrastructure import training_workflow as engine
    output, run, reference, knowledge = parent
    seal(run, {"sft": [record(reference)], "cot": [record(reference, target="cot")]})
    calls, original = [], engine.verify_artifacts
    def verify(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(engine, "verify_artifacts", verify)
    result = verified_knowledge_history(output, [example(run), example(run)], knowledge)
    assert len(result) == len(calls) == 1


def test_conflicting_cross_target_id_is_not_imported(parent):
    output, run, reference, knowledge = parent
    seal(run, {"sft": [record(reference)], "cot": [record(reference, question="Different actual task", target="cot")]})
    assert verified_knowledge_history(output, [example(run)], knowledge) == []


def test_duplicate_sealed_id_even_at_end_of_same_file_is_ambiguous(parent):
    output, run, reference, knowledge = parent
    seal(run, {"sft": [record(reference), record(reference)]})
    assert verified_knowledge_history(output, [example(run)], knowledge) == []


@pytest.mark.parametrize("variant", ["no_pack", "missing_pack_cache", "no_contract", "rejected", "changed_user"])
def test_missing_legacy_evidence_and_nonpublished_or_changed_input_are_not_guessed(parent, variant):
    output, run, reference, knowledge = parent
    row = record(reference)
    if variant == "no_pack":
        row.pop("source_context")
    elif variant == "missing_pack_cache":
        (run / reference["relative_path"]).unlink()
    elif variant == "no_contract":
        row.pop("qa_contract")
    elif variant == "rejected":
        row["status"] = "quarantined"
    else:
        row["messages"][0]["content"] = "Changed actual user question"
    seal(run, {"sft": [row]})
    assert verified_knowledge_history(output, [example(run)], knowledge) == []


def test_matching_old_ancestor_cannot_override_a_different_current_pack(parent):
    output, run, reference, knowledge = parent
    other, _ = make_pack(run, "A different current evidence pack", "material-b")
    row = record(reference)
    row["source_context"]["source_context_pack"] = other
    seal(run, {"sft": [row]})
    assert verified_knowledge_history(output, [example(run)], knowledge) == []


def test_changed_complete_artifact_is_not_silently_migrated(parent):
    output, run, reference, knowledge = parent
    seal(run, {"sft": [record(reference)]})
    path = run / "artifacts/sft.records.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="knowledge_history_artifact_integrity_error"):
        verified_knowledge_history(output, [example(run)], knowledge)


def test_changed_source_pack_is_not_silently_trusted(parent):
    output, run, reference, knowledge = parent
    seal(run, {"sft": [record(reference)]})
    path = run / reference["relative_path"]
    pack = json.loads(path.read_text(encoding="utf-8"))
    pack["materials"][0]["text"] += " forged new facts"
    path.write_text(canonical(pack), encoding="utf-8")
    with pytest.raises(ValueError, match="knowledge_history_.*integrity_error"):
        verified_knowledge_history(output, [example(run)], knowledge)


def test_malformed_tail_after_wanted_record_is_still_rejected(parent):
    output, run, reference, knowledge = parent
    seal(run, {"sft": [record(reference)]})
    path = run / "artifacts/sft.records.json"
    path.write_text(path.read_text(encoding="utf-8") + " TRAILING JUNK", encoding="utf-8")
    manifest_path = run / "artifacts/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="knowledge_history_record_integrity_error"):
        verified_knowledge_history(output, [example(run)], knowledge)


def test_id_path_namespace_and_missing_runs_never_expand_lookup_scope(parent):
    output, run, reference, knowledge = parent
    seal(run, {"sft": [record(reference)]})
    values = [example(run), {**example(run), "run_id": "../../outside"},
              {**example(run), "id": "b" * 32 + ":candidate"},
              {**example(run), "namespace": []},
              {**example(run), "namespace": "../../sft"},
              {"run_id": "b" * 32, "id": "b" * 32 + ":candidate", "namespace": "sft"}]
    assert len(verified_knowledge_history(output, values, knowledge)) == 1


def test_limit_is_twenty_real_references_with_one_stream_per_target(parent, monkeypatch):
    import lib.infrastructure.knowledge_history_import as module
    output, run, reference, knowledge = parent
    rows = [record(reference, identity=f"row-{i}", question=f"Actual task {i}?") for i in range(21)]
    seal(run, {"sft": rows})
    calls, original = [], module.iter_json_records
    def stream(path, **kwargs):
        calls.append(path)
        return original(path, **kwargs)
    monkeypatch.setattr(module, "iter_json_records", stream)
    result = verified_knowledge_history(output, [example(run, identity=f"row-{i}") for i in range(21)], knowledge)
    assert len(result) == 20 and len(calls) == 1
    assert [row["question"] for row in result] == [f"Actual task {i}?" for i in range(20)]
