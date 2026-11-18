"""Frozen decoder handoff: replay identical source trajectories under different layouts."""
from __future__ import annotations

import argparse
import copy
import hashlib
import time
from pathlib import Path

import torch
from safetensors.torch import load_file, save_file

from . import recurrent_runtime as runtime, runner
from .parallel_observer import route_transition
from .recurrent_contract import prepare_cases
from .recurrent_heads import RecurrentDirectionHeads
from .recurrent_observer import contextual_initialization


ARMS = ("prompt_only", "cat_1", "cat_repeat_5", "cat_split_5", "separate_projected_5",
        "permuted_projected_5", "separate_raw_5", "zero_5")


def digest(tensor):
    data = tensor.detach().cpu().contiguous().reshape(-1).view(torch.uint8).numpy()
    return hashlib.sha256(data.tobytes()).hexdigest()


def collect(base, tokenizer, auxiliary, case, config):
    cache, shared, prefix = runtime._prefill(base, case["prefix_ids"], differentiable=False)
    alive = torch.ones(5, dtype=torch.bool, device=shared.device)
    previous, memory = auxiliary.initialize(shared.float())
    previous = contextual_initialization(base, tokenizer, case) + (previous - shared.float()[:, None])
    rms = prefix.float().square().mean().sqrt()
    endings = {config["ending_token_id"]}
    closing = tokenizer.encode("</think>", add_special_tokens=False)
    if len(closing) == 1:
        endings.add(closing[0])
    vectors, masks, fused = [], [], []
    while bool(alive.any()) and len(masks) < 1024:
        pending = []
        for _ in range(config["block_size"]):
            proposed, memory = auxiliary.forward_step(shared.float(), previous, memory)
            ids = base.lm_head(proposed[0].to(base.lm_head.weight.dtype)).argmax(-1)
            masked, after = route_transition(proposed, ids, alive, endings)
            embedding, _ = runtime._inject(auxiliary, masked, alive, rms, prefix.dtype)
            vectors.append(proposed[0])
            masks.append(alive)
            fused.append(embedding[0])
            pending.append(embedding)
            previous = torch.where(after[None, :, None], proposed, 0.)
            memory = torch.where(after[None, :, None], memory, 0.)
            alive = after
            if not bool(alive.any()):
                break
        shared = runtime._forward(base, cache, torch.stack(pending, dim=1))[:, -1]
    if bool(alive.any()):
        raise ValueError("refusing to treat a truncated diagnostic as a full source trace")
    return torch.stack(vectors), torch.stack(masks), torch.stack(fused)


def layouts(auxiliary, q, active, fused, prefix, block):
    enabled = active[..., None]
    count = enabled.sum(dim=1, keepdim=True)
    if not count.all():
        raise ValueError("empty latent timestep")
    masked = torch.where(enabled, q, 0.)
    width = q.shape[-1]
    weight = auxiliary.fusion.weight
    projected = torch.stack([torch.nn.functional.linear(masked[:, slot], weight[:, slot*width:(slot+1)*width])
                             for slot in range(5)], dim=1)
    projected += enabled * auxiliary.fusion.bias / count
    raw_cat = auxiliary.fusion(masked.flatten(1))
    scale = prefix.float().square().mean().sqrt() * auxiliary.gain
    common = scale / raw_cat.square().mean(dim=-1, keepdim=True).sqrt().clamp_min(1e-8)
    contributions = projected * common[:, None]
    ideal = raw_cat * common
    error = (contributions.sum(1) - ideal).norm() / ideal.norm()
    native_error = (fused.float() - ideal).norm() / ideal.norm()
    if not (float(error) < 1e-4 and float(native_error) < .005):
        raise ValueError(f"split does not reconstruct trained projection: {error}, {native_error}")
    expanded = enabled * fused[:, None].float()
    positions = torch.arange(5, device=q.device).expand_as(active)
    active_first = torch.where(active, positions, positions + 5).argsort(dim=1)
    reflected = (count.squeeze(-1) - active.long().cumsum(dim=1)).clamp_min(0)
    reversed_slots = torch.where(active, active_first.gather(1, reflected), positions)
    reversed_parts = contributions.gather(1, reversed_slots[..., None].expand_as(contributions))
    raw_scale = scale / masked.square().mean(dim=-1, keepdim=True).sqrt().clamp_min(1e-8)
    variants = {
        "prompt_only": fused[:0], "cat_1": fused, "cat_repeat_5": expanded,
        "cat_split_5": expanded / count, "separate_projected_5": contributions,
        "permuted_projected_5": reversed_parts, "separate_raw_5": masked * raw_scale,
        "zero_5": torch.zeros_like(expanded),
    }
    for array in variants.values():
        if array.ndim == 3 and torch.count_nonzero(array[~active]):
            raise RuntimeError("nonzero inactive slot")
    return {name: (array.reshape(-1,width).to(prefix.dtype), block if array.ndim==2 else 5*block)
            for name,array in variants.items()}, float(error), float(native_error)


def score(base, tokenizer, cache, cue, choices):
    device = base.lm_head.weight.device
    encode = lambda text: tokenizer.encode(text, add_special_tokens=False)
    prefix_ids = encode(runner.BOUNDARY + cue)
    token_ids, logp = [], []
    for choice in choices:
        ids = encode(runner.BOUNDARY + cue + choice)
        if ids[:len(prefix_ids)] != prefix_ids or len(ids) <= len(prefix_ids):
            raise ValueError(f"choice merges across fixed cue: {cue!r} + {choice!r}")
        label = ids[len(prefix_ids):]
        token_ids.append(label)
        fork = copy.deepcopy(cache)
        text = base.get_input_embeddings()(torch.tensor([ids[:-1]], device=device))
        hidden = runtime._forward(base, fork, text)[0, -len(label):]
        values = base.lm_head(hidden).float().log_softmax(-1)
        logp.append(float(values[torch.arange(len(label), device=device),
                                 torch.tensor(label, device=device)].sum()))
    if len(token_ids[0]) != len(token_ids[1]):
        raise ValueError("unequal-length foil")
    return dict(choices=choices, token_ids=token_ids, logp=logp,
                margin=logp[0]-logp[1], correct=logp[0]>logp[1], cue=cue)


