"""Paired first-block CAT interventions with frozen Qwen and route weights."""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import torch
from safetensors.torch import load_file

from . import recurrent_runtime as runtime, runner
from .parallel_observer import route_transition
from .recurrent_contract import prepare_cases
from .recurrent_heads import RecurrentDirectionHeads
from .recurrent_observer import contextual_initialization


def score_fields(base, cache, case, tokenizer, fixture, side):
    scores = {}
    for field, prefix_key, choices in (
        ("day", "date_prefix", ["周" + fixture["days"][1-side], "周" + fixture["days"][side]]),
        ("contact", "contact_prefix", fixture["contacts"]),
    ):
        prefix = runner.BOUNDARY + fixture[prefix_key]
        prefix_ids = tokenizer.encode(prefix, add_special_tokens=False)
        if prefix_ids[:len(case["boundary_ids"])] != case["boundary_ids"]:
            raise ValueError("answer cue merged across thought boundary")
        candidate_ids = []
        for choice in choices:
            encoded = tokenizer.encode(prefix + choice, add_special_tokens=False)
            if encoded[:-1] != prefix_ids:
                raise ValueError(f"candidate must add exactly one token: {prefix!r} + {choice!r}")
            candidate_ids.append(encoded[-1])
        fork = copy.deepcopy(cache)
        device = base.lm_head.weight.device
        cue = base.get_input_embeddings()(torch.tensor([prefix_ids], device=device))
        hidden = runtime._forward(base, fork, cue)[0, -1]
        rows = base.lm_head.weight[candidate_ids].float()
        logits = rows @ hidden.float()
        scores[field+"_margin"] = float(logits[0] - logits[1])
        scores[field+"_choice_ids"] = candidate_ids
        scores[field+"_native_margin"] = float((base.lm_head(hidden).float()[candidate_ids] *
                                                hidden.new_tensor([1., -1.])).sum())
    return scores


def native_trace(base, auxiliary, prepared, initial, endings, block, limit):
    cache, shared, embeds = runtime._prefill(base, prepared["prefix_ids"], differentiable=False)
    reference = embeds.float().square().mean().sqrt()
    previous, memory = auxiliary.initialize(shared.float())
    previous = initial + (previous - shared.float()[:, None])
    alive = torch.ones(5, dtype=torch.bool, device=shared.device)
    masks, first, ending_rounds = [], [], [None] * 5
    while len(masks) < limit and bool(alive.any()):
        inputs = []
        for _ in range(min(block, limit - len(masks))):
            proposed, memory = auxiliary.forward_step(shared.float(), previous, memory)
            selected = base.lm_head(proposed[0].to(base.lm_head.weight.dtype)).argmax(-1)
            masked, next_alive = route_transition(proposed, selected, alive, endings)
            if len(first) < block:
                first.append(masked.clone())
            masks.append(alive)
            for index in (alive & ~next_alive).nonzero().flatten().tolist():
                ending_rounds[index] = len(masks)
            embedded, _ = runtime._inject(auxiliary, masked, alive, reference, embeds.dtype)
            inputs.append(embedded)
            alive = next_alive
            previous = torch.where(alive[None, :, None], proposed, 0.)
            memory = torch.where(alive[None, :, None], memory, 0.)
            if not bool(alive.any()):
                break
        shared = runtime._forward(base, cache, torch.stack(inputs, dim=1))[:, -1]
    return dict(first=first, masks=masks, ends=ending_rounds, complete=not bool(alive.any()))


def replay(base, auxiliary, prepared, initial, recipient, donor, arm, score, block):
    cache, shared, embeds = runtime._prefill(base, prepared["prefix_ids"], differentiable=False)
    reference = embeds.float().square().mean().sqrt()
    previous, memory = auxiliary.initialize(shared.float())
    previous = initial + (previous - shared.float()[:, None])
    masks = recipient["masks"]
    count = len(masks) if recipient["complete"] else block
    snapshots = {}
    for start in range(0, count, block):
        inputs = []
        for offset in range(start, min(start + block, count)):
            mask = masks[offset]
            proposed, memory = auxiliary.forward_step(shared.float(), previous, memory)
            slots = proposed
            if offset < block:
                if arm == "self":
                    slots = recipient["first"][offset]
                elif arm == "all":
                    slots = donor["first"][offset]
                elif arm == "zero":
                    slots = torch.zeros_like(proposed)
                elif arm != "base":
                    index = runner.PURPOSES.index(arm)
                    slots = proposed.clone()
                    slots[:, index] = donor["first"][offset][:, index]
            embedded, _ = runtime._inject(auxiliary, slots, mask, reference, embeds.dtype)
            inputs.append(embedded)
            if offset + 1 < count:
                active = masks[offset + 1][None, :, None]
                previous = torch.where(active, proposed, 0.)
                memory = torch.where(active, memory, 0.)
        shared = runtime._forward(base, cache, torch.stack(inputs, dim=1))[:, -1]
        if start == 0:
            snapshots["first_block"] = score(cache)
        if start + block >= count and recipient["complete"]:
            snapshots["natural"] = score(cache)
    return snapshots


