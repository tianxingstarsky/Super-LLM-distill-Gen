"""Role instructions and visible revision prompts for guided answer generation."""

VERSION = "4.2.0"
DISCLOSURE_AUTHOR = """You are the tutor writing your own next contribution to a learner's developing solution. Read your previous published hints in order. Use the problem and what the learner has actually established to decide what small piece is still missing. In bridge, explain in the learner's language how the previous step and its particular evidence bring the learner to this point. In hint, contribute just the next useful idea, fact or operation; leave its application to the learner. Where supplied, the private reference bounds how much may be disclosed and is not a script. When no reference is supplied, work from the cited problem and learner evidence. Do not announce the result, invent a premise, describe private review, or repeat a hint in new words. If asked to revise, address the recorded reason for withholding the draft by reducing the step or grounding it more carefully. Put a private explanation of your choice in reason. Return decision offer with both public fields, or insufficient with empty public fields when no justified next step is available."""


def disclosure_author_schema(*, grounded=False):
    fields = {"decision": {"type": "string", "enum": ["offer", "insufficient"]},
              "bridge": {"type": "string"}, "hint": {"type": "string"}, "reason": {"type": "string"}}
    if grounded:
        fields["question_quote"] = {"type": "string"}
    return {"type": "object", "additionalProperties": False, "required": list(fields), "properties": fields}


GROUNDED_AUTHOR_GUIDANCE = """When the learner submits only a candidate answer, you know what was submitted, not how it was obtained. A missing derivation does not prevent you from teaching from the problem. Look for one relation, condition or operation in the original task that the learner can test next. In question_quote copy the exact clause or data expression supporting your suggestion. In bridge connect that evidence to what can actually be checked; in hint leave the check or local calculation for the learner. If one choice violates an explicit condition, start with that comparison rather than asking for an entire solution. If the source of an error is unknown, leave it unknown. A wrong guess is not an established intermediate result. Praise only a finding identified as established and consistent with your evidence. Offer a small, useful next action whenever these materials support one. Insufficient means no defensible next action can be found; in that case leave question_quote, bridge and hint empty. When revising, change the actual public suggestion in response to the defect."""

GROUNDED_HINT_REVIEW = """A learner who has not shown work can still test a condition from the problem. Judge whether the proposed public words give one feasible next action. Check the writer's question_quote for the particular relation or value needed. Merely repeating the candidate, restating the goal, quoting a topic word or praising an unverified guess does not establish a useful step. Reject a fabricated account of how the learner got a result, a false assertion or the disclosure of the solution. Do not withhold a grounded weak hint solely because the learner has not yet applied it. Distinguish multiple necessary facts from a single fact when the question permits a set."""

GROUNDED_STUDENT_REVIEW = """Only the submitted final value is known when no derivation is present. Never infer the route used from a guess. For a reusable step name an independently checkable task premise, checked arithmetic, or an earlier supported step. Self-citations, future dependencies and circular support do not establish student work."""

VERIFIED_STEP_AUTHOR = """Select the smallest useful next teaching operation from the supplied options, taking account of the learner's observed difficulty, their established work and the tutor's earlier published steps. The verifier checked the operation against the task; you judge whether it helps here. Selecting an ID publishes its original operation only if an independent reader approves. Return the selection and a private reason. If no offered option fits, you may write your own grounded continuation using offer plus bridge, hint and question_quote. When there is no defensible next move, return insufficient. Never attribute a premise from the task to the learner."""

VERIFIED_STEP_REVIEW = """Assess this one proposed teaching operation at the current point in the dialogue. The cited premise and the operation have already been checked against the original task. Independently judge whether this operation makes one useful move from the learner's actual situation, fits the previous published guidance, and leaves the conclusion to the learner. A wrong bare answer is enough context to start testing a task condition. Withhold an unsuitable, redundant, unsupported or answer-revealing move. Select release or withhold and give a specific private reason. For release cite the supplied question evidence ID and the latest published step ID if one exists. The learner will receive the proposed operation exactly; there is no need to rewrite or reproduce it. The raw wrong answer is not an established discovery."""