@torch.inference_mode()
def run(args):
    started = time.perf_counter()
    args.output.mkdir(parents=True, exist_ok=False)
    original = runtime._json(args.replay/"source-case.json")
    if original.get("reference_answer") != "105" or original.get("data_role") != "training_inside_reconstruction_not_generalization":
        raise ValueError("expected the archived 105 mechanism probe")
    cases = [original]
    fields = [{"number": ("The answer is ", ["105", "104"])}]
    for fixture in runtime._json(args.fixtures)["pairs"]:
        for side, day in enumerate(fixture["days"]):
            cases.append(dict(case_id=f"{fixture['id']}-{side}",
                messages=[dict(msg,content=msg["content"].replace("{day}",day)) for msg in fixture["messages"]]))
            fields.append({"day": (fixture["date_prefix"], ["周"+day, "周"+fixture["days"][1-side]]),
                           "contact": (fixture["contact_prefix"], fixture["contacts"])})
    runner.write_json(args.output/"cases.json", cases)
    tokenizer, prepared = prepare_cases(args.model, args.checkpoint, args.output/"cases.json", args.output/"inputs")
    config = runtime._json(args.checkpoint/"auxiliary-config.json")
    binding = runtime._json(args.replay.parent/"manifest.json")["binding"]
    if binding["checkpoint_sha256"] != config["auxiliary_file_sha256"]:
        raise ValueError("archived checkpoint mismatch")
    base, _ = runner.load_frozen_model(args.model)
    auxiliary = RecurrentDirectionHeads(config["width"],config["memory_width"],config["rank"]).cuda()
    auxiliary.load_state_dict(load_file(str(args.checkpoint/"auxiliary.safetensors"), device="cuda"))
    auxiliary.eval().requires_grad_(False)
    rows, traces = [], []
    for index, case in enumerate(prepared):
        prefix_ids = case["prefix_ids"]
        embeddings = base.get_input_embeddings()(torch.tensor([prefix_ids],device="cuda"))
        if index == 0:
            receipt = runtime._json(args.replay/"receipt.json")
            archived = load_file(str(args.replay/"full-q-trace.safetensors"))
            q, active = archived["all_proposed_q"], archived["alive_before"]
            realized = archived["main_realized_inputs"]
            if (prefix_ids != runtime._json(args.replay/"prefix-ids.json")
                    or digest(q) != receipt["q_sha256"] or digest(realized) != receipt["realized_inputs_sha256"]
                    or not torch.equal(realized[:, :len(prefix_ids)], embeddings.cpu())
                    or active.long().sum(0).tolist() != binding["route_ending_rounds"]):
                raise ValueError("q, mask, prefix or handoff source mismatch")
            q, active, fused = q.cuda(), active.cuda(), realized[0, len(prefix_ids):].cuda()
            del archived, realized
        else:
            q, active, fused = collect(base, tokenizer, auxiliary, case, config)
            save_file({"all_proposed_q": q.cpu(), "alive_before": active.cpu(), "fused": fused.cpu()},
                      str(case["output"]/"handoff-trace.safetensors"))
        options, error, native_error = layouts(auxiliary,q,active,fused,embeddings,config["block_size"])
        traces.append(dict(case_id=case["case_id"], q_sha256=digest(q), mask_sha256=digest(active),
                           native_fused_sha256=digest(fused), endings=active.sum(0).tolist(),
                           reconstruction_relative_error=error, native_bf16_relative_error=native_error))
        for arm in ARMS:
            since = time.perf_counter()
            cache, _, _ = runtime._prefill(base,prefix_ids,differentiable=False)
            values, chunk = options[arm]
            for part in values.split(chunk):
                if part.numel():
                    runtime._forward(base,cache,part[None])
            if cache.get_seq_length() != len(prefix_ids)+len(values):
                raise ValueError("unexpected number of live cache positions")
            scores = {field:score(base,tokenizer,cache,cue,choices) for field,(cue,choices) in fields[index].items()}
            torch.cuda.synchronize()
            rows.append(dict(case_id=case["case_id"],arm=arm,rounds=len(q),latent_positions=len(values),
                             seconds=time.perf_counter()-since,fields=scores))
            runner.write_json(args.output/"partial.json",dict(rows=rows,traces=traces,complete=False))
            print(case["case_id"],arm,{key:round(value["margin"],2) for key,value in scores.items()},flush=True)
            del cache
        del options, q, active, fused
    runner.write_json(args.output/"results.json",dict(rows=rows,traces=traces,complete=True,
        elapsed_seconds=time.perf_counter()-started, peak_cuda_bytes=torch.cuda.max_memory_allocated(),
        paid_api_calls=0,optimizer_steps=0,checkpoint_sha256=config["auxiliary_file_sha256"],
        probe_sha256=runner.file_sha(Path(__file__)),fixtures_sha256=runner.file_sha(args.fixtures)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model","checkpoint","replay","fixtures","output"):
        parser.add_argument("--"+name,type=Path,required=True)
    run(parser.parse_args())
