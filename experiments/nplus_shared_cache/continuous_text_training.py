"""Train five readable continuous heads with frozen, teacher-forced latent inputs.

No discrete token embedding feedback. Default action validates prepared local
targets only; training is explicit and limited to one ordered epoch.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
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

ARCHITECTURE = "teacher_forced_continuous_five_route/v2"
POST_ARCHITECTURE = "teacher_forced_continuous_five_route/v3"


def read_json(path):
    return json.loads(Path(path).read_text("utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def target_path(prepared, record):
    root = Path(prepared).resolve()
    path = (root / record["targets_file"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("targets_file_must_stay_inside_prepared_directory")
    return path


def prepare(args):
    """Authenticate complete prepared targets without loading backbone weights."""
    import torch
    from safetensors import safe_open
    from transformers import AutoTokenizer

    prepared, output, model = Path(args.prepared), Path(args.output), Path(args.model)
    provider = read_json(prepared / "manifest.json")
    provider_binding = provider["binding"]
    state_position = provider_binding.get("state_position", "pre_target")
    if state_position not in {"pre_target", "post_target"}:
        raise ValueError("prepared_state_position_must_be_pre_target_or_post_target")
    architecture = POST_ARCHITECTURE if state_position == "post_target" else ARCHITECTURE
    if runner.file_sha(Path(args.initial_auxiliary)) != args.initial_sha256.lower():
        raise ValueError("initial_auxiliary_sha256_mismatch")
    for key, filename in (("model_config_sha256", "config.json"), ("template_sha256", "chat_template.jinja")):
        if provider_binding[key] != runner.file_sha(model / filename):
            raise ValueError("prepared_encoder_model_config_or_template_changed")
    if not isinstance(provider_binding["model_weights_sha256"], dict) or not provider_binding["model_weights_sha256"]:
        raise ValueError("prepared_base_weight_digest_binding_required")
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
    configuration = read_json(model / "config.json")
    width = configuration.get("text_config", configuration)["hidden_size"]
    if configuration.get("text_config", configuration).get("attention_dropout", 0) != 0:
        raise ValueError("zero_backbone_attention_dropout_required")
    ending_id = provider_binding["ending_token_id"]
    if not isinstance(ending_id, int) or not 0 <= ending_id < len(tokenizer):
        raise ValueError("invalid_prepared_ending_token")
    with safe_open(args.initial_auxiliary, framework="pt", device="cpu") as handle:
        if handle.get_slice("fusion.bias").get_shape() != [width]:
            raise ValueError("initial_auxiliary_width_mismatch")
        rank = handle.get_slice("adapters.0.1.weight").get_shape()[0]
        initial_metadata = handle.metadata()
    records = read_json(prepared / "index.json")["records"]
    if not records or min(args.head_learning_rate, args.fusion_learning_rate) <= 0 or min(args.gradient_accumulation, args.save_steps, args.ce_chunk_tokens) < 1:
        raise ValueError("nonempty_dataset_and_positive_training_configuration_required")
    target_bindings, seen = [], set()
    generation = read_json(model / "generation_config.json")
    answer_eos = generation.get("eos_token_id", tokenizer.eos_token_id)
    answer_eos = [answer_eos] if isinstance(answer_eos, int) else list(answer_eos)
    for record in records:
        key = record["record_key"]
        if key in seen:
            raise ValueError("duplicate_prepared_training_record")
        seen.add(key)
        lengths = record["route_lengths_with_ending"]
        if len(lengths) != 5 or any(not isinstance(length, int) or length < 1 for length in lengths):
            raise ValueError("five_complete_route_lengths_required")
        rounds = max(lengths)
        if not record["prefix_ids"] or not record["closing_ids"] or not record["answer_ids"] or record["answer_ids"][-1] not in answer_eos:
            raise ValueError("complete_prefix_boundary_and_answer_EOS_required")
        full_length = len(record["prefix_ids"]) + rounds + len(record["closing_ids"]) + len(record["answer_ids"]) - 1
        if full_length > args.max_context:
            raise ValueError("complete_prepared_context_exceeds_capacity_no_truncation:" + key)
        path = target_path(prepared, record)
        with safe_open(path, framework="pt", device="cpu") as handle:
            if handle.get_slice("teacher_heads").get_shape() != [rounds, 5, width]:
                raise ValueError("prepared_teacher_heads_shape_mismatch")
            labels, active = handle.get_tensor("labels"), handle.get_tensor("active").bool()
            if tuple(labels.shape) != (rounds, 5) or tuple(active.shape) != (rounds, 5):
                raise ValueError("prepared_labels_or_active_shape_mismatch")
            expected_active = torch.arange(rounds).unsqueeze(1) < torch.tensor(lengths).unsqueeze(0)
            if not torch.equal(active, expected_active) or not torch.equal(labels != -100, active):
                raise ValueError("prepared_ending_round_and_padding_contract_mismatch")
            for route, length in enumerate(lengths):
                if labels[length - 1, route].item() != ending_id or (labels[:length - 1, route] == ending_id).any():
                    raise ValueError("each_route_requires_exactly_one_supervised_ending")
        target_bindings.append({"record_key": key, "targets_file": record["targets_file"],
            "sha256": runner.file_sha(path), "rounds": rounds, "full_context": full_length})
    binding = {"schema": architecture, "state_position": state_position, "prepared_identity": provider["identity"],
        "prepared_manifest_sha256": runner.file_sha(prepared / "manifest.json"),
        "prepared_index_sha256": runner.file_sha(prepared / "index.json"),
        **{key: provider_binding[key] for key in ("model_config_sha256", "model_weights_sha256", "template_sha256", "ending_token_id")},
        "initial_auxiliary_sha256": args.initial_sha256.lower(), "initial_metadata": initial_metadata,
        "width": width, "rank": rank, "epochs": 1, "microbatch": 1,
        "gradient_accumulation": args.gradient_accumulation, "head_learning_rate": args.head_learning_rate,
        "fusion_learning_rate": args.fusion_learning_rate, "hidden_alignment_weight": args.alignment_weight,
        "hidden_alignment_metric": "squared_L2_of_unit_normalized_hidden",
        "ce_chunk_tokens": args.ce_chunk_tokens, "max_context": args.max_context, "seed": 42,
        "targets": target_bindings, "source_sha256": runner.file_sha(Path(__file__)),
        "feedback_source_sha256": runner.file_sha(Path(__file__).with_name("latent_feedback.py")),
        "base_loader_source_sha256": hashlib.sha256(inspect.getsource(runner.load_frozen_model).encode()).hexdigest()}
    manifest = {"identity": digest(binding), "binding": binding, "records": len(records),
        "target_origin": "frozen_local_Qwen_per_token_teacher_hidden_not_DeepSeek_hidden",
        "feedback": "continuous_head_vectors", "teacher_forcing_inputs": "gold_continuous_teacher_heads",
        "state_position": state_position,
        "observation": "current_token_from_post_state" if state_position == "post_target" else "native_next_token_lm_head",
        "text_supervision": "full_per_route_tokens_and_EOS", "full_free_rollout_BPTT": False,
        "loaded_backbone_weights": False, "base_weight_hashes_reused_from_frozen_provider": True}
    old = output / "manifest.json"
    if old.exists() and read_json(old)["identity"] != manifest["identity"]:
        raise ValueError("training_identity_changed_use_new_output")
    output.mkdir(parents=True, exist_ok=True)
    for source, name in ((prepared / "manifest.json", "prepared-manifest.json"), (prepared / "index.json", "prepared-index.json")):
        destination = output / name
        if destination.exists() and runner.file_sha(destination) != runner.file_sha(source):
            raise ValueError("prepared_snapshot_changed")
        if not destination.exists():
            shutil.copyfile(source, destination)
    runner.write_json(old, manifest)
    return tokenizer, manifest, records


def build_model(base, auxiliary, args, manifest):
    import torch
    import torch.nn.functional as functional
    from torch.utils.checkpoint import checkpoint
    from safetensors.torch import load_file, save_file

    identity, binding = manifest["identity"], manifest["binding"]
    architecture = binding["schema"]
    is_post = binding["state_position"] == "post_target"

    class ContinuousTextModel(torch.nn.Module):
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
                str(folder / "auxiliary.safetensors"), metadata={"format": "pt", "identity": identity, "architecture": architecture})
            runner.write_json(folder / "auxiliary-config.json", {"identity": identity, "architecture": architecture,
                "width": self.aux.width, "rank": self.aux.rank, "feedback": "continuous_head_vectors",
                "text_supervision": "full_per_route_tokens_and_EOS",
                "state_position": binding["state_position"], "observation": manifest["observation"],
                **{key: binding[key] for key in ("ending_token_id", "model_config_sha256", "template_sha256", "model_weights_sha256")}})

        def load_auxiliary(self, folder):
            config = read_json(Path(folder) / "auxiliary-config.json")
            if config["identity"] != identity or config["architecture"] != architecture:
                raise ValueError("checkpoint_identity_or_architecture_changed")
            self.aux.load_state_dict(load_file(str(Path(folder) / "auxiliary.safetensors"), device=str(next(self.aux.parameters()).device)), strict=True)

        def route_chunk_loss(self, hidden, target_hidden, labels, adapter):
            # The module is an explicit argument, not a late-bound loop closure.
            with torch.autocast(device_type=hidden.device.type, enabled=False):
                predicted = hidden.float() + adapter(hidden.float())
                alignment = (functional.normalize(predicted, dim=-1) - functional.normalize(target_hidden.float(), dim=-1)).square().sum(-1).sum()
                observed = self.aux.observation(predicted) if is_post else predicted
                gold_observed = self.aux.observation(target_hidden.float()) if is_post else None
            logits = self.base.lm_head(observed.to(self.base.lm_head.weight.dtype)).float()
            losses = functional.cross_entropy(logits, labels, reduction="none")
            gold_loss = logits.new_zeros(())
            gold_ending_loss = logits.new_zeros(())
            if is_post:
                gold_logits = self.base.lm_head(gold_observed.to(self.base.lm_head.weight.dtype)).float()
                gold_losses = functional.cross_entropy(gold_logits, labels, reduction="none")
                gold_loss = gold_losses.sum()
                gold_ending_loss = gold_losses[labels == binding["ending_token_id"]].sum()
            return losses.sum(), alignment, losses[labels == binding["ending_token_id"]].sum(), gold_loss, gold_ending_loss

        def answer_chunk_loss(self, hidden, labels):
            logits = self.base.lm_head(hidden.to(self.base.lm_head.weight.dtype)).float()
            return functional.cross_entropy(logits, labels, reduction="sum")

        def forward(self, prefix_ids, closing_ids, answer_ids, teacher_heads, labels, active, **kwargs):
            prefix = self.base.get_input_embeddings()(prefix_ids)
            rounds = teacher_heads.shape[1]
            with torch.autocast(device_type=prefix.device.type, enabled=False):
                gold_heads = teacher_heads.float() * active.unsqueeze(-1)
                fused = self.aux.fusion(gold_heads.flatten(-2))
                reference_rms = prefix.float().square().mean().sqrt()
                z = (fused * reference_rms / fused.square().mean(-1, keepdim=True).sqrt().clamp_min(1e-8) * self.aux.gain).to(prefix.dtype)
            closing = self.base.get_input_embeddings()(closing_ids)
            inputs = torch.cat((prefix, z, closing, self.base.get_input_embeddings()(answer_ids[:, :-1])), dim=1)
            self.base.model.rope_deltas = None
            hidden = self.base.model.language_model(inputs_embeds=inputs,
                attention_mask=torch.ones(inputs.shape[:2], dtype=torch.long, device=inputs.device), use_cache=False).last_hidden_state
            # z_t is at P+t; y_t must be read BEFORE z_t, at P-1+t.
            route_hidden = hidden[0, prefix.shape[1] - 1:prefix.shape[1] - 1 + rounds]
            route_losses, alignments, eos_losses, gold_losses, gold_eos_losses, counts = [], [], [], [], [], []
            for route, adapter in enumerate(self.aux.adapters):
                indices = active[0, :, route].nonzero(as_tuple=False).flatten()
                sums, align_sums, eos_sums, gold_sums, gold_eos_sums = [], [], [], [], []
                for start in range(0, indices.numel(), args.ce_chunk_tokens):
                    selected = indices[start:start + args.ce_chunk_tokens]
                    ce, mse, eos_ce, gold_ce, gold_eos_ce = checkpoint(self.route_chunk_loss, route_hidden[selected],
                        teacher_heads[0, selected, route], labels[0, selected, route], adapter, use_reentrant=False)
                    sums.append(ce)
                    align_sums.append(mse)
                    eos_sums.append(eos_ce)
                    gold_sums.append(gold_ce)
                    gold_eos_sums.append(gold_eos_ce)
                route_losses.append(torch.stack(sums).sum())
                alignments.append(torch.stack(align_sums).sum())
                eos_losses.append(torch.stack(eos_sums).sum())
                gold_losses.append(torch.stack(gold_sums).sum())
                gold_eos_losses.append(torch.stack(gold_eos_sums).sum())
                counts.append(int(indices.numel()))
            valid = sum(counts)
            token_ce = torch.stack(route_losses).sum() / valid
            teacher_decoder_ce = torch.stack(gold_losses).sum() / valid
            alignment = torch.stack(alignments).sum() / valid
            answer_start = prefix.shape[1] + rounds + closing.shape[1] - 1
            answer_hidden = hidden[0, answer_start:answer_start + answer_ids.shape[1]]
            answer_sums = [checkpoint(self.answer_chunk_loss, answer_hidden[start:start + args.ce_chunk_tokens],
                answer_ids[0, start:start + args.ce_chunk_tokens], use_reentrant=False)
                for start in range(0, answer_ids.shape[1], args.ce_chunk_tokens)]
            answer_ce = torch.stack(answer_sums).sum() / answer_ids.shape[1]
            loss = token_ce + answer_ce + args.alignment_weight * alignment + teacher_decoder_ce
            evidence_path = Path(args.output) / "training" / "objective-gradient-evidence.json"
            if self.training and not evidence_path.exists():
                head_parameters = [parameter for adapter in self.aux.adapters for parameter in adapter.parameters()]
                token_grads = torch.autograd.grad(token_ce, head_parameters, retain_graph=True, allow_unused=True)
                head_norms, offset = {}, 0
                for purpose, adapter in zip(runner.PURPOSES, self.aux.adapters):
                    size = len(list(adapter.parameters()))
                    head_norms[purpose] = math.sqrt(sum(float(value.detach().float().square().sum())
                        for value in token_grads[offset:offset + size] if value is not None))
                    offset += size
                observation_gradients = None
                observation_norm = None
                if is_post:
                    observation_gradients = torch.autograd.grad(teacher_decoder_ce,
                        list(self.aux.observation.parameters()), retain_graph=True, allow_unused=True)
                    observation_norm = math.sqrt(sum(float(value.detach().float().square().sum())
                        for value in observation_gradients if value is not None))
                answer_parameters = [*self.aux.fusion.parameters(), self.aux.gain, z]
                answer_grads = torch.autograd.grad(answer_ce, answer_parameters, retain_graph=True, allow_unused=True)
                answer_norms = [float(value.detach().float().norm()) if value is not None else None for value in answer_grads]
                first_z = float(answer_grads[-1][0, 0].detach().float().norm()) if answer_grads[-1] is not None else None
                first_token_ce, _, _, _, _ = self.route_chunk_loss(route_hidden[:1], teacher_heads[0, :1, 0],
                    labels[0, :1, 0], self.aux.adapters[0])
                future_gradient = torch.autograd.grad(first_token_ce, z, retain_graph=True, allow_unused=True)[0]
                causality = {"first_route_first_token_to_all_current_and_future_gold_z_l2":
                    float(future_gradient.detach().float().norm()) if future_gradient is not None else 0.0,
                    "max_abs": float(future_gradient.detach().float().abs().max()) if future_gradient is not None else 0.0,
                    "nonzero_elements": int(torch.count_nonzero(future_gradient)) if future_gradient is not None else 0}
                evidence = {"identity": identity, "token_CE_only_head_gradient_l2": head_norms,
                    "gold_decoder_CE_only_observation_gradient_l2": observation_norm,
                    "answer_CE_only_fusion_weight_bias_gain_z_gradient_l2": answer_norms,
                    "answer_CE_only_first_gold_z_gradient_l2": first_z,
                    "answer_CE_to_heads_expected_disconnected_under_teacher_forcing": True,
                    "first_round_gold_feedback_causality": causality,
                    "extra_optimizer_steps": 0, "full_free_rollout_BPTT": False}
                runner.write_json(evidence_path, evidence)
                required = [*head_norms.values(), *answer_norms[:3], first_z]
                if is_post:
                    required.append(observation_norm)
                if not all(value is not None and math.isfinite(value) and value > 0 for value in required):
                    raise RuntimeError("continuous_text_objective_gradient_disconnected")
                if causality["nonzero_elements"]:
                    raise RuntimeError("first_route_token_reads_current_or_future_gold_feedback")
                del token_grads, answer_grads, future_gradient, observation_gradients
            self.loss_components = {"token_ce": float(token_ce.detach()), "answer_ce": float(answer_ce.detach()),
                "teacher_decoder_ce": float(teacher_decoder_ce.detach()),
                "normalized_hidden_squared_l2": float(alignment.detach()), "total_loss": float(loss.detach()),
                "per_route_token_ce": {purpose: float(value.detach()) / count for purpose, value, count in zip(runner.PURPOSES, route_losses, counts)},
                "per_route_ending_ce": {purpose: float(value.detach()) for purpose, value in zip(runner.PURPOSES, eos_losses)},
                "per_route_teacher_decoder_ce": {purpose: float(value.detach()) / count for purpose, value, count in zip(runner.PURPOSES, gold_losses, counts)},
                "per_route_teacher_decoder_ending_ce": {purpose: float(value.detach()) for purpose, value in zip(runner.PURPOSES, gold_eos_losses)},
                "supervised_route_tokens_including_endings": valid, "answer_tokens_including_EOS": answer_ids.shape[1]}
            if self.training:
                record_index = int(kwargs["record_index"].reshape(-1)[0])
                with (Path(args.output) / "training" / "forward-loss-history.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"identity": identity, "record_index": record_index,
                        "loss_components": self.loss_components}, ensure_ascii=False) + "\n")
            return {"loss": loss}

    return ContinuousTextModel()


def train(args, tokenizer, manifest, records):
    import torch
    from torch.utils.data import Dataset, SequentialSampler
    from safetensors.torch import load_file
    from transformers import Trainer, TrainerCallback, TrainingArguments, set_seed
    from transformers.trainer_utils import get_last_checkpoint
    from experiments.nplus_shared_cache.latent_feedback import load_auxiliary

    output, training = Path(args.output), Path(args.output) / "training"
    training.mkdir(parents=True, exist_ok=True)
    completed = training / "training-receipt.json"
    if completed.exists():
        receipt = read_json(completed)
        if receipt["identity"] != manifest["identity"] or receipt["actual_epoch"] > 1.000001 or runner.file_sha(training / "auxiliary.safetensors") != receipt["auxiliary_file_sha256"]:
            raise ValueError("completed_training_binding_changed")
        return receipt
    checkpoint = get_last_checkpoint(str(training))
    checkpoint_step = 0
    if checkpoint:
        saved = read_json(Path(checkpoint) / "binding.json")
        if saved["identity"] != manifest["identity"] or saved["epoch"] > 1.000001 or runner.file_sha(Path(checkpoint) / "auxiliary.safetensors") != saved["auxiliary_file_sha256"]:
            raise ValueError("checkpoint_binding_changed")
        checkpoint_step = saved["global_step"]
    if (training / "progress.json").exists() and read_json(training / "progress.json")["global_step"] > checkpoint_step:
        raise ValueError("observed_updates_newer_than_checkpoint_no_replay")
    if (training / "started.json").exists() and not checkpoint:
        raise ValueError("interrupted_without_checkpoint_no_replay")
    set_seed(42)
    base, load_receipt = runner.load_frozen_model(args.model)
    runner.write_json(output / "model-load.json", load_receipt)
    is_post = manifest["binding"]["state_position"] == "post_target"
    auxiliary = load_auxiliary(args.initial_auxiliary, width=manifest["binding"]["width"],
        device=str(base.get_input_embeddings().weight.device), trainable=True, with_observation=is_post)
    student = build_model(base, auxiliary, args, manifest)
    base.config.use_cache = False

    class Records(Dataset):
        def __len__(self):
            return len(records)

        def __getitem__(self, index):
            record = records[index]
            targets = load_file(str(target_path(args.prepared, record)), device="cpu")
            return {"record_index": torch.tensor(index, dtype=torch.long),
                "teacher_heads": targets["teacher_heads"], "labels": targets["labels"].long(), "active": targets["active"].bool(),
                **{name: torch.tensor(record[name], dtype=torch.long) for name in ("prefix_ids", "closing_ids", "answer_ids")}}

    def collate(examples):
        if len(examples) != 1:
            raise ValueError("microbatch_one_required")
        return {name: value.unsqueeze(0) for name, value in examples[0].items()}

    class AuxiliaryTrainer(Trainer):
        def _get_train_sampler(self, train_dataset=None):
            return SequentialSampler(self.train_dataset)

        def _save(self, output_dir=None, state_dict=None):
            self.model.save_auxiliary(output_dir or self.args.output_dir)

        def _load_from_checkpoint(self, resume_from_checkpoint, model=None):
            (self.model if model is None else model).load_auxiliary(resume_from_checkpoint)

    groups = auxiliary.parameter_groups()
    snapshots = {name: [parameter.detach().float().cpu().clone() for parameter in parameters] for name, parameters in groups.items()}

    class Evidence(TrainerCallback):
        def on_pre_optimizer_step(self, arguments, state, control, **kwargs):
            norms = {name: math.sqrt(sum(float(parameter.grad.detach().float().square().sum())
                for parameter in values if parameter.grad is not None)) for name, values in groups.items()}
            runner.write_json(training / "latest-gradient-evidence.json", {"step": state.global_step + 1, "gradient_l2": norms})

        def on_optimizer_step(self, arguments, state, control, **kwargs):
            if not (training / "first-update-evidence.json").exists():
                deltas = {name: math.sqrt(sum(float((parameter.detach().float().cpu() - initial).square().sum())
                    for parameter, initial in zip(parameters, snapshots[name]))) for name, parameters in groups.items()}
                runner.write_json(training / "first-update-evidence.json", {"step": state.global_step + 1,
                    "parameter_delta_l2": deltas, "all_heads_fusion_gain_changed": all(value > 0 for value in deltas.values()),
                    "all_parameter_groups_changed": all(value > 0 for value in deltas.values())})

        def on_step_end(self, arguments, state, control, **kwargs):
            runner.write_json(training / "progress.json", {"identity": manifest["identity"], "global_step": state.global_step,
                "epoch": state.epoch, "loss_components": student.loss_components})

        def on_log(self, arguments, state, control, logs=None, **kwargs):
            with (training / "loss-history.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"step": state.global_step, "epoch": state.epoch, "trainer": logs or {},
                    "loss_components": student.loss_components}, ensure_ascii=False) + "\n")

        def on_save(self, arguments, state, control, **kwargs):
            folder = training / f"checkpoint-{state.global_step}"
            runner.write_json(folder / "binding.json", {"identity": manifest["identity"], "epoch": state.epoch,
                "global_step": state.global_step, "auxiliary_file_sha256": runner.file_sha(folder / "auxiliary.safetensors")})

    arguments = TrainingArguments(output_dir=str(training), num_train_epochs=1,
        per_device_train_batch_size=1, gradient_accumulation_steps=args.gradient_accumulation,
        learning_rate=args.fusion_learning_rate, lr_scheduler_type="constant", warmup_steps=0,
        weight_decay=0, max_grad_norm=1, bf16=True, optim="adamw_torch",
        activation_checkpointing=True, activation_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=1, logging_nan_inf_filter=False, save_strategy="steps", save_steps=args.save_steps,
        save_total_limit=2, report_to="none", dataloader_num_workers=0, remove_unused_columns=False, seed=42, data_seed=42)
    head_parameters = list(auxiliary.adapters.parameters())
    if is_post:
        head_parameters.extend(auxiliary.observation.parameters())
    optimizer = torch.optim.AdamW([
        {"params": head_parameters, "lr": args.head_learning_rate},
        {"params": [*auxiliary.fusion.parameters(), auxiliary.gain], "lr": args.fusion_learning_rate}],
        lr=args.fusion_learning_rate, weight_decay=0)
    trainer = AuxiliaryTrainer(model=student, args=arguments, train_dataset=Records(), data_collator=collate,
        processing_class=tokenizer, callbacks=[Evidence()], optimizers=(optimizer, None))
    runner.write_json(training / "started.json", {"identity": manifest["identity"], "requested_epochs": 1,
        "resume_step": checkpoint_step, "warm_start_auxiliary_sha256": args.initial_sha256})
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    try:
        result = trainer.train(resume_from_checkpoint=checkpoint)
        if trainer.state.epoch > 1.000001:
            raise RuntimeError("one_epoch_limit_exceeded")
        trainer.save_model(str(training))
        trainer.save_state()
        update = read_json(training / "first-update-evidence.json")
        if not update["all_heads_fusion_gain_changed"]:
            raise RuntimeError("head_fusion_or_gain_failed_to_update")
        torch.cuda.synchronize()
        final_deltas = {name: math.sqrt(sum(float((parameter.detach().float().cpu() - initial).square().sum())
            for parameter, initial in zip(parameters, snapshots[name]))) for name, parameters in groups.items()}
        receipt = {"identity": manifest["identity"], "architecture": manifest["binding"]["schema"], "status": "completed",
            "actual_epoch": trainer.state.epoch, "global_steps": trainer.state.global_step, "train_records": len(records),
            "seconds": time.perf_counter() - started, "auxiliary_file_sha256": runner.file_sha(training / "auxiliary.safetensors"),
            "warm_start_auxiliary_sha256": args.initial_sha256, "head_learning_rate": args.head_learning_rate,
            "state_position": manifest["binding"]["state_position"], "observation": manifest["observation"],
            "fusion_learning_rate": args.fusion_learning_rate, "base_trainable_parameters": sum(p.numel() for p in base.parameters() if p.requires_grad),
            "auxiliary_trainable_parameters": sum(p.numel() for p in auxiliary.parameters() if p.requires_grad),
            "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(), "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(),
            "metrics": result.metrics, "loss_history": trainer.state.log_history, "final_parameter_delta_l2": final_deltas,
            "first_update": update, "objective_gradients": read_json(training / "objective-gradient-evidence.json"),
            "full_free_rollout_BPTT": False, "teacher_forcing_inputs": "gold_continuous_teacher_heads",
            "discrete_token_embedding_feedback": False, "training_uses_cache": False,
            "one_ordered_pass_over_complete_records": True, "paid_model_calls": 0,
            "free_rollout_readability_or_quality_assessed": False}
        runner.write_json(completed, receipt)
        return receipt
    except Exception:
        runner.write_json(training / "failure.json", {"identity": manifest["identity"], "global_step": trainer.state.global_step,
            "epoch": trainer.state.epoch, "traceback": traceback.format_exc()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("prepare", "train"), default="prepare")
    for name in ("model", "initial-auxiliary", "initial-sha256", "prepared", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--gradient-accumulation", type=int, default=4)
    parser.add_argument("--head-learning-rate", type=float, default=1e-3)
    parser.add_argument("--fusion-learning-rate", type=float, default=1e-4)
    parser.add_argument("--alignment-weight", type=float, default=.05)
    parser.add_argument("--ce-chunk-tokens", type=int, default=32)
    parser.add_argument("--save-steps", type=int, default=4)
    parser.add_argument("--max-context", type=int, default=8192)
    args = parser.parse_args()
    tokenizer, manifest, records = prepare(args)
    print(json.dumps({"identity": manifest["identity"], "records": len(records), "architecture": manifest["binding"]["schema"],
        "weights_loaded_by_prepare": False}), flush=True)
    if args.action == "train":
        print(json.dumps(train(args, tokenizer, manifest, records), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
