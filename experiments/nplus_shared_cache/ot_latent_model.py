"""Qwen with a shared low-rank recurrent workspace and per-route halting."""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, dataclass
from functools import lru_cache
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

import torch
from safetensors.torch import load_file, save_file
from torch import nn
from torch.nn import functional as functional

from . import recurrent_runtime as runtime, runner


@dataclass(frozen=True)
class RouteConfig:
    routes: int
    width: int = 2560
    rank: int = 96
    max_rounds: int = 4
    halt_threshold: float = 0.5

    def __post_init__(self):
        if min(self.routes, self.width, self.rank, self.max_rounds) < 1:
            raise ValueError("route dimensions must be positive")
        if not 0 < self.halt_threshold < 1:
            raise ValueError("halt threshold must be strictly between zero and one")


class LatentRoutes(nn.Module):
    def __init__(self, config: RouteConfig):
        super().__init__()
        self.config = config
        width, rank = config.width, config.rank
        self.condition = nn.Linear(width, rank, bias=False)
        self.cell = nn.GRUCell(rank, rank)
        self.direction = nn.Linear(rank, width, bias=False)
        self.halt = nn.Linear(rank, 1)
        self.step_signal = self._make_clock(config)
        self.merge = nn.Linear(rank * config.routes, rank, bias=False)
        self.merge_output = nn.Linear(rank, width, bias=False)
        self.route_codes = nn.Parameter(torch.empty(config.routes, rank))
        nn.init.normal_(self.route_codes, std=.025)
        nn.init.constant_(self.halt.bias, -1.)
        self.gain = nn.Parameter(torch.tensor(1.))

    @staticmethod
    def _make_clock(config):
        return nn.Parameter(torch.zeros(config.max_rounds, config.rank))

    def _clock(self, index):
        return self.step_signal[index]

    def initial_state(self, hidden):
        seed = self.condition(functional.rms_norm(hidden.float(), (self.config.width,)))
        return torch.tanh(seed[:, None] + self.route_codes)

    def step(self, hidden, state, active, index, embedding_rms):
        batch = hidden.shape[0]
        anchor = functional.rms_norm(hidden.float(), (self.config.width,))
        signal = self.condition(anchor)[:, None] + self.route_codes + self._clock(index)
        updated = self.cell(signal.reshape(-1, self.config.rank),
                            state.reshape(-1, self.config.rank)).reshape_as(state)
        logits = self.halt(updated).squeeze(-1)
        mask = active[..., None]
        live = torch.where(mask, updated, 0.)
        routes = torch.where(mask, anchor[:, None] + self.direction(updated), 0.)
        combined = routes.sum(1) / active.sum(1, keepdim=True).clamp_min(1)
        combined = combined + self.merge_output(self.merge(live.reshape(batch, -1)))
        write = functional.rms_norm(combined, (self.config.width,), eps=1e-8) * embedding_rms * self.gain
        return live, routes, logits, write


@dataclass
class Rollout:
    cache: object
    hidden: torch.Tensor
    halt_logits: torch.Tensor
    masks: torch.Tensor
    latent_calls: int
    per_route_stop: list
    observations: list
    last_route_vectors: torch.Tensor
    route_history: torch.Tensor | None = None
    prefix_calls: int = 1


def encode_prompt(tokenizer, messages_or_question):
    messages = ([{"role": "user", "content": messages_or_question}]
                if isinstance(messages_or_question, str) else messages_or_question)
    return tokenizer.encode(tokenizer.apply_chat_template(messages, tokenize=False,
        add_generation_prompt=True, enable_thinking=True), add_special_tokens=False)


def boundary_ids(tokenizer):
    return tokenizer.encode(runner.BOUNDARY, add_special_tokens=False)


def render_image_prompt(processor, messages_or_question):
    """Render the actual user question as pixels for a visual training example."""
    from PIL import Image, ImageDraw, ImageFont
    import textwrap

    messages = ([{"role": "user", "content": messages_or_question}]
                if isinstance(messages_or_question, str) else list(messages_or_question))
    if not messages or messages[-1]["role"] != "user" or not isinstance(messages[-1]["content"], str):
        raise ValueError("expected a final text question to render")
    font = ImageFont.load_default(size=18)
    wrapped = [line for paragraph in messages[-1]["content"].splitlines()
               for line in (textwrap.wrap(paragraph, width=60, replace_whitespace=False) or [""])]
    if not wrapped:
        raise ValueError("empty image question")
    width = max(256, int(max(font.getlength(line) for line in wrapped)) + 32)
    image = Image.new("RGB", (width, 32 + 26*len(wrapped)), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(wrapped):
        draw.text((16, 12 + 26*index), line, fill="black", font=font)
    messages[-1] = {"role": "user", "content": [{"type": "image"},
                    {"type": "text", "text": "Solve the problem in the image."}]}
    chat = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=True)
    return processor(text=[chat], images=[image], return_tensors="pt"), image


