"""Variable-duration continuous routes over one shared Qwen cache."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time

import torch
from torch import nn

from . import ot_latent_model as legacy, recurrent_runtime as runtime
from .ot_latent_model import boundary_ids, encode_prompt, render_image_prompt, _base_identity, _prefill_image


SCHEMA = "open_thoughts_long_continuous_routes/v1"
DEFAULT_LATENT_BUDGET = 1024
DEFAULT_ANSWER_BUDGET = 32768


@dataclass(frozen=True)
class RouteConfig:
    routes: int
    width: int = 2560
    rank: int = 96
    halt_threshold: float = 0.5

    def __post_init__(self):
        if self.routes not in (1, 5) or min(self.width, self.rank) < 1:
            raise ValueError("expected one or five routes and positive dimensions")
        if not 0 < self.halt_threshold < 1:
            raise ValueError("halt threshold must be strictly between zero and one")


class LatentRoutes(legacy.LatentRoutes):
    """The same shared GRU and masked fusion operate at every nonnegative step."""

    @staticmethod
    def _make_clock(config):
        return nn.Parameter(torch.zeros(config.rank))

    def _clock(self, index):
        return self.step_signal * math.log1p(index)


def save_bundle(bundle_dir, heads, base_dir, *, base_ref="base", metadata=None):
    if not isinstance(heads, LatentRoutes):
        raise ValueError("long-route bundles require long-route weights")
    return legacy._save_bundle(bundle_dir, heads, base_dir, base_ref=base_ref, metadata=metadata, schema=SCHEMA)


def load_bundle(bundle_dir, *, base_dir=None, device="cuda:0"):
    return legacy._load_bundle(bundle_dir, base_dir=base_dir, device=device,
                               schemas={SCHEMA}, config_class=RouteConfig, heads_class=LatentRoutes)


@torch.inference_mode()
def generate(base, tokenizer, heads, messages_or_question, *, latent_budget=DEFAULT_LATENT_BUDGET,
             max_new_tokens=DEFAULT_ANSWER_BUDGET, observe=False, diagnostic_answer_on_partial=False,
             matched_total_calls=None):
    if min(latent_budget, max_new_tokens) < 1:
        raise ValueError("latent and answer budgets must be positive")
    embedding = base.get_input_embeddings()
    device = embedding.weight.device
    legacy._synchronize(device)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()
    prefix = encode_prompt(tokenizer, messages_or_question)
    closing = boundary_ids(tokenizer)
    capacity = base.config.text_config.max_position_embeddings
    available = capacity - len(prefix) - len(closing)
    if available < 1:
        raise ValueError("prompt leaves no room for a latent write and the answer boundary")
    cache, hidden, embeds = runtime._prefill(base, prefix, differentiable=False)
    state = heads.initial_state(hidden)
    rms = embeds.float().square().mean().sqrt()
    route_count = heads.config.routes
    active = torch.ones((1, route_count), dtype=torch.bool, device=device)
    stop_steps = torch.zeros_like(active, dtype=torch.long)
    observations = []
    for index in range(min(latent_budget, available)):
        state, vectors, logits, write = heads.step(hidden, state, active, index, rms)
        if observe:
            word_ids = base.lm_head(vectors[0].to(base.lm_head.weight.dtype)).argmax(-1)
            observations.append(torch.where(active[0], word_ids, -1).tolist())
        # Every active slot contributes its terminal vector before it retires.
        hidden = runtime._forward(base, cache, write[:, None].to(embeds.dtype))[:, -1]
        stopping = active & (logits.sigmoid() >= heads.config.halt_threshold)
        stop_steps = torch.where(stopping, index + 1, stop_steps)
        active = active & ~stopping
        if not bool(active.any()):
            break
    latent_calls = index + 1
    per_route_stop = [step or None for step in stop_steps[0].tolist()]
    complete = all(step is not None for step in per_route_stop)
    legacy._synchronize(device)
    latent_seconds = time.perf_counter() - started
    latent_stop = "all_routes_stopped" if complete else (
        "latent_budget" if latent_budget <= available else "context_limit")
    answer_capacity = available - latent_calls + 1
    tokens = []
    stop = "thinking_incomplete"
    if complete or diagnostic_answer_on_partial:
        config_eos = base.generation_config.eos_token_id
        ends = set(config_eos if isinstance(config_eos, (list, tuple)) else [config_eos])
        ends.add(tokenizer.eos_token_id)
        next_ids = closing
        for _ in range(min(max_new_tokens, answer_capacity)):
            inputs = embedding(torch.tensor([next_ids], device=device))
            hidden = runtime._forward(base, cache, inputs)[:, -1]
            selected = int(base.lm_head(hidden.to(base.lm_head.weight.dtype)).argmax(-1).item())
            tokens.append(selected)
            if selected in ends:
                stop = "eos"
                break
            next_ids = [selected]
        else:
            stop = "token_limit" if max_new_tokens <= answer_capacity else "context_limit"
    legacy._synchronize(device)
    seconds_to_stop = time.perf_counter() - started
    active_peak = torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0
    actual_calls = 1 + latent_calls + len(tokens)
    target_calls = actual_calls if matched_total_calls is None else matched_total_calls
    if target_calls < actual_calls:
        raise ValueError("matched call budget is shorter than the measured natural generation")
    padding_calls = target_calls - actual_calls
    if padding_calls:
        # No formal answer or subsequent route read can observe these benchmark writes.
        pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
        padding = embedding(torch.tensor([[pad_id]], device=device))
        for _ in range(padding_calls):
            if cache.get_seq_length() >= capacity:
                cache, _, _ = runtime._prefill(base, [pad_id], differentiable=False)
            else:
                runtime._forward(base, cache, padding)
    legacy._synchronize(device)
    seconds = time.perf_counter() - started
    texts = None
    if observe:
        texts = [tokenizer.decode([row[i] for row in observations if row[i] >= 0],
                                  skip_special_tokens=False) for i in range(route_count)]
    return dict(text=tokenizer.decode(tokens, skip_special_tokens=True), token_ids=tokens, stop=stop,
                thinking_status="complete" if complete else "incomplete", latent_stop=latent_stop,
                status_label="思考完成" if complete else "思考未完成",
                diagnostic_answer=bool(not complete and diagnostic_answer_on_partial),
                per_route_stop=per_route_stop, observations=texts, observation_token_ids=observations,
                prefix_calls=1, latent_calls=latent_calls, answer_active_calls=len(tokens),
                natural_calls=actual_calls, benchmark_padding_calls=padding_calls, total_calls=target_calls,
                latent_seconds=latent_seconds, seconds_to_stop=seconds_to_stop, seconds=seconds,
                natural_peak_cuda_bytes=active_peak,
                peak_cuda_bytes=torch.cuda.max_memory_allocated(device) if device.type == "cuda" else 0)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--bundle", type=Path, required=True)
    cli.add_argument("--device", default="cuda:0")
    cli.add_argument("--question", required=True)
    cli.add_argument("--system", help="Override the bundle's task system message")
    cli.add_argument("--latent-budget", type=int)
    cli.add_argument("--max-new-tokens", type=int)
    cli.add_argument("--observe", action="store_true")
    cli.add_argument("--diagnostic-answer-on-partial", action="store_true")
    args = cli.parse_args()
    receipt = json.loads((args.bundle / "route_model.json").read_text(encoding="utf-8"))
    metadata = receipt.get("metadata", {})
    system = args.system if args.system is not None else metadata.get("task_system")
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": args.question})
    model = load_bundle(args.bundle, device=args.device)
    print(json.dumps(generate(
        *model, messages, observe=args.observe, diagnostic_answer_on_partial=args.diagnostic_answer_on_partial,
        latent_budget=args.latent_budget if args.latent_budget is not None else metadata.get("latent_budget", DEFAULT_LATENT_BUDGET),
        max_new_tokens=args.max_new_tokens if args.max_new_tokens is not None else metadata.get("answer_budget", DEFAULT_ANSWER_BUDGET)
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
