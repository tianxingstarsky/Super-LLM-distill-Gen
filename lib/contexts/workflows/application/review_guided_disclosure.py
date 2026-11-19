"""Review a tutor's proposed hint before exposing it to the learner."""

import json

from lib.prompts.workflow_guided_answers import (
    AUTHORED_DISCLOSURE_REVIEW, DISCLOSURE_AUTHOR, DISCLOSURE_REVIEW, GROUNDED_AUTHOR_GUIDANCE,
    GROUNDED_HINT_REVIEW, TUTOR_TASK_ANALYSIS, VERIFIED_OUTCOME_CONTEXT, VERIFIED_STEP_AUTHOR, VERIFIED_STEP_REVIEW, disclosure_author_schema,
    DISCLOSURE_PAIRING, QUOTED_DISCLOSURE, disclosure_review_schema, verified_choice_schema,
    verified_choice_review_schema, with_disclosure_pairing,
)
from ..domain.guided_answers import prior_clue_evidence
from ..domain.guided_disclosure_text import checked_reviewer_pairing, locate_authored_task_citations, render_teacher_text
from ..domain.guided_teaching_steps import checked_step_review, checked_step_selection
from ..domain.guided_disclosure import (
    checked_disclosure, checked_teacher_draft, disclosure_evidence, is_substantive_question_quote,
)


def _review_material(source, attempt, retained, findings, disclosed, candidate):
    evidence = disclosure_evidence(source["question"], retained, findings, disclosed)
    check = attempt["check"]
    formal = attempt.get("formal_answer")
    if formal and check["answer_matches"] and attempt["coach"]["verdict"] == "supported":
        evidence["student_conclusion"] = {"kind": "established_student_conclusion",
                                          "text": check.get("answer_surface") or check["answer"]}
    previous_id = f"disclosed.{disclosed[-1]['ordinal']}" if disclosed else None
    anchor = candidate["replaces_from"]
    # A faulty student claim identifies the difficulty. It is never a valid premise.
    observed = formal["response"]["content"] if formal else (
        attempt.get("coach", {}).get("evidence") or attempt["student"]["content"])
    facts, history = [], []
    for ref, item in evidence.items():
        target = history if item["kind"] == "published_teacher_hint" else facts
        target.append({"evidence_id": ref, **item})
    material = {
        "established_evidence": facts,
        "ordered_previous_teacher_disclosures": history,
        "latest_disclosure_id": previous_id,
        "current_difficulty": {"at": anchor, "observed_text_end": observed[-1200:],
                               "excerpted": len(observed) > 1200},
        "proposed_next_teacher_disclosure": {"kind": candidate["kind"], "text": candidate["text"]},
    }
    if source.get("task_system"):
        material["task_instructions"] = source["task_system"]
    if attempt.get("supported_student_approaches"):
        material["supported_student_approaches"] = [
            {"text": item["text"], "question_quote": item["question_quote"]}
            for item in attempt["supported_student_approaches"]
        ]
    if "supported_student_approaches" in attempt and formal:
        material["current_difficulty"].update(
            formal_delivery_issue=formal["check"]["issue"],
            formal_value_matches_key=formal["check"]["answer_matches"],
            formal_format_ok=formal["check"]["format_ok"],
        )
    return material, evidence, previous_id


def review_next_disclosure(request, source, attempt, retained, findings, disclosed, candidate):
    material, evidence, previous_id = _review_material(source, attempt, retained, findings, disclosed, candidate)
    response = request("disclosure", attempt["attempt"], [
        {"role": "system", "content": DISCLOSURE_REVIEW},
        {"role": "user", "content": json.dumps(material, ensure_ascii=False)},
    ], disclosure_review_schema(list(evidence)))
    audit = {"candidate": dict(candidate), "latest_disclosure_id": previous_id, "response": response}
    attempt["disclosure_review"] = audit
    if response["finish_reason"] != "stop":
        audit["issue"] = "disclosure_review_incomplete"
        return None, audit["issue"]
    try:
        assessment = json.loads(response["content"])
        audit["assessment"] = assessment
        approved = checked_disclosure(assessment, evidence, previous_id, candidate, source["gold"])
    except (ValueError, TypeError) as exc:
        audit["issue"] = "invalid_disclosure_review"
        audit["detail"] = str(exc)
        return None, audit["issue"]
    if not approved:
        audit["issue"] = "teacher_disclosure_withheld"
        return None, audit["issue"]
    audit["validated_citations"] = approved["citations"]
    return {**candidate, **approved}, None


