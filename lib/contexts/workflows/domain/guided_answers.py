"""Source contracts, answer-key checks and disclosure rules for guided answers."""

from fractions import Fraction
import re
import unicodedata

from lib.prompts.workflow_guided_answers import HINTS


def number(value):
    value = unicodedata.normalize("NFKC", str(value)).strip().strip("$").strip()
    value = value.replace("−", "-").replace(r"\,", "").strip()
    value = re.sub(
        r"^([+-]?)\\(?:dfrac|tfrac|frac)\{([+-]?\d+)\}\{([+-]?\d+)\}$",
        r"\1\2/\3",
        value,
    )
    if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", value):
        value = value.replace(",", "")
    if len(value) > 256 or not re.fullmatch(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:/\d+|[eE][+-]?\d{1,2})?", value
    ):
        return None
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None


def validate_source(row):
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("id"), str)
        or not row["id"].strip()
    ):
        raise ValueError("source id must be a nonempty string")
    if not isinstance(row.get("question"), str) or not row["question"].strip():
        raise ValueError("source question must be nonempty")
    reasoning = row.get("source_reasoning", "")
    if not isinstance(reasoning, str) or not isinstance(row.get("source", {}), dict):
        raise ValueError("source reasoning and provenance have invalid types")
    gold = row.get("gold")
    if gold is None and not reasoning.strip():
        raise ValueError("a source without a key requires attributed source reasoning")
    if gold is not None:
        if not isinstance(gold, dict) or gold.get("kind") not in {
            "number",
            "choice",
            "exact",
        }:
            raise ValueError("unsupported answer key")
        if not isinstance(gold.get("value"), str) or not gold["value"].strip():
            raise ValueError("answer-key value must be a nonempty string")
        if gold["kind"] == "number" and number(gold["value"]) is None:
            raise ValueError("invalid exact rational answer key")
        if gold["kind"] == "choice" and not re.fullmatch(r"[A-Z]", gold["value"]):
            raise ValueError("choice answer key must be one uppercase letter")
    hints = row.get("disclosure_hints", [])
    if not isinstance(hints, list):
        raise ValueError("disclosure_hints must be an ordered list of reviewed clues")
    checks = row.get("progress_checks", [])
    if not isinstance(checks, list):
        raise ValueError("progress_checks must be a list")
    check_ids = set()
    variables = set()
    for check in checks:
        if (
            not isinstance(check, dict)
            or set(check) != {"id", "kind", "variable", "values", "meaning", "source"}
            or check["kind"] != "finite_set"
            or any(
                not isinstance(check[k], str) or not check[k].strip()
                for k in ("id", "variable", "meaning", "source")
            )
            or check["id"] in check_ids
            or check["variable"] in variables
            or not re.fullmatch(r"[a-zA-Z][a-zA-Z_0-9]{0,23}", check["variable"])
            or not isinstance(check["values"], list)
            or not 2 <= len(check["values"]) <= 128
            or any(type(n) is not int or abs(n) > 10**24 for n in check["values"])
            or len(set(check["values"])) != len(check["values"])
        ):
            raise ValueError("invalid reviewed finite-set progress check")
        check_ids.add(check["id"])
        variables.add(check["variable"])
    ids = set()
    for hint in hints:
        if (
            not isinstance(hint, dict)
            or not {"id", "source", "text"}
            <= set(hint)
            <= {"id", "source", "text", "kind"}
            or any(not isinstance(v, str) or not v.strip() for v in hint.values())
            or hint["id"] in ids
            or hint.get("kind", "idea") not in {"fact", "idea", "worked_step"}
        ):
            raise ValueError("each reviewed hint requires a unique id, source and text")
        ids.add(hint["id"])
    public_texts = [hint["text"] for hint in hints] + [
        check["meaning"] for check in checks
    ]
    for visible in public_texts:
        text = unicodedata.normalize("NFKC", visible)
        if re.search(
            r"<\/?think>|<\|im_|\b(?:system prompt|hidden answer|answer key|round \d+|the answer is|final answer)\b|(?:标准答案|内部轮次|指导员|教练|第[\d一二三四五六七八九十]+轮|答案是)",
            text,
            re.I,
        ):
            raise ValueError("a reviewed hint discloses an answer or internal protocol")
        if gold:
            key = gold["value"].strip()
            # A worked step may legitimately contain the same number or letter as the key.
            # Reject a standalone answer or explicit instruction to give that result.
            proposed = re.sub(
                r"^(?:use (?:the )?value|return|enter|choose|select|应选|选项|取值为)\s*",
                "",
                text.strip().rstrip(".。!！"),
                flags=re.I,
            ).strip()
            if gold["kind"] == "number":
                leaked = number(proposed) == number(key)
            else:
                leaked = proposed.casefold() == key.casefold()
            if leaked:
                raise ValueError("a reviewed hint contains the supplied answer key")
    return {
        "id": row["id"],
        "question": row["question"],
        "gold": gold,
        "source_reasoning": reasoning,
        "source": row.get("source", {}),
        "disclosure_hints": hints,
        **({"progress_checks": checks} if checks else {}),
    }


