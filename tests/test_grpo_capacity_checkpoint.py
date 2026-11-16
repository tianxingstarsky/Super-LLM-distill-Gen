"""A capacity change must restore the actual earlier step, not reset the epoch."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from lib.contexts.training.domain.grpo import GRPOValidationPlan
from lib.contexts.training.domain.validation import fingerprint
from lib.contexts.training.infrastructure.filesystem import file_sha, write_json
from lib.contexts.training.infrastructure.grpo_checkpoint import (
    authorize_capacity_resume, verify_capacity_parent_unchanged, reuse_capacity_baseline)
from lib.contexts.training.infrastructure.transformers_backend import _digest
from lib.contexts.training.infrastructure.trl_reward import REWARD_PROTOCOL


def parent_checkpoint(tmp_path, monkeypatch):
    root = tmp_path / "parent"
    configuration = GRPOValidationPlan("model", str(root), train_size=8, evaluation_size=8,
        max_completion_length=4096).to_dict()
    tasks = {"train": [{"task_id": "first"}], "evaluation": [{"task_id": "held-out"}]}
    model = {"files": [{"file": "model.safetensors", "sha256": "frozen"}]}
    manifest = {"configuration": configuration, "model": model, "tasks": tasks, "reward_protocol": REWARD_PROTOCOL}
    manifest["identity"] = fingerprint(manifest)
    write_json(root / "experiment.json", manifest)
    training = root / "grpo_train"
    identity = _digest([model, tasks["train"], configuration, REWARD_PROTOCOL])
    write_json(training / "training-identity.json", {"experiment_identity": identity,
        "config": configuration, "tasks_sha256": _digest(tasks["train"])})
    write_json(training / "training-progress.json", {"experiment_identity": identity, "global_step": 1, "epoch": .125})
    (training / "rollouts.jsonl").write_text("".join(json.dumps({"global_step": 0,
        "task_id": "first", "reasoning": "preserved", "ended_with_eos": True}) + "\n" for _ in range(4)), encoding="utf-8")
    checkpoint = training / "checkpoint-1"
    checkpoint.mkdir()
    adapter = checkpoint / "adapter_model.safetensors"
    adapter.write_bytes(b"immutable adapter fixture")
    write_json(checkpoint / "trainer_state.json", {"global_step": 1, "epoch": .125})
    write_json(checkpoint / "experiment-identity.json", {"experiment_identity": identity,
        "global_steps": 1, "epoch": .125,
        "adapter_files": [{"file": adapter.name, "sha256": file_sha(adapter)}]})
    monkeypatch.setitem(sys.modules, "transformers.trainer_utils", SimpleNamespace(get_last_checkpoint=lambda path: str(checkpoint)))
    plan = GRPOValidationPlan("model", str(tmp_path / "child"), parent_run=str(root), train_size=8, evaluation_size=8)
    return plan, model, tasks, training, checkpoint


def test_capacity_resume_authenticates_original_step_and_complete_native_history(tmp_path, monkeypatch):
    plan, model, tasks, _, checkpoint = parent_checkpoint(tmp_path, monkeypatch)
    parent = authorize_capacity_resume(plan, model, tasks)
    assert parent["checkpoint"] == str(checkpoint)
    assert parent["global_step"] == 1 and parent["epoch"] == .125
    assert len(parent["committed_rollouts"]) == 4
    assert parent["committed_rollouts"][0]["reasoning"] == "preserved"
    verify_capacity_parent_unchanged(parent)
    (checkpoint / "adapter_model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="parent_advanced_or_changed"):
        verify_capacity_parent_unchanged(parent)


def test_capacity_resume_rejects_a_checkpoint_behind_observed_optimizer_progress(tmp_path, monkeypatch):
    plan, model, tasks, training, _ = parent_checkpoint(tmp_path, monkeypatch)
    progress = json.loads((training / "training-progress.json").read_text())
    progress["global_step"] = 2
    write_json(training / "training-progress.json", progress)
    with pytest.raises(ValueError, match="checkpoint_behind_observed_progress"):
        authorize_capacity_resume(plan, model, tasks)


def test_capacity_extension_reuses_wrong_early_eos_but_never_capped_outputs(tmp_path):
    root = tmp_path / "parent"
    tasks = [{"task_id": "early-wrong", "prompt": "Question one", "expected_answer": "42", "family": "math"},
             {"task_id": "capped", "prompt": "Question two", "expected_answer": "42", "family": "math"}]
    rows = [{"case_id": task["task_id"], "messages": [{"role": "system", "content": "You are a helpful assistant."},
             {"role": "user", "content": task["prompt"]}], "expected_answer": "42", "answer": r"\boxed{43}",
             "reasoning": "unchanged native text", "ended_with_eos": index == 0, "truncated": index == 1,
             "experiment_identity": "earlier-evaluation", "generation_config": {"max_new_tokens": 4096}}
            for index, task in enumerate(tasks)]
    receipt = {"status": "completed", "cases": rows}
    write_json(root / "baseline.json", {"identity": "parent", "receipt_sha256": fingerprint(receipt),
        "result": receipt, "artifacts": []})
    plan = GRPOValidationPlan("model", str(tmp_path / "child"), parent_run=str(root), evaluation_size=2)
    reuse_capacity_baseline(plan, {"model": {"files": []}, "tasks": {"evaluation": tasks}},
        {"parent_root": str(root), "parent_experiment_identity": "parent"})
    reused = json.loads((Path(plan.output) / "baseline/case-0000.json").read_text(encoding="utf-8"))
    assert reused["answer"] == r"\boxed{43}"  # No selection by reward.
    assert reused["reasoning"] == "unchanged native text"
    assert reused["generation_config"]["max_new_tokens"] == 4096  # Preserve the actual old packet.
    assert not (Path(plan.output) / "baseline/case-0001.json").exists()