@torch.inference_mode()
def run(args):
    started = time.perf_counter()
    fixtures = runtime._json(args.fixtures)["pairs"]
    args.output.mkdir(parents=True, exist_ok=False)
    cases = []
    for fixture in fixtures:
        for side, day in enumerate(fixture["days"]):
            messages = [dict(msg, content=msg["content"].replace("{day}", day))
                        for msg in fixture["messages"]]
            cases.append({"case_id": f"{fixture['id']}-{side}", "messages": messages})
    runner.write_json(args.output/"cases.json", cases)
    tokenizer, prepared = prepare_cases(args.model, args.checkpoint, args.output/"cases.json",
                                        args.output/"inputs")
    config = runtime._json(args.checkpoint/"auxiliary-config.json")
    if config["route_initialization"] != "native_question_and_direction_hidden_plus_learned_residual":
        raise ValueError("this experiment requires the native prefix initializer")
    base, _ = runner.load_frozen_model(args.model)
    auxiliary = RecurrentDirectionHeads(config["width"], config["memory_width"], config["rank"]).cuda()
    auxiliary.load_state_dict(load_file(str(args.checkpoint/"auxiliary.safetensors"), device="cuda"))
    auxiliary.eval().requires_grad_(False)
    block = config["block_size"]
    endings = {config["ending_token_id"]}
    think_end = tokenizer.encode("</think>", add_special_tokens=False)
    if len(think_end) == 1:
        endings.add(think_end[0])
    summaries = []
    with (args.output/"rows.jsonl").open("x", encoding="utf-8") as rows:
        for pair, fixture in enumerate(fixtures):
            variants = prepared[2*pair:2*pair+2]
            ids_a, ids_b = [case["prefix_ids"] for case in variants]
            if len(ids_a) != len(ids_b) or sum(a != b for a,b in zip(ids_a,ids_b)) != 1:
                raise ValueError("matched prompts must differ by exactly one token")
            initial = [contextual_initialization(base, tokenizer, case) for case in variants]
            traces = [native_trace(base, auxiliary, case, seed, endings, block, args.max_rounds)
                      for case, seed in zip(variants, initial)]
            if not all(len(trace["first"]) == block and
                       all(bool(mask.all()) for mask in trace["masks"][:block]) for trace in traces):
                raise ValueError("first block not fully active in both cases")
            summaries.append(dict(pair=fixture["id"], ends=[trace["ends"] for trace in traces]))
            for side, case in enumerate(variants):
                trace = traces[side]
                reference_scores = {}
                for arm in ("base", "self", "zero", "all", *runner.PURPOSES):
                    scores = replay(base, auxiliary, case, initial[side], trace, traces[1-side], arm,
                                    lambda cache: score_fields(base, cache, case, tokenizer, fixture, side), block)
                    if arm == "base":
                        reference_scores = scores
                    if arm == "self" and scores != reference_scores:
                        raise RuntimeError("self transplant did not reproduce recipient")
                    for horizon, values in scores.items():
                        record = dict(pair=fixture["id"], recipient=side, arm=arm, horizon=horizon,
                            rounds=block if horizon=="first_block" else len(trace["masks"]),
                            stop="diagnostic_first_block" if horizon=="first_block" else "recipient_matched_lifetime",
                            date_delta=values["day_margin"]-reference_scores[horizon]["day_margin"],
                            contact_delta=values["contact_margin"]-reference_scores[horizon]["contact_margin"],
                            **values)
                        rows.write(json.dumps(record, ensure_ascii=False)+"\n")
                    rows.flush()
                print(f"{fixture['id']}/{side}: {len(trace['masks'])} rounds, 9 arms", flush=True)
    runner.write_json(args.output/"results.json", dict(complete=True, pairs=fixtures, endings=summaries,
        block_size=block, round_limit=args.max_rounds, paid_api_calls=0, optimizer_steps=0,
        elapsed_seconds=time.perf_counter()-started, peak_cuda_bytes=torch.cuda.max_memory_allocated(),
        checkpoint_sha256=config["auxiliary_file_sha256"], fixture_sha256=runner.file_sha(args.fixtures),
        probe_sha256=runner.file_sha(Path(__file__))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "checkpoint", "fixtures", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--max-rounds", type=int, default=1024,
                        help="Diagnostic ceiling; missing native endings cannot yield full horizon scores")
    options = parser.parse_args()
    if options.max_rounds < 8:
        parser.error("need at least one complete block")
    run(options)