def answer_tail(text, *, marked_finals=True):
    lines = text.splitlines(keepends=True)
    headers = []
    offset = 0
    fence = ""
    for i, line in enumerate(lines):
        if marked_finals:
            marker = re.match(r"^(`{3,}|~{3,})(.*)$", line.strip())
            if fence or marker:
                if not fence:
                    fence = marker[1]
                elif marker and marker[1].startswith(fence) and not marker[2].strip():
                    fence = ""
                offset += len(line)
                continue
        visible = re.sub(r"^#{1,6}\s+", "", line.strip())
        if marked_finals:
            # The entire declaration can be bold, e.g. **Final Answer: 16**.
            visible = re.sub(r"^(\*\*|__)([^*_]+)\1$", r"\2", visible)
        visible = re.sub(
            r"^([*_]{1,3})(final answer|最终答案|答案)(\s*[:：]?)\1",
            r"\2\3",
            visible,
            flags=re.I,
        )
        match = re.fullmatch(
            r"(?:final answer|最终答案|答案)\s*(?:[:：]\s*(.*))?", visible, re.I
        )
        if match:
            headers.append((i, offset, match[1] or ""))
        offset += len(line)
    if len(headers) != 1:
        return None, None
    i, start, inline = headers[0]
    following = [line.strip() for line in lines[i + 1 :] if line.strip()]
    if inline and not following:
        value = inline.strip()
    elif not inline and len(following) == 1:
        value = following[0]
    else:
        return None, None
    return start, re.sub(r"^(\*\*|__)(.+)\1$", r"\2", value)


def numerical_statement(answer):
    """Read an unqualified count in the already isolated, explicitly labelled final line."""
    text = unicodedata.normalize("NFKC", answer).strip()
    text = re.sub(r"(\*\*|__)([^*_]+)\1", r"\2", text).strip()
    if re.search(
        r"如果|假如|若|假设|当|否则|可能|也许|约|近似|大概|至少|至多|大于|小于|超过|不足|并非|并不|不(?:是|等于|会|一定)|区间|介于|或者",
        text,
    ):
        return None
    amount = r"(?P<value>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:/\d+)?)"
    for pattern in (
        r"(?:[\u4e00-\u9fff]{0,36}?(?:共有|一共有|合计|总计|总数为|数量为|个数为|答案为|结果为|等于))\s*"
        + amount
        + r"\s*(?:个|条|组|种|项|份|次|人|件)?[。.]?",
        r"(?:the (?:answer|total|result|count) is|there (?:are|is)(?: a total of)?)\s+"
        + amount
        + r"(?:\s+(?:items?|records?|ways?|solutions?|groups?|cases?))?[.]?",
    ):
        match = re.fullmatch(pattern, text, re.I)
        if match and number(match["value"]) is not None:
            return match["value"]
    return None


def _boxed_numerical_statement(answer, question):
    """Accept one exact scalar in a terminal assertion with an identified subject."""
    line = unicodedata.normalize("NFKC", answer).strip()
    box = re.fullmatch(r"([^\\${}]*?)(\$?)\\boxed\{(.+)\}\2\.?", line)
    if not box or number(box[3]) is None:
        return None
    prefix = " ".join(box[1].lower().split())
    if not prefix or re.fullmatch(r"the (?:final )?(?:answer|total|result|count) is", prefix):
        return box[3]
    # Allow a declarative count only when its subject echoes the actual question.
    # Other numbers in that subject can be genuine constraints, e.g. divisibility by 6.
    quantity = re.fullmatch(r"the (?:number|count) of ([a-z0-9 -]+) is", prefix)
    asked = re.fullmatch(r"how many ([a-z0-9 -]+)\?", " ".join((question or "").lower().split()))
    if not quantity or not asked or re.search(
        r"\b(?:if|unless|assuming|provided|supposing|otherwise|or|either|neither|nor|not|never|"
        r"may|might|could|would|perhaps|possibly|probably|approximately|roughly|alternatively)\b", quantity[1]
    ):
        return None
    subject = re.sub(r"\b(?:that|which) (?=are\b|were\b|have\b)", "", quantity[1])
    return box[3] if subject == asked[1] else None


