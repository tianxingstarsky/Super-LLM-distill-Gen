"""Pure-hidden recurrent research: one ordered epoch or explicit observation.

Each route carries its own continuous state. Shared-backbone feedback is
blockwise and uses actual predicted vectors, never teacher vectors or tokens.
"""
from __future__ import annotations

import argparse
import gc
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


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _forward(base, cache, inputs):
    """Publish a block into the one live cache with upstream Qwen equations."""
    import torch
    from torch.nn.attention import SDPBackend, sdpa_kernel
    position = cache.get_seq_length()
    positions = torch.arange(position, position + inputs.shape[1], device=inputs.device)[None]
    # After an image the next text token's rotary position can differ from
    # its physical KV slot. Keep this offset with the cache it belongs to.
    offset = getattr(cache, "image_rope_delta", None)
    if offset is not None:
        spatial = (positions + offset).unsqueeze(0).expand(3, -1, -1)
        positions = torch.cat((positions.unsqueeze(0), spatial))
    # A real long-answer CUDA backward exposed an upstream fused-mask stride
    # restriction. Select the supported native math backend in both phases.
    with sdpa_kernel(SDPBackend.MATH):
        out = base.model.language_model(inputs_embeds=inputs,
            attention_mask=torch.ones((1, position + inputs.shape[1]), dtype=torch.long, device=inputs.device),
            position_ids=positions,
            past_key_values=cache, use_cache=True)
    if out.past_key_values is not cache:
        raise RuntimeError("backbone_replaced_shared_cache")
    return out.last_hidden_state


def _prefill(base, prefix_ids, *, differentiable, train_prefix=False):
    import torch
    from transformers.cache_utils import DynamicCache
    from experiments.nplus_shared_cache.differentiable_cache import DifferentiableQwen35Cache
    device = base.get_input_embeddings().weight.device
    ids = torch.tensor([prefix_ids], device=device)
    if hasattr(base.model, "rope_deltas"):
        base.model.rope_deltas = None
    config = getattr(base.config, "text_config", base.config)
    cache = DifferentiableQwen35Cache(config) if differentiable else DynamicCache(config=config)
    with torch.set_grad_enabled(torch.is_grad_enabled() and differentiable and train_prefix):
        embeds = base.get_input_embeddings()(ids)
        hidden = _forward(base, cache, embeds)[:, -1].clone()
    return cache, hidden, embeds


def _goals(base, tokenizer):
    """Encode real purpose descriptions without any question, answer or target."""
    import torch
    from experiments.nplus_shared_cache.recurrent_contract import GOAL_DESCRIPTIONS
    states = []
    with torch.no_grad():
        for purpose in runner.PURPOSES:
            text = tokenizer.apply_chat_template([{"role": "user", "content": GOAL_DESCRIPTIONS[purpose]}],
                tokenize=False, add_generation_prompt=True, enable_thinking=True)
            _, hidden, _ = _prefill(base, tokenizer.encode(text, add_special_tokens=False), differentiable=False)
            states.append(hidden[0].float())
    return torch.stack(states)


def known_prefix_hidden(base, prefixes):
    """Encode original questions/direction instructions, not teacher outputs.

    No branch cache survives these temporary, frozen, uncached forwards.
    """
    import torch
    from torch.nn.attention import SDPBackend, sdpa_kernel
    device = base.get_input_embeddings().weight.device
    vectors = []
    with torch.no_grad(), sdpa_kernel(SDPBackend.MATH):
        for ids in prefixes:
            inputs = base.get_input_embeddings()(torch.tensor([ids], device=device))
            out = base.model.language_model(inputs_embeds=inputs,
                attention_mask=torch.ones((1, len(ids)), dtype=torch.long, device=device),
                position_ids=torch.arange(len(ids), device=device)[None], use_cache=False)
            vectors.append(out.last_hidden_state[0, -1].float().clone())
            del out
    return torch.stack(vectors)[None]


def _inject(auxiliary, vectors, active, reference_rms, dtype):
    import torch
    masked = torch.where(active[None, :, None], vectors, torch.zeros_like(vectors))
    cat = masked.flatten(1)
    fused = auxiliary.fusion(cat)
    aligned = fused * reference_rms / fused.square().mean(-1, keepdim=True).sqrt().clamp_min(1e-8)
    return (aligned * auxiliary.gain).to(dtype), cat


