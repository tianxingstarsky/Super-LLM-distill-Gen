"""One provider thinking response owns both the rationale and the answer."""
from copy import deepcopy

from .generation_ports import NativeAnswerPort
from ..domain.dialogue import without_source_reasoning, dialogue_digest as digest
from ..domain.generation import qa_metadata
from ..domain.authored_task_contract import compile_task_contract, project_task_context, task_surface_receipt
from ..domain.cot_review_policy import cot_review_plan, SAMPLED_REVIEW_INSTRUCTION
from ..domain.integrated_reflection import integrated_reflection, reflection_instruction
from ..domain.review_guidance import review_guidance_message, authenticated_review_guidance_positions
from ..domain.dialogue_style_context import (
    authenticated_context_positions, context_message_provenance, context_system_message,
)
from ..domain.native_cot import (
    NATIVE_EXECUTION_STATE, native_execution_state, native_instruction_leakage,
    native_instruction_scope_evidence,
)
from lib.domain.workflow_quality import accepted, text_issue
from lib.domain.training_surface import training_surface_issue
from lib.domain.knowledge_voice import knowledge_voice_options


class GenerateNativeCot:
    def __init__(self, port: NativeAnswerPort, recipe: dict, *, stage: str = "cot"):
        self.port, self.recipe, self.stage = port, recipe, stage

    def execute(self, sample, style, *, instruction=""):
        messages = sample.get("messages", [])
        context_positions = authenticated_context_positions(messages, self.recipe)
        repair_session = (sample.get("native_tool_feedback") or {}).get("session") or {}
        public_note_context = (self.recipe.get("native_feedback_guidance_version") == 7
            and self.recipe.get("native_revision_context_version") == 2
            and sample.get("native_previous_candidate") is not None
            and messages and messages[-1].get("role") == "system"
            and repair_session.get("messages", [])[-1:] == messages[-1:]
            and any(message.get("role") == "user" for message in messages))
        context_note_context = (messages and len(messages) - 1 in context_positions
            and any(message.get("role") == "user" for message in messages))
        if not messages or (messages[-1].get("role") not in {"user", "assistant", "tool"}
                            and not public_note_context
                            and not context_note_context):
            return [self.port.reject(sample, "cot_missing_dialogue")]
        previous = messages[-1] if messages[-1].get("role") == "assistant" else None
        prompt = without_source_reasoning(messages[:-1] if previous else messages)
        provided_previous = sample.get("native_previous_candidate")
        if provided_previous is not None:
            if previous is not None:
                return [self.port.reject(sample, "native_previous_candidate_conflict")]
            previous = deepcopy(provided_previous)
        from ..domain.public_experience import public_experience_text, with_public_experience
        experience = public_experience_text(self.recipe)
        already_public = (self.stage != "cot" and experience and any(
            message.get("role") == "system" and message.get("content") ==
            "<working_principles>\n" + experience + "\n</working_principles>" for message in prompt))
        if not already_public:
            prompt = with_public_experience(prompt, experience)
        if self.recipe.get("dialogue_style_context_version") is not None:
            step = sample.get("dialogue_step")
            if isinstance(step, dict):
                selected = (step.get("dialogue_state") or {}).get("dialogue_contexts", [])
            elif isinstance(sample.get("dialogue_state"), dict):
                selected = sample["dialogue_state"].get("dialogue_contexts", [])
            elif "dialogue_contexts" in sample:
                selected = sample["dialogue_contexts"]
            else:
                selected = (sample.get("qa_contract") or {}).get("dialogue_contexts", [])
            note, _ = context_system_message(self.recipe, selected)
            # Tool and review-guided repairs continue the authenticated original
            # request. They must not claim a new note absent from that session.
            repairing = (self.recipe.get("native_revision_context_version") == 2
                         and (previous is not None or "native_tool_feedback" in sample))
            pending_user = max((i for i, m in enumerate(prompt) if m.get("role") == "user"), default=-1)
            existing = [i for i in authenticated_context_positions(prompt, self.recipe) if i > pending_user]
            if note is not None and not repairing and not existing:
                prompt = [*prompt, deepcopy(note)]
        task = {"prompt": prompt, "source": deepcopy(sample.get("source_context"))}
        if sample.get("answer_context_scope"):
            task["answer_context_scope"] = deepcopy(sample["answer_context_scope"])
        contract = compile_task_contract(task)
        task = project_task_context(task, contract)
        # Authenticate cached source packs through the existing adapter. A
        # passage-only request never reads or carries the private full paper.
        if isinstance(task.get("source"), dict):
            task["source"] = {"text": self.port.source_evidence(task["source"])}
        revision_context_version = self.recipe.get("native_revision_context_version")
        if revision_context_version is not None and (
                type(revision_context_version) is not int or revision_context_version not in {1, 2}):
            raise ValueError("invalid_native_revision_context_version")
        revision_projection = None
        if previous:
            # The rejected private trace remains in the audit record, but is
            # not repeated as system-level input to a fresh native response.
            # Historical recipes retain the original complete request.
            request_previous = ({key: deepcopy(previous[key]) for key in ("role", "content") if key in previous}
                                if revision_context_version == 1 else deepcopy(previous))
            task["previous_candidate"] = request_previous
            if revision_context_version == 1:
                revision_projection = {"version": 1, "source_sha256": digest(previous),
                    "request_sha256": digest(request_previous), "included_fields": list(request_previous),
                    "excluded_fields": sorted(set(previous) - set(request_previous)),
                    "original_preserved": True, "scope": "previous_candidate_only"}
        if sample.get("native_revision_feedback"):
            task["revision_feedback"] = deepcopy(sample["native_revision_feedback"])
        execution_state = native_execution_state(self.recipe, task, contract)
        if execution_state is not None:
            task[NATIVE_EXECUTION_STATE] = execution_state
        instruction_scope_version = self.recipe.get("native_instruction_scope_version")
        if instruction_scope_version is not None and (
                type(instruction_scope_version) is not int or instruction_scope_version != 1):
            raise ValueError("invalid_native_instruction_scope_version")
        config = self.recipe["node_generation"][self.stage]
        guidance = style.get("instruction", "")
        if instruction:
            guidance += "\n" + instruction
        if integrated_reflection(config.get("self_check")):
            guidance += "\n" + reflection_instruction(config["self_check"])
        feedback_receipt = None
        review_guidance = None
        if revision_context_version == 2 and (previous is not None or "native_tool_feedback" in sample):
            repair = sample.get("native_tool_feedback") or {}
            session = repair.get("session")
            if not previous or not isinstance(session, dict) or session.get("stage") != self.stage:
                raise ValueError("native_feedback_original_session_missing_or_mismatched")
            response, feedback_receipt = self.port.continue_native_feedback(
                [sample["id"], "native_feedback_v1"], session, previous, findings=repair.get("findings"))
            response = response or {"reasoning": "", "answer": ""}
            details = deepcopy(feedback_receipt.get("response_details") or {})
            details.setdefault("api_format", "chat")
            if (feedback_receipt.get("feedback_request_origin") == "system_review_guidance"
                    and feedback_receipt.get("status") == "completed"):
                # This is actual learner-visible input, not audit-only feedback.
                # Authenticate before adding it to checks and training exports.
                review_guidance = review_guidance_message(feedback_receipt)
                prompt = [*prompt, deepcopy(review_guidance)]
                task["prompt"] = prompt
        else:
            response = self.port.request([sample["id"], self.stage + "_native_joint_v1", digest(task)],
                "generation", "workflow.cot_native_joint", task, instruction=guidance,
                capture_response_details=True, allow_reasoning_fallback=False, native_request_attempts=1)
            details = getattr(response, "response_details", {})
        # These fields are assembled by the transport, never parsed from LLM
        # JSON. Missing native reasoning cannot be replaced by visible prose.
        reasoning, answer = response.get("reasoning", ""), response.get("answer", "")
        context_positions = authenticated_context_positions(prompt, self.recipe)
        context_provenance = context_message_provenance(prompt, self.recipe)
        public_note_positions = (authenticated_review_guidance_positions({
            "question": prompt, "reasoning": [reasoning], "answer": answer,
            "native_generation": {"review_guidance": feedback_receipt,
                                  **({"dialogue_style_contexts": context_provenance} if context_provenance else {}),
                                  "content_sha256": digest([reasoning, answer])}})
            if review_guidance is not None else ())
        public_note_positions = tuple(sorted(set(public_note_positions) | set(context_positions)))
        issues = []
        if feedback_receipt is not None and feedback_receipt.get("status") != "completed":
            issues.append(feedback_receipt.get("issue") or "native_feedback_incomplete")
        if text_issue(reasoning):
            issues.append("native_reasoning_missing_or_invalid")
        if text_issue(answer):
            issues.append("native_answer_missing_or_invalid")
        surface = training_surface_issue(reasoning, answer, visible_messages=prompt,
            contract=sample.get("qa_contract"), protocol_version=self.recipe.get("training_surface_protocol_version", 2),
            public_system_positions=public_note_positions,
            **knowledge_voice_options(self.recipe))
        if surface:
            issues.append(surface)
        environment_version = execution_state["version"] if execution_state is not None else None
        scope_evidence = []
        if instruction_scope_version == 1:
            scope_evidence = native_instruction_scope_evidence(reasoning, prompt,
                public_system_positions=public_note_positions,
                environment_version=environment_version)
            if execution_state is not None:
                scope_evidence.extend(native_instruction_scope_evidence(answer, prompt,
                    public_system_positions=public_note_positions,
                    environment_version=environment_version, field="answer"))
            leakage = [finding for finding in scope_evidence if finding["disposition"] == "block"]
            issues.extend(dict.fromkeys(finding["issue"] for finding in leakage))
        else:
            leakage = native_instruction_leakage(reasoning, prompt, environment_version=environment_version,
                public_system_positions=public_note_positions)
            if execution_state is not None:
                leakage.extend(native_instruction_leakage(answer, prompt,
                    environment_version=environment_version, field="answer",
                    public_system_positions=public_note_positions))
            if leakage:
                issues.append("native_generation_instruction_leakage")
        delivery = task_surface_receipt({"prompt": prompt, "source": sample.get("source_context")},
            {"reasoning": reasoning, "answer": answer}, contract)
        if not delivery["observable_passed"]:
            issues.append("cot_task_delivery_mismatch")
        plan = cot_review_plan(config, sample) or {
            "version": 1, "selected": False, "semantic_status": "not_assessed",
            "coverage": "local_checks_only", "selection": "no_independent_review_requested"}
        if (self.stage == "multiturn" and type(self.recipe.get("multiturn_advisory_review_version")) is int
                and self.recipe["multiturn_advisory_review_version"] == 1):
            plan = {"version": 1, "selected": False, "semantic_status": "deferred",
                "model_called": False, "review_stage": "review", "coverage": "local_checks_only",
                "selection": "single_pass_multiturn_advisory",
                **({"requested_policy": deepcopy(config["review_policy"])} if "review_policy" in config else {})}
        if (self.stage == "sft" and self.recipe.get("native_sft_review_evolution_version") == 1):
            plan = {"version": 1, "selected": False, "semantic_status": "deferred",
                "model_called": False, "review_stage": "review", "coverage": "local_checks_only",
                "selection": "single_review_with_optional_optimization",
                **({"requested_policy": deepcopy(config["review_policy"])} if "review_policy" in config else {})}
        if feedback_receipt is not None:
            plan = {"version": 1, "selected": False, "semantic_status": "not_assessed",
                "coverage": "local_checks_only", "selection": (
                    "explicit_review_guidance_regeneration_no_new_review" if review_guidance is not None
                    else "explicit_tool_feedback_repair_no_new_review")}
        row = {"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
            "question": prompt, "reasoning": [reasoning], "answer": answer,
            "source_context": deepcopy(sample.get("source_context")), "quotes": sample.get("quotes", []),
            "evidence_level": sample.get("evidence_level", "unassessed"),
            "generation_style": deepcopy(style), "reasoning_origin": "provider_reasoning",
            "reasoning_api_format": details.get("api_format"), "reasoning_call": details.get("checkpoint"),
            "task_delivery": delivery, "cot_review_policy": plan,
            "native_generation": {"version": 1, "channels": ["reasoning_content", "content"],
                "content_sha256": digest([reasoning, answer]), "raw_reasoning_preserved": True,
                "previous_candidate": deepcopy(previous), "issues": issues, "leakage_evidence": leakage,
                **({"dialogue_style_contexts": context_provenance} if context_provenance else {}),
                **({"author_session": deepcopy(details["author_session"])} if details.get("author_session") else {}),
                **({("review_guidance" if feedback_receipt.get("feedback_request_origin") ==
                       "system_review_guidance" else "tool_feedback"): deepcopy(feedback_receipt),
                    **{name: deepcopy(feedback_receipt[name]) for name in
                        ("response_details", "continuation_session", "revised_assistant") if name in feedback_receipt},
                    **({"author_session": deepcopy(feedback_receipt["continuation_session"])}
                       if feedback_receipt.get("continuation_session") else {})}
                   if feedback_receipt is not None else {}),
                **({"revision_context_projection": revision_projection} if revision_projection is not None else {}),
                **({"instruction_scope_version": instruction_scope_version,
                    "instruction_scope_evidence": scope_evidence} if instruction_scope_version is not None else {}),
                **({"execution_state": deepcopy(execution_state)} if execution_state is not None else {}),
                "history_status": "unchanged_not_assessed" if any(m.get("role") == "assistant" for m in prompt) else "no_assistant_history"},
            **qa_metadata(sample)}
        from ..domain.generation_control import feedback_control_scope, generation_controls_metadata
        scope = feedback_control_scope(feedback_receipt) if feedback_receipt is not None else None
        if scope is not None and scope["status"] != "complete":
            # Per-call controls remain in the receipt, but joined reasoning
            # with mixed or unknown sources cannot train under one label.
            row["generation_control_scope"] = scope
        elif isinstance(details.get("generation_control"), dict):
            row["generation_control"] = deepcopy(details["generation_control"])
            row.update(generation_controls_metadata([(len(prompt), row)]))
        if feedback_receipt is not None:
            row["reasoning_origin"] = ("provider_reasoning_review_guided_regeneration" if
                review_guidance is not None else "provider_reasoning_tool_continuation")
            row.update(self.port.reject(sample, "native_feedback_rewrite_not_assessed"))
        if issues:
            row.update(self.port.reject(sample, issues[0]))
            plan["semantic_status"] = "not_assessed"
        elif plan["selected"]:
            check = self.port.assess([sample["id"], "native_cot_review_v1", digest([task, reasoning, answer])],
                {k: v for k, v in task.items() if k not in {"previous_candidate", "revision_feedback", NATIVE_EXECUTION_STATE}},
                {"reasoning": reasoning, "answer": answer}, prompt_id="workflow.rationale_check",
                allow_reasoning_fallback=False, instruction=SAMPLED_REVIEW_INSTRUCTION)
            row["judge"] = check
            plan["semantic_status"] = "model_assessed" if accepted(check) else "not_accepted"
            if not accepted(check):
                row.update(self.port.reject(sample, "cot_native_review_rejected"))
        return [row]
