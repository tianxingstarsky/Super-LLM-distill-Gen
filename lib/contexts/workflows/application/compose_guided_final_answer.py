"""Let the private teacher deliver a user-facing answer from the accepted student derivation."""

import hashlib
import json

from ..domain.guided_answers import assemble, read_teacher_submission
from ..domain.guided_final_experience import adopted_final_teacher_text
from ..domain.guided_teacher_delivery import TEACHER_FINAL_VERSIONS, inspect_teacher_delivery, requested_delivery
from lib.prompts.workflow_guided_answers import TEACHER_FINAL_ANSWER, TEACHER_FINAL_NECESSITY, VERSION, teacher_final_schema


def compose_guided_final_answer(request, source, index, segments, verified_conclusion, *, reasoning_trace=None,
                                protocol_version=VERSION, final_teacher_experience=None):
    steps = {s["id"]: s["text"] for s in segments}
    form = requested_delivery(source)
    system = TEACHER_FINAL_ANSWER
    if protocol_version in TEACHER_FINAL_VERSIONS and protocol_version != "4.4.0":
        system += "\n\n" + TEACHER_FINAL_NECESSITY
    elif protocol_version != "4.4.0":
        raise ValueError("unsupported teacher final-answer protocol")
    teacher_instruction = adopted_final_teacher_text(final_teacher_experience or {}, protocol_version)
    if teacher_instruction:
        system += "\n\n" + teacher_instruction
    if source.get("task_system"):
        system += "\n\nOriginal task system instruction:\n" + source["task_system"]
    materials = {
        "source": "workflow_verified_task_evidence",
        "reviewed_student_steps": [{"id": sid, "text": text} for sid, text in steps.items()],
        "reviewed_student_conclusion": verified_conclusion,
        "independent_reference_key": source["gold"],
        "requested_delivery": form,
    }
    if source.get("direct_answer_syntax"):
        materials["requested_result_syntax"] = source["direct_answer_syntax"]
    history_digest = None
    if reasoning_trace is not None:
        history_digest = hashlib.sha256(reasoning_trace.encode("utf-8")).hexdigest()
        if reasoning_trace == assemble(segments).rstrip():
            history = {"same_text_as": "joined_reviewed_student_steps", "rstrip": True}
        else:
            history = {"text": reasoning_trace}
        materials["solving_history"] = {**history, "sha256": history_digest,
                                        "purpose": "chronological context; earlier guesses and hints are not established facts"}
    response = request("teacher_final_answer", index, [
        {"role": "system", "content": system},
        {"role": "user", "content": source["question"]},
        {"role": "tool", "content": json.dumps(materials, ensure_ascii=False)},
    ], teacher_final_schema(steps))
    proposal = None
    if response["finish_reason"] == "stop":
        try:
            proposal = read_teacher_submission(response)
        except (ValueError, TypeError):
            pass
    check, public = inspect_teacher_delivery(
        proposal, source, steps, form, protect_evidence_ids=protocol_version in {"4.7.0", "4.7.1"}
    )
    if response["finish_reason"] != "stop":
        check["issue"] = "teacher_final_incomplete"
    provenance = {"author": "teacher", "model": response.get("model"), "attempt": index,
                  "receipt": f"{index:03}-teacher_final_answer.json", "requested_delivery": form,
                  "evidence_ids": proposal["evidence_ids"] if check["format_ok"] else [],
                  "verification": "public_conclusion_key_check_and_reviewed_student_evidence",
                  "independent_explanation_review": False,
                  **({"solving_history_sha256": history_digest} if history_digest else {})}
    return {"response": response, "check": check, "public_answer": public, "provenance": provenance}
