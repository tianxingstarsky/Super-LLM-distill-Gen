"""One explicit repair turn in the original author conversation, never a pass waiver."""
from copy import deepcopy
import json

from ..domain.authored_submission import SUBMIT_AUTHORED, authored_submission_tool, assemble_authored_submission
from ..domain.reasoning_checklist import checklist_digest


class RepairAuthoredSubmission:
    def __init__(self, port, editor=None):
        self.port = port
        self.editor = editor

    def recover_edit(self, key, submission_key, *, anchors=()):
        """Explicit one-turn recovery of a failed patch; no edit was committed."""
        from ..domain.authored_edits import EDIT_AUTHORED, apply_authored_edits
        record = self.editor.load_submission(submission_key)
        session, assistant = record["author_session"], record["assistant"]
        base = record.get("edit_base")
        calls = assistant.get("tool_calls", [])
        if (not isinstance(base, dict) or len(calls) != 1
                or calls[0].get("function", {}).get("name") != EDIT_AUTHORED
                or not isinstance(calls[0].get("id"), str) or not calls[0]["id"]):
            raise ValueError("authored_edit_recovery_unbound")
        issue = None
        try:
            apply_authored_edits(base, assistant, answer_required=False)
        except (ValueError, TypeError) as error:
            issue = str(error)
        if issue is None:
            raise ValueError("authored_edit_recovery_without_failure")
        if any(not isinstance(text, str) or not text or text not in base["reasoning"] for text in anchors):
            raise ValueError("authored_edit_recovery_unbound_anchor")
        observation = {"accepted": False, "applied_edits": 0,
            "protocol_issue": issue, "base_sha256": checklist_digest(base),
            "exact_original_anchors": list(anchors),
            "action": "The previous patch failed atomically; no proposed change was applied. "
                "Correct the failed edit targets against the exact original reasoning already supplied. "
                "Keep the intended sound edits and submit the complete replacement patch against this same base, "
                "with a complete updated reasoning_trace. Do not paraphrase old_text, skip validation, "
                "delete reasoning, change the fixed answer or resubmit the whole body. "
                "Supplied anchors are verbatim locations, not new facts or quality approval."}
        messages = [*deepcopy(session["messages"]), deepcopy(assistant),
            {"role": "tool", "tool_call_id": calls[0]["id"], "content": json.dumps(observation, ensure_ascii=False)}]
        identity = checklist_digest([session, assistant, observation])
        self.port.check_reasoning_cancel()
        reply = self.editor.turn([*key, "authored_edit_recovery_v1", identity], session, messages, session["tools"])
        candidate, patch = apply_authored_edits(base, reply["assistant"], answer_required=False)
        return candidate, {"version": 1, "mode": "same_author_failed_patch_recovery", "model_turns": 1,
            "semantic_status": "not_assessed", "original_base": deepcopy(base),
            "original_assistant": deepcopy(assistant), "revised_assistant": deepcopy(reply["assistant"]),
            "author": session["author"], "candidate_sha256": checklist_digest(candidate),
            "patch": patch, "observation": observation, "message_prefix_preserved": True,
            "tools_preserved": True, "response_details": deepcopy(reply.get("response_details", {}))}

    def edit(self, key, submission_key, *, findings, instruction=""):
        """One explicit local edit, using the original stored model conversation."""
        from ..domain.authored_edits import EDIT_AUTHORED, authored_edit_tool, apply_authored_edits
        record = self.editor.load_submission(submission_key)
        session, assistant = record["author_session"], record["assistant"]
        tools = deepcopy(session["tools"])
        if "edit_base" in record:
            answer_required = False
            candidate, _ = apply_authored_edits(record["edit_base"], assistant, answer_required=False)
        else:
            submission = next(t for t in tools if t["function"]["name"] == SUBMIT_AUTHORED)
            answer_required = "answer" in submission["function"]["parameters"]["properties"]
            candidate = assemble_authored_submission(assistant, answer_required=answer_required)
        if not any(t["function"]["name"] == EDIT_AUTHORED for t in tools):
            # Earlier sessions can still be edited, but a changed tool prefix
            # must not be reported as identical or guaranteed to hit cache.
            tools.append(authored_edit_tool(answer_required=answer_required))
        original_text = candidate["reasoning"] + "\n" + candidate.get("answer", "")
        if not findings:
            raise ValueError("authored_edit_without_issue")
        for finding in findings:
            if (set(finding) != {"quote", "issue"}
                    or not all(isinstance(v, str) and v.strip() for v in finding.values())
                    or finding["quote"] not in original_text):
                raise ValueError("authored_edit_unbound_finding")
        observation = {"accepted": False, "base_sha256": checklist_digest(candidate),
            "findings": deepcopy(findings), "instruction": instruction,
            "action": "Use edit_authored_candidate to edit this existing submission. Preserve sound reasoning. "
                "Check objections against the actual task and evidence; do not invent supporting conditions. "
                "Replace only uniquely matching text, update dependent claims and the evidence trace together. "
                "Keep the original language and role constraints. No whole-candidate resubmission or external prose. "
                "Edits do not imply independent quality approval."}
        messages = [*deepcopy(session["messages"]), deepcopy(assistant),
            {"role": "tool", "tool_call_id": assistant["tool_calls"][0]["id"],
             "content": json.dumps(observation, ensure_ascii=False)}]
        if tools != session["tools"]:
            messages.append({"role": "system", "content":
                "The initial full-submission stage has ended. For this explicit editing stage use "
                "edit_authored_candidate for local replacements, rather than resubmitting the full candidate. "
                "All original task, source, language and role constraints remain in force."})
        identity = checklist_digest([session, assistant, observation])
        self.port.check_reasoning_cancel()
        reply = self.editor.turn([*key, "authored_local_edit_v1", identity], session, messages, tools)
        revised, patch = apply_authored_edits(candidate, reply["assistant"], answer_required=answer_required)
        return revised, {"version": 1, "mode": "same_author_local_edit", "model_turns": 1,
            "semantic_status": "not_assessed", "author": session["author"],
            "original_candidate": candidate, "candidate_sha256": checklist_digest(revised),
            "patch": patch, "observation": observation,
            "original_assistant": deepcopy(assistant), "revised_assistant": deepcopy(reply["assistant"]),
            "message_prefix_preserved": True, "tools_preserved": tools == session["tools"],
            "response_details": deepcopy(reply.get("response_details", {}))}

    def execute(self, key, messages, assistant, *, answer_required, findings=()):
        calls = assistant.get("tool_calls", [])
        if (len(calls) != 1 or calls[0].get("function", {}).get("name") != SUBMIT_AUTHORED
                or not isinstance(calls[0].get("id"), str) or not calls[0]["id"]):
            raise ValueError("authored_repair_unbound_submission")
        # Findings must identify actual prior text. They request reconsideration,
        # never inject a replacement answer or certify a proposed condition.
        original = assistant.get("content", "") + "\n" + calls[0]["function"].get("arguments", "")
        bound_findings = []
        for finding in findings:
            if (set(finding) != {"quote", "issue"} or not all(isinstance(v, str) and v.strip()
                    for v in finding.values())
                    or (finding["quote"] not in original
                        and json.dumps(finding["quote"], ensure_ascii=False)[1:-1] not in original)):
                raise ValueError("authored_repair_unbound_finding")
            bound_findings.append(deepcopy(finding))
        protocol_issue = None
        try:
            assemble_authored_submission(assistant, answer_required=answer_required)
        except (ValueError, TypeError) as error:
            protocol_issue = str(error)
        if not protocol_issue and not bound_findings:
            raise ValueError("authored_repair_without_issue")
        observation = {"accepted": False, "protocol_issue": protocol_issue, "findings": bound_findings,
            "instruction": "Revise the existing submission in this same conversation. Retain the complete reasoning "
            "and all sound arguments; correct unsupported inferences rather than adding compensating caveats. "
            "Findings are objections to test against the user's evidence, not facts or replacement premises. "
            "Recheck the actual revised claims. Submit the full reasoning and trace in exactly one tool call. "
            "Do not output any ordinary assistant content, preamble or JSON fence outside the tool arguments. "
            "Any useful argument from earlier ordinary content belongs in the complete reasoning. "
            "No independent quality approval is implied. Keep the task's language, role and evidence boundaries."}
        history = [*deepcopy(messages), deepcopy(assistant), {"role": "tool", "tool_call_id": calls[0]["id"],
            "content": json.dumps(observation, ensure_ascii=False)}]
        tools = [authored_submission_tool(answer_required=answer_required)]
        binding = checklist_digest([history, tools])
        self.port.check_reasoning_cancel()
        revised = self.port.generation_source_turn([*key, "authored_submission_repair_v1", binding],
            history, tools, final=True)
        candidate = assemble_authored_submission(revised, answer_required=answer_required)
        return candidate, {"version": 1, "mode": "explicit_same_conversation_repair", "binding": binding,
            "semantic_status": "not_assessed", "original_assistant": deepcopy(assistant),
            "observation": observation, "revised_assistant": deepcopy(revised), "model_turns": 1}
