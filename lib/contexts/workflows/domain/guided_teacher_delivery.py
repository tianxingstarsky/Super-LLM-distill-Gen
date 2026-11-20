"""Check the teacher's actual public answer against the reviewed task."""

import re
import unicodedata

from .guided_answers import answer_tail, check_direct_answer, contains_chat_control, inspect_formal_answer, number
from .guided_student_calculations import verified_equation_result

STRICT_DELIVERY_VERSIONS = frozenset({"4.8.1", "4.8.2", "4.8.3"})
CALCULATION_PLAN_VERSIONS = frozenset({"4.10.0", "4.10.1", "4.10.2", "4.10.3", "4.10.4", "4.10.5", "4.10.6", "4.10.7", "4.10.8", "4.10.9", "4.10.10"})
CHECKED_TUTOR_VERSIONS = frozenset({"4.9.1"}) | CALCULATION_PLAN_VERSIONS
SOLVER_FINAL_VERSIONS = frozenset({"4.9.0"}) | CHECKED_TUTOR_VERSIONS
EVIDENCED_PROGRESS_VERSIONS = frozenset({"4.8.0"}) | STRICT_DELIVERY_VERSIONS | SOLVER_FINAL_VERSIONS
QWEN_SAMPLING_VERSIONS = frozenset({"4.7.0", "4.7.1"}) | EVIDENCED_PROGRESS_VERSIONS
TRUNCATED_TUTORING_VERSIONS = frozenset({"4.6.0"}) | QWEN_SAMPLING_VERSIONS
TEACHER_REASONING_VERSIONS = frozenset({"4.5.0", "4.5.1"}) | TRUNCATED_TUTORING_VERSIONS
GUIDED_DELIVERY_VERSIONS = frozenset({"4.4.0", "4.4.1"}) | TEACHER_REASONING_VERSIONS
TEACHER_FINAL_VERSIONS = GUIDED_DELIVERY_VERSIONS - SOLVER_FINAL_VERSIONS

_QUANTITY = r"[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:/\d+|[eE][+-]?\d+)?"
_FIGURES = re.compile(r"(?<![\w.])(" + _QUANTITY + r")(?!\w|\.\d)", re.ASCII)
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
_COMMON = set("an the it is are was be to for of in on and or then this that therefore thus result answer "
              "final because so we they as from given get gives calculation following follows correct".split())
_RESULT_NOUN = r"(?:final\s+)?(?:numeric(?:al)?\s+)?(?:answer|verdict|result|order|number|integer|sequence|identifiers?|letters?|fact\s+ids?)"
_RESULT_ONLY = re.compile(
    r"\b"+_RESULT_NOUN+r"\s+(?:only|alone)\b|\b(?:only\s+(?:give|return|provide|output|write)|"
    r"(?:give|return|provide|output|write)\s+(?:only|just))\s+(?:(?:an?|the|your)\s+)?"+_RESULT_NOUN+r"\b|"
    r"(?:只(?:需|要)?|仅(?:需|要)?)(?:给出?|返回|输出|写)(?:相应|所求|最终|一个|正确|对应|数值|选项)*"
    r"(?:答案|结果|顺序|序列|编号|数字|字母|数值)|(?:不要|无需)(?:解释|给出过程)", re.I)
_EXPLAIN = re.compile(
    r"\b(?:show (?:why|how|your work|the (?:calculation|reasoning))|explain(?: only)? (?:why|how|your|the)|"
    r"justify (?:your|the)|give (?:a |your )?(?:proof|reasoning))\b|(?:说明|解释)(?:理由|原因)|给出(?:推导|证明|过程)", re.I)
_NO_EXPLANATION = re.compile(
    r"\b(?:(?:do not|don't)\s+(?:explain(?:\s+(?:your|the)\s+answer)?|show\s+your\s+work)|"
    r"(?:without (?:any |an? )?|no )(?:explanation|reasoning|working))\b", re.I)
