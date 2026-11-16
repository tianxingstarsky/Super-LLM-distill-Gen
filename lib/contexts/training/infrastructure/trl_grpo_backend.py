"""Use TRL's unmodified GRPO trainer with local PEFT models and exact rewards.

TRL owns rollouts, group advantages, loss, optimizer and sampling. This adapter
only binds verified tasks, preserves native outputs and records durable evidence.
"""
from collections import Counter
from datetime import datetime, timezone
import gc
import importlib.metadata
import json
from pathlib import Path
import time
import uuid

from ..domain.grpo import GRPOValidationPlan
from .trl_reward import REWARD_PROTOCOL, verify_numeric_answers
from .transformers_backend import (TransformersTrainingBackend, _digest, _file_digest,
    _write, _guard_training_resume, _split_output, _eos_ids)


class TrlGrpoBackend(TransformersTrainingBackend):
    def inspect(self, model_path):
        facts = super().inspect(model_path)
        facts["dependencies"]["trl"] = importlib.metadata.version("trl")
        facts["dependencies"]["math-verify"] = importlib.metadata.version("math-verify")
        # Available VRAM is a measurement, not model identity; it changes after
        # every load and must not invalidate a frozen experiment on recovery.
        facts.get("device", {}).pop("free_bytes", None)
        return facts

    def evaluate(self, *args, **kwargs):
        import torch
        torch.cuda.reset_peak_memory_stats(0)
        receipt = super().evaluate(*args, **kwargs)
        receipt["cuda_peak_reserved_bytes"] = torch.cuda.max_memory_reserved(0)
        return receipt

    def train(self, model_path, tasks, output_path, config, *, parent_resume=None):
        import torch
        from datasets import Dataset
        from transformers import TrainerCallback, set_seed
        from transformers.trainer_utils import get_last_checkpoint
        from trl import GRPOConfig, GRPOTrainer

        plan = GRPOValidationPlan(**config)
        if plan.parent_run and parent_resume is None:
            raise ValueError("grpo_capacity_resume_authorization_required")
        if parent_resume:
            from .grpo_checkpoint import verify_capacity_parent_unchanged
            verify_capacity_parent_unchanged(parent_resume)
        output = Path(output_path)
        output.mkdir(parents=True, exist_ok=True)
        model_facts = self.inspect(model_path)
        identity = _digest([model_facts, tasks, config, REWARD_PROTOCOL])
        binding = {"experiment_identity": identity, "config": config, "tasks_sha256": _digest(tasks)}
        if parent_resume:
            binding["capacity_parent"] = {key: value for key, value in parent_resume.items()
                if key not in {"committed_rollouts", "unused_rollouts"}}
        identity_file = output / "training-identity.json"
        if identity_file.exists():
            if json.loads(identity_file.read_text(encoding="utf-8")) != binding:
                raise ValueError("grpo_training_identity_changed")
        elif list(output.glob("checkpoint-*")) or (output / "adapter_config.json").exists():
            raise ValueError("grpo_existing_training_not_bound")
        else:
            _write(identity_file, binding)
        receipt_path = output / "training-receipt.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt["experiment_identity"] != identity or receipt["status"] != "completed":
                raise ValueError("grpo_completed_training_changed")
            for artifact in receipt["adapter_files"]:
                if _file_digest(output / artifact["file"]) != artifact["sha256"]:
                    raise ValueError("grpo_completed_adapter_changed")
            return receipt
        checkpoint = get_last_checkpoint(str(output))
        parent_checkpoint = bool(not checkpoint and parent_resume)
        if parent_checkpoint:
            checkpoint = parent_resume["checkpoint"]
        checkpoint_step = int(Path(checkpoint).name.split("-")[-1]) if checkpoint else None
        _guard_training_resume(output, identity, checkpoint_step)
        if checkpoint:
            checkpoint_identity = json.loads((Path(checkpoint) / "experiment-identity.json").read_text(encoding="utf-8"))
            checkpoint_owner = parent_resume["parent_training_identity"] if parent_checkpoint else identity
            if checkpoint_identity["experiment_identity"] != checkpoint_owner:
                raise ValueError("grpo_checkpoint_identity_changed")
            for artifact in checkpoint_identity["adapter_files"]:
                if _file_digest(Path(checkpoint) / artifact["file"]) != artifact["sha256"]:
                    raise ValueError("grpo_checkpoint_adapter_changed")

        set_seed(plan.seed)
        torch.cuda.reset_peak_memory_stats(0)
        started = time.monotonic()
        model, tokenizer, dtype = self._load(model_path, {"qlora": True,
            "lora_rank": plan.lora_rank, "lora_alpha": plan.lora_rank * 2,
            "gradient_checkpointing": True}, trainable=True)
        tokenizer.padding_side = "left"
        load_seconds = time.monotonic() - started
        before_adapter = {}
        stops = _eos_ids(model, tokenizer)
        backend = self
        attempt_id = uuid.uuid4().hex
        journal = output / "rollouts.jsonl"
        if parent_resume and not (output / "capacity-parent.json").exists():
            if journal.exists():
                raise ValueError("grpo_capacity_journal_already_exists")
            journal.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                for row in parent_resume["committed_rollouts"]), encoding="utf-8")
            _write(output / "capacity-parent.json", binding["capacity_parent"])
            _write(output / "parent-unused-rollouts.json", parent_resume["unused_rollouts"])

        def exact_final_numeric(prompts, completions, completion_ids, expected_answer, task_id, family,
                                trainer_state, **kwargs):
            rewards = []
            decoded = []
            for prompt_messages, token_ids in zip(prompts, completion_ids, strict=True):
                raw = tokenizer.decode(token_ids, skip_special_tokens=False)
                prompt = tokenizer.apply_chat_template(prompt_messages, tokenize=False,
                    add_generation_prompt=True, enable_thinking=True)
                reasoning, answer, closed = _split_output(raw, prompt, tokenizer)
                decoded.append((raw, prompt, reasoning, answer, closed))
            numeric_rewards = verify_numeric_answers([row[3] for row in decoded], expected_answer)
            with journal.open("a", encoding="utf-8") as stream:
                for i, token_ids in enumerate(completion_ids):
                    raw, prompt, reasoning, answer, closed = decoded[i]
                    eos = bool(token_ids and token_ids[-1] in stops)
                    numeric = numeric_rewards[i] == 1.0
                    reward = float(numeric and eos)
                    rewards.append(reward)
                    stream.write(json.dumps({"schema_version": 1, "experiment_identity": identity,
                        "attempt_id": attempt_id, "global_step": trainer_state.global_step,
                        "epoch": trainer_state.epoch, "task_id": task_id[i], "family": family[i],
                        "messages": prompts[i], "prompt": prompt, "completion_token_ids": token_ids,
                        "raw_output": raw, "reasoning": reasoning, "answer": answer,
                        "thinking_closed": closed, "ended_with_eos": eos,
                        "truncated": not eos and len(token_ids) >= plan.max_completion_length,
                        "output_tokens": len(token_ids), "expected_answer": expected_answer[i],
                        "numeric_answer_correct": numeric, "reward": reward}, ensure_ascii=False) + "\n")
            backend.progress({"kind": "grpo_rollout_group", "global_step": trainer_state.global_step,
                "task_ids": list(dict.fromkeys(task_id)), "rewards": rewards,
                "output_tokens": sum(map(len, completion_ids)), "seconds_since_load": time.monotonic() - started})
            return rewards

        class ReceiptCallback(TrainerCallback):
            def on_step_end(self, args, state, control, **kwargs):
                _write(output / "training-progress.json", {"experiment_identity": identity,
                    "attempt_id": attempt_id, "global_step": state.global_step, "epoch": state.epoch})

            def on_log(self, args, state, control, logs=None, **kwargs):
                backend.progress({"kind": "grpo_training", "global_step": state.global_step,
                    "epoch": state.epoch, "logs": logs or {}})

            def on_save(self, args, state, control, **kwargs):
                saved = Path(args.output_dir) / f"checkpoint-{state.global_step}"
                _write(saved / "experiment-identity.json", {"experiment_identity": identity,
                    "global_steps": state.global_step, "epoch": state.epoch,
                    "adapter_files": [{"file": path.name, "sha256": _file_digest(path)}
                        for path in sorted(saved.glob("adapter*")) if path.is_file()]})

        rows = [{"prompt": [{"role": "system", "content": "You are a helpful assistant."},
                            {"role": "user", "content": task["prompt"]}],
                 "task_id": task["task_id"], "family": task["family"],
                 "expected_answer": task["expected_answer"]} for task in tasks]
        arguments = GRPOConfig(output_dir=str(output), num_train_epochs=plan.epochs,
            num_iterations=plan.num_iterations, num_generations=plan.num_generations,
            generation_batch_size=plan.num_generations,
            per_device_train_batch_size=plan.batch_size,
            gradient_accumulation_steps=plan.gradient_accumulation,
            max_completion_length=plan.max_completion_length,
            temperature=plan.temperature, top_p=0.95, top_k=20, repetition_penalty=1.0,
            chat_template_kwargs={"enable_thinking": True},
            generation_kwargs={"use_cache": True, "num_beams": 1},
            beta=plan.beta, loss_type="grpo", mask_truncated_completions=True,
            learning_rate=plan.learning_rate, lr_scheduler_type="constant", warmup_steps=0,
            weight_decay=0.0, optim="adamw_torch", bf16=dtype == torch.bfloat16,
            gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
            use_vllm=False, use_liger_kernel=False, torch_compile=False,
            pad_to_multiple_of=1, shuffle_dataset=False, seed=plan.seed, data_seed=plan.seed,
            save_strategy="steps", save_steps=1, save_total_limit=2,
            logging_steps=1, report_to="none", dataloader_num_workers=0)
        _write(output / "trl-config.json", arguments.to_dict())
        trainer = None
        try:
            _write(output / "training-started.json", {"experiment_identity": identity,
                "attempt_id": attempt_id, "started_at": datetime.now(timezone.utc).isoformat(),
                "requested_epochs": plan.epochs, "resume_step": checkpoint_step})
            trainer = GRPOTrainer(model=model, args=arguments, processing_class=tokenizer,
                reward_funcs=exact_final_numeric, train_dataset=Dataset.from_list(rows),
                callbacks=[ReceiptCallback()])
            # TRL casts QLoRA parameters during construction. Measure updates
            # afterwards so that dtype rounding cannot masquerade as learning.
            before_adapter = {name: parameter.detach().float().cpu().clone()
                for name, parameter in model.named_parameters() if parameter.requires_grad}
            trained = trainer.train(resume_from_checkpoint=checkpoint)
            if trainer.state.epoch is None or trainer.state.epoch > 1.000001:
                raise RuntimeError("grpo_actual_epoch_limit_exceeded")
            trainer.save_model(str(output))
            tokenizer.save_pretrained(str(output))
            trainer.save_state()
            rollouts = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
            grouped = {}
            for row in rollouts:
                grouped.setdefault((row["attempt_id"], row["global_step"], row["task_id"]), []).append(row["reward"])
            delta_squared = sum(float((parameter.detach().float().cpu() - before_adapter[name]).square().sum())
                for name, parameter in model.named_parameters() if name in before_adapter)
            receipt = {"schema_version": 1, "status": "completed", "backend": "trl_grpo_trainer_peft",
                "experiment_identity": identity, "config": config, "actual_epochs": trainer.state.epoch,
                "global_steps": trainer.state.global_step, "seconds": time.monotonic() - started,
                "model_load_seconds": load_seconds, "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad),
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(0),
                "peak_reserved_bytes": torch.cuda.max_memory_reserved(0),
                "rollout_count": len(rollouts), "unique_train_tasks_sampled": len({row["task_id"] for row in rollouts}),
                "rollout_task_counts": dict(Counter(row["task_id"] for row in rollouts)),
                "rollout_output_tokens": sum(row["output_tokens"] for row in rollouts),
                "truncated_rollouts": sum(row["truncated"] for row in rollouts),
                "reward_mean": sum(row["reward"] for row in rollouts) / len(rollouts),
                "zero_variance_groups": sum(len(set(rewards)) == 1 for rewards in grouped.values()),
                "informative_groups": sum(len(set(rewards)) > 1 for rewards in grouped.values()),
                "total_groups": len(grouped), "adapter_delta_l2": delta_squared ** 0.5,
                "metrics": trained.metrics, "loss_history": trainer.state.log_history,
                "model": model_facts, "resumed_from_step": checkpoint_step or 0,
                "new_optimizer_steps": trainer.state.global_step - (checkpoint_step or 0),
                "capacity_parent": binding.get("capacity_parent"),
                "adapter_files": [{"file": path.name, "sha256": _file_digest(path), "bytes": path.stat().st_size}
                    for path in sorted(output.glob("adapter*")) if path.is_file()]}
            _write(receipt_path, receipt)
            return receipt
        except Exception as error:
            _write(output / "training-failure.json", {"status": "failed", "experiment_identity": identity,
                "attempt_id": attempt_id, "error_type": type(error).__name__,
                "global_steps": trainer.state.global_step if trainer else 0,
                "actual_epochs": trainer.state.epoch if trainer else 0,
                "seconds": time.monotonic() - started,
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(0)})
            raise
        finally:
            del trainer, model, before_adapter
            gc.collect()
            torch.cuda.empty_cache()
