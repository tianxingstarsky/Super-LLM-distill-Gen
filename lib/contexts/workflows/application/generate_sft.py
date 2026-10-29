"""Generate or assess SFT while preserving source records and pinned review policy."""
from __future__ import annotations
from copy import deepcopy
from .generation_ports import SftGenerationPort
from ..domain.dialogue import (controlled_training_messages, without_source_reasoning,
    document_evidence_level, dialogue_digest as digest)
from lib.domain.workflow_quality import accepted, conversation_issue, text_issue, tool_error_flag
from ..domain.generation import is_pdf_source
from lib.domain.source_quote import quote_spans
from ..domain.imported_reasoning import imported_reasoning_metadata


class GenerateSft:
    def __init__(self, port: SftGenerationPort, recipe: dict):
        self._port = port
        self.recipe = recipe

    def execute(self, unit):
        rows = self._execute(unit)
        for row in rows:
            preserved = row.get("reasoning_origin") == "source" and "messages" in row
            row.update(imported_reasoning_metadata(unit, messages_preserved=preserved))
        return rows

    def _execute(self, unit):
        if unit.get("qa_contract") is not None:
            return self._port.generate_directed_sft(unit)
        if unit.get("verification_status") == "synthetic_unverified":
            return [self._port.reject(unit, "simulated_tool_observation_not_verified_sft")]
        if any(tool_error_flag(message)[0] or tool_error_flag(message)[1]
               for message in unit.get("messages", [])):
            return [self._port.reject(unit, "observed_tool_error_not_sft")]
        from lib.domain.reasoning_fields import sft_reasoning_issue, sft_training_messages
        from lib.domain.provider_reasoning import select_candidate_reasoning, reasoning_provenance
        if issue := sft_reasoning_issue(unit.get("messages", [])):
            return [self._port.reject(unit, issue)]
        history = sft_training_messages(unit.get("messages", []))
        if (self.recipe.get("independent_cot_input_version") == 1
                and self.recipe.get("targets") == ["cot"]
                and self._port.generation_style("cot", unit["id"]) is not None
                and unit["kind"] == "conversation" and history
                and self._port.generation_style("sft", unit["id"]) is None):
            # This is input preparation, not publication or factual acceptance.
            # Independently authored CoT must be checked on its new response,
            # rather than pay to grade a draft it is forbidden to consume.
            if issue := conversation_issue(history):
                return [self._port.reject(unit, issue)]
            restyle = self.recipe.get("node_generation", {}).get("cot", {}).get("answer_mode") == "faithful_restyle"
            return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible",
                     "messages": deepcopy(history) if restyle else without_source_reasoning(history), "tools": unit.get("tools", []),
                     "quotes": [], "source_context": deepcopy(unit),
                     "generation_check": "learner_structure_only_for_restyle" if restyle else "learner_structure_only_for_independent_cot",
                     "reasoning_origin": "unassessed_input_not_training_output",
                     "evidence_level": "recorded_context_unassessed"}]
        context = {"source": {**unit, "messages": [
                       {**m, "content": "[来源内容隐藏]" if m.get("role") == "assistant" else m.get("content", "")}
                       for m in unit.get("messages", [])]},
                   "requirement": self.recipe["brief"]}
        style = self._port.generation_style("sft", unit["id"])
        if style is not None:
            if history:
                controlled_training_messages(history[:-1], style)
            history = without_source_reasoning(history)
            context = without_source_reasoning(context)
            context["generation_style"] = style
        feedback = None
        check, style_check = None, None
        for attempt in range(2):
            evidence_spans = None
            if style is None and history and unit["kind"] == "conversation":
                messages = history
                quotes = []
            else:
                key = [unit["id"], "sft_styled" if style is not None else "sft", attempt]
                candidate = self._port.request(key, "generation", "workflow.sft_styled" if style is not None else "workflow.sft",
                    {**context, "feedback": feedback},
                    capture_response_details=style is None,
                    **({"instruction": "生成风格要求：\n" + style["instruction"]} if style is not None else {}))
                required = (("question", "answer", "reasoning") if style is not None
                    or self.recipe.get("provider_reasoning_capture_version") != 1 else ("question", "answer"))
                if not isinstance(candidate, dict) or any(text_issue(candidate.get(k)) for k in required):
                    feedback = "回答结构不合格或存在敏感数据"
                    continue
                try:
                    selected_reasoning = select_candidate_reasoning(candidate, styled=style is not None)
                except ValueError:
                    feedback = "推理字段结构无效"
                    continue
                quotes = candidate.get("quotes", [])
                if unit["kind"] == "document":
                    evidence_text = unit["text"]
                    if unit.get("source_context_pack"):
                        evidence_text = self._port.source_evidence(unit)
                    pdf_source = is_pdf_source(self.recipe, unit)
                    evidence_spans = quote_spans(evidence_text, quotes, pdf_whitespace=pdf_source)
                    if evidence_spans is None:
                        feedback = "需要原文中可逐字匹配的证据"
                        continue
                if unit["kind"] == "document":
                    from lib.domain.learner_context import prompt_at_least
                    prompt_id = "workflow.sft_styled" if style is not None else "workflow.sft"
                    if prompt_at_least(self.recipe, prompt_id, (1, 1, 0)):
                        visible_context = candidate.get("visible_context")
                        if visible_context is not None:
                            if (not isinstance(visible_context, str) or not visible_context.strip()
                                    or len(visible_context) > len(evidence_text)
                                    or any(line not in evidence_text for line in visible_context.splitlines() if line.strip())):
                                feedback = "题面资料须使用清洗资料包中的真实片段，包含本题所需条件。"
                                continue
                        else:
                            # Keep the anchor and every quoted supporting span
                            # visible, including evidence from adjacent sections.
                            visible_context = "\n\n".join(dict.fromkeys([unit["text"], *quotes]))
                        prompt = f"资料：\n{visible_context}\n\n用户消息：\n{candidate['question']}"
                    else:
                        prompt = f"请根据以下资料回答问题。只使用资料支持的内容。\n\n资料：\n{unit['text']}\n\n问题：\n{candidate['question']}"
                    prefix = [{"role": "user", "content": prompt}]
                elif unit["kind"] == "brief":
                    prompt = f"{self.recipe['brief']}\n\n任务：\n{candidate['question']}" if self.recipe["brief"] else candidate["question"]
                    prefix = [{"role": "user", "content": prompt}]
                else:
                    prefix = history[:-1] if history else [{"role": "user", "content": candidate["question"]}]
                messages = [*prefix, {"role": "assistant", "content": candidate["answer"],
                    **({"reasoning_content": selected_reasoning["content"]} if selected_reasoning["content"] else {})}]
                messages = controlled_training_messages(messages, style)
            issue = conversation_issue(messages)
            if issue:
                feedback = issue
                continue
            if self.recipe.get("review_repair") is not None:
                # Content scoring/correction belongs to the review node in v18.
                # Generation keeps deterministic shape and source-quote checks.
                return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible",
                         "messages": messages, "tools": unit.get("tools", []), "quotes": quotes,
                         "source_context": unit, "generation_check": "structure_and_source_quotes",
                         **({"quote_spans": evidence_spans} if evidence_spans is not None else {}),
                         **({"generation_style": style} if style is not None else {}),
                         "repair_attempts": attempt,
                         **({"reasoning_origin": "source"} if messages is history else reasoning_provenance(selected_reasoning)),
                         "evidence_level": ("recorded_context_model_assessed" if messages is history else
                            "model_assessed_synthetic" if unit["kind"] == "brief" else document_evidence_level(unit, assessed=True))}]
            # Recorded answers need no generation call. Key their verdict to
            # the exact record so an old generated-answer score cannot be reused.
            judge_key = ([unit["id"], "recorded_sft_judge_v1", digest(messages)] if messages is history
                         else [unit["id"], "sft_judge", attempt])
            check = self._port.assess(judge_key, {**context, "rendered_prompt": messages[:-1]}, messages,
                                      **({"allow_reasoning_fallback": False} if style is not None else {}))
            style_check = (self._port.check_style([unit["id"], "sft_style", attempt], style,
                                           messages[-1]["reasoning_content"], messages[-1]["content"])
                           if style is not None else None)
            if accepted(check) and (style_check is None or
                                    (style_check["keep"] and style_check["adherence"] >= 4)):
                return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible", "messages": messages,
                         "tools": unit.get("tools", []), "quotes": quotes, "judge": check, "source_context": unit,
                         **({"quote_spans": evidence_spans} if evidence_spans is not None else {}),
                         **({"generation_style": style, "style_check": style_check} if style is not None else {}),
                         **({"human_augmentation_status": "recorded_conversation_preserved"}
                            if unit["kind"] == "conversation" and
                            self.recipe.get("qa_director", {}).get("human_augmentation", {}).get("enabled") else {}),
                         "repair_attempts": attempt,
                         **({"reasoning_origin": "source"} if messages is history else reasoning_provenance(selected_reasoning)),
                         "evidence_level": ("recorded_context_model_assessed" if messages is history else
                                            "model_assessed_synthetic" if unit["kind"] == "brief"
                                            else document_evidence_level(unit, assessed=True))}]
            if messages is history:
                # Failed recorded evidence stays available for explicit rework;
                # importing a conversation is not permission to invent its answer.
                return [{**self._port.reject(unit, "recorded_sft_judge_rejected"),
                         "messages": messages, "tools": unit.get("tools", []), "quotes": quotes,
                         "source_context": unit, "judge": check, "feedback": check["reason"],
                         "reasoning_origin": "source", "evidence_level": "recorded_context_model_assessed"}]
            feedback = {"correctness": check["reason"] if not accepted(check) else None,
                        "style": style_check["reason"] if style_check is not None and
                        not (style_check["keep"] and style_check["adherence"] >= 4) else None}
            if style is None:
                feedback = check["reason"]
        return [{**self._port.reject(unit, "sft_quality_failed_after_repair"), "feedback": feedback,
                 **({"messages": messages, "quotes": quotes} if self.recipe.get("review_repair") is not None
                     and "messages" in locals() else {}),
                 **({"generation_style": style, "judge": check, "style_check": style_check,
                     "repair_attempts": 1} if style is not None else {})}]
