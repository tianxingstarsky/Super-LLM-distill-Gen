"""CLI arguments for the optional local training comparison use case."""
import argparse
import json

from lib.contexts.training.api import TrainingValidationPlan


def configure(parser):
    for name in ("model", "sft", "validation", "cpt", "evaluation", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--action", choices=("preflight", "run", "report"), default="preflight")
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--epochs", type=float, default=1)
    parser.add_argument("--max-length", type=int, default=8192)
    parser.add_argument("--cpt-block-size", type=int, default=1024)
    parser.add_argument("--max-new-tokens", type=int, default=32768)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--system-message", default="You are a helpful assistant.")
    parser.set_defaults(func=run)
    return parser


def run(args):
    from lib.bootstrap.training_validation import training_validation_application
    fields = TrainingValidationPlan.__dataclass_fields__
    plan = TrainingValidationPlan(**{key: getattr(args, key) for key in fields})
    report = training_validation_application().execute(plan, action=args.action)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["status"] in {"preflight_passed", "completed", "recorded_results"} else 2


def main():
    return run(configure(argparse.ArgumentParser(description="Local baseline/SFT/CPT→SFT validation")).parse_args())