_DECLARATION = re.compile(
    r"\b(?:the\s+)?(?:final\s+(?:answer|result|total|conclusion)|answer|verdict)\s*(?:is\b|equals\b|[:=])\s*"
    r"|(?:最终(?:答案|结果|结论|总数)|答案|判定)(?:为|是|等于|[:：=])\s*", re.I)
_PRIVATE = re.compile(r"\b(?:answer key|private reference|reviewed_student_steps|evidence_ids)\b|内部评语|参考答案键", re.I)


def requested_delivery(source):
    """Respect system instructions before the user, and their stated format before a dataset default."""
    for text in (source.get("task_system", ""), source["question"]):
        remaining, negative_count = _NO_EXPLANATION.subn("", text)
        if _EXPLAIN.search(remaining):
            return "worked_solution"
        if negative_count or _RESULT_ONLY.search(text):
            return "final_only"
    return source.get("answer_requirement", "worked_solution")


def uses_direct_first(source, protocol_version):
    return (source.get("answer_requirement") == "final_only"
            and (protocol_version not in GUIDED_DELIVERY_VERSIONS or requested_delivery(source) == "final_only"))


def _words(text):
    output = set()
    for word in _WORDS.findall(text.casefold()):
        if re.search(r"[\u3400-\u9fff]", word):
            output.update(word[i:i + 2] for i in range(len(word) - 1))
        elif len(word) > 1 and word not in _COMMON:
            output.add(word)
    return output


def _explicit_wrong_conclusion(explanation, gold):
    for hit in _DECLARATION.finditer(explanation):
        tail = explanation[hit.end():].lstrip(" $*" + chr(96))
        if gold["kind"] == "number":
            value = re.match(_QUANTITY, tail)
            if value and number(value[0]) != number(gold["value"]):
                return True
        else:
            expected = gold["value"]
            if expected.casefold() in {"true", "false", "unknown", "yes", "no"}:
                value = re.match(r"(?:true|false|unknown|yes|no)\b", tail, re.I)
            elif re.fullmatch(r"(?:[A-Z]+|F\d+(?:,F\d+)*)", expected):
                value = re.match(r"(?:F\d+(?:,F\d+)*|[A-Z]+)\b", tail)
            else:
                # Open-ended exact keys have no dependable inventory of opposite claims.
                continue
            if value and value[0].casefold() != expected.casefold():
                return True
    return False


def _check_surface(conclusion, source):
    gold = source["gold"]
    if not conclusion or len(conclusion.splitlines()) != 1:
        return None
    if source.get("direct_answer_syntax") and requested_delivery(source) == "final_only":
        if gold["kind"] == "number" and re.fullmatch(_QUANTITY, conclusion) is None:
            return None
        if gold["kind"] != "number" and conclusion != gold["value"]:
            return None
    if gold["kind"] == "number":
        # Permit a measured result such as "$12", "12 apples" or "3.5 m/s".
        # A source that says "in minutes" also gives a checkable public unit.
        unit_match = re.fullmatch(r"([$€£¥])\s*(" + _QUANTITY + r")|(" + _QUANTITY
                                  + r")\s*([^\W\d_]+(?:[²³]|[/.·][^\W\d_]+(?:[²³])?| per [^\W\d_]+)?|[%°])", conclusion)
        if unit_match:
            prefix, first, last, suffix = unit_match.groups()
            unit = prefix or suffix
            question = source["question"] + "\n" + source.get("task_system", "")
            requested_unit = re.search(r"\b(?:give (?:the |your )?(?:answer|result)|express (?:the |your )?(?:answer|result)|answer)\s+in\s+([a-zA-Z]+)\b"
                                       r"|以\s*([\u3400-\u9fffA-Za-z]+?)\s*为单位", question, re.I)
            if requested_unit:
                expected = next(x for x in requested_unit.groups() if x)
                known = {"$":"dollar", "€":"euro", "£":"pound", "%":"percent", "min":"minute", "h":"hour",
                         "kg":"kilogram", "g":"gram", "s":"second", "m":"meter", "km":"kilometer"}
                def name(text):
                    return known.get(text, text.casefold().removesuffix("s"))
                # Different literal spellings may be synonyms unknown to this verifier.
                familiar = set(known.values()) | {"mile", "cent", "liter", "foot", "inch", "day"}
                if name(expected) in familiar and name(unit) in familiar and name(expected) != name(unit):
                    return None
            checked = check_direct_answer(first or last, gold, include_mismatch=True)
            if checked:
                return {**checked, "visible_unit":unit, "unit_semantics_independently_checked":False}
    return check_direct_answer(conclusion, gold, include_mismatch=True)


