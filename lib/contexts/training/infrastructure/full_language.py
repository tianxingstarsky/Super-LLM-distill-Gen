"""Single-pass native Qwen3.5 language SFT over already encoded assistant labels."""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time
import uuid

from . import qwen35_weights
from .filesystem import file_sha, write_json
from ..domain.full_sft import validate_full_sft_optimizer


@contextmanager
def _exclusive_run(directory):
    with (directory / "training.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def assistant_causal_loss(hidden, weight, labels, denominator=None, chunk=96):
    """Standard shifted full-vocabulary CE; recompute readout blocks during backward."""
    import torch
    from torch.utils.checkpoint import checkpoint

    shifted = labels[:, 1:]
    selected = shifted != -100
    targets = shifted[selected]
    states = hidden[:, :-1][selected]
    if not targets.numel():
        raise ValueError("full_language_empty_causal_target")

    def block_cost(inputs, expected):
        logits = torch.nn.functional.linear(inputs, weight)
        return torch.nn.functional.cross_entropy(
            logits.float(), expected, reduction="sum"
        )

    total = torch.zeros((), device=hidden.device, dtype=torch.float32)
    for block, expected in zip(states.split(chunk), targets.split(chunk)):
        total = total + checkpoint(
            block_cost, block, expected, use_reentrant=False, preserve_rng_state=False
        )
    return total / (targets.numel() if denominator is None else denominator)


def _adamw(parameters, learning_rate):
    from bitsandbytes.optim import AdamW8bit

    return AdamW8bit(
        parameters, lr=learning_rate, betas=(0.9, 0.95), eps=1e-8, weight_decay=0.0
    )


def _rate(step, total, peak):
    warmup = max(1, round(total * 0.03))
    if step <= warmup:
        return peak * step / warmup
    fraction = (step - warmup) / max(total - warmup, 1)
    return peak * (0.1 + 0.45 * (1 + math.cos(math.pi * fraction)))


def _microbatches(group):
    current = []
    for row in sorted(group, key=lambda x: len(x["input_ids"])):
        if current and (
            (len(current) + 1) * len(row["input_ids"]) > 896 or len(current) == 4
        ):
            yield current
            current = []
        current.append(row)
    if current:
        yield current


def _pad(rows, key, fill, device):
    import torch

    tensors = [torch.tensor(row[key], dtype=torch.long) for row in rows]
    return torch.nn.utils.rnn.pad_sequence(
        tensors, batch_first=True, padding_value=fill
    ).to(device)


def _training_examples(examples, eos, trailing, max_positions):
    from ..domain.validation import validate_supervision

    row_count = input_tokens = target_tokens = 0
    digest = hashlib.sha256()
    for example in examples:
        row = {
            key: list(example[key]) for key in ("input_ids", "attention_mask", "labels")
        }
        audit = validate_supervision(
            row, eos_token_id=eos, trailing_whitespace_ids=trailing
        )
        if len(row["input_ids"]) > max_positions or any(
            mask != 1 for mask in row["attention_mask"]
        ):
            raise ValueError(
                "full_language_training_requires_unpadded_complete_context"
            )
        digest.update(json.dumps(row, separators=(",", ":")).encode("ascii") + b"\n")
        row_count += 1
        input_tokens += audit["input_tokens"]
        target_tokens += audit["supervised_tokens"]
    if not row_count or row_count != len(examples):
        raise ValueError("full_language_training_examples_required")
    return dict(
        rows=row_count,
        inputs=input_tokens,
        targets=target_tokens,
        encoded_sha256=digest.hexdigest(),
    )


def _save_state(directory, masters, optimizer, progress, identity):
    import torch

    slot = (progress["checkpoints"] + 1) % 2
    progress["checkpoints"] += 1
    target = directory / f"state-{slot}.pt"
    temporary = target.with_suffix(".pending")
    torch.save(
        dict(
            masters=[p.detach() for p in masters],
            optimizer=optimizer.state_dict(),
            progress=progress,
            identity=identity,
            torch_rng=torch.get_rng_state(),
            cuda_rng=torch.cuda.get_rng_state(),
        ),
        temporary,
    )
    temporary.replace(target)
    write_json(
        directory / "current.json",
        dict(
            file=target.name,
            bytes=target.stat().st_size,
            sha256=file_sha(target),
            progress=progress,
            identity=identity,
        ),
    )


def _read_state(directory, identity):
    import torch

    pointer = json.loads((directory / "current.json").read_text(encoding="utf-8"))
    if pointer["file"] not in {"state-0.pt", "state-1.pt"}:
        raise ValueError("invalid_full_language_checkpoint_path")
    target = directory / pointer["file"]
    if (
        pointer["identity"] != identity
        or target.stat().st_size != pointer["bytes"]
        or file_sha(target) != pointer["sha256"]
    ):
        raise ValueError("full_language_checkpoint_identity_changed")
    snapshot = torch.load(target, map_location="cpu", weights_only=True)
    if snapshot["identity"] != identity or snapshot["progress"] != pointer["progress"]:
        raise ValueError("full_language_checkpoint_pointer_mismatch")
    return snapshot


def _resume(directory, masters, student, optimizer, identity):
    import torch

    if not (directory / "current.json").exists():
        return dict(
            rows=0,
            steps=0,
            targets=0,
            inputs=0,
            loss_sum=0.0,
            checkpoints=0,
            training_seconds=0.0,
        )
    state = _read_state(directory, identity)
    with torch.no_grad():
        for master, param, saved in zip(
            masters, student, state["masters"], strict=True
        ):
            master.copy_(saved)
            param.copy_(master)
    optimizer.load_state_dict(state["optimizer"])
    torch.set_rng_state(state["torch_rng"])
    torch.cuda.set_rng_state(state["cuda_rng"])
    return state["progress"]


def _first_gradients(directory, named, masters):
    import torch

    norms = (
        torch.stack(list(torch._foreach_norm([p.grad for p in masters])))
        .detach()
        .cpu()
        .tolist()
    )
    rows = [
        dict(name=name, elements=p.numel(), gradient_l2=norm)
        for (name, p), norm in zip(named, norms, strict=True)
    ]
    write_json(
        directory / "first-update-gradient-coverage.json",
        dict(
            step=1,
            tensors=len(rows),
            parameters=sum(row["elements"] for row in rows),
            nonzero_tensors=sum(n > 0 for n in norms),
            tensors_detail=rows,
        ),
    )


def _optimize(base, directory, examples, totals, identity, notify):
    import torch
    from transformers import set_seed

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("full_language_training_requires_bf16_cuda")
    set_seed(identity["seed"])
    model, tokenizer = qwen35_weights.load(base)
    model.requires_grad_(False)
    model.model.language_model.requires_grad_(True)
    model.lm_head.requires_grad_(True)
    model.train()
    model.model.visual.eval()
    model.config.use_cache = False
    model.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    prefix = "model.language_model."
    named = [(name, p) for name, p in model.named_parameters() if p.requires_grad]
    if not named or any(not name.startswith((prefix, "lm_head.")) for name, _ in named):
        raise ValueError("unsupported_full_language_parameters")
    student = [p for _, p in named]
    masters = [
        torch.nn.Parameter(p.detach().to(torch.float32, copy=True)) for p in student
    ]
    optimizer = _adamw(masters, identity["learning_rate"])
    progress = _resume(directory, masters, student, optimizer, identity)
    group_size = identity["gradient_accumulation"]
    total_steps = math.ceil(len(examples) / group_size)
    attempt, first_row = uuid.uuid4().hex, progress["rows"]
    device = model.get_input_embeddings().weight.device
    torch.cuda.reset_peak_memory_stats()
    trainable = sum(p.numel() for p in student)
    write_json(
        directory / "started.json",
        dict(
            identity=identity,
            resumed_rows=first_row,
            attempt=attempt,
            gpu=torch.cuda.get_device_name(),
            trainable_tensors=len(named),
            trainable_parameters=trainable,
            total_steps=total_steps,
        ),
    )
    last = time.monotonic()
    for start in range(first_row, len(examples), group_size):
        group = [
            examples[i] for i in range(start, min(start + group_size, len(examples)))
        ]
        target_count = sum(
            sum(label != -100 for label in row["labels"][1:]) for row in group
        )
        step, mean_loss = progress["steps"] + 1, 0.0
        for batch in _microbatches(group):
            ids = _pad(batch, "input_ids", tokenizer.pad_token_id, device)
            labels = _pad(batch, "labels", -100, device)
            mask = _pad(batch, "attention_mask", 0, device) if len(batch) > 1 else None
            hidden = model.model(
                input_ids=ids, attention_mask=mask, use_cache=False
            ).last_hidden_state
            loss = assistant_causal_loss(
                hidden, model.lm_head.weight, labels, denominator=target_count
            )
            loss.backward()
            mean_loss += float(loss.detach())
            del loss, hidden, ids, labels, mask
        for param, master in zip(student, masters, strict=True):
            if param.grad is None:
                raise RuntimeError("missing_full_language_gradient")
            master.grad = param.grad.detach().float()
            param.grad = None
        if step == 1:
            _first_gradients(directory, named, masters)
        norm = float(
            torch.nn.utils.clip_grad_norm_(masters, 1.0, error_if_nonfinite=True)
        )
        rate = _rate(step, total_steps, identity["learning_rate"])
        optimizer.param_groups[0]["lr"] = rate
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        with torch.no_grad():
            for param, master in zip(student, masters, strict=True):
                param.copy_(master)
        torch.cuda.synchronize()
        now = time.monotonic()
        seconds, last = now - last, now
        progress.update(
            rows=start + len(group),
            steps=step,
            targets=progress["targets"] + target_count,
            inputs=progress["inputs"] + sum(len(e["input_ids"]) for e in group),
            loss_sum=progress["loss_sum"] + mean_loss * target_count,
            training_seconds=progress["training_seconds"] + seconds,
        )
        event = dict(
            **progress,
            attempt=attempt,
            resumed_rows=first_row,
            step_loss=mean_loss,
            step_seconds=seconds,
            learning_rate=rate,
            unclipped_gradient_norm=norm,
            peak_cuda_bytes=torch.cuda.max_memory_allocated(),
            examples_total=len(examples),
            steps_total=total_steps,
        )
        write_json(directory / "progress.json", event)
        with (directory / "steps.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(event, allow_nan=False) + "\n")
        if notify:
            notify(event)
        stopping = (directory / "STOP").exists()
        if step == 1 or step % 48 == 0 or progress["rows"] == len(examples) or stopping:
            _save_state(directory, masters, optimizer, progress, identity)
        if stopping and progress["rows"] < len(examples):
            return dict(status="paused", **progress)
    if any(progress[key] != totals[key] for key in ("rows", "inputs", "targets")):
        raise RuntimeError("single_pass_training_coverage_mismatch")
    result = dict(
        status="completed",
        actual_epochs=1,
        **progress,
        identity=identity,
        trainable_tensors=len(named),
        trainable_parameters=trainable,
        parameter_names=[name for name, _ in named],
        vision_parameters=sum(p.numel() for p in model.model.visual.parameters()),
        token_weighted_mean_loss=progress["loss_sum"] / progress["targets"],
        peak_cuda_bytes=torch.cuda.max_memory_allocated(),
    )
    write_json(directory / "training-receipt.json", result)
    return result


def _export(base, directory, destination, completed):
    import gc
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file

    receipt_path = destination / "native-sft-receipt.json"
    training_receipt_sha256 = file_sha(directory / "training-receipt.json")
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["training_receipt_sha256"] != training_receipt_sha256 or any(
            file_sha(destination / f) != digest
            for f, digest in receipt["model_files"].items()
        ):
            raise ValueError("completed_native_model_artifacts_changed")
        return receipt
    if destination.exists():
        raise ValueError("existing_model_directory_has_no_training_receipt")
    original_files = {
        **completed["identity"]["source_model"],
        **completed["identity"]["assets"],
    }
    if any(file_sha(base / name) != digest for name, digest in original_files.items()):
        raise ValueError("native_export_base_changed_after_training")
    state = _read_state(directory, completed["identity"])
    if state["progress"]["rows"] != completed["rows"]:
        raise ValueError("final_native_weight_checkpoint_stale")
    masters = dict(zip(completed["parameter_names"], state.pop("masters"), strict=True))
    del state
    gc.collect()
    pending = directory / "export-pending"
    pending.mkdir(exist_ok=True)
    mapping = qwen35_weights.weight_map(base)
    changes, unchanged = {}, {}
    for shard in sorted(set(mapping.values())):
        tensors = {}
        with safe_open(base / shard, framework="pt", device="cpu") as original:
            for key in original.keys():
                old = original.get_tensor(key)
                if key in masters:
                    trained = masters.pop(key).to(dtype=old.dtype)
                    if not torch.isfinite(trained).all():
                        raise ValueError("nonfinite_deployed_language_tensor:" + key)
                    changes[key] = dict(
                        elements=old.numel(),
                        dtype=str(old.dtype),
                        changed=int(torch.count_nonzero(trained != old)),
                    )
                    tensors[key] = trained
                else:
                    tensors[key] = old
                    unchanged[key] = old.numel()
            save_file(
                tensors,
                pending / shard,
                metadata=original.metadata() or {"format": "pt"},
            )
        del tensors
    if masters:
        raise ValueError("full_language_parameters_missing_from_native_export")
    for name in completed["identity"]["assets"]:
        shutil.copyfile(base / name, pending / name)
    shutil.copyfile(qwen35_weights.__file__, pending / "load_native.py")
    reloaded, tokenizer = qwen35_weights.load(pending, device="cpu")
    original_tied = bool(reloaded.config.text_config.tie_word_embeddings)
    if (
        reloaded.get_input_embeddings().weight is reloaded.lm_head.weight
    ) != original_tied:
        raise ValueError("native_export_readout_tying_mismatch")
    precise = sum(
        p.dtype == torch.float32
        for name, p in reloaded.named_parameters()
        if name.endswith((".linear_attn.A_log", ".linear_attn.norm.weight"))
    )
    # Include both native document and chat stops when upstream only declared document end.
    configured = reloaded.generation_config.eos_token_id
    stops = configured if isinstance(configured, list) else [configured]
    reloaded.generation_config.eos_token_id = list(
        dict.fromkeys(x for x in [*stops, tokenizer.eos_token_id] if x is not None)
    )
    reloaded.generation_config.pad_token_id = tokenizer.pad_token_id
    reloaded.generation_config.save_pretrained(pending)
    del reloaded, tokenizer
    gc.collect()
    for shard in sorted(set(mapping.values())):
        with (
            safe_open(base / shard, framework="pt", device="cpu") as original,
            safe_open(pending / shard, framework="pt", device="cpu") as saved,
        ):
            if set(original.keys()) != set(saved.keys()):
                raise ValueError("native_export_inventory_mismatch")
            for key in saved.keys():
                if key in unchanged and not torch.equal(
                    original.get_tensor(key), saved.get_tensor(key)
                ):
                    raise ValueError("native_asset_copy_mismatch:" + key)
    write_json(
        directory / "deployed-weight-change.json",
        dict(
            language_tensors=changes,
            passthrough_tensor_count=len(unchanged),
            passthrough_parameters=sum(unchanged.values()),
        ),
    )
    card = (
        "# Native Qwen3.5 full-language SFT\n\n"
        f"This model completed one pass over {completed['rows']:,} assistant turns "
        f"with {completed['targets']:,} supervised tokens. The checkpoint contains the "
        "updated language model and the original visual and auxiliary tensors.\n\n"
        "Run this from the model directory to preserve the stored Gated DeltaNet precision.\n\n"
        "```python\nfrom load_native import load\n\nmodel, tokenizer = load(device='cuda:0')\n```\n\n"
        "The tokenizer provides the original chat template. The model directory also supports "
        "the standard Transformers loading API. The training and deployment audit is recorded "
        "in native-sft-receipt.json.\n"
    )
    (pending / "README.md").write_text(card, encoding="utf-8")
    receipt = dict(
        schema="native-qwen35-full-language/v1",
        training_receipt_sha256=training_receipt_sha256,
        official_hf_layout=True,
        needs_adapter_runtime=False,
        rows=completed["rows"],
        supervised_tokens=completed["targets"],
        training_epochs=completed["actual_epochs"],
        trainable_parameters=completed["trainable_parameters"],
        total_language_tensors=len(changes),
        changed_language_tensors=sum(x["changed"] > 0 for x in changes.values()),
        changed_language_parameters=sum(x["changed"] for x in changes.values()),
        passthrough_tensors_verified=len(unchanged),
        fp32_gdn=precise,
        tied_output_embedding=original_tied,
        model_files={
            p.name: file_sha(p)
            for p in pending.iterdir()
            if p.is_file() and p.name != "native-sft-receipt.json"
        },
    )
    write_json(pending / "native-sft-receipt.json", receipt)
    pending.rename(destination)
    return receipt


def train_full_language(
    model_path,
    examples,
    output_path,
    *,
    source_sha256,
    learning_rate=8e-6,
    gradient_accumulation=8,
    seed=20261119,
    progress=None,
):
    """Fit every native language parameter once and return the reload-verified HF model.

    Examples must be repeatably indexable and contain unpadded native input_ids,
    attention_mask and assistant-only labels, including their native chat EOS.
    """
    from transformers import AutoConfig, AutoTokenizer

    base, output = Path(model_path).resolve(), Path(output_path).resolve()
    if base == output or base in output.parents or (output / "model") == base:
        raise ValueError("training_output_must_be_separate_from_base")
    validate_full_sft_optimizer(learning_rate, gradient_accumulation)
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(c not in "0123456789abcdef" for c in source_sha256)
    ):
        raise ValueError("full_language_source_digest_required")
    config = AutoConfig.from_pretrained(
        base, local_files_only=True, trust_remote_code=False
    )
    if config.model_type != "qwen3_5":
        raise ValueError("full_language_training_requires_native_qwen35")
    tokenizer = AutoTokenizer.from_pretrained(
        base, local_files_only=True, trust_remote_code=False
    )
    trailing = tokenizer.encode("\n", add_special_tokens=False)
    totals = _training_examples(
        examples,
        tokenizer.eos_token_id,
        trailing,
        config.text_config.max_position_embeddings,
    )
    assets = {
        "config.json",
        "generation_config.json",
        "model.safetensors.index.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
        "special_tokens_map.json",
        "added_tokens.json",
        "vocab.json",
        "merges.txt",
        "tokenizer.model",
        "preprocessor_config.json",
        "processor_config.json",
        "video_preprocessor_config.json",
        "audio_preprocessor_config.json",
        "LICENSE",
    }
    identity = dict(
        source_sha256=source_sha256,
        encoded_sha256=totals["encoded_sha256"],
        source_model={
            s: file_sha(base / s)
            for s in sorted(set(qwen35_weights.weight_map(base).values()))
        },
        assets={a: file_sha(base / a) for a in sorted(assets) if (base / a).is_file()},
        training_implementation=file_sha(__file__),
        loader_implementation=file_sha(qwen35_weights.__file__),
        algorithm="native BF16, FP32 master parameters, AdamW8bit, beta=(0.9,0.95), norm clip 1, cosine rate with 3% warmup",
        learning_rate=learning_rate,
        gradient_accumulation=gradient_accumulation,
        seed=seed,
        epoch_limit=1,
    )
    directory = output / "full-training"
    directory.mkdir(parents=True, exist_ok=True)
    with _exclusive_run(directory):
        binding = directory / "identity.json"
        if (
            binding.exists()
            and json.loads(binding.read_text(encoding="utf-8")) != identity
        ):
            raise ValueError("full_language_training_identity_changed")
        write_json(binding, identity)
        trained = directory / "training-receipt.json"
        if trained.exists():
            result = json.loads(trained.read_text(encoding="utf-8"))
            if (
                result["identity"] != identity
                or result["actual_epochs"] != 1
                or result["rows"] != totals["rows"]
            ):
                raise ValueError("completed_full_language_receipt_mismatch")
        elif (directory / "STOP").exists():
            return {"status": "paused"}
        else:
            result = _optimize(base, directory, examples, totals, identity, progress)
            if result["status"] != "completed":
                return result
        package = _export(base, directory, output / "model", result)
        return dict(
            status="completed",
            actual_epochs=1,
            rows=result["rows"],
            model=str(output / "model"),
            package=package,
        )
