"""Project accepted solver evidence into a single Qwen assistant target per task."""

import hashlib
import json

from .guided_answers import assemble, contains_chat_control, read_teacher_submission
from .guided_student_calculations import asserted_arithmetic_errors
from .guided_teaching_steps import certified_complete_disclosure, certified_rule_prefixes


GUIDED_TARGET_FILE = "training-targets.jsonl"
GUIDED_TARGET_SCHEMA = "guided-training-target/v1"
GUIDED_TARGET_VERSIONS = frozenset({"4.9.1", "4.10.0", "4.10.1", "4.10.2"})


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


def _strong_handoff_prompt(case, reference):
    weak, strong = case["attempts"][-2:]
    messages = strong["student_request_messages"]
    if (weak.get("solving_role") != "student" or weak["student_request_messages"] != messages
            or [m["role"] for m in messages] != ["system", "user", "tool"] or messages[1]["content"] != case["question"]):
        raise ValueError("strong continuation differs from the disclosed student request")
    prefix = "Continue the solution from this current view.\n"
    if not messages[-1]["content"].startswith(prefix):
        raise ValueError("strong continuation lacks the complete public view")
    view = json.loads(messages[-1]["content"][len(prefix):])
    if view.get("complete_disclosure") != reference:
        raise ValueError("strong continuation did not receive the attested solution")
    view["complete_disclosure"] = {key: reference[key] for key in ("worked_solution", "answer")}
    chinese = str(case["source"].get("language") or "").lower().startswith("zh") or any(
        "\u4e00" <= c <= "\u9fff" for c in case["question"])
    label = "接手作答时收到的公开材料" if chinese else "Public guidance supplied to the continuation solver"
    return case["question"] + "\n\n" + label + "\n" + json.dumps(view, ensure_ascii=False, indent=2)


def _calculation_target(case, steps, provenance):
    course = case["calculation_tutoring"]
    delivery = course["final_delivery"]
    if not steps:
        raise ValueError("verified calculation training requires replayed solver actions")
    # Make the offline exercise self-contained. Supplied operations are visible context.
    # Numeric results in the target are calculator executions, including self-proposed expressions.
    moves = []
    for action in course["actions"]:
        move = json.loads(action["request_messages"][-1]["content"])["target"]
        moves.append(move["quantity"] if "operation" not in move
                     else f"{move['quantity']}: {move['operation']}")
    question = case["question"]
    chinese = str(case.get("source", {}).get("language", "")).lower().startswith("zh") or any(
        "\u4e00" <= char <= "\u9fff" for char in question)
    title = "辅导时提供的计算目标和提示" if chinese else "Calculation goals and guidance supplied during tutoring"
    question += "\n\n" + title + "\n" + "\n".join(dict.fromkeys(moves))
    suggested = sum(s["operation_disclosed"] for s in steps)
    reader = "strong_continuation" if delivery["role"] == "verified_strong_answer" else "student"
    readout_only = delivery["public_answer"] == delivery["response"]["content"].strip()
    target = {"role": "assistant", "reasoning_content": "\n".join(s["text"] for s in steps),
              "content": delivery["public_answer"]}
    messages = [{"role": "system", "content": case["task_system"]}] if case.get("task_system") else []
    if any(contains_chat_control(t) for t in (question, target["reasoning_content"], target["content"])):
        raise GuidedTargetHeld("chat_delimiter_in_training_text")
    return {"id": case["id"], "status": "eligible",
            "messages": [*messages, {"role": "user", "content": question}, target],
            "guided_training_target": {**provenance,
                "objective": "calculation_demonstration" if suggested == len(steps) else "assisted_calculation",
                "reasoning_origin": "verified_tutoring_operations_and_calculator",
                "evidence_basis": "externally_attested_plan_and_exact_execution",
                "guidance_received": True, "independent_solution": False,
                "answer_author": reader, "answer_model": delivery["response"]["model"],
                "answer_assembly": f"{reader}_readout" if readout_only else f"verified_steps_with_{reader}_readout",
                "answer_receipt": delivery["receipt"], "plan_receipt": course["receipt_id"],
                "student_proposed_operations": len(steps) - suggested, "supplied_operations": suggested,
                "student_used_disclosed_operations": sum(s["operation_origin"] == "student_used_disclosed_operation" for s in steps),
                "demonstrated_operations": sum(s["operation_origin"] == "verified_teaching_demonstration" for s in steps),
                "operations": [{k: s[k] for k in ("id", "student_receipt", "operation_disclosed", "operation_origin", "value_source")}
                               for s in steps]}}