def _ce(base, hidden, labels, chunk=16):
    import torch
    import torch.nn.functional as F
    from torch.utils.checkpoint import checkpoint
    valid = labels != -100
    if not bool(valid.any()):
        return hidden.sum() * 0
    hidden, labels = hidden[valid], labels[valid]

    def part(states, expected):
        logits = base.lm_head(states.to(base.lm_head.weight.dtype)).float()
        return F.cross_entropy(logits, expected, reduction="sum")

    terms = [checkpoint(part, hidden[i:i + chunk], labels[i:i + chunk], use_reentrant=False)
        for i in range(0, len(labels), chunk)]
    return torch.stack(terms).sum() / len(labels)


def _chunk(base, auxiliary, cache, shared, previous, memory, reference_rms,
           teacher, labels, active, *, closing_ids=None, answer_ids=None):
    """Actual recurrent inputs; gold tensors are loss targets and lifetime masks."""
    import torch
    import torch.nn.functional as F
    predicted, injected = [], []
    first = None
    for t in range(len(labels)):
        proposed, memory = auxiliary.forward_step(shared.float(), previous, memory)
        # A completed route retains no contribution to CAT or future active q.
        previous = torch.where(active[t][None, :, None], proposed, torch.zeros_like(proposed))
        z, _ = _inject(auxiliary, previous, active[t], reference_rms, base.get_input_embeddings().weight.dtype)
        if first is None:
            first = previous
        predicted.append(previous[0])
        injected.append(z)
    states = torch.stack(predicted)
    embeddings = torch.stack(injected, dim=1)
    # All routes see the last committed shared state while forming this block.
    # The real predicted block then updates the shared state for the next block.
    hidden = _forward(base, cache, embeddings)
    shared_next = hidden[:, -1]
    token_ce = _ce(base, states, labels)
    alignment = (F.normalize(states[active], dim=-1)
        - F.normalize(teacher[active].float(), dim=-1)).square().sum(-1).mean()
    answer_ce = token_ce.new_zeros(())
    if answer_ids is not None:
        ids = torch.tensor([closing_ids + answer_ids[:-1]], device=shared.device)
        answer_inputs = base.get_input_embeddings()(ids)
        # Native saved-tensor offload retains the full answer objective and
        # real cache gradient without keeping its long activations on the GPU.
        with torch.autograd.graph.save_on_cpu(pin_memory=False):
            answer_hidden = _forward(base, cache, answer_inputs)[0]
            start = len(closing_ids) - 1
            answer_ce = _ce(base, answer_hidden[start:start + len(answer_ids)],
                torch.tensor(answer_ids, device=shared.device))
    return {"states": states, "first": first, "embeddings": embeddings, "token_ce": token_ce,
        "alignment": alignment, "answer_ce": answer_ce, "shared": shared_next,
        "previous": previous, "memory": memory}


def _save(auxiliary, folder, manifest, *, step, completed_records):
    import torch
    from safetensors.torch import save_file
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / "auxiliary.safetensors.tmp"
    save_file({k: v.detach().cpu().contiguous().clone() for k, v in auxiliary.state_dict().items()},
        str(temporary), metadata={"format": "pt", "architecture": "recurrent_hidden_five_route/v4",
            "identity": manifest["identity"]})
    runner.replace_with_retry(temporary, folder / "auxiliary.safetensors")
    runner.write_json(folder / "auxiliary-config.json", {"identity": manifest["identity"],
        "architecture": "recurrent_hidden_five_route/v4",
        **manifest["binding"], "global_steps": step, "completed_records": completed_records,
        "auxiliary_file_sha256": runner.file_sha(folder / "auxiliary.safetensors")})


