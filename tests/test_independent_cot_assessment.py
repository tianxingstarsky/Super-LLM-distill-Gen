"""Independent CoT assessment and node-snapshot resumes keep truthful provenance."""
from copy import deepcopy
import json

import pytest

from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from tests.test_review_repair_engine import create_child
from tests.test_workflow_styled_generation import run, records, Generator, Reviewer, ANSWER


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # All responses are injected; do not start the global TCP mock server.
    yield


class NoCalls:
    model = "unused-offline-assessment"
    usage = {}

    def chat(self, *_args, **_kwargs):
        raise AssertionError("This operation must not invoke any model.")


@pytest.mark.parametrize("version", [9, 21])
@pytest.mark.parametrize("metadata", [None, {"not_a_prompt": {"version": "obsolete", "sha256": "wrong"}}])
def test_node_snapshot_resume_does_not_require_or_resolve_historical_metadata(tmp_path, monkeypatch, version, metadata):
    source = tmp_path / "document.txt"
    text = "Disconnect the power supply before inspection. Check the cables and record the result after inspection."
    source.write_text(text, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"])
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    recipe["version"] = version
    recipe["node_prompt_templates"] = snapshot_node_prompts(recipe["node_prompt_templates"], recipe_version=version)
    for key in ("agent_experience_version", "agent_experience", "authored_role_projection_version"):
        recipe.pop(key)
    if metadata is None:
        recipe.pop("prompts")
    else:
        recipe["prompts"] = metadata
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state.update(recipe_hash=engine.digest(recipe), status="cancelled")
    atomic_json(path / "state.json", state)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Pinned node snapshots must not read the historical prompt catalog.")

    monkeypatch.setattr(engine.Workflow, "historical_prompt", forbidden)
    result = engine.Workflow(output, run_id, tmp_path, generator=NoCalls(), jev=NoCalls()).execute(resume_run=True)
    assert result["status"] == "completed", result.get("error")
    assert result["usage"] == {}
    assert records(path, "cpt")[0]["text"] == text
    assert engine.verify_artifacts(path)["counts"] == {"cpt": 1}


HUMAN_DESIGN = {"enabled": True, "human_augmentation": {"enabled": True,
    "seeds": [{"question": "设备应如何维护？", "answer": ANSWER}]}}
STYLE = {"cot": {"enabled": True, "style": "structured"}}


@pytest.mark.parametrize("review", [None, {"mode": "auto", "max_rounds": 0}])
def test_final_ai_assessment_releases_imported_independent_cot_in_human_design_mode(tmp_path, review):
    output, run_id, path = run(tmp_path, targets=["cot"], conversation=True,
        node_generation=STYLE, qa_director=HUMAN_DESIGN, review_repair=review)
    writer, reviewer = Generator(), Reviewer()
    # The unified review's generation role uses an offline scoring client.
    if review:
        original = writer.chat
        def generate_or_score(messages, **kwargs):
            if "source_evidence" in json.dumps(messages, ensure_ascii=False):
                return reviewer.chat(messages, **kwargs)
            return original(messages, **kwargs)
        writer.chat = generate_or_score
    workflow = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=reviewer)
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    row = records(path, "cot")[0]
    assert row["judge"]["keep"] is True and row["status"] == "eligible"
    assert row["evidence_level"] == "recorded_context_model_assessed"
    assert row["source_verification"] == "recorded_not_independently_verified"
    assert "human_augmentation_status" not in row  # The old answer was replaced.
    assert workflow.human_package_issue(row, "cot") is None
    assert engine.verify_artifacts(path)["counts"] == {"cot": 1}


def test_waiting_manual_assessment_stays_unassessed_and_accepted_manual_rework_is_not_ai_scored(tmp_path):
    output, run_id, path = run(tmp_path, targets=["cot"], conversation=True,
        node_generation=STYLE, qa_director=HUMAN_DESIGN, review_repair={"mode": "human"})
    workflow = engine.Workflow(output, run_id, tmp_path, generator=Generator(), jev=NoCalls())
    state = workflow.execute()
    assert state["status"] == "needs_attention", state.get("error")
    row = records(path, "cot")[0]
    assert row["evidence_level"] == "recorded_context_unassessed"
    assert row["review_repair"]["status"] == "waiting_manual_review"
    assert engine.verify_artifacts(path)["counts"] == {"cot": 0}
    child_id = create_child(output, run_id, row, target="cot", mode="human", max_rounds=0,
                           create_options={"node_generation": STYLE}, score=.95, approved=True)
    child = engine.Workflow(output, child_id, tmp_path, generator=NoCalls(), jev=NoCalls())
    result = child.execute()
    assert result["status"] == "completed", result.get("error")
    assessed = records(child.path, "cot")[0]
    assert assessed["evidence_level"] == "recorded_context_human_assessed"
    assert assessed["source_verification"] == "recorded_not_independently_verified"
    assert assessed["review_repair"]["mode"] == "human" and assessed["review_repair"]["status"] == "accepted"
    assert "judge" not in assessed and "human_augmentation_status" not in assessed
    assert child.human_package_issue(assessed, "cot") is None and result["usage"] == {}


@pytest.mark.parametrize("marker,level", [(None, "recorded_context_unassessed"),
                                        (1, "source_and_model_assessed"), (1, "document_quote")])
def test_assessment_metadata_does_not_upgrade_old_recipes_or_other_source_classes(marker, level):
    workflow = object.__new__(engine.Workflow)
    workflow.recipe = {} if marker is None else {"independent_cot_input_version": marker}
    row = {"evidence_level": level, "source_context": {"kind": "conversation"}}
    original = deepcopy(row)
    assert workflow.independent_cot_assessment(row) == {}
    assert workflow.independent_cot_assessment(row, human=True) == {}
    assert row == original