def eligible_teaching_steps(source, retained, findings, disclosed, tried=()):
    catalog = source.get("verified_teaching_steps", {})
    known = {h.get("source_step_id") for h in disclosed} | set(tried)
    return {s["id"]: s for s in catalog.get("steps", ()) if s["id"] not in known
            and not prior_clue_evidence({"text": s["operation"]}, retained, findings, disclosed)}


def _add_measured_outcome(material, source, attempt, retained, findings):
    submission = attempt.get("formal_answer", {}).get("check", attempt["check"])
    match = submission.get("answer_matches") if source["gold"] and submission.get("format_ok") else None
    material["current_difficulty"].update(
        submitted_answer_status={True: "correct", False: "incorrect", None: "not_checked"}[match],
        derivation_available=bool(attempt["derivation_available"] or retained or findings),
    )


def _review_verified_step(request, attempt, history, material, evidence, previous_id, reference, receipt, step,
                          *, paired=False, recommended_index=None, task_analysis=False):
    entry = history[-1]
    entry["verified_operation"] = {"id": step["id"], "receipt_id": receipt, "operation": step["operation"]}
    if (entry["revision"] and history[0].get("verified_operation") == entry["verified_operation"]
            and history[0].get("issue") == "teacher_disclosure_withheld"):
        entry["reused_prior_review_revision"] = 0
        return None, "unchanged_withheld_disclosure"
    anchor = f"task-span:{step['id']}"
    packet = {k: material[k] for k in ("established_evidence", "ordered_previous_teacher_disclosures",
                                       "latest_disclosure_id", "current_difficulty", "private_reference_ceiling")}
    if "task_instructions" in material:
        packet["task_instructions"] = material["task_instructions"]
    if "supported_student_approaches" in material:
        packet["supported_student_approaches"] = material["supported_student_approaches"]
    packet["proposed_next_teacher_disclosure"] = {
        "step_id": step["id"], "operation": step["operation"], "kind": step["kind"],
        "task_evidence_id": anchor, "quote": step["quote"], "span": step["span"],
    }
    reviewer = "disclosure_recheck" if entry["revision"] else "disclosure"
    request_index = attempt["attempt"]
    public_text = step["operation"]
    if paired:
        packet["student_attempt"] = attempt["attempt"]
        if recommended_index is not None:
            reviewer, request_index = "disclosure_candidate_review", recommended_index
        try:
            public_text, quotes = render_teacher_text(public_text, evidence["question"]["text"], step["quote"],
                                                     anchor_span=step["span"])
        except ValueError as exc:
            return None, str(exc)
        packet["proposed_next_teacher_disclosure"]["operation"] = public_text
        packet["proposed_next_teacher_disclosure"]["public_text"] = public_text
        entry.update(public_text=public_text, question_quotations=quotes,
                     review_checkpoint={"role": reviewer, "index": request_index, "student_attempt": attempt["attempt"]})
    # Only catalogue IDs and the actual dialogue observations go into the model's context.
    references = [anchor, *(ref for ref in evidence if ref != "question")]
    system = VERIFIED_STEP_REVIEW
    if task_analysis:
        system += "\n\n" + TUTOR_TASK_ANALYSIS
    if "submitted_answer_status" in material["current_difficulty"]:
        system += "\n\n" + VERIFIED_OUTCOME_CONTEXT
    schema = verified_choice_review_schema(references)
    if paired:
        system += "\n\n" + QUOTED_DISCLOSURE + "\n\n" + DISCLOSURE_PAIRING
        schema = with_disclosure_pairing(schema)
    response = request(reviewer, request_index, [
        {"role": "system", "content": system}, {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}
    ], schema)
    entry["review_response"] = response
    if response["finish_reason"] != "stop":
        return None, "disclosure_review_incomplete"
    try:
        assessment = json.loads(response["content"])
        entry["assessment"] = assessment
        checked = checked_reviewer_pairing(assessment, previous_id) if paired else assessment
        citations = checked_step_review(checked, step, evidence, previous_id)
    except (ValueError, TypeError) as exc:
        return None, str(exc)
    if citations is None:
        return None, "teacher_disclosure_withheld"
    entry["validated_citations"] = citations
    return {**reference, "source": "verified_teaching_step", "source_step_id": step["id"],
            "source_step_receipt": receipt, "kind": step["kind"], "text": public_text,
            "candidate_text": public_text, "citations": citations,
            **({"question_quotations": quotes, "review_checkpoint": entry["review_checkpoint"]} if paired else {})}, None