def inspect_answer(text, gold, *, parse_count_sentence=True, marked_finals=True, question=None):
    start, answer = answer_tail(text, marked_finals=marked_finals)
    if start is None:
        return dict(
            format_ok=False,
            answer=None,
            answer_matches=None,
            issue="missing_or_ambiguous_final_line",
        )
    explanation = text[:start].strip()
    if not explanation or explanation.casefold() == answer.casefold():
        return dict(
            format_ok=False,
            answer=answer,
            answer_matches=None,
            issue="missing_checkable_explanation",
        )
    if re.search(r"<\/?think>|<\|im_(?:start|end)\|>", text, re.I):
        return dict(
            format_ok=False,
            answer=answer,
            answer_matches=None,
            issue="private_or_template_markers",
        )
    candidate = answer.strip("$").strip()
    if box := re.fullmatch(r"\\boxed\{(.+)\}", candidate):
        candidate = box[1]
    surface = answer
    if gold is None:
        matches = None
    elif gold["kind"] == "number":
        if parse_count_sentence and number(candidate) is None:
            candidate = numerical_statement(answer)
            if candidate is None and marked_finals:
                candidate = _boxed_numerical_statement(answer, question)
            if candidate is None:
                return dict(
                    format_ok=False,
                    answer=None,
                    answer_surface=surface,
                    answer_matches=None,
                    issue="ambiguous_or_unsupported_numeric_final",
                )
            answer = candidate
        matches = number(candidate) == number(gold["value"])
    elif gold["kind"] == "choice":
        matches = candidate.upper() == gold["value"]
    else:
        matches = answer == gold["value"]
    return dict(
        format_ok=True,
        answer=answer,
        answer_surface=surface,
        answer_matches=matches,
        issue="answer_key_mismatch" if matches is False else None,
    )


def validated_review(review, segments, answer_line, *, anchored=False, single_decision=False):
    if single_decision:
        if not isinstance(review, dict) or set(review) != {"decision", "diagnosis", "reusable"}:
            raise ValueError("expected one tutor decision")
        decision = review["decision"]
        if not isinstance(decision, str):
            raise ValueError("invalid tutor decision")
        verdict, _, anchor = decision.partition(":")
        if decision == "supported":
            anchor = "complete"
        elif verdict not in {"revise", "uncertain"} or anchor == "complete":
            raise ValueError("unknown tutor decision")
        review = {"verdict": verdict, "first_unresolved_id": anchor,
                  "diagnosis": review["diagnosis"], "reusable": review["reusable"]}
    fields = {
        "verdict",
        "diagnosis",
        "reusable",
    } | (
        {"first_unresolved_id"}
        if anchored
        else {"evidence_id", "verified_prefix_count"}
    )
    if not isinstance(review, dict) or set(review) != fields:
        raise ValueError("invalid tutor assessment fields")
    if anchored:
        ids = [s["id"] for s in segments]
        anchor = review["first_unresolved_id"]
        if anchor not in [*ids, "final_answer", "complete"] or (
            anchor == "complete"
        ) != (review["verdict"] == "supported"):
            raise ValueError("missing or inconsistent first unresolved anchor")
        cite = anchor if anchor in ids else ("answer" if answer_line else ids[-1])
        review = {
            **review,
            "evidence_id": cite,
            "verified_prefix_count": ids.index(anchor) if anchor in ids else len(ids),
        }
    if type(review["verified_prefix_count"]) is not int or not all(
        isinstance(review[k], str) for k in ("verdict", "evidence_id", "diagnosis")
    ):
        raise ValueError("invalid tutor assessment values")
    if review["verdict"] not in {"supported", "revise", "uncertain"}:
        raise ValueError("unknown tutor assessment")
    citations = {s["id"]: s["text"] for s in segments}
    if answer_line:
        citations["answer"] = answer_line
    if (
        review["evidence_id"] not in citations
        or not review["diagnosis"].strip()
        or not isinstance(review["reusable"], list)
    ):
        raise ValueError("tutor assessment lacks a grounded citation or diagnosis")
    return {**review, "evidence": citations[review["evidence_id"]]}


