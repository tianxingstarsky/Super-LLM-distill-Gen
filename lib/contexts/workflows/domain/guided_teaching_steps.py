"""Attested, source-specific teaching operations and their exact task evidence."""

import hashlib
import json
import re


def complete_solution_digest(source):
    """Identity for an independent verifier's full proof, distinct from individual teaching steps."""
    identity = {key: source.get(key) for key in (
        "id", "question", "task_system", "answer_requirement", "gold", "source_reasoning",
    )}
    identity["schema"] = "guided-complete-solution/v1"
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def certified_complete_disclosure(source, trusted_receipts):
    proof = source.get("source_reasoning", "")
    key = source.get("gold")
    if not trusted_receipts or not key or not proof.strip() or proof.strip() == key["value"].strip():
        return None
    receipt = complete_solution_digest(source)
    if receipt not in trusted_receipts:
        return None
    return {"id": receipt, "worked_solution": proof, "answer": key["value"]}


def certified_rule_prefixes(question, reference):
    """Select complete, ordered inferences from an attested rule proof. Keep its verdict for later."""
    if not reference or "\n\nClaim:" not in question:
        return []
    theory, _, _ = question.partition("\n\nClaim:")
    statements = {s.strip().casefold(): s.strip() for s in theory.strip().rstrip(".").split(". ")}
    proof = reference["worked_solution"]
    sentence = re.compile(r"[^.\n]+\.")
    sentences = list(sentence.finditer(proof))
    # Abbreviations, fragments, and unfamiliar proof formats require a tutor.
    if len(sentences) < 2 or sentence.sub("", proof).strip():
        return []
    evidence = []
    derived = {}
    for pos, match in enumerate(sentences):
        text = match.group().strip()
        start = match.end() - len(text)
        end = match.end()
        step_id = f"proof-{len(evidence) + 1}"
        dependencies = []
        question_evidence = []
        if text.startswith("With "):
            inference = re.fullmatch(r"With (.+), the rule “([^”]+)” gives “([^”]+)”\.", text)
            if not inference or inference[2].casefold() not in statements:
                return []
            premises = re.fullmatch(r"“[^”]+”(?: and “[^”]+”)*", inference[1])
            needed = [s.casefold() for s in re.findall("“([^”]+)”", inference[1])]
            if not premises or any(s not in statements and s not in derived for s in needed):
                return []
            question_evidence = [statements[s] for s in needed if s in statements] + [statements[inference[2].casefold()]]
            dependencies = list(dict.fromkeys(derived[s] for s in needed if s not in statements))
            derived[inference[3].casefold()] = step_id
            if pos == len(sentences) - 1 or (pos == len(sentences) - 2 and
                    sentences[-1].group().strip() == "This establishes the opposite of the claim."):
                # The final implication is still the learner's to draw. Cite its
                # checked premises and rule without publishing their conclusion.
                end = start + inference.end(2) + 1
                text = f"Apply “{inference[2]}” using {inference[1]}."
        elif text == "This establishes the opposite of the claim.":
            return evidence if match == sentences[-1] else []
        elif text == "Neither direction follows under the given rules.":
            return evidence if match == sentences[-1] else []
        elif not (proof.startswith("Neither “") and re.fullmatch(
            r"Neither “[^”]+” nor “[^”]+” is stated\.|No rule concludes “[^”]+”\.|"
            r"To obtain “[^”]+” from .+, the missing premise(?:s are| is) .+\.", text
        )):
            return []
        evidence.append({"id": step_id, "source": "verified_solution_prefix",
                         "kind": "inference", "text": text, "reference_receipt": reference["id"],
                         "source_span": [start, end], "question_evidence": question_evidence,
                         "depends_on": dependencies})
    return evidence


