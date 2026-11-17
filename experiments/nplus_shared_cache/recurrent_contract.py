"""CPU-only authenticated inputs for a continuous recurrent five-route experiment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil

from experiments.nplus_shared_cache import runner, teacher_states, training

ARCHITECTURE = "recurrent_hidden_five_route/v4"
GOAL_DESCRIPTIONS = {
    "forward": "从当前已知条件向前推导，分清事实、推测与尚未解决的问题。",
    "backward": "从正在考虑的结论反向检查它需要哪些条件，以及这些条件是否成立。",
    "alternatives": "寻找实质不同的解释或解决路径，比较适用条件与代价。",
    "constraints": "保留用户真实要求、最新上下文、边界和未知信息，不擅自增加条件。",
    "counterexamples": "寻找可能推翻当前判断的反例、遗漏和失败情形，并据此修正。",
}


def _read(path: Path):
    return json.loads(path.read_text("utf-8"))


def _snapshot(source: Path, destination: Path):
    if destination.exists():
        if runner.file_sha(source) != runner.file_sha(destination):
            raise ValueError("frozen_snapshot_bytes_changed:" + destination.name)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _inside(root: Path, relative: str):
    candidate = Path(relative)
    path = (root / candidate).resolve()
    if candidate.is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("prepared_artifact_must_stay_inside_source")
    return path


def _model_binding(model: Path, tokenizer):
    template = tokenizer.chat_template
    if not isinstance(template, str):
        template = json.dumps(template, ensure_ascii=False, sort_keys=True)
    template_sha = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return {"model_config_sha256": runner.file_sha(model / "config.json"),
        "model_weights_sha256": teacher_states._base_weight_digests(model),
        "template_sha256": runner.file_sha(model / "chat_template.jinja")
            if (model / "chat_template.jinja").is_file() else template_sha,
        "runtime_template_sha256": template_sha,
        "tokenizer_files_sha256": {name: runner.file_sha(model / name) for name in
            ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json", "generation_config.json")
            if (model / name).is_file()}, "ending_token_id": tokenizer.eos_token_id}


def _destination(output: Path, manifest: dict, filename="manifest.json"):
    previous = output / filename
    if previous.exists():
        if _read(previous).get("identity") != manifest["identity"]:
            raise ValueError("experiment_identity_changed_use_new_output")
    elif output.exists() and any(output.iterdir()):
        raise ValueError("new_experiment_output_must_be_empty")
    output.mkdir(parents=True, exist_ok=True)


def _continuation(checkpoint: Path, failure_path: Path, *, model_binding: dict,
                  provider: dict, records: list, targets: list, width: int,
                  memory_width: int, rank: int):
    """Authenticate a rollback and unseen suffix; never call it an exact resume."""
    from safetensors import safe_open

    checkpoint = checkpoint / "auxiliary.safetensors" if checkpoint.is_dir() else checkpoint
    config_path = checkpoint.with_name("auxiliary-config.json")
    progress_path = failure_path.with_name("progress.json")
    history_path = failure_path.with_name("loss-history.jsonl")
    parent_manifest_path = failure_path.parent.parent / "manifest.json"
    config, failure, progress, parent = map(_read,
        (config_path, failure_path, progress_path, parent_manifest_path))
    parent_binding = parent["binding"]
    identity = parent["identity"]
    if (identity != training.digest(parent_binding) or config.get("identity") != identity
            or failure.get("identity") != identity or not failure.get("not_a_completed_epoch")
            or parent_binding.get("schema") != ARCHITECTURE
            or config.get("architecture") != ARCHITECTURE
            or parent_binding.get("prepared_identity") != provider["identity"]
            or config.get("prepared_identity") != provider["identity"]
            or parent_binding.get("targets") != targets or config.get("targets") != targets):
        raise ValueError("continuation_parent_identity_dataset_or_failure_changed")
    if not checkpoint.resolve().is_relative_to(failure_path.parent.resolve()):
        raise ValueError("continuation_checkpoint_must_belong_to_failed_training")
    for name, value in model_binding.items():
        if parent_binding.get(name) != value or config.get(name) != value:
            raise ValueError("continuation_parent_base_model_changed:" + name)
    for name, value in (("width", width), ("memory_width", memory_width), ("rank", rank)):
        if config.get(name) != value:
            raise ValueError("continuation_recurrent_shape_changed:" + name)
    checkpoint_sha = runner.file_sha(checkpoint)
    if config.get("auxiliary_file_sha256") != checkpoint_sha:
        raise ValueError("continuation_checkpoint_bytes_changed")
    with safe_open(checkpoint, framework="np") as stored:
        metadata = stored.metadata() or {}
        if metadata.get("identity") != identity or metadata.get("architecture") != ARCHITECTURE:
            raise ValueError("continuation_checkpoint_header_changed")
        for name, shape in (("fusion.weight", [width, 5 * width]), ("fusion.bias", [width]),
                            ("gain", []), ("purpose_codes", [5, memory_width]),
                            ("cell.weight_ih", [3 * memory_width, memory_width])):
            if stored.get_slice(name).get_shape() != shape:
                raise ValueError("continuation_checkpoint_shape_changed:" + name)
    last, previous, last_bytes, count = None, None, None, 0
    with history_path.open("rb") as history:
        for raw_line in history:
            if not raw_line.strip():
                continue
            row = json.loads(raw_line)
            if last is not None and row["global_step"] != last["global_step"] + 1:
                raise ValueError("continuation_parent_loss_history_not_ordered")
            previous, last, last_bytes, count = last, row, raw_line, count + 1
    total_steps = failure["optimizer_steps"]
    fields = ("record_index", "record_key", "begin", "end", "global_step")
    progress_matches = lambda row: row is not None and all(row.get(k) == progress.get(k) for k in fields)
    progress_current = progress_matches(last) and progress["optimizer_steps"] == total_steps
    progress_lagged = (progress_matches(previous) and progress["optimizer_steps"] == total_steps - 1)
    if last is None or last["global_step"] + 1 != total_steps or not (progress_current or progress_lagged):
        raise ValueError("continuation_failure_progress_or_loss_history_disagree")
    parent_continuation = parent_binding.get("continuation") or {}
    prior_start = parent_continuation.get("start_record", 0)
    prior_seen = parent_continuation.get("parent_seen_fraction_of_epoch", 0.)
    seen_index, saved_records = last["record_index"], config["completed_records"]
    start_record = seen_index + 1
    saved_steps = config["global_steps"]
    if (not 0 <= saved_records <= seen_index < len(records)
            or not 0 <= prior_start <= seen_index
            or start_record >= len(records) or not 0 <= saved_steps <= total_steps
            or records[seen_index]["record_key"] != last["record_key"]
            or failure["completed_records"] != seen_index - prior_start
            or progress["completed_records"] != progress["record_index"] - prior_start):
        raise ValueError("continuation_seen_record_or_checkpoint_counters_invalid")
    rounds = records[seen_index]["rounds"]
    if not 0 <= last["begin"] < last["end"] <= rounds:
        raise ValueError("continuation_last_successful_block_outside_original_record")
    seen_fraction = prior_seen + (seen_index - prior_start + last["end"] / rounds) / len(records)
    progress_fraction = prior_seen + (progress["record_index"] - prior_start
        + progress["end"] / records[progress["record_index"]]["rounds"]) / len(records)
    if (abs(progress_fraction - progress["fraction_of_one_epoch"]) > 1e-12
            or seen_fraction + (len(records) - start_record) / len(records) > 1.000001):
        raise ValueError("continuation_cumulative_seen_fraction_exceeds_original_one_epoch")
    sources = {"auxiliary.safetensors": checkpoint, "auxiliary-config.json": config_path,
        "failure.json": failure_path, "progress.json": progress_path,
        "loss-history.jsonl": history_path, "manifest.json": parent_manifest_path}
    hashes = {name: runner.file_sha(path) for name, path in sources.items()}
    value = {"parent_identity": identity, "checkpoint_file": "continuation/auxiliary.safetensors",
        "checkpoint_sha256": checkpoint_sha, "checkpoint_config_sha256": hashes["auxiliary-config.json"],
        "failure_sha256": hashes["failure.json"], "progress_sha256": hashes["progress.json"],
        "loss_history_sha256": hashes["loss-history.jsonl"], "parent_manifest_sha256": hashes["manifest.json"],
        "parent_checkpoint_completed_records": saved_records, "checkpoint_global_steps": saved_steps,
        "parent_completed_records": failure["completed_records"], "parent_optimizer_steps": total_steps,
        "parent_last_seen_record_index": seen_index, "parent_loss_history_rows": count,
        "parent_start_record": prior_start, "parent_seen_fraction_of_epoch": seen_fraction,
        "progress_lagged_one_successful_update": bool(progress_lagged),
        "last_successful_loss_record": last,
        "last_successful_loss_record_sha256": training.digest(last),
        "last_successful_loss_line_sha256": hashlib.sha256(last_bytes).hexdigest(),
        "last_loss_snapshot": "manifest.binding.continuation.last_successful_loss_record",
        "start_record": start_record, "remaining_records": len(records) - start_record,
        "skipped_seen_record_indices": list(range(start_record)),
        "discarded_uncheckpointed_updates": total_steps - saved_steps,
        "optimizer_reset": True, "exact_resume": False, "skipped_partial_record_tail": True,
        "full_128_record_epoch_completed": False,
        "snapshot_files_sha256": {"continuation/" + name: sha for name, sha in hashes.items()}}
    return value, sources


def prepare_training(model: Path, prepared: Path, initial_auxiliary: Path, output: Path,
                     block_size=8, memory_width=512, rank=64, head_lr=3e-4,
                     fusion_lr=1e-4, alignment_weight=.05,
                     continuation_checkpoint: Path | None = None,
                     continuation_failure: Path | None = None):
    """Authenticate full pre-target records; never load a backbone or start training."""
    from safetensors import safe_open
    from transformers import AutoTokenizer

    model, prepared, initial_auxiliary, output = map(Path, (model, prepared, initial_auxiliary, output))
    if min(block_size, memory_width, rank) < 1 or min(head_lr, fusion_lr) <= 0 or alignment_weight < 0:
        raise ValueError("positive_recurrent_dimensions_rates_and_nonnegative_alignment_required")
    if (continuation_checkpoint is None) != (continuation_failure is None):
        raise ValueError("continuation_requires_both_checkpoint_and_failure")
    provider = _read(prepared / "manifest.json")
    binding = provider["binding"]
    if (provider["identity"] != training.digest(binding)
            or binding.get("state_position", "pre_target") != "pre_target"
            or provider.get("encoded_status") != "encoded"):
        raise ValueError("authenticated_complete_pre_target_cache_required")
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
    model_binding = _model_binding(model, tokenizer)
    for name, value in model_binding.items():
        if binding.get(name) != value:
            raise ValueError("prepared_model_or_tokenizer_binding_changed:" + name)
    index = _read(prepared / "index.json")
    if index["identity"] != provider["identity"] or runner.file_sha(prepared / "index.json") != provider["index_sha256"]:
        raise ValueError("prepared_index_identity_or_digest_changed")
    configuration = _read(model / "config.json")
    width = configuration.get("text_config", configuration)["hidden_size"]
    with safe_open(initial_auxiliary, framework="np") as handle:
        metadata = handle.metadata() or {}
        if metadata.get("architecture") != "teacher_forced_continuous_five_route/v2":
            raise ValueError("fusion_warm_start_requires_v2_native_auxiliary")
        for name, shape in (("fusion.weight", [width, 5 * width]), ("fusion.bias", [width]), ("gain", [])):
            if handle.get_slice(name).get_shape() != shape:
                raise ValueError("native_fusion_shape_changed:" + name)
    records, targets, seen = index["records"], [], set()
    if not records or len(records) != provider["records"] or provider["encoded_records"] != len(records):
        raise ValueError("complete_nonempty_prepared_record_count_required")
    for record in records:
        key = record["record_key"]
        if key in seen or record.get("state_position", "pre_target") != "pre_target":
            raise ValueError("duplicate_record_or_post_target_cache")
        seen.add(key)
        for name in ("targets_file", "supervision_file"):
            _inside(prepared, record[name])
        receipt = teacher_states._validated_target(prepared, record, provider["identity"], width)
        if receipt is None:
            raise ValueError("prepared_teacher_target_missing")
        targets.append({"record_key": key, "targets_file": record["targets_file"],
            "sha256": receipt["targets_sha256"], "route_lengths_with_ending": record["route_lengths_with_ending"]})
    snapshots = {name: runner.file_sha(prepared / name) for name in
        ("manifest.json", "index.json", "selected-records.jsonl", "records-source.jsonl", "heldout-source.jsonl")}
    for name, sha in (("records-source.jsonl", binding["records_sha256"]),
                      ("heldout-source.jsonl", binding["heldout_sha256"]),
                      ("selected-records.jsonl", provider["selected_records_sha256"])):
        if snapshots[name] != sha:
            raise ValueError("prepared_source_snapshot_digest_changed:" + name)
    code_names = ("recurrent_heads.py", "recurrent_runtime.py", "differentiable_cache.py", "recurrent_contract.py")
    code = {name: runner.file_sha(Path(__file__).with_name(name)) for name in code_names}
    continuation, parent_sources = None, {}
    if continuation_checkpoint is not None:
        continuation, parent_sources = _continuation(Path(continuation_checkpoint), Path(continuation_failure),
            model_binding=model_binding, provider=provider, records=records, targets=targets,
            width=width, memory_width=memory_width, rank=rank)
    contract = {"schema": ARCHITECTURE, **model_binding, "width": width,
        "state_position": "pre_target", "prepared_identity": provider["identity"],
        "prepared_snapshots_sha256": snapshots, "targets": targets,
        "initial_auxiliary_sha256": runner.file_sha(initial_auxiliary), "initial_metadata": metadata,
        "epochs": 1, "ordered": True, "shuffle": False, "additional_ending_rounds": 0, "seed": 42,
        "block_size": block_size, "memory_width": memory_width, "rank": rank,
        "gradient_horizon": 2 * block_size, "attention_backend": "torch_SDPA_MATH",
        "full_answer_saved_tensor_CPU_offload": True,
        "head_learning_rate": head_lr, "fusion_learning_rate": fusion_lr,
        "hidden_alignment_weight": alignment_weight, "goal_descriptions": GOAL_DESCRIPTIONS,
        "source_code_sha256": code, "feedback": "blockwise_shared_continuous_hidden",
        "route_inputs": "own_previous_q_private_recurrent_memory_current_shared_h_known_goal_hidden",
        "route_initialization": "native_question_and_direction_hidden_plus_learned_residual",
        "gold_usage": "labels_alignment_and_route_lifetime_only_never_model_or_recurrent_inputs",
        "observation_feedback": False, "discrete_token_feedback": False,
        "initial_fusion_only_warm_start": continuation is None, "new_recurrent_parameters": continuation is None,
        "continuation": continuation,
        "epoch_scope": "one_ordered_unseen_record_suffix" if continuation else "one_ordered_complete_record_pass"}
    manifest = {"schema": ARCHITECTURE, "identity": training.digest(contract), "binding": contract,
        "records": len(records), "loaded_backbone_weights": False, "training_started": False}
    _destination(output, manifest)
    for source_name in snapshots:
        _snapshot(prepared / source_name, output / "prepared" / source_name)
    for name, path in parent_sources.items():
        _snapshot(path, output / "continuation" / name)
    runner.write_json(output / "manifest.json", manifest)
    return tokenizer, manifest, records


def prepare_cases(model: Path, checkpoint_dir: Path, cases_path: Path, output: Path):
    """Archive expectations separately; render only original supported-role messages."""
    from safetensors import safe_open
    from transformers import AutoTokenizer

    model, checkpoint_dir, cases_path, output = map(Path, (model, checkpoint_dir, cases_path, output))
    source = _read(cases_path)
    cases = source["cases"] if isinstance(source, dict) and "cases" in source else source
    if not isinstance(cases, list) or not cases:
        raise ValueError("nonempty_explicit_case_list_required")
    checkpoint = checkpoint_dir / "auxiliary.safetensors"
    configuration = _read(checkpoint_dir / "auxiliary-config.json")
    if configuration.get("auxiliary_file_sha256") != runner.file_sha(checkpoint):
        raise ValueError("recurrent_checkpoint_content_digest_changed")
    with safe_open(checkpoint, framework="np") as stored:
        metadata = stored.metadata() or {}
    if (configuration.get("architecture", configuration.get("schema")) != ARCHITECTURE
            or metadata.get("identity") != configuration.get("identity")):
        raise ValueError("recurrent_checkpoint_identity_or_architecture_changed")
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
    model_binding = _model_binding(model, tokenizer)
    for name in ("model_config_sha256", "model_weights_sha256", "template_sha256"):
        if configuration.get(name) != model_binding[name]:
            raise ValueError("checkpoint_base_model_binding_changed:" + name)
    top = {"schema": ARCHITECTURE, "source_sha256": runner.file_sha(cases_path),
        "checkpoint_sha256": runner.file_sha(checkpoint), "config_sha256": runner.file_sha(checkpoint_dir / "auxiliary-config.json"),
        "model_binding": model_binding, "source_code_sha256": runner.file_sha(Path(__file__))}
    top["identity"] = training.digest(top)
    _destination(output, top, "cases-manifest.json")
    _snapshot(cases_path, output / "source-input.json")
    runner.write_json(output / "cases-manifest.json", top)
    prepared, seen = [], set()
    for case in cases:
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", case_id) or case_id in seen:
            raise ValueError("case_id_must_be_unique_safe_slug")
        seen.add(case_id)
        messages, adaptations = runner.normalize_messages(case["messages"])
        options = {"tools": case["tools"]} if "tools" in case else {}
        prefix = tokenizer.apply_chat_template(messages, tokenize=False,
            add_generation_prompt=True, enable_thinking=True, **options)
        prefix_ids = tokenizer.encode(prefix, add_special_tokens=False)
        boundary_ids = tokenizer.encode(runner.BOUNDARY, add_special_tokens=False)
        payload = {"parent_identity": top["identity"], "case_id": case_id,
            "prefix_sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
            "prefix_ids_sha256": training.digest(prefix_ids), "boundary_ids": boundary_ids}
        manifest = {"schema": ARCHITECTURE, "identity": training.digest(payload), **payload,
            "message_adaptations": adaptations, "expected_fields_are_not_prompt": True}
        destination = output / case_id
        _destination(destination, manifest, "input-manifest.json")
        runner.write_json(destination / "source-case.json", case)
        runner.write_json(destination / "model-messages.json", messages)
        (destination / "prefix.txt").write_bytes(prefix.encode("utf-8"))
        runner.write_json(destination / "prefix-ids.json", prefix_ids)
        runner.write_json(destination / "input-manifest.json", manifest)
        prepared.append({"case_id": case_id, "prefix_ids": prefix_ids, "output": destination,
            "manifest": manifest, "boundary_ids": boundary_ids})
    return tokenizer, prepared
