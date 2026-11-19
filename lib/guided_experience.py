"""Freeze Google ReasoningBank guidance for a new guided-answer batch."""
import argparse
import json
from pathlib import Path

from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application
from lib.bootstrap.experience_evolution import experience_evolution_application
from lib.bootstrap.backends import backend_application
from lib.bootstrap.reasoning_bank import reasoning_bank_application
from lib.contexts.workflows.api import adopt_final_teacher_edition, learn_guided_experience, recorded_final_proposal
from lib.io_utils import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, help="Guided case.json with the completed model call")
    parser.add_argument("--attempt", type=int, help="One-based attempt number")
    parser.add_argument("--original-delivery", action="store_true", help="Assess the teacher's original delivery before its correction")
    parser.add_argument("--role", choices=["student", "disclosure_teacher", "final_answer_teacher"], default="student")
    parser.add_argument("--prepare-final-assessment", action="store_true", help="Save the actual proposed answer with its bound assessment form, without calling a model")
    parser.add_argument("--assessment", type=Path, help="Completed human assessment or independent verification specification")
    parser.add_argument("--adopt-final-edition", help="Freeze an existing teacher final-answer edition for use in a new run, without calling a model")
    parser.add_argument("--revision", type=int, default=0, help="Teacher draft index, starting at zero")
    parser.add_argument("--query", help="Skill needed in the next batch (student and disclosure roles)")
    parser.add_argument("--backend", help="Registered model service for induction")
    parser.add_argument("--model")
    parser.add_argument("--library", type=Path, help="Personal experience store")
    parser.add_argument("--embedding-cache", type=Path, help="Existing local FastEmbed cache")
    parser.add_argument("--parent", help="Parent experience edition in the same library")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--context-tokens", type=int, default=131072)
    parser.add_argument("--max-tokens", type=int, default=32768)
    args = parser.parse_args()
    if args.adopt_final_edition:
        if not args.library or args.prepare_final_assessment:
            parser.error("Adopting an existing edition requires --library and a separate output from the assessment")
        result = adopt_final_teacher_edition(experience_evolution_application(root=args.library), args.adopt_final_edition)
        atomic_json(args.output, result)
        print(json.dumps({"version": result["version"], "status": "adopted"}))
        return
    if not args.case or args.attempt is None:
        parser.error("--case and --attempt are required to inspect or learn from an answer")
    case = json.loads(args.case.read_text(encoding="utf-8-sig"))
    receipt = assessment = None
    if args.prepare_final_assessment or args.role == "final_answer_teacher":
        if not 1 <= args.attempt <= len(case["attempts"]):
            parser.error("Select the actual teacher delivery round")
        item = case["attempts"][args.attempt - 1]
        stage = "teacher_final_correction" if item.get("previous_teacher_delivery") and not args.original_delivery else "teacher_final_answer"
        receipt = json.loads((args.case.parent / f"{args.attempt:03}-{stage}.json").read_text(encoding="utf8"))
        if args.prepare_final_assessment:
            text, _, bound = recorded_final_proposal(case, args.attempt, receipt)
            atomic_json(args.output, {**bound, "kind":"human", "scope":"full_answer", "outcome":"", "quote":"", "reason":"",
                                     "proposed_answer":text})
            return
        if not args.assessment:
            parser.error("Learning from a teacher's formal answer requires --assessment")
        assessment = json.loads(args.assessment.read_text(encoding="utf-8-sig"))
        if assessment.get("kind") not in ("human", "wrong_final_value"):
            parser.error("The CLI accepts a human assessment or the built-in wrong_final_value replay")
    if not args.library or not args.backend or not args.model:
        parser.error("--library, --backend and --model are required to learn a candidate")
    if args.role != "final_answer_teacher" and not args.query:
        parser.error("--query is required for student or disclosure-teacher experience")
    binding = {"backend": args.backend, "model": args.model, "max_output_tokens": args.max_tokens,
               "context_window_tokens": args.context_tokens}
    if args.role == "final_answer_teacher":
        capabilities = backend_application(Path(__file__).resolve().parents[1]).get_model_info(args.backend, args.model)
        if capabilities.get("reasoning_format") == "llama_cpp_qwen":
            binding["reasoning"] = {"mode": "enabled"}
    bank = google_reasoning_bank_application(binding, root=args.library)
    if args.embedding_cache:
        bank.bank = reasoning_bank_application(root=args.library, embedding_cache=args.embedding_cache)
    result = learn_guided_experience(bank, case, args.attempt, args.query, args.parent,
                                     role=args.role, revision=args.revision, teacher_receipt=receipt,
                                     assessment=assessment)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, result)
    print(json.dumps({"version": result["version"], "status": result["receipt"]["learning_status"]}))


if __name__ == "__main__":
    main()
