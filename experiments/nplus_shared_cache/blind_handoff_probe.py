"""Decode matched source traces with identical recipient text and lifetimes."""
import argparse
import time
from pathlib import Path

import torch
from safetensors.torch import load_file

from . import recurrent_runtime as runtime, runner
from .handoff_probe import digest, layouts, score
from .recurrent_contract import prepare_cases
from .recurrent_heads import RecurrentDirectionHeads


@torch.inference_mode()
def run(args):
    started = time.perf_counter()
    args.output.mkdir(parents=True, exist_ok=False)
    source = runtime._json(args.handoff / "results.json")
    config = runtime._json(args.checkpoint / "auxiliary-config.json")
    if (not source["complete"] or runner.file_sha(args.fixtures) != source["fixtures_sha256"]
            or config["auxiliary_file_sha256"] != source["checkpoint_sha256"]):
        raise ValueError("source handoff, fixture or checkpoint mismatch")
    fixtures = runtime._json(args.fixtures)["pairs"]
    question = "请确认取件日和联系人的姓氏。"
    runner.write_json(args.output / "case.json", [dict(case_id="blind", messages=[
        {"role": "user", "content": question}])])
    tokenizer, [common] = prepare_cases(args.model, args.checkpoint, args.output / "case.json",
                                       args.output / "common")
    base, _ = runner.load_frozen_model(args.model)
    heads = RecurrentDirectionHeads(config["width"], config["memory_width"], config["rank"]).cuda()
    heads.load_state_dict(load_file(str(args.checkpoint / "auxiliary.safetensors"), device="cuda"))
    heads.eval().requires_grad_(False)
    moves = {row["case_id"]: row for row in source["traces"]}
    case_ids = [f"{fixture['id']}-{side}" for fixture in fixtures for side in (0, 1)]
    block = config["block_size"]
    rounds = min(min(moves[case_id]["endings"]) for case_id in case_ids) // block * block
    if rounds < block:
        raise ValueError("no shared fully active block")
    rows = []
    for fixture in fixtures:
        for side, day in enumerate(fixture["days"]):
            case_id = f"{fixture['id']}-{side}"
            folder = args.handoff / "inputs" / case_id
            if runtime._json(folder / "model-messages.json") != [
                    dict(msg, content=msg["content"].replace("{day}", day)) for msg in fixture["messages"]]:
                raise ValueError("source messages do not match fixture")
            trace = load_file(str(folder / "handoff-trace.safetensors"))
            q, mask, fused = (trace[name] for name in ("all_proposed_q", "alive_before", "fused"))
            if [digest(q), digest(mask), digest(fused)] != [moves[case_id][key] for key in
                    ("q_sha256","mask_sha256","native_fused_sha256")]:
                raise ValueError("trace digest mismatch")
            if not mask[:rounds].all():
                raise ValueError("mask would disclose source identity")
            original = runtime._json(folder / "prefix-ids.json")
            embeds = base.get_input_embeddings()(torch.tensor([original], device="cuda"))
            arms, _, _ = layouts(heads, q[:rounds].cuda(), mask[:rounds].cuda(),
                                  fused[:rounds].cuda(), embeds, block)
            for arm in ("prompt_only","cat_1","cat_split_5","separate_projected_5","zero_5","visible_text"):
                prefix = original if arm == "visible_text" else common["prefix_ids"]
                cache, _, _ = runtime._prefill(base, prefix, differentiable=False)
                values, chunk = arms["prompt_only" if arm == "visible_text" else arm]
                for part in values.split(chunk):
                    if part.numel():
                        runtime._forward(base, cache, part[None])
                fields = dict(day=score(base, tokenizer, cache, fixture["date_prefix"],
                                        ["周"+day, "周"+fixture["days"][1-side]]),
                              contact=score(base, tokenizer, cache, fixture["contact_prefix"], fixture["contacts"]))
                rows.append(dict(case_id=case_id, arm=arm, fields=fields, positions=len(values)))
    for fixture in fixtures:
        for arm in ("prompt_only","zero_5"):
            first, second = [row["fields"] for row in rows if row["case_id"].startswith(fixture["id"]+"-")
                             and row["arm"] == arm]
            if first["day"]["logp"] != second["day"]["logp"][::-1] or first["contact"] != second["contact"]:
                raise RuntimeError("identical negative controls disagreed")
    runner.write_json(args.output / "results.json", dict(complete=True, rows=rows, rounds=rounds,
        common_prompt=question, model_calls_paid=0, optimizer_steps=0,
        elapsed_seconds=time.perf_counter()-started, peak_cuda_bytes=torch.cuda.max_memory_allocated(),
        source_receipt_sha256=runner.file_sha(args.handoff/"results.json")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model","checkpoint","handoff","fixtures","output"):
        parser.add_argument("--"+name,type=Path,required=True)
    run(parser.parse_args())
