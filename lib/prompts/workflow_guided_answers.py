"""Role instructions and visible revision prompts for guided answer generation."""

VERSION = "3.5.0"
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