_UNCERTAIN_CLAIM = re.compile(
    r"如果|假如|若|假设|假定|假说|设想|猜|也许|可能|或许|未必|似乎|例如|比如|反例|错误|不对|不成立|不正确|不满足|不确定|否定|尚未|是否|除非|仅当|即使|并非|不是|不属于|未证|不妨假|当.+时|[?？]|\\notin|∉|"
    r"\b(?:if|unless|when|whether|assuming|suppos\w*|hypothes\w*|guess\w*|might|maybe|possibly|perhaps|example|wrong|incorrect|invalid|not|never|false|could|either)\b",
    re.I,
)


def affirmative_membership(left, right):
    left = re.sub(r"^\s*(?:\d+[.)]|[-*])\s+", "", left).strip()
    positive = not left or re.fullmatch(
        r"(?:所以|故|因此|可得|从而|于是|由此可知|可知|可取|thus|hence|therefore|we (?:obtain|have|conclude that))\s*[,，]?",
        left,
        re.I,
    )
    if not positive:
        # Accept an explicit restatement of a named quantity inside a larger
        # explanation. A quoted, hypothetical or evaluated formula is not enough.
        positive = re.search(
            r"(?:^|[（(，,；;])\s*(?:即|也就是|亦即|即为)\s*(?:[A-Za-z0-9\u4e00-\u9fff ]{1,28}(?:数|数量|变量|参数|集合|总和|值|长度))?\s*$",
            left,
        )
    return bool(positive) and bool(
        re.fullmatch(r"[\s)\]）】。.;；!！]*", right)
        or re.match(r"^[,，(（]\s*(?:因为|由于|because\b|since\b)", right, re.I)
    )


def checked_progress(checks, pieces, suffix):
    """Attribute only a complete, affirmative, variable-bound exact set membership to its actual author."""
    found = {}
    for rule in checks:
        variable = re.escape(rule["variable"])
        membership = re.compile(
            rf"(?<![\w\\]){variable}\s*(?:\\in(?![A-Za-z])|∈)\s*"
            r"(?:\\)?\{\s*(?P<values>[+\-−]?[0-9]{1,25}(?:\s*[,，]\s*[+\-−]?[0-9]{1,25}){1,127})\s*(?:\\)?\}"
        )
        eligible = []
        conflict = False
        for piece in pieces:
            for match in membership.finditer(piece["text"]):
                values = [
                    int(n.strip().replace("−", "-"))
                    for n in re.split("[,，]", match["values"])
                ]
                if len(values) != len(set(values)) or set(values) != set(
                    rule["values"]
                ):
                    conflict = True
                    continue
                start, end = (
                    piece["start"] + match.start(),
                    piece["start"] + match.end(),
                )
                line_start = (
                    max(suffix.rfind("\n", 0, start), suffix.rfind("\r", 0, start)) + 1
                )
                newline = re.search(r"[\r\n]", suffix[end:])
                line_end = end + newline.start() if newline else len(suffix)
                line = suffix[line_start:line_end]
                prior = suffix[:line_start].rstrip().splitlines()
                scope = prior[-1] if prior and prior[-1].endswith((":", "：")) else ""
                left = suffix[line_start:start].rstrip().rstrip("$").rstrip()
                right = suffix[end:line_end].lstrip().lstrip("$").lstrip()
                if (
                    _UNCERTAIN_CLAIM.search(line)
                    or _UNCERTAIN_CLAIM.search(scope)
                    or not affirmative_membership(left, right)
                ):
                    continue
                eligible.append(
                    dict(
                        segment_id=piece["id"],
                        author="student",
                        text=suffix[start:end],
                        source_span=[start, end],
                        check_id=rule["id"],
                        meaning=rule["meaning"],
                        reviewed_source=rule["source"],
                        evidence_level="program_checked_reviewed_finite_set",
                        basis_id="reviewed_progress_check",
                        depends_on=[],
                    )
                )
        if eligible and not conflict:
            found["check:" + rule["id"]] = eligible[0]
    return found


