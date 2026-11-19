"""Bind a judge's findings and independently grounded approaches to the student's original draft."""

from collections import Counter
import re

from .guided_answers import contains_chat_control, shared_findings
from .experience_evolution import digest
from .guided_disclosure import is_substantive_question_quote


_PRIVATE_REVIEW_REFERENCE = re.compile(
    r"\b(?:hidden[ _]answer|answer[ _]key|system[ _]prompt|reviewer[ _]instructions)\b"
    r"|私有金标|内部评审指令|系统提示词", re.IGNORECASE,
)


def partial_review_evidence(proposed, segments, existing_prefix, anchor_count, question):
    """A revision location identifies where to work; only a grounded claim certifies a new step."""
    count = min(len(existing_prefix), anchor_count)
    found = shared_findings(proposed, segments, segments[:count], question, exclude_list_markers=True)
    if any(item["evidence_level"] == "verbatim_question_fact" for item in found.values()):
        # A substring can reverse the premise ("five jobs" inside "not five jobs").
        # Preserve the whole source assertion and its qualifications.
        clauses = {part.strip().rstrip(".。").strip() for part in re.split(r"(?<=[。！？])|(?<=[.!?])\s+|\n", question)}
        found = {sid: item for sid, item in found.items() if item["evidence_level"] != "verbatim_question_fact"
                 or (item["text"].strip().rstrip(".。").strip() in clauses and is_substantive_question_quote(item["text"]))}
    # The prefix is contiguous. A useful finding beyond a gap remains independently usable.
    while count < anchor_count and segments[count]["id"] in found:
        count += 1
    return count, found


def checked_derivation_conflict(proposal, review, current_segments, question, worked_solution):
    """Bind an independent reviewer's conflict to both texts; semantic judgment belongs to that reviewer."""
    if not isinstance(proposal, list) or len(proposal) != 1 or review["verdict"] != "revise":
        return None
    subject = next((s for s in current_segments if s["id"] == review["first_unresolved_id"]), None)
    item = proposal[0]
    if not subject or not isinstance(item, dict) or set(item) != {"student_quote", "source", "premise_quote", "incompatibility"}:
        return None
    if not all(isinstance(v, str) and v.strip() for v in item.values()):
        return None
    source = {"question": question, "worked_solution": worked_solution}.get(item["source"], "")
    quote, premise = item["student_quote"], item["premise_quote"]
    if (not is_substantive_question_quote(quote) or not is_substantive_question_quote(premise)
            or quote.casefold().strip(".。 ") == premise.casefold().strip(".。 ")
            or subject["text"].count(quote) != 1 or source.count(premise) != 1):
        return None
    offset, origin = subject["start"] + subject["text"].index(quote), source.index(premise)
    return {**item, "segment_id": subject["id"], "student_span": [offset, offset + len(quote)],
            "premise_span": [origin, origin + len(premise)], "verification": "reviewer_paired_verbatim_contradiction"}


def verified_student_stage(check, findings, prior_attempts, hints_received, *, supported=False, direct=False,
                           approaches=None, unresolved_evidence=False, answer_shown=False):
    """Describe observed student progress independently of how many clues were issued."""
    facts = {}
    for item in findings.values():
        kind = item["evidence_level"]
        if kind == "program_checked_reviewed_finite_set":
            key = "check:" + item["check_id"]
        elif kind == "program_checked_arithmetic_identity":
            # Whitespace and typography do not make a repeated calculation new.
            claim = re.sub(r"\s+", "", item["text"]).strip(".。").replace("×", "*").replace("÷", "/").replace("−", "-")
            key = "arithmetic:" + digest(claim)
        else:
            continue
        facts[key] = item["segment_id"]
    prior = [a["verified_stage"] for a in prior_attempts if a.get("verified_stage", {}).get("assessed")]
    seen = {key for earlier in prior for key in earlier["verified_fact_ids"]}
    # The same grounded premise can be paraphrased indefinitely. Count a new
    # grounding once; a changed approach using it may still be useful to the learner.
    methods = {digest(item["question_span"]): item["segment_id"] for item in (approaches or {}).values()}
    method_claims = {digest([item["question_span"], " ".join(item["text"].split())])
                     for item in (approaches or {}).values()}
    seen_methods = {key for earlier in prior for key in earlier.get("grounded_method_ids", ())}
    new_methods = sorted(methods.keys() - seen_methods)
    seen_claims = {key for earlier in prior for key in earlier.get("grounded_method_claims", ())}
    changed_method = bool(method_claims - seen_claims) and not new_methods
    unresolved_evidence = unresolved_evidence or changed_method
    match = check.get("answer_matches") if check.get("format_ok") else None
    complete = bool(supported and match is True)
    fresh = sorted(facts.keys() - seen)
    new_match = not answer_shown and match is True and not any(a["answer_key_match"] is True for a in prior)
    new_solution = complete and not any(a["complete_student_solution"] for a in prior)
    state = ("direct_key_match" if direct and match is True else "reviewed_solution" if complete
             else "key_match_only" if match is True else "verified_partial" if facts else
             "grounded_method" if methods else "unresolved")
    return {"assessed": True, "comparable": not unresolved_evidence and (match is not None or bool(facts or methods)), "state": state,
            "answer_key_match": match, "reference_answer_was_visible": answer_shown, "complete_student_solution": complete,
            "hints_received": hints_received, "verified_fact_ids": sorted(facts), "new_fact_ids": fresh,
            "new_fact_segments": [facts[key] for key in fresh],
            "grounded_method_ids": sorted(methods), "grounded_method_claims": sorted(method_claims),
            "new_method_segments": [methods[key] for key in new_methods],
            "unresolved_review_evidence": unresolved_evidence,
            "student_advanced": bool(fresh or new_methods or new_match or new_solution)}