def _discloses_private_evidence_id(public_text, question, reviewed_steps):
    """Only an identifier already visible in the user's question may be cited publicly."""
    public_text = unicodedata.normalize("NFKC", public_text).casefold()
    question = unicodedata.normalize("NFKC", question).casefold()
    for step_id in reviewed_steps:
        literal = unicodedata.normalize("NFKC", step_id).casefold()
        if not literal or literal not in public_text:
            continue
        # Segment identifiers are ASCII; Chinese words and sentence punctuation can touch them.
        # A qualified name or a longer numbered identifier is a different literal.
        token = re.compile(r"(?<![a-z0-9_])(?<![a-z0-9_][.-])" + re.escape(literal)
                           + r"(?![a-z0-9_]|[.-][a-z0-9_])")
        if token.search(public_text) and not token.search(question):
            return True
    return False


def inspect_solver_delivery(response, source, reviewed_steps, form):
    """Check the solver's unedited public continuation, including task-specific presentation."""
    def reject(issue, match=None):
        return dict(format_ok=False, answer=None, answer_matches=match, issue=issue)

    raw = response["content"]
    if response["finish_reason"] != "stop" or response.get("reasoning_content") or not raw.strip():
        return reject("formal_answer_incomplete_or_native_thinking")
    if contains_chat_control(raw):
        return reject("formal_answer_contains_control_markers")
    if _PRIVATE.search(raw) or _discloses_private_evidence_id(raw, source["question"], reviewed_steps):
        return reject("formal_answer_contains_private_material")
    text = raw.strip()
    if form == "final_only" and source.get("direct_answer_syntax"):
        return _check_surface(text, source) or reject("formal_answer_wrong_requested_syntax")
    start, conclusion = answer_tail(text)
    if start is None:
        explanation, _, conclusion = text.rpartition("\n")
        if form == "worked_solution" and not explanation:
            # A complete verified equation can itself be both working and result.
            value = verified_equation_result(text.rstrip(".。"))
            if value is not None and source["gold"]["kind"] == "number":
                return check_direct_answer(str(value), source["gold"], include_mismatch=True)
            boundary = re.search(r"\b(?:to get|which gives|which yields)\s+|(?:所以得到|因此得到)", text, re.I)
            if boundary:
                explanation, conclusion = text[:boundary.start()], text[boundary.end():]
    else:
        explanation = text[:start]
    check = _check_surface(conclusion.strip().rstrip(".。"), source)
    if check is None:
        check = inspect_formal_answer(response, source["gold"], source["question"], final_only=form == "final_only",
                                      require_explanation=form == "worked_solution")
    if not check["format_ok"] or not check["answer_matches"]:
        return check
    if form == "final_only":
        return reject("formal_answer_unrequested_explanation", True) if explanation.strip() else check
    evidence = "\n".join(reviewed_steps.values())
    figures = {number(m[1]) for m in _FIGURES.finditer(explanation)}
    known_figures = {number(m[1]) for m in _FIGURES.finditer(evidence + "\n" + source["question"])}
    # Evidence of an explanation is distinct from the keyed conclusion. Lexical
    # silence can mean a valid paraphrase, so leave it for review.
    if not explanation.strip() or check_direct_answer(explanation.strip(), source["gold"]):
        return reject("formal_answer_missing_requested_explanation", True)
    if not (_words(explanation) & _words(evidence) or figures & known_figures):
        return reject("formal_answer_explanation_needs_review", True)
    for clause in re.split(r"[\n。！？]|(?<=[.!?])\s+", explanation):
        claim = clause.strip()
        if _DECLARATION.match(claim) and _explicit_wrong_conclusion(claim, source["gold"]):
            return reject("formal_answer_conflicting_conclusion", True)
    return check


