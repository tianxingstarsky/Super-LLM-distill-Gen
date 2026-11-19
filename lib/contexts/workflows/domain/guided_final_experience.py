"""Bind a teaching observation to the actual proposed final answer and its frozen request."""

import hashlib
import json
import re

from .experience_evolution import digest, validate_experience_text, validate_identifier
from .guided_answers import number


FINAL_TEACHER_ROLE = "final_answer_teacher"
FINAL_EXPERIENCE_VERSIONS = frozenset({"4.5.1"})


def text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def recorded_final_proposal(case, attempt, receipt):
    if type(attempt) is not int or not 1 <= attempt <= len(case["attempts"]):
        raise ValueError("Select the actual teacher delivery round")
    if not isinstance(receipt, dict):
        raise ValueError("The teacher final-answer request receipt is required")
    item = case["attempts"][attempt - 1]
    delivery = item.get("teacher_final_answer") or {}
    request = receipt.get("request", {})
    identity = digest({"binding": case["binding"], "id": case["id"], "payload": request})
    response = receipt.get("response") or {}
    users = [m.get("content") for m in request.get("messages", []) if m.get("role") == "user"]
    systems = [m.get("content") for m in request.get("messages", []) if m.get("role") == "system"]
    materials = [json.loads(m["content"]) for m in request.get("messages", []) if m.get("role") == "tool"]
    if (receipt.get("status") != "complete" or receipt.get("role") != "teacher_final_answer"
            or receipt.get("attempt") != attempt or item.get("attempt") != attempt
            or receipt.get("identity") != identity or response != delivery.get("response")
            or response.get("finish_reason") != "stop" or users != [case["question"]]
            or len(systems) != 1 or not isinstance(systems[0], str)
            or (case.get("task_system") and not systems[0].endswith("\n\nOriginal task system instruction:\n" + case["task_system"]))
            or (not case.get("task_system") and "\n\nOriginal task system instruction:\n" in systems[0])
            or len(materials) != 1 or not isinstance(materials[0], dict)
            or materials[0].get("source") != "workflow_verified_task_evidence"
            or materials[0].get("independent_reference_key") != case.get("gold")):
        raise ValueError("The teacher's completed request, case and response must agree")
    proposed = json.loads(response["content"])
    if (not isinstance(proposed, dict) or proposed.get("decision") != "deliver"
            or not isinstance(proposed.get("explanation"), str) or not isinstance(proposed.get("conclusion"), str)):
        raise ValueError("No complete teacher answer was proposed")
    visible = "\n\n".join(s.strip() for s in (proposed["explanation"], proposed["conclusion"]) if s.strip())
    if not visible or delivery.get("public_answer") not in ("", visible):
        raise ValueError("The proposed teacher answer differs from the case archive")
    binding = {"case_id": case["id"], "attempt": attempt, "source_binding": case["binding"],
               "request_identity": identity, "public_answer_sha256": text_sha(visible)}
    return visible, proposed, binding


def final_delivery_observation(case, attempt, receipt, assessment, *, replay_verifier=None):
    visible, proposal, binding = recorded_final_proposal(case, attempt, receipt)
    if not isinstance(assessment, dict) or any(assessment.get(k) != v for k, v in binding.items()):
        raise ValueError("Assessment must identify this exact case, teacher request and proposed text")
    if assessment.get("proposed_answer", visible) != visible:
        raise ValueError("The displayed answer in the assessment was altered")
    kind = assessment.get("kind")
    if kind == "human":
        decision = assessment
    elif kind == "wrong_final_value":
        # Independently compare two explicit numeric values. This can prove an error only.
        gold = case.get("gold") or {}
        value = number(proposal["conclusion"])
        if gold.get("kind") != "number" or value is None or value == number(gold["value"]):
            raise ValueError("The independent numerical mismatch could not be reproduced")
        decision = {"outcome": "failure", "quote": proposal["conclusion"].strip(),
                    "reason": "The numerical value in the proposed public conclusion differs from the independent answer key."}
    elif (kind == "independent" and replay_verifier is not None
          and isinstance(assessment.get("checker"), str) and assessment["checker"]):
        # The operator supplies the checker out of band; the observation file cannot execute code.
        decision = replay_verifier({"case_id": case["id"], "question": case["question"],
                                    "task_system": case.get("task_system", ""), "gold": case.get("gold")}, visible)
        if (not isinstance(decision, dict) or decision.get("case_id") != case["id"]
                or decision.get("public_answer_sha256") != binding["public_answer_sha256"]
                or decision.get("checker") != assessment["checker"]
                or not isinstance(decision.get("checker_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", decision["checker_sha256"])
                or decision["checker_sha256"] != assessment.get("checker_sha256")):
            raise ValueError("The independent verdict does not describe this checker, case and proposal")
    else:
        raise ValueError("Use a human assessment or replay an independent check of the public answer")
    if (not isinstance(decision, dict) or decision.get("outcome") not in ("success", "failure")
            or not isinstance(decision.get("reason"), str) or not decision["reason"].strip()
            or not isinstance(decision.get("quote"), str) or not decision["quote"].strip()
            or decision["quote"] not in visible):
        raise ValueError("Assessment requires a decision, reason and exact quotation from the teacher's answer")
    if decision["outcome"] == "success" and decision.get("scope") != "full_answer":
        raise ValueError("A checked conclusion alone does not establish a successful full answer")
    return {"id": f"{case['id']}-teacher-final-{attempt}", "guided_final_projection_version": 1,
            "final_assessment": {**binding, "kind": kind, "outcome": decision["outcome"],
                                 "quote": decision["quote"], "reason": decision["reason"],
                                 "scope": decision.get("scope", "observed_defect"),
                                 **({"checker": assessment["checker"], "checker_sha256": assessment.get("checker_sha256")}
                                    if kind == "independent" else {})},
            "conversation": {"messages": [*([{"role": "system", "content": case["task_system"]}] if case.get("task_system") else []),
                {"role": "user", "content": case["question"]},
                {"role": "assistant", "content": visible, "reasoning_content": receipt["response"].get("reasoning_content", "")}]},
            "review_observation": {"verdict": {"keep": decision["outcome"] == "success",
                "reason": decision["reason"] + "\nTeacher's actual words: " + decision["quote"]}}}


def final_teacher_edition(edition):
    episodes = edition.get("episodes") or []
    if (edition.get("scope") != "reasoning" or not episodes
            or not all(e.get("guided_final_projection_version") == 1 for e in episodes)):
        raise ValueError("Select an edition learned from reviewed teacher final answers")
    return edition


def is_final_teacher_edition(edition):
    return any(isinstance(e, dict) and e.get("guided_final_projection_version") == 1
               for e in edition.get("episodes", []))


def adopted_final_teacher_text(experience, version):
    text = experience.get(FINAL_TEACHER_ROLE, "")
    if not text or version not in FINAL_EXPERIENCE_VERSIONS:
        return ""
    adoption = experience.get("final_answer_teacher_adoption") or {}
    if (not isinstance(adoption, dict) or adoption.get("role") != FINAL_TEACHER_ROLE
            or not isinstance(adoption.get("edition_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", adoption["edition_sha256"])
            or adoption.get("text_sha256") != text_sha(validate_experience_text(text))
            or adoption.get("status") != "adopted" or adoption.get("edition_id") != experience.get("version")):
        raise ValueError("Use an explicitly adopted final-answer teacher edition with this protocol")
    validate_identifier(adoption["edition_id"])
    return text
