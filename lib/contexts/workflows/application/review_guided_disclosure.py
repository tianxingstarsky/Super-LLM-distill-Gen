"""Review a tutor's proposed hint before exposing it to the learner."""

import json

from lib.prompts.workflow_guided_answers import (
    BARE_ANSWER_OPERATION_AUTHOR, BARE_ANSWER_OPERATION_REVIEW,
    AUTHORED_DISCLOSURE_REVIEW, DISCLOSURE_AUTHOR, DISCLOSURE_REVIEW, GROUNDED_AUTHOR_GUIDANCE,
    GROUNDED_HINT_REVIEW, TUTOR_TASK_ANALYSIS, VERIFIED_OUTCOME_CONTEXT, VERIFIED_STEP_AUTHOR, VERIFIED_STEP_REVIEW, disclosure_author_schema,
    DISCLOSURE_PAIRING, QUOTED_DISCLOSURE, disclosure_draft_decision_schema, disclosure_review_schema, verified_choice_schema,
    verified_choice_review_schema, with_disclosure_pairing,
)
from ..domain.guided_answers import prior_clue_evidence, read_teacher_submission
from ..domain.guided_disclosure_text import (
    checked_reviewer_pairing, locate_authored_task_citations, locate_teacher_premise, render_teacher_text,
)
from ..domain.guided_teaching_steps import checked_step_review, checked_step_selection
from ..domain.guided_student_calculations import asserted_arithmetic_errors
from ..domain.guided_quantity_scope import storage_quantity_findings, storage_quantity_issue
from ..domain.guided_disclosure import (
    checked_disclosure, checked_teacher_draft, disclosure_evidence, is_substantive_question_quote, question_premise_choices,
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
    if attempt.get("arithmetic_errors"):
        material["current_difficulty"]["refuted_student_equations"] = attempt["arithmetic_errors"]
    if attempt.get("coach_feedback_check"):
        equations = attempt["coach_feedback_check"]["arithmetic_errors"]
        material["private_review_check"] = {
            "status": "refuted", "scope": "arithmetic_of_the_expression_only",
            "evaluated_expressions": [{"expression": e["expression"], "value": e["values"][1]}
                                      for e in equations if "expression" in e],
        }
    calculation_context = []
    for finding in findings.values():
        if (finding.get("evidence_level") != "source_verified_student_calculation"
                or finding["source_attempt"] != attempt["attempt"]):
            continue
        start, end = finding["source_span"]
        student_text = attempt["student"]["content"]
        # The equation is checked. Its surrounding units and interpretation are the learner's claims.
        line_start = max(student_text.rfind("\n", 0, start) + 1, start - 160)
        calculation_context.append({"evidence_id": finding["segment_id"], "checked_equation": finding["text"],
                                    "learner_wording_through_equation": student_text[line_start:end]})
    if calculation_context:
        material["learner_wording_for_interpretation"] = calculation_context
    if attempt.get("truncated_observation"):
        material["current_difficulty"] = {"at": anchor, **attempt["truncated_observation"],
                                          "status": "output_limit_without_final_answer"}
        material["private_source_reference"] = {
            "answer_key": source["gold"], "attributed_source_reasoning": source["source_reasoning"],
            "use": "Check the direction privately. Ground the next hint in the user's question and previously established facts.",
        }
    if source.get("task_system"):
        material["task_instructions"] = source["task_system"]
    if attempt.get("verified_stage"):
        stage = attempt["verified_stage"]
        material["current_difficulty"]["student_progress"] = {
            k: stage[k] for k in ("state", "comparable", "answer_key_match", "student_advanced", "new_fact_segments",
                                  "new_method_segments", "unresolved_review_evidence")
            if k in stage}
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
        assessment = read_teacher_submission(response)
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


def _wrong_answer_without_work(material):
    observed = material["current_difficulty"]
    return observed.get("submitted_answer_status") == "incorrect" and observed.get("derivation_available") is False


def _review_verified_step(request, attempt, history, material, evidence, previous_id, reference, receipt, step,
                          *, paired=False, recommended_index=None, task_analysis=False, check_arithmetic=False,
                          bare_answer_operations=False):
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
    if "private_source_reference" in material:
        packet["private_source_reference"] = material["private_source_reference"]
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
        if check_arithmetic and (issue := _checked_disclosure_issue(entry, evidence["question"]["text"])):
            entry.pop("review_checkpoint")
            return None, issue
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
        if bare_answer_operations and step["kind"] == "operation" and _wrong_answer_without_work(material):
            system += "\n\n" + BARE_ANSWER_OPERATION_REVIEW
    response = request(reviewer, request_index, [
        {"role": "system", "content": system}, {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}
    ], schema)
    entry["review_response"] = response
    if response["finish_reason"] != "stop":
        return None, "disclosure_review_incomplete"
    try:
        assessment = read_teacher_submission(response)
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
                           task_analysis=False, quoted_premises=False, strict_author_decisions=False,
                           check_arithmetic=False, bare_answer_operations=False):
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
        if check_arithmetic and source.get("source_reasoning") and source.get("gold"):
            material["private_source_reference"] = {
                "answer_key": source["gold"],
                "attributed_source_reasoning": source["source_reasoning"],
                "use": ("Compare the next inference with the original question. The supplied solution is a "
                        "reference, not proof that every statement is sound. Use the smallest supported "
                        "intermediate move. Keep the key and the remaining solution private."),
            }
        if check_arithmetic and attempt["student"]["content"].strip():
            material["current_difficulty"]["student_wrote_a_response"] = True
            if not attempt["check"]["format_ok"]:
                material["current_difficulty"]["answer_format_issue"] = attempt["check"].get("issue")
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
    if (bare_answer_operations and _wrong_answer_without_work(material)
            and any(step["kind"] == "operation" for step in options.values())):
        author_system += "\n\n" + BARE_ANSWER_OPERATION_AUTHOR
    if strict_author_decisions:
        draft_schema = disclosure_draft_decision_schema(grounded=grounded, step_ids=options)
    else:
        draft_schema = verified_choice_schema(options) if options else disclosure_author_schema(grounded=grounded)
    for revision in range(2):
        entry = {"revision": revision, "private_reference": reference["text"], "latest_disclosure_id": previous_id}
        history.append(entry)
        if revision and check_arithmetic and quoted_premises:
            choices = question_premise_choices(source["question"])
            if choices:
                material["requested_revision"]["verbatim_question_choices"] = choices
                draft_schema = disclosure_draft_decision_schema(grounded=grounded, step_ids=options, premise_choices=choices)
        role = "disclosure_rewrite" if revision else "disclosure_draft"
        draft = request(role, attempt["attempt"], [
            {"role": "system", "content": author_system},
            {"role": "user", "content": json.dumps(material, ensure_ascii=False)},
        ], draft_schema)
        entry["draft_response"] = draft
        if draft["finish_reason"] != "stop":
            entry["issue"] = "disclosure_draft_incomplete"
            return None, entry["issue"]
        try:
            proposal = read_teacher_submission(draft)
            entry["draft"] = proposal
            if not isinstance(proposal, dict):
                raise ValueError("invalid_disclosure_draft")
            step = checked_step_selection(proposal, options) if options else None
            if options and proposal.get("decision") == "insufficient":
                proposal = {"bridge": "", "hint": "", "question_quote": "", **proposal}
            if quoted_premises and not step and proposal.get("decision") == "offer":
                premise = locate_teacher_premise(source["question"], proposal.get("question_quote"))
                if isinstance(proposal.get("hint"), str) and proposal["hint"].strip() == proposal["question_quote"]:
                    raise ValueError("teacher_hint_only_repeats_task_premise")
                entry["question_premise"] = premise
                proposal = {**proposal, "question_quote": premise["quote"]}
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
                if check_arithmetic and (issue := _checked_disclosure_issue(entry, source["question"])):
                    raise ValueError(issue)
        except (TypeError, ValueError) as exc:
            entry["issue"] = str(exc)
        else:
            if step:
                approved, issue = _review_verified_step(request, attempt, history, material, evidence, previous_id,
                                                        reference, catalog["receipt_id"], step, paired=paired,
                                                        task_analysis=task_analysis, check_arithmetic=check_arithmetic,
                                                        bare_answer_operations=bare_answer_operations)
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
                assessment = read_teacher_submission(response)
                entry["assessment"] = assessment
                checked = checked_reviewer_pairing(assessment, previous_id) if paired else assessment
                approved = checked_disclosure(checked, evidence, previous_id, candidate, source["gold"],
                                              quoted_premises=quoted_premises)
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
        if entry.get("arithmetic_errors"):
            material["requested_revision"]["arithmetic_errors"] = entry["arithmetic_errors"]
        if entry.get("quantity_findings"):
            material["requested_revision"]["quantity_findings"] = entry["quantity_findings"]


