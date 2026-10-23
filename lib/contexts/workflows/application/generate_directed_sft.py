"""Answer the assigned learner task without rewriting its question or evidence scope."""
from __future__ import annotations
from .generation_ports import DirectedSftPort
from ..domain.dialogue import controlled_training_messages, without_source_reasoning
from lib.domain.workflow_quality import accepted, conversation_issue, text_issue
from ..domain.generation import task_controls, learner_prompt
from lib.domain.provider_reasoning import select_candidate_reasoning, reasoning_provenance
from lib.domain.human_augmentation import human_generation_instruction


class GenerateDirectedSft:
    def __init__(self, port: DirectedSftPort, recipe: dict):
        self._port = port
        self.recipe = recipe

    def execute(self, unit):
        """Consume exactly the director question, evidence visibility and policy."""
        if unit.get("kind") == "conversation":
            raise ValueError("qa_director_recorded_conversation_changed")
        contract = unit["qa_contract"]
        separated = self._port.separated_questions()
        evidence_level = (unit.get("revision_source_level") or (
            "human_provided_and_model_assessed" if unit.get("human_provided") else
            "model_assessed_synthetic" if unit["kind"] == "brief" else
            "model_restructured_source_text_and_model_assessed"
                if (unit.get("document_reading") or {}).get("mode") == "model" else
            "model_transcribed_visual_source_and_model_assessed" if unit.get("document_reading") else
            "source_and_model_assessed"))
        style = self._port.generation_style("sft", unit["id"])
        native_capture = style is None and self.recipe.get("provider_reasoning_capture_version") == 1
        context = {"source_context": {key: value for key, value in unit.items()
                                      if key not in {"qa_contract", "family_id", "parent_id", "director_batch"}},
                   "qa_contract": contract,
                   "requirement": self.recipe.get("brief", ""),
                   "question_rules": self.recipe["qa_director"]["question_rules"],
                   "answer_rules": self.recipe["qa_director"]["answer_rules"],
                   **task_controls(contract)}
        if style is not None:
            context = without_source_reasoning(context)
            context["generation_style"] = style
        context = self._port.prepare_source_request(context, unit, contract)
        if self.recipe.get("source_memory_protocol_version") in {1, 2}:
            context["learner_messages"] = controlled_training_messages(
                [{"role": "user", "content": learner_prompt(contract)}], style)
        selected_blocks = (self._port.selected_evidence_blocks(unit, contract)
                           if context.get("source_line_scope") == "selected" else None)
        feedback = None
        check = contract_check = style_check = None
        instructions = ([human_generation_instruction(contract)] if contract.get("human_design") else [])
        if style is not None:
            instructions.append("生成风格要求：\n" + style["instruction"])
        failure_kind = "invalid_output"
        for attempt in range(2):
            candidate = self._port.request([unit["id"], "sft_directed", attempt], "generation",
                "workflow.sft_answer" if separated else "workflow.sft_directed", {**context, "feedback": feedback},
                **({"instruction": "\n".join(instructions)} if instructions else {}),
                **({"allow_reasoning_fallback": False} if style is not None else {}),
                **({"capture_response_details": True} if native_capture else {}))
            failure_kind = "invalid_output"
            required_fields = (("answer", "reasoning") if style is not None or not native_capture else ("answer",))
            if not separated:
                required_fields = ("question", *required_fields)
            separated_schema_invalid = False
            if separated and isinstance(candidate, dict):
                if style is not None or not native_capture:
                    separated_schema_invalid = set(candidate) != {"answer", "reasoning", "quotes"}
                else:
                    separated_schema_invalid = (not {"answer", "quotes"}.issubset(candidate) or
                        not set(candidate).issubset({"answer", "reasoning", "quotes"}))
            if (not isinstance(candidate, dict)
                    or separated_schema_invalid
                    or any(text_issue(candidate.get(key)) for key in required_fields)
                    or (not separated and candidate["question"].strip() != contract["question"])):
                feedback = (("只返回answer、reasoning、quotes，不新增或改写用户问题。" if style is not None or not native_capture else
                             "只返回answer、quotes，可附带文本reasoning，不新增或改写用户问题。") if separated else
                            "保持指导员分配的问题原文，返回完整问题和答案；推理字段须为文本。")
                continue
            try:
                reasoning = select_candidate_reasoning(candidate if native_capture else dict(candidate),
                                                       styled=style is not None)
            except ValueError:
                feedback = "推理字段必须是文本；不能把结构化对象当作推理。"
                continue
            quotes = candidate.get("quotes")
            evidence_text = unit.get("text", "")
            if unit.get("source_context_pack"):
                evidence_text = (self._port.selected_evidence_text(unit, contract)
                    if context.get("source_line_scope") == "selected" else self._port.source_evidence(unit))
            if (not isinstance(quotes, list) or len(quotes) > 32
                    or any(not isinstance(q, str) or not q.strip() for q in quotes)
                    or any(q not in evidence_text for q in quotes)
                    or (selected_blocks is not None and any(not any(q in block for block in selected_blocks) for q in quotes))
                    or (unit["kind"] == "document" and contract["answer_policy"] in {"answer", "correct_premise"}
                        and not quotes)):
                feedback = "证据必须是教师来源中的逐字片段；不能把历史答案当作事实依据。"
                continue
            assistant_message = {"role": "assistant", "content": candidate["answer"]}
            if reasoning["content"]:
                assistant_message["reasoning_content"] = reasoning["content"]
            messages = controlled_training_messages(
                [{"role": "user", "content": learner_prompt(contract)}, assistant_message], style)
            issue = conversation_issue(messages)
            if issue:
                feedback = issue
                continue
            if self.recipe.get("review_repair") is not None:
                return [{"id": unit["id"], "source_id": unit["source_id"], "source_name": unit.get("source_name"),
                         "location": unit.get("location"), "source_location": unit.get("source_location"),
                         "status": "eligible", "messages": messages, "tools": [], "quotes": quotes,
                         "source_context": unit, "qa_contract": contract, "family_id": unit["family_id"],
                         "parent_id": unit.get("parent_id"), "generation_check": "structure_and_source_quotes",
                         "repair_attempts": attempt, **reasoning_provenance(reasoning),
                         "evidence_level": evidence_level,
                         **({"generation_style": style} if style else {})}]
            check = self._port.assess([unit["id"], "sft_directed_judge", attempt],
                {**context, "rendered_prompt": messages[:-1]}, messages,
                **({"allow_reasoning_fallback": False} if style is not None else {}))
            contract_check = self._port.check_contract([unit["id"], "sft_directed_contract", attempt], unit, messages)
            style_check = (self._port.check_style([unit["id"], "sft_directed_style", attempt], style,
                           candidate["reasoning"], candidate["answer"]) if style is not None else None)
            if (accepted(check) and contract_check["keep"] and contract_check["adherence"] >= 4
                    and (style_check is None or (style_check["keep"] and style_check["adherence"] >= 4))):
                return [{"id": unit["id"], "source_id": unit["source_id"],
                         "source_name": unit.get("source_name"), "location": unit.get("location"),
                         "source_location": unit.get("source_location"), "status": "eligible",
                         "messages": messages, "tools": [], "quotes": quotes, "judge": check,
                         "source_context": unit, "qa_contract": contract,
                         "qa_contract_check": contract_check, "family_id": unit["family_id"],
                         "parent_id": unit.get("parent_id"), "repair_attempts": attempt,
                         **reasoning_provenance(reasoning),
                         "evidence_level": evidence_level,
                         **({"generation_style": style, "style_check": style_check} if style else {})}]
            feedback = {"quality": check["reason"], "contract": contract_check["reason"],
                        "style": style_check["reason"] if style_check else None}
            failure_kind = "quality"
        return [{**self._port.reject(unit, "directed_sft_quality_failed_after_repair"),
                 **({"messages": messages, "quotes": quotes} if self.recipe.get("review_repair") is not None
                     and "messages" in locals() else {}),
                 "qa_contract": contract, "family_id": unit["family_id"],
                 "parent_id": unit.get("parent_id"), "feedback": feedback,
                 **({"generation_failure_kind": failure_kind} if separated else {}),
                 "judge": check, "qa_contract_check": contract_check,
                 **({"generation_style": style, "style_check": style_check} if style else {})}]