def guided_training_target(case, case_archive, trusted_solution_receipts, *, calculation_steps=None):
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
    if "calculation_tutoring" in case:
        return _calculation_target(case, calculation_steps, provenance)
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
        strong_handoff = case.get("protocol") == "guided-draft-snapshot/v4.10.2" and role == "strong_continuation"
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
        staged = [h for h in case["disclosures"] if h.get("source") == "verified_solution_prefix"]
        reference = None
        if staged or full or continuation:
            reference = certified_complete_disclosure(case, trusted_solution_receipts)
        if staged:
            expected = certified_rule_prefixes(question, reference)
            if (case.get("protocol") not in {"guided-draft-snapshot/v4.10.1", "guided-draft-snapshot/v4.10.2"}
                    or len(staged) > len(expected)
                    or any(any(actual.get(k) != v for k, v in planned.items())
                           for actual, planned in zip(staged, expected))):
                raise ValueError("published proof prefix differs from its attested order and source")
            provenance.update(reference_receipt=reference["id"], published_proof_spans=[s["source_span"] for s in staged])
        if full or continuation or strong_handoff:
            if (not reference or full != [reference] or not continuation
                    or continuation.get("reference_answer_disclosed") is not True
                    or continuation["complete_disclosure_id"] != reference["id"]):
                raise ValueError("teacher demonstration requires the bound verified reference")
            if strong_handoff:
                question = _strong_handoff_prompt(case, reference)
                current = [s for s in case["retained_segments"] if s["attempt"] == last["attempt"]]
                derivation = assemble(current).rstrip()
                if not derivation:
                    raise GuidedTargetHeld("missing_reviewed_derivation")
                solver_receipt = f"{last['attempt']:03}-strong_continuation.json"
                if (origin["solver_receipt"] != solver_receipt or case["messages"][-2] != last["student_request_messages"][-1]
                        or case["messages"][-1].get("reasoning_content") != derivation
                        or origin["solving_history_sha256"] != hashlib.sha256(derivation.encode("utf-8")).hexdigest()):
                    raise ValueError("strong readout differs from the reviewed continuation and its public context")
                objective = "full_disclosure_strong_continuation"
                target["reasoning_content"] = derivation
                provenance.update(reasoning_origin="reviewed_continuation_spans", reference_receipt=reference["id"],
                                  reasoning_spans=[s for s in spans if s["receipt"] == solver_receipt],
                                  solver_input_receipt=solver_receipt)
            if staged or strong_handoff:
                if " ".join(derivation.split()) == " ".join(reference["worked_solution"].split()):
                    raise GuidedTargetHeld("solver_only_repeated_the_disclosed_proof")
                provenance["reference_answer_disclosed"] = True
            else:
                objective = "teacher_demonstration"
                target["reasoning_content"] = reference["worked_solution"]
                provenance.update(reasoning_origin="verified_source_solution", reference_receipt=reference["id"],
                                  reference_author=case["source"].get("reasoning_author"))
        if not strong_handoff and (staged or not (full or continuation)):
            objective = "independent_solution" if independent else "guided_correction"
            target["reasoning_content"] = derivation
            provenance.update(reasoning_origin="reviewed_student_spans", reasoning_spans=spans)
            if not independent:
                language = str(case["source"].get("language") or "").lower()
                chinese = language.startswith("zh") if language else any("\u4e00" <= ch <= "\u9fff" for ch in question)
                question = _correction_prompt(question, attempts[:-1], chinese)
                if full:
                    label = "作答前已给出的完整辅导内容" if chinese else "Complete guidance seen before answering"
                    question += "\n\n" + label + "\n" + reference["worked_solution"] + "\n" + reference["answer"]
    if any(contains_chat_control(text) for text in (question, target.get("reasoning_content", ""), answer)):
        raise GuidedTargetHeld("chat_delimiter_in_training_text")
    messages = [{"role": "system", "content": case["task_system"]}] if case.get("task_system") else []
    if case.get("protocol") in {"guided-draft-snapshot/v4.10.0", "guided-draft-snapshot/v4.10.1", "guided-draft-snapshot/v4.10.2"}:
        provenance["evidence_basis"] = {"independent_answer": "independent_answer_key",
                                        "teacher_demonstration": "verified_source_solution"}.get(
                                            objective, "answer_key_and_tutor_review")
    return {"id": case["id"], "status": "eligible",
            "messages": [*messages, {"role": "user", "content": question}, target],
            "guided_training_target": {**provenance, "objective": objective, "guidance_received": not independent,
                                       "independent_solution": objective == "independent_solution"}}