def author_next_disclosure(request, source, attempt, retained, findings, disclosed, reference, teacher_experience,
                           *, grounded=False, allow_catalog=False, show_outcome=False, paired=False, tried=(),
                           task_analysis=False):
    """Draft and independently review one publishable teacher continuation, with one bounded repair."""
    material, evidence, previous_id = _review_material(source, attempt, retained, findings, disclosed, reference)
    ceiling = material.pop("proposed_next_teacher_disclosure")
    material["private_reference_ceiling"] = ceiling if reference["text"] else None
    history = []
    attempt["disclosure_work"] = history
    author_system = DISCLOSURE_AUTHOR
    reviewer_system = AUTHORED_DISCLOSURE_REVIEW
    if grounded:
        author_system += "\n\n" + GROUNDED_AUTHOR_GUIDANCE
        reviewer_system += "\n\n" + GROUNDED_HINT_REVIEW
    catalog = source.get("verified_teaching_steps") if allow_catalog else None
    options = eligible_teaching_steps(source, retained, findings, disclosed, tried) if catalog else {}
    if options:
        author_system = VERIFIED_STEP_AUTHOR
        material["available_operations"] = [
            {k: step[k] for k in ("id", "kind", "quote", "span", "operation")} for step in options.values()]
    if show_outcome:
        _add_measured_outcome(material, source, attempt, retained, findings)
        author_system += "\n\n" + VERIFIED_OUTCOME_CONTEXT
        reviewer_system += "\n\n" + VERIFIED_OUTCOME_CONTEXT
    if paired:
        material["student_attempt"] = attempt["attempt"]
        author_system += "\n\n" + QUOTED_DISCLOSURE
        reviewer_system += "\n\n" + QUOTED_DISCLOSURE + "\n\n" + DISCLOSURE_PAIRING
    if task_analysis:
        author_system += "\n\n" + TUTOR_TASK_ANALYSIS
        reviewer_system += "\n\n" + TUTOR_TASK_ANALYSIS
    author_system += "\n\n" + teacher_experience if teacher_experience else ""
    for revision in range(2):
        entry = {"revision": revision, "private_reference": reference["text"], "latest_disclosure_id": previous_id}
        history.append(entry)
        role = "disclosure_rewrite" if revision else "disclosure_draft"
        draft = request(role, attempt["attempt"], [
            {"role": "system", "content": author_system},
            {"role": "user", "content": json.dumps(material, ensure_ascii=False)},
        ], verified_choice_schema(options) if options else disclosure_author_schema(grounded=grounded))
        entry["draft_response"] = draft
        if draft["finish_reason"] != "stop":
            entry["issue"] = "disclosure_draft_incomplete"
            return None, entry["issue"]
        try:
            proposal = json.loads(draft["content"])
            entry["draft"] = proposal
            if not isinstance(proposal, dict):
                raise ValueError("invalid_disclosure_draft")
            step = checked_step_selection(proposal, options) if options else None
            if options and proposal.get("decision") == "insufficient":
                proposal = {"bridge": "", "hint": "", "question_quote": "", **proposal}
            wording = None if step else checked_teacher_draft(proposal, source["gold"], retained, findings, disclosed,
                                                               question=source["question"] if grounded else None)
            if not step and wording is None:
                entry["issue"] = "teacher_needs_more_evidence"
                return None, entry["issue"]
            if paired and wording:
                bridge_text, _ = render_teacher_text(wording[0], source["question"], proposal["question_quote"])
                hint_text, _ = render_teacher_text(wording[1], source["question"], proposal["question_quote"])
                wording = (bridge_text, hint_text)
                entry["public_text"], entry["question_quotations"] = render_teacher_text(
                    bridge_text + "\n" + hint_text, source["question"], proposal["question_quote"])
        except (TypeError, ValueError) as exc:
            entry["issue"] = str(exc)
        else:
            if step:
                approved, issue = _review_verified_step(request, attempt, history, material, evidence, previous_id,
                                                        reference, catalog["receipt_id"], step, paired=paired,
                                                        task_analysis=task_analysis)
                if approved:
                    return approved, None
                entry["issue"] = issue
                if revision or issue == "disclosure_review_incomplete":
                    return None, issue
                prior_assessment = entry.get("assessment")
                material["requested_revision"] = {"prior_draft": proposal, "issue": issue,
                                                  "private_reviewer_reason": prior_assessment.get("reason")
                                                  if isinstance(prior_assessment, dict) else None}
                continue
            bridge, hint = wording
            prior_review = history[0].get("assessment")
            if grounded and revision and isinstance(prior_review, dict) and prior_review.get("decision") == "withhold":
                previous = history[0]["draft"]
                if all(" ".join(proposal[k].split()) == " ".join(previous.get(k, "").split()) for k in ("hint", "bridge")):
                    entry["reused_prior_review_revision"] = history[0]["revision"]
                    entry["issue"] = "unchanged_withheld_disclosure"
                    return None, entry["issue"]
            candidate = {**reference, "source": "teacher_authored", "text": hint}
            packet = {k: v for k, v in material.items() if k != "requested_revision"}
            packet["proposed_next_teacher_disclosure"] = {"kind": reference["kind"], "bridge": bridge, "hint": hint}
            if grounded:
                packet["proposed_next_teacher_disclosure"]["question_quote"] = proposal["question_quote"]
            reviewer = "disclosure_recheck" if revision else "disclosure"
            schema = disclosure_review_schema(list(evidence))
            if paired:
                packet["proposed_next_teacher_disclosure"]["public_text"] = entry["public_text"]
                entry["review_checkpoint"] = {"role": reviewer, "index": attempt["attempt"],
                                              "student_attempt": attempt["attempt"]}
                schema = with_disclosure_pairing(schema)
            response = request(reviewer, attempt["attempt"], [
                {"role": "system", "content": reviewer_system},
                {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
            ], schema)
            entry["review_response"] = response
            if response["finish_reason"] != "stop":
                entry["issue"] = "disclosure_review_incomplete"
                return None, entry["issue"]
            try:
                assessment = json.loads(response["content"])
                entry["assessment"] = assessment
                checked = checked_reviewer_pairing(assessment, previous_id) if paired else assessment
                approved = checked_disclosure(checked, evidence, previous_id, candidate, source["gold"])
                if approved is None:
                    raise ValueError("teacher_disclosure_withheld")
                if grounded and not any(c["evidence_id"] == "question" and is_substantive_question_quote(c["quote"])
                                        for c in approved["citations"]):
                    raise ValueError("review_needs_a_specific_verbatim_task_premise")
                if approved["bridge"] != bridge:
                    raise ValueError("reviewer_changed_the_authored_bridge")
                if paired:
                    if approved["text"] != entry["public_text"]:
                        raise ValueError("approved_text_differs_from_reviewed_disclosure")
                    approved["citations"] = locate_authored_task_citations(
                        approved["citations"], source["question"], proposal["question_quote"])
                    approved.update(question_quotations=entry["question_quotations"], review_checkpoint=entry["review_checkpoint"])
                entry["validated_citations"] = approved["citations"]
                return {**candidate, **approved}, None
            except (TypeError, ValueError) as exc:
                entry["issue"] = str(exc)
        if revision:
            return None, entry["issue"]
        assessment = entry.get("assessment")
        material["requested_revision"] = {"prior_draft": entry.get("draft"), "issue": entry["issue"],
                                          "private_reviewer_reason": assessment.get("reason")
                                          if isinstance(assessment, dict) else None}


def review_or_recommend_disclosure(request, source, attempt, retained, findings, disclosed, reference,
                                   *, teacher_experience, tried, task_analysis=False):
    """Try each remaining externally verified possibility at most once, still requiring independent release."""
    if attempt.get("recommend_verified_operation"):
        history = attempt["disclosure_work"] = []
        issue = "no_new_verified_student_progress"
    else:
        approved, issue = author_next_disclosure(
            request, source, attempt, retained, findings, disclosed, reference, teacher_experience,
            grounded=True, allow_catalog=True, show_outcome=True, paired=True, tried=tried, task_analysis=task_analysis,
        )
        history = attempt["disclosure_work"]
        tried.update(entry["verified_operation"]["id"] for entry in history if "verified_operation" in entry)
        if approved or issue in {"disclosure_review_incomplete", "disclosure_draft_incomplete"}:
            return approved, issue
    remaining = eligible_teaching_steps(source, retained, findings, disclosed, tried)
    if not remaining:
        return None, issue
    catalog = source["verified_teaching_steps"]
    material, evidence, previous_id = _review_material(source, attempt, retained, findings, disclosed, reference)
    ceiling = material.pop("proposed_next_teacher_disclosure")
    material["private_reference_ceiling"] = ceiling if reference["text"] else None
    _add_measured_outcome(material, source, attempt, retained, findings)
    for ordinal, step in enumerate(catalog["steps"], 1):
        if step["id"] not in remaining:
            continue
        entry = {"revision": len(history), "proposed_by": "program", "latest_disclosure_id": previous_id,
                 "trigger": issue, "draft": {"decision": "recommend:" + step["id"], "hint": step["operation"]}}
        history.append(entry)
        tried.add(step["id"])
        approved, issue = _review_verified_step(request, attempt, history, material, evidence, previous_id,
                                                reference, catalog["receipt_id"], step, paired=True,
                                                recommended_index=ordinal, task_analysis=task_analysis)
        if approved:
            return approved, None
        entry["issue"] = issue
        if issue == "disclosure_review_incomplete":
            return None, issue
    return None, "verified_teaching_operations_exhausted"
