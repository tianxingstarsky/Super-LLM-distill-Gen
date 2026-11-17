"""One ordered HF Trainer epoch of differentiable shared-state latent feedback.

Default action prepares text/token identities only. Training is explicit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time
import traceback

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.nplus_shared_cache import runner

SYSTEM = "Solve the user's mathematical problem. Preserve its conditions and put the final answer in \\boxed{}."
DIRECTIONS = (
    "Derive what follows from the known facts and the actual question.",
    "Work backward from a proposed conclusion to its necessary premises; do not invent premises.",
    "Compare genuinely different mathematical solution methods when applicable.",
    "Check the exact constraints, definitions, units, boundary cases and required answer.",
    "Look for counterexamples, contradictions and unsupported approaches to exclude.")


def read_json(path):
    return json.loads(Path(path).read_text("utf-8"))


def jsonl_records(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def render_prefix(tokenizer, record, purpose=None):
    if purpose is None and "messages" in record:
        messages, _ = runner.normalize_messages(record["messages"])
    else:
        system = SYSTEM + ("\nYour analysis direction: " + DIRECTIONS[runner.PURPOSES.index(purpose)] if purpose else "")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": record["question"]}]
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=True)


def prepare_training(args):
    """Freeze source, token supervision, split, initial checkpoint, and target reuse."""
    from safetensors import safe_open
    from transformers import AutoTokenizer
    import numpy as np

    output, model = Path(args.output), Path(args.model)
    if args.epochs != 1 or args.latent_steps != 3:
        raise ValueError("this_experiment_requires_one_epoch_and_three_latent_steps")
    if min(args.train_limit, args.gradient_accumulation, args.save_steps, args.max_context, args.max_target_context) < 1:
        raise ValueError("positive_limits_required")
    if runner.file_sha(Path(args.initial_auxiliary)) != args.initial_sha256.lower():
        raise ValueError("initial_auxiliary_sha256_mismatch")
    with safe_open(args.initial_auxiliary, framework="pt", device="cpu") as handle:
        width = handle.get_slice("fusion.bias").get_shape()[0]
        rank = handle.get_slice("adapters.0.1.weight").get_shape()[0]
        initial_metadata = handle.metadata()
    configuration = read_json(model / "config.json")
    if width != configuration["text_config"]["hidden_size"] or rank != 16:
        raise ValueError("initial_auxiliary_width_or_rank_mismatch")
    if configuration["text_config"].get("attention_dropout", 0) != 0:
        raise ValueError("experiment_requires_zero_attention_dropout")
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
    if tokenizer.eos_token_id is None:
        raise ValueError("answer_eos_required")
    heldout = list(jsonl_records(args.heldout))
    heldout_hashes = {record["question_sha256"] for record in heldout}
    heldout_texts = {" ".join(record["question"].split()).casefold() for record in heldout}
    selected, encoded, excluded, reused_targets = [], [], [], {}
    seen = set()
    for record in jsonl_records(args.records):
        if record.get("split") != "train" or record["question_sha256"] in heldout_hashes or " ".join(record["question"].split()).casefold() in heldout_texts:
            raise ValueError("training_record_split_or_question_level_heldout_overlap")
        if record.get("structural_status") != "complete" or record.get("declared_invalid"):
            raise ValueError("incomplete_or_declared_invalid_teacher_record")
        if record.get("teacher_verification", {}).get("score") != 1:
            raise ValueError("teacher_final_requires_existing_verified_record")
        if set(record["paths"]) != set(runner.PURPOSES) or not all(isinstance(record["paths"][p], str) and record["paths"][p].strip() for p in runner.PURPOSES):
            raise ValueError("five_complete_teacher_paths_required")
        key = digest({"question": record["question"], "paths": record["paths"], "answer": record["final_answer"]})
        if record.get("record_key", key) != key:
            raise ValueError("teacher_record_key_changed")
        if key in seen:
            continue  # Only exact five-path/answer duplicates; retain distinct solutions.
        seen.add(key)
        prefix_text = render_prefix(tokenizer, record)
        prefix_ids = tokenizer.encode(prefix_text, add_special_tokens=False)
        closing_ids = tokenizer.encode(runner.BOUNDARY, add_special_tokens=False)
        answer_ids = tokenizer.encode(record["final_answer"], add_special_tokens=False)
        if not answer_ids:
            raise ValueError("empty_final_answer")
        answer_ids.append(tokenizer.eos_token_id)
        context = len(prefix_ids) + args.latent_steps + len(closing_ids) + len(answer_ids) - 1
        target_lengths = [len(tokenizer.encode(render_prefix(tokenizer, record, purpose), add_special_tokens=False))
            + len(tokenizer.encode(record["paths"][purpose], add_special_tokens=False)) for purpose in runner.PURPOSES]
        if context > args.max_context or max(target_lengths) > args.max_target_context:
            excluded.append({"record_key": key, "reason": "complete_context_exceeds_capacity_not_truncated",
                "training_context": context, "target_context_lengths": target_lengths})
            continue
        record = {**record, "record_key": key}
        selected.append(record)
        encoded.append({"record_key": key, "question_sha256": record["question_sha256"],
            "prefix_text": prefix_text, "prefix_ids": prefix_ids, "closing_ids": closing_ids,
            "answer_ids": answer_ids, "full_context_tokens": context})
        if args.teacher_targets:
            target_folder = Path(args.teacher_targets) / key
            target_file = target_folder / "targets.npz"
            origin_file = target_folder / "target-origin.json"
            if target_file.is_file() and origin_file.is_file():
                origin = read_json(origin_file)
                if origin.get("record_key") != key:
                    raise ValueError("legacy_teacher_target_record_binding_changed")
                with np.load(target_file) as stored:
                    target = stored["targets"]
                    if target.shape != (args.latent_steps, 5, width) or not np.isfinite(target).all():
                        raise ValueError("legacy_teacher_target_shape_or_finiteness_invalid")
                reused_targets[key] = {"targets_sha256": runner.file_sha(target_file),
                    "origin_sha256": runner.file_sha(origin_file), "legacy_binding_scope": "record_key_and_local_Qwen_proxy_origin"}
        if len(selected) >= args.train_limit:
            break
    if not selected:
        raise ValueError("no_complete_training_records_fit_capacity")
    binding = {"schema": "nplus-shared-state-training/v1", "records_sha256": runner.file_sha(Path(args.records)),
        "heldout_sha256": runner.file_sha(Path(args.heldout)), "initial_auxiliary_sha256": args.initial_sha256.lower(),
        "initial_metadata": initial_metadata, "model_config_sha256": runner.file_sha(model / "config.json"),
        "template_sha256": runner.file_sha(model / "chat_template.jinja"), "rank": rank, "width": width,
        "epochs": 1, "latent_steps": args.latent_steps, "learning_rate": args.learning_rate,
        "gradient_accumulation": args.gradient_accumulation, "direction_weight": args.direction_weight,
        "relative_direction_weight": .25, "max_context": args.max_context, "max_target_context": args.max_target_context,
        "seed": 42, "record_keys": [record["record_key"] for record in selected],
        "encoded_sha256": digest(encoded), "reused_targets": reused_targets,
        "source_code_sha256": {name: runner.file_sha(Path(__file__).parent / name) for name in ("training.py", "latent_feedback.py", "runner.py")}}
    manifest = {"identity": digest(binding), "binding": binding, "records": len(selected),
        "questions": len({record["question_sha256"] for record in selected}), "heldout_questions": len(heldout_hashes),
        "targets_reusable": len(reused_targets), "targets_require_local_encoding": len(selected) - len(reused_targets),
        "target_origin": "frozen_local_Qwen_encoding_of_teacher_text_not_DeepSeek_hidden",
        "supervision": "full_final_answer_and_EOS_CE_plus_direction_proxy_cosine",
        "full_native_CoT_or_per_head_token_decoding_claim": False, "training_performed_by_prepare": False}
    previous = output / "manifest.json"
    if previous.exists() and read_json(previous)["identity"] != manifest["identity"]:
        raise ValueError("training_identity_changed_use_new_output")
    output.mkdir(parents=True, exist_ok=True)
    for filename, source in (("records-source.jsonl", args.records), ("heldout.jsonl", args.heldout)):
        destination = output / filename
        if destination.exists() and runner.file_sha(destination) != runner.file_sha(Path(source)):
            raise ValueError("frozen_source_bytes_changed")
        if not destination.exists():
            shutil.copyfile(source, destination)
    (output / "selected-records.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in selected), encoding="utf-8")
    runner.write_json(output / "encoding-exclusions.json", excluded)
    runner.write_json(output / "encoded-training-index.json", encoded)
    runner.write_json(previous, manifest)
    return tokenizer, manifest, selected, encoded


def prepare_teacher_targets(base, tokenizer, args, manifest, records):
    """Reuse existing local targets or encode five full teacher texts locally."""
    import numpy as np
    import torch

    output = Path(args.output)
    device = base.get_input_embeddings().weight.device
    base.eval()
    for index, record in enumerate(records):
        key = record["record_key"]
        folder = output / "teacher-targets" / key
        target_file = folder / "targets.npz"
        if target_file.exists():
            origin = read_json(folder / "target-origin.json")
            if origin.get("identity") != manifest["identity"] or runner.file_sha(target_file) != origin["targets_sha256"]:
                raise ValueError("persisted_target_binding_changed")
            continue
        folder.mkdir(parents=True, exist_ok=True)
        reused = manifest["binding"]["reused_targets"].get(key)
        if reused:
            source = Path(args.teacher_targets) / key / "targets.npz"
            if runner.file_sha(source) != reused["targets_sha256"]:
                raise ValueError("legacy_target_changed_after_prepare")
            shutil.copyfile(source, target_file)
        else:
            directions = []
            with torch.no_grad():
                for purpose in runner.PURPOSES:
                    prefix = tokenizer.encode(render_prefix(tokenizer, record, purpose), add_special_tokens=False)
                    thought = tokenizer.encode(record["paths"][purpose], add_special_tokens=False)
                    ids = torch.tensor([prefix + thought], dtype=torch.long, device=device)
                    hidden = base.model.language_model(input_ids=ids, use_cache=False).last_hidden_state[0]
                    quantiles = []
                    for step in range(args.latent_steps):
                        end = len(prefix) + max(1, math.ceil(len(thought) * (step + 1) / args.latent_steps))
                        quantiles.append(hidden[max(len(prefix), end - 16):end].float().mean(0).cpu().numpy())
                    directions.append(np.stack(quantiles))
                    del hidden
            temporary = target_file.with_suffix(".tmp")
            with temporary.open("wb") as handle:
                np.savez_compressed(handle, targets=np.stack(directions, axis=1).astype(np.float32))
            temporary.replace(target_file)
        runner.write_json(folder / "target-origin.json", {"identity": manifest["identity"], "record_key": key,
            "targets_sha256": runner.file_sha(target_file), "reused_existing_target": bool(reused),
            "origin": manifest["target_origin"], "pooling": "last_16_hidden_mean_at_three_teacher_token_quantiles",
            "teacher_answer_in_student_input": False, "all_student_hidden_computed_online": True})
        runner.write_json(output / "target-progress.json", {"completed_records": index + 1, "total_records": len(records)})


def build_training_model(base, auxiliary, *, args, identity):
    import torch
    from safetensors.torch import load_file, save_file
    from experiments.nplus_shared_cache.latent_feedback import project_shared_hidden

    class SharedLoopTrainingModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.base, self.aux = base, auxiliary
            self.config = base.config
            self.main_input_name = "prefix_ids"
            self.accepts_loss_kwargs = False
            self.loss_components = {}

        def activation_checkpointing_enable(self, **kwargs):
            self.base.activation_checkpointing_enable(**kwargs)

        def gradient_checkpointing_enable(self, **kwargs):
            self.base.gradient_checkpointing_enable(**kwargs)

        def save_auxiliary(self, folder):
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            save_file({name: value.detach().float().cpu().contiguous() for name, value in self.aux.state_dict().items()},
                str(folder / "auxiliary.safetensors"), metadata={"format": "pt", "identity": identity,
                    "architecture": "differentiable_shared_state_latent_feedback/v1"})
            runner.write_json(folder / "auxiliary-config.json", {"identity": identity,
                "architecture": "differentiable_shared_state_latent_feedback/v1", "width": self.aux.width,
                "rank": self.aux.rank, "latent_steps": args.latent_steps})

        def load_auxiliary(self, folder):
            if read_json(Path(folder) / "auxiliary-config.json")["identity"] != identity:
                raise ValueError("checkpoint_training_identity_changed")
            self.aux.load_state_dict(load_file(str(Path(folder) / "auxiliary.safetensors"), device=str(next(self.aux.parameters()).device)), strict=True)

        def forward(self, prefix_ids, closing_ids, answer_ids, teacher_targets, **kwargs):
            embedding = self.base.get_input_embeddings()
            prefix = embedding(prefix_ids)
            reference_rms = prefix.float().square().mean().sqrt()
            realized = prefix
            hidden = self.base.model.language_model(inputs_embeds=realized,
                attention_mask=torch.ones(realized.shape[:2], dtype=torch.long, device=realized.device), use_cache=False).last_hidden_state[:, -1]
            latents, feedback_hidden, adapted = [], [], []
            for step in range(args.latent_steps):
                heads, _, _, latent = project_shared_hidden(self.aux, hidden, reference_rms, prefix.dtype)
                adapted.append(heads)
                latents.append(latent)
                realized = torch.cat((realized, latent), dim=1)
                if step + 1 < args.latent_steps:
                    # Every new h is recomputed from actual earlier latent inputs.
                    # No cached/detached question features enter this computation.
                    hidden = self.base.model.language_model(inputs_embeds=realized,
                        attention_mask=torch.ones(realized.shape[:2], dtype=torch.long, device=realized.device),
                        use_cache=False).last_hidden_state[:, -1]
                    feedback_hidden.append(hidden)
            closing = embedding(closing_ids)
            answer_prefix = embedding(answer_ids[:, :-1])
            full_inputs = torch.cat((realized, closing, answer_prefix), dim=1)
            answer_start = realized.shape[1] + closing.shape[1]
            positions = torch.arange(answer_ids.shape[1], device=prefix.device) + answer_start - 1
            self.base.model.rope_deltas = None
            answer = self.base(inputs_embeds=full_inputs,
                attention_mask=torch.ones(full_inputs.shape[:2], dtype=torch.long, device=prefix.device),
                labels=torch.full(full_inputs.shape[:2], -100, dtype=torch.long, device=prefix.device),
                logits_to_keep=positions, shift_labels=answer_ids, use_cache=False)
            answer_ce = answer.loss
            heads = torch.stack(adapted, dim=1)
            targets = teacher_targets.float()
            alignment = (1 - torch.nn.functional.cosine_similarity(heads.float(), targets, dim=-1)).mean()
            centered = heads.float() - heads.float().mean(dim=2, keepdim=True)
            target_centered = targets - targets.mean(dim=2, keepdim=True)
            relative = (1 - torch.nn.functional.cosine_similarity(centered, target_centered, dim=-1, eps=1e-6)).mean()
            loss = answer_ce + args.direction_weight * (alignment + .25 * relative)
            evidence_file = Path(args.output) / "training" / "final-ce-gradient-evidence.json"
            if self.training and not evidence_file.exists():
                groups = self.aux.parameter_groups()
                parameters = [parameter for values in groups.values() for parameter in values]
                watched = latents + feedback_hidden + parameters
                gradients = torch.autograd.grad(answer_ce, watched, retain_graph=True, allow_unused=True)
                norms = [float(value.detach().float().norm()) if value is not None else None for value in gradients]
                offset = len(latents) + len(feedback_hidden)
                group_norms = {}
                for name, values in groups.items():
                    chunk = norms[offset:offset + len(values)]
                    group_norms[name] = math.sqrt(sum(value * value for value in chunk if value is not None))
                    offset += len(values)
                probe = torch.linspace(1, 2, latents[1].numel(), device=prefix.device).reshape_as(latents[1])
                recurrence_gradient = torch.autograd.grad((latents[1].float() * probe).sum(), latents[0], retain_graph=True)[0]
                recurrence_norm = float(recurrence_gradient.detach().float().norm())
                evidence = {"identity": identity, "loss_source": "final_answer_CE_only_no_direction_loss",
                    "latent_input_gradient_l2": norms[:len(latents)],
                    "feedback_hidden_gradient_l2": norms[len(latents):len(latents) + len(feedback_hidden)],
                    "parameter_group_gradient_l2": group_norms, "z2_fixed_probe_to_z1_vjp_l2": recurrence_norm,
                    "extra_optimizer_steps": 0, "answer_ce": float(answer_ce.detach())}
                runner.write_json(evidence_file, evidence)
                required = [*norms[:len(latents) + len(feedback_hidden)], *[group_norms[name] for name in (*runner.PURPOSES, "fusion")], recurrence_norm]
                if not all(value is not None and math.isfinite(value) and value > 0 for value in required):
                    raise RuntimeError("final_answer_CE_or_recursive_feedback_gradient_disconnected")
                del gradients, recurrence_gradient
            self.loss_components = {"answer_ce": float(answer_ce.detach()), "direction_alignment": float(alignment.detach()),
                "relative_alignment": float(relative.detach()), "total_loss": float(loss.detach())}
            return {"loss": loss}

    return SharedLoopTrainingModel()


def train_one_epoch(args, tokenizer, manifest, records, encoded):
    import numpy as np
    import torch
    from torch.utils.data import Dataset, SequentialSampler
    from transformers import Trainer, TrainerCallback, TrainingArguments, set_seed
    from transformers.trainer_utils import get_last_checkpoint
    from experiments.nplus_shared_cache.latent_feedback import load_auxiliary

    output, training = Path(args.output), Path(args.output) / "training"
    training.mkdir(parents=True, exist_ok=True)
    completed = training / "training-receipt.json"
    if completed.exists():
        receipt = read_json(completed)
        if receipt["identity"] != manifest["identity"] or runner.file_sha(training / "auxiliary.safetensors") != receipt["auxiliary_file_sha256"]:
            raise ValueError("completed_training_binding_changed")
        return receipt
    checkpoint = get_last_checkpoint(str(training))
    checkpoint_step = 0
    if checkpoint:
        binding = read_json(Path(checkpoint) / "binding.json")
        if binding["identity"] != manifest["identity"] or binding["epoch"] > 1.000001:
            raise ValueError("checkpoint_identity_or_epoch_invalid")
        if runner.file_sha(Path(checkpoint) / "auxiliary.safetensors") != binding["auxiliary_file_sha256"]:
            raise ValueError("checkpoint_auxiliary_changed")
        checkpoint_step = binding["global_step"]
    if (training / "progress.json").exists() and read_json(training / "progress.json")["global_step"] > checkpoint_step:
        raise ValueError("observed_progress_newer_than_checkpoint_no_replay")
    if (training / "started.json").exists() and not checkpoint:
        raise ValueError("interrupted_training_without_checkpoint_no_replay")
    set_seed(42)
    base, load_receipt = runner.load_frozen_model(args.model)
    runner.write_json(output / "model-load.json", load_receipt)
    prepare_teacher_targets(base, tokenizer, args, manifest, records)
    auxiliary = load_auxiliary(args.initial_auxiliary, width=manifest["binding"]["width"],
        device=str(base.get_input_embeddings().weight.device), trainable=True)
    student = build_training_model(base, auxiliary, args=args, identity=manifest["identity"])
    base.config.use_cache = False

    class Records(Dataset):
        def __len__(self):
            return len(encoded)

        def __getitem__(self, index):
            record = encoded[index]
            with np.load(output / "teacher-targets" / record["record_key"] / "targets.npz") as stored:
                targets = torch.from_numpy(stored["targets"].copy())
            return {"teacher_targets": targets, **{name: torch.tensor(record[name], dtype=torch.long)
                for name in ("prefix_ids", "closing_ids", "answer_ids")}}

    def collate(examples):
        if len(examples) != 1:
            raise ValueError("shared_loop_requires_microbatch_one")
        return {name: value.unsqueeze(0) for name, value in examples[0].items()}

    class AuxiliaryTrainer(Trainer):
        def _get_train_sampler(self, train_dataset=None):
            return SequentialSampler(self.train_dataset)

        def _save(self, output_dir=None, state_dict=None):
            self.model.save_auxiliary(output_dir or self.args.output_dir)

        def _load_from_checkpoint(self, resume_from_checkpoint, model=None):
            (self.model if model is None else model).load_auxiliary(resume_from_checkpoint)

    snapshots = {name: [parameter.detach().float().cpu().clone() for parameter in values]
        for name, values in auxiliary.parameter_groups().items()}

    class Evidence(TrainerCallback):
        def on_pre_optimizer_step(self, arguments, state, control, **kwargs):
            groups = auxiliary.parameter_groups()
            norms = {name: math.sqrt(sum(float(parameter.grad.detach().float().square().sum())
                for parameter in values if parameter.grad is not None)) for name, values in groups.items()}
            runner.write_json(training / "latest-gradient-evidence.json", {"step": state.global_step + 1,
                "loss_source": "training_total_loss", "parameter_group_gradient_l2": norms})

        def on_optimizer_step(self, arguments, state, control, **kwargs):
            if not (training / "first-update-evidence.json").exists():
                deltas = {name: math.sqrt(sum(float((parameter.detach().float().cpu() - old).square().sum())
                    for parameter, old in zip(values, snapshots[name]))) for name, values in auxiliary.parameter_groups().items()}
                runner.write_json(training / "first-update-evidence.json", {"step": state.global_step + 1,
                    "parameter_delta_l2": deltas, "all_five_heads_and_fusion_changed": all(deltas[name] > 0 for name in (*runner.PURPOSES, "fusion"))})

        def on_step_end(self, arguments, state, control, **kwargs):
            runner.write_json(training / "progress.json", {"identity": manifest["identity"],
                "global_step": state.global_step, "epoch": state.epoch, "loss_components": student.loss_components})

        def on_save(self, arguments, state, control, **kwargs):
            folder = training / f"checkpoint-{state.global_step}"
            runner.write_json(folder / "binding.json", {"identity": manifest["identity"], "epoch": state.epoch,
                "global_step": state.global_step, "auxiliary_file_sha256": runner.file_sha(folder / "auxiliary.safetensors")})

    arguments = TrainingArguments(output_dir=str(training), num_train_epochs=1,
        per_device_train_batch_size=1, gradient_accumulation_steps=args.gradient_accumulation,
        learning_rate=args.learning_rate, lr_scheduler_type="constant", warmup_steps=0,
        weight_decay=0, max_grad_norm=1, optim="adamw_torch", bf16=True,
        activation_checkpointing=True, activation_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=1, logging_nan_inf_filter=False, save_strategy="steps", save_steps=args.save_steps,
        save_total_limit=2, report_to="none", dataloader_num_workers=0, remove_unused_columns=False,
        seed=42, data_seed=42)
    trainer = AuxiliaryTrainer(model=student, args=arguments, train_dataset=Records(),
        data_collator=collate, processing_class=tokenizer, callbacks=[Evidence()])
    runner.write_json(training / "started.json", {"identity": manifest["identity"], "resume_step": checkpoint_step,
        "requested_epochs": 1, "initial_auxiliary_sha256": args.initial_sha256})
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    try:
        result = trainer.train(resume_from_checkpoint=checkpoint)
        if trainer.state.epoch > 1.000001:
            raise RuntimeError("one_epoch_limit_exceeded")
        trainer.save_model(str(training))
        trainer.save_state()
        update = read_json(training / "first-update-evidence.json")
        if not update["all_five_heads_and_fusion_changed"]:
            raise RuntimeError("direction_or_fusion_failed_to_update")
        torch.cuda.synchronize()
        receipt = {"identity": manifest["identity"], "status": "completed", "actual_epoch": trainer.state.epoch,
            "global_steps": trainer.state.global_step, "train_records": len(encoded), "train_questions": manifest["questions"],
            "seconds": time.perf_counter() - started, "base_trainable_parameters": sum(p.numel() for p in base.parameters() if p.requires_grad),
            "auxiliary_trainable_parameters": sum(p.numel() for p in auxiliary.parameters() if p.requires_grad),
            "auxiliary_file_sha256": runner.file_sha(training / "auxiliary.safetensors"),
            "initial_auxiliary_sha256": args.initial_sha256, "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(),
            "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(), "metrics": result.metrics,
            "loss_history": trainer.state.log_history, "first_update": update,
            "final_ce_only_gradient_evidence": read_json(training / "final-ce-gradient-evidence.json"),
            "all_student_hidden_computed_online": True, "backprop_through_realized_feedback": True,
            "training_uses_cache": False, "ordered_single_pass": True, "paid_model_calls": 0,
            "not_100000_completed": True, "not_full_CoT_token_supervision": True}
        runner.write_json(completed, receipt)
        return receipt
    except Exception:
        runner.write_json(training / "failure.json", {"identity": manifest["identity"],
            "global_step": trainer.state.global_step, "epoch": trainer.state.epoch, "traceback": traceback.format_exc()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("prepare", "train"), default="prepare")
    for name in ("model", "records", "heldout", "initial-auxiliary", "initial-sha256", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--teacher-targets", help="Optional read-only directory of compatible local-Qwen targets")
    parser.add_argument("--train-limit", type=int, default=128)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--latent-steps", type=int, default=3)
    parser.add_argument("--gradient-accumulation", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--direction-weight", type=float, default=.2)
    parser.add_argument("--save-steps", type=int, default=4)
    parser.add_argument("--max-context", type=int, default=8192)
    parser.add_argument("--max-target-context", type=int, default=8192)
    args = parser.parse_args()
    tokenizer, manifest, records, encoded = prepare_training(args)
    print(json.dumps({"identity": manifest["identity"], "records": manifest["records"],
        "targets_reusable": manifest["targets_reusable"], "targets_require_local_encoding": manifest["targets_require_local_encoding"]}), flush=True)
    if args.action == "train":
        print(json.dumps(train_one_epoch(args, tokenizer, manifest, records, encoded), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
