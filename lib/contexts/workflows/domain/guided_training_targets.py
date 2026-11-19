"""Project accepted solver evidence into a single Qwen assistant target per task."""

import json

from .guided_answers import assemble, contains_chat_control, read_teacher_submission
from .guided_student_calculations import asserted_arithmetic_errors
from .guided_teaching_steps import certified_complete_disclosure


GUIDED_TARGET_FILE = "training-targets.jsonl"
GUIDED_TARGET_SCHEMA = "guided-training-target/v1"
GUIDED_TARGET_VERSIONS = frozenset({"4.9.1"})


class GuidedTargetHeld(ValueError):
    """The case remains archived but lacks a usable text training target."""


def _reviewed_segments(case):
    attempts = case["attempts"]
    last = attempts[-1]
    segments = case["retained_segments"]
    ids = [segment["id"] for segment in segments]
    version = case["draft_versions"][-1]
    if not ids:
        raise GuidedTargetHeld("missing_reviewed_derivation")
    submission = read_teacher_submission(last["coach_response"])
    if last.get("coach_feedback_check") or asserted_arithmetic_errors(
            submission.get("diagnosis", ""), question=case["question"], check_result_expressions=True):
        raise GuidedTargetHeld("refuted_teacher_feedback")
    if (submission.get("decision") != "supported"
            or len(set(ids)) != len(ids) or ids != version["retained_after"]
            or ids != version["submitted_ids"] or last["coach"]["verdict"] != "supported"
            or last["coach"]["verified_prefix_count"] != len(segments)):
        raise ValueError("guided training requires a completely reviewed derivation")
    spans = []
    for piece in segments:
        index = piece["attempt"]
        if type(index) is not int or not 1 <= index <= len(attempts):
            raise ValueError("invalid reviewed student attempt")
        attempt = attempts[index - 1]
        start, end = piece["start"], piece["end"]
        draft = attempt["student"]["content"]
        if (piece["author"] != "student" or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(draft) or draft[start:end] != piece["text"]):
            raise ValueError("reviewed segment differs from the solver's original draft")
        role = attempt.get("solving_role", "student")
        spans.append({"id": piece["id"], "receipt": f"{index:03}-{role}.json", "span": [start, end]})
    return assemble(segments).rstrip(), spans


def _correction_prompt(question, attempts, chinese):
    # This is an offline exercise. Attribute the quoted observations inside
    # its prompt, without fabricating live user, assistant or tool messages.
    history = []
    for attempt in attempts:
        turn = {"attempt": attempt["attempt"], "student_draft": attempt["student"]["content"]}
        if attempt.get("formal_answer"):
            turn["student_delivery"] = attempt["formal_answer"]["response"]["content"]
        if attempt.get("disclosure"):
            turn["published_tutor_guidance"] = attempt["disclosure"]["text"]
        history.append(turn)
    instruction = ("请根据原题和以下历史材料修正作答。学生旧稿可能有误；提示来自辅导教师。"
                   if chinese else "Correct the solution to the original task using the recorded drafts "
                   "and guidance below. Earlier student work may contain errors.")
    return question + "\n\n" + instruction + "\n" + json.dumps(history, ensure_ascii=False, indent=2)


def guided_training_target(case, case_archive, trusted_solution_receipts):
    """Use the keyed decision and source spans; never supervise a serialized tutoring transcript."""
    attempts = case["attempts"]
    last = attempts[-1]
    if (case["status"] != "accepted" or case["verification"]["answer_key_check"] is not True
            or [a["attempt"] for a in attempts] != list(range(1, len(attempts) + 1))):
        raise ValueError("guided training requires an accepted keyed case with ordered attempts")
    question = case["question"]
    answer = case["messages"][-1]["content"]
    independent = len(attempts) == 1 and not case["disclosures"] and not case.get("continuation_provenance")
    if independent:
        request = last["student_request_messages"]
        if ([m["role"] for m in request] != ["system", "user"] or request[-1]["content"] != question):
            raise ValueError("independent training requires the original question without tutor material")
    provenance = {"schema": GUIDED_TARGET_SCHEMA, "case_archive": case_archive,
                  "case_binding": case["binding"], "source_sha256": case["source_sha256"]}
    target = {"role": "assistant", "content": answer}
    if case["training_route"] == "nonthinking_direct":
        if not independent or answer != last["student"]["content"] or last["check"]["answer_matches"] is not True:
            raise ValueError("direct answer differs from the unaided student reply")
        objective = "independent_answer"
        provenance.update(answer_author="student", answer_receipt="001-student.json")
    else:
        formal = last["formal_answer"]
        origin = case["final_answer_provenance"]
        role = last.get("solving_role", "student")
        if (origin != formal["provenance"] or origin["author"] != role or not origin["model"]
                or formal["response"].get("model") != origin["model"] or last["student"].get("model") != origin["model"]
                or formal["response"]["content"] != answer or formal["check"]["format_ok"] is not True
                or formal["check"]["answer_matches"] is not True):
            raise ValueError("guided training final answer differs from the checked solver delivery")
        provenance.update(answer_author=role, answer_model=origin["model"], answer_receipt=origin["receipt"],
                          independent_explanation_review=origin["independent_explanation_review"])
        derivation, spans = _reviewed_segments(case)
        full = [a["received_complete_disclosure"] for a in attempts if "received_complete_disclosure" in a]
        continuation = case.get("continuation_provenance")
        if full or continuation:
            reference = certified_complete_disclosure(case, trusted_solution_receipts)
            if (not reference or full != [reference] or not continuation
                    or continuation.get("reference_answer_disclosed") is not True
                    or continuation["complete_disclosure_id"] != reference["id"]):
                raise ValueError("teacher demonstration requires the bound verified reference")
            objective = "teacher_demonstration"
            target["reasoning_content"] = reference["worked_solution"]
            provenance.update(reasoning_origin="verified_source_solution", reference_receipt=reference["id"],
                              reference_author=case["source"].get("reasoning_author"))
        else:
            objective = "independent_solution" if independent else "guided_correction"
            target["reasoning_content"] = derivation
            provenance.update(reasoning_origin="reviewed_student_spans", reasoning_spans=spans)
            if not independent:
                language = str(case["source"].get("language") or "").lower()
                chinese = language.startswith("zh") if language else any("\u4e00" <= ch <= "\u9fff" for ch in question)
                question = _correction_prompt(question, attempts[:-1], chinese)
    if any(contains_chat_control(text) for text in (question, target.get("reasoning_content", ""), answer)):
        raise GuidedTargetHeld("chat_delimiter_in_training_text")
    messages = [{"role": "system", "content": case["task_system"]}] if case.get("task_system") else []
    return {"id": case["id"], "status": "eligible",
            "messages": [*messages, {"role": "user", "content": question}, target],
            "guided_training_target": {**provenance, "objective": objective, "guidance_received": not independent,
                                       "independent_solution": objective == "independent_solution"}}
