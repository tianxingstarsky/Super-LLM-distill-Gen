"""Generate a visible solution, check it and request a directed revision when needed."""

import json
from functools import partial

from .compose_guided_final_answer import compose_guided_final_answer
from .review_guided_disclosure import (
    author_next_disclosure, eligible_teaching_steps, review_next_disclosure, review_or_recommend_disclosure,
)
from .repair_guided_student_evidence import check_or_repair_student_findings
from ..domain.guided_student_evidence import checked_student_approaches
from ..domain.guided_teacher_delivery import TEACHER_FINAL_VERSIONS, uses_direct_first

from ..domain.guided_answers import (
    assemble,
    check_direct_answer,
    checked_progress,
    checked_truncated_progress,
    contains_chat_control,
    disclosure_bank,
    draft_fragment,
    formal_delivery_hint,
    inspect_answer,
    inspect_formal_answer,
    prior_clue_evidence,
    rooted_prefix_count,
    shared_findings,
    student_reasoning_trace,
    validated_review,
)
from lib.prompts.workflow_guided_answers import (
    ANCHORED_REVIEW,
    COACH,
    DIRECT_STUDENT,
    GROUNDED_STUDENT_REVIEW,
    ORDERED_STUDENT_EVIDENCE,
    REVIEW_TASK_ANALYSIS,
    RESULT_ONLY_DELIVERY,
    STUDENT_TASK_ANALYSIS,
    WORKED_ANSWER_DELIVERY,
    SINGLE_DECISION_REVIEW,
    review_schema,
    SOURCE_COMPLETION_STUDENT,
    STUDENT,
    VERSION,
)