def shared_findings(proposed, segments, prefix, question, *, exclude_list_markers=False):
    """Only an authenticated student span is published. Unsupported proposals remain in the private review."""
    valid = {s["id"]: s["text"] for s in prefix}
    later = {s["id"]: s for s in segments[len(prefix) :]}
    found = {}
    for item in proposed:
        if not isinstance(item, dict) or set(item) != {
            "segment_id",
            "basis_id",
            "depends_on",
        }:
            continue
        sid, basis, deps = item["segment_id"], item["basis_id"], item["depends_on"]
        if (
            not isinstance(sid, str)
            or not isinstance(basis, str)
            or sid not in later
            or not isinstance(deps, list)
        ):
            continue
        if not all(isinstance(k, str) and k in valid for k in deps):
            continue
        text = later[sid]["text"].strip().rstrip(".。")
        if exclude_list_markers and re.fullmatch(r"[\s\d.、()（）\-*#]*", text):
            continue
        if basis == "arithmetic" and not deps:
            match = re.fullmatch(
                r"\s*([+-]?\d+(?:/\d+)?)\s*([+*/×÷−-])\s*([+-]?\d+(?:/\d+)?)\s*=\s*([+-]?\d+(?:/\d+)?)\s*",
                text,
            )
            if not match:
                continue
            left, right, result = map(number, (match[1], match[3], match[4]))
            if None in (left, right, result):
                continue
            op = match[2]
            if op in "/÷" and right == 0:
                continue
            calculated = (
                left + right
                if op == "+"
                else left - right
                if op in "-−"
                else left * right
                if op in "*×"
                else left / right
            )
            if calculated != result:
                continue
            grade = "program_checked_arithmetic_identity"
        elif basis == "question" and text and text in question:
            grade = "verbatim_question_fact"
        elif basis in valid and text and text in valid[basis]:
            grade = "verbatim_reviewed_prefix"
        else:
            continue
        found[sid] = {
            "segment_id": sid,
            "author": "student",
            "text": later[sid]["text"],
            "basis_id": basis,
            "depends_on": deps,
            "evidence_level": grade,
        }
    return found


def disclosure_bank(source):
    if source["disclosure_hints"]:
        return [{"kind": "idea", **hint} for hint in source["disclosure_hints"]]
    zh = bool(re.search(r"[\u4e00-\u9fff]", source["question"]))
    order = (
        "interpretation",
        "derivation",
        "substitute",
        "coverage",
        "calculation",
        "units",
        "premise",
        "counterexample",
    )
    return [
        {
            "id": action,
            "kind": "idea",
            "source": "program_method_prompt",
            "text": HINTS[action][int(zh)],
        }
        for action in order
    ]


def prior_clue_evidence(clue, retained, findings, disclosed):
    """Match the full claim, preserving case, symbols, punctuation and word boundaries."""

    def normalized(text):
        return " ".join(unicodedata.normalize("NFC", text).split())

    claim = normalized(clue["text"])
    for earlier in disclosed:
        if normalized(earlier["text"]) == claim:
            return {"status": "already_disclosed", "disclosure_id": earlier["id"]}
    for piece in retained:
        if normalized(piece["text"]) == claim:
            return {
                "status": "student_already_derived",
                "student_segment_id": piece["id"],
                "evidence_level": "coach_reviewed_contiguous_prefix",
            }
    for finding in findings.values():
        if normalized(finding["text"]) == claim:
            return {
                "status": "student_already_derived",
                "student_segment_id": finding["segment_id"],
                "evidence_level": finding["evidence_level"],
            }
    return None


def draft_fragment(text, attempt, *, preserve_list_items=True, marked_finals=True):
    """Keep exact source spans. A terminal answer is replaceable separately from the derivation."""
    terminal, _ = answer_tail(text, marked_finals=marked_finals)
    work = text[:terminal] if terminal is not None else text
    pieces = []
    start = 0
    boundaries = []
    for match in re.finditer(r"(?<=[。！？.!?])[ \t]+|(?:\r\n|\n|\r)+", work):
        if preserve_list_items and work[match.start() - 1:match.start()] == ".":
            # The period in a Markdown ordered-list marker belongs to the next words.
            line_start = max(work.rfind("\n", 0, match.start()), work.rfind("\r", 0, match.start())) + 1
            if re.fullmatch(r"[ \t>]*\d{1,9}\.", work[line_start:match.start()]):
                continue
        boundaries.append(match.end())
    for end in [*boundaries, len(work)]:
        if work[start:end].strip():
            pieces.append(
                dict(
                    id=f"a{attempt}.s{len(pieces) + 1}",
                    author="student",
                    attempt=attempt,
                    start=start,
                    end=end,
                    text=work[start:end],
                )
            )
            start = end
    if start < len(work) and pieces:
        pieces[-1]["text"] += work[start:]
        pieces[-1]["end"] = len(work)
    return pieces, text[terminal:] if terminal is not None else ""


def assemble(segments, final_line=""):
    built = ""
    for segment in segments:
        if built and not built[-1].isspace() and not segment["text"][0].isspace():
            built += "\n"
        built += segment["text"]
    if final_line and built and not built.endswith(("\r", "\n")):
        built += "\n"
    return built + final_line