def checked_student_approaches(proposals, segments, first_unresolved_id, question, *, independent_positions=False):
    """Locate judge-certified student methods after a defect. Semantic support comes from the judge."""
    if not isinstance(proposals, list) or not isinstance(question, str):
        return {}
    positions = {piece["id"]: index for index, piece in enumerate(segments)}
    if not independent_positions and first_unresolved_id not in positions:
        return {}
    subjects = Counter(
        item["segment_id"] for item in proposals
        if isinstance(item, dict) and isinstance(item.get("segment_id"), str)
    )
    accepted = {}
    for item in proposals:
        if (not isinstance(item, dict) or set(item) != {"segment_id", "question_quote", "justification"}
                or not all(isinstance(value, str) and value.strip() for value in item.values())):
            continue
        sid, quote, reason = item["segment_id"], item["question_quote"], item["justification"]
        if subjects[sid] != 1 or sid not in positions or sid == first_unresolved_id:
            continue
        if not independent_positions and positions[sid] <= positions[first_unresolved_id]:
            continue
        piece = segments[positions[sid]]
        text, start, end = piece.get("text"), piece.get("start"), piece.get("end")
        if (piece.get("author") != "student" or not isinstance(text, str) or not text.strip()
                or type(start) is not int or type(end) is not int or start < 0 or end - start != len(text)):
            continue
        if any(contains_chat_control(value) or _PRIVATE_REVIEW_REFERENCE.search(value)
               for value in (text, quote, reason)):
            continue
        first = question.find(quote)
        if first == -1 or question.find(quote, first + 1) != -1 or not is_substantive_question_quote(quote):
            continue
        accepted[sid] = {
            "segment_id": sid, "author": "student", "text": text, "source_span": [start, end],
            "question_quote": quote, "question_span": [first, first + len(quote)],
            "evidence_level": "judge_question_grounded_approach",
        }
    return accepted


def normalize_student_findings(findings, segment_ids):
    positions = {segment: position for position, segment in enumerate(segment_ids, 1)}
    normalized, issues, seen = [], [], set()
    if not isinstance(findings, list):
        return [], [{"issue": "expected_findings_array"}]
    for item_index, item in enumerate(findings):
        segment = item.get("segment_id") if isinstance(item, dict) else None
        issue = None
        if not isinstance(item, dict) or set(item) != {"segment_id", "basis_id", "depends_on"}:
            issue = "invalid_finding_fields"
        elif not isinstance(segment, str) or segment not in positions or segment in seen:
            issue = "unknown_or_repeated_finding"
        else:
            seen.add(segment)
            current = positions[segment]
            basis, dependencies = item["basis_id"], item["depends_on"]
            root = isinstance(basis, str) and basis in {"question", "arithmetic"}
            if not root and not (type(basis) is int and 1 <= basis < current):
                issue = "basis_must_be_an_independent_root_or_strictly_earlier_position"
            elif (not isinstance(dependencies, list)
                  or any(type(ref) is not int or not 1 <= ref < current for ref in dependencies)):
                issue = "dependencies_must_be_strictly_earlier_positions"
            else:
                normalized.append({"segment_id": segment, "basis_id": basis if root else segment_ids[basis - 1],
                                   "depends_on": [segment_ids[ref - 1] for ref in dependencies]})
        if issue:
            issues.append({"finding_index": item_index, "segment_id": segment if isinstance(segment, str) else None,
                           "issue": issue})
    return normalized, issues