class GenerateGuidedAnswer:
    """The injected request port records each native request and its actual response."""

    def __init__(self, request):
        self.request = request

    def run(self, source, experience, max_attempts, *, protocol_version=VERSION, first_pass_only=False,
            until_correct=False):
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        question, gold = source["question"], source["gold"]
        skip_acquired = protocol_version != "3.0.0"
        teacher_delivery = protocol_version in TEACHER_FINAL_VERSIONS
        task_analysis = teacher_delivery or protocol_version == "4.3.0"
        ordered_evidence = task_analysis or protocol_version == "4.2.1"
        paired = ordered_evidence or protocol_version == "4.2.0"
        show_outcome = paired or protocol_version == "4.1.1"
        allow_catalog = show_outcome or protocol_version == "4.1.0"
        grounded = allow_catalog or protocol_version == "4.0.0"
        authored_hints = grounded or protocol_version == "3.9.0"
        if until_correct and not authored_hints:
            raise ValueError("until_correct requires a guided run with authored disclosure")
        review_disclosures = authored_hints or protocol_version == "3.8.0"
        assess_hint = (partial(author_next_disclosure, teacher_experience=experience.get("disclosure_teacher", ""),
                               grounded=grounded, allow_catalog=allow_catalog, show_outcome=show_outcome)
                       if authored_hints else review_next_disclosure)
        tried_verified_steps = set()
        if paired:
            assess_hint = partial(review_or_recommend_disclosure, teacher_experience=experience.get("disclosure_teacher", ""),
                                  tried=tried_verified_steps, task_analysis=task_analysis)
        record_guided_reasoning = review_disclosures or protocol_version == "3.7.0"
        direct_protocol = record_guided_reasoning or protocol_version == "3.6.0"
        truncated_progress = direct_protocol or protocol_version == "3.5.0"
        marked_finals = truncated_progress or protocol_version == "3.4.0"
        single_decision = marked_finals or protocol_version == "3.3.0"
        anchored = single_decision or protocol_version == "3.2.0"
        protocol = {"3.0.0": "v3", "3.1.0": "v3.1", "3.2.0": "v3.2", "3.3.0": "v3.3", "3.4.0": "v3.4",
                    "3.5.0": "v3.5", "3.6.0": "v3.6", "3.7.0": "v3.7", "3.8.0": "v3.8", "3.9.0": "v3.9",
                    "4.0.0": "v4.0", "4.1.0": "v4.1", "4.1.1": "v4.1.1", "4.2.0": "v4.2",
                    "4.2.1": "v4.2.1", "4.3.0": "v4.3", "4.4.0": "v4.4", "4.4.1": "v4.4.1"}[protocol_version]
        tutor_instruction = SINGLE_DECISION_REVIEW if single_decision else ANCHORED_REVIEW if anchored else COACH
        if grounded:
            tutor_instruction += "\n\n" + GROUNDED_STUDENT_REVIEW
        if ordered_evidence:
            tutor_instruction += "\n\n" + ORDERED_STUDENT_EVIDENCE
        if task_analysis:
            tutor_instruction += "\n\n" + REVIEW_TASK_ANALYSIS
        mode = "answer_key" if gold is not None else "source_reasoning_only"
        attempts = []
        status = "quarantine"
        issue = "attempt_limit"
        student_experience = "\n\n" + experience["student"] if experience.get("student") else ""
        task_system = source.get("task_system") if record_guided_reasoning else None
        task_messages = [{"role": "system", "content": task_system}] if task_system else []
        task_messages.append({"role": "user", "content": question})
        require_explanation = task_analysis and source.get("answer_requirement") == "worked_solution"
        final_task = task_messages
        if task_analysis and source.get("answer_requirement"):
            delivery = WORKED_ANSWER_DELIVERY if require_explanation else RESULT_ONLY_DELIVERY
            delivery_system = (task_system + "\n\n" if task_system else "") + delivery
            final_task = [{"role": "system", "content": delivery_system}, task_messages[-1]]
        task_suffix = "\n\n" + task_system if task_system else ""
        system = STUDENT if gold is not None else SOURCE_COMPLETION_STUDENT
        if task_analysis:
            system += "\n\n" + STUDENT_TASK_ANALYSIS
        system += student_experience + task_suffix
        initial_messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]
        if gold is None:
            material = {
                "source_type": "previously_published_source_reasoning",
                "author": source["source"].get("reasoning_author", "source_model"),
                "content": source["source_reasoning"],
            }
            initial_messages.append(
                {
                    "role": "tool",
                    "content": "The supplied source author left this reasoning without a final answer. "
                    "Use it as attributed evidence, check its claims, and provide the missing formal answer.\n"
                    + json.dumps(material, ensure_ascii=False),
                }
            )
        bank = disclosure_bank(source) if source["disclosure_hints"] or not authored_hints else []
        disclosed = []
        clue_ledger = []
        active = []
        versions = []
        findings = {}
        approaches = {}
        final_text = ""
        final_provenance = None
        direct_match = None
        training_reasoning = ""
        formal_history = []
        greatest_verified_prefix, stalled_rounds = 0, 0
        seen_findings = set()
        public = list(initial_messages)
        attempt_limit = max_attempts
        while len(attempts) < attempt_limit:
            index = len(attempts) + 1
            messages = list(initial_messages)
            direct_first = index == 1 and direct_protocol and uses_direct_first(source, protocol_version)
            if direct_first:
                messages[0] = {"role": "system", "content": DIRECT_STUDENT + student_experience + task_suffix}
            if disclosed:
                view = {
                    "retained_solution": assemble(active),
                    "confirmed_findings_from_your_own_work": [
                        f["text"] for f in findings.values()
                    ],
                    "previously_disclosed_clues": [d["text"] for d in disclosed[:-1]],
                    "new_clue": disclosed[-1]["text"],
                }
                attributed = [
                    {"student_excerpt": f["text"], "reviewed_meaning": f["meaning"]}
                    for f in findings.values()
                    if f.get("check_id")
                ]
                if attributed:
                    view["confirmed_meanings"] = attributed
                if approaches:
                    view["supported_approaches_from_your_own_work"] = [item["text"] for item in approaches.values()]
                if attempts and "formal_answer" in attempts[-1]:
                    view["your_last_formal_answer"] = attempts[-1]["formal_answer"]["response"]["content"]
                    view["your_previous_conclusion"] = attempts[-1]["check"].get("answer_surface")
                messages.append(
                    {
                        "role": "tool",
                        "content": "Continue the solution from this current view.\n"
                        + json.dumps(view, ensure_ascii=False),
                    }
                )
                if skip_acquired:
                    public.append(messages[-1])
            student = self.request("student", index, messages, None)
            attempt = dict(
                attempt=index,
                student=student,
                student_request_messages=messages,
                coach=None,
                hint_to_student=None,
            )
            attempts.append(attempt)
            if (
                student["finish_reason"] != "stop"
                or student.get("reasoning_content")
                or not student["content"].strip()
            ):
                issue = "student_incomplete_or_native_thinking"
                attempt["check"] = dict(
                    format_ok=False, answer=None, answer_matches=None, issue=issue
                )
                if truncated_progress and student["finish_reason"] == "length" and not student.get("reasoning_content"):
                    certified = checked_truncated_progress(
                        source.get("progress_checks", []), student["content"], index
                    )
                    for key, item in certified.items():
                        item["source_attempt"] = index
                        item["previous_disclosure_ids"] = [d["id"] for d in disclosed]
                        if key in findings:
                            item["previously_confirmed_segment_id"] = findings[key]["segment_id"]
                        else:
                            findings[key] = item
                    attempt["program_confirmed"] = list(certified.values())
                break
            suffix = student["content"]
            public.append({"role": "assistant", "content": suffix})
            direct = check_direct_answer(suffix, gold, include_mismatch=grounded) if direct_first else None
            direct_match = direct if direct and direct["answer_matches"] else None
            if direct:
                new_segments, answer_line = [], suffix
            else:
                new_segments, answer_line = draft_fragment(suffix, index, preserve_list_items=single_decision,
                                                           marked_finals=marked_finals)
            for piece in new_segments:
                piece["reuses_student_findings"] = [
                    sid
                    for sid, f in findings.items()
                    if f["text"].strip() == piece["text"].strip()
                ]
            submitted = [*active, *new_segments]
            if show_outcome:
                attempt["derivation_available"] = bool(active or new_segments and (answer_line or len(new_segments) > 1))
            text = assemble(submitted, answer_line)
            final_text = text
            check = direct or inspect_answer(text, gold, parse_count_sentence=skip_acquired,
                                                   marked_finals=marked_finals, question=question)
            if gold is None and not any(
                tag in text.lower()
                for tag in ("<think>", "</think>", "<|im_start|>", "<|im_end|>")
            ):
                check = dict(
                    format_ok=True, answer=text.strip(), answer_matches=None, issue=None
                )
            prior_texts = {piece["text"].strip() for piece in active}
            repeated = next(
                (
                    pos
                    for pos, piece in enumerate(new_segments)
                    if piece["text"].strip() in prior_texts
                ),
                None,
            )
            if repeated is not None:
                check.update(format_ok=False, issue="repeated_retained_segment")
            attempt["check"] = check
            attempt["candidate_content"] = text
            version = dict(
                attempt=index,
                retained_before=[s["id"] for s in active],
                student_suffix=new_segments,
                final_line=answer_line,
                submitted_ids=[s["id"] for s in submitted],
                retained_after=[],
                withdrawn=[],
                first_unresolved=None,
            )
            versions.append(version)
            if direct_match:
                status, issue = "accepted", None
                break
            if first_pass_only and direct_first:
                # The request port has persisted the original draft. A later full pass
                # reuses those exact bytes and starts tutoring without redrawing it.
                return None
            certified = (
                checked_progress(
                    source.get("progress_checks", []), new_segments, suffix
                )
                if anchored
                else {}
            )
            # Keep the original discovery when a later response repeats it.
            additions = {
                key: item for key, item in certified.items() if key not in findings
            }
            for item in additions.values():
                item["previous_disclosure_ids"] = [d["id"] for d in disclosed]
            if anchored:
                version["program_confirmed"] = list(additions.values())
            material = {
                "question": question,
                **({"task_system": task_system} if task_system else {}),
                "mode": mode,
                "answer_key": gold,
                "attributed_source_reasoning": source["source_reasoning"],
                "program_check": {
                    k: check[k] for k in ("format_ok", "answer_matches", "issue")
                },
                "retained_before": [{"id": s["id"], "text": s["text"]} for s in active],
                "current_student_continuation": {
                    "segments": [
                        {"id": s["id"], "text": s["text"]} for s in new_segments
                    ],
                    "final_line": answer_line,
                },
                "shared_student_findings": list(findings.values()),
                "number_of_ordered_segments": len(submitted),
                "disclosed_guidance": list(disclosed),
            }
            if anchored:
                material["confirmed_current_spans"] = [
                    {
                        key: item[key]
                        for key in (
                            "segment_id",
                            "source_span",
                            "meaning",
                            "evidence_level",
                        )
                    }
                    for item in additions.values()
                ]
            findings.update(additions)
            if skip_acquired:
                material["already_known_clues"] = [
                    item for item in clue_ledger if item["status"] != "disclosed"
                ]
            if direct:
                assessment = {"decision": "revise:final_answer", "reusable": [],
                              "diagnosis": "The submitted final value disagrees with the independent key. "
                                           "The learner supplied no derivation."}
                attempt["student_assessment_source"] = "independent_answer_key"
            else:
                if ordered_evidence:
                    material["ordered_segment_ids"] = version["submitted_ids"]
                schema = review_schema(version["submitted_ids"], bool(answer_line), anchored=anchored,
                                       single_decision=single_decision, ordered_evidence=ordered_evidence,
                                       task_analysis=task_analysis)
                coach = self.request(
                    "coach", index, [
                        {"role": "system", "content": tutor_instruction},
                        {"role": "user", "content": json.dumps(material, ensure_ascii=False)},
                    ], schema,
                )
                attempt["coach_response"] = coach
                if coach["finish_reason"] != "stop":
                    issue = "coach_incomplete"
                    break
            try:
                proposed_approaches = []
                if not direct:
                    assessment = json.loads(coach["content"])
                    if task_analysis and isinstance(assessment, dict):
                        proposed_approaches = assessment.pop("independent_approaches", None)
                        if not isinstance(proposed_approaches, list):
                            raise ValueError("student assessment is missing the independent approaches decision")
                review = validated_review(
                    assessment,
                    submitted,
                    answer_line,
                    anchored=anchored,
                    single_decision=single_decision,
                )
                count = review["verified_prefix_count"]
                if not 0 <= count <= len(submitted) or (
                    review["verdict"] == "supported" and count != len(submitted)
                ):
                    raise ValueError(
                        "assessment does not identify a valid contiguous reviewed prefix"
                    )
            except (ValueError, TypeError):
                issue = "invalid_coach_assessment"
                if ordered_evidence:
                    status = "awaiting_guidance"
                break
            if ordered_evidence and not direct:
                resolved, problem = check_or_repair_student_findings(
                    self.request, attempt, material, assessment, version["submitted_ids"], schema["properties"]["reusable"])
                if problem:
                    status, issue = "awaiting_guidance", problem
                    attempt["coach_evidence_repair"]["issue"] = issue
                    break
                review["reusable"] = resolved
            attempt["coach"] = review
            # A checked contradiction and a blanket "supported" ruling cannot jointly
            # authenticate new statements. Reuse only prior evidence on this conflict.
            if (
                skip_acquired
                and review["verdict"] == "supported"
                and (not check["format_ok"] or check["answer_matches"] is False)
            ):
                count = min(count, len(active))
                version["review_conflict"] = "program_check_contradicts_supported"
            if repeated is not None:
                count = min(count, len(active) + repeated)
            if grounded:
                rooted = rooted_prefix_count(review["reusable"], submitted, count)
                if rooted < count:
                    version["unrooted_evidence_at"] = submitted[rooted]["id"]
                    count = rooted
            active = submitted[:count]
            if task_analysis:
                unresolved = submitted[count]["id"] if count < len(submitted) else None
                if not version.get("review_conflict"):
                    approaches.update(checked_student_approaches(proposed_approaches, submitted, unresolved, question))
                attempt["supported_student_approaches"] = list(approaches.values())
            active_ids = {s["id"] for s in active}
            findings = {
                sid: f
                for sid, f in findings.items()
                if all(k in active_ids for k in f["depends_on"])
                and (
                    f["basis_id"]
                    in {"question", "arithmetic", "reviewed_progress_check"}
                    or f["basis_id"] in active_ids
                )
            }
            extracted = shared_findings(review["reusable"], submitted, active, question,
                                        exclude_list_markers=single_decision)
            findings.update(extracted)
            if until_correct:
                current_findings = {f.get("check_id") or f["text"].strip() for f in findings.values()}
                current_findings.update(item["text"].strip() for item in approaches.values())
                grew = len(active) > greatest_verified_prefix or bool(current_findings - seen_findings)
                if paired and index > 1 and attempts[-2].get("disclosure", {}).get("source_step_receipt"):
                    grew = True
                greatest_verified_prefix = max(greatest_verified_prefix, len(active))
                seen_findings.update(current_findings)
                stalled_rounds = 0 if grew else stalled_rounds + int(index > 1)
                if (paired and stalled_rounds >= max_attempts
                        and eligible_teaching_steps(source, active, findings, disclosed, tried_verified_steps)):
                    attempt["recommend_verified_operation"] = True
            version.update(
                retained_after=[s["id"] for s in active],
                reusable=list(extracted),
                shared_findings=list(findings.values()),
                withdrawn=[
                    s["id"] for s in submitted[count:] if s["id"] not in extracted and s["id"] not in approaches
                ],
                first_unresolved=submitted[count]["id"]
                if count < len(submitted)
                else "final_answer",
            )
            if (
                check["format_ok"]
                and check["answer_matches"] is not False
                and review["verdict"] == "supported"
                and (not grounded or count == len(submitted))
            ):
                if record_guided_reasoning and gold is not None:
                    trace = student_reasoning_trace(attempts, question)
                    if contains_chat_control(trace):
                        issue = "guided_reasoning_contains_control_markers"
                        break
                    if teacher_delivery:
                        delivery = compose_guided_final_answer(
                            self.request, source, index, active, check.get("answer_surface") or check["answer"],
                            reasoning_trace=trace, protocol_version=protocol_version)
                        attempt["teacher_final_answer"] = delivery
                        final_provenance = delivery["provenance"]
                        formal_history.append({"attempt": index, "author": "teacher", "check": delivery["check"],
                                               "receipt": final_provenance["receipt"]})
                        training_reasoning, final_text = trace, delivery["public_answer"]
                        if not delivery["check"]["format_ok"]:
                            status, issue = "awaiting_guidance", delivery["check"]["issue"]
                            version["first_unresolved"] = "teacher_final_answer"
                        else:
                            status, issue = "accepted", None
                            version["first_unresolved"] = None
                        break
                    prefix = {"role": "assistant", "reasoning_content": trace, "content": ""}
                    formal = self.request("final_answer", index, [*final_task, prefix], None)
                    formal_check = inspect_formal_answer(
                        formal, gold, question, final_only=source.get("answer_requirement") == "final_only",
                        allow_bare_delivery=grounded,
                        require_explanation=require_explanation,
                    )
                    attempt["formal_answer"] = {"response": formal, "check": formal_check}
                    formal_history.append({"attempt": index, "check": formal_check,
                                           "receipt": f"{index:03}-final_answer.json"})
                    final_text = formal["content"]
                    if not (formal_check["format_ok"] and formal_check["answer_matches"] is True):
                        issue = formal_check["issue"]
                        version["first_unresolved"] = "formal_answer"
                        if (formal["finish_reason"] != "stop" or formal.get("reasoning_content")
                            or not final_text.strip() or contains_chat_control(final_text)):
                            break
                        if index == attempt_limit:
                            if until_correct and stalled_rounds >= max_attempts and not attempt.get("recommend_verified_operation"):
                                status, issue = "awaiting_guidance", "no_new_verified_student_progress"
                                break
                            if not until_correct and (index != max_attempts or len(formal_history) != 1
                                                      or formal_check["answer_matches"] is not False):
                                break
                            # A late first delivery error gets one actual student revision.
                            # Subsequent delivery errors cannot grow the run again.
                            attempt_limit += 1
                            formal_history[-1]["granted_correction_round"] = attempt_limit
                        disclosure = {
                            **formal_delivery_hint(question, "worked_solution" if issue == "formal_answer_missing_requested_explanation"
                                                   else gold["kind"], index),
                            "ordinal": len(disclosed) + 1, "issued_after_attempt": index,
                            "anchor_after": active[-1]["id"] if active else None,
                            "replaces_from": "formal_answer",
                        }
                        if review_disclosures:
                            disclosure, issue = assess_hint(
                                self.request, source, attempt, active, findings, disclosed, disclosure)
                            if disclosure is None:
                                if authored_hints:
                                    status = "awaiting_guidance"
                                break
                        disclosed.append(disclosure)
                        attempt["disclosure"] = disclosure
                        attempt["hint_to_student"] = disclosure["text"]
                        continue
                    training_reasoning = trace
                status = "accepted" if gold is not None else "unverified"
                version["first_unresolved"] = None
                issue = None
                break
            issue = check["issue"] or "explanation_requires_review"
            if until_correct and index == attempt_limit:
                if stalled_rounds >= max_attempts and not attempt.get("recommend_verified_operation"):
                    status, issue = "awaiting_guidance", "no_new_verified_student_progress"
                    break
                attempt_limit += 1
            if index < max_attempts or until_correct:
                while skip_acquired and len(clue_ledger) < len(bank):
                    clue = bank[len(clue_ledger)]
                    evidence = prior_clue_evidence(clue, active, findings, disclosed)
                    if not evidence:
                        break
                    clue_ledger.append(
                        {
                            **clue,
                            **evidence,
                            "bank_position": len(clue_ledger) + 1,
                            "recorded_after_attempt": index,
                        }
                    )
                if len(clue_ledger) >= len(bank):
                    if not authored_hints:
                        issue = "disclosure_bank_exhausted"
                        break
                    reference = {"id": f"teacher-{index}", "source": "teacher_authored", "kind": "idea", "text": ""}
                else:
                    reference = bank[len(clue_ledger)]
                disclosure = {
                    **reference,
                    "ordinal": len(disclosed) + 1,
                    "issued_after_attempt": index,
                    "anchor_after": active[-1]["id"] if active else None,
                    "replaces_from": version["first_unresolved"],
                }
                if review_disclosures:
                    disclosure, issue = assess_hint(
                        self.request, source, attempt, active, findings, disclosed, disclosure)
                    if disclosure is None:
                        if authored_hints:
                            status = "awaiting_guidance"
                        break
                clue_ledger.append(
                    {
                        **disclosure,
                        "status": "disclosed",
                        "bank_position": len(clue_ledger) + 1 if len(clue_ledger) < len(bank) else None,
                    }
                )
                disclosed.append(disclosure)
                attempt["disclosure"] = disclosure
                attempt["hint_to_student"] = disclosure["text"]
                if skip_acquired:
                    continue
                fragment = assemble(active)
                direction = (
                    "Continue directly after the following retained passage. Write only its continuation.\n"
                    + fragment
                    + "\n"
                    if fragment
                    else "Start the corrected derivation from the beginning.\n"
                )
                if findings:
                    direction += (
                        "Other independently grounded findings from your own work:\n"
                        + "\n".join(f["text"].strip() for f in findings.values())
                        + "\n"
                    )
                public.append(
                    {"role": "tool", "content": direction + "\n" + disclosure["text"]}
                )
        last = attempts[-1]
        terminal_check = last.get("formal_answer", {}).get("check", last["check"])
        if final_provenance:
            terminal_check = last["teacher_final_answer"]["check"]
        route = "reviewed_solution"
        correctness = "unverified"
        if status == "accepted":
            if training_reasoning:
                route = "guided_reasoning" if disclosed else "unaided_reasoning"
                correctness = ("reviewed_student_solution_and_teacher_final_key_match" if final_provenance
                               else "reviewed_solution_and_fresh_final_key_match")
            elif direct_match:
                route, correctness = "nonthinking_direct", "unaided_direct_key_match"
            else:
                correctness = "key_matched_and_explanation_reviewed"
        return {
            **source,
            "protocol": "guided-draft-snapshot/" + protocol,
            "mode": mode,
            "attempts": attempts,
            "status": status,
            **({"continuation": {"reason": issue, "last_student_round": last["attempt"],
                                 "published_hints": len(disclosed), "experience_version": experience["version"]}}
               if status == "awaiting_guidance" else {}),
            **({"training_route": route} if direct_protocol and status == "accepted" else {}),
            **({"finalization": formal_history} if formal_history else {}),
            **({"final_answer_provenance": final_provenance} if final_provenance and status == "accepted" else {}),
            "verification": {
                "answer_key_check": terminal_check["answer_matches"],
                "explanation_review": (last.get("coach") or {}).get("verdict"),
                "correctness": correctness,
                "issue": issue,
            },
            "answer": terminal_check["answer"],
            "public_dialogue": public[1:],
            "disclosures": disclosed,
            "clue_ledger": clue_ledger,
            "draft_versions": versions,
            "retained_segments": active,
            "shared_student_findings": list(findings.values()),
            **({"supported_student_approaches": list(approaches.values())} if task_analysis else {}),
            "messages": [
                *task_messages,
                {"role": "assistant", "content": final_text,
                 **({"reasoning_content": training_reasoning} if training_reasoning else {})},
            ],
            "experience_version": experience["version"],
        }