def inspect_teacher_delivery(proposed, source, reviewed_steps, form, *, protect_evidence_ids=False):
    """Validate both public fields before joining them into the actual training target."""
    def reject(issue, *, match=None):
        return {"format_ok": False, "answer": None, "answer_matches": match, "issue": issue}, ""

    fields = {"decision", "reason", "explanation", "conclusion", "evidence_ids"}
    if (not isinstance(proposed, dict) or set(proposed) != fields
            or not all(isinstance(proposed[k], str) for k in fields - {"evidence_ids"})):
        return reject("invalid_teacher_final_response")
    reason = proposed["reason"].strip()
    explanation = proposed["explanation"].strip()
    conclusion = proposed["conclusion"].strip()
    ids = proposed["evidence_ids"]
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return reject("invalid_teacher_final_response")
    if proposed["decision"] == "insufficient" and reason and not explanation and not conclusion:
        return reject("teacher_reports_insufficient_evidence")
    if proposed["decision"] != "deliver" or reason:
        return reject("invalid_teacher_final_response")
    if not ids or len(set(ids)) != len(ids) or any(i not in reviewed_steps for i in ids):
        return reject("teacher_final_unbound_evidence")
    if any(contains_chat_control(s) or _PRIVATE.search(s) for s in (explanation, conclusion)):
        return reject("teacher_final_contains_private_material")
    if protect_evidence_ids and _discloses_private_evidence_id(explanation + "\n" + conclusion, source["question"], reviewed_steps):
        return reject("teacher_final_contains_private_evidence_id")
    check = _check_surface(conclusion, source)
    if not check or not check["format_ok"] or not check["answer_matches"]:
        return reject("teacher_final_conclusion_does_not_match_key", match=check and check["answer_matches"])
    if form == "final_only":
        if explanation:
            return reject("teacher_final_unrequested_explanation", match=True)
        return {**check, "answer_surface": conclusion}, conclusion
    if not explanation or check_direct_answer(explanation, source["gold"]) or _explicit_wrong_conclusion(explanation, source["gold"]):
        return reject("teacher_final_missing_or_conflicting_explanation", match=True)
    evidence = source["question"] + "\n" + "\n".join(reviewed_steps[i] for i in ids)
    figures = {number(m[1]) for m in _FIGURES.finditer(unicodedata.normalize("NFKC", evidence))}
    if source["gold"]["kind"] == "number":
        figures.add(number(source["gold"]["value"]))
    claimed = {number(m[1]) for m in _FIGURES.finditer(unicodedata.normalize("NFKC", explanation))}
    notes = []
    if not claimed <= figures:
        notes.append("intermediate_quantities_not_independently_checked")
    if not (_words(explanation) & _words(evidence) or (claimed and re.search(r"[=+×÷*/−<>]", explanation))):
        notes.append("paraphrase_not_lexically_aligned")
    if _DECLARATION.match(explanation) and not re.search(r"[=+×÷*/−<>]|\b(?:because|since|using|from)\b|因为|由于|根据", explanation, re.I):
        # A lone answer declaration is not supporting work even when phrased as a sentence.
        if len(re.split(r"(?<=[.。!?！？])\s+|[；;\n]", explanation.rstrip(".。!！?？"))) == 1:
            return reject("teacher_final_missing_or_conflicting_explanation", match=True)
    return {**check, "answer_surface": conclusion, "advisories": notes}, explanation + "\n\n" + conclusion
