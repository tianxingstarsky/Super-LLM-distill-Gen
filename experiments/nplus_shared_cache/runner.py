"""Inspect shared-state latent feedback around an unchanged Qwen3.5 backbone.

No training or remote API calls. Default CLI action only prepares the inputs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import time
from typing import Any

PURPOSES = ("forward", "backward", "alternatives", "constraints", "counterexamples")
BOUNDARY = "\n</think>\n\n"


def replace_with_retry(source: Path, target: Path) -> None:
    """Keep atomic writes during a brief Windows reader/virus-scanner lock."""
    deadline = time.monotonic() + 2.0
    while True:
        try:
            source.replace(target)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or time.monotonic() >= deadline:
                raise
            time.sleep(.02)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_with_retry(temporary, path)


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_messages(messages: list[dict]) -> tuple[list[dict], list[str]]:
    """Keep role/source boundaries; adapt only tool argument serialization."""
    normalized = json.loads(json.dumps(messages, ensure_ascii=False))
    changes = []
    if not normalized:
        raise ValueError("empty_conversation")
    for index, message in enumerate(normalized):
        if message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"unsupported_message_role:{index}")
        content = message.get("content")
        if isinstance(content, list):
            if any(part.get("type") != "text" for part in content):
                raise ValueError(f"media_input_not_supported:{index}")
        elif content is not None and not isinstance(content, str):
            raise ValueError(f"unsupported_content:{index}")
        for call in message.get("tool_calls", []):
            arguments = call.get("function", {}).get("arguments")
            if isinstance(arguments, str):
                call["function"]["arguments"] = json.loads(arguments)
                changes.append(f"message_{index}:tool_arguments_json_decoded")
    return normalized, changes


def auxiliary_provenance(path: Path, description: dict, *, model: Path,
                         latent_steps: int, auxiliary_sha: str) -> dict:
    """Distinguish a declared architecture from authenticated completed training."""
    metadata = description.get("metadata") or {}
    provenance = {"architecture": metadata.get("architecture", "unspecified"),
        "identity": metadata.get("identity"), "status": "training_origin_unverified",
        "inference_performs_training": False, "complete_base_weight_digest_bound": False}
    architecture = metadata.get("architecture")
    if architecture not in {"differentiable_shared_state_latent_feedback/v1",
                            "teacher_forced_continuous_five_route/v2",
                            "teacher_forced_continuous_five_route/v3"}:
        return provenance
    config_path = path.with_name("auxiliary-config.json")
    config = json.loads(config_path.read_text("utf-8"))
    model_config = json.loads((model / "config.json").read_text("utf-8"))
    width = model_config.get("text_config", model_config)["hidden_size"]
    if (config.get("identity") != metadata.get("identity")
            or config.get("architecture") != metadata["architecture"]
            or config.get("width") != width
            or (architecture == "differentiable_shared_state_latent_feedback/v1"
                and config.get("latent_steps") != latent_steps)
            or config.get("rank") != description["shapes"]["adapters.0.1.weight"][0]):
        raise ValueError("shared_checkpoint_config_does_not_match_requested_model_or_loop")
    provenance.update(status="shared_loop_checkpoint_loaded", config_sha256=file_sha(config_path))
    if architecture in {"teacher_forced_continuous_five_route/v2", "teacher_forced_continuous_five_route/v3"}:
        if (config.get("feedback") != "continuous_head_vectors"
                or config.get("text_supervision") != "full_per_route_tokens_and_EOS"):
            raise ValueError("continuous_text_checkpoint_contract_mismatch")
        if architecture.endswith("/v3"):
            if (config.get("observation") != "current_token_from_post_state"
                    or config.get("state_position") != "post_target"
                    or description["shapes"].get("observation.weight") != [width, width]):
                raise ValueError("post_state_checkpoint_requires_its_trained_observation_map")
        if (config.get("model_config_sha256") != file_sha(model / "config.json")
                or config.get("template_sha256") != file_sha(model / "chat_template.jinja")):
            raise ValueError("continuous_text_checkpoint_base_configuration_changed")
        weights = config.get("model_weights_sha256")
        if not weights:
            raise ValueError("continuous_text_checkpoint_base_weight_binding_missing")
        for name, expected_sha in weights.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or file_sha(model / relative) != expected_sha:
                raise ValueError("continuous_text_checkpoint_base_weight_binding_changed")
        provenance.update(complete_base_weight_digest_bound=True,
            text_supervision=config["text_supervision"], feedback=config["feedback"],
            ending_token_id=config["ending_token_id"],
            observation=config.get("observation", "native_next_token_head"),
            state_position=config.get("state_position", "pre_target"))
    receipt_path = path.with_name("training-receipt.json")
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text("utf-8"))
        if (receipt.get("identity") != metadata["identity"] or receipt.get("status") != "completed"
                or receipt.get("auxiliary_file_sha256") != auxiliary_sha):
            raise ValueError("shared_checkpoint_training_receipt_mismatch")
        provenance.update(status="verified_completed_shared_loop_training",
            receipt_sha256=file_sha(receipt_path), actual_epoch=receipt["actual_epoch"],
            optimizer_steps=receipt["global_steps"], train_records=receipt["train_records"])
    return provenance


def prepare_cases(*, model: str | Path, auxiliary: str | Path,
                  output: str | Path, messages: str | Path | None = None,
                  cases: str | Path | None = None, latent_steps: int = 3) -> tuple[Any, list[dict]]:
    """Load tokenizer only; archive exact source bytes and rendered prefix."""
    from safetensors import safe_open
    from transformers import AutoTokenizer

    model, auxiliary, output = Path(model), Path(auxiliary), Path(output)
    if bool(messages) == bool(cases):
        raise ValueError("choose_exactly_one_of_messages_or_cases")
    if latent_steps < 1:
        raise ValueError("latent_steps_must_be_positive")
    source_path = Path(cases or messages)
    source_bytes = source_path.read_bytes()
    archived_source = output / "source-input.json"
    if archived_source.exists() and archived_source.read_bytes() != source_bytes:
        raise ValueError("output_source_bytes_changed_use_new_output")
    source_value = json.loads(source_bytes)
    source_cases = source_value["cases"] if cases else [{
        "case_id": "case-01", "messages": source_value if isinstance(source_value, list) else source_value["messages"]}]
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
    auxiliary_sha = file_sha(auxiliary)
    with safe_open(auxiliary, framework="pt", device="cpu") as handle:
        auxiliary_description = {"metadata": handle.metadata(), "keys": list(handle.keys()),
            "shapes": {key: list(handle.get_slice(key).get_shape()) for key in handle.keys()}}
    provenance = auxiliary_provenance(auxiliary, auxiliary_description, model=model,
        latent_steps=latent_steps, auxiliary_sha=auxiliary_sha)
    output.mkdir(parents=True, exist_ok=True)
    if not archived_source.exists():
        archived_source.write_bytes(source_bytes)
    prepared = []
    seen = set()
    for source_case in source_cases:
        case_id = source_case["case_id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", case_id) or case_id in seen:
            raise ValueError("case_id_must_be_unique_safe_filename")
        seen.add(case_id)
        case_messages, adaptations = normalize_messages(source_case["messages"])
        prefix = tokenizer.apply_chat_template(case_messages, tokenize=False,
            add_generation_prompt=True, enable_thinking=True)
        prefix_ids = tokenizer.encode(prefix, add_special_tokens=False)
        boundary_ids = tokenizer.encode(BOUNDARY, add_special_tokens=False)
        identity_payload = {"prefix_sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
            "auxiliary_sha256": auxiliary_sha, "config_sha256": file_sha(model / "config.json"),
            "latent_steps": (None if provenance["architecture"].endswith("/v3") else latent_steps), "boundary_ids": boundary_ids,
            "mechanism": "shared_h_five_residual_heads_feature_cat_single_cache/v1"}
        identity = hashlib.sha256(json.dumps(identity_payload, sort_keys=True).encode()).hexdigest()
        case_output = output / case_id
        previous = case_output / "input-manifest.json"
        if previous.exists() and json.loads(previous.read_text("utf-8"))["identity"] != identity:
            raise ValueError("output_input_identity_changed")
        case_output.mkdir(exist_ok=True)
        write_json(case_output / "source-case.json", source_case)
        write_json(case_output / "model-messages.json", case_messages)
        (case_output / "prefix.txt").write_bytes(prefix.encode("utf-8"))
        (case_output / "closing-boundary.txt").write_bytes(BOUNDARY.encode("utf-8"))
        write_json(case_output / "prefix-ids.json", prefix_ids)
        write_json(case_output / "closing-boundary-ids.json", boundary_ids)
        manifest = {"schema": "nplus-shared-cache-input/v1", "case_id": case_id,
            "identity": identity, **identity_payload, "prefix_tokens": len(prefix_ids),
            "source_input_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "message_adaptations": adaptations, "source_extra_fields_are_not_prompt": True,
            "auxiliary": auxiliary_description, "auxiliary_provenance": provenance,
            "inference_performs_training": False}
        write_json(previous, manifest)
        prepared.append({"case_id": case_id, "output": case_output, "prefix_ids": prefix_ids,
            "boundary_ids": boundary_ids, "manifest": manifest})
    write_json(output / "preparation.json", {"schema": "nplus-shared-cache-preparation/v1",
        "cases": [{"case_id": item["case_id"], "identity": item["manifest"]["identity"]} for item in prepared],
        "loaded_model_weights": False, "gpu_used": False, "training_performed": False,
        "dependencies": {name: importlib.metadata.version(name) for name in ("torch", "transformers", "safetensors")}})
    return tokenizer, prepared


def load_frozen_model(model: str | Path, *, device: str = "cuda:0") -> tuple[Any, dict]:
    """Use official HF loader; restore checkpoint FP32 GDN parameters exactly."""
    import torch
    from safetensors import safe_open
    from transformers import AutoModelForImageTextToText

    model = Path(model)
    started = time.perf_counter()
    base = AutoModelForImageTextToText.from_pretrained(model, local_files_only=True,
        trust_remote_code=False, device_map={"": device}, dtype=torch.bfloat16, attn_implementation="sdpa")
    index = model / "model.safetensors.index.json"
    if index.exists():
        mapping = json.loads(index.read_text("utf-8"))["weight_map"]
    else:
        with safe_open(model / "model.safetensors", framework="pt", device="cpu") as handle:
            mapping = {name: "model.safetensors" for name in handle.keys()}
    groups = defaultdict(list)
    for name, shard in mapping.items():
        if name.endswith((".linear_attn.A_log", ".linear_attn.norm.weight")):
            groups[shard].append(name)
    with torch.no_grad():
        for shard, names in groups.items():
            with safe_open(model / shard, framework="pt", device="cpu") as handle:
                for name in names:
                    original = handle.get_tensor(name)
                    if original.dtype != torch.float32:
                        raise ValueError(f"expected_original_fp32_gdn:{name}")
                    parameter = base.get_parameter(name)
                    parameter.data = original.to(parameter.device, dtype=torch.float32)
    base.requires_grad_(False).eval()
    base.config.use_cache = True
    return base, {"load_seconds": time.perf_counter() - started, "class": type(base).__name__,
        "fp32_gdn_parameters_restored": sum(map(len, groups.values())), "frozen": True,
        "training_performed": False, "original_weights_modified": False}


def load_auxiliary(auxiliary: str | Path, *, width: int, device: str) -> Any:
    """Compatibility entry; training and inference use the same head module."""
    try:
        from .latent_feedback import load_auxiliary as load_shared
    except ImportError:  # Direct script execution.
        from latent_feedback import load_auxiliary as load_shared
    return load_shared(auxiliary, width=width, device=device)


def tensor_sha(tensor: Any) -> str:
    return hashlib.sha256(tensor.detach().contiguous().cpu().view(-1).view(__import__("torch").uint8).numpy().tobytes()).hexdigest()


def tensor_summary(tensor: Any) -> dict | None:
    if tensor is None:
        return None
    return {"shape": list(tensor.shape), "dtype": str(tensor.dtype),
        "norm": float(tensor.detach().float().norm()), "device": str(tensor.device)}


def cache_snapshot(cache: Any, prefix_tokens: int) -> dict:
    """Summarize hybrid state; hash unchanged prefix slices without cloning GDN."""
    layers = []
    for index, layer in enumerate(cache.layers):
        item = {"layer": index, "type": type(layer).__name__}
        for name in ("keys", "values"):
            tensor = getattr(layer, name, None)
            if tensor is not None:
                item[name] = tensor_summary(tensor)
                item[name]["prefix_sha256"] = tensor_sha(tensor[..., :prefix_tokens, :])
                item["full_attention_length"] = int(tensor.shape[-2])
        for name in ("conv_states", "recurrent_states"):
            values = getattr(layer, name, None)
            if values is not None:
                item[name] = {str(key): tensor_summary(value) for key, value in values.items()}
        layers.append(item)
    return {"object_id": id(cache), "type": type(cache).__name__,
        "sequence_length": int(cache.get_seq_length()), "layers": layers}


def lm_readout(base: Any, tokenizer: Any, hidden: Any, top_k: int = 8) -> dict:
    import torch
    logits = base.lm_head(hidden.to(base.lm_head.weight.dtype)).float()
    scores, indices = logits.topk(top_k, dim=-1)
    return {"purpose": "diagnostic_not_a_textual_thought_transcript", "top": [
        {"id": int(token), "text": tokenizer.decode([int(token)]), "logit": float(score)}
        for token, score in zip(indices[0], scores[0])]}


def compare_hidden(base: Any, cached: Any, replay: Any) -> dict:
    import torch
    left, right = cached.float(), replay.float()
    delta = left - right
    cached_logits = base.lm_head(cached.to(base.lm_head.weight.dtype)).float()
    replay_logits = base.lm_head(replay.to(base.lm_head.weight.dtype)).float()
    logits_delta = cached_logits - replay_logits
    return {"max_abs": float(delta.abs().max()),
        "relative_l2": float(delta.norm() / right.norm().clamp_min(1e-12)),
        "cosine": float(torch.nn.functional.cosine_similarity(left, right).mean()),
        "cached_top1": int(cached_logits.argmax(-1)[0]), "replay_top1": int(replay_logits.argmax(-1)[0]),
        "logits_max_abs": float(logits_delta.abs().max()),
        "logits_relative_l2": float(logits_delta.norm() / replay_logits.norm().clamp_min(1e-12)),
        "threshold_applied": False}


@dataclass
class SharedCaseState:
    cache: Any
    realized_embeddings: Any
    attention_mask: Any
    last_hidden: Any
    prefix_tokens: int
    boundary_ids: list[int]
    output: Path
    manifest: dict
    verify_replay: bool


def run_shared_case(base: Any, tokenizer: Any, auxiliary: Any, prepared: dict,
                    *, verify_replay: bool = False) -> SharedCaseState:
    """One prefill, one cache, simultaneous heads, actual fused feedback."""
    import torch
    from safetensors.torch import save_file
    if prepared["manifest"].get("auxiliary_provenance", {}).get("architecture") in {
            "teacher_forced_continuous_five_route/v2", "teacher_forced_continuous_five_route/v3"}:
        raise ValueError("use_parallel_observer_for_variable_length_text_supervised_checkpoint")
    try:
        from .latent_feedback import project_shared_hidden
    except ImportError:
        from latent_feedback import project_shared_hidden

    folder = prepared["output"]
    if (folder / "loop-complete.json").exists():
        raise ValueError("completed_case_will_not_be_silently_reexecuted")
    device = base.get_input_embeddings().weight.device
    prefix_ids = torch.tensor([prepared["prefix_ids"]], dtype=torch.long, device=device)
    prefix_tokens = prefix_ids.shape[1]
    if hasattr(base.model, "rope_deltas"):
        base.model.rope_deltas = None
    torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()
    steps = []
    backbone = base.model.language_model
    with torch.inference_mode():
        realized = base.get_input_embeddings()(prefix_ids)
        reference_rms = realized.float().square().mean().sqrt()
        mask = torch.ones(realized.shape[:2], device=device, dtype=torch.long)
        positions = torch.arange(prefix_tokens, device=device).unsqueeze(0)
        prefill = backbone(inputs_embeds=realized, attention_mask=mask,
            position_ids=positions, use_cache=True)
        cache = prefill.past_key_values
        hidden = prefill.last_hidden_state[:, -1].clone()
        del prefill
        save_file({"prefix_embeddings": realized.cpu().contiguous(), "h_after_prefill": hidden.cpu().contiguous()},
            str(folder / "prefill.safetensors"))
        prefill_record = {"positions": positions[0].tolist(), "attention_mask": mask[0].tolist(),
            "cache": cache_snapshot(cache, prefix_tokens), "readout": lm_readout(base, tokenizer, hidden)}
        write_json(folder / "prefill.json", prefill_record)
        for step in range(prepared["manifest"]["latent_steps"]):
            before = hidden
            heads, feature_cat, fused, injected = project_shared_hidden(auxiliary, before, reference_rms, realized.dtype)
            position = int(realized.shape[1])
            realized = torch.cat((realized, injected), dim=1)
            mask = torch.ones(realized.shape[:2], device=device, dtype=torch.long)
            continued = backbone(inputs_embeds=injected, attention_mask=mask,
                position_ids=torch.tensor([[position]], device=device),
                past_key_values=cache, use_cache=True)
            if continued.past_key_values is not cache:
                raise RuntimeError("backbone_replaced_shared_cache_object")
            hidden = continued.last_hidden_state[:, -1].clone()
            del continued
            tensor_path = folder / f"step-{step + 1:02}.safetensors"
            tensors = {"h_before": before, "five_vectors": heads, "feature_cat": feature_cat,
                "fused": fused, "injected": injected, "h_after": hidden}
            save_file({name: tensor.detach().cpu().contiguous() for name, tensor in tensors.items()}, str(tensor_path))
            record = {"step": step + 1, "input_position": position, "attention_mask": mask[0].tolist(),
                "vector_shapes": {name: list(value.shape) for name, value in tensors.items()},
                "cache": cache_snapshot(cache, prefix_tokens), "shared_input_h_sha256": tensor_sha(before),
                "readout_before": lm_readout(base, tokenizer, before),
                "head_readouts": {name: lm_readout(base, tokenizer, heads[:, i]) for i, name in enumerate(PURPOSES)},
                "readout_after": lm_readout(base, tokenizer, hidden)}
            if verify_replay:
                replay = backbone(inputs_embeds=realized, attention_mask=mask,
                    position_ids=torch.arange(realized.shape[1], device=device).unsqueeze(0), use_cache=False)
                record["full_prefix_replay"] = compare_hidden(base, hidden, replay.last_hidden_state[:, -1])
                del replay
            write_json(folder / f"step-{step + 1:02}.json", record)
            steps.append(record)
        torch.cuda.synchronize(device)
        elapsed = time.perf_counter() - started
        write_json(folder / "loop-complete.json", {"schema": "nplus-shared-cache-loop/v1",
            "identity": prepared["manifest"]["identity"], "steps": len(steps),
            "elapsed_seconds": elapsed, "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(device),
            "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(device),
            "same_cache_object_throughout": all(item["cache"]["object_id"] == id(cache) for item in steps),
            "inference_performs_training": False,
            "auxiliary_provenance": prepared["manifest"]["auxiliary_provenance"],
            "full_prefix_replay_enabled": verify_replay})
    return SharedCaseState(cache, realized, mask, hidden, prefix_tokens,
        prepared["boundary_ids"], folder, prepared["manifest"], verify_replay)


def decode_answer(base: Any, tokenizer: Any, state: SharedCaseState, *,
                  logits_processor: Any = None, stopping_criteria: Any = None,
                  seed: int = 42, max_new_tokens: int = 32768) -> dict:
    """Native HF decode with full realized embeddings and unconsumed boundary.

    External processors receive generated IDs only for this embeds-only call.
    Nothing is re-prefilled manually; HF slices the unprocessed boundary.
    """
    import torch
    from safetensors.torch import save_file
    from transformers import StoppingCriteria, StoppingCriteriaList, TextStreamer, set_seed

    folder, device = state.output, state.realized_embeddings.device
    if (folder / "answer.json").exists():
        raise ValueError("completed_answer_will_not_be_silently_reexecuted")
    boundary = torch.tensor([state.boundary_ids], dtype=torch.long, device=device)
    boundary_embeddings = base.get_input_embeddings()(boundary)
    full_inputs = torch.cat((state.realized_embeddings, boundary_embeddings), dim=1)
    mask = torch.ones(full_inputs.shape[:2], dtype=torch.long, device=device)
    save_file({"boundary_embeddings": boundary_embeddings.detach().cpu().contiguous()},
        str(folder / "closing-boundary.safetensors"))
    write_json(folder / "decode-handoff.json", {"cache": cache_snapshot(state.cache, state.prefix_tokens),
        "full_inputs_shape": list(full_inputs.shape), "unconsumed_boundary_ids": state.boundary_ids,
        "unconsumed_count": int(full_inputs.shape[1] - state.cache.get_seq_length()),
        "attention_mask": mask[0].tolist(), "seed": seed, "max_new_tokens": max_new_tokens,
        "sampling": {"temperature": .7, "top_p": .8, "top_k": 20, "repetition_penalty": 1.0},
        "external_logits_processor": type(logits_processor).__name__ if logits_processor is not None else None})
    replay_boundary_hidden = None
    if state.verify_replay:
        with torch.inference_mode():
            replay = base.model.language_model(inputs_embeds=full_inputs, attention_mask=mask,
                position_ids=torch.arange(full_inputs.shape[1], device=device).unsqueeze(0), use_cache=False)
            replay_boundary_hidden = replay.last_hidden_state[:, -1].clone()
            del replay
            save_file({"h_after_boundary_full_prefix_replay": replay_boundary_hidden.cpu().contiguous()},
                str(folder / "boundary-replay.safetensors"))
    generated = []
    stopped = {"requested": False}
    started = time.perf_counter()

    class CaptureStream(TextStreamer):
        def __init__(self):
            super().__init__(tokenizer, skip_prompt=False)
            self.prompt_seen = False

        def put(self, value):
            ids = value.detach().cpu().reshape(-1).tolist()
            # Embeds-only generation sends an empty input_ids tensor first.
            if not self.prompt_seen:
                self.prompt_seen = True
                if ids:
                    write_json(folder / "generation-initial-ids.json", ids)
                return
            generated.extend(ids)
            if len(generated) % 32 == 0:
                self.persist("generating")

        def persist(self, stage):
            (folder / "answer-stream.txt").write_text(tokenizer.decode(generated, skip_special_tokens=False), encoding="utf-8")
            write_json(folder / "answer-stream-ids.json", generated)
            write_json(folder / "generation-progress.json", {"stage": stage, "tokens": len(generated),
                "elapsed_seconds": time.perf_counter() - started})

        def end(self):
            self.persist("returned_before_receipt")

    class ExplicitStop(StoppingCriteria):
        def __call__(self, input_ids, scores, **kwargs):
            stopped["requested"] = (folder / "STOP").exists()
            return torch.full((input_ids.shape[0],), stopped["requested"], dtype=torch.bool, device=input_ids.device)

    streamer = CaptureStream()
    criteria = StoppingCriteriaList(list(stopping_criteria or []) + [ExplicitStop()])
    first_forward = {}

    def observe_first_forward(module, args, kwargs):
        if first_forward:
            return
        embeds = kwargs.get("inputs_embeds")
        past = kwargs.get("past_key_values")
        first_forward.update({"inputs_embeds_shape": list(embeds.shape) if embeds is not None else None,
            "input_ids_shape": list(kwargs["input_ids"].shape) if kwargs.get("input_ids") is not None else None,
            "cache_object_id": id(past), "cache_sequence_length": int(past.get_seq_length()) if past is not None else None})
        write_json(folder / "generation-first-forward.json", first_forward)

    hook = base.register_forward_pre_hook(observe_first_forward, with_kwargs=True)
    boundary_seen = {"done": False}

    def observe_boundary_hidden(module, args, kwargs, result):
        if boundary_seen["done"]:
            return
        boundary_seen["done"] = True
        hidden = result.last_hidden_state[:, -1]
        save_file({"h_after_cached_boundary": hidden.detach().cpu().contiguous()},
            str(folder / "boundary-cached.safetensors"))
        receipt = {"readout": lm_readout(base, tokenizer, hidden)}
        if replay_boundary_hidden is not None:
            receipt["full_prefix_replay"] = compare_hidden(base, hidden, replay_boundary_hidden)
        write_json(folder / "boundary-cached.json", receipt)

    boundary_hook = base.model.language_model.register_forward_hook(observe_boundary_hidden, with_kwargs=True)
    set_seed(seed)
    torch.cuda.reset_peak_memory_stats(device)
    try:
        with torch.inference_mode():
            output = base.generate(inputs_embeds=full_inputs, attention_mask=mask,
                past_key_values=state.cache, use_cache=True, do_sample=True,
                temperature=.7, top_p=.8, top_k=20, repetition_penalty=1.0,
                max_new_tokens=max_new_tokens, streamer=streamer,
                logits_processor=logits_processor, stopping_criteria=criteria,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=base.generation_config.eos_token_id)
        torch.cuda.synchronize(device)
    finally:
        hook.remove()
        boundary_hook.remove()
        streamer.persist("generation_left_call")
    ids = output[0].detach().cpu().tolist()
    write_json(folder / "answer-ids.json", ids)
    (folder / "answer-raw.txt").write_text(tokenizer.decode(ids, skip_special_tokens=False), encoding="utf-8")
    (folder / "answer.txt").write_text(tokenizer.decode(ids, skip_special_tokens=True), encoding="utf-8")
    if ids != generated:
        write_json(folder / "generation-stream-mismatch.json", {
            "returned_tokens": len(ids), "streamed_tokens": len(generated),
            "full_returned_ids": "answer-ids.json", "full_streamed_ids": "answer-stream-ids.json"})
        raise RuntimeError("returned_token_ids_differ_from_captured_stream")
    eos = base.generation_config.eos_token_id
    eos = [eos] if isinstance(eos, int) else list(eos or [])
    receipt = {"schema": "nplus-shared-cache-answer/v1", "identity": state.manifest["identity"],
        "tokens": len(ids), "elapsed_seconds": time.perf_counter() - started,
        "eos_token_ids": eos, "ended_with_eos": bool(ids and ids[-1] in eos),
        "reached_cap": len(ids) >= max_new_tokens, "explicitly_stopped": stopped["requested"],
        "partial": stopped["requested"], "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(device),
        "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(device),
        "cache_at_return": cache_snapshot(state.cache, state.prefix_tokens),
        "quality_assessed": False, "not_native_cot_best_ability_claim": True}
    write_json(folder / "answer.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--auxiliary", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--messages")
    source.add_argument("--cases")
    parser.add_argument("--output", required=True)
    parser.add_argument("--latent-steps", type=int, default=3)
    parser.add_argument("--execute", action="store_true", help="Load weights and run inference; default is tokenizer-only preparation")
    parser.add_argument("--verify-replay", action="store_true")
    parser.add_argument("--decode", action="store_true", help="Also run native HF answer generation; no external presence helper")
    parser.add_argument("--inspect-vectors", action="store_true", help="Inspect saved heads and local last-step fusion interventions; no extra generated answers")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    tokenizer, prepared = prepare_cases(model=args.model, auxiliary=args.auxiliary,
        output=args.output, messages=args.messages, cases=args.cases, latent_steps=args.latent_steps)
    if not args.execute:
        print(json.dumps({"prepared_cases": len(prepared), "weights_loaded": False, "gpu_used": False}))
        return
    base, load_receipt = load_frozen_model(args.model)
    write_json(Path(args.output) / "model-load.json", load_receipt)
    auxiliary = load_auxiliary(args.auxiliary, width=base.config.text_config.hidden_size,
        device=str(base.get_input_embeddings().weight.device))
    for case in prepared:
        state = run_shared_case(base, tokenizer, auxiliary, case, verify_replay=args.verify_replay)
        if args.inspect_vectors:
            try:
                from .inspection import inspect_vectors
            except ImportError:
                from inspection import inspect_vectors
            inspect_vectors(base, auxiliary, tokenizer, state)
        if args.decode:
            decode_answer(base, tokenizer, state, seed=args.seed)
        del state
    print(json.dumps({"completed_cases": len(prepared), "training_performed": False}))


if __name__ == "__main__":
    main()
