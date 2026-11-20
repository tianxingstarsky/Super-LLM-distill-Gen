"""Collect observed teacher-to-student outcomes and freeze advice for a new guided run."""

import argparse
import json
from pathlib import Path

from lib.bootstrap.experience_evolution import experience_evolution_application
from lib.bootstrap.guided_teacher_feedback import local_teacher_induction, teacher_feedback_store, verify_teacher_application
from lib.contexts.workflows.api import freeze_teacher_experience, learn_teacher_feedback
from lib.io_utils import atomic_json


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="command", required=True)
    observe = cmds.add_parser("observe", help="Authenticate completed source cases and record the next learner outcome")
    observe.add_argument("--run", type=Path, required=True)
    observe.add_argument("--input", type=Path, required=True, help="Original JSONL matching the frozen input digest")
    observe.add_argument("--case-id", action="append", default=[])
    observe.add_argument("--source-run", type=Path, action="append", default=[],
                         help="Original parent run for authenticating a replayed receipt (repeatable)")
    status = cmds.add_parser("status", help="Count attributed evidence without running a model")
    learn = cmds.add_parser("learn", help="Use local Google ReasoningBank induction once per batch of new eligible observations")
    provider = learn.add_mutually_exclusive_group(required=True)
    provider.add_argument("--local-url", help="Loopback OpenAI-compatible llama.cpp endpoint including /v1")
    provider.add_argument("--backend", help="Registered loopback model endpoint")
    learn.add_argument("--model", required=True)
    learn.add_argument("--runtime-root", type=Path, default=Path(__file__).resolve().parents[1],
                       help="Checkout that owns the active local model slot pool")
    learn.add_argument("--parent", help="Earlier disclosure-teacher edition from this library")
    learn.add_argument("--reconsider", help="Reconsider a blocked older induction only after the observable action projection changed")
    learn.add_argument("--min-new", type=int, default=8)
    learn.add_argument("--batch-size", type=int, default=16)
    learn.add_argument("--context-tokens", type=int, default=98304)
    learn.add_argument("--max-tokens", type=int, default=32768)
    adopt = cmds.add_parser("adopt", help="Prepare a candidate edition for a separate new run; use an earlier edition to roll back")
    adopt.add_argument("--edition", required=True, help="Candidate or prior edition ID, or initial to remove learned teacher advice")
    adopt.add_argument("--base", type=Path, help="Frozen base JSON whose other role settings must be preserved")
    adopt.add_argument("--trial", action="store_true", help="Freeze unproven transferable advice only for a separate comparison run")
    verify = cmds.add_parser("verify", help="Confirm that a real new teacher request used the frozen advice")
    verify.add_argument("--run", required=True, type=Path)
    verify.add_argument("--experience", required=True, type=Path)
    verify.add_argument("--case-id", action="append", default=[], help="Can also select a case that has not yet finalized")
    for command in (observe, status, learn, adopt):
        command.add_argument("--library", required=True, type=Path)
    for command in (observe, status, learn, verify, adopt):
        command.add_argument("--output", type=Path, required=command in (learn, adopt))
    args = parser.parse_args()
    if args.command == "verify":
        result = verify_teacher_application(args.run, read(args.experience), args.case_id)
    elif args.command == "adopt":
        result = freeze_teacher_experience(teacher_feedback_store(args.library), experience_evolution_application(root=args.library),
            args.edition, read(args.base) if args.base else None, trial=args.trial)
    else:
        store = teacher_feedback_store(args.library)
        if args.command == "observe":
            result = store.observe(args.run, args.input, args.case_id, args.source_run)
        elif args.command == "status":
            result = store.status()
        else:
            if args.output.exists() and read(args.output).get("status") in {"candidate", "no_update", "candidate_source_overlap"}:
                parser.error("use a separate output to preserve the earlier learning receipt")
            def get_bank():
                return local_teacher_induction(args.library, args.runtime_root, args.model, local_url=args.local_url,
                    backend=args.backend, context_tokens=args.context_tokens, max_tokens=args.max_tokens)
            result = learn_teacher_feedback(store, experience_evolution_application(root=args.library), get_bank,
                lambda result: atomic_json(args.output, result), min_new=args.min_new, batch_size=args.batch_size,
                parent=args.parent, reconsider=args.reconsider)
    if args.output:
        if args.command == "adopt" and args.output.exists() and read(args.output) != result:
            raise ValueError("choose a new output file to preserve the frozen adoption")
        atomic_json(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key not in {"events", "disclosure_teacher"}},
                     ensure_ascii=False) if args.output else json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
