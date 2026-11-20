"""Interpret student outcomes after delivered teaching actions and freeze transferable advice."""

import re

from ..domain.experience_evolution import digest
from ..domain.reasoning_bank import no_new_memory


def observed_followup(before, after, *, certified_value, exposed_answer, replayed_calculations=()):
    previous = before.get("verified_stage") or {}
    stage = after.get("verified_stage") or {}
    check = after["check"]
    facts = stage.get("new_fact_ids", [])
    methods = stage.get("new_method_segments", [])
    evidence = {
        "previous_key_match": previous.get("answer_key_match"),
        "next_key_match": stage.get("answer_key_match"),
        "published_answer_matches_key": certified_value,
        "new_checked_fact_ids": facts,
        "replayed_new_calculations": list(replayed_calculations),
        "new_method_segments_reviewer_only": methods,
        "reviewer_supported_derivation": bool(stage.get("complete_student_solution")),
        "unresolved_review_evidence": bool(stage.get("unresolved_review_evidence")),
        "issued_public_answer": check.get("answer_surface", check.get("answer")),
        "output_issue": check.get("issue"),
        "previous_chars": len(before["student"]["content"]),
        "next_chars": len(after["student"]["content"]),
    }
    if exposed_answer or after.get("solving_role") == "strong_continuation":
        return "assisted_or_changed_solver", evidence
    if after["student"].get("finish_reason") != "stop":
        return "incomplete_response", evidence
    if after.get("coach_feedback_check") or after.get("reviewed_derivation_conflict"):
        return "conflicting_review", evidence
    # The literal answer key can verify a delivered value, never a judge's semantic interpretation.
    if certified_value is False:
        return "wrong_delivered_value", evidence
    if certified_value is True and previous.get("answer_key_match") is not True:
        return "newly_delivered_key_match", evidence
    if replayed_calculations and not evidence["unresolved_review_evidence"]:
        return "new_program_checked_fact", evidence
    if methods:
        return "method_requires_independent_check", evidence
    if not check.get("format_ok") and not evidence["unresolved_review_evidence"]:
        return "no_checkable_delivery", evidence
    return "insufficient_evidence", evidence


OBSERVED_REWARDS = {"newly_delivered_key_match", "new_program_checked_fact", "wrong_delivered_value",
                    "no_checkable_delivery"}
POSITIVE_OUTCOMES = {"newly_delivered_key_match", "new_program_checked_fact"}
TEACHER_ACTION_VERSION = 3


def teacher_batch_episode(events):
    """Induce from one real action and its outcome, with separately attributed peer outcomes as context."""
    # The official single-trajectory algorithm needs an actual anchor. Other cases are separate
    # observations of the teaching policy, never fictitious turns in the anchor's task.
    anchor, *peers = sorted(events, key=lambda row: row["outcome"] in POSITIVE_OUTCOMES)
    def action(row):
        properties = row["action_properties"]
        words = ["The tutor selected a previously checked operation and sent it to the learner."
                 if row["author"] == "teacher_selected" else "The tutor wrote a new hint and sent it to the learner."]
        if properties["grounded_in_quoted_premise"]:
            words.append("The tutor's hint quoted a premise of the problem.")
        if properties["continues_a_previous_hint"]:
            words.append("The learner had already received an earlier hint.")
        if properties["cites_learner_result"]:
            words.append("A citation in this hint pointed to the learner's earlier work.")
        return " ".join(words)
    def outcome(row):
        return {"wrong_delivered_value": "The learner then submitted a definite answer. Its value disagreed with the task key.",
                "no_checkable_delivery": "The learner then wrote a response, but did not deliver a formal answer that could be verified.",
                "new_program_checked_fact": "The learner then supplied a new intermediate calculation checked against the task.",
                "newly_delivered_key_match": "The learner then delivered a clear answer matching the task key after previously lacking a verified match."}[row["outcome"]]
    guidance = ("You are helping a tutor decide how to write its next hint. What could that tutor try to help "
                "the learner work out and justify the next concrete step? Write advice to the tutor for "
                "its next conversation. The tutor sends a hint; the learner alone writes the subsequent "
                "answer. A result that follows a hint is an observation; the effect of changing the hint "
                "has not yet been tested. Other independent examples from the same teaching practice follow.\n")
    messages = [{"role": "user", "content": guidance + "\n".join(action(row) + " " + outcome(row) for row in peers)},
                {"role": "assistant", "content": action(anchor)},
                {"role": "tool", "content": outcome(anchor)}]
    return {"id": "teacher-followup-" + digest([e["id"] for e in events]),
            "guided_teacher_followup_projection_version": 1, "teacher_action_features_version": TEACHER_ACTION_VERSION,
            "conversation": {"messages": messages},
            "review_observation": {"verdict": {"keep": anchor["outcome"] in POSITIVE_OUTCOMES,
                "reason": outcome(anchor) + " Whether an alternative hint would help will be tested separately."}}}


