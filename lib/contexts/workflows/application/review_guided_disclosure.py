"""Review a tutor's proposed hint before exposing it to the learner."""

import json

from lib.prompts.workflow_guided_answers import (
    AUTHORED_DISCLOSURE_REVIEW, DISCLOSURE_AUTHOR, DISCLOSURE_REVIEW, GROUNDED_AUTHOR_GUIDANCE,
    GROUNDED_HINT_REVIEW, disclosure_author_schema,
    disclosure_review_schema,
)
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


def author_next_disclosure(request, source, attempt, retained, findings, disclosed, reference, teacher_experience,
                           *, grounded=False):
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
    author_system += "\n\n" + teacher_experience if teacher_experience else ""
    for revision in range(2):
        entry = {"revision": revision, "private_reference": reference["text"], "latest_disclosure_id": previous_id}
        history.append(entry)
        role = "disclosure_rewrite" if revision else "disclosure_draft"
        draft = request(role, attempt["attempt"], [
            {"role": "system", "content": author_system},
            {"role": "user", "content": json.dumps(material, ensure_ascii=False)},
        ], disclosure_author_schema(grounded=grounded))
        entry["draft_response"] = draft
        if draft["finish_reason"] != "stop":
            entry["issue"] = "disclosure_draft_incomplete"
            return None, entry["issue"]
        try:
            proposal = json.loads(draft["content"])
            entry["draft"] = proposal
            wording = checked_teacher_draft(proposal, source["gold"], retained, findings, disclosed,
                                            question=source["question"] if grounded else None)
            if wording is None:
                entry["issue"] = "teacher_needs_more_evidence"
                return None, entry["issue"]
        except (TypeError, ValueError) as exc:
            entry["issue"] = str(exc)
        else:
            bridge, hint = wording
            prior_review = history[0].get("assessment")
            if grounded and revision and isinstance(prior_review, dict) and prior_review.get("decision") == "withhold":
                previous = history[0]["draft"]
                if all(" ".join(proposal[k].split()) == " ".join(previous[k].split()) for k in ("hint", "bridge")):
                    entry["reused_prior_review_revision"] = history[0]["revision"]
                    entry["issue"] = "unchanged_withheld_disclosure"
                    return None, entry["issue"]
            candidate = {**reference, "source": "teacher_authored", "text": hint}
            packet = {k: v for k, v in material.items() if k != "requested_revision"}
            packet["proposed_next_teacher_disclosure"] = {"kind": reference["kind"], "bridge": bridge, "hint": hint}
            if grounded:
                packet["proposed_next_teacher_disclosure"]["question_quote"] = proposal["question_quote"]
            reviewer = "disclosure_recheck" if revision else "disclosure"
            response = request(reviewer, attempt["attempt"], [
                {"role": "system", "content": reviewer_system},
                {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
            ], disclosure_review_schema(list(evidence)))
            entry["review_response"] = response
            if response["finish_reason"] != "stop":
                entry["issue"] = "disclosure_review_incomplete"
                return None, entry["issue"]
            try:
                assessment = json.loads(response["content"])
                entry["assessment"] = assessment
                approved = checked_disclosure(assessment, evidence, previous_id, candidate, source["gold"])
                if approved is None:
                    raise ValueError("teacher_disclosure_withheld")
                if grounded and not any(c["evidence_id"] == "question" and is_substantive_question_quote(c["quote"])
                                        for c in approved["citations"]):
                    raise ValueError("review_needs_a_specific_verbatim_task_premise")
                if approved["bridge"] != bridge:
                    raise ValueError("reviewer_changed_the_authored_bridge")
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
