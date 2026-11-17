"""Observe trained vectors and local fusion influence without training or grading."""
from pathlib import Path
import time


def inspect_vectors(base, auxiliary, tokenizer, state):
    """Hold prefix and earlier latents fixed; intervene on the final five heads.

    Every decoder comparison uses the same full-sequence, no-cache path. This
    isolates local influence, not semantic specialization or improved accuracy.
    """
    import torch
    from safetensors.torch import load_file, save_file
    try:
        from .runner import PURPOSES, write_json
    except ImportError:
        from runner import PURPOSES, write_json

    output = Path(state.output)
    if (output / "vector-inspection.json").exists():
        raise ValueError("completed_vector_inspection_will_not_be_silently_reexecuted")
    device = state.realized_embeddings.device
    started = time.perf_counter()
    geometry = []
    tensors = {}

    def delta(value, reference):
        difference = value.float() - reference.float()
        return {"max_abs": float(difference.abs().max()),
            "relative_l2": float(difference.norm() / reference.float().norm().clamp_min(1e-12)),
            "changed_fraction": float((value != reference).float().mean())}

    def cosines(vectors):
        unit = torch.nn.functional.normalize(vectors.float(), dim=-1)
        return (unit @ unit.transpose(-1, -2)).squeeze(0).tolist()

    with torch.inference_mode():
        for index in range(state.manifest["latent_steps"]):
            step = load_file(str(output / f"step-{index + 1:02}.safetensors"), device=str(device))
            heads, hidden = step["five_vectors"], step["h_before"].float()
            residuals = heads - hidden.unsqueeze(1)
            centered = heads - heads.mean(dim=1, keepdim=True)
            geometry.append({"step": index + 1, "head_shape": list(heads.shape),
                "cat_shape": list(step["feature_cat"].shape),
                "cat_exactly_splits_into_saved_heads": bool(torch.equal(
                    step["feature_cat"].reshape_as(heads), heads)),
                "residual_l2": residuals.norm(dim=-1)[0].tolist(),
                "centered_head_l2": centered.norm(dim=-1)[0].tolist(),
                "raw_cosines": cosines(heads), "residual_cosines": cosines(residuals),
                "centered_cosines": cosines(centered),
                "all_pairs_numerically_distinct": all(not torch.equal(heads[:, i], heads[:, j])
                    for i in range(len(PURPOSES)) for j in range(i))})
        # The final saved step comes from the actual cached trajectory. Earlier
        # injected positions stay unchanged in every intervention below.
        original_heads = step["five_vectors"]
        shared_hidden = step["h_before"].float()
        original_injected = step["injected"]
        reference_rms = state.realized_embeddings[:, :state.prefix_tokens].float().square().mean().sqrt()
        prefix_and_earlier = state.realized_embeddings[:, :-1]
        boundary = base.get_input_embeddings()(torch.tensor([state.boundary_ids], device=device))

        def align(heads):
            fused = auxiliary.fusion(heads.flatten(1))
            aligned = fused * reference_rms / fused.square().mean(-1, keepdim=True).sqrt().clamp_min(1e-8)
            aligned = aligned * auxiliary.gain
            return fused, aligned, aligned.to(original_injected.dtype).unsqueeze(1)

        def readout(injected):
            inputs = torch.cat((prefix_and_earlier, injected, boundary), dim=1)
            result = base.model.language_model(inputs_embeds=inputs,
                attention_mask=torch.ones(inputs.shape[:2], dtype=torch.long, device=device),
                position_ids=torch.arange(inputs.shape[1], device=device).unsqueeze(0), use_cache=False)
            hidden = result.last_hidden_state[:, -1].clone()
            logits = base.lm_head(hidden.to(base.lm_head.weight.dtype)).float()
            del result
            return hidden, logits

        fused, aligned, injected = align(original_heads)
        if not torch.equal(injected, original_injected):
            raise RuntimeError("inspection_did_not_reproduce_actual_injected_embedding")
        baseline_hidden, baseline_logits = readout(original_injected)
        baseline_logp = baseline_logits.log_softmax(-1)
        tensors.update(baseline_hidden=baseline_hidden, baseline_logits=baseline_logits,
            baseline_injected=original_injected, baseline_fused=fused, baseline_aligned_fp32=aligned)
        variants = [("identical_repeat", original_heads)]
        for index, purpose in enumerate(PURPOSES):
            changed = original_heads.clone()
            changed[:, index] = shared_hidden
            variants.append(("remove_residual_" + purpose, changed))
        variants.append(("cyclic_slots_1_2_3_4_0", original_heads[:, [1, 2, 3, 4, 0]]))
        interventions = []
        for name, heads in variants:
            variant_fused, variant_aligned, variant_injected = align(heads)
            hidden, logits = readout(variant_injected)
            logp = logits.log_softmax(-1)
            values, ids = logits.topk(5, dim=-1)
            interventions.append({"name": name, "fused_fp32": delta(variant_fused, fused),
                "aligned_fp32": delta(variant_aligned, aligned),
                "injected_bf16": delta(variant_injected, original_injected),
                "decoder_hidden": delta(hidden, baseline_hidden), "first_answer_logits": delta(logits, baseline_logits),
                "kl_baseline_to_variant": float((baseline_logp.exp() * (baseline_logp - logp)).sum()),
                "baseline_top1": int(baseline_logits.argmax(-1)[0]),
                "variant_top1": int(ids[0, 0]),
                "variant_top5": [{"id": int(token), "text": tokenizer.decode([int(token)]), "logit": float(value)}
                    for token, value in zip(ids[0], values[0])]})
            tensors.update({name + "_injected": variant_injected,
                name + "_hidden": hidden, name + "_logits": logits})
        save_file({name: value.detach().cpu().contiguous() for name, value in tensors.items()},
            str(output / "vector-inspection.safetensors"))
        torch.cuda.synchronize(device)
        receipt = {"schema": "shared-loop-local-vector-inspection/v1", "identity": state.manifest["identity"],
            "geometry": geometry, "interventions": interventions,
            "fused_alignment_reproduced_actual_injection": True,
            "fixed_prefix_tokens": state.prefix_tokens,
            "fixed_earlier_latents": state.manifest["latent_steps"] - 1,
            "decoder_comparison_path": "same_full_sequence_no_cache_for_all_variants",
            "intervention_scope": "last_step_residual_only_or_fixed_slot_rotation",
            "direction_names_are_supervision_targets_not_proven_semantic_roles": True,
            "no_accuracy_or_direction_usefulness_claim": True,
            "threshold_applied": False, "training_steps": 0, "paid_api_calls": 0,
            "extra_generated_answers": 0, "elapsed_seconds": time.perf_counter() - started}
        write_json(output / "vector-inspection.json", receipt)
    return receipt
