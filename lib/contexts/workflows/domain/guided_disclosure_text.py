"""Render source quotations for exactly the disclosure an independent tutor will review."""

from difflib import SequenceMatcher
import re


def _literal_quotes(text, question):
    """Find paired outer double quotes. Inner quote marks may be part of the cited source."""
    cursor = 0
    while cursor < len(text):
        opener = re.search('[“”"]', text[cursor:])
        if not opener:
            return
        start = cursor + opener.start()
        if text[start] == "”":
            raise ValueError("unpaired_teacher_quotation")
        mark = "”" if text[start] == "“" else '"'
        stops = [i for i in range(start + 1, len(text)) if text[i] == mark]
        if not stops:
            raise ValueError("unpaired_teacher_quotation")
        for end in reversed(stops):
            body = text[start + 1:end]
            if not body.strip() or body not in question:
                continue
            depth = 0
            for c in body:
                depth += int(c == "“") - int(c == "”")
                if depth < 0:
                    break
            if depth != 0:
                continue
            if body.count('"') % 2:
                continue
            yield start, end + 1, body
            cursor = end + 1
            break
        else:
            raise ValueError("teacher_quotation_is_unpaired_or_not_verbatim_in_question")


def _verbatim_runs(text, question, anchor):
    spans = []
    if anchor and anchor in text:
        spans.extend(m.span() for m in re.finditer(re.escape(anchor), text))
    for a, b, size in SequenceMatcher(None, text, question).get_matching_blocks():
        fragment = text[a:a + size]
        if not fragment.strip():
            continue
        # Avoid quoting fractions of a word and incidental shared connectives.
        while fragment and a and text[a-1].isascii() and text[a-1].isalnum() and fragment[0].isascii() and fragment[0].isalnum():
            a += 1
            fragment = fragment[1:]
        end = a + len(fragment)
        while fragment and end < len(text) and text[end].isascii() and text[end].isalnum() and fragment[-1].isascii() and fragment[-1].isalnum():
            fragment = fragment[:-1]
            end -= 1
        stripped = fragment.strip(" \t\n,.;:!?，。；：！？")
        words = re.findall("[A-Za-z0-9]+", stripped)
        if (len(words) < 4 and len(re.findall(r"[\u3400-\u9fff]", stripped)) < 7
                and not re.search(r"\b[A-Za-z0-9]+\s*(?:→|≠|=|≤|≥|<|>)\s*[A-Za-z0-9]+", stripped)):
            continue
        left = a + fragment.index(stripped)
        spans.append((left, left + len(stripped)))
    merged = []
    for lo, hi in sorted(spans):
        if merged and lo < merged[-1][1]:
            if hi > merged[-1][1] and text[merged[-1][0]:hi] in question:
                merged[-1] = (merged[-1][0], hi)
            continue
        merged.append((lo, hi))
    return merged


def _source_ranges(question, value):
    return [(m.start(), m.start() + len(value)) for m in re.finditer("(?=" + re.escape(value) + ")", question)]


def _quotation_position(question, value, anchor_span):
    positions = _source_ranges(question, value)
    if len(positions) == 1:
        return positions[0]
    if anchor_span:
        start, stop = anchor_span
        linked = [(lo, hi) for lo, hi in positions if lo <= start <= stop <= hi or start <= lo <= hi <= stop]
        if len(linked) == 1:
            return linked[0]
    raise ValueError("ambiguous_teacher_quotation_needs_a_specific_task_span")


def _unique_anchor_position(question, anchor):
    positions = _source_ranges(question, anchor) if anchor else []
    return positions[0] if len(positions) == 1 else None


def locate_teacher_premise(question, submitted):
    """Find a unique source span. Literal quotation marks in the task take precedence."""
    if not isinstance(submitted, str):
        raise ValueError("teacher_needs_a_specific_verbatim_task_premise")
    quote = submitted
    if quote not in question and (quote[:1], quote[-1:]) in (
        ('"', '"'), ("“", "”"), ("„", "“"), ("«", "»"), ("『", "』"),
    ):
        quote = quote[1:-1]
    span = _unique_anchor_position(question, quote)
    if span is None or not quote.strip():
        raise ValueError("teacher_needs_a_specific_verbatim_task_premise")
    return {"quote": quote, "source_span": list(span)}


def locate_authored_task_citations(citations, question, premise):
    anchor = _unique_anchor_position(question, premise)
    return [{**entry, "span": list(_quotation_position(question, entry["quote"], anchor))}
            if entry["evidence_id"] == "question" else entry for entry in citations]


def render_teacher_text(text, question, anchor="", *, anchor_span=None):
    """Wrap copied premises, verify explicit citations and leave independently written operations alone."""
    pieces, quotes = [], []
    if anchor_span is not None:
        if (len(anchor_span) != 2 or not 0 <= anchor_span[0] < anchor_span[1] <= len(question)
                or question[slice(*anchor_span)] != anchor):
            raise ValueError("teacher_quotation_anchor_mismatches_its_signed_task_span")
    elif anchor:
        # A freely written citation can use a longer, uniquely identifiable task
        # passage as context. Identical passages cannot supply their own location.
        anchor_span = _unique_anchor_position(question, anchor)
    def add_quote(value, original=None):
        if original is None:
            # The alternate quotation style preserves quotes present inside the original.
            opening, closing = ('"', '"') if any(c in value for c in "“”") else ("“", "”")
            original = opening + value + closing
        start = sum(map(len, pieces))
        pieces.append(original)
        location = _quotation_position(question, value, anchor_span)
        quotes.append({"quote": value, "source_span": list(location),
                       "public_span": [start + 1, start + len(original) - 1]})
    def add_unquoted(chunk):
        cursor = 0
        for lo, hi in _verbatim_runs(chunk, question, anchor):
            pieces.append(chunk[cursor:lo])
            add_quote(chunk[lo:hi])
            cursor = hi
        pieces.append(chunk[cursor:])
    cursor = 0
    for start, end, value in _literal_quotes(text, question):
        add_unquoted(text[cursor:start])
        add_quote(value, text[start:end])
        cursor = end
    add_unquoted(text[cursor:])
    rendered = "".join(pieces)
    markers = r"题面(?:上)?写(?:道|着)|题中(?:原话|原文)(?:为|是)|the (?:question|problem) (?:states|says|reads)"
    for marker in re.finditer(markers, rendered, re.I):
        if any(lo <= marker.start() < hi for lo, hi in (q["public_span"] for q in quotes)):
            continue
        if not rendered[marker.end():].lstrip(" \t\n:：,，").startswith(("“", '"')):
            raise ValueError("attributed_task_wording_needs_a_verbatim_quotation")
    return rendered, quotes


def checked_reviewer_pairing(assessment, previous_id):
    if not isinstance(assessment, dict) or not isinstance(assessment.get("pairing"), dict):
        raise ValueError("missing_disclosure_pairing")
    pairing = assessment["pairing"]
    if (set(pairing) != {"student_fit", "evidence_support", "history_fit"}
            or any(type(pairing[k]) is not bool for k in ("student_fit", "evidence_support"))
            or pairing["history_fit"] not in ("continuous", "disconnected", "first")):
        raise ValueError("invalid_disclosure_pairing")
    if assessment.get("decision") == "release" and (
        not pairing["student_fit"] or not pairing["evidence_support"]
        or pairing["history_fit"] != ("continuous" if previous_id else "first")
    ):
        raise ValueError("unconfirmed_disclosure_pairing")
    return {k: v for k, v in assessment.items() if k != "pairing"}
