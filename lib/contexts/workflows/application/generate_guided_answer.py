"""Generate a visible solution, check it and request a directed revision when needed."""

import json

from ..domain.guided_answers import (
    assemble,
    checked_progress,
    disclosure_bank,
    draft_fragment,
    inspect_answer,
    prior_clue_evidence,
    shared_findings,
    validated_review,
)
from lib.prompts.workflow_guided_answers import (
    ANCHORED_REVIEW,
    COACH,
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

    def run(self, source, experience, max_attempts, *, protocol_version=VERSION):
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        question, gold = source["question"], source["gold"]
        skip_acquired = protocol_version != "3.0.0"
        marked_finals = protocol_version == "3.4.0"
        single_decision = protocol_version in {"3.3.0", "3.4.0"}
        anchored = single_decision or protocol_version == "3.2.0"
        protocol = {"3.0.0": "v3", "3.1.0": "v3.1", "3.2.0": "v3.2", "3.3.0": "v3.3", "3.4.0": "v3.4"}[protocol_version]
        tutor_instruction = SINGLE_DECISION_REVIEW if single_decision else ANCHORED_REVIEW if anchored else COACH
        mode = "answer_key" if gold is not None else "source_reasoning_only"
        attempts = []
        status = "quarantine"
        issue = "attempt_limit"
        system = (STUDENT if gold is not None else SOURCE_COMPLETION_STUDENT) + (
            "\n\n" + experience["student"] if experience.get("student") else ""
        )
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
        bank, disclosed = disclosure_bank(source), []
        clue_ledger = []
        active = []
        versions = []
        findings = {}
        final_text = ""
        public = list(initial_messages)
        for index in range(1, max_attempts + 1):
            messages = list(initial_messages)
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
                break
            suffix = student["content"]
            public.append({"role": "assistant", "content": suffix})
            new_segments, answer_line = draft_fragment(suffix, index, preserve_list_items=single_decision,
                                                       marked_finals=marked_finals)
            for piece in new_segments:
                piece["reuses_student_findings"] = [
                    sid
                    for sid, f in findings.items()
                    if f["text"].strip() == piece["text"].strip()
                ]
            submitted = [*active, *new_segments]
            text = assemble(submitted, answer_line)
            final_text = text
            check = inspect_answer(text, gold, parse_count_sentence=skip_acquired,
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
            coach = self.request(
                "coach",
                index,
                [
                    {
                        "role": "system",
                        "content": tutor_instruction,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(material, ensure_ascii=False),
                    },
                ],
                review_schema(
                    version["submitted_ids"], bool(answer_line), anchored=anchored, single_decision=single_decision
                ),
            )
            attempt["coach_response"] = coach
            if coach["finish_reason"] != "stop":
                issue = "coach_incomplete"
                break
            try:
                review = validated_review(
                    json.loads(coach["content"]),
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
                break
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
            active = submitted[:count]
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
            version.update(
                retained_after=[s["id"] for s in active],
                reusable=list(extracted),
                shared_findings=list(findings.values()),
                withdrawn=[
                    s["id"] for s in submitted[count:] if s["id"] not in extracted
                ],
                first_unresolved=submitted[count]["id"]
                if count < len(submitted)
                else "final_answer",
            )
            if (
                check["format_ok"]
                and check["answer_matches"] is not False
                and review["verdict"] == "supported"
            ):
                status = "accepted" if gold is not None else "unverified"
                version["first_unresolved"] = None
                issue = None
                break
            issue = check["issue"] or "explanation_requires_review"
            if index < max_attempts:
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
                    issue = "disclosure_bank_exhausted"
                    break
                disclosure = {
                    **bank[len(clue_ledger)],
                    "ordinal": len(disclosed) + 1,
                    "issued_after_attempt": index,
                    "anchor_after": active[-1]["id"] if active else None,
                    "replaces_from": version["first_unresolved"],
                }
                clue_ledger.append(
                    {
                        **disclosure,
                        "status": "disclosed",
                        "bank_position": len(clue_ledger) + 1,
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
        return {
            **source,
            "protocol": "guided-draft-snapshot/" + protocol,
            "mode": mode,
            "attempts": attempts,
            "status": status,
            "verification": {
                "answer_key_check": last["check"]["answer_matches"],
                "explanation_review": (last.get("coach") or {}).get("verdict"),
                "correctness": "key_matched_and_explanation_reviewed"
                if status == "accepted"
                else "unverified",
                "issue": issue,
            },
            "answer": last["check"]["answer"],
            "public_dialogue": public[1:],
            "disclosures": disclosed,
            "clue_ledger": clue_ledger,
            "draft_versions": versions,
            "retained_segments": active,
            "shared_student_findings": list(findings.values()),
            "messages": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": final_text},
            ],
            "experience_version": experience["version"],
        }
