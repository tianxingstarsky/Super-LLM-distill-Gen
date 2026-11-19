"""Check explicit storage-to-count equivalences against stated bytes per page or pixel."""

import re

from .guided_answers import number


_NUMBER = r"(?:\d{1,3}(?:,\d{3}){1,8}|\d{1,24})(?:\.\d{1,12})?"
_SIZE = r"(?:MiB|MB|(?i:mebibytes?|megabytes?|bytes?))\b"
_COUNT = r"(?i:pixels?|pages?)\b"
_TARGET = rf'["“]?(?P<count>{_NUMBER})\s+(?P<unit>{_COUNT})(?: of paper)?["”]?'
# In the two-sentence form the immediately preceding sentence must end with the quantity itself.
_EQUIVALENCE = re.compile(
    rf"(?<![\w.,/+%-])\$?(?P<size>{_NUMBER})\$?\s*(?P<storage>{_SIZE})"
    rf"(?P<link>\s*(?:=|(?i:equals?|is equivalent to|is equal to|is the same as|is|corresponds to|means))\s*"
    rf"|[.]\s+(?i:this (?:effectively )?(?:means|equals|corresponds to))\s+)"
    rf"(?P<targets>{_TARGET}(?:\s+or\s+[\"“]?{_NUMBER}\s+{_COUNT}(?: of paper)?[\"”]?)?)"
)
_TARGETS = re.compile(_TARGET)
_RATE = re.compile(
    rf"(?<![\w.,/+%-])(?P<size>{_NUMBER})\s*(?P<storage>{_SIZE})\s*(?:/|(?i:per)\s+)(?P<unit>{_COUNT})"
)
_UNIT_RATE = re.compile(
    rf"(?i:\b(?:1|one|each)\s+)(?P<unit>{_COUNT})\s*(?:=|(?i:is|uses|stores|takes|occupies|requires))\s*"
    rf"(?P<size>{_NUMBER})\s*(?P<storage>{_SIZE})"
)
_ASSUMPTION = re.compile(r"\s*\((?i:if|assuming)\s+(?P<rate>[^()\n]{1,100})\)")
_QUOTED = re.compile(r'''"[^"\n]*"|“[^”]*”|‘[^’]*’|(?<!\w)'[^'\n]*'(?!\w)''')
_NONASSERTION = re.compile(
    r"\b(?:if|assum\w*|suppos\w*|unless|provided|conditionally|depending|may|might|could|would|"
    r"possibly|approximately|roughly|about|not|never|wrong\w*|false|invalid|incorrect\w*|error|mistak\w*|"
    r"claim\w*|wrote|said|says|quotes?|counterexample|reject\w*|whether|instead|rather)\b|n['’]t\b|[?？]",
    re.I,
)
_STOP = re.compile(r"(?:[.!?](?=\s|$)|[\n;])")


def _bytes(match, *, binary_mb=False):
    unit = match["storage"].casefold()
    if unit.startswith(("mi", "mebi")) or (binary_mb and unit.startswith("m")):
        return number(match["size"]) * 2**20
    return number(match["size"]) * (10**6 if unit.startswith("m") else 1)


def storage_quantity_issue(findings):
    if any(item["status"] == "contradicted" for item in findings):
        return "quantity_contradiction"
    return "quantity_conversion_unverified" if findings else None


def _question_rates(question, unit):
    rates = []
    # Bind a rate only when the question states it as a complete premise. A rate for one
    # image, a rejected rate, or several encodings does not establish a rate for all images.
    for sentence in re.split(r"(?<=[.!?])\s+|\n", question):
        words = sentence.strip().removesuffix(".").strip()
        rate = _UNIT_RATE.fullmatch(words) or _RATE.fullmatch(re.sub(r"^(?:use|allow|allocate)\s+", "", words, flags=re.I))
        if rate and rate["unit"].lower().rstrip("s") == unit:
            rates.append(rate)
        elif any(r["unit"].lower().rstrip("s") == unit for p in (_RATE, _UNIT_RATE) for r in p.finditer(words)):
            return []
    return rates


def storage_quantity_findings(text, *, question, quoted_spans=()):
    """Return supported contradictions or missing conversion evidence, each bound to public text.

    A byte count alone does not specify a page or pixel count. Only affirmative, explicit
    equalities in this narrow grammar are checked. Unrecognized encodings remain with the reviewer.
    """
    if not re.search(r"\b(?:pixels?|pages?)\b", text, re.I) or any(mark in text for mark in ("`", "~~~")):
        return []
    exclusions = [*quoted_spans, *(m.span() for m in _QUOTED.finditer(text))]
    findings = []
    for claim in _EQUIVALENCE.finditer(text):
        if any(start <= claim.start() < end or start <= claim.start("link") < end for start, end in exclusions):
            continue
        # Quotation marks around a quantity can be emphatic; a quoted assertion is not the author's.
        if any(start < claim.end() and claim.start() < end for start, end in quoted_spans):
            continue
        premise = _ASSUMPTION.match(text, claim.end())
        assumed_rate = _UNIT_RATE.fullmatch(premise["rate"]) if premise else None
        preceding = text[:claim.start()]
        before = _STOP.split(preceding)[-1]
        previous_line = preceding.rstrip().splitlines()[-1] if preceding.strip() else ""
        tail = text[premise.end() if assumed_rate else claim.end():]
        after = _STOP.split(tail, maxsplit=1)[0]
        if (before.lstrip().startswith(">") or _NONASSERTION.search(before + " " + after)
                or re.search(r"(?:[+−*/×÷=^%-]|\\(?:times|div|cdot)|\b(?:[xX]|plus|minus|times|over))\s*\$?$", before)
                or (previous_line.endswith(":") and _NONASSERTION.search(previous_line))
                or tail.lstrip().startswith(("?", "？"))
                or re.search(_SIZE, after) or re.match(r"\s*(?:/|per\b|of\b|at\b|\*|×|\+|-)", after, re.I)
                or re.match(r"\s*[.]\s*(?:this|that|the above) (?:is|was) (?:not|wrong|false|incorrect|invalid)", tail, re.I)):
            continue
        for target in _TARGETS.finditer(text, claim.start("targets"), claim.end("targets")):
            unit = target["unit"].lower().rstrip("s")
            rates = _question_rates(question, unit)
            has_assumption = assumed_rate and assumed_rate["unit"].lower().rstrip("s") == unit
            if has_assumption:
                rates = [assumed_rate]
            byte_count = _bytes(claim)
            entry = {"span": list(target.span()), "claim": target[0], "storage_quantity": text[claim.start():claim.start("link")],
                     "storage_span": [claim.start(), claim.start("link")], "storage_bytes": str(byte_count), "count_unit": unit}
            if rates:
                if len({_bytes(r) for r in rates}) != 1:
                    continue
                rate = rates[0]
                implied = _bytes(rate) * number(target["count"])
                if implied == byte_count or _bytes(rate, binary_mb=True) * number(target["count"]) == _bytes(claim, binary_mb=True):
                    continue
                entry.update(status="contradicted", conversion=rate[0], conversion_source="stated_condition" if has_assumption else "question",
                             implied_bytes=str(implied))
            else:
                # A word problem or another statement involving this unit can supply a relation
                # beyond this grammar (e.g. packed RGB). Leave such cases to the semantic reviewer.
                outside = question + "\n" + preceding + "\n" + text[claim.end():]
                if re.search(r"\b" + unit + r"s?\b", outside, re.I):
                    continue
                entry.update(status="insufficient_evidence", missing_relation=f"bytes per {unit}")
            findings.append(entry)
    return findings