def _prefill_image(base, processor_inputs):
    from transformers.cache_utils import DynamicCache
    from torch.nn.attention import SDPBackend, sdpa_kernel
    from .differentiable_cache import DifferentiableQwen35Cache

    embedding = base.get_input_embeddings()
    inputs = {name: processor_inputs[name].to(embedding.weight.device)
              for name in ("input_ids", "attention_mask", "pixel_values", "image_grid_thw", "mm_token_type_ids")}
    cache_class = DifferentiableQwen35Cache if torch.is_grad_enabled() else DynamicCache
    cache = cache_class(config=base.config.text_config)
    base.model.rope_deltas = None
    with sdpa_kernel(SDPBackend.MATH):
        output = base.model(**inputs, use_cache=True, past_key_values=cache)
    if output.past_key_values is not cache or base.model.rope_deltas is None:
        raise RuntimeError("visual prefill did not return native multimodal positions and cache")
    cache.image_rope_delta = base.model.rope_deltas.detach().clone()
    # Calibrate the continuous writes to native textual embeddings. The image
    # features have already reached hidden and cache through Qwen's merger.
    text_ids = inputs["input_ids"][inputs["mm_token_type_ids"] == 0]
    return cache, output.last_hidden_state[:, -1], embedding(text_ids)[None]


def _benchmark_cache(source):
    detached = {}
    for layer in source.layers:
        for value in vars(layer).values():
            entries = value.values() if isinstance(value, dict) else (value,)
            for entry in entries:
                if torch.is_tensor(entry):
                    detached[id(entry)] = entry.detach().clone()
    return copy.deepcopy(source, detached)


def latent_rollout(base, heads, prefix_ids, *, matched=True, observe=False, teacher_alive=None,
                   multimodal_inputs=None):
    config = heads.config
    if multimodal_inputs is None:
        cache, hidden, prefix = runtime._prefill(base, prefix_ids, differentiable=torch.is_grad_enabled(),
                                                  train_prefix=True)
    else:
        cache, hidden, prefix = _prefill_image(base, multimodal_inputs)
    active = torch.ones(1, config.routes, device=hidden.device, dtype=torch.bool)
    state = heads.initial_state(hidden)
    if teacher_alive is not None:
        teacher_alive = torch.as_tensor(teacher_alive, dtype=torch.bool, device=hidden.device)
        if (not torch.is_grad_enabled() or tuple(teacher_alive.shape) != (config.max_rounds, config.routes)
                or not bool(teacher_alive[0].all()) or bool((teacher_alive[1:] & ~teacher_alive[:-1]).any())):
            raise ValueError("training lifetime masks must start live and cannot reactivate routes")
    norms = prefix.float().square().mean().sqrt()
    halt_history, masks, observations = [], [], []
    train_history = [] if torch.is_grad_enabled() else None
    benchmark_cache = None
    for index in range(config.max_rounds):
        if teacher_alive is not None:
            active = teacher_alive[index][None]
        live_step = bool(active.any())
        if not matched and not live_step:
            break
        state, routes, logits, write = heads.step(hidden, state, active, index, norms)
        if train_history is not None:
            train_history.append(routes[0])
        halt_history.append(logits[0])
        masks.append(active[0])
        if observe:
            with torch.no_grad():
                ids = base.lm_head(routes[0].to(base.lm_head.weight.dtype)).argmax(-1)
                observations.append(torch.where(active[0], ids, -1).tolist())
        if live_step:
            last_live_vectors = routes
            hidden = runtime._forward(base, cache, write[:, None].to(prefix.dtype))[:, -1]
        else:
            with torch.no_grad():
                if benchmark_cache is None:
                    benchmark_cache = _benchmark_cache(cache)
                runtime._forward(base, benchmark_cache, write[:, None].to(prefix.dtype))
        active = active & (logits.detach().sigmoid() < config.halt_threshold)
    logit_tensor, mask_tensor = torch.stack(halt_history), torch.stack(masks)
    if teacher_alive is not None:
        stopped = teacher_alive.sum(dim=0).tolist()
    else:
        stopping = (logit_tensor.detach().sigmoid() >= config.halt_threshold) & mask_tensor
        stopped = [int(step) + 1 if bool(ended) else None for step, ended in zip(
            stopping.long().argmax(dim=0).tolist(), stopping.any(dim=0).tolist())]
    return Rollout(cache, hidden, logit_tensor, mask_tensor, len(masks),
                   stopped, observations, last_live_vectors,
                   torch.stack(train_history) if train_history is not None else None)


