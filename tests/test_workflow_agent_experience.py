"""Durable expert instructions reach the proper role without becoming facts."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.domain.agent_experience import default_experience_text, snapshot_agent_experience
from lib.domain.creation_draft import validate_creation_draft
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_agent_experience import experience_system
from lib.infrastructure.prompt_library_sqlite import PromptLibrarySQLite
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def test_experience_library_is_user_scoped_and_preserves_literal_edits(tmp_path):
    alice = PromptLibrarySQLite(root=tmp_path, user_key="alice")
    bob = PromptLibrarySQLite(root=tmp_path, user_key="bob")
    payload = {"enabled": True, "text": '先核实实际角色。保留 {literal} 与 {"x":1}。\n'}
    saved = alice.save_template("experience:answer", "My approach", payload)
    assert alice.get_template(saved["id"], "experience:answer")["payload"] == payload
    assert bob.list_templates("experience:answer") == []
    with pytest.raises(ValueError, match="not_found"):
        bob.get_template(saved["id"], "experience:answer")
    with pytest.raises(ValueError, match="invalid_payload"):
        alice.save_template("experience:answer", "Bad", {"text": "x"})


def test_invalid_experience_is_rejected_before_creating_any_job(tmp_path):
    driver = Mock()
    invalid = {"answer": {"enabled": True, "text": ""}}
    with pytest.raises(ValueError):
        WorkflowApplication(driver).create_run(agent_experience=invalid)
    driver.create.assert_not_called()
    with pytest.raises(ValueError):
        engine.create_run(tmp_path / "output", brief="业务资料", targets=["sft"], agent_experience=invalid)
    assert not (tmp_path / "output").exists()


def test_new_run_pins_experience_and_reuse_copies_the_exact_body(tmp_path):
    text = "用户表面问句未必表达全部意图。只依据实际角色前文判断省略成分。" * 30
    output = tmp_path / "output"
    rid = engine.create_run(output, brief="设备维护业务", targets=["sft"],
                            agent_experience={"answer": {"enabled": True, "text": text}})
    recipe = json.loads((engine.run_path(output, rid) / "recipe.json").read_text(encoding="utf-8"))
    assert recipe["agent_experience_version"] == 1
    assert recipe["agent_experience"]["scopes"]["answer"]["text"] == text
    assert recipe["authored_role_projection_version"] == 3
    before = deepcopy(recipe)
    draft = recipe_to_draft(recipe, "Reuse", "fixture", [])["values"]
    assert recipe == before
    assert draft["workflow-agent-experience-text:fixture:answer"] == text
    assert draft["workflow-agent-experience-enabled:fixture:answer"] is True
    validate_creation_draft(draft)


def test_each_role_gets_only_its_own_guidance_and_legacy_run_gets_none():
    recipe = {"agent_experience_version": 1, "agent_experience": snapshot_agent_experience({
        "answer": {"enabled": True, "text": "ANSWER_ONLY_SENTINEL"},
        "reasoning": {"enabled": True, "text": "RATIONALE_ONLY_SENTINEL"},
        "review": {"enabled": False, "text": "REVIEW_DISABLED"}})}
    answer = experience_system(recipe, "workflow.cot_native_answer")
    assert "ANSWER_ONLY_SENTINEL" in answer and "RATIONALE_ONLY_SENTINEL" not in answer
    assert "RATIONALE_ONLY_SENTINEL" in experience_system(recipe, "workflow.cot_rationale")
    assert experience_system(recipe, "workflow.rationale_check") == ""
    assert experience_system({}, "workflow.cot_rationale") == ""
    assert experience_system(recipe, "unsupported.prompt") == ""


def test_actual_model_request_puts_experience_in_system_not_task(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("维护前先断电。", encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=["sft"],
                            agent_experience={"answer": {"enabled": True, "text": "EXPERIENCE_LITERAL_{unchanged}"}})
    writer = Mock(model="offline", usage={"calls": 0}, source_memory_transport="tool")
    writer.chat.return_value = '{"answer":"先断电。"}'
    workflow = engine.Workflow(output, rid, tmp_path, generator=writer)
    workflow.stage = "sft"
    workflow.ask(["capture"], "generation", "workflow.sft", {"text": "维护前先断电。"})
    messages = writer.chat.call_args.args[0]
    assert "EXPERIENCE_LITERAL_{unchanged}" in messages[0]["content"]
    assert all("EXPERIENCE_LITERAL" not in str(message) for message in messages[1:])


@pytest.mark.parametrize("prompt_id", ["workflow.review_reference", "workflow.learner_context_check", "workflow.review_score"])
def test_review_experience_scope_marker_changes_only_newly_mapped_real_system_prompts(tmp_path, monkeypatch, prompt_id):
    output = tmp_path / "output"
    rid = engine.create_run(output, brief="设备检查", targets=["sft"],
        review_repair={"mode": "auto", "max_rounds": 0},
        agent_experience={"review": {"enabled": True, "text": "REVIEW_SCOPE_LITERAL_{unchanged}_883"}})
    writer = Mock(model="offline", usage={"calls": 0}, source_memory_transport="tool")
    writer.chat.return_value = '{"ok":true}'
    workflow = engine.Workflow(output, rid, tmp_path, generator=writer)
    workflow.stage = "review"
    assert workflow.recipe["claim_review_experience_scope_version"] == 1
    workflow.ask(["new", prompt_id], "generation", prompt_id, {"case": "fixed_task"}, prompt_stage="review")
    new_wire = deepcopy(writer.chat.call_args.args[0])
    added = experience_system(workflow.recipe, prompt_id)
    workflow.recipe.pop("claim_review_experience_scope_version")
    workflow.ask(["frozen_old", prompt_id], "generation", prompt_id, {"case": "fixed_task"}, prompt_stage="review")
    old_wire = deepcopy(writer.chat.call_args.args[0])
    if prompt_id == "workflow.review_score":
        assert new_wire == old_wire
        assert "REVIEW_SCOPE_LITERAL_{unchanged}_883" in old_wire[0]["content"]
    else:
        assert new_wire[0]["content"] == old_wire[0]["content"] + added
        assert "REVIEW_SCOPE_LITERAL_{unchanged}_883" not in old_wire[0]["content"]
        # Replay the prior mapping itself and compare full actual request bytes.
        import lib.infrastructure.workflow_agent_experience as adapter
        previous_scope = adapter.experience_scope
        monkeypatch.setattr(adapter, "experience_scope", lambda value: None if value == prompt_id else previous_scope(value))
        workflow.ask(["old_mapping", prompt_id], "generation", prompt_id, {"case": "fixed_task"}, prompt_stage="review")
        assert writer.chat.call_args.args[0] == old_wire
    assert all("REVIEW_SCOPE_LITERAL" not in str(message) for message in new_wire[1:])
    assert new_wire[1:] == old_wire[1:]


@pytest.mark.parametrize("value", [True, False, 0, 2, "1", []])
def test_invalid_review_experience_scope_marker_never_calls_a_model(tmp_path, value):
    output = tmp_path / "output"
    rid = engine.create_run(output, brief="设备检查", targets=["sft"], review_repair={"mode": "auto", "max_rounds": 0})
    writer = Mock(model="offline", usage={"calls": 0})
    workflow = engine.Workflow(output, rid, tmp_path, generator=writer)
    workflow.stage = "review"
    workflow.recipe["claim_review_experience_scope_version"] = value
    with pytest.raises(ValueError, match="invalid_claim_review_experience_scope_version"):
        workflow.ask(["invalid"], "generation", "workflow.review_reference", {}, prompt_stage="review")
    writer.chat.assert_not_called()


def test_draft_allows_partial_experience_edits_but_rejects_wrong_scopes():
    values = {"workflow-agent-experience-text:fixture:director": "",
              "workflow-agent-experience-enabled:fixture:director": False}
    assert validate_creation_draft(values) == values
    with pytest.raises(ValueError):
        validate_creation_draft({"workflow-agent-experience-text:fixture:secret": "unknown"})


def test_human_session_blueprint_keeps_native_phase_edits_and_experience(tmp_path):
    from lib.bootstrap.workflows import human_augmentation_application
    application = human_augmentation_application(tmp_path, tmp_path / "output")
    custom = "Return a native final reply. Preserve {literal}."
    identifier = application.create_session(targets=["cot"], brief="业务对话设计",
        node_generation={"cot": {"enabled": True, "style": "default", "answer_mode": "native_prefill"}},
        node_prompts={"cot": {"workflow.cot_native_answer": custom}},
        agent_experience={"answer": {"enabled": True, "text": "Use the real dialogue."}})
    blueprint = json.loads((tmp_path / "output" / "human-sessions" / identifier / "blueprint.json").read_text(encoding="utf-8"))
    assert blueprint["node_prompts"]["cot"]["workflow.cot_native_answer"] == custom
    assert blueprint["agent_experience"]["answer"]["text"] == "Use the real dialogue."
