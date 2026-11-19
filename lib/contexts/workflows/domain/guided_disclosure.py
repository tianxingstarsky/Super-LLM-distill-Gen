"""Evidence visible to a learner and the decision to publish a tutor's next hint."""

import re

from .guided_answers import check_direct_answer, contains_chat_control, prior_clue_evidence, validate_public_guidance
from .guided_teaching_steps import verified_steps_digest


def disclosure_evidence(question, retained, findings, disclosed):
    evidence = {"question": {"kind": "question", "text": question}}
    for piece in retained:
        evidence[piece["id"]] = {"kind": "established_student_step", "text": piece["text"]}
    for finding in findings.values():
        sid = finding["segment_id"]
        # Preserve the full verified step if it already contains this checked excerpt.
        if sid not in evidence:
            evidence[sid] = {"kind": "established_student_finding", "text": finding["text"]}
    for hint in disclosed:
        evidence[f"disclosed.{hint['ordinal']}"] = {"kind": "published_teacher_hint", "text": hint["text"]}
    return evidence


def is_substantive_question_quote(quote):
    """Filter isolated labels and filler. Entailment still needs the independent reader."""
    tokens = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", quote.lower())
    content = [t for t in tokens if t not in {"a", "an", "the", "of", "to", "in", "on", "by", "is",
                                               "it", "are", "and", "or", "for", "as", "at", "with"}]
    return len(set(content)) >= 2 or bool(re.search(r"[A-Za-z0-9]\s*(?:[=<>≠≤≥→]|->)\s*[A-Za-z0-9]", quote))


def checked_teacher_draft(proposal, gold, retained, findings, disclosed, *, question=None):
    fields = {"decision", "bridge", "hint", "reason"}
    if question is not None:
        fields.add("question_quote")
    if (not isinstance(proposal, dict) or set(proposal) != fields
        or any(not isinstance(v, str) for v in proposal.values()) or not proposal["reason"].strip()
        or proposal["decision"] not in ("offer", "insufficient")):
        raise ValueError("invalid_disclosure_draft")
    if proposal["decision"] == "insufficient":
        if proposal["bridge"].strip() or proposal["hint"].strip() or proposal.get("question_quote", "").strip():
            raise ValueError("insufficient_disclosure_has_public_text")
        return None
    bridge, hint = proposal["bridge"].strip(), proposal["hint"].strip()
    if question is not None:
        quote = proposal["question_quote"].strip()
        if quote not in question or not is_substantive_question_quote(quote):
            raise ValueError("teacher_needs_a_specific_verbatim_task_premise")
        if hint == quote:
            raise ValueError("teacher_hint_only_repeats_task_premise")
    for visible in (bridge, hint):
        if not visible or contains_chat_control(visible):
            raise ValueError("missing_or_invalid_teacher_continuation")
        validate_public_guidance(visible, gold)
        if gold and check_direct_answer(visible, gold):
            raise ValueError("teacher_discloses_terminal_answer")
    if prior_clue_evidence({"text": hint}, retained, findings, disclosed):
        raise ValueError("teacher_repeats_established_or_disclosed_work")
    return bridge, hint


def checked_disclosure(review, evidence, previous_id, candidate, gold):
    """Return auditable citations and publishable wording for a released disclosure."""
    if (not isinstance(review, dict) or set(review) != {"decision", "citations", "bridge", "reason"}
        or review["decision"] not in ("release", "withhold") or not isinstance(review["citations"], list)
        or not isinstance(review["bridge"], str) or not isinstance(review["reason"], str) or not review["reason"].strip()):
        raise ValueError("invalid_disclosure_assessment")
    if review["decision"] == "withhold":
        if review["bridge"].strip():
            raise ValueError("withheld_disclosure_has_public_text")
        return None
    bridge = review["bridge"].strip()
    if not bridge or contains_chat_control(bridge) or bridge == candidate["text"].strip():
        raise ValueError("missing_or_invalid_disclosure_bridge")
    citations = []
    cited = set()
    for item in review["citations"]:
        if not isinstance(item, dict) or set(item) != {"evidence_id", "quote"}:
            raise ValueError("invalid_disclosure_citation")
        ref, quote = item["evidence_id"], item["quote"]
        if not isinstance(ref, str) or ref not in evidence or not isinstance(quote, str) or not re.search(r"\w", quote):
            raise ValueError("unknown_or_empty_disclosure_citation")
        start = evidence[ref]["text"].find(quote)
        if start < 0:
            raise ValueError("disclosure_quote_not_in_evidence")
        citations.append({**item, "span": [start, start + len(quote)]})
        cited.add(ref)
    if not any(evidence[ref]["kind"] != "published_teacher_hint" for ref in cited):
        raise ValueError("disclosure_needs_a_task_or_established_finding")
    if previous_id and previous_id not in cited:
        raise ValueError("disclosure_does_not_connect_to_last_hint")
    if bridge in (item["quote"].strip() for item in citations):
        raise ValueError("disclosure_bridge_only_repeats_evidence")
    validate_public_guidance(bridge, gold)
    if gold and check_direct_answer(bridge, gold):
        raise ValueError("disclosure_bridge_gives_the_answer")
    return {"text": bridge + "\n" + candidate["text"], "bridge": bridge,
            "candidate_text": candidate["text"], "citations": citations}
