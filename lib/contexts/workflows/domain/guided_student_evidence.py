"""Bind a judge's findings and independently grounded approaches to the student's original draft."""

from collections import Counter
import re

from .guided_answers import contains_chat_control
from .guided_disclosure import is_substantive_question_quote


_PRIVATE_REVIEW_REFERENCE = re.compile(
    r"\b(?:hidden[ _]answer|answer[ _]key|system[ _]prompt|reviewer[ _]instructions)\b"
    r"|私有金标|内部评审指令|系统提示词", re.IGNORECASE,
)


def checked_student_approaches(proposals, segments, first_unresolved_id, question):
    """Locate judge-certified student methods after a defect. Semantic support comes from the judge."""
    if not isinstance(proposals, list) or not isinstance(question, str):
        return {}
    positions = {piece["id"]: index for index, piece in enumerate(segments)}
    if first_unresolved_id not in positions:
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
        if subjects[sid] != 1 or positions.get(sid, -1) <= positions[first_unresolved_id]:
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
