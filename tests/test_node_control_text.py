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
    assert preview["marked_text"] == ("<reason-strength>Desired oververbosity 5</reason-strength>. "
                                       "<reason-strength>[[SJ_REASONING_EFFORT]]</reason-strength>")
    assert preview["transformed_text"] == "适当展开. <reason-strength>[[SJ_REASONING_EFFORT]]</reason-strength>"
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
    configs["cot"].pop("annotation_tag")
    configs["cot"].pop("prefix_char_limit")
    configs["cot"].pop("review_suggestions")
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
    assert examples[0]["text"].startswith("<reason-strength>Desired oververbosity 5</reason-strength>")
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


def test_directed_pending_native_response_keeps_frozen_owner_on_replay(tmp_path):
    from io import StringIO
    from threading import Event
    from lib.infrastructure.workflow_qa_director import WorkflowQADirector
    from lib.contexts.review.domain.control_text_suggestions import build_control_text_review_context
    fixture = json.loads((Path(__file__).parent / "fixtures/native_sft_control_owner.json").read_text(encoding="utf-8"))
    original = fixture["record"]
    config = default_control_text_config()

    class Replay(WorkflowQADirector):
        recipe = {"concurrency": 1, "node_control_text_configs": {"sft": config}}
        path = tmp_path

        def __init__(self):
            self._abort, self.cached = Event(), None
            self.state = {"id": "offline-replay", "stages": {"sft": {key: 0 for key in
                ("done", "outputs", "eligible", "quarantined", "cached", "batches_done")}}}

        def activate_directed_stage(self, stage): pass
        def check_cancel(self): pass
        def event(self, *args, **kwargs): pass
        def save(self): pass
        def checkpoint_has(self, key, *, stage): return self.cached is not None
        def checkpoint(self, key, operation):
            if self.cached is None:
                self.cached = deepcopy(operation())
            return deepcopy(self.cached)
        def sft(self, unit): return [deepcopy(original)]

    worker = Replay()
    unit = {"id": original["id"], "source_id": original["source_id"], "kind": "document"}
    for _ in range(2):
        output = StringIO()
        worker._directed_worker_batch("sft", [unit], 0, output, Mock(), Mock())
        record = json.loads(output.getvalue())
        assert record == {**original, "control_text_node": "sft", "control_text_config": config}
        assert record["status"] == "quarantined" and record["reason"] == "native_instruction_reference_unresolved"
        context = build_control_text_review_context("sft", record, {"messages": record["messages"]})
        assert context["sources"][0]["node"] == "sft"
        assert context["sources"][0]["prefix"] == original["messages"][-1]["reasoning_content"][:512]
    assert worker.state["stages"]["sft"]["cached"] == 1
    assert worker.cached == [original]


def test_previous_node_configuration_can_restore_v1_without_migrating_it():
    current = default_control_text_config()
    previous = {**deepcopy(current), "schema": "shujian.control-text-adaptation/v1"}
    previous.pop("annotation_tag")
    previous.pop("prefix_char_limit")
    previous.pop("review_suggestions")
    draft = {"workflow-control-text:default:sft": current,
             "workflow-control-text-previous:default:sft": previous}
    assert validate_creation_draft(draft) == draft
    invalid = {"workflow-control-text-previous:default:unknown-node": previous}
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft(invalid)


def test_inline_exports_preserve_author_text_and_each_turns_own_rules():
    config = default_control_text_config()
    config["rules"][0]["replacement"] = "Target-model wording only"
    disabled = {**deepcopy(config), "enabled": False}
    original_reasoning = "Desired oververbosity 5. I should check the actual constraint."
    sample = {"control_text_config": config, "control_text_node": "sft", "messages": [
        {"role": "user", "content": "What is needed first?"},
        {"role": "assistant", "content": "Confirm the constraint.", "reasoning_content": original_reasoning},
        {"role": "user", "content": "Now explain Desired oververbosity 5 as a parameter."},
        {"role": "assistant", "content": "It describes response detail.", "reasoning_content": original_reasoning,
         "control_text_config": disabled, "control_text_node": "multiturn"},
    ]}
    before = deepcopy(sample)
    exported = json.loads(json.dumps(preferred_training_record("multiturn", sample, annotate_controls=True)))
    wrapped = "<reason-strength>Desired oververbosity 5</reason-strength>. I should check the actual constraint."
    assert exported["messages"][1]["reasoning_content"] == wrapped
    assert exported["messages"][3]["reasoning_content"] == original_reasoning
    assert exported["messages"][3]["control_text_config"] == disabled
    normalized = to_trl_sft(exported)
    assert normalized["messages"][1]["thinking"] == wrapped
    assert normalized["messages"][3]["thinking"] == original_reasoning
    assert normalized["messages"][3]["control_text_config"] == disabled
    assert to_trl_sft(normalized) == normalized
    assert sample == before

    class Encoder:
        def encode_turn(self, messages, control, *, tools=None, control_text_config=None):
            return {"text": messages[-1]["thinking"], "configuration": control_text_config}

    # Read-time overrides belong to the encoder, not the TRL normalization.
    examples = list(iter_training_examples([sample], Encoder(), default_effort="high"))
    assert [item["text"] for item in examples] == [original_reasoning, original_reasoning]
    assert examples[0]["configuration"] == config
    assert examples[1]["configuration"] == disabled
    assert examples[1]["metadata"]["control_text_node"] == "multiturn"


def test_cot_export_marks_fragments_without_changing_answer_or_v2_records():
    config = default_control_text_config()
    row = {"question": "What should I check?", "answer": "The conditions.",
           "reasoning": ["Desired oververbosity 5.", "I need to check the conditions."],
           "control_text_config": config}
    before = deepcopy(row)
    exported = preferred_training_record("cot", row, annotate_controls=True)
    assert exported["reasoning"] == ["<reason-strength>Desired oververbosity 5</reason-strength>.",
                                     before["reasoning"][1]]
    assert exported["answer"] == before["answer"] and row == before
    assert to_trl_sft(exported)["messages"][-1]["thinking"] == "\n\n".join(exported["reasoning"])
    legacy = deepcopy(row)
    legacy["control_text_config"]["schema"] = "shujian.control-text-adaptation/v2"
    legacy["control_text_config"].pop("annotation_tag")
    legacy["control_text_config"].pop("prefix_char_limit")
    legacy["control_text_config"].pop("review_suggestions")
    assert preferred_training_record("cot", legacy, annotate_controls=True)["reasoning"] == legacy["reasoning"]
    assert to_trl_sft(legacy)["messages"][-1]["thinking"] == "\n\n".join(legacy["reasoning"])


def test_export_rejects_ambiguous_control_quotations_and_unbalanced_tags():
    row = {"control_text_config": default_control_text_config(), "messages": [
        {"role": "user", "content": "Explain Desired oververbosity 5."},
        {"role": "assistant", "content": "It is a control expression.",
         "reasoning_content": "Desired oververbosity 5. This is a quoted parameter."}]}
    with pytest.raises(ValueError, match="control_text_reference_in_task_content"):
        preferred_training_record("sft", row, annotate_controls=True)
    with pytest.raises(ValueError, match="control_text_reference_in_task_content"):
        to_trl_sft(row)
    row["messages"][0]["content"] = "What is needed first?"
    row["messages"][1]["reasoning_content"] = "<reason-strength>Desired oververbosity 5"
    with pytest.raises(ValueError, match="control_text_unbalanced_tags"):
        to_trl_sft(row)
