"""Optional Transformers/PEFT execution for reproducible local validation.

Encoded labels and official model templates are the input contract. Trainer
owns optimization; this adapter owns local files, model lifetime and receipts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gc
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import time
import uuid


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluation_identity(model_files, adapter_files, cases, config, validation_examples=None):
    """Keep the established evaluation binding shared by cache import and execution."""
    return _digest([model_files, adapter_files, cases, config, validation_examples])


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _versions():
    versions = {}
    for package in ("torch", "transformers", "peft", "datasets", "accelerate", "bitsandbytes"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _guard_training_resume(output, identity, checkpoint_step):
    """Never replay optimizer progress observed after the last saved state."""
    output = Path(output)
    observed = 0
    failure = None
    for name, step_key in (("training-progress.json", "global_step"),
                           ("training-failure.json", "global_steps")):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("experiment_identity") != identity:
            raise ValueError("local_training_progress_identity_changed")
        step = record.get(step_key)
        if type(step) is not int or step < 0:
            raise ValueError("local_training_progress_step_invalid")
        observed = max(observed, step)
        if name == "training-failure.json":
            failure = record
    if checkpoint_step is not None:
        if observed > checkpoint_step:
            raise ValueError("local_training_checkpoint_behind_observed_progress")
        return
    if observed:
        raise ValueError("local_training_progress_has_no_checkpoint")
    started = output / "training-started.json"
    if started.exists():
        attempt = json.loads(started.read_text(encoding="utf-8"))
        if attempt.get("experiment_identity") != identity:
            raise ValueError("local_training_started_identity_changed")
        # A caught zero-step failure proves no completed optimizer step. A hard
        # interruption has no such receipt and must not restart from zero.
        if (not failure or failure.get("status") != "failed" or failure["global_steps"] != 0
                or not attempt.get("attempt_id") or failure.get("attempt_id") != attempt["attempt_id"]):
            raise ValueError("local_training_interrupted_without_checkpoint")


def _eos_ids(model, tokenizer):
    configured = getattr(model.generation_config, "eos_token_id", None)
    values = list(configured) if isinstance(configured, list) else [configured]
    if tokenizer.eos_token_id is not None:
        values.append(tokenizer.eos_token_id)
    return list(dict.fromkeys(int(value) for value in values if value is not None))


def _split_output(raw_text, prompt, tokenizer):
    """Decode fields from actual delimiters; an unclosed thought has no answer."""
    opening_in_prompt = prompt.endswith("<think>\n")
    combined = ("<think>\n" if opening_in_prompt else "") + raw_text
    if combined.startswith("<think>"):
        thought, closed, answer = combined[len("<think>"):].partition("</think>")
        reasoning = thought
        if not closed:
            return reasoning, "", False
    else:
        reasoning, answer, closed = "", raw_text, ""
    # Keep raw_text separately. These are the model's protocol delimiters,
    # not portions of its reasoning or answer prose.
    for token in (tokenizer.eos_token, tokenizer.pad_token, "<|im_end|>", "<|endoftext|>"):
        if token and answer.endswith(token):
            answer = answer[:-len(token)]
    return reasoning, answer.strip(), bool(closed)


class TransformersTrainingBackend:
    """Use a local model with optional quantized LoRA and ordinary Trainer."""

    def __init__(self, *, prompt_renderer=None, progress=None):
        self.prompt_renderer = prompt_renderer
        self.progress = progress or (lambda event: None)
        self._inspection_cache = {}

    def inspect(self, model_path):
        import torch
        from transformers import AutoTokenizer

        path = Path(model_path)
        files = [path / "config.json", *sorted(path.glob("*.safetensors")),
            *[path / name for name in ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja",
                                      "generation_config.json", "model.safetensors.index.json",
                                      "special_tokens_map.json", "added_tokens.json") if (path / name).is_file()]]
        signature = tuple((file.name, file.stat().st_size, file.stat().st_mtime_ns) for file in files)
        key = (str(path.resolve()), signature)
        if key in self._inspection_cache:
            return deepcopy(self._inspection_cache[key])
        configuration = json.loads((path / "config.json").read_text(encoding="utf-8"))
        tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
        template = tokenizer.chat_template
        template_text = template if isinstance(template, str) else json.dumps(template, sort_keys=True)
        facts = {"schema_version": 1, "model_name": configuration.get("model_type"),
            "architectures": configuration.get("architectures"), "dependencies": _versions(),
            "files": [{"file": file.name, "bytes": file.stat().st_size, "sha256": _file_digest(file)} for file in files],
            "chat_template_sha256": hashlib.sha256(template_text.encode("utf-8")).hexdigest(),
            "tokenizer_class": type(tokenizer).__name__, "tokenizer_eos": tokenizer.eos_token,
            "tokenizer_eos_id": tokenizer.eos_token_id, "pad_token_id": tokenizer.pad_token_id,
            "cuda_available": torch.cuda.is_available()}
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info(0)
            facts["device"] = {"name": torch.cuda.get_device_name(0), "free_bytes": free,
                "total_bytes": total, "bf16_supported": torch.cuda.is_bf16_supported()}
        self._inspection_cache[key] = deepcopy(facts)
        return facts

    def _load(self, model_path, config, *, adapter_path=None, trainable=False):
        import torch
        from transformers import AutoConfig, AutoModelForCausalLM, AutoModelForImageTextToText, AutoTokenizer, BitsAndBytesConfig
        from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training

        if not torch.cuda.is_available():
            raise RuntimeError("local_training_requires_cuda")
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        configuration = AutoConfig.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
        tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = "right"
        loader = AutoModelForImageTextToText if getattr(configuration, "vision_config", None) is not None else AutoModelForCausalLM
        options = {"local_files_only": True, "trust_remote_code": False,
            "device_map": {"": 0}, "dtype": dtype,
            "attn_implementation": config.get("attn_implementation", "sdpa")}
        if config.get("qlora", True):
            options["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True,
                bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
        model = loader.from_pretrained(str(model_path), **options)
        if adapter_path:
            model = PeftModel.from_pretrained(model, str(adapter_path), is_trainable=trainable)
        elif trainable:
            if config.get("qlora", True):
                model = prepare_model_for_kbit_training(model,
                    use_gradient_checkpointing=config.get("gradient_checkpointing", True),
                    gradient_checkpointing_kwargs={"use_reentrant": False})
            names = [name for name, module in model.named_modules()
                if isinstance(module, torch.nn.Linear) or type(module).__name__ in {"Linear4bit", "Linear8bitLt"}]
            text_names = [name for name in names if ("language_model" in name or "text_model" in name)
                          and not name.endswith("lm_head")]
            if not text_names:
                text_names = [name for name in names if ".layers." in name
                    and not any(part in name for part in ("visual", "vision", "lm_head"))]
            if not text_names:
                raise RuntimeError("local_training_text_lora_modules_not_found")
            model = get_peft_model(model, LoraConfig(r=int(config.get("lora_rank", 16)),
                lora_alpha=int(config.get("lora_alpha", 32)), lora_dropout=float(config.get("lora_dropout", 0.0)),
                target_modules=text_names, bias="none", task_type="CAUSAL_LM"))
        model.config.use_cache = not trainable
        return model, tokenizer, dtype

    def train(self, model_path, examples, output_path, config, parent_adapter=None):
        import torch
        from torch.utils.data import SequentialSampler
        from datasets import Dataset
        from transformers import DataCollatorForSeq2Seq, Trainer, TrainerCallback, TrainingArguments, set_seed

        epochs = float(config.get("epochs", 1.0))
        if not 0 < epochs <= 1:
            raise ValueError("local_training_epoch_limit_exceeded")
        encoded = [{key: list(example[key]) for key in ("input_ids", "attention_mask", "labels")}
                   for example in examples]
        if not encoded or any(len(row["input_ids"]) != len(row["labels"])
                or not any(label != -100 for label in row["labels"]) for row in encoded):
            raise ValueError("local_training_encoded_examples_invalid")
        output = Path(output_path)
        output.mkdir(parents=True, exist_ok=True)
        seed = int(config.get("seed", 42))
        set_seed(seed)
        before = self.inspect(model_path)
        parent_files = [{"file": file.name, "sha256": _file_digest(file)}
            for file in sorted(Path(parent_adapter).glob("adapter*")) if file.is_file()] if parent_adapter else []
        identity = _digest([before["files"], encoded, config, parent_files])
        binding = {"schema_version": 1, "experiment_identity": identity,
            "model_files": before["files"], "examples_sha256": _digest(encoded),
            "config_sha256": _digest(config), "parent_adapter_files": parent_files}
        identity_path = output / "training-identity.json"
        if identity_path.exists():
            if json.loads(identity_path.read_text(encoding="utf-8")) != binding:
                raise ValueError("local_training_checkpoint_identity_changed")
        elif (output / "adapter_config.json").exists() or list(output.glob("checkpoint-*")):
            raise ValueError("local_training_unbound_existing_output")
        else:
            _write(identity_path, binding)
        completed_path = output / "training-receipt.json"
        if completed_path.exists():
            completed = json.loads(completed_path.read_text(encoding="utf-8"))
            if completed.get("experiment_identity") != identity or completed.get("status") != "completed":
                raise ValueError("local_training_completed_identity_changed")
            recorded_files = completed.get("adapter_files", [])
            if not recorded_files or not any(file["file"].endswith((".safetensors", ".bin")) for file in recorded_files):
                raise ValueError("local_training_completed_adapter_missing")
            if sorted(file["file"] for file in recorded_files) != sorted(file.name for file in output.glob("adapter*") if file.is_file()):
                raise ValueError("local_training_completed_adapter_inventory_changed")
            for file in recorded_files:
                path = output / file["file"]
                if not path.is_file() or _file_digest(path) != file["sha256"]:
                    raise ValueError("local_training_completed_adapter_changed")
            return {**completed, "adapter_path": str(output), "reused_complete": True}
        from transformers.trainer_utils import get_last_checkpoint
        checkpoint = get_last_checkpoint(str(output))
        checkpoint_step = None
        if checkpoint:
            checkpoint_path = Path(checkpoint)
            checkpoint_binding = json.loads((checkpoint_path / "experiment-identity.json").read_text(encoding="utf-8"))
            if checkpoint_binding.get("experiment_identity") != identity:
                raise ValueError("local_training_saved_checkpoint_unbound")
            if not 0 <= checkpoint_binding.get("epoch", -1) <= 1:
                raise ValueError("local_training_checkpoint_epoch_exceeded")
            checkpoint_step = checkpoint_binding["global_steps"]
            trainer_state = json.loads((checkpoint_path / "trainer_state.json").read_text(encoding="utf-8"))
            if (type(checkpoint_step) is not int or checkpoint_step < 0
                    or checkpoint_step != trainer_state.get("global_step")
                    or checkpoint_path.name != f"checkpoint-{checkpoint_step}"):
                raise ValueError("local_training_checkpoint_step_mismatch")
            for file in checkpoint_binding["adapter_files"]:
                if _file_digest(checkpoint_path / file["file"]) != file["sha256"]:
                    raise ValueError("local_training_saved_checkpoint_changed")
        _guard_training_resume(output, identity, checkpoint_step)
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        arguments = TrainingArguments(output_dir=str(output), num_train_epochs=epochs,
            per_device_train_batch_size=int(config.get("batch_size", 1)),
            gradient_accumulation_steps=int(config.get("gradient_accumulation_steps", 4)),
            learning_rate=float(config.get("learning_rate", 5e-5)), lr_scheduler_type="constant", warmup_steps=0,
            weight_decay=0.0, max_grad_norm=1.0, optim="adamw_torch", seed=seed, data_seed=seed,
            bf16=dtype == torch.bfloat16, fp16=dtype == torch.float16,
            gradient_checkpointing=config.get("gradient_checkpointing", True),
            gradient_checkpointing_kwargs={"use_reentrant": False},
            logging_steps=1, save_strategy="steps", save_steps=int(config.get("save_steps", 1)), save_total_limit=2,
            report_to="none", dataloader_num_workers=0, remove_unused_columns=False)
        started = time.monotonic()
        model, tokenizer, dtype = self._load(model_path, config, adapter_path=parent_adapter, trainable=True)
        load_seconds = time.monotonic() - started
        torch.cuda.reset_peak_memory_stats(0)
        backend = self
        attempt_id = uuid.uuid4().hex

        class OrderedTrainer(Trainer):
            def _get_train_sampler(self, train_dataset=None):
                return SequentialSampler(self.train_dataset if train_dataset is None else train_dataset)

        class ReceiptCallback(TrainerCallback):
            def on_log(self, args, state, control, logs=None, **kwargs):
                _write(output / "training-progress.json", {"experiment_identity": identity,
                    "attempt_id": attempt_id, "global_step": state.global_step, "epoch": state.epoch, "logs": logs or {}})
                backend.progress({"kind": "training", "global_step": state.global_step,
                    "epoch": state.epoch, "logs": logs or {}})

            def on_save(self, args, state, control, **kwargs):
                saved = Path(args.output_dir) / f"checkpoint-{state.global_step}"
                _write(saved / "experiment-identity.json", {"schema_version": 1,
                    "experiment_identity": identity, "global_steps": state.global_step,
                    "epoch": state.epoch, "adapter_files": [
                        {"file": file.name, "sha256": _file_digest(file)}
                        for file in sorted(saved.glob("adapter*")) if file.is_file()]})

        collator = DataCollatorForSeq2Seq(tokenizer=tokenizer, model=None, padding=True,
            label_pad_token_id=-100, pad_to_multiple_of=8, return_tensors="pt")
        trainer = OrderedTrainer(model=model, args=arguments, train_dataset=Dataset.from_list(encoded),
            data_collator=collator, processing_class=tokenizer, callbacks=[ReceiptCallback()])
        try:
            _write(output / "training-started.json", {"schema_version": 1,
                "experiment_identity": identity, "started_at": datetime.now(timezone.utc).isoformat(),
                "attempt_id": attempt_id, "resume_step": checkpoint_step, "requested_epochs": epochs})
            result = trainer.train(resume_from_checkpoint=checkpoint or None)
            if trainer.state.epoch is not None and trainer.state.epoch > 1.000001:
                raise RuntimeError("local_training_epoch_receipt_exceeded")
            trainer.save_model(str(output))
            tokenizer.save_pretrained(str(output))
            trainer.save_state()
            receipt = {"schema_version": 1, "status": "completed", "backend": "transformers_trainer_peft",
                "adapter_path": str(output),
                "experiment_identity": identity, "resumed_from_step":
                    int(Path(checkpoint).name.split("-")[-1]) if checkpoint else 0,
                "completed_at": datetime.now(timezone.utc).isoformat(), "seconds": time.monotonic() - started,
                "model_load_seconds": load_seconds,
                "requested_config": deepcopy(config), "actual_epochs": trainer.state.epoch,
                "global_steps": trainer.state.global_step, "ordered_single_pass": True,
                "example_count": len(encoded), "input_tokens": sum(len(row["input_ids"]) for row in encoded),
                "supervised_tokens": sum(sum(label != -100 for label in row["labels"]) for row in encoded),
                "trainable_parameters": sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad),
                "metrics": result.metrics, "loss_history": trainer.state.log_history,
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(0),
                "peak_reserved_bytes": torch.cuda.max_memory_reserved(0), "model": before,
                "adapter_files": [{"file": file.name, "sha256": _file_digest(file), "bytes": file.stat().st_size}
                    for file in sorted(output.glob("adapter*")) if file.is_file()]}
            _write(output / "training-receipt.json", receipt)
            return receipt
        except Exception as error:
            _write(output / "training-failure.json", {"schema_version": 1, "status": "failed",
                "experiment_identity": identity,
                "attempt_id": attempt_id,
                "error_type": type(error).__name__, "seconds": time.monotonic() - started,
                "actual_epochs": trainer.state.epoch, "global_steps": trainer.state.global_step,
                "loss_history": trainer.state.log_history,
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(0)})
            raise
        finally:
            del trainer, model
            gc.collect()
            torch.cuda.empty_cache()

    def evaluate(self, model_path, adapter_path, evaluation_cases, output_path, config, *, validation_examples=None):
        import torch
        from transformers import GenerationConfig, TextStreamer, set_seed

        output = Path(output_path)
        output.mkdir(parents=True, exist_ok=True)
        model_facts = self.inspect(model_path)
        adapter_files = [{"file": file.name, "sha256": _file_digest(file)}
                         for file in sorted(Path(adapter_path).glob("adapter*")) if file.is_file()] if adapter_path else []
        identity = evaluation_identity(model_facts["files"], adapter_files, evaluation_cases, config, validation_examples)
        started = time.monotonic()
        model, tokenizer, _ = self._load(model_path, config, adapter_path=adapter_path, trainable=False)
        load_seconds = time.monotonic() - started
        model.eval()
        stops = _eos_ids(model, tokenizer)
        if not stops:
            raise ValueError("local_evaluation_eos_not_defined")
        rows = []
        maximum = int(config.get("max_new_tokens", 32768))
        presence_supported = "presence_penalty" in GenerationConfig().to_dict()

        class LiveFileStreamer(TextStreamer):
            def __init__(self, target):
                super().__init__(tokenizer, skip_prompt=True, skip_special_tokens=False)
                self.target = target
                self.pending = ""
                self.last_write = time.monotonic()
                target.write_text("", encoding="utf-8")

            def flush(self):
                if self.pending:
                    with self.target.open("a", encoding="utf-8") as stream:
                        stream.write(self.pending)
                    self.pending = ""
                    self.last_write = time.monotonic()

            def on_finalized_text(self, text, stream_end=False):
                self.pending += text
                if stream_end or len(self.pending) >= 256 or time.monotonic() - self.last_write >= 1:
                    self.flush()

        try:
            for index, case in enumerate(evaluation_cases):
                path = output / f"case-{index:04d}.json"
                if path.exists():
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    if saved.get("experiment_identity") != identity:
                        raise ValueError("local_evaluation_checkpoint_identity_changed")
                    rows.append(saved)
                    continue
                messages = deepcopy(case["messages"])
                prompt = (self.prompt_renderer(tokenizer, messages) if self.prompt_renderer else
                    tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=True))
                inputs = tokenizer(prompt, add_special_tokens=False, return_tensors="pt").to("cuda:0")
                set_seed(int(config.get("seed", 42)) + index)
                generation_options = {"max_new_tokens": maximum, "do_sample": True,
                    "temperature": float(config.get("temperature", 1.0)), "top_p": float(config.get("top_p", 0.95)),
                    "top_k": int(config.get("top_k", 20)),
                    "min_p": float(config.get("min_p", 0.0)),
                    "repetition_penalty": float(config.get("repetition_penalty", 1.0)),
                    "eos_token_id": stops, "pad_token_id": tokenizer.pad_token_id, "use_cache": True}
                if presence_supported and config.get("requested_presence_penalty") is not None:
                    generation_options["presence_penalty"] = config["requested_presence_penalty"]
                live_path = output / f"live-case-{index:04d}.txt"
                streamer = LiveFileStreamer(live_path)
                self.progress({"kind": "evaluation_started", "case_id": case.get("id", str(index)),
                    "case_number": index + 1, "total": len(evaluation_cases), "live_file": live_path.name})
                case_started = time.monotonic()
                try:
                    with torch.inference_mode():
                        generated = model.generate(**inputs, **generation_options, streamer=streamer)
                finally:
                    streamer.flush()
                tokens = generated[0, inputs["input_ids"].shape[-1]:].tolist()
                raw = tokenizer.decode(tokens, skip_special_tokens=False)
                live_path.write_text(raw, encoding="utf-8")
                reasoning, answer, closed = _split_output(raw, prompt, tokenizer)
                eos = bool(tokens and tokens[-1] in stops)
                row = {"schema_version": 1, "experiment_identity": identity, "case_id": case.get("id", str(index)),
                    "messages": messages, "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "generation_config": generation_options, "input_tokens": int(inputs["input_ids"].shape[-1]),
                    "sampling_support": {"requested_presence_penalty": config.get("requested_presence_penalty"),
                        "presence_penalty_supported": presence_supported,
                        "presence_penalty_applied": "presence_penalty" in generation_options,
                        "reason": "native GenerationConfig support checked; unsupported options are not sent"},
                    "output_tokens": len(tokens), "output_token_ids": tokens, "raw_output": raw,
                    "reasoning": reasoning, "answer": answer, "thinking_closed": closed,
                    "ended_with_eos": eos, "truncated": not eos and len(tokens) >= maximum,
                    "finish_reason": "eos" if eos else "max_new_tokens" if len(tokens) >= maximum else "stopped_without_eos",
                    "seconds": time.monotonic() - case_started,
                    "expected_answer": case.get("expected_answer"), "source": deepcopy(case.get("source")),
                    "group": case.get("group"), "language": case.get("language"),
                    "rubric": deepcopy(case.get("rubric")), "metadata": deepcopy(case.get("metadata")),
                    "semantic_status": "not_assessed"}
                _write(path, row)
                rows.append(row)
                self.progress({"kind": "evaluation", "case_id": row["case_id"], "completed": index + 1,
                               "total": len(evaluation_cases), "finish_reason": row["finish_reason"]})
            validation_metrics = None
            if validation_examples:
                from transformers import DataCollatorForSeq2Seq
                encoded_validation = [{key: list(example[key]) for key in ("input_ids", "attention_mask", "labels")}
                                      for example in validation_examples]
                collator = DataCollatorForSeq2Seq(tokenizer=tokenizer, model=None, padding=True,
                    label_pad_token_id=-100, pad_to_multiple_of=8, return_tensors="pt")
                validation_started = time.monotonic()
                weighted_loss, target_tokens = 0.0, 0
                with torch.inference_mode():
                    for example in encoded_validation:
                        batch = {key: value.to("cuda:0") for key, value in collator([example]).items()}
                        prediction = None
                        try:
                            # Native causal-LM loss shifts labels by one token.
                            count = int((batch["labels"][:, 1:] != -100).sum().item())
                            if count <= 0:
                                raise ValueError("local_validation_has_no_shifted_targets")
                            prediction = model(**batch, use_cache=False)
                            if prediction.loss is None:
                                raise ValueError("local_validation_native_loss_missing")
                            loss = float(prediction.loss.detach().float().cpu().item())
                            if not math.isfinite(loss):
                                raise ValueError("local_validation_native_loss_nonfinite")
                            weighted_loss += loss * count
                            target_tokens += count
                        finally:
                            del prediction, batch
                validation_metrics = {"eval_loss": weighted_loss / target_tokens,
                    "eval_runtime": time.monotonic() - validation_started,
                    "example_count": len(encoded_validation), "supervised_tokens": target_tokens,
                    "metric_kind": "native_hf_forward_token_weighted_assistant_loss"}
            rows_path = output / "generations.jsonl"
            rows_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            receipt = {"schema_version": 1, "status": "completed", "experiment_identity": identity,
                "rows_path": str(rows_path),
                "seconds": time.monotonic() - started, "model": model_facts, "adapter_files": adapter_files,
                "model_load_seconds": load_seconds,
                "config": deepcopy(config), "cases": rows, "case_count": len(rows),
                "validation_metrics": validation_metrics,
                "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(0), "semantic_status": "not_assessed",
                "summary": {"case_count": len(rows), "answers_present": sum(bool(row["answer"].strip()) for row in rows),
                    "thinking_closed": sum(row["thinking_closed"] for row in rows),
                    "ended_with_eos": sum(row["ended_with_eos"] for row in rows),
                    "truncated": sum(row["truncated"] for row in rows),
                    "input_tokens": sum(row["input_tokens"] for row in rows),
                    "output_tokens": sum(row["output_tokens"] for row in rows)}}
            _write(output / "evaluation-receipt.json", receipt)
            return receipt
        finally:
            del model
            gc.collect()
            torch.cuda.empty_cache()
