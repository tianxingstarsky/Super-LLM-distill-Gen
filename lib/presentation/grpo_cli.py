"""Explicit CLI entry for the optional local TRL reinforcement-learning pilot."""
import argparse
import json

from lib.contexts.training.api import GRPOValidationPlan


def configure(parser):
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--task-source", choices=("synthetic", "gsm8k"), default="synthetic")
    parser.add_argument("--resume-from", dest="parent_run", default="",
        help="Increase output capacity in a new run and continue the authenticated parent checkpoint")
    parser.add_argument("--action", choices=("preflight", "baseline", "run", "report"), default="preflight")
    defaults = GRPOValidationPlan("model", "output")
    for field in ("seed", "train_size", "evaluation_size", "num_generations", "max_prompt_length",
                  "max_completion_length", "batch_size", "gradient_accumulation", "lora_rank", "num_iterations"):
        parser.add_argument("--" + field.replace("_", "-"), type=int, default=getattr(defaults, field))
    for field in ("epochs", "learning_rate", "temperature", "beta"):
        parser.add_argument("--" + field.replace("_", "-"), type=float, default=getattr(defaults, field))
    parser.set_defaults(func=run)
    return parser


def run(args):
    from lib.bootstrap.training_validation import grpo_validation_application
    plan = GRPOValidationPlan(**{field: getattr(args, field) for field in GRPOValidationPlan.__dataclass_fields__})
    report = grpo_validation_application().execute(plan, action=args.action)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["status"] in {"preflight_passed", "baseline_completed", "completed", "recorded_results"} else 2


def main():
    return run(configure(argparse.ArgumentParser(description="Local single-pass TRL GRPO validation")).parse_args())
