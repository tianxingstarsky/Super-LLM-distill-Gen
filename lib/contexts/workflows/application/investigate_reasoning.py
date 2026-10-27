"""Let a reviewer choose tools and submit evidence incrementally in one resumable session."""
from copy import deepcopy
import json

from ..domain.reasoning_checklist import checklist_digest
from ..domain.reasoning_investigation import ReasoningInvestigation, investigation_tools


class InvestigateReasoning:
    def __init__(self, port, config):
        self.port, self.config = port, config

    def execute(self, key, context, candidate, *, phase):
        context = self.port.prepare_reasoning_context(context)
        body = {k: candidate[k] for k in ("reasoning", "answer") if k in candidate}
        audit = {"version": self.config["version"], "phase": phase, "passed": False,
                 "candidate_sha256": checklist_digest(body), "rules_sha256": checklist_digest(self.config["rules"]),
                 "context_sha256": checklist_digest(context), "rules": deepcopy(self.config["rules"])}
        for role, label in (("generation", "self_check"), ("jev", "independent_check")):
            # Separate histories: a reviewer never receives the author's positive votes.
            factory = ReasoningInvestigation
            if self.config["version"] == 4:
                from ..domain.reasoning_evidence import ReasoningEvidence
                factory = ReasoningEvidence
            if self.config["version"] == 5:
                from ..domain.reasoning_workspace import ReasoningWorkspace
                workspace = ReasoningWorkspace(self.config["rules"], context, body, phase=phase)
            else:
                workspace = factory(self.config["rules"], context, body)
            audit["claim_anchors"] = deepcopy(workspace.anchors)
            outcome = self._session([*key, {5: "investigation_v3", 4: "investigation_v2"}.get(self.config["version"], "investigation_v1"), workspace.binding, label], role,
                                    workspace, context, phase)
            audit[label] = outcome["assessment"]
            audit[label + "_verification"] = outcome["verification"]
            audit[label + "_session"] = outcome
            if not outcome["passed"]:
                if self.config["version"] == 5 and any(r["status"] == "violated" for r in outcome.get("assertions", {}).values()):
                    return {**audit, "issue": "reasoning_agent_found_gap", "decision_basis": "deterministic_comparison"}
                if self.config["version"] in (4, 5) and outcome["failed"] and label == "self_check":
                    # A self-report can misbind a negated claim. Keep it intact,
                    # but require a blind independent finding before rewriting.
                    continue
                if self.config["version"] in (4, 5) and outcome["failed"] and label == "independent_check":
                    authored = audit["self_check_session"]
                    def defects(session):
                        assessment = session["assessment"]
                        return ({("rule", c["rule_id"]) for c in assessment["checks"] if c["status"] == "unmet"}
                                | {("rule", f["rule_id"]) for f in session.get("findings", []) if f["status"] == "violated"}
                                | {("claim", c["anchor_id"]) for c in session["verification"]["items"]
                                   if not c["passed"] and c["status"] != "uncertain"})
                    if not defects(authored).intersection(defects(outcome)):
                        return {**audit, "issue": "reasoning_agent_review_disagreement"}
                return {**audit, "issue": outcome["issue"]}
        if self.config["version"] in (4, 5) and not audit["self_check_session"]["passed"]:
            return {**audit, "issue": "reasoning_agent_review_disagreement"}
        return {**audit, "passed": True}

    def _session(self, key, role, workspace, context, phase):
        if self.config["version"] in (4, 5):
            from .reasoning_session import run_reasoning_session
            return run_reasoning_session(self.port, key, role, workspace, context, phase)
        inputs = {"phase": phase, "candidate": workspace.candidate, "rules": workspace.rules,
                  "claim_anchors": workspace.anchors, "source_index": workspace.source_index(),
                  "task": {k: deepcopy(v) for k, v in context.items()
                           if k in {"prompt", "question", "messages", "qa_contract", "generation_style"}}}
        messages = [{"role": "user", "content": json.dumps(inputs, ensure_ascii=False)}]
        tools = investigation_tools()
        trace, repeated, seen_calls = [], {}, set()
        # This is a resource ceiling, not an execution recipe or a passing condition.
        max_steps = min(128, max(12, 2 * (len(workspace.rules) + len(workspace.anchors)) + 4))
        issue = "reasoning_agent_step_limit"
        for step in range(max_steps):
            self.port.check_reasoning_cancel()
            assistant = self.port.reasoning_tool_turn([*key, step, checklist_digest(messages)], role,
                                                       deepcopy(messages), deepcopy(tools))
            if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
                issue = "reasoning_agent_invalid_assistant"
                break
            calls = assistant.get("tool_calls", [])
            if (not isinstance(calls, list) or not calls or len(calls) > 64
                    or any(not isinstance(c, dict) or not isinstance(c.get("id"), str)
                           or not c["id"] or c["id"] in seen_calls for c in calls)
                    or len({c["id"] for c in calls}) != len(calls)):
                issue = "reasoning_agent_missing_or_invalid_tools"
                break
            messages.append(deepcopy(assistant))  # Preserve native thought/signature/tool fields.
            before = workspace.pending()
            signatures = []
            for call in calls:
                self.port.check_reasoning_cancel()
                seen_calls.add(call["id"])
                function = call.get("function", {})
                try:
                    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                        raise ValueError
                    raw = function.get("arguments", "")
                    if not isinstance(raw, str) or len(raw) > 131072:
                        raise ValueError
                    arguments = json.loads(raw)
                except (ValueError, TypeError):
                    observation = {"ok": False, "issue": "invalid_tool_arguments"}
                else:
                    observation = workspace.execute(function["name"], arguments)
                signatures.append(checklist_digest(function))
                trace.append({"step": step, "call": deepcopy(call), "observation": deepcopy(observation)})
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(observation, ensure_ascii=False)})
                self.port.reasoning_progress(role, function.get("name") if isinstance(function, dict) else "invalid",
                                             workspace.pending(), observation.get("issue"))
            snapshot = workspace.snapshot()
            if snapshot["failed"]:
                issue = "reasoning_agent_found_gap"
                break
            if snapshot["blocked"]:
                issue = "reasoning_agent_evidence_unresolved"
                break
            if snapshot["passed"]:
                issue = None
                break
            signature = checklist_digest(sorted(signatures))
            repeated[signature] = repeated.get(signature, 0) + 1 if before == workspace.pending() else 0
            if repeated[signature] >= 3:
                issue = "reasoning_agent_no_progress"
                break
        return {**workspace.snapshot(), "issue": issue, "trace": trace,
                "turns": step + 1, "messages": messages, "binding": workspace.binding}