def verified_steps_digest(source_id, question, gold, steps):
    identity = {"source_id": source_id, "question": question, "gold": gold, "steps": steps}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_verified_steps(row, trusted_receipts):
    packet = row["verified_teaching_steps"]
    if not isinstance(packet, dict) or set(packet) != {"receipt_id", "steps"}:
        raise ValueError("expected a verified teaching receipt and a list of operations")
    receipt, steps = packet["receipt_id"], packet["steps"]
    if (not isinstance(receipt, str) or not re.fullmatch("[0-9a-f]{64}", receipt)
            or not trusted_receipts or receipt not in trusted_receipts):
        raise ValueError("teaching operations lack a trusted independent verification receipt")
    if not isinstance(steps, list) or not steps:
        raise ValueError("the verified source has no teaching operations")
    if verified_steps_digest(row["id"], row["question"], row.get("gold"), steps) != receipt:
        raise ValueError("verified teaching receipt does not match the current question, key and operations")
    ids = set()
    operations = set()
    for s in steps:
        if (not isinstance(s, dict) or set(s) != {"id", "kind", "quote", "span", "operation", "verification"}
                or not all(isinstance(s[key], str) and s[key].strip() for key in set(s) - {"span"})
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", s["id"]) or s["id"] in ids
                or s["kind"] not in {"premise", "operation", "counterexample"}
                or s["operation"] in operations):
            raise ValueError("invalid or repeated verified teaching operation")
        span = s["span"]
        if (not isinstance(span, list) or len(span) != 2 or any(type(x) is not int for x in span)
                or not 0 <= span[0] < span[1] <= len(row["question"])
                or row["question"][span[0]:span[1]] != s["quote"]
                or s["quote"].strip() == s["operation"].strip()):
            raise ValueError("teaching operation needs an exact task span and a distinct action")
        gold = row.get("gold")
        if gold:
            key = gold["value"].strip()
            # A multi-ID witness is the delivered result. A numerical task may legitimately
            # mention the target number as one of its inputs, so check answer commands there.
            token = r"(?<![\w.])" + re.escape(key) + r"(?![\w.])"
            if ((gold["kind"] == "exact" and len(key) > 1 and re.fullmatch(r"(?:[A-Z]{2,}|F\d+(?:,F\d+)+)", key)
                 and re.search(token, s["operation"]))
                    or re.search(r"(?:\b(?:return|submit|output)\s+|(?:填入|提交|输出)\s*)"
                                 + re.escape(key) + r"(?![\w.])", s["operation"], re.I)):
                raise ValueError("verified teaching operation reveals the requested final value")
        ids.add(s["id"])
        operations.add(s["operation"])
    return packet


def checked_step_selection(proposal, options):
    decision = proposal.get("decision")
    if not isinstance(decision, str) or not decision.startswith("select:"):
        return None
    selected = options.get(decision.removeprefix("select:"))
    if (not selected or not isinstance(proposal.get("reason"), str) or not proposal["reason"].strip()
            or set(proposal) - {"decision", "reason", "bridge", "hint", "question_quote"}
            or any(not isinstance(proposal.get(k, ""), str) for k in ("bridge", "hint", "question_quote"))):
        raise ValueError("selection must identify one eligible operation")
    # Freewriting fields only apply to the 'offer' variant. Retain the full raw receipt;
    # selection and contextual review always refer to the immutable source operation.
    return selected


def checked_step_review(assessment, selected, evidence, previous_id):
    if (not isinstance(assessment, dict) or set(assessment) != {"decision", "evidence_ids", "reason"}
            or assessment["decision"] not in ("release", "withhold")
            or not isinstance(assessment["reason"], str) or not assessment["reason"].strip()
            or not isinstance(assessment["evidence_ids"], list)):
        raise ValueError("invalid verified operation assessment")
    anchor = f"task-span:{selected['id']}"
    cited = assessment["evidence_ids"]
    if any(not isinstance(ref, str) or ref not in {*evidence, anchor} for ref in cited):
        raise ValueError("unknown verified operation evidence reference")
    if assessment["decision"] == "withhold":
        return None
    if anchor not in cited or previous_id and previous_id not in cited:
        raise ValueError("operation review did not ground the task and required prior disclosure")
    citations = []
    for ref in dict.fromkeys(cited):
        if ref == anchor:
            citations.append({"evidence_id": "question", "quote": selected["quote"], "span": selected["span"]})
        else:
            text = evidence[ref]["text"]
            citations.append({"evidence_id": ref, "quote": text, "span": [0, len(text)]})
    return citations
