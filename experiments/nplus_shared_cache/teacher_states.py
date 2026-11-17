"""Freeze complete per-token local-Qwen teacher states for continuous heads.

Preparation uses the tokenizer and CPU supervision files only. ``--execute``
loads the official frozen model once and encodes every original path token plus
EOS, using the selected pre-token or post-token state. No vocabulary logits, training, downloads or API calls
are performed. These are local-Qwen states, not the remote teacher's hidden CoT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.nplus_shared_cache import runner, training
from experiments.nplus_shared_cache.text_supervision import encode_five_paths

SCHEMA = "nplus-full-token-teacher-states/v1"
SUPERVISION_SCHEMA = "nplus-full-token-teacher-labels/v1"


def _read(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def _array_digest(labels: Any, active: Any) -> str:
    digest = hashlib.sha256()
    for name, value in (("labels", labels), ("active", active)):
        digest.update(training.digest({"name": name, "shape": list(value.shape),
            "dtype": value.dtype.str}).encode("ascii"))
        digest.update(value.tobytes(order="C"))
    return digest.hexdigest()


def _base_weight_digests(model: Path) -> dict[str, str]:
    index = model / "model.safetensors.index.json"
    names = sorted(set(_read(index)["weight_map"].values())) if index.is_file() else ["model.safetensors"]
    if not names:
        raise ValueError("base_model_weight_files_required")
    result = {}
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not (model / relative).is_file():
            raise ValueError("invalid_or_missing_base_model_weight_shard")
        result[relative.as_posix()] = runner.file_sha(model / relative)
    return result


def _archive(source: Path, destination: Path) -> None:
    if destination.exists():
        if runner.file_sha(destination) != runner.file_sha(source):
            raise ValueError("frozen_teacher_source_bytes_changed")
    elif source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)


def _write_unchanged(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("existing_teacher_preparation_content_changed:" + path.name)
        return
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _write_supervision(path: Path, *, labels: Any, active: Any,
                       identity: str, record_key: str, encoding_sha: str) -> None:
    import numpy as np
    from safetensors import safe_open
    from safetensors.numpy import save_file

    metadata = {"schema": SUPERVISION_SCHEMA, "identity": identity,
        "record_key": record_key, "encoding_sha256": encoding_sha}
    if path.exists():
        with safe_open(path, framework="np") as stored:
            if stored.metadata() != metadata or set(stored.keys()) != {"labels", "active"}:
                raise ValueError("teacher_supervision_identity_changed")
            if (not np.array_equal(stored.get_tensor("labels"), labels)
                    or not np.array_equal(stored.get_tensor("active"), active)):
                raise ValueError("teacher_supervision_values_changed")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    save_file({"labels": labels, "active": active}, str(temporary), metadata=metadata)
    temporary.replace(path)


def _validated_target(output: Path, entry: dict, identity: str, width: int) -> dict | None:
    """Authenticate completed cache bytes/header and the small label tensors."""
    import numpy as np
    from safetensors import safe_open

    path = output / entry["targets_file"]
    receipt_path = path.with_suffix(".json")
    if not path.exists() and not receipt_path.exists():
        return None
    if not path.is_file() or not receipt_path.is_file():
        raise ValueError("teacher_state_or_receipt_missing_requires_inspection")
    receipt = _read(receipt_path)
    expected_shape = [entry["rounds"], len(runner.PURPOSES), width]
    if (receipt.get("identity") != identity or receipt.get("record_key") != entry["record_key"]
            or receipt.get("encoding_sha256") != entry["encoding_sha256"]
            or receipt.get("status") != "encoded"
            or receipt.get("teacher_heads_shape") != expected_shape
            or receipt.get("targets_sha256") != runner.file_sha(path)):
        raise ValueError("teacher_state_receipt_identity_shape_or_digest_changed")
    with safe_open(output / entry["supervision_file"], framework="np") as supervision:
        labels = supervision.get_tensor("labels")
        active = supervision.get_tensor("active")
    # Header inspection does not materialize the large BF16 state tensor.
    with safe_open(path, framework="np") as stored:
        metadata = stored.metadata() or {}
        if (metadata.get("schema") != SCHEMA or metadata.get("identity") != identity
                or metadata.get("record_key") != entry["record_key"]
                or metadata.get("encoding_sha256") != entry["encoding_sha256"]
                or set(stored.keys()) != {"teacher_heads", "labels", "active"}
                or stored.get_slice("teacher_heads").get_shape() != expected_shape
                or stored.get_slice("teacher_heads").get_dtype() != "BF16"
                or stored.get_slice("labels").get_dtype() != "I64"
                or stored.get_slice("active").get_dtype() != "BOOL"):
            raise ValueError("teacher_state_header_changed")
        if (not np.array_equal(stored.get_tensor("labels"), labels)
                or not np.array_equal(stored.get_tensor("active"), active)):
            raise ValueError("teacher_state_labels_or_active_changed")
    return receipt


def _status_manifest(manifest: dict, receipts: dict[str, dict], total: int) -> dict:
    return {**manifest, "encoded_status": "encoded" if len(receipts) == total else "prepared",
        "encoded_records": len(receipts), "records_require_encoding": total - len(receipts),
        "encoded_targets": {key: {"targets_file": value["targets_file"],
            "targets_sha256": value["targets_sha256"], "teacher_heads_shape": value["teacher_heads_shape"]}
            for key, value in receipts.items()}}


def prepare_teacher_states(args):
    """Freeze data/model identities and complete labels, without loading weights.

    Returns ``tokenizer, manifest, index, selected_records``. Source validation
    and prefix rendering share the existing training contract, but do not call
    its legacy three-position target encoder or require an auxiliary checkpoint.
    """
    import numpy as np
    from transformers import AutoTokenizer

    model, output = Path(args.model), Path(args.output)
    records_path, heldout_path = Path(args.records), Path(args.heldout)
    state_position = getattr(args, "state_position", "pre_target")
    if state_position not in {"pre_target", "post_target"}:
        raise ValueError("unknown_teacher_state_position")
    if args.train_limit < 1:
        raise ValueError("positive_teacher_train_limit_required")
    if output.resolve() == model.resolve():
        raise ValueError("teacher_output_must_not_be_model_directory")
    if any(path.resolve() == (output / "selected-records.jsonl").resolve() for path in (records_path, heldout_path)):
        raise ValueError("teacher_input_must_not_be_generated_selected_records")
    configuration = _read(model / "config.json")
    text_configuration = configuration.get("text_config", configuration)
    width = text_configuration["hidden_size"]
    capacity = text_configuration.get("max_position_embeddings")
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
    if tokenizer.eos_token_id is None:
        raise ValueError("teacher_ending_token_required")
    ending = tokenizer.eos_token_id
    heldout = list(training.jsonl_records(heldout_path))
    heldout_hashes = {record["question_sha256"] for record in heldout}
    heldout_texts = {" ".join(record["question"].split()).casefold() for record in heldout}
    selected, encoded, arrays = [], [], {}
    seen = set()
    for record in training.jsonl_records(records_path):
        if (record.get("split") != "train" or record["question_sha256"] in heldout_hashes
                or " ".join(record["question"].split()).casefold() in heldout_texts):
            raise ValueError("training_record_split_or_question_level_heldout_overlap")
        if record.get("structural_status") != "complete" or record.get("declared_invalid"):
            raise ValueError("incomplete_or_declared_invalid_teacher_record")
        if record.get("teacher_verification", {}).get("score") != 1:
            raise ValueError("teacher_final_requires_existing_verified_record")
        key = training.digest({"question": record["question"], "paths": record["paths"], "answer": record["final_answer"]})
        if record.get("record_key", key) != key:
            raise ValueError("teacher_record_key_changed")
        if key in seen:
            continue
        seen.add(key)  # Only exact complete records; retain same-question variants.
        supervision = encode_five_paths(tokenizer, record["paths"], ending_token_id=ending)
        labels = np.asarray(supervision["labels"], dtype=np.int64)
        active = np.asarray(supervision["active"], dtype=np.bool_)
        prefix_ids = tokenizer.encode(training.render_prefix(tokenizer, record, purpose=None), add_special_tokens=False)
        teacher_prefixes = [tokenizer.encode(training.render_prefix(tokenizer, record, purpose=purpose),
            add_special_tokens=False) for purpose in runner.PURPOSES]
        closing_ids = tokenizer.encode(runner.BOUNDARY, add_special_tokens=False)
        answer_ids = tokenizer.encode(record["final_answer"], add_special_tokens=False)
        if not answer_ids:
            raise ValueError("empty_final_answer")
        answer_ids.append(ending)
        lengths = supervision["route_lengths_with_ending"]
        teacher_lengths = [len(prefix) + route_length - (state_position == "pre_target")
            for prefix, route_length in zip(teacher_prefixes, lengths)]
        student_context = len(prefix_ids) + supervision["rounds"] + len(closing_ids) + len(answer_ids) - 1
        if isinstance(capacity, int) and max(teacher_lengths + [student_context]) > capacity:
            raise ValueError("complete_teacher_or_answer_context_exceeds_model_capacity_not_truncated")
        item = {"record_key": key, "question_sha256": record["question_sha256"],
            "state_position": state_position,
            "prefix_ids": prefix_ids, "closing_ids": closing_ids, "answer_ids": answer_ids,
            "teacher_prefix_ids": teacher_prefixes, "route_lengths_with_ending": lengths,
            "rounds": supervision["rounds"], "ending_token_id": ending,
            "padding_token_id": tokenizer.pad_token_id,
            "teacher_input_lengths": teacher_lengths, "student_full_context_tokens": student_context,
            "supervision_file": "supervision/" + key + ".safetensors",
            "targets_file": "targets/" + key + ".safetensors",
            "supervision_content_sha256": _array_digest(labels, active)}
        item["encoding_sha256"] = training.digest(item)
        encoded.append(item)
        arrays[key] = (labels, active)
        selected.append({**record, "record_key": key})
        if len(selected) >= args.train_limit:
            break
    if not selected:
        raise ValueError("no_complete_verified_teacher_records")
    template_file = model / "chat_template.jinja"
    runtime_template = tokenizer.chat_template
    if not isinstance(runtime_template, str):
        runtime_template = json.dumps(runtime_template, ensure_ascii=False, sort_keys=True)
    directory = Path(__file__).parent
    alignment = ("hidden[prefix_length-1+t] predicts route_label[t]; t=0..body_length"
        if state_position == "pre_target" else
        "hidden[prefix_length+t] has consumed route_label[t]; t=0..body_length; final label is EOS")
    binding = {"schema": SCHEMA, "state_position": state_position,
        "records_sha256": runner.file_sha(records_path),
        "heldout_sha256": runner.file_sha(heldout_path), "train_limit": args.train_limit,
        "selected_record_keys": [entry["record_key"] for entry in encoded], "width": width,
        "model_config_sha256": runner.file_sha(model / "config.json"),
        "model_weights_sha256": _base_weight_digests(model),
        "model_weight_index_sha256": runner.file_sha(model / "model.safetensors.index.json")
            if (model / "model.safetensors.index.json").is_file() else None,
        "template_sha256": runner.file_sha(template_file) if template_file.is_file()
            else hashlib.sha256(runtime_template.encode("utf-8")).hexdigest(),
        "runtime_template_sha256": hashlib.sha256(runtime_template.encode("utf-8")).hexdigest(),
        "tokenizer_files_sha256": {name: runner.file_sha(model / name) for name in
            ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json", "generation_config.json")
            if (model / name).is_file()},
        "ending_token_id": ending, "padding_token_id": tokenizer.pad_token_id,
        "purposes": list(runner.PURPOSES), "teacher_encoding_code_sha256": runner.file_sha(Path(__file__)),
        "source_code_sha256": {name: runner.file_sha(directory / name) for name in
            ("teacher_states.py", "text_supervision.py", "training.py", "runner.py", "latent_feedback.py")},
        "encoded_input_sha256": training.digest(encoded), "target_dtype": "BF16",
        "teacher_state_alignment": alignment,
        "after_route_ending": "teacher_heads_exact_zero; labels=-100; active=false; EOS round remains active",
        "tokenization": "separately_encoded_prefix_and_complete_original_path; EOS is a programmatic target boundary",
        "target_origin": "frozen_local_Qwen_full_token_teacher_forcing_not_DeepSeek_hidden",
        "full_original_final_answer_plus_EOS": True, "training": False}
    identity = training.digest(binding)
    manifest_path = output / "manifest.json"
    previous = _read(manifest_path) if manifest_path.exists() else None
    if previous and previous.get("identity") != identity:
        raise ValueError("teacher_state_identity_changed_use_new_output")
    output.mkdir(parents=True, exist_ok=True)
    for entry in encoded:
        labels, active = arrays[entry["record_key"]]
        path = output / entry["supervision_file"]
        _write_supervision(path, labels=labels, active=active, identity=identity,
            record_key=entry["record_key"], encoding_sha=entry["encoding_sha256"])
        entry["supervision_sha256"] = runner.file_sha(path)
    _archive(records_path, output / "records-source.jsonl")
    _archive(heldout_path, output / "heldout-source.jsonl")
    selected_bytes = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in selected).encode("utf-8")
    _write_unchanged(output / "selected-records.jsonl", selected_bytes)
    index = {"schema": SCHEMA, "identity": identity, "records": encoded}
    index_path = output / "index.json"
    if index_path.exists():
        if _read(index_path) != index or (previous and runner.file_sha(index_path) != previous["index_sha256"]):
            raise ValueError("frozen_teacher_state_index_changed")
    else:
        runner.write_json(index_path, index)
    manifest = {"schema": SCHEMA, "identity": identity, "binding": binding,
        "records": len(selected), "questions": len({r["question_sha256"] for r in selected}),
        "heldout_questions": len(heldout_hashes), "index_file": "index.json",
        "index_sha256": runner.file_sha(index_path),
        "selected_records_sha256": runner.file_sha(output / "selected-records.jsonl"),
        "full_base_weight_content_bound": True, "teacher_head_encoding_is_training": False,
        "vocabulary_probabilities_computed": False, "three_position_proxy_used": False,
        "target_origin": binding["target_origin"]}
    receipts = {}
    for entry in encoded:
        receipt = _validated_target(output, entry, identity, width)
        if receipt is not None:
            receipts[entry["record_key"]] = receipt
    manifest = _status_manifest(manifest, receipts, len(encoded))
    runner.write_json(manifest_path, manifest)
    return tokenizer, manifest, index, selected


def encode_teacher_states(args, tokenizer, manifest: dict, index: dict) -> dict:
    """Encode missing full-token states once; reuse authenticated completed files."""
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file

    output = Path(args.output)
    identity = manifest["identity"]
    width = manifest["binding"]["width"]
    state_position = manifest["binding"].get("state_position", "pre_target")
    if getattr(args, "state_position", "pre_target") != state_position:
        raise ValueError("teacher_state_position_differs_from_frozen_binding")
    if index["identity"] != identity or runner.file_sha(output / "index.json") != manifest["index_sha256"]:
        raise ValueError("teacher_encoding_index_identity_changed")
    receipts, pending = {}, []
    for entry in index["records"]:
        receipt = _validated_target(output, entry, identity, width)
        if receipt is None:
            pending.append(entry)
        else:
            receipts[entry["record_key"]] = receipt
    if not pending:
        return _status_manifest(manifest, receipts, len(index["records"]))
    base, load_receipt = runner.load_frozen_model(args.model, device=getattr(args, "device", "cuda:0"))
    runner.write_json(output / "encoder-load.json", {"identity": identity, **load_receipt})
    text_model = getattr(getattr(base, "model", None), "language_model", None)
    if text_model is None:
        raise ValueError("full_teacher_encoder_requires_Qwen_text_backbone")
    device = base.get_input_embeddings().weight.device
    for entry in pending:
        key = entry["record_key"]
        if runner.file_sha(output / entry["supervision_file"]) != entry["supervision_sha256"]:
            raise ValueError("teacher_supervision_file_digest_changed")
        with safe_open(output / entry["supervision_file"], framework="pt", device="cpu") as stored:
            labels = stored.get_tensor("labels")
            active = stored.get_tensor("active")
        rounds = entry["rounds"]
        heads = torch.zeros((rounds, len(runner.PURPOSES), width), dtype=torch.bfloat16, device="cpu")
        started = time.perf_counter()
        source_dtypes = []
        with torch.inference_mode():
            for route, prefix in enumerate(entry["teacher_prefix_ids"]):
                length = entry["route_lengths_with_ending"][route]
                route_labels = labels[:length, route].tolist()
                if (route_labels[-1] != entry["ending_token_id"] or not active[:length, route].all()
                        or active[length:, route].any()):
                    raise ValueError("teacher_ending_or_active_alignment_changed")
                body_ids = route_labels[:-1]
                # Pre-target states predict each next label; post-target states
                # contain the current label, including the final EOS state.
                teacher_ids = prefix + (route_labels if state_position == "post_target" else body_ids)
                input_ids = torch.tensor([teacher_ids], dtype=torch.long, device=device)
                positions = torch.arange(len(teacher_ids), dtype=torch.long, device=device).unsqueeze(0)
                result = text_model(input_ids=input_ids, attention_mask=torch.ones_like(input_ids),
                    position_ids=positions, use_cache=False)
                start = len(prefix) - (state_position == "pre_target")
                aligned = result.last_hidden_state[0, start:start+length, :]
                if tuple(aligned.shape) != (length, width) or not torch.isfinite(aligned).all():
                    raise ValueError("full_teacher_state_shape_or_finiteness_invalid")
                source_dtypes.append(str(aligned.dtype))
                heads[:length, route] = aligned.detach().to(device="cpu", dtype=torch.bfloat16)
                del result, aligned, input_ids, positions
        if not torch.isfinite(heads).all() or torch.count_nonzero(heads[~active]).item() != 0:
            raise ValueError("teacher_padding_must_be_finite_exact_zero")
        target = output / entry["targets_file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        save_file({"teacher_heads": heads.contiguous(), "labels": labels.contiguous(), "active": active.contiguous()},
            str(temporary), metadata={"schema": SCHEMA, "identity": identity,
                "record_key": key, "encoding_sha256": entry["encoding_sha256"],
                "state_position": state_position,
                "origin": manifest["target_origin"], "after_EOS": "exact_zero_slots"})
        temporary.replace(target)
        receipt = {"status": "encoded", "identity": identity, "record_key": key,
            "state_position": state_position,
            "encoding_sha256": entry["encoding_sha256"] , "targets_file": entry["targets_file"],
            "targets_sha256": runner.file_sha(target), "teacher_heads_shape": list(heads.shape),
            "labels_shape": list(labels.shape), "active_shape": list(active.shape),
            "route_lengths_with_ending": entry["route_lengths_with_ending"],
            "source_hidden_dtypes": source_dtypes, "saved_hidden_dtype": "BF16",
            "inactive_slots_exact_zero": True, "EOS_round_active": True,
            "teacher_forcing_positions": manifest["binding"]["teacher_state_alignment"],
            "encoder_seconds": time.perf_counter() - started, "vocabulary_probabilities_computed": False,
            "training_performed": False}
        runner.write_json(target.with_suffix(".json"), receipt)
        receipts[key] = receipt
        manifest = _status_manifest(manifest, receipts, len(index["records"]))
        runner.write_json(output / "manifest.json", manifest)
        print(json.dumps({"record_key": key, "encoded_records": len(receipts),
            "teacher_heads_shape": receipt["teacher_heads_shape"], "encoder_seconds": receipt["encoder_seconds"]}), flush=True)
        del heads, labels, active
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "records", "heldout", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--train-limit", type=int, default=128)
    parser.add_argument("--state-position", choices=("pre_target", "post_target"), default="pre_target",
        help="Use states before predicting each path label or after consuming it, including EOS")
    parser.add_argument("--execute", action="store_true", help="Encode missing states using the frozen local model; never trains")
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()
    tokenizer, manifest, index, _ = prepare_teacher_states(args)
    if args.execute:
        manifest = encode_teacher_states(args, tokenizer, manifest, index)
    print(json.dumps({"identity": manifest["identity"], "records": manifest["records"],
        "encoded_status": manifest["encoded_status"], "encoded_records": manifest["encoded_records"],
        "records_require_encoding": manifest["records_require_encoding"],
        "training_performed": False}), flush=True)


if __name__ == "__main__":
    main()