def _checked_disclosure_issue(entry, question):
    quoted = [q["public_span"] for q in entry["question_quotations"]]
    errors = asserted_arithmetic_errors(entry["public_text"], question=question, quoted_spans=quoted)
    quantities = storage_quantity_findings(entry["public_text"], question=question, quoted_spans=quoted)
    if quantities:
        entry["quantity_findings"] = quantities
    if errors:
        entry["arithmetic_errors"] = errors
        return "teacher_disclosure_arithmetic_contradiction"
    if quantities:
        return "teacher_disclosure_" + storage_quantity_issue(quantities)
    return None


def review_or_recommend_disclosure(request, source, attempt, retained, findings, disclosed, reference,
                                   *, teacher_experience, tried, task_analysis=False, quoted_premises=False,
                                   strict_author_decisions=False, check_arithmetic=False, bare_answer_operations=False):
    """Try each remaining externally verified possibility at most once, still requiring independent release."""
    if attempt.get("recommend_verified_operation"):
        history = attempt["disclosure_work"] = []
        issue = "no_new_verified_student_progress"
    else:
        approved, issue = author_next_disclosure(
            request, source, attempt, retained, findings, disclosed, reference, teacher_experience,
            grounded=True, allow_catalog=True, show_outcome=True, paired=True, tried=tried, task_analysis=task_analysis,
            quoted_premises=quoted_premises, strict_author_decisions=strict_author_decisions, check_arithmetic=check_arithmetic,
            bare_answer_operations=bare_answer_operations,
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
                                                recommended_index=ordinal, task_analysis=task_analysis,
                                                check_arithmetic=check_arithmetic, bare_answer_operations=bare_answer_operations)
        if approved:
            return approved, None
        entry["issue"] = issue
        if issue == "disclosure_review_incomplete":
            return None, issue
    return None, "verified_teaching_operations_exhausted"
