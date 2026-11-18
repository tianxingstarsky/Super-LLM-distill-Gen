"""Extend the first fixed-budget comparison to distinguish truncation from a final answer."""

import argparse
import gc
import json
from pathlib import Path

import torch

from . import ot_latent_model as model
from . import runner
from .ot_latent_run import final_number, fraction


def extend_first(run, ceiling):
    primary = json.loads((run / "eval-results.json").read_text("utf-8"))
    case = primary["cases"][0]
    if len(primary["summary"]) != 2 or ceiling <= primary["answer_budget"]:
        raise ValueError(
            "use a completed primary comparison and a greater token ceiling"
        )
    output = run / "extended-case-01.json"
    binding = {
        "primary": runner.file_sha(run / "eval-results.json"),
        "ceiling": ceiling,
    }
    if output.exists():
        record = json.loads(output.read_text("utf-8"))
        if record["binding"] != binding:
            raise ValueError("existing extended comparison has different inputs")
    else:
        record = dict(
            binding=binding,
            id=case["id"],
            question=case["question"],
            reference=case["reference"],
            system=primary["system"],
            arms={},
        )
    messages = [
        {"role": "system", "content": record["system"]},
        {"role": "user", "content": record["question"]},
    ]
    # The arm already known to produce the longer prefix determines the actual
    # matched budget. Break a tie at the full ceiling so either arm can finish.
    lengths = {n: case["arms"][n]["answer_active_calls"] for n in ("1", "5")}
    first, second = sorted(lengths, key=lengths.get, reverse=True)
    matched = ceiling if lengths[first] == lengths[second] else None
    for name in (first, second):
        if name not in record["arms"]:
            base, tokenizer, heads = model.load_bundle(
                run / "models" / f"routes-{name}"
            )
            generated = model.generate(
                base,
                tokenizer,
                heads,
                messages,
                matched=True,
                max_new_tokens=ceiling,
                matched_answer_steps=matched,
            )
            prior = case["arms"][name]["token_ids"]
            parsed = final_number(generated["text"])
            generated.update(
                prefix_matches_primary=generated["token_ids"][: len(prior)] == prior,
                prefix_compared_tokens=len(prior),
                parsed=str(parsed) if parsed is not None else None,
                correct=generated["stop"] == "eos"
                and parsed == fraction(case["reference"]),
                padding_seconds=generated["seconds"] - generated["seconds_to_stop"],
            )
            record["arms"][name] = generated
            runner.write_json(output, record)
            del base, tokenizer, heads
            gc.collect()
            torch.cuda.empty_cache()
        result = record["arms"][name]
        if not result["prefix_matches_primary"]:
            raise RuntimeError(
                "reloaded sequence diverged before the original stopping point"
            )
        matched = result["answer_calls"]
    if record["arms"][first]["total_calls"] != record["arms"][second]["total_calls"]:
        raise RuntimeError("extended comparison does not have equal backbone calls")
    record["matched_total_calls_per_arm"] = record["arms"][first]["total_calls"]
    runner.write_json(output, record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--ceiling", type=int, default=2048)
    options = parser.parse_args()
    extend_first(options.run, options.ceiling)
