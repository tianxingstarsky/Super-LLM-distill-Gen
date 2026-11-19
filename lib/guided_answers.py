"""Import keyed or reasoning-only JSONL, guide native nonthinking answers, and export results."""

import argparse
import json
from pathlib import Path

from lib.bootstrap.guided_answers import generate_guided_cases


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--student-url")
    cli.add_argument("--student-model")
    cli.add_argument("--coach-url")
    cli.add_argument("--coach-model")
    cli.add_argument("--student-api-key-env")
    cli.add_argument("--coach-api-key-env")
    cli.add_argument(
        "--coach-thinking",
        action="store_true",
        help="Enable native reasoning for a local tutor",
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
    cli.add_argument("--first-pass-only", action="store_true",
                     help="Run independent final-only student answers and checkpoint questions needing guidance")
    cli.add_argument("--protocol-version", help="Pin a supported protocol when creating a new run")
    cli.add_argument("--verified-step-receipts", type=Path,
                     help="Trust teaching microsteps independently checked by the nominated source verifier. The run freezes its receipt registry.")
    cli.add_argument("--until-correct", action=argparse.BooleanOptionalAction, default=None,
                     help="Continue while verifiable guidance is available (default for new 4.2 runs). "
                          "Use --no-until-correct for a bounded experiment")
    cli.add_argument(
        "--reuse-first-from",
        type=Path,
        help="Replay the same case's byte-identical initial student request from a previous guided run; all tutor calls are new",
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
    return 0 if result["complete"] or result.get("first_pass_complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
