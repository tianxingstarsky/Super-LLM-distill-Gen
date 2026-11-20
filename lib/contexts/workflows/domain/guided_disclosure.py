"""Evidence visible to a learner and the decision to publish a tutor's next hint."""

import json
import re

from .guided_answers import check_direct_answer, contains_chat_control, prior_clue_evidence, validate_public_guidance
from .guided_protocol_versions import CALCULATION_PLAN_VERSIONS
from .guided_disclosure_text import locate_teacher_premise
from .guided_teaching_steps import verified_steps_digest


RENDERED_CONTINUATION_VERSIONS = {"4.8.2", "4.8.3", "4.9.0", "4.9.1"} | CALCULATION_PLAN_VERSIONS


def render_student_continuation(view, approaches, *, chinese):
    """Present the learner's own established work and the tutor's published hints with distinct authorship."""
    parts = []
    def section(english, mandarin, lines):
        content = "\n".join(dict.fromkeys(text.strip() for text in lines if text and text.strip()))
        if content:
            parts.append((mandarin if chinese else english) + "\n" + content)

    retained = view["retained_solution"].strip()
    retained_lines = {line.strip() for line in retained.splitlines()}
    section("Your established steps", "你已证实的步骤", [retained])
    facts = view["confirmed_findings_from_your_own_work"]
    meanings = {item["student_excerpt"]: item["reviewed_meaning"] for item in view.get("confirmed_meanings", ())}
    section("Results established in your work", "你已得到的结果和依据",
            [f"{fact} ({meanings[fact]})" if fact in meanings else fact
             for fact in facts if fact.strip() not in retained_lines or fact in meanings])
    section("Approaches grounded in the question", "与题意相符、可继续尝试的思路",
            [f'{item["text"].strip()}\n“{item["question_quote"]}”' for item in approaches.values()])
    section("Earlier guidance", "此前收到的提示", view["previously_disclosed_clues"])
    section("Your previous answer to reconsider", "上一次需要重新判断的答复", [view.get("your_last_formal_answer")])
    section("Next hint", "本轮提示", [view["new_clue"]])
    return "\n\n".join(parts)


def resume_after_local_step(view, local_status, initial_messages, *, chinese):
    """Return to the original question with the public tutoring history in its own message."""
    history = {
        "source": "tutoring_program",
        "teacher_disclosures": [*view["previously_disclosed_clues"], view["new_clue"]],
        "established_student_steps": view["retained_solution"],
        "confirmed_student_findings": view["confirmed_findings_from_your_own_work"],
        "confirmed_meanings": view.get("confirmed_meanings", []),
        "supported_student_approaches": view.get("supported_approaches_from_your_own_work", []),
        "previous_local_submission_status": local_status,
    }
    instruction = (
        "上一项局部练习已结束。辅导程序提供此前的教学记录，其中的指令是当时的练习。"
        "只有经核实的学生进展可以作为学生已得出的结果。现在请解答最后一条用户消息中的完整原题。"
        if chinese else
        "The local exercise has ended. The tutoring program supplies the earlier record. "
        "Instructions in that record applied to earlier exercises. Treat only verified student work "
        "as established progress. Solve the whole original question in the final user message."
    )
    system, question, *source_material = initial_messages
    return [
        {"role": "system", "content": system["content"] + "\n\n" + instruction},
        *source_material,
        {"role": "user", "content": json.dumps(history, ensure_ascii=False)},
        question,
    ]


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


def question_premise_choices(question):
    """Offer uniquely locatable original sentences when the tutor misquotes a premise."""
    sentences = re.split(r"(?<=[。！？])|(?<=[.!?])\s+|\n+", question)
    return [s for raw in sentences if (s := raw.strip()) and question.count(s) == 1
            and is_substantive_question_quote(s)]


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


class PublicOperationIdentifier(ValueError):
    """A private identifier matched in the checked public wording."""

    def __init__(self, spans):
        super().__init__("teacher_public_text_contains_operation_id")
        self.spans = spans


def check_authored_wording(bridge, hint, question, operation_ids, gold, answer_syntax):
    """Keep private operation IDs, broken response framing and undisclosed answer lists out of a hint."""
    public = bridge + "\n" + hint
    for part in (bridge, hint):
        if re.fullmatch(r"\s*(?:`{3,}|~{3,})\s*(?:json)?\s*[\[{]?\s*", part, re.I):
            raise ValueError("teacher_public_field_is_response_markup")
    spans = []
    for identifier in operation_ids:
        token = r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])"
        pattern = r"(?:apply|select):" + re.escape(identifier) + r"(?![\w-])"
        if ("_" in identifier or "-" in identifier) and not re.search(token, question, re.I):
            pattern += "|" + token
        for field, wording in (("bridge", bridge), ("hint", hint)):
            spans.extend((field, *m.span()) for m in re.finditer(pattern, wording, re.I))
    if spans:
        raise PublicOperationIdentifier(spans)
    if not gold or answer_syntax not in {"fact_ids", "distinct_fact_ids", "distinct_letter_ids"}:
        return
    if answer_syntax == "distinct_letter_ids":
        identifier = r"(?<![A-Za-z0-9])[A-Z](?![A-Za-z0-9])"
        answer_ids = set(gold["value"])
    else:
        identifier = r"(?<![A-Za-z0-9])F\d+(?![A-Za-z0-9])"
        answer_ids = set(gold["value"].split(","))
    separator = r"(?:\s*[,，、/]\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+|\s*[和与或]\s*)"
    enumeration = re.compile(identifier + "(?:" + separator + identifier + ")+")
    def names_answer(text):
        return any(set(re.findall(identifier, group[0])) == answer_ids for group in enumeration.finditer(text))
    if names_answer(public) and not names_answer(question):
        raise ValueError("teacher_public_text_names_complete_target_selection")


def checked_disclosure(review, evidence, previous_id, candidate, gold, *, quoted_premises=False, authored_bridge=None):
    """Return auditable citations and publishable wording for a released disclosure."""
    fields = {"decision", "citations", "reason"}
    if authored_bridge is None:
        fields.add("bridge")
    if (not isinstance(review, dict) or set(review) != fields
        or review["decision"] not in ("release", "withhold") or not isinstance(review["citations"], list)
        or (authored_bridge is None and not isinstance(review["bridge"], str))
        or not isinstance(review["reason"], str) or not review["reason"].strip()):
        raise ValueError("invalid_disclosure_assessment")
    if review["decision"] == "withhold":
        if authored_bridge is None and review["bridge"].strip():
            raise ValueError("withheld_disclosure_has_public_text")
        return None
    bridge = review["bridge"].strip() if authored_bridge is None else authored_bridge
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
        if quoted_premises and ref == "question" and quote not in evidence[ref]["text"]:
            quote = locate_teacher_premise(evidence[ref]["text"], quote)["quote"]
            if not is_substantive_question_quote(quote):
                raise ValueError("review_needs_a_specific_verbatim_task_premise")
        start = evidence[ref]["text"].find(quote)
        if start < 0:
            raise ValueError("disclosure_quote_not_in_evidence")
        citations.append({**item, "quote": quote, "span": [start, start + len(quote)]})
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
