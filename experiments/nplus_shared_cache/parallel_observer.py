"""Observe five continuous-state token streams until their individual endings.

The original vectors, not the displayed token IDs, drive shared feedback.
Legacy three-position checkpoints remain untrained text-observation probes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.nplus_shared_cache import runner


def route_transition(heads, token_ids, alive, ending_ids):
    """An EOS-emitting route participates now and becomes exactly zero next round."""
    import torch
    masked_heads = torch.where(alive[None, :, None], heads, torch.zeros_like(heads))
    ended = torch.zeros_like(alive)
    for token in ending_ids:
        ended |= token_ids == token
    next_alive = alive & ~ended
    return masked_heads, next_alive


def run_parallel_observer(base, tokenizer, auxiliary, prepared, *,
                          max_rounds=32768, initialization_probe=False,
                          soft_embeddings=False, decode_final_answer=False):
    """Run a single shared cache with five independently ending visible streams."""
    import torch
    from safetensors.torch import save_file

    if max_rounds < 1:
        raise ValueError("max_rounds_must_be_positive")
    provenance = prepared["manifest"].get("auxiliary_provenance", {})
    text_trained = (provenance.get("status") == "verified_completed_shared_loop_training"
        and provenance.get("architecture") in {"teacher_forced_continuous_five_route/v2", "teacher_forced_continuous_five_route/v3"}
        and provenance.get("text_supervision") == "full_per_route_tokens_and_EOS")
    if not initialization_probe and not text_trained:
        raise ValueError("current_checkpoints_lack_per_route_token_supervision_select_initialization_probe_explicitly")
    if soft_embeddings and provenance.get("observation") == "current_token_from_post_state":
        raise ValueError("soft_embeddings_require_predictive_heads_not_post_state_observation_weights")
    folder = prepared["output"]
    started_path = folder / "parallel-observer-started.json"
    if started_path.exists():
        raise ValueError("observation_already_started_preserve_it_use_a_new_output")
    device = base.get_input_embeddings().weight.device
    if text_trained:
        ending_ids = {provenance["ending_token_id"]}
    else:
        eos = base.generation_config.eos_token_id
        ending_ids = set([eos] if isinstance(eos, int) else eos or [])
        think_end = tokenizer.encode("</think>", add_special_tokens=False)
        if len(think_end) != 1:
            raise ValueError("this_observer_requires_a_single_token_thought_end_marker")
        ending_ids.add(think_end[0])
    contract = {"schema": "shared-five-route-continuous-observer/v1",
        "source_identity": prepared["manifest"]["identity"], "max_rounds": max_rounds,
        "source_auxiliary_provenance": prepared["manifest"].get("auxiliary_provenance", {}),
        "source_code_sha256": {name: runner.file_sha(Path(__file__).parent / name)
            for name in (("parallel_observer.py", "runner.py", "latent_feedback.py")
                + (("soft_embedding.py",) if soft_embeddings else ()))},
        "ending_token_ids": sorted(ending_ids), "selection": "greedy",
        "feedback": ("soft_probability_weighted_input_embeddings" if soft_embeddings
            else "continuous_vectors_not_selected_token_embeddings"),
        "soft_thinking": ({"max_topk": 10, "temperature": .6, "top_k": 30,
            "top_p": .95, "min_p": .001, "cold_stop_disabled": True,
            "upstream_revision": "b97407c60d5e971da4a4fe76f405d6e236d0ddfa"} if soft_embeddings else None),
        "decode_final_answer_after_all_endings": decode_final_answer,
        "padding": "zero_entire_CAT_slot_after_that_routes_ending_round",
        "checkpoint_scope": ("full_route_token_supervised_teacher_latent_forcing" if text_trained
            else "initialization_probe_without_per_route_token_supervision"),
        "no_readable_CoT_quality_claim": True, "training_steps": 0, "paid_api_calls": 0}
    contract["identity"] = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    runner.write_json(started_path, contract)
    routes = [{"purpose": purpose, "token_ids": [], "ended": False,
        "ending_token_id": None, "ending_round": None} for purpose in runner.PURPOSES]
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats(device)
    if hasattr(base.model, "rope_deltas"):
        base.model.rope_deltas = None
    tensor_samples = {}
    concept_readout = None
    if soft_embeddings:
        from experiments.nplus_shared_cache.soft_embedding import SoftEmbeddingReadout
        concept_readout = SoftEmbeddingReadout(base.get_input_embeddings())

    def persist(stage, rounds, **extra):
        visible = []
        for route in routes:
            # Decode the complete actual ID sequence; do not concatenate byte pieces.
            text_ids = route["token_ids"][:-1] if route["ended"] else route["token_ids"]
            text = tokenizer.decode(text_ids, skip_special_tokens=False)
            (folder / (route["purpose"] + ".txt")).write_text(text, encoding="utf-8")
            visible.append({**route, "text": text})
        value = {"identity": contract["identity"], "stage": stage, "rounds": rounds,
            "routes": visible, "elapsed_seconds": time.perf_counter() - started,
            "all_routes_ended": all(route["ended"] for route in routes),
            "text_is_continuous_state_emission_not_inverse_CoT_reconstruction": True, **extra}
        runner.write_json(folder / "parallel-streams.json", value)
        return value

    rounds, cause = 0, None
    persist("prefilling", 0)
    with torch.inference_mode(), (folder / "parallel-rounds.jsonl").open("x", encoding="utf-8") as trace:
        ids = torch.tensor([prepared["prefix_ids"]], dtype=torch.long, device=device)
        realized = base.get_input_embeddings()(ids)
        reference_rms = realized.float().square().mean().sqrt()
        prefix_tokens = ids.shape[1]
        text_config = getattr(base.config, "text_config", base.config)
        context_capacity = text_config.max_position_embeddings
        prefill = base.model.language_model(inputs_embeds=realized,
            attention_mask=torch.ones(ids.shape, dtype=torch.long, device=device),
            position_ids=torch.arange(prefix_tokens, device=device).unsqueeze(0), use_cache=True)
        cache = prefill.past_key_values
        hidden = prefill.last_hidden_state[:, -1].clone()
        del prefill
        runner.write_json(folder / "parallel-prefill.json", runner.cache_snapshot(cache, prefix_tokens))
        alive = torch.ones(5, dtype=torch.bool, device=device)
        for index in range(max_rounds):
            if (folder / "STOP").exists():
                cause = "explicit_stop"
                break
            if realized.shape[1] >= context_capacity:
                cause = "context_capacity"
                break
            before_alive = alive.clone()
            heads, _, _ = auxiliary(hidden.float())
            observed_heads = (auxiliary.observation(heads[0])
                if provenance.get("observation") == "current_token_from_post_state" else heads[0])
            logits = base.lm_head(observed_heads.to(base.lm_head.weight.dtype)).float()
            soft_support = None
            if concept_readout is not None:
                vectors, selected, soft_support = concept_readout(logits)
                feedback_heads = vectors.unsqueeze(0).float()
            else:
                selected = logits.argmax(-1)
                feedback_heads = heads
            masked, next_alive = route_transition(feedback_heads, selected, before_alive, ending_ids)
            # Keep real vectors; do not embed or concatenate the visible words.
            feature_cat = masked.flatten(1)
            fused = auxiliary.fusion(feature_cat)
            # Concept vectors already live in the pretrained input space.
            # Hidden-vector feedback requires the separate RMS bridge.
            injected = (fused if soft_embeddings else fused * reference_rms
                / fused.square().mean(-1, keepdim=True).sqrt().clamp_min(1e-8))
            injected = (injected * auxiliary.gain).to(realized.dtype).unsqueeze(1)
            inactive = ~before_alive
            if bool(inactive.any()) and not bool(torch.all(masked[:, inactive] == 0)):
                raise RuntimeError("ended_route_CAT_slot_is_not_exactly_zero")
            selected_ids = selected.tolist()
            before_flags = before_alive.tolist()
            after_flags = next_alive.tolist()
            for route_index, route in enumerate(routes):
                if before_flags[route_index]:
                    route["token_ids"].append(selected_ids[route_index])
                    if not after_flags[route_index]:
                        route.update(ended=True, ending_token_id=selected_ids[route_index], ending_round=index + 1)
            position = realized.shape[1]
            realized = torch.cat((realized, injected), dim=1)
            continued = base.model.language_model(inputs_embeds=injected,
                attention_mask=torch.ones(realized.shape[:2], dtype=torch.long, device=device),
                position_ids=torch.tensor([[position]], device=device), past_key_values=cache, use_cache=True)
            if continued.past_key_values is not cache:
                raise RuntimeError("backbone_replaced_shared_cache_object")
            hidden = continued.last_hidden_state[:, -1].clone()
            del continued
            rounds = index + 1
            record = {"round": rounds, "alive_before": before_flags, "alive_after": after_flags,
                "emitted_ids": [token if was_alive else None for token, was_alive in zip(selected_ids, before_flags)],
                "CAT_slot_l2": masked.float().norm(dim=-1)[0].tolist(),
                "cache_sequence_length": cache.get_seq_length(),
                "ended_slots_exactly_zero": True}
            if soft_support is not None:
                record["concept_support"] = {key: value.tolist() for key, value in soft_support.items()}
            trace.write(json.dumps(record, ensure_ascii=False) + "\n")
            if index == 0 or before_flags != after_flags:
                for name, tensor in {"heads": heads, "masked_CAT": feature_cat, "injected": injected}.items():
                    tensor_samples[f"round_{rounds}_{name}"] = tensor.detach().cpu().contiguous().clone()
            alive = next_alive
            if rounds % 32 == 0 or before_flags != after_flags:
                trace.flush()
                persist("observing", rounds)
            if not bool(alive.any()):
                cause = "all_route_endings"
                break
        if cause is None:
            cause = "round_capacity"
        torch.cuda.synchronize(device)
        tensor_samples["last_masked_CAT"] = (feature_cat.detach().cpu().contiguous().clone() if rounds
            else torch.zeros(1, len(runner.PURPOSES) * hidden.shape[-1]))
        save_file(tensor_samples, str(folder / "parallel-tensor-samples.safetensors"))
        partial = cause != "all_route_endings"
        receipt = persist("completed" if not partial else "partial", rounds, stop_cause=cause,
            partial=partial, max_rounds=max_rounds, prefix_tokens=prefix_tokens,
            peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(device),
            peak_cuda_reserved_bytes=torch.cuda.max_memory_reserved(device),
            cache_at_return=runner.cache_snapshot(cache, prefix_tokens),
            tensor_sampling="first_last_and_route_ending_transitions_only",
            training_steps=0, paid_api_calls=0)
        if decode_final_answer:
            if partial:
                receipt["final_answer_status"] = "skipped_unfinished_routes"
            else:
                # Soft input already consumed the thought-end concept. Only
                # its whitespace separator remains; do not append </think> twice.
                thought_end_id = tokenizer.encode("</think>", add_special_tokens=False)
                closed_thought = (soft_embeddings and len(thought_end_id) == 1
                    and all(route["ending_token_id"] == thought_end_id[0] for route in routes))
                boundary_ids = (tokenizer.encode("\n\n", add_special_tokens=False)
                    if closed_thought else prepared["boundary_ids"])
                state = runner.SharedCaseState(cache=cache, realized_embeddings=realized,
                    attention_mask=torch.ones(realized.shape[:2], dtype=torch.long, device=device),
                    last_hidden=hidden, prefix_tokens=prefix_tokens, boundary_ids=boundary_ids,
                    output=folder, manifest=prepared["manifest"], verify_replay=False)
                receipt["final_answer"] = runner.decode_answer(base, tokenizer, state)
                receipt["final_answer_status"] = ("complete" if receipt["final_answer"]["ended_with_eos"]
                    else "partial")
                receipt["thought_end_separator_only"] = closed_thought
        runner.write_json(folder / "parallel-observer-receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("model", "auxiliary", "cases", "output"):
        parser.add_argument("--" + argument, required=True)
    parser.add_argument("--max-rounds", type=int, default=32768)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--initialization-probe", action="store_true",
        help="Explicitly observe a checkpoint not trained for continuous readable text")
    parser.add_argument("--soft-embeddings", action="store_true",
        help="Soft Thinking continuous input embeddings; not discrete token feedback")
    parser.add_argument("--decode-final-answer", action="store_true",
        help="Generate the formal answer from the same cache only after all routes end")
    args = parser.parse_args()
    # The old checkpoint has three trained proxy positions. This authenticates
    # its origin only; observer rounds use their own explicit new contract.
    tokenizer, prepared = runner.prepare_cases(model=args.model, auxiliary=args.auxiliary,
        output=args.output, cases=args.cases, latent_steps=3)
    if not args.execute:
        print(json.dumps({"prepared_cases": len(prepared), "gpu_used": False,
            "observation_training_status": "per_route_token_supervision_not_trained"}))
        return
    if not args.initialization_probe and any(case["manifest"]["auxiliary_provenance"].get("architecture")
            not in {"teacher_forced_continuous_five_route/v2", "teacher_forced_continuous_five_route/v3"} for case in prepared):
        parser.error("legacy checkpoints require explicit --initialization-probe")
    base, loaded = runner.load_frozen_model(args.model)
    runner.write_json(Path(args.output) / "model-load.json", loaded)
    auxiliary = runner.load_auxiliary(args.auxiliary, width=base.config.text_config.hidden_size,
        device=str(base.get_input_embeddings().weight.device))
    for case in prepared:
        receipt = run_parallel_observer(base, tokenizer, auxiliary, case,
            max_rounds=args.max_rounds, initialization_probe=args.initialization_probe,
            soft_embeddings=args.soft_embeddings, decode_final_answer=args.decode_final_answer)
        print(json.dumps({"case": case["case_id"], "rounds": receipt["rounds"],
            "stop_cause": receipt["stop_cause"]}), flush=True)


if __name__ == "__main__":
    main()