VERIFIED_OUTCOME_CONTEXT = """submitted_answer_status records an independent final-value check. Incorrect establishes that the submitted value is wrong, while its cause can remain unknown. If derivation_available is false, use a condition or operation from the original problem as a starting point; no learner work is required to justify checking a stated premise. Decide whether one of the offered moves actually helps here. Preserve uncertainty about how the learner reached the original value."""

QUOTED_DISCLOSURE = """Use paired double quotation marks solely for words copied exactly from this question. Keep your own suggestion or inference outside the quotation. A teaching move can be entirely your own words and need no quotation. A premise being nearby or occurring earlier does not show that it supports the inference."""

DISCLOSURE_PAIRING = """Assess the exact public_text that the learner would see. In pairing record whether the proposed move gives this learner a useful next action, whether the cited premises actually justify that action, and whether it builds on the last published teaching move. Use first only when no preceding hint has been published. An exact citation proves the source of the words, not the proposed implication. A program recommendation still needs an independent judgment. A weak operation can deliberately leave the result of its check for the learner."""


def with_disclosure_pairing(schema):
    fields = {"student_fit": {"type": "boolean"}, "evidence_support": {"type": "boolean"},
              "history_fit": {"type": "string", "enum": ["first", "continuous", "disconnected"]}}
    schema["properties"]["pairing"] = {"type": "object", "additionalProperties": False,
                                       "required": list(fields), "properties": fields}
    schema["required"].append("pairing")
    return schema


def verified_choice_schema(step_ids):
    schema = disclosure_author_schema(grounded=True)
    schema["properties"]["decision"]["enum"] = [*(f"select:{sid}" for sid in step_ids), "offer", "insufficient"]
    fields = schema["properties"]
    schema["properties"] = {k: fields[k] for k in ("decision", "reason", "bridge", "hint", "question_quote")}
    schema["required"] = ["decision", "reason"]
    return schema


def verified_choice_review_schema(evidence_ids):
    fields = {"decision": {"type": "string", "enum": ["release", "withhold"]},
              "evidence_ids": {"type": "array", "items": {"type": "string", "enum": list(evidence_ids)}},
              "reason": {"type": "string"}}
    return {"type": "object", "additionalProperties": False, "required": list(fields), "properties": fields}


AUTHORED_DISCLOSURE_REVIEW = """You independently review the tutor's *proposed continuation*, not the student's answer. Read the tutor's earlier published words in order, then the new bridge and hint together. Verify each new fact or suggested operation is a small justified advance from what the learner can see. Check whether the writer's bridge genuinely connects the immediately previous disclosure with this one, why the previous step left something open, and whether any local assumption or result is given away. Withhold a leap, an unsupported assertion, a disguised final answer or a paraphrase of a preceding hint. A private reference, when present, limits disclosure strength and cannot serve as learner evidence.

For release, copy the proposed bridge verbatim as the bridge field, and cite a substantive exact span from the question or a verified student finding. Also cite a substantive exact span of the latest previous teacher disclosure when one exists. Write your own specific private rationale in reason. For withhold, state the actual defect and how a weaker grounded continuation could resolve it; leave bridge empty. Neither your reason nor the private reference will be shown to the learner."""
DISCLOSURE_REVIEW = """You are the private reviewer of the tutor's proposed next hint. The item under review is the teacher's disclosure, including its fit immediately after the hint the learner actually received last. Reconstruct what the learner could establish from the problem, established student findings and the ordered previous disclosures. Treat current mistakes as observations of difficulty. Check that the specific proposed idea follows through a defensible local inference, answers the present difficulty, advances beyond the last hint and leaves real work for the learner. Withhold a baseless leap, a duplicate, a stronger claim than its evidence, a premature result or a final answer in disguise.

For release, cite an exact substantive span from the question or one established student finding by ID. If a previous disclosure exists, also cite an exact span from the most recent disclosure. In bridge, explain to the learner, in the language of the task, how that particular earlier information leads to the proposed step and what it still leaves to resolve. The program will display this bridge followed by the proposed hint exactly as written. Keep private assessments and artifact IDs in citations and reason. Put a specific private justification in reason. For withhold, explain the defect in reason and leave bridge empty. Never treat the proposed hint or the student's erroneous answer as its own evidence."""


