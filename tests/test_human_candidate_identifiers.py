"""Stable candidate IDs are identity data; QA bodies still receive PII checks."""
from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from lib.bootstrap.workflows import human_augmentation_application
from lib.domain.human_augmentation import candidate_identifier, validate_revision_context
from lib.domain.human_session import validate_feedback, validate_issue_selection
from lib.domain.workflow_quality import text_issue
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.human_revision import verified_revision_parent


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


HASH_ID = "13793636584ee" + "a" * 51
Q = "设备检查前应该先做什么？"
A = "检查设备前必须先断电，检查完成后记录结果。"


def context(identity=HASH_ID):
    return {"session_id": "a" * 32, "round_id": "b" * 32, "parent_run_id": "c" * 32,
            "target": "sft", "candidate_id": identity, "content_sha256": "d" * 64,
            "source_id": "e" * 64, "source_kind": "document", "teacher_evidence": A,
            "messages": [{"role": "user", "content": Q}, {"role": "assistant", "content": A}],
            "instruction": "保留安全前提。", "depth": 1,
            "ancestors": [{"run_id": "c" * 32, "candidate_id": identity}]}


@pytest.mark.parametrize("identity", [HASH_ID, "用户手册.md#章节:二/样本-甲", "  exact candidate  "])
def test_opaque_identity_is_preserved_across_selection_and_revision_validation(identity):
    selection = {"round_id": "b" * 32, "target": "sft", "candidate_id": identity}
    assert validate_issue_selection([selection])[0]["candidate_id"] == identity
    raw = context(identity)
    original = deepcopy(raw)
    normalized = validate_revision_context(raw, review=True)
    assert normalized["candidate_id"] == identity and normalized["ancestors"][0]["candidate_id"] == identity
    assert raw == original


def test_regression_hash_resembles_pii_but_is_not_training_text():
    assert len(HASH_ID) == 64 and text_issue(HASH_ID) == "potential_personal_data"
    assert candidate_identifier(HASH_ID) == HASH_ID


@pytest.mark.parametrize("identity", ["", " ", "x" * 501, "id\x00hidden", "id\nhidden", "id\rhidden",
                                      "id\thidden", "id\x7fhidden", "id\u200bhidden", None])
def test_invalid_candidate_identifiers_are_rejected_without_coercion(identity):
    with pytest.raises(ValueError):
        candidate_identifier(identity)
    with pytest.raises(ValueError):
        validate_issue_selection([{"round_id": "b" * 32, "target": "sft", "candidate_id": identity}])
    with pytest.raises(ValueError):
        validate_revision_context(context(identity), review=True)


@pytest.mark.parametrize("field", ["question", "answer", "instruction"])
def test_phone_number_in_editable_training_or_feedback_body_still_rejected(field):
    value = "请联系手机号13793636584办理。"
    with pytest.raises(ValueError, match="invalid_human_session_text"):
        validate_feedback(**{field: value})
    with pytest.raises(ValueError, match="invalid_human_session_text"):
        validate_issue_selection([{"round_id": "b" * 32, "target": "sft", "candidate_id": HASH_ID,
                                  field: value}])


def test_source_hash_session_id_and_parent_identity_checks_remain_strict():
    for field, malformed in (("source_id", "not-a-source-hash"), ("session_id", "user-defined-session"),
                             ("content_sha256", "short")):
        raw = context()
        raw[field] = malformed
        with pytest.raises(ValueError, match="invalid_human_revision_context"):
            validate_revision_context(raw, review=True)
    raw = context()
    raw["ancestors"][-1]["candidate_id"] += "changed"
    with pytest.raises(ValueError, match="invalid_human_revision_context"):
        validate_revision_context(raw, review=True)


def test_save_issues_feedback_and_repair_child_keep_the_original_hash_id(tmp_path, monkeypatch):
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs", dirs_exist_ok=True)
    source = tmp_path / "manual.txt"
    source.write_text(A, encoding="utf-8")
    output = tmp_path / "out"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], settings_root=tmp_path)
    def imported(self, unit):
        return [{"id": HASH_ID, "source_id": unit["source_id"], "status": "eligible", "evidence_level": "document_quote",
                 "source_context": deepcopy(unit), "messages": [{"role": "user", "content": Q},
                    {"role": "assistant", "content": A}], "reasoning_origin": "source", "quotes": [unit["text"]]}]
    monkeypatch.setattr(engine.Workflow, "sft", imported)
    class NeverCalled:
        usage = {}
        def chat(self, *args, **kwargs):
            pytest.fail("Preparing an issue revision must not call a model")
    initial = engine.Workflow(output, run_id, tmp_path, generator=NeverCalled()).execute()
    assert initial["status"] == "completed", initial.get("error")
    app = human_augmentation_application(tmp_path, output)
    session_id = app.create_session(from_run_id=run_id, review_only=True)
    app.configure_review_models(session_id, {"generation": {"backend": "ollama", "model": "offline-rework"}},
                                expected_version=app.session(session_id)["version"])
    candidate = app.results(session_id)["rows"][0]
    assert candidate["candidate_id"] == HASH_ID
    parent = app.session(session_id)["rounds"][0]
    feedback = app.save_feedback(session_id, parent["id"], "sft", HASH_ID, instruction="补充安全提醒。",
                                 expected_version=app.session(session_id)["version"])
    assert app.session(session_id)["feedback"][-1]["candidate_id"] == HASH_ID
    saved = app.save_issues(session_id, [{"round_id": parent["id"], "target": "sft", "candidate_id": HASH_ID}],
                            instruction="补充安全提醒。", expected_version=feedback["version"])
    child = app.revise_issues(session_id, request_id="opaque-hash-regression", expected_version=saved["version"])
    recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    item = recipe["repair_inputs"][0]
    revision = item["revision_context"]
    assert item["record"]["id"] == revision["candidate_id"] == HASH_ID
    assert revision["ancestors"][-1]["candidate_id"] == HASH_ID
    assert verified_revision_parent(output, child["run_id"], revision)["id"] == HASH_ID
    assert engine.read_json(engine.run_path(output, run_id) / "artifacts/sft.records.json")[0]["id"] == HASH_ID
