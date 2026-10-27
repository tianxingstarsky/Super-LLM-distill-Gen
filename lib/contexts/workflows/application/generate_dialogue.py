"""Generate separate participant turns, assess each turn and then the complete dialogue.

The application owns retries and stop semantics. The port owns source reads,
request checkpoints, scheduling and provider protocols. Imported conversations
are assessed literally and never rewritten by this use case.
"""
from __future__ import annotations
from copy import deepcopy
from .dialogue_ports import DialoguePort
from ..domain.dialogue import (DialogueProgress, DialoguePlanError, controlled_training_messages,
    without_source_reasoning, document_evidence_level, dialogue_digest as digest)
from lib.domain.multiturn import completed_turn_ends
from lib.domain.workflow_quality import accepted, conversation_issue, same_answer, text_issue, tool_error_flag
from lib.domain.human_augmentation import human_generation_instruction


class GenerateMultiturnDialogue:
    def __init__(self, port: DialoguePort, recipe: dict):
        self._port = port
        self.recipe = recipe

    def execute(self, unit):
        from ..domain.reading_session import reading_session_enabled
        if reading_session_enabled(self.recipe):
            return self._reading_session(unit)
        # New snapshots use this node's authored style, falling back to SFT.
        # Imported dialogue and frozen older requests remain literal.
        style_node = "multiturn" if "multiturn" in self.recipe.get("node_generation", {}) else "sft"
        style = (self._port.generation_style(style_node, unit["id"])
                 if self.recipe.get("multiturn_generation_style_version") == 1
                 and unit.get("kind") != "conversation" else None)
        rows = self.generate(unit, style=style)
        if style is not None:
            for row in rows:
                row["generation_style"] = deepcopy(style)
                if any(message.get("role") == "assistant" and message.get("reasoning_content")
                       for message in row.get("messages", [])):
                    row["reasoning_origin"] = "prompt_styled_generation"
        return rows

    def _reading_session(self, unit):
        """One private scenario, then isolated user/answer histories with resume keys."""
        from ..domain.reading_session import (END_DIALOGUE, reading_source, reading_context,
            simulator_messages, utterance_advisories)
        from ..domain.native_cot import native_instruction_leakage
        from ..domain.authored_task_contract import compile_task_contract, task_surface_receipt
        if unit.get("kind") != "document":
            return [self._port.reject(unit, "reading_session_requires_document")]
        style = self._port.generation_style("multiturn", unit["id"])
        prepared = self._port.prepare_source(unit, style)
        try:
            source = reading_source(unit, prepared)
        except ValueError as error:
            return [self._port.reject(unit, str(error))]
        from ..domain.public_experience import public_experience_text
        public_rules = self._port.prompt_text("workflow.reading_answer")
        if experience := public_experience_text(self.recipe):
            public_rules += "\n\n" + experience
        messages = reading_context(public_rules, source,
                                   (style or {}).get("instruction", ""))
        scene_task = {"material": source, "brief": self.recipe.get("brief", "")}
        if experience := public_experience_text(self.recipe, "director"):
            scene_task["experience"] = experience
        scene_response = self._port.request([unit["id"], "reading_scene_v1", digest(scene_task)],
            "generation", "workflow.reading_scene", scene_task,
            capture_response_details=True, allow_reasoning_fallback=False, native_request_attempts=1)
        scene = scene_response.get("answer", "")
        if text_issue(scene):
            return [self._port.reject(unit, "reading_session_invalid_scene")]
        turns, actor_receipts, issues = [], [], []
        end_reason = "turn_limit"
        user_template = self._port.prompt_text("workflow.reading_user")
        if experience := public_experience_text(self.recipe, "question"):
            user_template += "\n\n" + experience
        for turn in range(self.recipe.get("conversation_turns", 3)):
            user_task = {"messages": simulator_messages(user_template, scene, source, messages,
                protocol_version=self.recipe.get("reading_actor_protocol_version", 1))}
            user_response = self._port.request([unit["id"], "reading_user_v1", turn, digest(user_task)],
                "generation", "workflow.reading_user", user_task,
                capture_response_details=True, allow_reasoning_fallback=False, native_request_attempts=1)
            utterance = user_response.get("answer", "")
            actor_receipts.append({"turn": turn + 1, "response": deepcopy(dict(user_response)),
                "call": getattr(user_response, "response_details", {}).get("checkpoint")})
            if utterance.strip() == END_DIALOGUE:
                end_reason = "user_goal_complete"
                if not turns:
                    issues.append("reading_session_empty_dialogue")
                break
            if text_issue(utterance):
                issues.append("reading_session_invalid_user_message")
                break
            messages.append({"role": "user", "content": utterance})
            # The answering model receives no scenario, rubric, desired answer,
            # private actor reasoning or retrospective review feedback.
            answer_task = {"messages": without_source_reasoning(messages)}
            response = self._port.request([unit["id"], "reading_answer_v1", turn, digest(answer_task)],
                "generation", "workflow.reading_answer", answer_task,
                capture_response_details=True, allow_reasoning_fallback=False, native_request_attempts=1)
            answer, reasoning = response.get("answer", ""), response.get("reasoning", "")
            local_issues = []
            if text_issue(answer) or text_issue(reasoning):
                local_issues.append("reading_session_missing_native_output")
            leakage = native_instruction_leakage(reasoning, messages, public_task_rules=True)
            if leakage:
                local_issues.append("native_generation_instruction_leakage")
            task = {"prompt": messages}
            delivery = task_surface_receipt(task, {"answer": answer, "reasoning": reasoning}, compile_task_contract(task))
            if not delivery["observable_passed"]:
                local_issues.append("cot_task_delivery_mismatch")
            messages.append({"role": "assistant", "content": answer, "reasoning_content": reasoning})
            turns.append({"turn": turn + 1, "issues": local_issues, "leakage_evidence": leakage,
                "utterance_advisories": utterance_advisories(utterance), "task_delivery": delivery,
                "semantic_status": "not_assessed", "reasoning_origin": "provider_reasoning",
                "call": getattr(response, "response_details", {}).get("checkpoint"),
                "request_messages_sha256": digest(answer_task["messages"])})
            if local_issues:
                issues.extend(local_issues)
                end_reason = "local_defect"
                break
        row = {"id": unit["id"], "source_id": unit["source_id"], "source_name": unit.get("source_name"),
            "status": "eligible", "messages": messages, "tools": [], "source_context": deepcopy(unit),
            "synthetic": True, "turn_count": len(turns), "reasoning_origin": "provider_reasoning",
            "evidence_level": "source_text_not_independently_verified", "fact_verification": "not_independently_verified",
            "generation_style": style, "generation_check": "public_context_and_native_channels",
            "reading_session": {"version": 1, "source_sha256": digest(source), "public_context_sha256": digest(messages[0]),
                "semantic_status": "not_assessed", "end_reason": end_reason, "turns": turns,
                "scenario": scene, "user_simulation": actor_receipts,
                "visibility": "rules_and_material_exported_scene_private", "issues": issues}}
        from lib.domain.training_surface import training_record_surface_issue
        if surface_issue := training_record_surface_issue("multiturn", row, protocol_version=2):
            issues.append(surface_issue)
        if issues:
            row.update(self._port.reject(unit, issues[0]))
        return [row]

    def generate(self, unit, *, style=None):
        """Build or conservatively assess a complete multi-turn conversation.

        Imported traces are never rewritten. Generated turns use separate
        user/assistant calls, turn-level review, and a whole-dialogue check.
        All model checks are assessments, never independent fact verification.
        """
        if unit.get("verification_status") == "synthetic_unverified":
            return [self._port.reject(unit, "simulated_tool_observation_not_verified_multiturn")]
        if any(tool_error_flag(message)[0] or tool_error_flag(message)[1]
               for message in unit.get("messages", [])):
            return [self._port.reject(unit, "observed_tool_error_not_multiturn")]
        dialogue = self._prepare(unit, style)
        failed = (self._review_recorded(unit, dialogue) if unit["kind"] == "conversation"
                  else self._generate_turns(unit, style, dialogue))
        if failed is not None:
            return failed
        return self._assess_complete(unit, dialogue)

    def _prepare(self, unit, style):
        dialogue = DialogueProgress()
        prepared = self._port.prepare_source(unit, style)
        dialogue.source_context, dialogue.pack_evidence = prepared.context, prepared.evidence_text
        dialogue.selected_evidence_blocks = prepared.evidence_blocks
        dialogue.contract = unit.get("qa_contract")
        dialogue.evidence_level = (unit.get("revision_source_level") or ("human_provided_and_model_assessed" if unit.get("human_provided") else
                          document_evidence_level(unit, assessed=True) if unit.get("document_reading")
                          else "recorded_context_model_assessed" if unit["kind"] == "conversation"
                          else "source_and_model_assessed" if unit["kind"] == "document"
                          else "model_assessed_synthetic"))
        dialogue.source_verification = ("human_provided_not_independently_verified" if unit.get("human_provided") else
                               "text_restructuring_not_independently_verified" if (unit.get("document_reading") or {}).get("mode") == "model"
                               else "visual_transcription_not_independently_verified" if unit.get("document_reading")
                               else "recorded_unverified" if unit["kind"] == "conversation"
                               else "exact_quote_presence_only" if unit["kind"] == "document"
                               else "no_external_source")
        dialogue.reviews = []
        dialogue.quotes_by_turn = []
        dialogue.dialogue_design = (dialogue.contract or {}).get("dialogue_design") or {}
        # Type balancing governs the initial scenario, not who may see source
        # memory. Every separated dialogue keeps the director/questioner route
        # for later turns, including contracts without an optional design.
        dialogue.legacy_adaptive_route = (dialogue.contract is not None and dialogue.contract.get("dialogue_design") is not None
            and self.recipe.get("qa_director", {}).get("planning_mode") == "adaptive")
        dialogue.separated_step_route = dialogue.contract is not None and self._port.separated_questions() and not dialogue.legacy_adaptive_route
        dialogue.adaptive_dialogue = dialogue.legacy_adaptive_route or dialogue.separated_step_route
        dialogue.ordinary_stop_mode = (not dialogue.adaptive_dialogue and unit["kind"] != "conversation"
            and self.recipe.get("version", 0) >= 9
            and self.recipe.get("multiturn_stop_protocol_version") == 1)
        dialogue.ordinary_stop_audit = {}
        dialogue.dialogue_steps = []
        dialogue.dialogue_state = ({"user_intent": dialogue.dialogue_design.get("user_intent", ""),
            "progress": "", "shared_understanding": [], "open_issues": [],
            "active_constraints": [], "context_links": [],
            **({"conversational_frame": deepcopy(dialogue.dialogue_design["conversational_frame"])}
               if "conversational_frame" in dialogue.dialogue_design else {}),
            **({"expression_design": deepcopy(dialogue.contract["expression_design"])}
               if self.recipe.get("qa_expression_version") == 2 and dialogue.contract.get("expression_design") else {})}
            if dialogue.adaptive_dialogue else None)
        dialogue.dialogue_end_reason = "turn_limit"
        dialogue.dialogue_end_guidance = ""
        dialogue.dialogue_state_after_turn = 0
        if dialogue.adaptive_dialogue:
            dialogue.source_context["dialogue_design"] = dialogue.dialogue_design
        return dialogue

    def _review_recorded(self, unit, dialogue):
        dialogue.messages = deepcopy(unit["messages"])
        dialogue.ends, issue = completed_turn_ends(dialogue.messages)
        if issue:
            return [self._port.reject(unit, issue)]
        for turn_index, end in enumerate(dialogue.ends):
            check = self._port.assess([unit["id"], "multiturn_turn", turn_index],
                {**dialogue.source_context, "dialogue_before_answer": dialogue.messages[:end],
                 "recorded_context_not_independently_verified": True}, dialogue.messages[end])
            dialogue.reviews.append({"turn": turn_index + 1, "end_message_index": end, "judge": check})
            if not accepted(check):
                return [{**self._port.reject(unit, "multiturn_turn_rejected"), "failed_turn": turn_index + 1,
                         "turn_reviews": dialogue.reviews, "evidence_level": dialogue.evidence_level}]

    def _generate_turns(self, unit, style, dialogue):
        dialogue.messages = controlled_training_messages([], style)
        dialogue.raw_user_messages = []
        dialogue.finished_dialogue = False
        for turn_index in range(self.recipe.get("conversation_turns", 3)):
            feedback = None
            last_failed_candidate = None
            for attempt in range(2):
                planned_step = None
                current_dialogue_state = dialogue.dialogue_state
                if dialogue.contract is not None and turn_index == 0:
                    user_data = {"message": dialogue.contract["question"]}
                elif dialogue.adaptive_dialogue:
                    try:
                        planned_step = self._port.next_dialogue_step(unit, without_source_reasoning(dialogue.messages), dialogue.dialogue_state,
                            attempt=attempt, feedback=feedback)
                    except DialoguePlanError as error:
                        feedback = (error.feedback if type(self.recipe.get("question_brief_protocol_version")) is int
                            and self.recipe["question_brief_protocol_version"] == 2 else "invalid_dialogue_step")
                        continue
                    if not planned_step["continue"]:
                        dialogue.dialogue_steps.append({"after_turn": turn_index, **planned_step})
                        # This is still an untrusted summary. The final
                        # independent whole-dialogue review checks it
                        # against actual messages and source evidence.
                        dialogue.dialogue_state = planned_step["dialogue_state"]
                        dialogue.dialogue_state_after_turn = turn_index
                        dialogue.dialogue_end_reason = "natural_completion"
                        dialogue.dialogue_end_guidance = planned_step["reason"]
                        dialogue.finished_dialogue = True
                        break
                    current_dialogue_state = planned_step["dialogue_state"]
                    user_data = {"message": planned_step["user_message"]}
                else:
                    user_data = self._port.request([unit["id"], "multiturn_user", turn_index, attempt],
                        "generation", "workflow.multiturn_user",
                        {**dialogue.source_context, "turn": turn_index + 1, "total_turns": self.recipe.get("conversation_turns", 3),
                         "previous_messages": without_source_reasoning(dialogue.messages), "feedback": feedback},
                        **({"instruction": "遵循问答指导契约和提问规则。隐藏的教师资料不得自动成为用户可见线索；"
                            "无线索题的后续问题必须自包含，不引用读者不可见的来源。"} if dialogue.contract is not None else {}))
                if dialogue.ordinary_stop_mode:
                    from lib.domain.multiturn import user_actor_action
                    try:
                        action = user_actor_action(user_data)
                    except ValueError as error:
                        feedback = str(error)
                        continue
                    if action["end"]:
                        if not any(message.get("role") == "assistant" for message in dialogue.messages):
                            feedback = "multiturn_stop_before_first_turn"
                            continue
                        # A termination suggestion is private metadata,
                        # never a user turn or a factual approval. No next
                        # answer/turn score is purchased after this point.
                        dialogue.ordinary_stop_audit = {"dialogue_end_reason": "natural_completion",
                            "dialogue_end_after_turn": turn_index,
                            "dialogue_end_guidance": action["reason"],
                            "dialogue_end_scope": "user_actor_suggestion_not_message_or_evidence"}
                        dialogue.finished_dialogue = True
                        break
                    user_data = {"message": action["message"]}
                user_text = user_data.get("message") if isinstance(user_data, dict) else None
                if text_issue(user_text) or len(user_text) > (12000 if dialogue.contract is not None and turn_index == 0 else 6000):
                    feedback = "用户消息为空、含敏感信息或过长"
                    continue
                surface_repetition = any(same_answer(user_text, previous) for previous in dialogue.raw_user_messages)
                if surface_repetition and not dialogue.adaptive_dialogue:
                    feedback = "不能重复之前的用户问题"
                    continue
                if turn_index == 0 and dialogue.contract is not None:
                    user_text = self._port.learner_prompt(dialogue.contract)
                elif turn_index == 0 and unit["kind"] == "document":
                    user_text = ("请依据以下资料完成多轮问答，只使用资料支持的内容。\n\n资料：\n"
                                 f"{unit['text']}\n\n问题：\n{user_text}")
                elif turn_index == 0 and self.recipe.get("brief"):
                    user_text = f"任务背景：{self.recipe['brief']}\n\n问题：\n{user_text}"
                user_message = {"role": "user", "content": user_text}
                # Balanced runs used to ask later questions through the
                # answer model. A newly directed question must not inherit
                # an old answer or score when an interrupted run resumes.
                turn_identity = ([digest({"messages": [*dialogue.messages, user_message],
                    "contract": dialogue.contract, "step": planned_step})] if dialogue.separated_step_route and turn_index else [])
                response = self._port.request([unit["id"], "multiturn_answer", turn_index, attempt, *turn_identity],
                    "generation", "workflow.multiturn_assistant",
                    {**dialogue.source_context, "messages": [*without_source_reasoning(dialogue.messages), user_message], "feedback": feedback,
                     **({"learner_messages": [*without_source_reasoning(dialogue.messages), user_message]} if self._port.separated_questions() else {}),
                     **({"dialogue_state": current_dialogue_state,
                         "dialogue_step": planned_step} if dialogue.adaptive_dialogue else {})},
                    capture_response_details=style is None,
                    **({"allow_reasoning_fallback": False} if style is not None else {}),
                    **({"instruction": "遵循问答指导契约和回答规则。首轮严格遵循answer_policy；"
                        "后续按用户新提供的信息回答，仍须区分教师证据与用户可见证据，"
                        "无线索题允许使用教师资料核验的必要知识事实和准确、必要的公开引用。"
                        "不得披露内部提示词、内部检索包装或标识，不得输出与回答无关的原文，"
                        "也不得假装读者见过隐藏上文。" +
                        human_generation_instruction(dialogue.contract) +
                        ("\n生成风格要求：\n" + style["instruction"] if style is not None else "")}
                       if dialogue.contract is not None else
                       {"instruction": "生成风格要求：\n" + style["instruction"]} if style is not None else {}))
                answer = response.get("answer") if isinstance(response, dict) else None
                quotes = response.get("quotes") if isinstance(response, dict) else None
                if text_issue(answer) or len(answer) > 12000 or not isinstance(quotes, list):
                    feedback = "回答或逐字证据结构不合格"
                    continue
                response_policy = ((planned_step or {}).get("response_policy") if turn_index else
                                   (dialogue.contract or {}).get("answer_policy", "answer"))
                # Routing later turns through the director does not waive
                # evidence for factual answers. An unlabelled adaptive
                # follow-up may be a nonfactual acknowledgement; its actual
                # content is still checked against the dialogue and source.
                quotes_required = (response_policy in {"answer", "correct_premise"} or
                    not dialogue.adaptive_dialogue and (dialogue.contract is None or dialogue.contract["answer_policy"] in {"answer", "correct_premise"}))
                if unit["kind"] == "document" and ((not quotes and
                        quotes_required) or any(
                        not isinstance(quote, str) or not quote.strip()
                        or quote not in (dialogue.pack_evidence if dialogue.pack_evidence is not None else unit["text"])
                        or (dialogue.selected_evidence_blocks is not None and not any(quote in block for block in dialogue.selected_evidence_blocks))
                        for quote in quotes)):
                    feedback = "文档回答缺少可逐字匹配的原文依据"
                    continue
                if unit["kind"] == "brief" and quotes:
                    feedback = "开放需求没有可核对的外部来源，不应编造引文"
                    continue
                from lib.domain.provider_reasoning import select_candidate_reasoning, reasoning_provenance
                try:
                    selected_reasoning = select_candidate_reasoning(response, styled=style is not None)
                except ValueError:
                    feedback = "推理字段结构无效"
                    continue
                if style is not None and not selected_reasoning["content"]:
                    feedback = "风格化多轮回答必须返回非空reasoning和answer，按节点指定的推理语言组织实际推导。"
                    continue
                assistant_message = {"role": "assistant", "content": answer,
                    **({"reasoning_content": selected_reasoning["content"]} if selected_reasoning["content"] else {})}
                candidate = [*dialogue.messages, user_message, assistant_message]
                if self.recipe.get("multiturn_failure_audit_version") == 1:
                    last_failed_candidate = {"messages": deepcopy(candidate), "quotes": deepcopy(quotes),
                        "candidate_sha256": digest({"messages": candidate, "quotes": quotes}),
                        "status": "unaccepted_generation", "attempt": attempt,
                        "turn": turn_index + 1, "training_eligible": False}
                issue = conversation_issue(candidate)
                if issue:
                    feedback = issue
                    continue
                check = self._port.assess([unit["id"], "multiturn_turn", turn_index, attempt, *turn_identity],
                    {**dialogue.source_context, "dialogue_before_answer": [*dialogue.messages, user_message],
                     "quotes": quotes, "source_text": unit.get("text"),
                     **({"dialogue_state": current_dialogue_state,
                         "dialogue_step": planned_step,
                         "surface_repetition": surface_repetition} if dialogue.adaptive_dialogue else {})}, assistant_message)
                if not accepted(check):
                    feedback = check["reason"]
                    continue
                style_check = (self._port.check_style([unit["id"], "multiturn_style", turn_index, attempt, *turn_identity],
                    style, selected_reasoning["content"], answer)
                    if style is not None and self.recipe.get("review_repair") is None else None)
                if style_check is not None and not (style_check["keep"] and style_check["adherence"] >= 4):
                    feedback = style_check["reason"]
                    continue
                try:
                    contract_check = (self._port.check_contract(
                        [unit["id"], "multiturn_contract", turn_index, attempt, *turn_identity], unit, candidate,
                        prompt_id="workflow.multiturn_directed_check",
                        review_scope="completed_prefix",
                        **({"dialogue_state": current_dialogue_state,
                            "dialogue_steps": dialogue.dialogue_steps} if dialogue.adaptive_dialogue else {})) if dialogue.contract is not None else None)
                except ValueError as error:
                    if (self.recipe.get("multiturn_failure_audit_version") != 1
                            or str(error) != "invalid_qa_director_judge_schema"):
                        raise
                    return [{**self._port.reject(unit, "multiturn_contract_output_invalid"),
                        "failed_turn": turn_index + 1, "feedback": str(error),
                        "failed_generation": last_failed_candidate,
                        "accepted_prefix": deepcopy(dialogue.messages), "source_context": deepcopy(unit),
                        "turn_reviews": dialogue.reviews, "evidence_level": dialogue.evidence_level}]
                if contract_check is not None and not (contract_check["keep"] and contract_check["adherence"] >= 4):
                    feedback = contract_check["reason"]
                    continue
                dialogue.messages = candidate
                dialogue.raw_user_messages.append(user_data["message"])
                dialogue.reviews.append({"turn": turn_index + 1, "end_message_index": len(dialogue.messages) - 1,
                                "judge": check, "repair_attempts": attempt,
                                **reasoning_provenance(selected_reasoning),
                                **({"style_check": style_check} if style_check is not None else {}),
                                **({"surface_repetition": surface_repetition} if dialogue.adaptive_dialogue else {}),
                                **({"qa_contract_check": contract_check} if contract_check is not None else {})})
                dialogue.quotes_by_turn.append(quotes)
                if dialogue.adaptive_dialogue:
                    dialogue.dialogue_state = current_dialogue_state
                    dialogue.dialogue_state_after_turn = turn_index
                    dialogue.dialogue_steps.append({"turn": turn_index + 1,
                        **(planned_step if planned_step is not None else {
                            "continue": True, "user_message": dialogue.contract["question"],
                            "reason": "initial_design", "dialogue_state": dialogue.dialogue_state})})
                break
            else:
                reason = (feedback if dialogue.ordinary_stop_mode and feedback in {
                    "invalid_multiturn_user_action", "invalid_multiturn_stop", "multiturn_stop_before_first_turn"}
                    else "multiturn_turn_failed_after_repair")
                return [{**self._port.reject(unit, reason),
                         "failed_turn": turn_index + 1, "feedback": feedback,
                         **({"failed_generation": last_failed_candidate,
                             "accepted_prefix": deepcopy(dialogue.messages), "source_context": deepcopy(unit)}
                            if self.recipe.get("multiturn_failure_audit_version") == 1 else {}),
                         "turn_reviews": dialogue.reviews, "evidence_level": dialogue.evidence_level}]
            if dialogue.finished_dialogue:
                break
        dialogue.ends, issue = completed_turn_ends(dialogue.messages)
        if issue:
            participant_stop = (self.recipe.get("qa_expression_version") == 2
                                and dialogue.adaptive_dialogue and dialogue.dialogue_end_reason == "natural_completion")
            if (dialogue.ordinary_stop_audit or participant_stop) and issue == "insufficient_completed_turns":
                issue = "multiturn_natural_end_insufficient_turns"
            return [{**self._port.reject(unit, issue),
                **({"messages": dialogue.messages, "turn_reviews": dialogue.reviews,
                    "completed_turns": len(dialogue.reviews), **dialogue.ordinary_stop_audit}
                   if dialogue.ordinary_stop_audit else {}),
                **({"messages": dialogue.messages, "turn_reviews": dialogue.reviews, "dialogue_steps": dialogue.dialogue_steps,
                    "completed_turns": len(dialogue.reviews), "dialogue_state": dialogue.dialogue_state,
                    "dialogue_end_reason": dialogue.dialogue_end_reason, "dialogue_end_guidance": dialogue.dialogue_end_guidance,
                    "dialogue_end_scope": "director_suggestion_not_message_or_evidence"}
                   if participant_stop else {}),
                **({"source_context": deepcopy(unit), "evidence_level": dialogue.evidence_level,
                    "source_verification": dialogue.source_verification,
                    "quotes_by_turn": deepcopy(dialogue.quotes_by_turn)}
                   if self.recipe.get("multiturn_terminal_review_version") == 1
                   and issue == "multiturn_natural_end_insufficient_turns" else {})}]

    def _assess_complete(self, unit, dialogue):
        completion_check = {}
        if dialogue.ordinary_stop_audit:
            from lib.prompts.workflow_multiturn import NATURAL_COMPLETION_REVIEW
            completion_check["instruction"] = NATURAL_COMPLETION_REVIEW
        consistency = self._port.assess([unit["id"], "multiturn_consistency"],
            {**dialogue.source_context, "evidence_level": dialogue.evidence_level, "turn_reviews": dialogue.reviews,
             "source_text": unit.get("text"), "whole_dialogue": True,
             **dialogue.ordinary_stop_audit,
             **({"dialogue_state": dialogue.dialogue_state, "dialogue_steps": dialogue.dialogue_steps,
                 "dialogue_end_reason": dialogue.dialogue_end_reason,
                 "dialogue_state_after_turn": dialogue.dialogue_state_after_turn,
                 "dialogue_state_scope": "final_summary" if dialogue.dialogue_end_reason == "natural_completion" else "before_last_turn",
                 "dialogue_end_guidance": dialogue.dialogue_end_guidance} if dialogue.adaptive_dialogue else {})},
            dialogue.messages, prompt_id="workflow.multiturn_consistency", **completion_check)
        if not accepted(consistency):
            return [{**self._port.reject(unit, "multiturn_consistency_rejected"),
                     **({"messages": dialogue.messages, "source_context": deepcopy(unit)}
                        if self.recipe.get("review_repair") is not None else {}),
                     "turn_reviews": dialogue.reviews, "consistency": consistency,
                     "evidence_level": dialogue.evidence_level, **dialogue.ordinary_stop_audit}]
        if (dialogue.contract is not None and dialogue.contract.get("human_design", {}).get("seed", {}).get("revision_context")):
            contract_check = self._port.check_contract([unit["id"], "multiturn_revision_complete"], unit, dialogue.messages,
                prompt_id="workflow.multiturn_directed_check", review_scope="complete",
                dialogue_state=dialogue.dialogue_state, dialogue_steps=dialogue.dialogue_steps)
            if not (contract_check["keep"] and contract_check["adherence"] >= 4):
                return [{**self._port.reject(unit, "human_revision_feedback_not_applied"),
                         "turn_reviews": dialogue.reviews, "consistency": consistency, "qa_contract_check": contract_check,
                         "evidence_level": dialogue.evidence_level}]
        return [{"id": unit["id"], "source_id": unit["source_id"],
                 "source_name": unit.get("source_name"), "location": unit.get("location"),
                 "source_location": unit.get("source_location"),
                 "status": "eligible", "messages": dialogue.messages, "tools": unit.get("tools", []),
                 **({"source_context": deepcopy(unit)} if self.recipe.get("review_repair") is not None else {}),
                 "synthetic": unit["kind"] != "conversation", "evidence_level": dialogue.evidence_level,
                 "source_verification": dialogue.source_verification,
                 "fact_verification": "not_independently_verified",
                 "tool_verification": ("not_replayed" if any(
                     message.get("tool_calls") or message.get("toolCalls") for message in dialogue.messages)
                     else "not_applicable"),
                 "turn_count": len(dialogue.ends), "turn_reviews": dialogue.reviews, "consistency": consistency,
                 "quotes_by_turn": dialogue.quotes_by_turn,
                 **({"multiturn_stop_protocol_version": 1,
                     "dialogue_end_reason": "turn_limit", **dialogue.ordinary_stop_audit} if dialogue.ordinary_stop_mode else {}),
                 **({"dialogue_design": dialogue.dialogue_design, "dialogue_steps": dialogue.dialogue_steps,
                     "dialogue_state": dialogue.dialogue_state, "dialogue_end_reason": dialogue.dialogue_end_reason,
                     "dialogue_state_after_turn": dialogue.dialogue_state_after_turn,
                     "dialogue_state_scope": "final_summary" if dialogue.dialogue_end_reason == "natural_completion" else "before_last_turn",
                     "dialogue_end_guidance": dialogue.dialogue_end_guidance} if dialogue.adaptive_dialogue else {}),
                 **({"qa_contract": dialogue.contract, "family_id": unit["family_id"],
                     "parent_id": unit.get("parent_id")} if dialogue.contract is not None else {}),
                 "source_text_sha256": digest(unit["text"]) if unit.get("text") else None}]