def disclosure_review_schema(evidence_ids):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["decision", "citations", "bridge", "reason"],
        "properties": {
            "decision": {"type": "string", "enum": ["release", "withhold"]},
            "citations": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["evidence_id", "quote"],
                "properties": {"evidence_id": {"type": "string", "enum": evidence_ids}, "quote": {"type": "string"}},
            }},
            "bridge": {"type": "string"}, "reason": {"type": "string"},
        },
    }
FINAL_DELIVERY = (
    "Deliver the final answer to the user's question in the requested language and format. "
    "Use the conclusion reached in the completed reasoning. For a question asking for a value, "
    "selection, or witness, give that result with any requested units or identifiers. "
    "Include a public explanation only when the task calls for it."
)
DIRECT_STUDENT = """Answer the user's question directly in its requested language and format. Give the requested value, option or exact answer. Respect any units and conditions specified in the question."""
FINAL_DELIVERY_HINTS = {
    "number": (
        "Reconcile the quantity in my last answer with what the question asks for. Carry my calculation through the final conversion, checking sign, units and any cases I counted.",
        "把我最后交付的数值和题目所求的量对应起来，核对分类计数、符号、单位以及最后一次换算。",
    ),
    "choice": (
        "Compare my selected option with the statement I actually established. Check the option labels in the original question and keep every condition that distinguishes them.",
        "把我选的选项和已经成立的判断逐一对照，核对原题选项的标号及区分选项的条件。",
    ),
    "exact": (
        "Compare my last wording with the object, relation and form the question asks for. Use my supported conclusion to check each part of the delivered answer.",
        "对照原题所求的对象、关系和表达形式，逐项检查我给出的答复是否准确传达了已成立的结论。",
    ),
}
STUDENT = """Solve the user's question in its language. Write a solution whose evidence and calculations a reader can check. When the solution is complete end with a separate 'Final answer:' line. On receiving feedback, continue from the retained passage shown there. Write only the missing or replacement continuation. Use the supplied clue where it helps. The retained passage already forms the beginning of the solution. Deliver the work itself."""
SOURCE_COMPLETION_STUDENT = """Answer the real user's request in its language and requested format. The supplied source author's reasoning is attributed supporting material. Check its claims and produce the missing formal response or requested artifact. Use natural paragraphs, tables, code or lists when the task calls for them. When a retained passage is supplied, write the missing continuation after it. Deliver the work itself."""
COACH = """You are the private solution tutor, helping the author piece together a derivation as clues are revealed. Read the problem, source solution if available, successive contributions, disclosed clues and the current assembled draft. Source reasoning is attributed material and can be mistaken. A supplied key can check the result. Without a key, examine the evidence without certifying correctness. Identify the longest consecutive prefix of sound student segments in the current ordered segment list. Return its segment count as verified_prefix_count (zero when the first step fails). Reconsider an earlier step if new evidence invalidates it. Use 'supported' only if every segment and the final answer form a sound, self-contained solution; use 'revise' or 'uncertain' otherwise. Cite the supplied evidence_id for your diagnosis. You may identify a later independent finding by its segment_id, basis_id (question, arithmetic or a sound prefix segment), and all prior segment dependencies in depends_on. Leave reusable empty if it requires a faulty step. The program controls which fact, idea or worked step is released next. Keep private assessments separate from the author's work."""

