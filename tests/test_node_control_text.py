"""Node configuration reaches frozen artifacts and lazy training views unchanged."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from unittest.mock import Mock

import pytest

from lib.bootstrap.workflows import workflow_application
from lib.contexts.datasets.api import (default_control_text_config, preview_control_text,
                                      iter_training_examples)
from lib.contexts.datasets.contracts import preferred_training_record
from lib.contexts.datasets.domain.trainer_formats import to_trl_sft
from lib.contexts.workflows.domain.native_generation import native_sft_to_cot_record
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.control_protocol import PROTOCOL, VERSION, validate_request
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_control import WorkflowControl


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def test_unfinished_editor_draft_survives_but_cannot_freeze(tmp_path):
    config = default_control_text_config()
    config["rules"][0]["marker"] = ""
    draft = {"workflow-control-text:default:sft": config}
    assert validate_creation_draft(draft) == draft
    with pytest.raises(ValueError):
        workflow_application(tmp_path, tmp_path / "output").create_run(
            brief="test", targets=["sft"], node_control_text_configs={"sft": config})
    config["enabled"] = False
    assert validate_creation_draft(draft) == draft


def test_preview_counts_existing_markers_once_and_preserves_defaults():
    config = default_control_text_config()
    config["rules"][0]["replacement"] = "适当展开"
    original = "Desired oververbosity 5. [[SJ_REASONING_EFFORT]]"
    preview = preview_control_text(config, original)
    assert preview["match_count"] == 2
    assert preview["original_text"] == original
    assert preview["marked_text"] == original
    assert preview["transformed_text"] == "适当展开. [[SJ_REASONING_EFFORT]]"
    config["enabled"] = False
    preview = preview_control_text(config, original)
    assert preview["match_count"] == 0 and preview["transformed_text"] == original
    assert default_control_text_config()["enabled"] is True


def test_protocol_freeze_checkpoint_export_and_lazy_training_use_node_rules(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs",
                    ignore=shutil.ignore_patterns("*.local.*", "review_remote*"))
    source = tmp_path / "source.txt"
    source.write_text("Maintain equipment only after disconnecting its power.", encoding="utf-8")
    output = tmp_path / "output"
    configs = {node: default_control_text_config() for node in ("sft", "multiturn", "cot")}
    configs["multiturn"]["enabled"] = False
    configs["cot"]["schema"] = "shujian.control-text-adaptation/v1"
    request = validate_request({"protocol": PROTOCOL, "version": VERSION,
        "request_id": "node-controls", "action": "runs.create", "params": {"recipe": {
            "sources": [str(source)], "targets": ["sft", "multiturn", "cot"],
            "node_control_text_configs": configs}}})
    app = workflow_application(tmp_path, output)
    created = WorkflowControl(app, None, output).handle(request)
    work = engine.Workflow(output, created["run_id"], tmp_path)
    frozen = deepcopy(configs)
    configs["sft"]["enabled"] = False
    assert work.recipe["node_control_text_configs"] == frozen
    rows = {}
    for stage in ("sft", "multiturn", "cot"):
        original = {"id": stage, "status": "eligible", "messages": [
            {"role": "user", "content": "What should I check first?"},
            {"role": "assistant", "content": "Disconnect power.",
             "reasoning_content": "Desired oververbosity 5. I need to address electrical safety."}],
            "question": "What should I check first?",
            "reasoning": ["Desired oververbosity 5. I need to address electrical safety."],
            "answer": "Disconnect power."}
        before = deepcopy(original)
        action = Mock(return_value=[original])
        rows[stage] = list(work.stage_items(stage, [{"id": stage}], action))[0]
        repeated = list(work.stage_items(stage, [{"id": stage}], action))[0]
        assert action.call_count == 1 and repeated == rows[stage]
        assert original == before
        exported = json.loads(json.dumps(preferred_training_record(stage, rows[stage])))
        assert exported["control_text_config"] == frozen[stage]
        assert exported["control_text_node"] == stage
        assert "Desired oververbosity 5" in json.dumps(exported)
    assert native_sft_to_cot_record(rows["sft"])["control_text_node"] == "sft"

    class Encoder:
        def encode_turn(self, messages, control, *, tools=None, control_text_config=None):
            preview = preview_control_text(control_text_config, messages[-1]["thinking"])
            return {"text": preview["transformed_text"], "metadata": preview["receipt"]}

    exported = [preferred_training_record(stage, rows[stage]) for stage in ("sft", "multiturn", "cot")]
    before = deepcopy(exported)
    for record in exported:
        assert to_trl_sft(record)["control_text_config"] == record["control_text_config"]
    examples = list(iter_training_examples(exported, Encoder(), default_effort="high"))
    assert examples[0]["text"].startswith("Desired oververbosity 5")
    assert examples[1]["text"].startswith("Desired oververbosity 5")
    assert examples[2]["text"].startswith("[[SJ_OUTPUT_VERBOSITY:5]]")
    assert examples[0]["metadata"]["source_control_edits"] == []
    assert examples[0]["metadata"]["source_control_annotations"][0]["original"] == "Desired oververbosity 5"
    assert examples[2]["metadata"]["source_control_edits"][0]["original"] == "Desired oververbosity 5"
    assert exported == before


def test_absent_configuration_keeps_legacy_artifact_shape():
    record = {"messages": [{"role": "user", "content": "question"},
                           {"role": "assistant", "content": "answer", "reasoning_content": "reasoning"}]}
    assert set(preferred_training_record("sft", record)) == {"messages"}


def test_previous_node_configuration_can_restore_v1_without_migrating_it():
    current = default_control_text_config()
    previous = {**deepcopy(current), "schema": "shujian.control-text-adaptation/v1"}
    draft = {"workflow-control-text:default:sft": current,
             "workflow-control-text-previous:default:sft": previous}
    assert validate_creation_draft(draft) == draft
    invalid = {"workflow-control-text-previous:default:unknown-node": previous}
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft(invalid)