def teacher_edition(repository, edition_id):
    entry = repository.get_version(edition_id)
    if no_new_memory(entry.get("text")):
        raise ValueError("no-update observation has no teacher advice to adopt")
    if not any(e.get("guided_teacher_followup_projection_version") == 1 for e in entry["episodes"]):
        raise ValueError("edition does not belong to the disclosure teacher's observed follow-ups")
    return entry


def transfer_findings(text, events):
    """Catch copied task-specific expressions and distinctive entities in candidate advice."""
    words = re.compile(r"[A-Za-z\u4e00-\u9fff]+|\d+(?:\.\d+)?|[^\w\s]")
    phrases = set()
    # These are private telemetry field names, not part of the tutor's instructions or conversation.
    for field in ("action_properties", "linked_to_verifiable_operation", "grounded_in_quoted_premise",
                  "teacher_authored", "reviewed_derivation_only"):
        if field in text:
            phrases.add(field)
    contents = [line.strip().casefold() for line in text.splitlines() if line.startswith("## Content")]
    if len(contents) != len(set(contents)):
        phrases.add("identical memory contents repeated")
    candidate = words.findall(text.casefold())
    for row in events:
        original = row["question"] + "\n" + row["hint"]
        for value in re.findall(r"(?<!\d)\d+\s*[/÷×]\s*\d+(?!\d)", original):
            if value in text:
                phrases.add(value)
        tokens = words.findall(original.casefold())
        groups = {tuple(tokens[i:i + 5]) for i in range(len(tokens) - 4)}
        for i in range(len(candidate) - 4):
            group = tuple(candidate[i:i + 5])
            if group in groups and any(t[0].isdigit() for t in group):
                phrases.add(" ".join(group))
    return sorted(phrases)


def freeze_teacher_experience(store, library, edition_id, base=None, *, trial=False):
    document = dict(base or {"version": "initial"})
    if edition_id == "initial":
        document.pop("disclosure_teacher", None)
        document.pop("disclosure_teacher_adoption", None)
    else:
        entry = teacher_edition(library, edition_id)
        events = store.edition_events(edition_id)
        if not events:
            raise ValueError("candidate source observations are unavailable")
        overlaps = transfer_findings(entry["text"], events)
        if overlaps:
            raise ValueError("candidate repeats source-specific expressions or induction scaffolding: " + ", ".join(overlaps))
        pairs = store.transfer_pairs(edition_id, entry["parent_id"] or "initial", {e["case_id"] for e in events}, entry["text"])
        gains = {p["id"] for p in pairs if p["comparable"] and not p["old_gain_supported"] and p["new_gain_supported"]}
        regressions = [p for p in pairs if p["comparable"] and p["old_gain_supported"] and not p["new_gain_supported"]]
        if not trial and (len(gains) < 2 or regressions or any(not p["comparable"] for p in pairs)):
            raise ValueError("complete held-out coverage and new gains on two cases required; --trial stages an experiment")
        document.update(disclosure_teacher=entry["text"], disclosure_teacher_adoption={
            "edition": entry["id"], "sha256": entry["sha256"], "parent": entry["parent_id"],
            "scope": "comparison_trial" if trial else "paired_learner_evidence", "comparison": pairs})
    document["version"] = edition_id
    return document


def learn_teacher_feedback(store, library, get_bank, save_result, *, min_new, batch_size, parent=None, reconsider=None):
    if not 1 <= min_new <= batch_size:
        raise ValueError("min_new must be positive and cannot exceed batch_size")
    with store.induction_lock():
        events = store.batch(batch_size)
        if reconsider:
            prior = teacher_edition(library, reconsider)
            previous = store.edition_events(reconsider)
            if (not previous or not transfer_findings(prior["text"], previous) or
                    prior["episodes"][0].get("teacher_action_features_version") == TEACHER_ACTION_VERSION):
                raise ValueError("reconsider requires a blocked prior edition and a materially new observation projection")
            events = list({e["id"]:e for e in previous + events}.values())[:batch_size]
        if len(events) < min_new:
            return {"status": "insufficient_new_evidence", "pending": len(events), "calls": []}
        if parent:
            # A rejected parent would otherwise reintroduce its invalid advice into the new model request.
            freeze_teacher_experience(store, library, parent, trial=True)
        learned = get_bank().learn("reasoning", teacher_batch_episode(events), parent_id=parent)
        result = {"status": learned["status"], "edition_id": learned.get("id"), "parent": parent,
                  "events": [e["id"] for e in events], "calls": learned["model_request_receipt"]}
        if learned.get("id"):
            result["source_overlap"] = transfer_findings(learned["text"], events)
            if result["source_overlap"]:
                result["status"] = "candidate_source_overlap"
        save_result(result)
        store.consumed(events, learned.get("id") or learned["result_sha256"])
        return result
