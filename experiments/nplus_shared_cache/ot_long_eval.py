"""Paired free generation and measured equal-call replay of immutable long-route checkpoints."""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
from statistics import mean

import torch

from . import ot_long_model as model, runner
from .ot_latent_run import SYSTEM, final_number, fraction


def read_cases(path):
    with path.open(encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    if not records:
        raise ValueError("evaluation JSONL is empty")
    seen_ids, seen_questions = set(), set()
    for case in records:
        identity = case.get("id")
        question = case.get("question")
        if not isinstance(identity, str) or not identity.strip() or not isinstance(question, str) or not question.strip():
            raise ValueError("each evaluation row needs a stable ID and a nonempty question")
        normalized = " ".join(question.casefold().split())
        if identity in seen_ids or normalized in seen_questions:
            raise ValueError("evaluation ID or question is duplicated")
        if fraction(case.get("reference")) is None:
            raise ValueError(f"evaluation row {identity} has no gradeable numeric reference")
        seen_ids.add(identity)
        seen_questions.add(normalized)
    return records


def checkpoint(path, routes):
    manifest = path / "route_model.json"
    receipt = json.loads(manifest.read_text(encoding="utf-8"))
    if receipt["schema"] != model.SCHEMA or receipt["config"]["routes"] != routes:
        raise ValueError(f"expected a long-route checkpoint with {routes} routes")
    meta = receipt["metadata"]
    if (not meta.get("training_status") or meta.get("rows_applied", 0) < 1
            or not meta.get("comparison_binding") or not meta.get("teacher_binding_sha256")):
        raise ValueError("checkpoint must disclose training progress and the common dataset, teacher and run identities")
    return dict(route_model_sha256=runner.file_sha(manifest), metadata=meta)


def score(output, case):
    parsed = final_number(output["text"])
    output["parsed"] = str(parsed) if parsed is not None else None
    output["correct"] = (
        output["thinking_status"] == "complete" and output["stop"] == "eos" and parsed == fraction(case["reference"])
    )
    return output


def run_arm(args, paths, report, phase, arm):
    records = report["cases"]
    if all(arm in case[phase] for case in records):
        return
    expected = report["binding"]["checkpoints"][arm]
    if checkpoint(paths[arm], int(arm)) != expected:
        raise ValueError("checkpoint moved between paired passes")
    network = None
    for case in records:
        if arm in case[phase]:
            continue
        natural = case["natural"]
        target = max(entry["natural_calls"] for entry in natural.values()) if phase == "matched" else None
        if phase == "matched" and natural[arm]["natural_calls"] == target:
            case[phase][arm] = dict(natural[arm], matched_measurement="natural_run_at_target")
            runner.write_json(args.output, report)
            continue
        if network is None:
            network = model.load_bundle(paths[arm], device=args.device)
        messages = [{"role": "system", "content": report["binding"]["system"]},
                    {"role": "user", "content": case["question"]}]
        output = score(model.generate(
            *network, messages, latent_budget=args.latent_budget, max_new_tokens=args.answer_budget,
            matched_total_calls=target
        ), case)
        if phase == "matched":
            for key in ("thinking_status", "stop", "per_route_stop", "token_ids", "natural_calls"):
                if output[key] != natural[arm][key]:
                    raise RuntimeError(f"natural and equal-call replays disagree for {case['id']}, arm {arm}, {key}")
            if output["total_calls"] != target:
                raise RuntimeError("equal-call replay did not execute the required workload")
            output["matched_measurement"] = "replay_with_measured_padding"
        case[phase][arm] = output
        runner.write_json(args.output, report)
        print(f"{phase} {arm} route(s), {case['id']}, {output['stop']}, {output['total_calls']} calls", flush=True)
    del network
    gc.collect()
    if torch.device(args.device).type == "cuda":
        torch.cuda.empty_cache()


def summarize(records, arm):
    natural = [case["natural"][arm] for case in records]
    matched = [case["matched"][arm] for case in records]
    correct = sum(item["correct"] for item in matched)
    return dict(cases=len(records), correct=correct, accuracy=correct / len(records),
                unfinished_thinking=sum(item["thinking_status"] == "incomplete" for item in matched),
                natural_eos=sum(item["stop"] == "eos" for item in matched),
                natural_calls=sum(item["natural_calls"] for item in natural),
                matched_total_calls=sum(item["total_calls"] for item in matched),
                matched_padding_calls=sum(item["benchmark_padding_calls"] for item in matched),
                mean_seconds_to_stop=mean(item["seconds_to_stop"] for item in natural),
                mean_matched_seconds=mean(item["seconds"] for item in matched),
                peak_natural_cuda_bytes=max(item["natural_peak_cuda_bytes"] for item in natural),
                peak_matched_cuda_bytes=max(item["peak_cuda_bytes"] for item in matched))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("evaluation", "single", "five", "output"):
        cli.add_argument("--" + name, type=Path, required=True)
    cli.add_argument("--latent-budget", type=int, default=model.DEFAULT_LATENT_BUDGET)
    cli.add_argument("--answer-budget", type=int, default=model.DEFAULT_ANSWER_BUDGET)
    cli.add_argument("--device", default="cuda:0")
    args = cli.parse_args()
    if min(args.latent_budget, args.answer_budget) < 1:
        cli.error("budgets must be positive")
    torch.set_num_threads(min(6, torch.get_num_threads()))
    cases = read_cases(args.evaluation)
    paths = {"1": args.single, "5": args.five}
    checkpoints = {arm: checkpoint(path, int(arm)) for arm, path in paths.items()}
    one, five = [checkpoints[arm]["metadata"] for arm in ("1", "5")]
    for key in ("comparison_binding", "teacher_binding_sha256", "rows_applied", "rows_examined", "total_rows", "task_system"):
        if one.get(key) != five.get(key):
            raise ValueError(f"paired checkpoint training scope differs for {key}")
    binding = dict(schema="openthoughts-long-paired-evaluation/v1", checkpoints=checkpoints,
                   evaluation_sha256=runner.file_sha(args.evaluation), latent_budget=args.latent_budget,
                   answer_budget=args.answer_budget, system=SYSTEM, device=args.device,
                   torch_version=str(torch.__version__),
                   device_name=torch.cuda.get_device_name(torch.device(args.device)) if torch.device(args.device).type == "cuda" else "cpu")
    if args.output.exists():
        report = json.loads(args.output.read_text(encoding="utf-8"))
        if report["binding"] != binding:
            raise ValueError("existing evaluation report belongs to another pair or set of budgets")
    else:
        report = dict(binding=binding, complete=False, score="Exact rational final numerical answer, EOS and all routes stopped. Every question counts.",
                      cases=[dict(id=item["id"], question=item["question"], reference=item["reference"],
                                  source=item.get("source"), natural={}, matched={}) for item in cases])
    for phase in ("natural", "matched"):
        # Reverse loading order on replay. Only one model must fit on the device.
        for arm in (("1", "5") if phase == "natural" else ("5", "1")):
            run_arm(args, paths, report, phase, arm)
    report["summary"] = {arm: summarize(report["cases"], arm) for arm in paths}
    report["complete"] = True
    runner.write_json(args.output, report)


if __name__ == "__main__":
    main()
