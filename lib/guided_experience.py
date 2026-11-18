"""Freeze Google ReasoningBank guidance for a new guided-answer batch."""
import argparse
import json
from pathlib import Path

from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application
from lib.bootstrap.reasoning_bank import reasoning_bank_application
from lib.contexts.workflows.api import learn_guided_experience
from lib.io_utils import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path, help="Completed guided case.json")
    parser.add_argument("--attempt", required=True, type=int, help="One-based attempt number")
    parser.add_argument("--query", required=True, help="Skill needed in the next batch")
    parser.add_argument("--backend", required=True, help="Registered model service")
    parser.add_argument("--model", required=True)
    parser.add_argument("--library", required=True, type=Path, help="Personal experience store")
    parser.add_argument("--embedding-cache", type=Path, help="Existing local FastEmbed cache")
    parser.add_argument("--parent", help="Parent experience edition in the same library")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--context-tokens", type=int, default=131072)
    parser.add_argument("--max-tokens", type=int, default=32768)
    args = parser.parse_args()
    case = json.loads(args.case.read_text(encoding="utf-8"))
    binding = {"backend": args.backend, "model": args.model, "max_output_tokens": args.max_tokens,
               "context_window_tokens": args.context_tokens}
    bank = google_reasoning_bank_application(binding, root=args.library)
    if args.embedding_cache:
        bank.bank = reasoning_bank_application(root=args.library, embedding_cache=args.embedding_cache)
    result = learn_guided_experience(bank, case, args.attempt, args.query, args.parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, result)
    print(json.dumps({"version": result["version"], "status": result["receipt"]["learning_status"]}))


if __name__ == "__main__":
    main()
