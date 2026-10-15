"""One durable source-scoped director conversation with real read-only tools."""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import json
from time import monotonic

from filelock import FileLock, Timeout

from lib.domain.workflow_quality import canonical
from lib.infrastructure.knowledge_agent_session import (
    KnowledgeAgentSession, digest, knowledge_identity, knowledge_task_identity,
)
from lib.infrastructure.knowledge_agent_tools import KnowledgeAgentTools

MAX_TOOL_ROUNDS = 4
MAX_CALLS_PER_ROUND = 6


class WorkflowKnowledgeAgent:
    def knowledge_agent_enabled(self):
        return self.recipe.get("knowledge_agent_context_version") == 1

    def knowledge_store(self):
        with self._lock:
            if not hasattr(self, "_knowledge_store"):
                self._knowledge_store = KnowledgeAgentSession(self.path.parent.parent / ".knowledge-agent/contexts.sqlite3")
            return self._knowledge_store

    @contextmanager
    def knowledge_planning_lock(self, runtime):
        """Serialize one agent's read/plan/commit across jobs, not all models."""
        session = runtime[1]["session_id"]
        store = self.knowledge_store()
        lock = FileLock(store.path.parent / ("planning-" + session + ".lock"))
        binding = self.recipe.get("node_models", {}).get("director", {}).get("generation", {})
        timeout = binding.get("request_queue_timeout_seconds", 300)
        deadline = monotonic() + timeout
        waiting = False
        while True:
            self.check_cancel()
            try:
                lock.acquire(timeout=0.25)
                break
            except Timeout:
                if not waiting:
                    self.event("knowledge_agent_waiting", session_id=session)
                    waiting = True
                if monotonic() >= deadline:
                    raise ValueError("knowledge_agent_planning_timeout") from None
        try:
            self.check_cancel()
            yield
        finally:
            lock.release()

    def prepare_knowledge_agent(self, guided, published_history, stages):
        if not self.knowledge_agent_enabled():
            return None
        materials = {}
        for unit in guided:
            for material in self._director_evidence_materials(unit) or []:
                materials[material["material_id"]] = material
        if not materials:
            return None  # Open briefs have no authenticated clean knowledge set.
        values = list(materials.values())
        knowledge = knowledge_identity(values)
        binding = self.recipe.get("node_models", {}).get("director", {}).get("generation", {})
        # Replay only under the same operating rules. Batch size and recall
        # limits change scheduling volume, not what old instructions mean.
        rules = {key: value for key, value in self.recipe.get("qa_director", {}).items()
                 if key not in {"batch_size", "history_limit", "context_mode", "tools_mode"}}
        session_scope = [knowledge, binding, self.recipe.get("knowledge_agent_instruction"),
                          self.prompt_text("workflow.qa_director"), self.recipe.get("agent_experience"),
                          self.recipe.get("endpoint_pins", {}).get("director", {}).get("generation"),
                          sorted(materials),
                          self.recipe.get("qa_director", {}).get("tools_mode", "auto")]
        if self.recipe.get("knowledge_agent_session_rules_version") == 1:
            session_scope.extend([self.recipe.get("node_prompt_system"), rules])
        session = digest(session_scope)
        descriptor = {"version": 1, "knowledge_id": knowledge, "session_id": session}
        store = self.knowledge_store()
        source_ids = {material["source_id"] for material in values}
        for stage in stages:
            from lib.infrastructure.knowledge_history_import import verified_knowledge_history
            examples = published_history.source_recent(source_ids, limit=20, namespace=stage)
            if not hasattr(self, "_knowledge_imported"):
                self._knowledge_imported = set()
            import_key = (knowledge, stage, digest([(old["id"], old["run_id"]) for old in examples]))
            if import_key in self._knowledge_imported:
                continue
            for old in verified_knowledge_history(self.path.parent.parent, examples, knowledge):
                identity = old["task_identity"]
                candidate_id = old["candidate_id"]
                duplicate = store.claim(knowledge, identity, run_id=old["run_id"], sample_id=candidate_id,
                    question=old["question"], qa_type=old["qa_type"], topic="")
                if duplicate is None:
                    store.outcome(knowledge, identity, run_id=old["run_id"], sample_id=candidate_id,
                                  stage="published_history", status="final_eligible")
            self._knowledge_imported.add(import_key)
        for unit in guided:
            unit["knowledge_agent_context"] = deepcopy(descriptor)
        self.state.setdefault("qa_director", {})["knowledge_context"] = {
            **descriptor, "mode": "persistent", "history_is_not_evidence": True,
            "tool_calls": self.state.get("qa_director", {}).get("knowledge_context", {}).get("tool_calls", 0)}
        return values, descriptor

    def _knowledge_coverage(self, knowledge):
        values = self.knowledge_store().coverage(knowledge)["counts"]
        accepted = Counter()
        for row in values:
            if row["status"] == "final_eligible":
                accepted[row["qa_type"]] += row["count"]
        return {"ledgers": [{"scope": "same_knowledge_final_results", "namespace": "logical_tasks",
                             "accepted_by_type": dict(accepted), "accepted_dimensions": {}}]}

    def knowledge_agent_plan(self, key, data, runtime, *, instruction=None):
        from lib.infrastructure.model_context_prefix import split_context_request, NODE_RULE_HEADER
        from lib.infrastructure.source_memory_messages import source_memory_messages
        from lib.infrastructure.workflow_node_messages import split_node_rules
        from lib.infrastructure.workflow_agent_experience import experience_system
        materials, descriptor = runtime
        store = self.knowledge_store()
        knowledge, session = descriptor["knowledge_id"], descriptor["session_id"]
        operation = digest([self.state["id"], key])
        tools = KnowledgeAgentTools(materials,
            history_search=lambda **arguments: store.search(knowledge, **arguments),
            coverage=lambda: self._knowledge_coverage(knowledge))
        client = self.client("generation", stage="director")
        use_tools = (self.recipe["qa_director"].get("tools_mode", "auto") == "auto"
                     and getattr(client, "model_info", {}).get("tools") is not False)
        schemas = tools.schemas() if use_tools else []
        binding = self.recipe.get("node_models", {}).get("director", {}).get("generation", {})
        context = binding.get("context_window_tokens", 131072)
        output = binding.get("max_output_tokens", 32768)

        def request_snapshot():
            rules, task = split_node_rules(data, instruction=instruction, qa_director=self.recipe.get("qa_director"))
            task = deepcopy(task)
            task["knowledge_agent_memory"] = store.coverage(knowledge)
            system = (self.recipe["node_prompt_system"] + NODE_RULE_HEADER + "\n"
                + self.prompt_text("workflow.qa_director") + experience_system(self.recipe, "workflow.qa_director")
                + rules + "\n" + self.recipe["knowledge_agent_instruction"])
            messages = source_memory_messages(system,
                split_context_request(self.path, task, protocol_version=self.recipe.get("source_memory_protocol_version", 2)))
            fixed = len(canonical(messages).encode()) + len(canonical(schemas).encode()) + 4096
            available = context - output - fixed
            if available < 0:
                raise ValueError("qa_director_context_too_small")
            history = store.history(session, max_bytes=min(32768, available), exclude_operation=operation)
            current = messages.pop()
            messages.extend(history)
            messages.append(current)
            self.state["qa_director"]["knowledge_context"]["retained_messages"] = len(history)
            return messages
        messages = self.checkpoint(["knowledge_agent_inputs", key], request_snapshot)
        start = len(messages) - 1
        history_start = next((index for index, message in enumerate(messages[:start])
                              if message.get("role") == "user"), start)

        def trim_to_fit(extra=()):
            """Evict whole prior turns; keep the current tool chain unchanged."""
            nonlocal start
            from lib.model_tool_calls import tool_context_input_bytes
            def fits():
                return tool_context_input_bytes([*messages, *extra], schemas) + output <= context
            while not fits() and history_start < start:
                end = history_start + 1
                while end < start and messages[end].get("role") != "user":
                    end += 1
                del messages[history_start:end]
                start -= end - history_start
            self.state["qa_director"]["knowledge_context"]["retained_messages"] = start - history_start
            return fits()

        for round_number in range(MAX_TOOL_ROUNDS + 1):
            self.check_cancel()
            if not trim_to_fit():
                raise ValueError("knowledge_agent_context_exhausted")
            call_key = ["knowledge_agent_turn", key, round_number, digest(messages), digest(schemas)]
            assistant = self._knowledge_model_turn(call_key, messages, schemas, binding)
            if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
                raise ValueError("knowledge_agent_invalid_assistant")
            messages.append(assistant)
            calls = assistant.get("tool_calls") or []
            if not calls:
                from lib.llm_client import parse_json_robust
                try:
                    value = parse_json_robust(assistant.get("content", ""))
                except ValueError:
                    from lib.model_request_reliability import ModelJSONError
                    raise ModelJSONError(1) from None
                if not hasattr(self, "_knowledge_pending"):
                    self._knowledge_pending = {}
                self._knowledge_pending[digest(key)] = (session, operation, deepcopy(messages[start:]))
                return value
            if round_number >= MAX_TOOL_ROUNDS or not use_tools or len(calls) > MAX_CALLS_PER_ROUND:
                raise ValueError("knowledge_agent_tool_limit")
            seen = set()
            for call in calls:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str) or call["id"] in seen:
                    raise ValueError("knowledge_agent_invalid_tool_call")
                seen.add(call["id"])
                function = call.get("function") or {}
                name = function.get("name")
                executed = False
                def execute():
                    nonlocal executed
                    executed = True
                    self.check_cancel()
                    try:
                        arguments = json.loads(function.get("arguments", ""))
                        result = tools.execute(name, arguments)
                    except (json.JSONDecodeError, ValueError) as error:
                        # Recover argument mistakes only; integrity failures remain fatal.
                        if "integrity" in str(error) or "hash" in str(error):
                            raise
                        result = {"ok": False, "error": "invalid_knowledge_tool_request",
                                  "instruction": "Use only the advertised tool names, fields and bounded source line ranges."}
                    return result
                result = self.checkpoint(["knowledge_agent_tool", key, round_number, call["id"], digest(function)], execute)
                def receipt(value):
                    return {"role": "tool", "tool_call_id": call["id"], "content": canonical(value)}
                if not trim_to_fit([receipt(result)]):
                    result = {"ok": False, "error": "knowledge_context_result_too_large",
                              "result_not_transmitted_sha256": digest(result),
                              "instruction": "Request a narrower source line range or fewer search results."}
                if not trim_to_fit([receipt(result)]):
                    raise ValueError("knowledge_agent_context_exhausted")
                messages.append(receipt(result))
                metric = self.state["qa_director"]["knowledge_context"]
                if executed:
                    metric["tool_calls"] += 1
                    metric["last_tool"] = name
                    self.event("knowledge_agent_tool_completed", tool=name, result_sha256=digest(result))
                    self.save(force=False)
        raise ValueError("knowledge_agent_tool_limit")

    def _knowledge_model_turn(self, key, messages, schemas, binding):
        def invoke():
            from lib.llm_client import ChatClient
            from lib.infrastructure.workflow_stream_journal import StreamJournal
            client = self.client("generation", stage="director")
            with self._client_locks[id(client)]:
                before = dict(getattr(client, "usage", {}))
                self.event("model_started", role="generation")
                journal = StreamJournal(self.path, stage=self.stage, unit=digest(key)[:16], role="generation",
                    run_attempt=self.state.get("attempt", 0), checkpoint=digest(key)) if isinstance(client, ChatClient) else None
                try:
                    self.check_cancel()
                    streaming = {"on_stream": journal} if journal is not None else {}
                    if schemas:
                        return client.tool_turn(deepcopy(messages), schemas,
                            max_tokens=binding.get("max_output_tokens", 32768), **streaming)
                    details = {}
                    text = client.chat(deepcopy(messages), json_mode=True, allow_reasoning_fallback=False,
                        response_details=details, max_tokens=binding.get("max_output_tokens", 32768), **streaming)
                    return {"role": "assistant", "content": text,
                            **({"reasoning_content": details["reasoning_content"]} if details.get("reasoning_content") else {})}
                except BaseException:
                    if journal is not None:
                        journal.interrupted()
                    raise
                finally:
                    after = getattr(client, "usage", {})
                    with self._lock:
                        usage = self.state.setdefault("usage", {}).setdefault("generation", {})
                        for metric in ("calls", "prompt_tokens", "completion_tokens"):
                            usage[metric] = usage.get(metric, 0) + max(0, after.get(metric, 0) - before.get(metric, 0))
                    self.event("model_finished", role="generation")
        return self.checkpoint(key, invoke)

    def commit_knowledge_plan(self, key):
        pending = getattr(self, "_knowledge_pending", {}).pop(digest(key), None)
        if pending is not None:
            self.knowledge_store().remember_turn(*pending)

    @staticmethod
    def _knowledge_task_identity(contract, stage):
        return knowledge_task_identity(contract, stage)

    def claim_knowledge_task(self, unit, stage):
        descriptor = unit.get("knowledge_agent_context")
        if not self.knowledge_agent_enabled() or not descriptor or not unit.get("qa_contract"):
            return None
        contract = unit["qa_contract"]
        return self.knowledge_store().claim(descriptor["knowledge_id"],
            self._knowledge_task_identity(contract, stage),
            run_id=self.state["id"], sample_id=unit["id"], question=contract["question"], qa_type=contract["qa_type"],
            topic=(contract.get("dialogue_design") or {}).get("interaction_goal", ""))

    def record_knowledge_outcome(self, unit, row, stage, *, final=False):
        descriptor = unit.get("knowledge_agent_context")
        contract = unit.get("qa_contract") or row.get("qa_contract")
        source = unit
        for _ in range(8):
            if descriptor or not isinstance(source.get("source_context"), dict):
                break
            source = source["source_context"]
            descriptor = source.get("knowledge_agent_context")
            contract = contract or source.get("qa_contract")
        if not self.knowledge_agent_enabled() or not descriptor or not contract:
            return
        self.knowledge_store().outcome(descriptor["knowledge_id"],
            self._knowledge_task_identity(contract, stage),
            run_id=self.state["id"], sample_id=unit.get("id", row["id"]), stage=stage,
            status=("final_" if final else "draft_") + row["status"], reason=row.get("reason", ""))
