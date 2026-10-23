"""Extract provider reasoning or author and verify a bounded styled continuation."""
from __future__ import annotations
from copy import deepcopy
from .generation_ports import CotGenerationPort
from ..domain.dialogue import (controlled_training_messages, without_source_reasoning,
    dialogue_digest as digest)
from ..domain.generation import qa_metadata
from lib.domain.workflow_quality import accepted, text_issue, verdict
import hashlib
import re
from ..domain.generation import EmptyVisibleAnswer
from lib.domain.workflow_reasoning_route import cot_updates_sft, cot_with_sft_context
from lib.domain.knowledge_voice import knowledge_voice_options


class GenerateCot:
    def __init__(self, port: CotGenerationPort, recipe: dict):
        self._port = port
        self.recipe = recipe

    def execute(self, sample):
        rows = self._port.extract_cot(sample)
        if cot_updates_sft(self.recipe):
            return [cot_with_sft_context(sample, row) for row in rows]
        return rows

    def extract(self, sample):
        style = self._port.generation_style("cot", sample["id"])
        if style is not None:
            return self._port.generate_styled_cot(sample, style)
        messages = sample["messages"]
        if not messages or messages[-1].get("role") != "assistant":
            return [self._port.reject(sample, "cot_missing_final_answer")]
        reasoning = messages[-1].get("reasoning_content", "")
        answer = messages[-1].get("content", "")
        if sample.get("reasoning_origin") != "provider_reasoning":
            reasoning = reasoning.strip()
            answer = answer.strip()
        if not reasoning.strip() or not answer.strip():
            return [self._port.reject(sample, "cot_missing_reasoning_or_answer")]
        if self.recipe.get("review_repair") is not None:
            return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                     "question": messages[:-1], "reasoning": [reasoning], "answer": answer,
                     "source_context": deepcopy(sample.get("source_context")), "quotes": sample.get("quotes", []),
                     **{name: deepcopy(sample[name]) for name in ("reasoning_origin", "reasoning_summary",
                         "reasoning_api_format", "reasoning_call") if name in sample},
                     "evidence_level": sample["evidence_level"], **qa_metadata(sample)}]
        task = {"prompt": messages[:-1], "reasoning": reasoning, "answer": answer,
                "source": sample.get("source_context"), "quotes": sample.get("quotes", [])}
        check = self._port.request([sample["id"], "cot_check"], "jev", "workflow.rationale_check", task)
        try:
            check = verdict(check)
        except ValueError:
            self._port.invalidate_checkpoint(["call", [sample["id"], "cot_check"]])
            raise
        if not accepted(check):
            return [{**self._port.reject(sample, "cot_reasoning_rejected"), "judge": check}]
        contract_check = (self._port.check_contract([sample["id"], "cot_directed_contract"], sample, messages,
            prompt_id="workflow.cot_directed_check") if sample.get("qa_contract") else None)
        if contract_check is not None and not (contract_check["keep"] and contract_check["adherence"] >= 4):
            return [{**self._port.reject(sample, "cot_qa_contract_rejected"),
                     **qa_metadata(sample, contract_check)}]
        steps = ([reasoning] if sample.get("reasoning_origin") == "provider_reasoning" else
                 [part.strip() for part in re.split(r"(?<=[。.!?])\s*", reasoning) if part.strip()])
        return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                 "question": messages[:-1], "reasoning": steps or [reasoning], "answer": answer,
                 "judge": check, "evidence_level": sample["evidence_level"],
                 **{name: deepcopy(sample[name]) for name in ("reasoning_origin", "reasoning_summary",
                     "reasoning_api_format", "reasoning_call") if name in sample},
                 **qa_metadata(sample, contract_check)}]

    def generate_styled(self, sample, style):
        """Author a new explicit rationale; never copy a provider reasoning channel."""
        messages = sample.get("messages", [])
        if (not messages or messages[-1].get("role") != "assistant"
                or text_issue(messages[-1].get("content"))):
            return [self._port.reject(sample, "cot_missing_final_answer")]
        source = without_source_reasoning(sample.get("source_context"))
        task = {"prompt": controlled_training_messages(
                    without_source_reasoning(messages[:-1]), style), "source": source,
                "quotes": sample.get("quotes", []), "reference_answer": messages[-1]["content"],
                "generation_style": style}
        if (type(self.recipe.get("authored_reasoning_warmup_version")) is int
                and self.recipe["authored_reasoning_warmup_version"] == 1
                and self.recipe.get("node_generation", {}).get("cot", {}).get("answer_mode") != "native_prefill"):
            task["authored_reasoning_warmup"] = {"version": 1, "stage": "cot",
                                                "fields": ["candidate.reasoning"]}
        if self.recipe.get("training_surface_protocol_version") in {1, 2}:
            try:
                assessed_reference = isinstance(sample.get("judge"), dict) and accepted(sample["judge"])
            except (KeyError, TypeError, ValueError):
                assessed_reference = False
            task["reference_answer_status"] = "model_assessed" if assessed_reference else "unassessed_not_supplied"
            if not assessed_reference:
                # Structural eligibility is not factual validation. A long
                # styled explanation must not beautify an unchecked answer.
                task.pop("reference_answer")
        if sample.get("qa_contract"):
            task.update(qa_contract=sample["qa_contract"],
                question_rules=self.recipe["qa_director"]["question_rules"],
                answer_rules=self.recipe["qa_director"]["answer_rules"])
        if isinstance(source, dict):
            task = self._port.prepare_source_request(task, source, sample.get("qa_contract"))
        protocol = self.recipe.get("authored_reasoning_protocol_version") == 1
        native_prefill = self.recipe.get("node_generation", {}).get("cot", {}).get("answer_mode") == "native_prefill"
        if native_prefill:
            # Capability checks happen before spending on the rationale phase.
            self._port.validate_native_prefill()
            if any(message.get("role") == "tool" or message.get("tool_calls") or message.get("function_call")
                   for message in task["prompt"]):
                raise ValueError("model_reasoning_prefill_tools_unsupported")
        instruction = "生成风格要求：\n" + style["instruction"]
        if task.get("reference_answer_status") == "unassessed_not_supplied":
            instruction += "\n上游只有结构合格，尚未事实评审，本次不提供其答案。独立依据真实问题和原始知识生成答案与解释，不推测上游答案，不把未评审结果当事实。资料不足时如实保留边界，不以换题或添造事实补救。"
        if protocol and not native_prefill:
            from lib.domain.authored_reasoning import AUTHORED_REASONING_PROTOCOL_INSTRUCTION, authored_reasoning_issue
            protocol_instruction = AUTHORED_REASONING_PROTOCOL_INSTRUCTION
            if self.recipe.get("reasoning_shape_version") == 1:
                from lib.prompts.workflow_reasoning import task_adaptive_reasoning_template
                protocol_instruction = task_adaptive_reasoning_template(protocol_instruction)
            instruction += "\n" + protocol_instruction
        from lib.domain.authored_reasoning import authored_reasoning_issue
        feedback, check, style_check, contract_check = None, None, None, None
        for attempt in range(2):
            prefill_audit = {}
            if native_prefill:
                value, prefill_audit = self._port.generate_native_answer(sample, task, style, feedback, attempt)
                if value is None:
                    feedback = prefill_audit
                    if (self.recipe.get("native_answer_retry_version") == 1
                            and isinstance(prefill_audit.get("native_answer_terminal"), dict)):
                        # One completed-but-empty answer is a sample failure,
                        # not a reason to re-author its verified rationale or
                        # stop other independent rows in the batch.
                        return [{**self._port.reject(sample, "native_answer_missing_after_retry"),
                            "question": deepcopy(task["prompt"]), "source_context": deepcopy(source),
                            "input_digest": digest({"question": task["prompt"], "source_context": source}),
                            "sft_parent_id": sample["id"], "generation_style": style,
                            "feedback": prefill_audit, "native_answer_terminal": deepcopy(
                                prefill_audit["native_answer_terminal"]),
                            "repair_attempts": attempt, **qa_metadata(sample)}]
                    continue
            else:
                value = self._port.request([sample["id"], "cot_generate_v1", attempt], "generation",
                             "workflow.cot_generate", {**task, "feedback": feedback},
                             instruction=instruction)
            if not isinstance(value, dict) or any(text_issue(value.get(key)) for key in ("reasoning", "answer")):
                feedback = "推理和答案必须是有效的完整文本"
                continue
            if protocol and (issue := authored_reasoning_issue(value)):
                feedback = {"reasoning_protocol": issue,
                    "repair": "补齐具体前提、支持桥梁与本题针对性检查，保持任务和风格；不要只加字数或机械凑步骤。"}
                continue
            surface_protocol = self.recipe.get("training_surface_protocol_version") in {1, 2}
            if surface_protocol:
                from lib.domain.training_surface import training_surface_issue
                issue = training_surface_issue(value["reasoning"], value["answer"],
                    visible_messages=task["prompt"], contract=sample.get("qa_contract"),
                    protocol_version=self.recipe.get("training_surface_protocol_version", 1),
                    **knowledge_voice_options(self.recipe))
                if issue:
                    feedback = {"training_surface": issue,
                        "repair": "解释和答案全文只围绕用户任务展开，用已掌握的事实推导；移除幕后制作过程，不删必要论证或编造经历。文章理解可引用用户实际可见的文章。"}
                    continue
            reasoning_audit = ({"reasoning_protocol_version": 1,
                "reasoning_trace": deepcopy(value["reasoning_trace"]), "reasoning_trace_status": "authored",
                "reasoning_trace_content_sha256": digest({"reasoning": value["reasoning"], "answer": value["answer"]})}
                if protocol else {})
            if surface_protocol:
                reasoning_audit["training_surface_protocol_version"] = self.recipe["training_surface_protocol_version"]
            if native_prefill:
                reasoning_audit.update(prefill_audit)
            if self.recipe.get("review_repair") is not None:
                return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                         "question": task["prompt"], "reasoning": [value["reasoning"]], "answer": value["answer"],
                         "source_context": source, "quotes": sample.get("quotes", []), "generation_style": style,
                         "repair_attempts": attempt, "reasoning_origin": "prompt_styled_generation",
                         "evidence_level": sample["evidence_level"], **reasoning_audit, **qa_metadata(sample)}]
            check = self._port.assess([sample["id"], "cot_generated_check_v1", attempt],
                                      {key: item for key, item in task.items() if key != "generation_style"},
                                      {"reasoning": value["reasoning"], "answer": value["answer"],
                                       **({"reasoning_trace": value["reasoning_trace"]} if protocol else {})},
                                      prompt_id="workflow.rationale_check", allow_reasoning_fallback=False)
            style_check = self._port.check_style([sample["id"], "cot_style_v1", attempt], style,
                                          value["reasoning"], value["answer"])
            contract_check = (self._port.check_contract([sample["id"], "cot_generated_directed_contract", attempt],
                sample, [*task["prompt"], {"role": "assistant", "content": value["answer"],
                    "reasoning_content": value["reasoning"]}], prompt_id="workflow.cot_directed_check")
                if sample.get("qa_contract") else None)
            if (accepted(check) and style_check["keep"] and style_check["adherence"] >= 4
                    and (contract_check is None or (contract_check["keep"] and contract_check["adherence"] >= 4))):
                return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                         "question": task["prompt"], "reasoning": [value["reasoning"]], "answer": value["answer"],
                         "source_context": source, "quotes": sample.get("quotes", []),
                         "judge": check, "style_check": style_check, "generation_style": style,
                         "repair_attempts": attempt, "reasoning_origin": "prompt_styled_generation",
                         "evidence_level": sample["evidence_level"],
                         **self._port.independent_cot_assessment(sample),
                         **reasoning_audit, **qa_metadata(sample, contract_check)}]
            feedback = {"correctness": check["reason"] if not accepted(check) else None,
                        "style": style_check["reason"] if not (style_check["keep"] and
                                  style_check["adherence"] >= 4) else None}
            if contract_check is not None:
                feedback["contract"] = (contract_check["reason"] if
                    not (contract_check["keep"] and contract_check["adherence"] >= 4) else None)
        return [{**self._port.reject(sample, "cot_generation_failed_after_repair"),
                 # A failed generation still needs the exact learner task and
                 # authenticated source to support a later same-task retry.
                 # Retain input only, never the unchecked answer or chain.
                 "question": deepcopy(task["prompt"]), "source_context": deepcopy(source),
                 "input_digest": digest({"question": task["prompt"], "source_context": source}),
                 "sft_parent_id": sample["id"],
                 "generation_style": style, "judge": check, "style_check": style_check,
                 "feedback": feedback, "repair_attempts": 1,
                 "reasoning_origin": "prompt_styled_generation", **qa_metadata(sample, contract_check)}]

    def generate_native_answer(self, sample, task, style, feedback, attempt):
        """Reuse a verified authored prefix; answer via provider-native continuation."""
        from lib.domain.authored_reasoning import authored_rationale_issue
        from lib.domain.training_surface import training_surface_issue

        # No previous answer is supplied to this reasoning-only phase.
        rationale_task = {key: value for key, value in task.items() if key != "reference_answer"}
        rationale = self._port.request([sample["id"], "cot_rationale_v1", attempt], "generation",
            "workflow.cot_rationale", {**rationale_task, "feedback": feedback},
            instruction="生成风格要求：\n" + style["instruction"], allow_reasoning_fallback=False)
        if issue := authored_rationale_issue(rationale):
            return None, {"reasoning_protocol": issue}
        issue = training_surface_issue(rationale["reasoning"], "",
            visible_messages=task["prompt"], contract=sample.get("qa_contract"),
            protocol_version=self.recipe.get("training_surface_protocol_version", 1),
            **knowledge_voice_options(self.recipe))
        if issue:
            return None, {"training_surface": issue}
        checked = self._port.check_adherence([sample["id"], "cot_prefix_check_v1", attempt],
            "workflow.cot_prefix_check", {"prompt": task["prompt"], "source": task["source"],
                "reasoning": rationale["reasoning"], "reasoning_trace": rationale["reasoning_trace"],
                **({"source_context_pack": task["source_context_pack"]} if "source_context_pack" in task else {})})
        if not checked["keep"] or checked["adherence"] < 4:
            return None, {"prefix_review": checked["reason"]}
        # The dispatch/schema/audit trace never enter the reasoning channel.
        answer_task = {key: value for key, value in rationale_task.items()
                       if key not in {"generation_style", "reference_answer_status", "quotes"}}
        prefix_sha = hashlib.sha256(rationale["reasoning"].encode("utf-8")).hexdigest()
        retry_enabled = self.recipe.get("native_answer_retry_version") == 1
        answer_attempts = 2 if retry_enabled else 1
        empty_receipts = []
        answer = None
        base_key = [sample["id"], "cot_native_answer_v1", attempt]
        for answer_attempt in range(answer_attempts):
            key = base_key if not answer_attempt else [*base_key, "visible_retry_v1", answer_attempt]
            empty_key = ["native_answer_empty_v1", sample["id"], attempt, answer_attempt, prefix_sha]
            if retry_enabled and self._port.checkpoint_has(empty_key):
                # A later upstream failure or process exit must not purchase
                # the already-recorded empty response again on resume.
                saved = self._port.checkpoint(empty_key, lambda: None)
                expected = {"version": 1, "status": "empty_visible_output", "eligible": False,
                    "code": "model_visible_output_missing", "answer_attempt": answer_attempt + 1,
                    "prefix_sha256": prefix_sha, "answer_checkpoint_sha256": digest(["call", key])}
                if saved != expected:
                    raise ValueError("checkpoint_integrity_error")
                empty_receipts.append(deepcopy(saved))
                continue
            try:
                answer = self._port.request_native_answer(key, answer_task, catch_empty=retry_enabled,
                    allow_reasoning_fallback=False, reasoning_prefix=rationale["reasoning"],
                    capture_response_details=True,
                    # Only the supplemental visible-answer request has no
                    # nested network loop. The first request retains the
                    # existing transport retry policy.
                    **({"native_request_attempts": 1} if answer_attempt else {}))
                break
            except EmptyVisibleAnswer:
                receipt = {"version": 1, "status": "empty_visible_output", "eligible": False,
                    "code": "model_visible_output_missing", "answer_attempt": answer_attempt + 1,
                    "prefix_sha256": prefix_sha, "answer_checkpoint_sha256": digest(["call", key])}
                self._port.checkpoint(empty_key, lambda: receipt)
                empty_receipts.append(deepcopy(receipt))
                self._port.event("native_answer_empty", role="generation", **receipt)
        if answer is None:
            terminal = {"version": 1, "code": "model_visible_output_missing", "answer_attempts": answer_attempts,
                "prefix_sha256": prefix_sha, "empty_response_receipts": empty_receipts,
                "rationale_checkpoint": {"stage": self._port.current_stage,
                    "sha256": digest(["call", [sample["id"], "cot_rationale_v1", attempt]])},
                "prefix_review_checkpoint": {"stage": self._port.current_stage,
                    "sha256": digest(["call", [sample["id"], "cot_prefix_check_v1", attempt]])}}
            return None, {"native_answer_terminal": terminal}
        if not isinstance(answer, dict) or set(answer) != {"answer"} or text_issue(answer.get("answer")):
            raise ValueError("native_prefill_invalid_answer")
        details = getattr(answer, "response_details", {})
        receipt = details.get("reasoning_prefill", {})
        if receipt.get("prefix_sha256") != prefix_sha or receipt.get("transport") != "deepseek_chat_beta":
            raise ValueError("native_prefill_receipt_missing")
        return {**rationale, "answer": answer["answer"]}, {
            "reasoning_origin": "authored_prefix_native_answer",
            "reasoning_prefill": deepcopy(receipt), "prefix_review": checked,
            "answer_response_details": deepcopy(details),
            **({"native_answer_retry": {"version": 1, "answer_attempts": len(empty_receipts) + 1,
                "empty_response_receipts": empty_receipts}} if retry_enabled else {}),
            "reasoning_scope": "authored_prefix_not_complete_provider_internal_reasoning"}
