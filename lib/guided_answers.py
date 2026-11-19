"""Import keyed or reasoning-only JSONL, guide native nonthinking answers, and export results."""

import argparse
import json
from pathlib import Path

from lib.bootstrap.guided_answers import SAMPLING_PROFILES, generate_guided_cases


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--case-id", dest="case_ids", action="append",
                     help="Schedule this case from the unchanged input shard. Repeat to select more cases. "
                          "The shard summary still counts every source row.")
    cli.add_argument("--student-url")
    cli.add_argument("--student-model")
    cli.add_argument("--coach-url")
    cli.add_argument("--coach-model", help="Tutor used for disclosure and review; protocol 4.4 also uses it to write the final answer after a verified solution")
    cli.add_argument("--student-api-key-env")
    cli.add_argument("--coach-api-key-env")
    cli.add_argument("--continuation-model", help="Optional stronger student for verified full-disclosure cases")
    cli.add_argument("--continuation-url", help="Local llama.cpp endpoint for the stronger student; defaults to the tutor URL")
    cli.add_argument("--continuation-api-key-env")
    for role in ("student", "coach", "continuation"):
        cli.add_argument(f"--{role}-sampling", choices=SAMPLING_PROFILES,
                         help="Local text sampling profile. Use the model size for opaque aliases. "
                              "New runs auto-detect standard Qwen names and otherwise use server defaults.")
    cli.add_argument(
        "--coach-thinking",
        action=argparse.BooleanOptionalAction, default=None,
        help="Enable or disable native reasoning for every local teacher role. New runs default to on from 4.5; resumed runs use their frozen setting.",
    )
    cli.add_argument(
        "--context-tokens",
        type=int,
        default=131072,
        help="Declared remote tutor capacity; native slots are measured",
    )
    cli.add_argument("--max-tokens", type=int, default=32768)
    cli.add_argument("--max-attempts", type=int, default=3,
                     help="Consecutive rounds without verified progress in continuous mode; total attempts in bounded mode")
    cli.add_argument(
        "--workers",
        "--concurrency",
        type=int,
        default=4,
        help="Upper bound; native slot availability may lower actual concurrency",
    )
    cli.add_argument(
        "--experience",
        type=Path,
        help="Frozen version and optional student instruction string; private coach uses the review protocol",
    )
    cli.add_argument("--export-only", action="store_true")
    phase = cli.add_mutually_exclusive_group()
    phase.add_argument("--first-pass-only", action="store_true",
                       help="Run independent final-only student answers and checkpoint questions needing guidance")
    phase.add_argument("--prefetch-worked-first", nargs="?", type=int, const=True, default=False, metavar="N",
                       help="Checkpoint the first student response for every keyed worked-solution question "
                            "or just the first N in input order. "
                            "Resume the same output without this flag to review and finish them.")
    cli.add_argument("--historical-first-from", type=Path,
                     help="Continue from a completed greedy local direct answer as historical input. "
                          "Sampling applies to new calls. Missing evidence stops the first pass.")
    cli.add_argument("--historical-first-input", type=Path,
                     help="Original full input JSONL for a subset of a historical first-pass run. "
                          "Its file digest must match the source manifest.")
    cli.add_argument("--protocol-version", help="Pin a supported protocol when creating a new run")
    cli.add_argument("--verified-step-receipts", type=Path,
                     help="Trust teaching microsteps independently checked by the nominated source verifier. The run freezes its receipt registry.")
    cli.add_argument("--reuse-matching-from", type=Path,
                     help="Reuse completed calls from another run of the same input, experience and endpoints "
                          "when the exact request bytes, role and checkpoint match")
    cli.add_argument("--until-correct", action=argparse.BooleanOptionalAction, default=None,
                     help="Continue while verifiable guidance is available (default since protocol 4.2). "
                          "Use --no-until-correct for a bounded experiment")
    cli.add_argument(
        "--reuse-first-from",
        type=Path,
        help="Reuse the same case's byte-identical initial student request from the original run. Use --reuse-matching-from separately for other identical calls.",
    )
    cli.add_argument(
        "--reuse-worked-first-from", type=Path,
        help="When changing tutors, reuse original first student replies to keyed worked questions from "
             "the same input shard. Questions without a prior request run normally.",
    )
    cli.add_argument(
        "--retry-interrupted",
        action="store_true",
        help="Reissue unresolved model calls; prior charges may exist",
    )
    args = vars(cli.parse_args())
    source = args.pop("input")
    output = args.pop("output")
    if args["experience"]:
        args["experience"] = json.loads(
            args["experience"].read_text(encoding="utf-8-sig")
        )
    result = generate_guided_cases(source, output, **args)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if any(result.get(key) for key in ("complete", "selection_complete", "first_pass_complete", "prefetch_complete")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
