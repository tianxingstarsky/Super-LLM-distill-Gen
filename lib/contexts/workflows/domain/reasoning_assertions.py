"""Conservative extra obligations for numerical comparisons and source-wide negatives.

These are bounded detectors, not a semantic truth oracle. Ambiguous reference or
negation stays unresolved; no negative search result proves a universal negative.
"""
from decimal import Decimal
import re
from .reasoning_claims import claim_anchors
from .reasoning_checklist import checklist_digest

HALF = re.compile(r"不到一半|少于一半|超过一半|多于一半|至少一半|至多一半|\b(?:less|more) than half\b|\bat (?:least|most) half\b", re.I)
ABSENCE = re.compile(r"(?:论文|研究|作者|全文).{0,20}(?:没有|未曾|未)(?:给出|报告|提供|进行|做)|\b(?:paper|study|authors?)\b.{0,40}\b(?:does not|did not|never|no)\b", re.I)


def assertion_anchors(candidate):
    anchors = claim_anchors(candidate)
    # v5 only: a closing curly quote after a sentence terminator belongs to the
    # preceding sentence. Keep the historical ID so saved calls remain traceable.
    # Never strip straight quotes (opening/closing is ambiguous) or negation.
    for anchor in anchors:
        text, start = candidate[anchor["field"]], anchor["start"]
        if anchor["quote"].startswith(("” ", "’ ")) and text[:start].rstrip().endswith((".", "。", "!", "?")):
            removed = len(anchor["quote"]) - len(anchor["quote"][1:].lstrip())
            anchor["quote"] = anchor["quote"][removed:]
            anchor["start"] += removed
    for field, text in candidate.items():
        for match in list(HALF.finditer(text)) + list(ABSENCE.finditer(text)):
            if any(a["field"] == field and a["start"] <= match.start() < a["start"] + len(a["quote"]) for a in anchors):
                continue
            left = max(text.rfind(mark, 0, match.start()) for mark in ("。", "\n", ". ")) + 1
            stops = [p for mark in ("。", "\n", ". ") if (p := text.find(mark, match.end())) >= 0]
            right = min(stops) + 1 if stops else len(text)
            raw = text[left:right]
            start = left + len(raw) - len(raw.lstrip())
            quote = raw.strip()
            anchors.append({"id": "c_" + checklist_digest([field, start, quote])[:16],
                "field": field, "start": start, "quote": quote, "numeric": bool(re.search(r"\d", quote)), "scope": True})
    return sorted(anchors, key=lambda a: (a["field"], a["start"]))


def assertion_receipt(anchor, claim, interpretation):
    quote = anchor["quote"]
    half = list(HALF.finditer(quote))
    absence = ABSENCE.search(quote)
    if not half and not absence:
        return None
    if interpretation["stance"] in {"negated", "quoted", "non_propositional"}:
        explicit_denial = re.fullmatch(r'(?:It is (?:wrong|incorrect) to say|不能说|错误说法是)\s*["“][^"”]+["”][。.]*', quote, re.I)
        if explicit_denial and claim["status"] == "not_claim":
            return {"status": "verified", "issue": None, "anchor_id": anchor["id"],
                    "limits": "Explicitly rejected quotation, not an assertion of the embedded comparison."}
        # A model-supplied stance cannot erase a located obligation. Explicitly
        # quoted/negated prose needs semantic confirmation, not an automatic fail.
        return {"status": "unresolved", "issue": "assertion_stance_needs_evidence", "anchor_id": anchor["id"]}
    if absence:
        return {"status": "unresolved", "issue": "source_wide_absence_not_established", "anchor_id": anchor["id"],
                "reason": "Provided excerpts/search misses do not establish what an entire paper did or did not report."}
    values = re.findall(r"(?<![\d.])([0-9]+(?:\.[0-9]+)?)\s*[%％]", quote)
    # Only evaluate an unambiguous explicit same-clause comparison. Multiple
    # quantities, quoted claims and conditional examples require review evidence.
    direct_relation = re.search(r"[0-9]+(?:\.[0-9]+)?\s*[%％]\s*[，,]?\s*(?:即|也就是|which is\s+)?(" + HALF.pattern + r")[。.!;；]?\s*$", quote, re.I)
    if (len(values) != 1 or len(half) != 1 or not direct_relation
            or re.search(r"[“”\"「」]|假如|如果|并非|不是|不能说|声称|说法|约|近似|\b(?:if|not|incorrect|wrong|says?|claims?|about|roughly|approximately)\b", quote, re.I)):
        return {"status": "unresolved", "issue": "comparison_reference_unresolved", "anchor_id": anchor["id"]}
    number, phrase = Decimal(values[0]), half[0][0].lower()
    holds = (number < 50 if phrase in {"不到一半", "少于一半", "less than half"} else
             number > 50 if phrase in {"超过一半", "多于一半", "more than half"} else
             number >= 50 if phrase in {"至少一半", "at least half"} else number <= 50)
    return {"status": "verified" if holds else "violated", "issue": None if holds else "comparison_value_conflict",
            "anchor_id": anchor["id"], "reported_percent": str(number), "comparison_quote": half[0][0],
            "threshold_percent": "50", "limits": "Explicit same-clause comparison only; no source entailment inferred."}
