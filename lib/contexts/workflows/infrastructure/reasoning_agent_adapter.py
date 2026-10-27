"""Checkpointed native tool turns using the node's frozen prompt and shared model admission."""
from copy import deepcopy

from ..domain.reasoning_checklist import checklist_digest


class CheckpointedReasoningAgent:
    def __init__(self, runtime):
        self.runtime = runtime

    def check_reasoning_cancel(self):
        self.runtime.check_cancel()

    def prepare_reasoning_context(self, context, *, include_source_scope=False):
        from lib.infrastructure.model_context_prefix import split_context_request
        clean = {k: deepcopy(v) for k, v in context.items() if k != "feedback"}
        clean.pop("source_import_verified", None)
        manifest = (clean.get("source") or {}).get("coverage_manifest") if isinstance(clean.get("source"), dict) else None
        if isinstance(manifest, dict):
            import hashlib
            for entry in self.runtime.recipe.get("sources", []):
                if entry.get("sha256") == manifest.get("original_sha256"):
                    snapshot = self.runtime.path / "inputs" / entry["file"]
                    if hashlib.sha256(snapshot.read_bytes()).hexdigest() != entry["sha256"]:
                        raise ValueError("source_snapshot_changed")
                    clean["source_import_verified"] = True
                    break
        prepared = split_context_request(self.runtime.path, clean,
            protocol_version=self.runtime.recipe.get("source_memory_protocol_version", 2))
        if not prepared["teacher_context_pack"]:
            return clean
        projected = prepared["task_payload"]
        # Resolve authenticated packs and selected line scopes before advertising
        # read tools. Do not let cached focus text substitute for the actual pack.
        projected["source"] = {"materials": [
            {k: deepcopy(v) for k, v in material.items()
             if k in {"material_id", "source_id", "text", "numbered_text", "start_line", "end_line"}}
            for pack in prepared["teacher_context_pack"] for material in pack["materials"]]}
        if include_source_scope and any(pack.get("source_line_scope") == "selected" for pack in prepared["teacher_context_pack"]):
            projected["source_line_scope"] = "selected"
        projected.pop("source_context", None)
        return projected

    def reasoning_review_identity(self):
        return checklist_digest({"prompt": self.runtime.prompt_text("workflow.cot_check_agent", stage="cot"),
            "models": self.runtime.recipe.get("node_models", {}).get("cot", {}), "policy": 1})

    def reasoning_progress(self, role, tool, pending, issue):
        self.runtime.event("reasoning_agent_progress", role=role, tool=tool,
                           pending_rules=len(pending["rule_ids"]), pending_claims=len(pending["anchor_ids"]),
                           issue=issue)

    def reasoning_tool_turn(self, key, role, messages, tools):
        runtime = self.runtime
        prompt = runtime.prompt_text("workflow.cot_check_agent", stage="cot")
        identity = "独立评审：重新判断原稿，不存在可供依赖的作者通过意见。" if role == "jev" else "作者自检：优先查找能改变结论的疑点，诚实记录缺口。"
        prepared = [{"role": "system", "content": prompt + "\n" + identity}, *messages]
        return self.prepared_tool_turn(key, role, prepared, tools,
            recover_arguments=runtime.recipe.get("node_generation", {}).get("cot", {}).get("self_check", {}).get("version") in (4, 5, 6))

    def prepared_tool_turn(self, key, role, prepared, tools, *, recover_arguments=False, final=False, strict=False,
                           request_options=None, capture_details=False):
        """Transport a prepared author or reviewer turn; never add role instructions here."""
        runtime = self.runtime
        call_key = [*key, checklist_digest(prepared), checklist_digest(tools)]
        if final and not strict:
            call_key.append("tools_disabled")
        if strict:
            call_key.append("strict_tools_v1")
        if request_options is not None or capture_details:
            call_key.extend(["author_continuation_v1", checklist_digest(request_options), capture_details])
        journal = None

        def invoke():
            nonlocal journal
            from lib.infrastructure.workflow_stream_journal import StreamJournal
            client = runtime.client(role, stage="cot")
            binding = runtime.recipe.get("node_models", {}).get("cot", {}).get(role, {})
            with runtime._client_locks[id(client)]:
                before = dict(getattr(client, "usage", {}))
                runtime.check_cancel()
                runtime.event("model_started", role=role)
                journal = StreamJournal(runtime.path, stage=runtime.stage, unit=checklist_digest(key)[:16],
                           role=role, run_attempt=runtime.state.get("attempt", 0), checkpoint=checklist_digest(call_key))
                try:
                    # tool_turn already uses ExecuteToolTurn and the model's shared concurrency/cooldown limits.
                    details = {}
                    assistant = client.tool_turn(deepcopy(prepared), deepcopy(tools),
                        **(request_options if request_options is not None else
                           {"max_tokens": binding.get("max_output_tokens", 32768)}),
                        **({"recover_arguments": True} if recover_arguments else {}),
                        **({"tool_choice": "none"} if final and not strict else {}),
                        **({"strict": True, "retries": 1} if strict else {}),
                        **({"response_details": details} if capture_details else {}),
                        **({"on_stream": journal} if journal is not None else {}))
                    return {"assistant": assistant, "response_details": details} if capture_details else assistant
                finally:
                    after = getattr(client, "usage", {})
                    with runtime._lock:
                        usage = runtime.state.setdefault("usage", {}).setdefault(role, {})
                        for metric in ("calls", "prompt_tokens", "completion_tokens"):
                            usage[metric] = usage.get(metric, 0) + max(0, after.get(metric, 0) - before.get(metric, 0))
                    runtime.event("model_finished", role=role)

        try:
            assistant = runtime.checkpoint(call_key, invoke)
        except BaseException:
            if journal is not None:
                try:
                    journal.interrupted()
                except (OSError, ValueError):
                    pass
            raise
        if journal is not None:
            try:
                journal.completed()
            except (OSError, ValueError):
                pass
        return assistant