def _synchronize(device):
    if device.type == "cuda":
        torch.cuda.synchronize(device)


@torch.inference_mode()
def generate(base, tokenizer, heads, messages_or_question, *, matched=False, observe=False,
             max_new_tokens=32768, matched_answer_steps=None):
    budget = int(max_new_tokens if matched_answer_steps is None else matched_answer_steps)
    if min(budget, int(max_new_tokens)) < 1:
        raise ValueError("generation budget must be positive")
    device = base.get_input_embeddings().weight.device
    _synchronize(device)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()
    prefix_ids = encode_prompt(tokenizer, messages_or_question)
    closing = boundary_ids(tokenizer)
    capacity = base.config.text_config.max_position_embeddings - len(prefix_ids) - len(closing) - heads.config.max_rounds
    if capacity < budget and matched_answer_steps is not None:
        raise ValueError("insufficient context for matched evaluation")
    budget = min(budget, capacity)
    if budget < 1:
        raise ValueError("no answer capacity left")
    result = latent_rollout(base, heads, prefix_ids, matched=matched, observe=observe)
    _synchronize(device)
    latent_seconds = time.perf_counter() - started
    config_eos = base.generation_config.eos_token_id
    ends = set(config_eos if isinstance(config_eos, (list, tuple)) else [config_eos])
    ends.add(tokenizer.eos_token_id)
    padding = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    tokens, decode_input, stop, active_calls = [], closing, None, 0
    until_stop = None
    for index in range(budget):
        inputs = torch.tensor([decode_input], device=device)
        final = runtime._forward(base, result.cache, base.get_input_embeddings()(inputs))[:, -1]
        if stop is None:
            selected = int(base.lm_head(final.to(base.lm_head.weight.dtype)).argmax(-1).item())
            tokens.append(selected)
            active_calls += 1
            if selected in ends:
                stop = "eos"
            elif active_calls >= min(max_new_tokens, budget):
                stop = "token_limit"
            if stop is not None:
                _synchronize(device)
                until_stop = time.perf_counter() - started
                if matched_answer_steps is None:
                    break
            decode_input = [selected]
        else:
            decode_input = [padding]
    calls = index + 1
    _synchronize(device)
    elapsed = time.perf_counter() - started
    observation_texts = None
    if observe:
        observation_texts = [tokenizer.decode([row[slot] for row in result.observations if row[slot] != -1],
                             skip_special_tokens=False) for slot in range(heads.config.routes)]
    return dict(text=tokenizer.decode(tokens, skip_special_tokens=True), token_ids=tokens, stop=stop,
        per_route_stop=result.per_route_stop, observations=observation_texts, observation_token_ids=result.observations,
        prefix_calls=1, prompt_calls=1, latent_calls=result.latent_calls, answer_calls=calls,
        answer_active_calls=active_calls, total_calls=1 + calls + result.latent_calls,
        alive_before=result.masks.tolist(), seconds=elapsed, seconds_to_stop=until_stop,
        latent_seconds=latent_seconds, peak_cuda_bytes=torch.cuda.max_memory_allocated(device) if device.type=="cuda" else 0)


@lru_cache(maxsize=64)
def _digest_path(path, size, modified):
    return runner.file_sha(Path(path))


def _base_identity(path, *, include_processor=True):
    names = ["config.json", "tokenizer.json", "tokenizer_config.json", "generation_config.json"]
    optional = ["chat_template.jinja", "model.safetensors.index.json"]
    if include_processor:
        optional.extend(("preprocessor_config.json", "video_preprocessor_config.json", "processor_config.json"))
    for extra in optional:
        if (path / extra).exists():
            names.append(extra)
    shards = sorted(item.name for item in path.glob("*.safetensors"))
    if not shards:
        raise ValueError("expected local Qwen safetensors")
    def digest(name):
        item = (path / name).resolve(strict=True)
        stat = item.stat()
        return _digest_path(str(item), stat.st_size, stat.st_mtime_ns)
    return {name: digest(name) for name in names + shards}


