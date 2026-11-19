"""Use an observed student attempt or an independently reviewed teacher hint to learn a role-specific strategy."""

import json

from ..domain.guided_final_experience import (
    FINAL_TEACHER_ROLE, final_delivery_observation, final_teacher_edition, text_sha,
)
from ..domain.guided_student_calculations import asserted_arithmetic_errors
from ..domain.guided_disclosure_text import locate_teacher_premise
from ..domain.guided_quantity_scope import storage_quantity_findings, storage_quantity_issue


def observed_attempt(case, number):
    if number < 1 or number > len(case["attempts"]):
        raise ValueError("Select a recorded attempt, numbered from one")
    attempt = case["attempts"][number - 1]
    if attempt.get("solving_role") == "strong_continuation":
        raise ValueError("This round belongs to the continuation model. Select a round answered by the original student.")
    check = attempt["check"]
    coach = attempt.get("coach") or {}
    match = check.get("answer_matches")
    if case.get("gold") is None or not isinstance(match, bool):
        raise ValueError("This attempt has no comparable reference answer")
    arithmetic_errors = attempt.get("arithmetic_errors")
    quantities = attempt.get("quantity_findings")
    # Previously saved reviews can be consumed by a new experience run. Evaluate
    # the recorded prose as well as the marker produced by a current generation.
    refuted_feedback = attempt.get("coach_feedback_check") or asserted_arithmetic_errors(
        coach.get("diagnosis", ""), question=case.get("question", ""), check_result_expressions=True)
    if (match and not arithmetic_errors and not quantities
            and (refuted_feedback or not check.get("format_ok") or coach.get("verdict") != "supported")):
        raise ValueError("Matching the key alone does not establish a successful explanation")
    if "student_request_messages" in attempt:
        # Independent continuations saw only their current program snapshot.
        messages = [dict(m) for m in attempt["student_request_messages"] if m["role"] != "system"]
        messages.append({"role": "assistant", "content": attempt["student"]["content"],
                         "reasoning_content": attempt["student"].get("reasoning_content") or ""})
    else:
        # Original continuous-dialogue records did replay the earlier replies.
        messages = []
        replies = 0
        for entry in case["public_dialogue"]:
            messages.append(dict(entry))
            if entry["role"] == "assistant":
                replies += 1
                if replies == number:
                    break
        if replies != number:
            raise ValueError("The public dialogue does not contain the selected attempt")
    reason = ("The program matched the reference answer; the tutor accepted the explanation."
              if match else "The program found that the final answer differs from the reference.")
    if arithmetic_errors:
        reason = "Exact arithmetic refutes an asserted equality in the student's own response: " + json.dumps(
            arithmetic_errors, ensure_ascii=False)
    if quantities:
        findings = [item for segment in quantities.values() for item in segment]
        quantity_reason = ("The proposed storage count disagrees with the stated conversion rate."
                           if storage_quantity_issue(findings) == "quantity_contradiction"
                           else "The proposed storage count requires a conversion rate that is missing.")
        quantity_reason += " " + json.dumps(quantities, ensure_ascii=False)
        reason = reason + " " + quantity_reason if arithmetic_errors else quantity_reason
    if attempt.get("solving_role"):
        reason += (f" The complete worked solution and answer were supplied. This was a continuation by "
                   f"{attempt['student'].get('model') or attempt['solving_role']}. Assess use of the supplied work.")
    if coach.get("diagnosis") and not refuted_feedback:
        reason += " Tutor observation: " + coach["diagnosis"]
    return {"id": f"{case['id']}-attempt-{number}", "conversation": {"messages": messages},
            "guided_answer_projection_version": 1,
            "review_observation": {"verdict": {"keep": match and not arithmetic_errors and not quantities, "reason": reason}}}


