"""Observe pure-hidden recurrent routes and decode their shared-cache answer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.nplus_shared_cache import runner
from experiments.nplus_shared_cache import recurrent_runtime as runtime


def contextual_initialization(base, tokenizer, prepared):
    """Read-only initialization intervention from known messages, not targets.

    Uncached forwards produce route-conditioned initial hidden vectors. Their
    caches are not retained. Observed text never enters this path.
    """
    import copy
    import torch
    from torch.nn.attention import SDPBackend, sdpa_kernel
    from experiments.nplus_shared_cache.training import DIRECTIONS
    source = runtime._json(prepared["output"] / "source-case.json")
    original = runtime._json(prepared["output"] / "model-messages.json")
    device = base.get_input_embeddings().weight.device
    vectors, archive = [], []
    for purpose, direction in zip(runner.PURPOSES, DIRECTIONS):
        messages = copy.deepcopy(original)
        note = "Your analysis direction: " + direction
        if messages and messages[0]["role"] == "system" and isinstance(messages[0].get("content"), str):
            messages[0]["content"] += "\n" + note
        else:
            messages.insert(0, {"role": "system", "content": note})
        options = {"tools": source["tools"]} if "tools" in source else {}
        text = tokenizer.apply_chat_template(messages, tokenize=False,
            add_generation_prompt=True, enable_thinking=True, **options)
        ids = tokenizer.encode(text, add_special_tokens=False)
        inputs = base.get_input_embeddings()(torch.tensor([ids], device=device))
        with sdpa_kernel(SDPBackend.MATH):
            hidden = base.model.language_model(inputs_embeds=inputs,
                attention_mask=torch.ones((1, len(ids)), dtype=torch.long, device=device),
                position_ids=torch.arange(len(ids), device=device)[None], use_cache=False).last_hidden_state
        vectors.append(hidden[0, -1].float().clone())
        archive.append({"purpose": purpose, "messages": messages, "prefix_ids": ids,
            "prefix_text": text, "contains_teacher_answer_or_path": False})
    runner.write_json(prepared["output"] / "contextual-initialization.json", archive)
    return torch.stack(vectors)[None]


def observe(base, tokenizer, auxiliary, prepared, *, block_size, ending_id,
            max_rounds=32768, decode_answer=True, contextual_init=False,
            trained_contextual_init=False, diagnostic_answer_on_partial=False):
    import torch
    from safetensors.torch import save_file
    from experiments.nplus_shared_cache.parallel_observer import route_transition

    folder = prepared["output"]
    if (folder / "observer-started.json").exists():
        raise ValueError("observation_already_started_use_new_output")
    initialization = ("native_question_and_direction_hidden_plus_learned_residual" if trained_contextual_init
        else "known_context_direction_intervention" if contextual_init else "learned_initialization")
    runner.write_json(folder / "observer-started.json", {"identity": prepared["manifest"]["identity"],
        "block_size": block_size, "max_rounds": max_rounds,
        "initialization": initialization,
        "contextual_initialization_was_training_protocol": trained_contextual_init,
        "feedback": "pure_continuous_hidden_vectors", "text_feedback": False,
        "diagnostic_answer_on_partial": diagnostic_answer_on_partial,
        "source_code_sha256": {name: runner.file_sha(Path(__file__).with_name(name)) for name in
            ("recurrent_observer.py", "recurrent_runtime.py", "recurrent_heads.py")}})
    device = base.get_input_embeddings().weight.device
    torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()
    routes = [{"purpose": p, "token_ids": [], "ended": False,
        "ending_round": None, "ending_token_id": None} for p in runner.PURPOSES]
    endings = {ending_id}
    think_end = tokenizer.encode("</think>", add_special_tokens=False)
    if len(think_end) == 1:
        endings.add(think_end[0])
    sample_tensors = {}

    def persist(stage, rounds, **extra):
        visible = []
        for route in routes:
            ids = route["token_ids"][:-1] if route["ended"] else route["token_ids"]
            text = tokenizer.decode(ids, skip_special_tokens=False)
            (folder / (route["purpose"] + ".txt")).write_text(text, encoding="utf-8")
            visible.append({**route, "text": text})
        value = {"identity": prepared["manifest"]["identity"], "stage": stage,
            "rounds": rounds, "routes": visible, "all_routes_ended": all(r["ended"] for r in routes),
            "elapsed_seconds": time.perf_counter() - started, "block_size": block_size,
            "initialization": initialization,
            "text_feedback": False, "paid_api_calls": 0, **extra}
        runner.write_json(folder / "parallel-streams.json", value)
        return value

    with torch.inference_mode(), (folder / "parallel-rounds.jsonl").open("x", encoding="utf-8") as trace:
        cache, shared, realized = runtime._prefill(base, prepared["prefix_ids"], differentiable=False)
        reference = realized.float().square().mean().sqrt()
        prefix_length = len(prepared["prefix_ids"])
        runner.write_json(folder / "parallel-prefill.json", runner.cache_snapshot(cache, prefix_length))
        previous, memory = auxiliary.initialize(shared.float())
        if trained_contextual_init:
            residual = previous - shared.float()[:, None]
            previous = contextual_initialization(base, tokenizer, prepared) + residual
        elif contextual_init:
            previous = contextual_initialization(base, tokenizer, prepared)
        alive = torch.ones(5, dtype=torch.bool, device=device)
        capacity = getattr(base.config, "text_config", base.config).max_position_embeddings
        rounds, cause = 0, None
        persist("observing", 0)
        while rounds < max_rounds and bool(alive.any()):
            injected = []
            for _ in range(min(block_size, max_rounds - rounds)):
                if (folder / "STOP").exists():
                    cause = "explicit_stop"
                    break
                if cache.get_seq_length() + len(injected) >= capacity:
                    cause = "context_capacity"
                    break
                proposed, new_memory = auxiliary.forward_step(shared.float(), previous, memory)
                # Readout is observation/EOS only. Neither logits nor token IDs
                # appear in the continuous-vector or recurrent-state update.
                selected = base.lm_head(proposed[0].to(base.lm_head.weight.dtype)).argmax(-1)
                masked, next_alive = route_transition(proposed, selected, alive, endings)
                z, cat = runtime._inject(auxiliary, masked, alive, reference, realized.dtype)
                before, after, ids = alive.tolist(), next_alive.tolist(), selected.tolist()
                rounds += 1
                for i, route in enumerate(routes):
                    if before[i]:
                        route["token_ids"].append(ids[i])
                        if not after[i]:
                            route.update(ended=True, ending_round=rounds, ending_token_id=ids[i])
                normalized = torch.nn.functional.normalize(proposed[0], dim=-1)
                trace.write(json.dumps({"round": rounds, "alive_before": before, "alive_after": after,
                    "emitted_ids": [v if a else None for v, a in zip(ids, before)],
                    "CAT_slot_l2": masked[0].norm(dim=-1).tolist(),
                    "route_cosine": (normalized @ normalized.T).tolist(),
                    "shared_cache_before_block_commit": cache.get_seq_length(),
                    "block_offset": len(injected)}) + "\n")
                if rounds == 1 or before != after:
                    sample_tensors[f"round_{rounds}_q"] = proposed.cpu().contiguous().clone()
                    sample_tensors[f"round_{rounds}_CAT"] = cat.cpu().contiguous().clone()
                    sample_tensors[f"round_{rounds}_memory"] = new_memory.cpu().contiguous().clone()
                injected.append(z)
                previous = torch.where(next_alive[None, :, None], proposed, torch.zeros_like(proposed))
                memory = torch.where(next_alive[None, :, None], new_memory, torch.zeros_like(new_memory))
                alive = next_alive
                if not bool(alive.any()):
                    cause = "all_route_endings"
                    break
            if injected:
                inputs = torch.stack(injected, dim=1)
                shared = runtime._forward(base, cache, inputs)[:, -1].clone()
                realized = torch.cat((realized, inputs), dim=1)
            trace.flush()
            persist("observing", rounds)
            if cause:
                break
        cause = cause or "round_capacity"
        torch.cuda.synchronize(device)
        partial = cause != "all_route_endings"
        receipt = persist("partial" if partial else "completed", rounds, partial=partial,
            stop_cause=cause, prefix_tokens=prefix_length,
            cache_at_return=runner.cache_snapshot(cache, prefix_length),
            peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(device),
            tensor_sampling="first_and_each_route_ending_transition",
            direction_semantics_not_established_by_vector_distance=True)
        save_file(sample_tensors, str(folder / "parallel-tensor-samples.safetensors"))
        if decode_answer and (not partial or diagnostic_answer_on_partial):
            from torch.nn.attention import SDPBackend, sdpa_kernel
            # Observing an ending through a hidden-state probe has not consumed
            # its token embedding. Close thought mode explicitly for the answer.
            state = runner.SharedCaseState(cache=cache, realized_embeddings=realized,
                attention_mask=torch.ones(realized.shape[:2], dtype=torch.long, device=device),
                last_hidden=shared, prefix_tokens=prefix_length,
                boundary_ids=prepared["boundary_ids"], output=folder,
                manifest=prepared["manifest"], verify_replay=False)
            with sdpa_kernel(SDPBackend.MATH):
                receipt["final_answer"] = runner.decode_answer(base, tokenizer, state)
            receipt["answer_role"] = ("diagnostic_after_unfinished_routes_not_completed_workflow"
                if partial else "after_all_natural_route_endings")
        elif decode_answer:
            receipt["final_answer_status"] = "skipped_unfinished_routes"
        runner.write_json(folder / "observer-receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "checkpoint", "cases", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-rounds", type=int, default=32768)
    parser.add_argument("--without-answer", action="store_true")
    parser.add_argument("--diagnostic-answer-on-partial", action="store_true",
        help="Explicit diagnostic answer from unfinished vectors; never marks routes complete")
    parser.add_argument("--contextual-initialization", action="store_true",
        help="Explicit inference-only initial-vector intervention; not the trained initialization")
    args = parser.parse_args()
    from safetensors.torch import load_file
    from experiments.nplus_shared_cache.recurrent_contract import prepare_cases
    from experiments.nplus_shared_cache.recurrent_heads import RecurrentDirectionHeads
    tokenizer, cases = prepare_cases(Path(args.model), Path(args.checkpoint), Path(args.cases), Path(args.output))
    if not args.execute:
        print(json.dumps({"prepared_cases": len(cases), "weights_loaded": False}))
        return
    config = runtime._json(Path(args.checkpoint) / "auxiliary-config.json")
    if args.max_rounds < 1:
        raise ValueError("positive_round_capacity_required")
    base, loaded = runner.load_frozen_model(args.model)
    runner.write_json(Path(args.output) / "model-load.json", loaded)
    auxiliary = RecurrentDirectionHeads(config["width"], config["memory_width"], config["rank"]).to("cuda")
    auxiliary.load_state_dict(load_file(str(Path(args.checkpoint) / "auxiliary.safetensors"), device="cuda"), strict=True)
    auxiliary.eval().requires_grad_(False)
    for case in cases:
        r = observe(base, tokenizer, auxiliary, case, block_size=config["block_size"],
            ending_id=config["ending_token_id"], max_rounds=args.max_rounds,
            decode_answer=not args.without_answer, contextual_init=args.contextual_initialization,
            diagnostic_answer_on_partial=args.diagnostic_answer_on_partial,
            trained_contextual_init=config.get("route_initialization") ==
                "native_question_and_direction_hidden_plus_learned_residual")
        print(json.dumps({"case": case["case_id"], "rounds": r["rounds"], "stop": r["stop_cause"]}), flush=True)


if __name__ == "__main__":
    main()
