"""Observe a pinned, local LOTUS NL checkpoint using the authors' generation path."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time


CODE_REVISION = "eb77e2f7909c5006f58ff0ad7cd6629b942caa9e"
MODEL_REVISION = "9a4fbb8467de1376117f501f2694ca8fac9f3255"
CHECKPOINT = "yingfanbot/gsm-lotus-llama3b-nl"
SHARDS = {
    "model-00001-of-00002.safetensors": "7d75a848c203d6cf421fc53a76a9be9aa2a411ac57a9dd409b3a5dc862e6534c",
    "model-00002-of-00002.safetensors": "8513f75dc4467e0c2168b554c9cb63445bc02b8dc7648857b6a953aa2ef492d8",
}
TOKENIZER_SHA256 = "33b6804858a48cd76783fcefe02ce74635cfca73d6154610484193e4b7a8441a"
GIT_BLOBS = {
    "config.json": "95631242e3a944d0ee0da2381b9875cf519ad7a9",
    "generation_config.json": "144bd5f2c9517b4f661ab69269dfd80703ca597a",
    "model.safetensors.index.json": "300800796cfe82e9a970edd1719dfca40cc691c5",
    "special_tokens_map.json": "b43be96621d147110fb8a18b5776ec6e38516127",
    "tokenizer_config.json": "012896bbbb66d10350e3c23bd35dec07b7c8413c",
}


def run(args):
    import torch
    import transformers

    if transformers.__version__ != "4.46.2":
        raise ValueError("use the upstream transformers==4.46.2 environment")
    if args.output.exists():
        raise FileExistsError(args.output.name)
    for filename, expected in {**SHARDS, "tokenizer.json": TOKENIZER_SHA256}.items():
        with (args.model / filename).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("checkpoint file SHA-256 mismatch: " + filename)
    for filename, expected in GIT_BLOBS.items():
        content = (args.model / filename).read_bytes()
        git_object = f"blob {len(content)}\0".encode() + content
        if hashlib.sha1(git_object).hexdigest() != expected:
            raise ValueError("checkpoint configuration digest mismatch: " + filename)
    source = args.upstream / "scripts" / "lotus.py"
    upstream_revision = subprocess.check_output(
        ["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True).strip()
    expected_source = subprocess.check_output(
        ["git", "-C", str(args.upstream), "show", CODE_REVISION + ":scripts/lotus.py"])
    if (upstream_revision != CODE_REVISION
            or source.read_bytes().replace(b"\r\n", b"\n") != expected_source.replace(b"\r\n", b"\n")):
        raise ValueError("unpinned LOTUS source tree")
    spec = importlib.util.spec_from_file_location("upstream_lotus", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    started = time.perf_counter()
    tokenizer = transformers.AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    tokenizer.pad_token = tokenizer.eos_token
    markers = ["<|start-latent|>", "<|end-latent|>", "<|latent|>"]
    for marker in markers:
        tokenizer.add_tokens(marker)
    start, end, latent = tokenizer.convert_tokens_to_ids(markers)
    backbone = transformers.AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=torch.bfloat16, local_files_only=True)
    if backbone.get_input_embeddings().weight.shape[0] != len(tokenizer):
        raise ValueError("official model and tokenizer vocabularies disagree")
    torch.cuda.reset_peak_memory_stats()
    count, width, loops = 6, 50, 6
    model = module.Lotus(backbone, latent_token_id=latent, start_latent_id=start,
                         end_latent_id=end, eos_token_id=tokenizer.eos_token_id,
                         pad_token_id=tokenizer.pad_token_id, c_thought=width).cuda()
    model.eval()
    tokens = tokenizer.encode(args.question + "\n", add_special_tokens=True)
    tokens += [start] + [latent] * (count * width) + [end]
    inputs = torch.tensor([tokens], device="cuda")
    torch.cuda.synchronize()
    loaded = time.perf_counter()
    with torch.inference_mode():
        generated = model.generate(inputs, torch.ones_like(inputs), n_looped_iters=loops,
                                   max_new_tokens=64)
        scores = model._last_intermediate_logits
        if len(scores) != loops or scores[-1].shape[:2] != (1, count * width + 1):
            raise ValueError("unexpected upstream post-loop readout shape")
        groups = scores[-1][0, :count * width].argmax(-1).reshape(count, width).tolist()
    answer_ids = generated[0, len(tokens):].tolist()
    sampled_ids = [int(token) for token, _ in model._last_decode_token_times]
    blocks = [{"index": index + 1, "ids": ids, "text": tokenizer.decode(ids, skip_special_tokens=False),
               "visible_text": tokenizer.decode(ids, skip_special_tokens=True)}
              for index, ids in enumerate(groups)]
    result = {"status": "completed", "question": args.question, "blocks": blocks,
              "answer": tokenizer.decode(answer_ids, skip_special_tokens=True),
              "answer_ids": answer_ids, "sampled_answer_ids_including_terminal": sampled_ids,
              "ended_with_eos": sampled_ids[-1] == tokenizer.eos_token_id, "eos_id": tokenizer.eos_token_id,
              "checkpoint": CHECKPOINT, "revision": MODEL_REVISION, "code_revision": CODE_REVISION,
              "shard_sha256": SHARDS, "block_width": width, "loops": loops, "prefix_ids": tokens,
              "device": "cuda", "dtype": "bfloat16", "seconds": time.perf_counter() - loaded,
              "load_seconds": loaded - started, "peak_gpu_bytes": torch.cuda.max_memory_allocated(),
              "torch": torch.__version__, "transformers": transformers.__version__,
              "optimizer_steps": 0, "model_api_calls": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--question", required=True)
    run(parser.parse_args())
