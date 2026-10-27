"""Frozen prompt projection, model binding, stream journal and checkpoint adapter."""
from __future__ import annotations
import re
import threading
from lib.domain.document_parser import validate_document_parser
from lib.domain.workflow_quality import text_issue
from ..domain.reading_session import READING_PROMPTS, reading_request_messages


class CheckpointedModelRequest:
    def __init__(self, runtime, hooks):
        self.runtime, self.hooks = runtime, hooks
        self.journal = None

    @property
    def recipe(self):
        return self.runtime.recipe

    @property
    def stage(self):
        return self.runtime.stage

    def checkpoint(self, key, action):
        return self.runtime.checkpoint(key, action)

    def finish_stream(self, *, interrupted):
        if self.journal is not None:
            try:
                self.journal.interrupted() if interrupted else self.journal.completed()
            except (OSError, ValueError):
                pass  # Keep the original failure or already committed checkpoint.

    def captured_result(self, value):
        from lib.model_response_details import ModelJSONResult
        return ModelJSONResult(value["data"], value["response_details"]) if isinstance(value["data"], dict) else value["data"]

    def client(self, role, *, stage=None):
        runtime = self.runtime
        digest = self.hooks["digest"]
        ChatClient = self.hooks["ChatClient"]
        load_backend = self.hooks["load_backend"]
        stage = stage or runtime.stage
        if role == "vision":
            from lib.infrastructure.document_vision import require_vision_model
            parser = validate_document_parser(runtime.recipe.get("document_parser"))
            require_vision_model(runtime.root, parser.get("assistance",parser)["binding"])
        field = {"judge": "judge", "jev": "jev", "question": "questioner"}.get(role, "generator")
        client = getattr(runtime, field)
        if client is None:
            cache = getattr(runtime._local, "clients", None)
            if cache is None:
                cache = runtime._local.clients = {}
            cache_key = (stage, role)
            client = cache.get(cache_key)
            if client is None:
                prefix = f"{role}_" if role in {"judge", "jev"} else ""
                binding = runtime.recipe.get("node_models", {}).get(stage, {}).get(role, {})
                pins = runtime.recipe.get("endpoint_pins")
                if pins is not None and not isinstance(pins, dict):
                    raise ValueError("workflow_endpoint_pin_invalid")
                stage_pins = pins.get(stage, {}) if pins is not None else {}
                if not isinstance(stage_pins, dict):
                    raise ValueError("workflow_endpoint_pin_invalid")
                pin = stage_pins.get(role)
                if pins is not None and binding and (not isinstance(pin, dict) or
                        pin.get("backend") != binding.get("backend") or pin.get("model") != binding.get("model")):
                    raise ValueError("workflow_endpoint_pin_invalid")
                client, _ = load_backend(runtime.root, backend=binding.get("backend") or runtime.recipe.get(prefix + "backend"),
                                         model=binding.get("model") or runtime.recipe.get(prefix + "model"), role=role,
                                         allow_global_endpoint_override=not bool(binding),
                                         context_window_tokens=binding.get("context_window_tokens"),
                                         **({"reasoning": binding["reasoning"]} if "reasoning" in binding else {}),
                                         **({"additional_budget": runtime._task_budget} if runtime._task_budget is not None else {}),
                                         **({"expected_endpoint_pin": pin} if pin is not None else {}))
                cache[cache_key] = client
        if runtime._task_budget is not None and isinstance(client, ChatClient):
            client.additional_budget = runtime._task_budget
        if isinstance(client, ChatClient):
            client.cancel_check = runtime.check_cancel
        endpoint_identity = str(getattr(getattr(client, "client", None), "base_url", "injected"))
        api_format = str(getattr(client, "api_format", "chat"))
        if api_format != "chat":
            endpoint_identity += "|" + api_format
        identity = {"model": getattr(client, "model", type(client).__name__),
                    "endpoint_hash": digest(endpoint_identity)}
        model_key = f"{stage}.{role}" if runtime.recipe.get("node_models", {}).get(stage, {}).get(role) else role
        with runtime._lock:
            pinned = runtime.state.setdefault("models", {}).get(model_key)
            if pinned and pinned != identity:
                raise ValueError("model_configuration_changed_create_new_run")
            runtime.state["models"][model_key] = identity
            runtime._client_locks.setdefault(id(client), threading.Lock())
            runtime.save(force=False)
        return client

    def historical_prompt(self, prompt_id):
        """Resolve a pre-node-template recipe by its saved asset version.

        Older catalogs may omit later prompt ids. Their historical fallback
        is version 1.0.0, never the newest registered default. A saved pin must
        still match the retained version's exact template hash.
        """
        runtime = self.runtime
        digest, get = self.hooks["digest"], self.hooks["get"]
        pins = runtime.recipe.get("prompts", {})
        if not isinstance(pins, dict):
            raise ValueError("prompts_changed_create_new_run")
        pinned = pins.get(prompt_id)
        if pinned is not None and (not isinstance(pinned, dict) or set(pinned) != {"version", "sha256"}
                or not isinstance(pinned.get("version"), str)):
            raise ValueError("prompts_changed_create_new_run")
        version = pinned["version"] if pinned is not None else "1.0.0"
        try:
            spec = get(prompt_id, version=version)
        except (KeyError, TypeError) as error:
            raise ValueError("prompts_changed_create_new_run") from error
        if pinned is not None and digest(spec.template) != pinned["sha256"]:
            raise ValueError("prompts_changed_create_new_run")
        return spec

    def prompt_text(self, prompt_id, *, stage=None):
        """Resolve by node as well as step, without formatting user-owned text."""
        runtime = self.runtime
        render = self.hooks["render"]
        if runtime.recipe.get("version", 0) >= 9:
            try:
                text = runtime.recipe["node_prompt_templates"][stage or runtime.stage][prompt_id]
            except (KeyError, TypeError) as exc:
                raise ValueError("invalid_node_prompt_snapshot") from exc
            if not isinstance(text, str) or not text.strip():
                raise ValueError("invalid_node_prompt_snapshot")
            return text
        return render(runtime.historical_prompt(prompt_id))

    def messages(self, client, request):
        runtime = self.runtime
        data, instruction = request.data, request.instruction
        if request.prompt_id in READING_PROMPTS:
            return reading_request_messages(request.prompt_id, runtime.prompt_text(request.prompt_id, stage=request.prompt_stage), data, instruction)
        if request.prompt_id == "workflow.cot_native_joint":
            from .native_cot_messages import native_cot_messages
            return native_cot_messages(runtime.prompt_text(request.prompt_id, stage=request.prompt_stage), data, instruction)
        from ..domain.authored_task_contract import (
            task_contract_enabled, compile_task_contract, project_task_context, task_contract_instruction,
        )
        task_contract = None
        if (task_contract_enabled(self.recipe) and request.prompt_id in {
                "workflow.cot_generate", "workflow.cot_rationale", "workflow.cot_native_answer"}):
            task_contract = compile_task_contract(data)
            data = project_task_context(data, task_contract)
        if runtime.recipe.get("claim_review_transport_version") == 1:
            from lib.contexts.review.contracts import project_review_window_hints
            data = project_review_window_hints(data)
        image, image_values = request.image, request.image_values
        prompt_id, prompt_stage = request.prompt_id, request.prompt_stage
        model_stage, role = request.model_stage, request.role
        canonical, render = self.hooks["canonical"], self.hooks["render"]
        base_system = (runtime.recipe["node_prompt_system"] if runtime.recipe.get("version", 0) >= 9
                       else render(runtime.historical_prompt("workflow.system")))
        system = base_system + "\n" + (runtime.prompt_text(prompt_id, stage=prompt_stage)
            if prompt_stage is not None else runtime.prompt_text(prompt_id))
        stable_instruction, dynamic_review_instruction = instruction, None
        if (isinstance(data, dict) and data.get("claim_review_protocol_version") == 4
                and (runtime.recipe.get("claim_review_wire_schema_version") == 1
                     or runtime.recipe.get("claim_review_focus_version") == 1
                     or runtime.recipe.get("claim_review_condition_contract_version") == 1)
                and isinstance(instruction, str)):
            from lib.contexts.review.contracts import CLAIM_REVIEW_DYNAMIC_WIRE_HEADER
            if CLAIM_REVIEW_DYNAMIC_WIRE_HEADER in instruction:
                stable_instruction, dynamic_review_instruction = instruction.split(CLAIM_REVIEW_DYNAMIC_WIRE_HEADER, 1)
        from lib.infrastructure.workflow_node_messages import split_node_rules
        rule_system, request_data = split_node_rules(
            data, instruction=stable_instruction, qa_director=runtime.recipe.get("qa_director"),
            knowledge_application_voice_version=runtime.recipe.get("knowledge_application_voice_version"))
        from lib.infrastructure.document_reading_context import project_reading_context
        request_data = project_reading_context(request_data)
        if dynamic_review_instruction is not None:
            # Scope hashes and current ids belong after the shared
            # system/source prefix, not before its reusable memory.
            request_data["node_task_instructions"] = dynamic_review_instruction
        memory_version = runtime.recipe.get("source_memory_protocol_version")
        sealed_review_request = (memory_version in {1, 2}
                                 and (memory_version == 2 or bool(runtime.recipe.get("repair_inputs")))
                                 and isinstance(data, dict)
                                 and isinstance(data.get("source_evidence"), dict)
                                 and not data.get("learner_context_only"))
        source_pack_request = ((runtime.recipe.get("source_context_pack_version") == 1 or sealed_review_request)
                               and image is None and isinstance(data, dict)
                               and not data.get("learner_context_only"))
        memory_context = None
        if source_pack_request:
            from lib.infrastructure.model_context_prefix import serialize_context_request, split_context_request, NODE_RULE_HEADER
            if memory_version in {1, 2}:
                if sealed_review_request:
                    from lib.infrastructure.source_memory_messages import sealed_review_memory_context
                    memory_context = sealed_review_memory_context(request_data, protocol_version=memory_version)
                else:
                    memory_context = split_context_request(runtime.path, request_data, protocol_version=memory_version)
                request_text = canonical(memory_context["task_payload"])
                has_pack = bool(memory_context["teacher_context_pack"])
            else:
                request_text = serialize_context_request(runtime.path, request_data)
                has_pack = request_text.startswith('{"teacher_context_pack":')
            if has_pack:
                pinned_prompt = (runtime.prompt_text(prompt_id, stage=prompt_stage)
                                 if prompt_stage is not None else runtime.prompt_text(prompt_id))
                system = base_system + NODE_RULE_HEADER + "\n" + pinned_prompt
        else:
            request_text = canonical(request_data)
        # Procedural experience belongs to the fixed system prefix,
        # never to source facts, a user turn, or a generated answer.
        from lib.infrastructure.workflow_agent_experience import experience_system
        system += experience_system(runtime.recipe, prompt_id) + rule_system
        request_messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": request_text if image is None else [
                {"type": "text", "text": request_text},
                *[{"type": "image_url", "image_url": {"url": value}} for value in image_values]]}]
        if source_pack_request and memory_version in {1, 2}:
            from lib.infrastructure.source_memory_messages import source_memory_messages
            transport = ("system" if memory_version == 2 or prompt_id == "workflow.cot_native_answer" else
                         getattr(client, "source_memory_transport", "tool"))
            if memory_context.get("claim_review_protocol_version") in {1, 2, 3, 4}:
                transport = "system"
            request_messages = source_memory_messages(system, memory_context, transport=transport)
            if memory_context["teacher_context_pack"] or memory_context.get("claim_review_protocol_version") in {1, 2, 3, 4}:
                with runtime._lock:
                    runtime.state.setdefault("source_memory_transports", {})[
                        f"{model_stage or runtime.stage}.{role}"] = transport
        if runtime.recipe.get("qa_director", {}).get("question_separation"):
            from lib.infrastructure.dialogue_role_messages import answer_role_messages, question_role_messages
            transport = ("system" if memory_version == 2 or prompt_id == "workflow.cot_native_answer" else
                         getattr(client, "source_memory_transport", "tool"))
            if prompt_id == "workflow.question_user":
                from lib.domain.learner_context import prompt_at_least
                first_turn_version = (2 if runtime.recipe.get("authored_role_projection_version", 1) >= 3
                    and prompt_at_least(runtime.recipe, prompt_id, (1, 2, 0)) else 1)
                request_messages = question_role_messages(system, request_data, transport=transport,
                    first_turn_policy_version=first_turn_version,
                    question_brief_protocol_version=runtime.recipe.get("question_brief_protocol_version", 1),
                    **({"expression_guide_version": 2} if runtime.recipe.get("qa_expression_version") == 2 else {}))
            elif prompt_id in {"workflow.sft_answer", "workflow.multiturn_assistant"}:
                request_messages = answer_role_messages(system, memory_context, request_data, transport=transport,
                    source_memory_protocol_version=memory_version if memory_version in {1, 2} else None,
                    answer_dispatch_protocol_version=runtime.recipe.get("answer_dispatch_protocol_version", 1))
        if (runtime.recipe.get("training_surface_protocol_version") in {1, 2}
                and prompt_id == "workflow.sft_directed"
                and isinstance(request_data, dict) and request_data.get("learner_messages")):
            from lib.infrastructure.dialogue_role_messages import answer_role_messages
            request_messages = answer_role_messages(system, memory_context, request_data,
                transport="system" if memory_version == 2 else getattr(client, "source_memory_transport", "tool"),
                include_question=True, source_memory_protocol_version=memory_version if memory_version in {1, 2} else None,
                answer_dispatch_protocol_version=runtime.recipe.get("answer_dispatch_protocol_version", 1))
        if (runtime.recipe.get("training_surface_protocol_version") in {1, 2}
                and prompt_id in {"workflow.cot_generate", "workflow.cot_rationale", "workflow.cot_native_answer", "workflow.trim"}):
            from lib.infrastructure.dialogue_role_messages import authored_role_messages
            # DeepSeek beta disallows function-call history with
            # prefix. Use its explicit private-memory transport;
            # never erase or reclassify real learner tool turns.
            transport = ("system" if memory_version == 2 or prompt_id == "workflow.cot_native_answer" else
                         getattr(client, "source_memory_transport", "tool"))
            authored_messages = authored_role_messages(system, memory_context, request_data, transport=transport,
                role_projection_version=runtime.recipe.get("authored_role_projection_version", 1),
                phase={"workflow.cot_rationale": "rationale", "workflow.cot_native_answer": "native_answer",
                       "workflow.trim": "trim"}.get(prompt_id, "combined"),
                source_memory_protocol_version=memory_version if memory_version in {1, 2} else None)
            if authored_messages is not None:
                request_messages = authored_messages
                if (type(runtime.recipe.get("authored_stage_contract_version")) is int
                        and runtime.recipe["authored_stage_contract_version"] == 1
                        and prompt_id in {"workflow.cot_generate", "workflow.cot_rationale", "workflow.cot_native_answer"}):
                    from ..domain.authored_stage_contract import authored_stage_contract
                    # Preserve the reusable source prefix and every actual
                    # learner turn. This is a production system instruction,
                    # not a new user request or a fabricated assistant draft.
                    from ..domain.authored_submission import strict_submission_enabled, submission_instruction
                    from ..domain.cot_style_transfer import style_edit_base, style_edit_instruction
                    edit_base = style_edit_base(runtime.recipe, request.data) if prompt_id == "workflow.cot_rationale" else None
                    stage_contract = (submission_instruction(answer_required=prompt_id == "workflow.cot_generate",
                        local_edit=runtime.recipe.get("authored_local_edit_version") == 1)
                        if strict_submission_enabled(runtime.recipe) and prompt_id != "workflow.cot_native_answer"
                        else authored_stage_contract(prompt_id))
                    if edit_base is not None:
                        stage_contract = style_edit_instruction(edit_base)
                    request_messages = [*request_messages, {"role": "system", "content": stage_contract}]
            if prompt_id == "workflow.cot_native_answer":
                with runtime._lock:
                    runtime.state.setdefault("source_memory_transports", {})[
                        f"{model_stage or runtime.stage}.{role}.native_answer"] = transport
        from ..domain.integrated_reflection import trailing_reflection_enabled, reflection_for_prompt
        if trailing_reflection_enabled(runtime.recipe) and prompt_id in {
                "workflow.cot_generate", "workflow.cot_rationale", "workflow.cot_native_answer"}:
            guidance = reflection_for_prompt(runtime.recipe["node_generation"]["cot"]["self_check"], prompt_id)
            if guidance:
                request_messages = [*request_messages, {"role": "system", "content": guidance}]
        if task_contract is not None:
            # Both stages get the actual delivery constraints. A bilingual
            # rationale does not authorize a different answer language.
            if runtime.recipe.get("authored_task_contract_version") in (2, 3):
                # Delivery constraints belong to the primary system contract;
                # they must not depend on being inferred from a frozen prefix.
                request_messages[0] = {**request_messages[0], "content":
                    task_contract_instruction(task_contract, version=2, phase=prompt_id)
                    + "\n" + request_messages[0]["content"]}
            else:
                request_messages = [*request_messages, {"role": "system",
                    "content": task_contract_instruction(task_contract)}]
            if (runtime.recipe.get("authored_task_contract_version") == 3
                    and prompt_id in {"workflow.cot_native_answer", "workflow.cot_generate"}):
                from ..domain.authored_task_contract import answer_language_instruction
                delivery_instruction = answer_language_instruction(task_contract)
                if delivery_instruction:
                    request_messages.append({"role": "system", "content": delivery_instruction})
        from ..domain.cot_style_transfer import style_edit_base, style_edit_instruction
        edit_base = style_edit_base(runtime.recipe, request.data) if prompt_id == "workflow.cot_rationale" else None
        if edit_base is not None:
            edit_instruction = style_edit_instruction(edit_base)
            if not any(m.get("role") == "system" and m.get("content") == edit_instruction for m in request_messages):
                request_messages.append({"role": "system", "content": edit_instruction})
        return request_messages

    def invoke(self, request):
        runtime = self.runtime
        key, role, prompt_id = request.key, request.role, request.prompt_id
        model_stage, prompt_stage = request.model_stage, request.prompt_stage
        capture, allow_reasoning_fallback = request.capture, request.allow_reasoning_fallback
        reasoning_prefix, native_request_attempts = request.reasoning_prefix, request.native_request_attempts
        ChatClient, StreamJournal = self.hooks["ChatClient"], self.hooks["StreamJournal"]
        digest, chat_json = self.hooks["digest"], self.hooks["chat_json"]
        runtime.check_cancel()
        client = runtime.client(role, stage=model_stage) if model_stage is not None else runtime.client(role)
        with runtime._client_locks[id(client)]:
            runtime.check_cancel()
            before = dict(getattr(client, "usage", {}))
            runtime.event("model_started", role=role)
            try:
                binding = runtime.recipe.get("node_models", {}).get(model_stage or runtime.stage, {}).get(role, {})
                streaming = {}
                if isinstance(client, ChatClient):
                    first = key[0] if isinstance(key, (list, tuple)) and key else key
                    unit = first if isinstance(first, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", first) else digest(key)[:16]
                    journal = self.journal = StreamJournal(runtime.path, stage=runtime.stage, unit=unit, role=role,
                                            run_attempt=runtime.state.get("attempt", 0),
                                            checkpoint=digest(["call", key]))
                    def received(event):
                        runtime.check_cancel()
                        if event.get("type") == "queued":
                            runtime.event("model_queued", role=role,
                                       **{name: event[name] for name in
                                          ("position", "active", "queued", "cooldown_seconds") if name in event})
                        journal(event)
                    streaming["on_stream"] = received
                request_messages = self.messages(client, request)
                visible_only = (capture or not allow_reasoning_fallback or prompt_id in {
                    "workflow.sft_styled", "workflow.cot_generate", "workflow.style_check",
                    "workflow.cot_rationale", "workflow.cot_native_answer", "workflow.cot_prefix_check",
                    "workflow.trim", "workflow.trim_check", "workflow.trim_rules_check"})
                response_details = {}
                from ..domain.authored_submission import strict_submission_enabled, authored_submission_tool, assemble_authored_submission
                if strict_submission_enabled(runtime.recipe) and prompt_id in {"workflow.cot_generate", "workflow.cot_rationale"}:
                    answer_required = prompt_id == "workflow.cot_generate"
                    from ..domain.cot_style_transfer import style_edit_base, style_edit_tool
                    edit_base = style_edit_base(runtime.recipe, request.data) if not answer_required else None
                    submission_tools = [authored_submission_tool(answer_required=answer_required)]
                    if edit_base is not None:
                        submission_tools = [style_edit_tool()]
                    elif runtime.recipe.get("authored_local_edit_version") == 1:
                        from ..domain.authored_edits import authored_edit_tool
                        submission_tools.append(authored_edit_tool(answer_required=answer_required))
                    def submit():
                        from .authored_edit_adapter import author_session
                        details = {}
                        session = author_session(client, binding, request_messages, submission_tools,
                            stage=model_stage or runtime.stage, role=role, max_tokens=binding.get("max_output_tokens"))
                        assistant = client.tool_turn(request_messages,
                            submission_tools, strict=True,
                            max_tokens=binding.get("max_output_tokens"), temperature=0.2,
                            retries=request.structured_response_attempts or 1,
                            response_details=details, **streaming)
                        return {"assistant": assistant, "response_details": details, "author_session": session,
                                **({"edit_base": edit_base} if edit_base is not None else {})}
                    # Persist the provider submission before local projection.
                    # An invalid field must not repurchase an entire rationale on resume.
                    submission = runtime.checkpoint(["authored_submission_v1", key], submit)
                    response_details.update(submission["response_details"])
                    if edit_base is not None:
                        from ..domain.authored_edits import apply_authored_edits
                        value, patch = apply_authored_edits(edit_base, submission["assistant"], answer_required=False)
                        response_details["style_transfer_patch"] = patch
                    else:
                        value = assemble_authored_submission(submission["assistant"], answer_required=answer_required)
                elif prompt_id == "workflow.cot_native_joint" or prompt_id in READING_PROMPTS:
                    if getattr(client, "reasoning", {}).get("mode") == "disabled":
                        raise ValueError("native_cot_thinking_disabled")
                    visible = client.chat(request_messages, max_tokens=binding.get("max_output_tokens"),
                        allow_reasoning_fallback=False, response_details=response_details,
                        retries=native_request_attempts or 1, **streaming)
                    value = {"answer": visible, "reasoning": response_details.get("reasoning_content", "")}
                elif prompt_id == "workflow.cot_native_answer":
                    # Prefix completion returns the real visible answer,
                    # not a model-owned JSON envelope. No text is invented
                    # or decoded from native reasoning to fill an answer.
                    visible = client.chat(request_messages, max_tokens=binding.get("max_output_tokens"),
                        reasoning_prefix=reasoning_prefix, allow_reasoning_fallback=False,
                        response_details=response_details, **streaming,
                        **({"retries": native_request_attempts} if native_request_attempts is not None else {}))
                    # Early beta recipes explicitly requested this exact
                    # one-field JSON schema. Decode their actual field,
                    # rather than double-wrap JSON as user-visible text.
                    # Current plain-text recipes retain the raw answer.
                    if '仅返回JSON：{"answer"' in runtime.prompt_text(prompt_id, stage=prompt_stage):
                        from lib.contexts.models.contracts import parse_json_robust
                        try:
                            decoded = parse_json_robust(visible)
                        except ValueError:
                            decoded = None
                        if isinstance(decoded, dict) and set(decoded) == {"answer"} and isinstance(decoded["answer"], str):
                            response_details["visible_response_envelope"] = visible
                            visible = decoded["answer"]
                    if text_issue(visible):
                        raise ValueError("native_prefill_invalid_answer")
                    value = {"answer": visible}
                else:
                    value = chat_json(client, request_messages,
                        max_tokens=binding.get("max_output_tokens"), **streaming,
                        **({"retries": request.structured_response_attempts}
                           if request.structured_response_attempts is not None else {}),
                        **({"response_details": response_details} if capture and isinstance(client, ChatClient) else {}),
                        **({"reasoning_prefix": reasoning_prefix} if reasoning_prefix is not None else {}),
                        **({"allow_reasoning_fallback": False} if visible_only else {}))
                if capture:
                    # The envelope is created by the worker, outside the model's
                    # JSON. It is committed atomically with this exact answer.
                    response_details["checkpoint"] = {"stage": runtime.stage,
                        "sha256": digest(["call", key])}
                    return {"model_json_response_version": 1, "data": value,
                            "response_details": response_details}
                return value
            finally:
                after = getattr(client, "usage", {})
                with runtime._lock:
                    usage = runtime.state["usage"].setdefault(role, {})
                    for metric in ("calls", "prompt_tokens", "completion_tokens"):
                        usage[metric] = usage.get(metric, 0) + max(0, after.get(metric, 0) - before.get(metric, 0))
                    runtime.event("model_finished", role=role)