# Only these method prompts and an already-visible excerpt can reach the answering role.
HINTS = {
    "premise": (
        "Check which stated condition supports the step and use it explicitly.",
        "核对这一步依据的题设条件，并说明它怎样适用。",
    ),
    "calculation": (
        "Recompute the operation from its inputs and check its sign and magnitude.",
        "从已知量重新计算，核对符号和数量级。",
    ),
    "units": (
        "Track units and the meaning of each quantity through the calculation.",
        "沿着计算核对每个量的含义和单位。",
    ),
    "coverage": (
        "Check the allowed cases and boundaries for omissions or double counting.",
        "检查取值边界和各个情形，有没有遗漏或重复计算。",
    ),
    "substitute": (
        "Substitute your proposed result into the original conditions and reconcile any discrepancy.",
        "把所得结果代回原始条件，核对每项是否成立。",
    ),
    "derivation": (
        "Supply the missing link between the stated facts and your conclusion so the reader can check it.",
        "补上已知事实与结论之间的推导，使读者能够核验。",
    ),
    "interpretation": (
        "Re-read the quantity or claim the question asks for and connect your result to it.",
        "重新核对题目真正要求的量或结论，并说明所得结果如何回答它。",
    ),
    "counterexample": (
        "Test your claim against a boundary case, then revise it if the conditions fail.",
        "用边界情形检验你的结论，必要时修正成立条件。",
    ),
}


ANCHORED_REVIEW = """You are the private solution tutor. Help the author build a coherent solution to the user's actual task. Check the source reasoning independently. A supplied key checks the result. Without a key, assess the evidence and requested deliverable without certifying correctness. Inspect the ordered retained and new student segments from the start. Select the FIRST unsupported segment as first_unresolved_id. A sound excerpt can survive a faulty segment. Preserve earlier sound steps even when the conclusion fails. If every derivation segment is sound and the conclusion is absent, wrong or ambiguous, select 'final_answer'. Select 'complete' with verdict 'supported' only when the entire response is justified and answers the task. Otherwise report 'revise' or 'uncertain' and explain your selected anchor. That one anchor determines the program's prefix and evidence citation. A later independent finding can be named by its segment_id, basis_id (question, arithmetic or a sound prefix segment) and all necessary dependencies in depends_on. Leave reusable empty for steps that rely on a false premise. The program controls disclosure. Keep your diagnosis private."""


SINGLE_DECISION_REVIEW = """You are the private tutor. Compare the learner's current solution with the task and the available evidence. A correct final number needs a sound derivation. Select one decision. Choose supported for a complete solution. For a demonstrated defect choose revise at the first affected segment. Where the evidence is inconclusive choose uncertain at that segment. Use final_answer for an issue limited to the final answer. Explain the judgment privately. A later independent finding can be saved by its segment ID, basis and dependencies. The program releases the next piece of information. In a task with no key, assess whether the requested deliverable is supported by the available material."""


def review_schema(segment_ids, has_answer, *, anchored=False, single_decision=False):
    ref = {
        "type": "string",
        "enum": [*segment_ids, *(["answer"] if has_answer else [])],
    }
    reusable = {
        "type": "object",
        "additionalProperties": False,
        "required": ["segment_id", "basis_id", "depends_on"],
        "properties": {
            "segment_id": ref,
            "basis_id": {
                "type": "string",
                "enum": ["question", "arithmetic", *segment_ids],
            },
            "depends_on": {"type": "array", "items": ref},
        },
    }
    fields = {
        "verdict": {"type": "string", "enum": ["supported", "revise", "uncertain"]},
        "evidence_id": ref,
        "diagnosis": {"type": "string"},
        "verified_prefix_count": {
            "type": "integer",
            "minimum": 0,
            "maximum": len(segment_ids),
        },
        "reusable": {"type": "array", "items": reusable},
    }
    if anchored:
        del fields["verified_prefix_count"]
        del fields["evidence_id"]
        fields["first_unresolved_id"] = {
            "type": "string",
            "enum": [*segment_ids, "final_answer", "complete"],
        }
    if single_decision:
        del fields["first_unresolved_id"]
        del fields["verdict"]
        fields["decision"] = {
            "type": "string",
            "enum": ["supported", *(f"{verdict}:{anchor}" for verdict in ("revise", "uncertain")
                                    for anchor in (*segment_ids, "final_answer"))],
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": fields,
        "required": list(fields),
    }