def _unit(base, auxiliary, cache, shared, previous, memory, reference,
          tensors, begin, end, record, *, block_size, validate_only):
    """Two committed blocks per backward keep the feedback gradient alive.

    The second block's token loss can reach the first block's fusion through
    the real backbone. Detaching after every single commit would cut that edge.
    """
    import torch
    chunks = []
    for start in range(begin, end, block_size):
        stop = min(end, start + block_size)
        answer = stop == len(tensors["labels"]) or (validate_only and stop == end)
        part = _chunk(base, auxiliary, cache, shared, previous, memory, reference,
            tensors["teacher_heads"][start:stop], tensors["labels"][start:stop],
            tensors["active"][start:stop].bool(),
            closing_ids=record["closing_ids"] if answer else None,
            answer_ids=record["answer_ids"] if answer else None)
        shared, previous, memory = part["shared"], part["previous"], part["memory"]
        chunks.append(part)
    return {"first": chunks[0]["first"], "shared": shared, "previous": previous,
        "memory": memory, "token_ce": torch.stack([x["token_ce"] for x in chunks]).mean(),
        "alignment": torch.stack([x["alignment"] for x in chunks]).mean(),
        "answer_ce": chunks[-1]["answer_ce"], "committed_blocks": len(chunks)}


def train(args, tokenizer, manifest, records, *, validate_only=False):
    import torch
    from safetensors.torch import load_file
    from experiments.nplus_shared_cache.recurrent_heads import RecurrentDirectionHeads, warm_start_fusion
    output = Path(args.output)
    validation_folder = ("gradient-capacity-validation" if args.action == "capacity" else "gradient-validation")
    training = output / (validation_folder if validate_only else "training")
    if (training / "started.json").exists():
        raise ValueError("run_already_started_preserve_original_receipt")
    training.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(42)
    base, loaded = runner.load_frozen_model(args.model)
    runner.write_json(training / "model-load.json", loaded)
    device = base.get_input_embeddings().weight.device
    auxiliary = RecurrentDirectionHeads(manifest["binding"]["width"],
        memory_width=args.memory_width, rank=args.rank).to(device)
    continuation = manifest["binding"].get("continuation")
    if continuation:
        auxiliary.load_state_dict(load_file(str(Path(args.continue_from) / "auxiliary.safetensors"),
            device=str(device)), strict=True)
    else:
        warm_start_fusion(auxiliary, args.initial_auxiliary,
            expected_sha256=manifest["binding"]["initial_auxiliary_sha256"])
        auxiliary.set_goal_states(_goals(base, tokenizer))
    auxiliary.train()
    fusion_params = [*auxiliary.fusion.parameters(), auxiliary.gain]
    fusion_ids = {id(p) for p in fusion_params}
    heads = [p for p in auxiliary.parameters() if p.requires_grad and id(p) not in fusion_ids]
    optimizer = torch.optim.AdamW([{"params": heads, "lr": args.head_learning_rate},
        {"params": fusion_params, "lr": args.fusion_learning_rate}], weight_decay=0)
    runner.write_json(training / "started.json", {"identity": manifest["identity"],
        "requested_epochs": 0 if validate_only else 1, "optimizer_steps_allowed": not validate_only,
        "trainable_parameters": sum(p.numel() for p in auxiliary.parameters() if p.requires_grad)})
    snapshot = {n: p.detach().cpu().clone() for n, p in auxiliary.named_parameters()}
    archive = training / "executed-source"
    archive.mkdir(exist_ok=True)
    for name in ("recurrent_runtime.py", "recurrent_heads.py", "recurrent_contract.py", "differentiable_cache.py"):
        shutil.copyfile(Path(__file__).with_name(name), archive / name)
    start = time.perf_counter()
    torch.cuda.reset_peak_memory_stats(device)
    step, completed_records = 0, 0
    try:
        start_record = continuation["start_record"] if continuation else 0
        available = list(enumerate(records))[start_record:]
        chosen = ([max(available, key=lambda pair: len(pair[1]["answer_ids"]))]
            if args.action == "capacity" else available)
        for index, record in chosen:
            tensors = load_file(str(Path(args.prepared) / record["targets_file"]), device=str(device))
            cache, shared, prefix = _prefill(base, record["prefix_ids"], differentiable=True)
            before_prefix = runner.cache_snapshot(cache, len(record["prefix_ids"]))
            reference = prefix.float().square().mean().sqrt()
            previous, memory = auxiliary.initialize(shared.float())
            initial_residual = previous - shared.float()[:, None]
            previous = known_prefix_hidden(base, record["teacher_prefix_ids"]) + initial_residual
            count = len(tensors["labels"])
            for begin in range(0, count, 2 * args.block_size):
                if (output / "STOP").exists():
                    raise RuntimeError("explicit_training_stop_no_completed_epoch_claim")
                end = min(count, begin + 2 * args.block_size)
                optimizer.zero_grad(set_to_none=True)
                previous_start = previous.detach()
                block = _unit(base, auxiliary, cache, shared, previous, memory, reference,
                    tensors, begin, end, record, block_size=args.block_size, validate_only=validate_only)
                loss = block["token_ce"] + args.alignment_weight * block["alignment"] + block["answer_ce"]
                if not torch.isfinite(loss):
                    raise RuntimeError("nonfinite_recurrent_training_loss")
                if validate_only:
                    all_parameters = [*heads, *fusion_params]
                    answer_grad = torch.autograd.grad(block["answer_ce"], all_parameters + [block["first"]],
                        retain_graph=True, allow_unused=True)
                    head_norm = math.sqrt(sum(float(g.float().square().sum()) for g in answer_grad[:len(heads)] if g is not None))
                    fusion_norm = math.sqrt(sum(float(g.float().square().sum()) for g in answer_grad[len(heads):-1] if g is not None))
                    first_norm = float(answer_grad[-1].float().norm()) if answer_grad[-1] is not None else 0
                    route_norms = (answer_grad[-1][0].float().norm(dim=-1).tolist()
                        if answer_grad[-1] is not None else [0.] * 5)
                    token_feedback_grad = torch.autograd.grad(block["token_ce"], fusion_params,
                        retain_graph=True, allow_unused=True)
                    feedback_norm = math.sqrt(sum(float(g.float().square().sum())
                        for g in token_feedback_grad if g is not None))
                    if min(head_norm, fusion_norm, first_norm, *route_norms) <= 0:
                        raise RuntimeError("answer_loss_not_connected_to_real_heads_fusion_and_early_state")
                    if block["committed_blocks"] == 2 and feedback_norm <= 0:
                        raise RuntimeError("second_block_token_loss_not_connected_to_prior_fusion")
                loss.backward()
                gradient = float(torch.nn.utils.clip_grad_norm_(auxiliary.parameters(), 1.0))
                if not math.isfinite(gradient):
                    raise RuntimeError("nonfinite_recurrent_training_gradient")
                record_loss = {"record_index": index, "record_key": record["record_key"],
                    "begin": begin, "end": end, "global_step": step,
                    "token_ce": float(block["token_ce"].detach()), "answer_ce": float(block["answer_ce"].detach()),
                    "alignment": float(block["alignment"].detach()), "total": float(loss.detach()),
                    "gradient_l2_before_clip": gradient, "cache_length": cache.get_seq_length(),
                    "route_q_l2": block["previous"][0].detach().norm(dim=-1).tolist(),
                    "route_q_change_l2": (block["previous"] - previous_start)[0].detach().norm(dim=-1).tolist(),
                    "block_shared_context_is_constant": True, "teacher_vectors_used_as_inputs": False}
                if validate_only:
                    torch.cuda.synchronize(device)
                    receipt = {"identity": manifest["identity"], "optimizer_steps": 0,
                        "answer_CE_head_parameter_gradient_l2": head_norm,
                        "answer_CE_fusion_gradient_l2": fusion_norm,
                        "answer_CE_first_predicted_vector_gradient_l2": first_norm,
                        "answer_CE_first_predicted_vector_per_route_gradient_l2": route_norms,
                        "token_CE_via_real_backbone_to_prior_fusion_gradient_l2": feedback_norm,
                        "full_chunk_backward_succeeded": True, "actual_block": record_loss,
                        "before_prefix": before_prefix,
                        "after_prefix": runner.cache_snapshot(cache, len(record["prefix_ids"])),
                        "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(device),
                        "seconds": time.perf_counter() - start}
                    runner.write_json(training / "receipt.json", receipt)
                    return receipt
                optimizer.step()
                step += 1
                detached = cache.detach_history()
                shared = block["shared"].detach()
                previous, memory = block["previous"].detach(), block["memory"].detach()
                record_loss["cache_detach"] = detached
                with (training / "loss-history.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record_loss) + "\n")
                runner.write_json(training / "progress.json", {**record_loss, "completed_records": completed_records,
                    "fraction_of_one_epoch": ((continuation["parent_seen_fraction_of_epoch"] if continuation else 0.)
                        + (index - start_record + end / count) / len(records)),
                    "elapsed_seconds": time.perf_counter() - start, "optimizer_steps": step})
                del block, loss
            completed_records += 1
            # Save the completed record before proceeding. This bounds lost
            # parameter updates if a later record fails; no data is replayed.
            _save(auxiliary, training / "latest", manifest,
                step=step, completed_records=index + 1)
            if completed_records % 8 == 0 or completed_records == len(records):
                _save(auxiliary, training / ("checkpoint-" + str(completed_records)), manifest,
                    step=step, completed_records=index + 1)
            print(json.dumps({"record": completed_records, "of": len(records), "step": step,
                "seconds": time.perf_counter() - start, "last_token_ce": record_loss["token_ce"]}), flush=True)
            del cache, prefix, tensors, shared, previous, memory
            gc.collect()
        _save(auxiliary, training, manifest, step=step, completed_records=len(records))
        torch.cuda.synchronize(device)
        deltas = {n: float((p.detach().cpu() - snapshot[n]).norm()) for n, p in auxiliary.named_parameters()}
        actual_epoch = ((continuation["parent_seen_fraction_of_epoch"] if continuation else 0.)
            + completed_records / len(records))
        if actual_epoch > 1.000001:
            raise RuntimeError("cumulative_one_epoch_limit_exceeded")
        receipt = {"identity": manifest["identity"], "status": "completed_remaining_records" if continuation else "completed",
            "actual_epoch": actual_epoch, "source_record_cursor": len(records), "continuation": continuation,
            "completed_records": completed_records, "optimizer_steps": step,
            "seconds": time.perf_counter() - start, "parameter_delta_l2": deltas,
            "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(device),
            "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(device),
            "auxiliary_sha256": runner.file_sha(training / "auxiliary.safetensors"),
            "backbone_trainable_parameters": sum(p.numel() for p in base.parameters() if p.requires_grad),
            "observation_token_feedback": False, "teacher_hidden_feedback": False,
            "training_route_lifetime": "gold_active_masks_only_in_training",
            "gradient_horizon": 2 * args.block_size, "global_feedback_block_size": args.block_size,
            "cache_history_spans_earlier_parameter_versions": True,
            "full_answer_saved_tensors_offloaded_to_CPU": True,
            "full_sequence_BPTT": False, "paid_api_calls": 0,
            "free_generation_quality_established": False}
        runner.write_json(training / "training-receipt.json", receipt)
        return receipt
    except Exception:
        runner.write_json(training / "failure.json", {"identity": manifest["identity"],
            "completed_records": completed_records, "optimizer_steps": step,
            "traceback": traceback.format_exc(), "not_a_completed_epoch": True})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("prepare", "validate", "capacity", "train"), default="prepare")
    for name in ("model", "prepared", "initial-auxiliary", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--block-size", type=int, default=8)
    parser.add_argument("--memory-width", type=int, default=512)
    parser.add_argument("--rank", type=int, default=64)
    parser.add_argument("--head-learning-rate", type=float, default=3e-4)
    parser.add_argument("--fusion-learning-rate", type=float, default=1e-4)
    parser.add_argument("--alignment-weight", type=float, default=.05)
    parser.add_argument("--continue-from")
    parser.add_argument("--continuation-failure")
    args = parser.parse_args()
    from experiments.nplus_shared_cache.recurrent_contract import prepare_training
    tokenizer, manifest, records = prepare_training(Path(args.model), Path(args.prepared),
        Path(args.initial_auxiliary), Path(args.output), block_size=args.block_size,
        memory_width=args.memory_width, rank=args.rank, head_lr=args.head_learning_rate,
        fusion_lr=args.fusion_learning_rate, alignment_weight=args.alignment_weight,
        continuation_checkpoint=Path(args.continue_from) if args.continue_from else None,
        continuation_failure=Path(args.continuation_failure) if args.continuation_failure else None)
    print(json.dumps({"identity": manifest["identity"], "records": len(records), "action": args.action}), flush=True)
    if args.action != "prepare":
        print(json.dumps(train(args, tokenizer, manifest, records,
            validate_only=args.action in {"validate", "capacity"})), flush=True)


if __name__ == "__main__":
    main()
