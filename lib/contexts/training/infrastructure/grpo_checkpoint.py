"""Authenticate an explicit capacity-only continuation of a single GRPO pass."""
import json
from pathlib import Path

from ..domain.grpo import validate_capacity_resume
from ..domain.validation import fingerprint
from .filesystem import file_sha, write_json
from .transformers_backend import _digest, _guard_training_resume, evaluation_identity
from .trl_reward import REWARD_PROTOCOL


def evaluation_input(plan, tasks):
    cases = [{"id": task["task_id"], "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": task["prompt"]}],
        "expected_answer": task["expected_answer"], "group": task["family"]} for task in tasks]
    configuration = {"seed": plan.seed, "max_new_tokens": plan.max_completion_length,
        "temperature": plan.temperature, "top_p": 0.95, "top_k": 20,
        "repetition_penalty": 1.0, "qlora": True}
    return cases, configuration


def read_committed_stage(root, identity, stage):
    root = Path(root)
    path = root / (stage + ".json")
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record["identity"] != identity or record["receipt_sha256"] != fingerprint(record["result"]):
        raise ValueError("grpo_stage_identity_changed")
    for artifact in record["artifacts"]:
        target = root / artifact["path"]
        if not target.is_file() or file_sha(target) != artifact["sha256"]:
            raise ValueError("grpo_stage_artifact_changed")
    return record["result"] if record["result"].get("status") == "completed" else None


def authorize_capacity_resume(plan, model, tasks):
    """Bind frozen inputs and the actual native Trainer checkpoint before work."""
    from transformers.trainer_utils import get_last_checkpoint

    parent_root = Path(plan.parent_run)
    manifest_path = parent_root / "experiment.json"
    parent = json.loads(manifest_path.read_text(encoding="utf-8"))
    if parent["identity"] != fingerprint({key: value for key, value in parent.items() if key != "identity"}):
        raise ValueError("grpo_parent_manifest_changed")
    validate_capacity_resume(parent["configuration"], plan)
    if parent["model"] != model or parent["tasks"] != tasks or parent["reward_protocol"] != REWARD_PROTOCOL:
        raise ValueError("grpo_parent_inputs_changed")
    training = parent_root / "grpo_train"
    binding = json.loads((training / "training-identity.json").read_text(encoding="utf-8"))
    identity = _digest([parent["model"], tasks["train"], parent["configuration"], REWARD_PROTOCOL])
    if (binding["experiment_identity"] != identity or binding["config"] != parent["configuration"]
            or binding["tasks_sha256"] != _digest(tasks["train"])):
        raise ValueError("grpo_parent_training_identity_changed")
    checkpoint = get_last_checkpoint(str(training))
    if not checkpoint:
        raise ValueError("grpo_parent_has_no_committed_checkpoint")
    checkpoint = Path(checkpoint)
    if not checkpoint.resolve().is_relative_to(training.resolve()):
        raise ValueError("grpo_parent_checkpoint_outside_run")
    state = json.loads((checkpoint / "trainer_state.json").read_text(encoding="utf-8"))
    proof = json.loads((checkpoint / "experiment-identity.json").read_text(encoding="utf-8"))
    step = state["global_step"]
    if (proof["experiment_identity"] != identity or proof["global_steps"] != step
            or state["epoch"] != proof["epoch"] or not 0 < state["epoch"] < plan.epochs):
        raise ValueError("grpo_parent_checkpoint_state_changed_or_finished")
    _guard_training_resume(training, identity, step)
    for artifact in proof["adapter_files"]:
        if file_sha(checkpoint / artifact["file"]) != artifact["sha256"]:
            raise ValueError("grpo_parent_adapter_changed")
    journal = training / "rollouts.jsonl"
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    used, unused = [row for row in rows if row["global_step"] < step], [row for row in rows if row["global_step"] >= step]
    if len(used) != step * plan.gradient_accumulation * plan.batch_size:
        raise ValueError("grpo_parent_rollout_history_ambiguous")
    files = [manifest_path, training / "training-identity.json", training / "training-progress.json", journal,
             *[path for path in checkpoint.rglob("*") if path.is_file()]]
    return {"parent_experiment_identity": parent["identity"], "parent_training_identity": identity,
        "checkpoint": str(checkpoint), "global_step": step, "epoch": state["epoch"],
        "previous_max_completion_length": parent["configuration"]["max_completion_length"],
        "committed_rollouts": used, "unused_rollouts": unused,
        "parent_root": str(parent_root),
        "source_files": [{"path": str(path), "sha256": file_sha(path)} for path in files]}


def verify_capacity_parent_unchanged(parent):
    """Check the earlier authorization's file digests before loading its state."""
    for artifact in parent["source_files"]:
        if file_sha(artifact["path"]) != artifact["sha256"]:
            raise ValueError("grpo_capacity_parent_advanced_or_changed")


def reuse_capacity_baseline(plan, manifest, parent):
    """Reuse every early-EOS output, including wrong answers; never reuse caps."""
    root = Path(parent["parent_root"])
    previous = read_committed_stage(root, parent["parent_experiment_identity"], "baseline")
    if previous is None:
        return
    cases, configuration = evaluation_input(plan, manifest["tasks"]["evaluation"])
    identity = evaluation_identity(manifest["model"]["files"], [], cases, configuration)
    old_rows = {row["case_id"]: row for row in previous["cases"]}
    destination = Path(plan.output) / "baseline"
    destination.mkdir(parents=True, exist_ok=True)
    for index, case in enumerate(cases):
        target = destination / f"case-{index:04d}.json"
        row = old_rows.get(case["id"])
        if target.exists() or not row or not row.get("ended_with_eos") or row.get("truncated"):
            continue
        if row["messages"] != case["messages"] or row["expected_answer"] != case["expected_answer"]:
            raise ValueError("grpo_capacity_baseline_input_changed")
        # Parent tasks, model, seeds and sampling parameters were authenticated;
        # only the stop ceiling increases. An existing EOS is already terminal.
        reused = dict(row)
        reused["experiment_identity"] = identity
        reused["capacity_reuse"] = {"source_experiment_identity": parent["parent_experiment_identity"],
            "source_evaluation_identity": row["experiment_identity"],
            "reason": "native_eos_before_previous_capacity; not_selected_by_reward",
            "original_generation_config_preserved": True, "native_output_changed": False}
        write_json(target, reused)
