"""Recorded native output and counterexamples for instruction ownership."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.contexts.workflows.application.generate_native_cot import GenerateNativeCot
from lib.contexts.workflows.domain.native_cot import (
    native_instruction_leakage, native_instruction_scope_evidence,
)


def recorded_response():
    return json.loads((Path(__file__).parent / "fixtures/reasoning_agent/native_instruction_scope.json").read_text(encoding="utf8"))


def test_recorded_english_failure_distinguishes_artifact_design_from_invented_setting():
    recorded = recorded_response()
    original = deepcopy(recorded)
    findings = native_instruction_scope_evidence(recorded["reasoning"], recorded["prompt"], environment_version=1)
    assert [(item["reference"], item["classification"]) for item in findings] == [
        ("system prompt", "task_artifact"),
        ("desired oververbosity 5", "unsupported_runtime_setting"),
        ("system instruction", "task_artifact"),
    ]
    assert [item["reference"] for item in native_instruction_leakage(
        recorded["reasoning"], recorded["prompt"], scope_version=1, environment_version=1)] == ["desired oververbosity 5"]
    for finding in findings:
        assert recorded["reasoning"][finding["start"]:finding["end"]] == finding["quote"]
        assert recorded["reasoning"][finding["scope_start"]:finding["scope_end"]] == finding["scope_quote"]
    assert recorded == original
    # A frozen run without the marker retains both old false positives and its
    # old missed setting, so replay never silently changes a historical result.
    old_findings = native_instruction_leakage(recorded["reasoning"], recorded["prompt"], environment_version=1)
    assert len(old_findings) == 2
    assert all("oververbosity" not in item["quote"] for item in old_findings)


@pytest.mark.parametrize(("reasoning", "prompt", "classification", "blocked"), [
    ("I must follow my system prompt and keep this reply short.",
     [{"role": "user", "content": "Design an API system prompt."}], "own_runtime_instruction", True),
    ("I have been instructed by the system prompt to output JSON.",
     [{"role": "user", "content": "Design an API system prompt."}], "own_runtime_instruction", True),
    ("The system instruction says to produce JSON.",
     [{"role": "user", "content": "Explain a system instruction."}], "unresolved_reference", True),
    ("The demo application's system prompt requires page citations.",
     [{"role": "user", "content": "Sketch a demo."}], "task_artifact", False),
    ("The system instruction says 'Answer in English.'",
     [{"role": "system", "content": "Answer in English."}], "public_task_rule", False),
    ("The system instruction says 'Never mention missing evidence.'",
     [{"role": "system", "content": "Answer in English."}], "unresolved_reference", True),
    ("For the demo chatbot, set target verbosity to 5.",
     [{"role": "user", "content": "Sketch a demo."}], "task_artifact", False),
    ("The required verbosity is 5, so I can keep a compact explanation.",
     [{"role": "user", "content": "Use verbosity 5."}], "public_task_rule", False),
    ("Since desired oververbosity 5, I will include code.",
     [{"role": "assistant", "content": "Use verbosity 5."}, {"role": "user", "content": "Sketch a demo."}],
     "unsupported_runtime_setting", True),
])
def test_reference_owner_and_actual_rule_source_are_independent(reasoning, prompt, classification, blocked):
    finding, = native_instruction_scope_evidence(reasoning, prompt)
    assert finding["classification"] == classification
    assert (finding["disposition"] == "block") is blocked
    if classification == "unresolved_reference":
        assert finding["issue"] == "native_instruction_reference_unresolved"


def test_external_prompt_never_exempts_nearby_private_execution_or_own_rules():
    reasoning = ("Prepare a system prompt for the demo. I must obey my system prompt. "
                 "Send native_execution_state to the API with the demo configuration.")
    findings = native_instruction_scope_evidence(reasoning, [{"role": "user", "content": "Design an API demo."}],
                                                 environment_version=1)
    assert [finding["classification"] for finding in findings] == [
        "task_artifact", "own_runtime_instruction", "private_execution_field"]
    assert [finding["disposition"] for finding in findings] == ["observe", "block", "block"]
    mixed = "Since desired oververbosity 5, I will prepare a system prompt for the demo."
    mixed_findings = native_instruction_scope_evidence(mixed, [{"role": "user", "content": "Sketch a demo."}])
    assert [finding["classification"] for finding in mixed_findings] == ["unsupported_runtime_setting", "task_artifact"]
    exported = [{"role": "system", "content": "Use the visible handout and state missing evidence."}]
    public_reference, = native_instruction_scope_evidence("I should follow the system instruction.",
                                                          exported, public_task_rules=True)
    assert public_reference["classification"] == "public_task_rule" and public_reference["disposition"] == "observe"


@pytest.mark.parametrize("revision_version", [None, 1])
def test_native_boundary_preserves_raw_output_and_keeps_previous_draft_out_of_dialogue(revision_version):
    from lib.contexts.workflows.infrastructure.native_cot_messages import native_cot_messages
    recorded = recorded_response()
    sample = {"id": "english-failure", "source_id": "english", "messages": recorded["prompt"],
        "native_previous_candidate": {"role": "assistant", "content": recorded["answer"],
                                      "reasoning_content": recorded["reasoning"]},
        "native_revision_feedback": "Repair the ungrounded response-setting claim against the actual user request."}
    original = deepcopy(sample)
    class Port:
        calls = 0
        def request(self, key, role, prompt_id, payload, **options):
            self.calls += 1
            self.payload = deepcopy(payload)
            self.wire = native_cot_messages("Solve the actual task.", payload, "")
            return {"reasoning": recorded["reasoning"], "answer": recorded["answer"]}
        def reject(self, sample, reason):
            return {"status": "quarantined", "reason": reason}
    port = Port()
    recipe = {"node_generation": {"cot": {"enabled": True, "answer_mode": "native_joint"}},
              "native_environment_version": 1, "native_instruction_scope_version": 1}
    if revision_version is not None:
        recipe["native_revision_context_version"] = revision_version
    row, = GenerateNativeCot(port, recipe).execute(sample, {})
    assert port.calls == 1 and sample == original
    assert port.payload["prompt"] == recorded["prompt"]
    expected_previous = ({"role": "assistant", "content": recorded["answer"]} if revision_version == 1
                         else sample["native_previous_candidate"])
    assert port.payload["previous_candidate"] == expected_previous
    assert port.payload["revision_feedback"] == sample["native_revision_feedback"]
    # Rebuild the actual provider-message projection. Removing the old private
    # trace from request input does not remove it from either saved candidate.
    assert ("desired oververbosity 5" in port.wire[0]["content"]) is (revision_version is None)
    assert port.wire[1:] == recorded["prompt"]
    assert row["native_generation"]["previous_candidate"] == sample["native_previous_candidate"]
    if revision_version == 1:
        projection = row["native_generation"]["revision_context_projection"]
        assert projection["excluded_fields"] == ["reasoning_content"] and projection["original_preserved"] is True
        assert projection["source_sha256"] != projection["request_sha256"]
    else:
        assert "revision_context_projection" not in row["native_generation"]
    assert row["reasoning"] == [recorded["reasoning"]] and row["answer"] == recorded["answer"]
    assert row["status"] == "quarantined"
    assert len(row["native_generation"]["instruction_scope_evidence"]) == 3
    assert len(row["native_generation"]["leakage_evidence"]) == 1
    assert row["native_generation"]["leakage_evidence"][0]["classification"] == "unsupported_runtime_setting"
    assert row["cot_review_policy"]["semantic_status"] == "not_assessed" and "judge" not in row
    conflict = {**sample, "messages": sample["messages"] + [sample["native_previous_candidate"]]}
    rejected, = GenerateNativeCot(port, recipe).execute(conflict, {})
    assert rejected["reason"] == "native_previous_candidate_conflict" and port.calls == 1