def observed_teacher_disclosure(case, number, revision):
    if not 1 <= number <= len(case["attempts"]):
        raise ValueError("Select an actual student round")
    work = case["attempts"][number - 1].get("disclosure_work", [])
    if not 0 <= revision < len(work):
        raise ValueError("Select a recorded teacher disclosure draft")
    item = work[revision]
    if item.get("proposed_by") == "program":
        raise ValueError("A program nomination is not a teacher-authored or teacher-selected disclosure")
    review = item.get("assessment") or {}
    decision = review.get("decision")
    reason = "Independent assessment of the teacher's hint. " + review.get("reason", "")
    if item.get("issue") == "teacher_needs_a_specific_verbatim_task_premise":
        claimed = (item.get("draft") or {}).get("question_quote")
        if isinstance(claimed, str) and claimed.strip():
            try:
                locate_teacher_premise(case["question"], claimed)
            except ValueError:
                decision = "withhold"
                reason = ("The tutor presented this text as a unique verbatim premise, but it cannot be located "
                          "uniquely in the original question: " + json.dumps(claimed, ensure_ascii=False))
    if item.get("issue") == "teacher_disclosure_arithmetic_contradiction":
        # The author can learn from a refutation before an independent model call.
        errors = asserted_arithmetic_errors(item.get("public_text", ""), question=case["question"])
        if not errors or errors != item.get("arithmetic_errors"):
            raise ValueError("Could not reproduce the recorded disclosure arithmetic error")
        decision = "withhold"
        reason = "Exact arithmetic refutes the proposed teacher hint: " + json.dumps(errors, ensure_ascii=False)
    if item.get("issue") in {"teacher_disclosure_quantity_contradiction", "teacher_disclosure_quantity_conversion_unverified"}:
        quantities = storage_quantity_findings(item.get("public_text", ""), question=case["question"],
            quoted_spans=[q["public_span"] for q in item.get("question_quotations", [])])
        if (not quantities or quantities != item.get("quantity_findings")
                or item["issue"] != "teacher_disclosure_" + storage_quantity_issue(quantities)):
            raise ValueError("Could not reproduce the recorded storage conversion finding")
        decision = "withhold"
        reason = "Storage conversion evidence for the proposed teacher hint: " + json.dumps(quantities, ensure_ascii=False)
    if not item.get("draft") or decision not in ("release", "withhold"):
        raise ValueError("The teacher draft has no substantive independent assessment")
    if decision == "release" and not item.get("validated_citations"):
        raise ValueError("The proposed release has no verified evidence")
    earlier = [a["disclosure"]["text"] for a in case["attempts"][:number - 1] if a.get("disclosure")]
    prompt = {"task": "Give one justified next hint from what the learner can already establish.",
              "problem": case["question"], "earlier_published_hints": earlier}
    draft = item["draft"]
    selection = item.get("verified_operation")
    text = selection["operation"] if selection else draft["bridge"] + "\n" + draft["hint"]
    return {
        "id": f"{case['id']}-disclosure-{number}-{revision}", "guided_disclosure_projection_version": 1,
        "conversation": {"messages": [{"role":"user","content":json.dumps(prompt, ensure_ascii=False)},
                                      {"role":"assistant","content":text}]},
        "review_observation": {"verdict": {"keep": decision == "release", "reason": reason}},
    }


def adopt_final_teacher_edition(experience_library, identifier):
    edition = final_teacher_edition(experience_library.get_version(identifier))
    # Adopt the immutable upstream edition itself. Looking up an ID needs no model or embedding.
    adoption = {"role": FINAL_TEACHER_ROLE, "status": "adopted", "edition_id": identifier,
                "edition_sha256": edition["sha256"], "parent_id": edition["parent_id"], "text_sha256": text_sha(edition["text"])}
    return {"version": identifier, FINAL_TEACHER_ROLE: edition["text"], "final_answer_teacher_adoption": adoption}


def learn_guided_experience(bank, case, attempt, query=None, parent_id=None, *, role="student", revision=0,
                            teacher_receipt=None, assessment=None, replay_verifier=None):
    if role == FINAL_TEACHER_ROLE:
        observation = final_delivery_observation(case, attempt, teacher_receipt, assessment, replay_verifier=replay_verifier)
        if parent_id:
            final_teacher_edition(bank.experience.get_version(parent_id))
        learned = bank.learn("reasoning", observation, parent_id=parent_id)
        return {"version": learned.get("id") or parent_id or "initial", "receipt": {
            "role": role, "learning_status": learned["status"], "parent": parent_id,
            "assessment": observation["final_assessment"], "model_calls": learned["model_request_receipt"]}}
    if role not in ("student", "disclosure_teacher"):
        raise ValueError("Unknown guided experience role")
    observation = (observed_teacher_disclosure(case, attempt, revision)
                   if role == "disclosure_teacher" else observed_attempt(case, attempt))
    learned = bank.learn("reasoning", observation, parent_id=parent_id)
    identifier = parent_id if learned.get("status") == "no_update" else learned["id"]
    receipt = {"source_case": case["id"], "attempt": attempt, "parent": parent_id,
               "learning_status": learned["status"], "source_binding": case["binding"],
               "model_calls": learned["model_request_receipt"]}
    if role == "disclosure_teacher":
        receipt.update(role=role, disclosure_revision=revision, outcome_scope="quality_of_the_teacher_hint")
    elif case["attempts"][attempt - 1].get("solving_role") == "student":
        receipt.update(role=role, model=case["attempts"][attempt - 1]["student"].get("model"),
                       outcome_scope="student_continuation_after_full_disclosure", independent_solution=False)
    if not identifier:
        return {"version": "initial", role: "", "receipt": receipt}
    indexed = bank.bank.index_edition(identifier)
    if indexed["status"] != "indexed":
        raise ValueError(f"Could not prepare the learned rules for retrieval: {indexed['status']}")
    selected = bank.bank.retrieve("reasoning", query, edition_ids=[identifier])
    if selected["status"] != "retrieved" or not selected["matches"]:
        raise ValueError(f"No guidance retrieved for this task: {selected['status']}")
    rules = []
    for match in selected["matches"]:
        entry = match["entry"]
        rules.append("\n".join(filter(None, [entry["when"], entry["principle"], entry["avoid"]])))
    return {"version": identifier, role: "\n\n".join(rules),
            "receipt": {**receipt, "selection": selected["receipt"]}}
