"""Command-line entry for one epoch of native Qwen3.5 full-language SFT."""
import json
from pathlib import Path


def configure(parser):
    parser.add_argument("--model", type=Path, required=True, help="Local Qwen3.5 weights and tokenizer")
    parser.add_argument("--sft", type=Path, required=True, help="Approved text messages in JSONL format")
    parser.add_argument("--output", type=Path, required=True, help="Training workspace and model destination")
    parser.add_argument("--action", choices=("preflight", "run"), default="preflight")
    parser.add_argument("--max-length", type=int, help="Reject longer turns, default to the native model context")
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=8e-6)
    parser.add_argument("--seed", type=int, default=20261119)
    parser.set_defaults(func=run)
    return parser


def run(args):
    from lib.bootstrap.training_validation import full_sft_application

    result = full_sft_application().execute(args.model, args.sft, args.output, action=args.action,
        max_length=args.max_length, gradient_accumulation=args.gradient_accumulation,
        learning_rate=args.learning_rate, seed=args.seed)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] in {"preflight_passed", "completed"} else 2