def save_bundle(bundle_dir, heads, base_dir, *, base_ref="base", metadata=None):
    return _save_bundle(bundle_dir, heads, base_dir, base_ref=base_ref, metadata=metadata,
                        schema="open_thoughts_continuous_routes/v2")


def _save_bundle(bundle_dir, heads, base_dir, *, base_ref, metadata, schema):
    root, base_dir = Path(bundle_dir), Path(base_dir)
    if Path(base_ref).is_absolute() or (root / base_ref).resolve() != base_dir.resolve():
        raise ValueError("the portable base reference must resolve to the supplied model")
    root.mkdir(parents=True, exist_ok=True)
    path = root / "route_model.safetensors"
    receipt = root / "route_model.json"
    if receipt.exists():
        raise FileExistsError("checkpoint destination already contains a completed bundle")
    identity = _base_identity(base_dir)
    with TemporaryDirectory(dir=root, prefix=".route-write-") as staging:
        weights = Path(staging) / path.name
        save_file({name: value.detach().cpu().contiguous() for name, value in heads.state_dict().items()},
                  str(weights))
        staged_receipt = Path(staging) / receipt.name
        runner.write_json(staged_receipt, dict(schema=schema,
            config=asdict(heads.config), base_ref=base_ref, base_sha256=identity,
            adapter_sha256=runner.file_sha(weights), metadata=metadata or {}))
        runner.replace_with_retry(weights, path)
        runner.replace_with_retry(staged_receipt, receipt)


def load_bundle(bundle_dir, *, base_dir=None, device="cuda:0"):
    return _load_bundle(bundle_dir, base_dir=base_dir, device=device,
                        schemas={"open_thoughts_continuous_routes/v1", "open_thoughts_continuous_routes/v2"},
                        config_class=RouteConfig, heads_class=LatentRoutes)


def _load_bundle(bundle_dir, *, base_dir, device, schemas, config_class, heads_class):
    from transformers import AutoTokenizer
    root = Path(bundle_dir)
    receipt = json.loads((root / "route_model.json").read_text(encoding="utf-8"))
    if receipt["schema"] not in schemas:
        raise ValueError("unsupported checkpoint schema")
    base_path = Path(base_dir) if base_dir is not None else root / receipt["base_ref"]
    if receipt["base_sha256"] != _base_identity(base_path, include_processor=receipt["schema"] != "open_thoughts_continuous_routes/v1"):
        raise ValueError("native model, tokenizer, or processor differs from this trained route bundle")
    path = root / "route_model.safetensors"
    if receipt["adapter_sha256"] != runner.file_sha(path):
        raise ValueError("route weights differ from manifest")
    base, _ = runner.load_frozen_model(base_path, device=device)
    tokenizer = AutoTokenizer.from_pretrained(base_path, local_files_only=True, trust_remote_code=False)
    heads = heads_class(config_class(**receipt["config"])).to(device).eval()
    heads.load_state_dict(load_file(str(path)), strict=True)
    return base, tokenizer, heads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--question", required=True)
    system = parser.add_mutually_exclusive_group()
    system.add_argument("--system", help="System message to place before the question")
    system.add_argument("--training-prompt", action="store_true",
        help="Use the exact training and evaluation system message recorded in this bundle")
    parser.add_argument("--observe", action="store_true", help="Also project route vectors through the frozen word head")
    parser.add_argument("--matched", action="store_true", help="Keep executing latent positions after all routes stopped")
    parser.add_argument("--max-new-tokens", type=int, default=32768)
    args = parser.parse_args()
    prompt = args.system
    if args.training_prompt:
        receipt = json.loads((args.bundle / "route_model.json").read_text(encoding="utf-8"))
        prompt = receipt.get("metadata", {}).get("task_system")
        if not isinstance(prompt, str) or not prompt:
            parser.error("this bundle has no recorded training system message")
    messages = [{"role": "user", "content": args.question}]
    if prompt is not None:
        messages.insert(0, {"role": "system", "content": prompt})
    model = load_bundle(args.bundle, base_dir=args.base, device=args.device)
    print(json.dumps(generate(*model, messages, observe=args.observe, matched=args.matched,
                             max_new_tokens=args.max_new_tokens), ensure_ascii=False))


if __name__ == "__main__":
    main()
