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
import json
from .check_reasoning import CheckReasoning
from ..domain.generation import EmptyVisibleAnswer
from ..domain.reasoning_investigation import investigation_feedback
from ..domain.presentation_admission import style_admission
from ..domain.integrated_reflection import (
    integrated_reflection, reflection_instruction, reflection_receipt, answer_reflection_instruction,
    trailing_reflection_enabled,
)
from lib.domain.workflow_reasoning_route import cot_updates_sft, cot_with_sft_context
from lib.domain.knowledge_voice import knowledge_voice_options
from ..domain.cot_finalization import finalization_receipt
from ..domain.cot_review_policy import cot_review_plan, known_original_rejection, SAMPLED_REVIEW_INSTRUCTION


class GenerateCot:
    def __init__(self, port: CotGenerationPort, recipe: dict, *, reference_verifier=None):
        self._port = port
        self.recipe = recipe
        self._reference_verifier = reference_verifier

    def _checklist(self):
        return self.recipe.get("node_generation", {}).get("cot", {}).get("self_check")

    def _trace_transport_enabled(self):
        return type(self.recipe.get("authored_trace_transport_version")) is int and self.recipe["authored_trace_transport_version"] == 1

    def _integrated_reflection(self):
        return integrated_reflection(self._checklist())

    def _author(self, key, prompt_id, task, instruction, **options):
        from ..domain.authored_task_contract import task_contract_enabled, compile_task_contract, project_task_context
        from ..domain.cot_style_transfer import style_edit_base
        if task_contract_enabled(self.recipe) or style_edit_base(self.recipe, task) is not None:
            # Source tools and direct requests receive the same limited view.
            # The persisted source and the actual learner history stay intact.
            task = project_task_context(task, compile_task_contract(task))
        lookup = self.recipe.get("node_generation", {}).get("cot", {}).get("source_lookup")
        if lookup is not None and self._integrated_reflection():
            from .generate_with_sources import GenerateWithSources
            from ..domain.authored_submission import strict_submission_enabled
            from ..domain.cot_style_transfer import style_edit_base
            return GenerateWithSources(self._port, lookup, strict_submission=strict_submission_enabled(self.recipe),
                local_edit=self.recipe.get("authored_local_edit_version") == 1,
                edit_base=style_edit_base(self.recipe, task)).execute(key, prompt_id, task, instruction)
        return self._port.request(key, "generation", prompt_id, task, instruction=instruction, **options), None

    def _draft_for_finalization(self, sample, task, style):
        """One checkpointed draft, without an answer to rationalize backwards."""
        from ..domain.authored_trace_transport import prepare_authored_response
        from lib.domain.authored_reasoning import authored_rationale_issue
        draft_task = {key: value for key, value in task.items() if key != "reference_answer"}
        instruction = "生成风格要求：\n" + style["instruction"] + self._checklist_instruction(phase="prefix")
        draft, source_audit = self._author([sample["id"], "cot_finalization_draft_v1"],
            "workflow.cot_rationale", draft_task, instruction=instruction,
            allow_reasoning_fallback=False, structured_response_attempts=1)
        audit = finalization_receipt(draft)
        if source_audit:
            audit["generation_sources"] = source_audit
            if source_audit["status"] != "completed":
                return None, audit, source_audit["issue"]
        prepared, transport = prepare_authored_response(draft, answer_required=False)
        audit["response_transport"] = transport
        issue = ("authored_response_schema_invalid" if transport["errors"] else
                 transport.get("content_issue") or authored_rationale_issue(prepared))
        # The draft is not certified by its trace. Semantic defects are the
        # finalizer's work; invalid transport must not buy a second request.
        return (None if issue else prepared), audit, issue

    def _protocol_rejection(self, sample, task, style, failure, attempt):
        """A wire failure cannot justify reauthoring or certify a candidate."""
        return [{**self._port.reject(sample, "cot_response_protocol_invalid"),
                 "question": deepcopy(task["prompt"]), "source_context": deepcopy(task.get("source")),
                 "sft_parent_id": sample["id"], "generation_style": deepcopy(style),
                 "reasoning": [failure.get("reasoning", "")], "answer": failure.get("answer", ""),
                 "feedback": deepcopy(failure), "repair_attempts": attempt,
                 "reasoning_origin": "prompt_styled_generation", **qa_metadata(sample)}]

    def _checklist_instruction(self, *, phase="final"):
        if not self._checklist():
            return ""
        if self._integrated_reflection():
            if trailing_reflection_enabled(self.recipe):
                return ""  # The request adapter adds the frozen rules after actual dialogue.
            return reflection_instruction(self._checklist(), phase=phase)
        rules = self._checklist()["rules"]
        if self._checklist().get("evidence_policy") in (1, 2):
            from ..domain.review_source_inventory import evidence_rules
            rules = evidence_rules(rules)
        if self._checklist()["version"] in (5, 6):
            from ..domain.reasoning_environment import compile_requirements
            rules = [r for r in compile_requirements(rules, phase) if r["active"]]
        supplement = ("\n本任务还核验全部历史助手训练目标、图表的指标/基线/单位、用户选段与教师全文的归属、指定互译的实际语义作用。"
            "原文措辞可能不够精确：比较变化时对照图表轴与数值，明确百分点、相对增幅或分数点。"
            "不要把未在用户选段出现的细节说成该段内容；使用额外知识时明确其归属。"
            "关键中英文对应需真正解释本题条件或关系，不能只堆英文标签；不规定步骤、次序、长度。"
            if self._checklist().get("evidence_policy") == 2 else "")
        return ("\n本次冻结的逐项自检规则如下。写出任务所需的实际工作，完成后另行核验；"
                "不要把规则、检查表或审计话术写进正文，不套固定步骤：\n"
                + json.dumps(rules, ensure_ascii=False) + supplement)

    def _review_key(self, sample, label, attempt, task, candidate):
        if self._checklist() and self._checklist()["version"] in (5, 6):
            return [sample["id"], label, "bound_review_v5", digest([task, candidate, self._checklist()])]
        return [sample["id"], label, attempt]

    def _unresolved_investigation(self, sample, task, style, audit, body):
        return [{**self._port.reject(sample, "reasoning_agent_incomplete"),
                 "question": deepcopy(task["prompt"]), "source_context": deepcopy(task.get("source")),
                 "reasoning": [body.get("reasoning", "")], "answer": body.get("answer", ""),
                 "generation_style": style, "reasoning_checklist": deepcopy(audit), **qa_metadata(sample)}]

    def execute(self, sample):
        rows = self._port.extract_cot(sample)
        if cot_updates_sft(self.recipe):
            return [cot_with_sft_context(sample, row) for row in rows]
        return rows

    def extract(self, sample):
        from ..domain.imported_reasoning import imported_reasoning_metadata, imported_turn_reasoning_origin
        style = self._port.generation_style("cot", sample["id"])
        rows = self._extract(sample, style)
        preserved = style is None and sample.get("conversation_reasoning_scope") != "input_only"
        for row in rows:
            metadata = imported_reasoning_metadata(sample, messages_preserved=preserved)
            # A CoT record extracts only the last assistant's rationale. Earlier
            # reconstructed turns cannot relabel an original final rationale.
            if "reasoning_origin" in metadata:
                metadata.pop("reasoning_origin")
                origin = imported_turn_reasoning_origin(sample, len(sample.get("messages", [])) - 1)
                if origin is not None and "reasoning" in row:
                    metadata["reasoning_origin"] = origin
            row.update(metadata)
        return rows

    def _extract(self, sample, style):
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
        if self.recipe.get("node_generation", {}).get("cot", {}).get("answer_mode") == "native_joint":
            from .generate_native_cot import GenerateNativeCot
            return GenerateNativeCot(self._port, self.recipe).execute(sample, style)
        transfer = None
        if self.recipe.get("node_generation", {}).get("cot", {}).get("answer_mode") == "faithful_restyle":
            from ..domain.cot_style_transfer import style_transfer_input, style_transfer_receipt
            try:
                transfer = style_transfer_input(sample)
            except ValueError as error:
                return [{**self._port.reject(sample, str(error)), "source_context": deepcopy(sample)}]
            if (self.recipe.get("node_generation", {}).get("cot", {}).get("review_policy")
                    and (issue := known_original_rejection(sample))):
                row = {**self._port.reject(sample, issue), "question": deepcopy(sample["messages"][:-1]),
                       "source_context": deepcopy(sample.get("source_context")),
                       "reasoning": [transfer["original"]["reasoning"]], "answer": transfer["original"]["answer"],
                       "original_review": deepcopy(sample.get("judge"))}
                row["style_transfer"] = {**style_transfer_receipt(transfer, row), "semantic_status": "not_accepted"}
                return [row]
            if self.recipe.get("cot_style_transfer_version") == 2:
                source = sample.get("source_context")
                reference = source.get("answer_reference") if isinstance(source, dict) else None
                if reference is not None:
                    if self._reference_verifier is None:
                        raise RuntimeError("cot_reference_verifier_not_composed")
                    receipt = self._port.checkpoint([sample["id"], "style_answer_reference_v1",
                        digest([reference, transfer["original"]["answer"]])],
                        lambda: self._reference_verifier.execute(reference, transfer["original"]["answer"]))
                    transfer["answer_reference_check"] = receipt
                    if receipt["status"] != "matched":
                        row = {**self._port.reject(sample, "cot_original_answer_reference_" + receipt["status"]),
                            "question": deepcopy(sample["messages"][:-1]),
                            "source_context": deepcopy(source), "reasoning": [transfer["original"]["reasoning"]],
                            "answer": transfer["original"]["answer"]}
                        row["style_transfer"] = {**style_transfer_receipt(transfer, row), "semantic_status": "not_accepted"}
                        return [row]
        rows = self._generate_styled(sample, style, transfer)
        if transfer:
            for row in rows:
                row["style_transfer"] = style_transfer_receipt(transfer, row)
                row["style_transfer"]["semantic_status"] = (
                    row.get("cot_review_policy", {}).get("semantic_status", "model_assessed")
                    if row.get("status") == "eligible" else "not_accepted")
                from ..domain.cot_style_transfer import style_edit_base
                if style_edit_base(self.recipe, {"style_transfer": transfer}) is not None:
                    row["style_transfer"]["editing_protocol"] = "literal_replacements_v2"
        return rows

    def _generate_styled(self, sample, style, transfer=None):
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
        if self._integrated_reflection() or self._checklist() and self._checklist().get("evidence_policy") in (1, 2):
            from ..domain.review_source_inventory import source_inventory
            # Coverage is available before drafting, not discovered only after
            # buying an unsupported manuscript-wide claim.
            task["source_inventory"] = source_inventory(task)
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
        if transfer:
            task["style_transfer"] = deepcopy(transfer)
            task.pop("reference_answer", None)
            task.pop("reference_answer_status", None)
        from ..domain.cot_style_transfer import style_edit_base
        patch_restyle = style_edit_base(self.recipe, task) is not None
        if self._integrated_reflection():
            # Style already travels in the system instruction, not twice.
            task.pop("generation_style", None)
        protocol = self.recipe.get("authored_reasoning_protocol_version") == 1
        native_prefill = self.recipe.get("node_generation", {}).get("cot", {}).get("answer_mode") == "native_prefill"
        finalize_draft = self.recipe.get("node_generation", {}).get("cot", {}).get("answer_mode") == "draft_then_finalize"
        if native_prefill:
            # Capability checks happen before spending on the rationale phase.
            self._port.validate_native_prefill()
            if any(message.get("role") == "tool" or message.get("tool_calls") or message.get("function_call")
                   for message in task["prompt"]):
                raise ValueError("model_reasoning_prefill_tools_unsupported")
        instruction = "生成风格要求：\n" + style["instruction"]
        instruction += self._checklist_instruction()
        if transfer:
            from ..domain.cot_style_transfer import STYLE_TRANSFER_INSTRUCTION_V1
            instruction += "\n" + STYLE_TRANSFER_INSTRUCTION_V1
        if task.get("reference_answer_status") == "unassessed_not_supplied":
            instruction += "\n上游只有结构合格，尚未事实评审，本次不提供其答案。独立依据真实问题和原始知识生成答案与解释，不推测上游答案，不把未评审结果当事实。资料不足时如实保留边界，不以换题或添造事实补救。"
        if protocol and not native_prefill and not transfer:
            from lib.domain.authored_reasoning import AUTHORED_REASONING_PROTOCOL_INSTRUCTION, authored_reasoning_issue
            protocol_instruction = AUTHORED_REASONING_PROTOCOL_INSTRUCTION
            if self.recipe.get("reasoning_shape_version") == 1:
                from lib.prompts.workflow_reasoning import task_adaptive_reasoning_template
                protocol_instruction = task_adaptive_reasoning_template(protocol_instruction)
            instruction += "\n" + protocol_instruction
        if self._trace_transport_enabled() and not native_prefill and self.recipe.get("authored_submission_version") != 1:
            from ..domain.authored_trace_transport import authored_transport_instruction
            instruction += authored_transport_instruction(answer_required=not transfer)
        from lib.domain.authored_reasoning import authored_reasoning_issue
        feedback, check, style_check, contract_check = None, None, None, None
        reasoning_audit, value, source_audit = {}, None, None
        draft_audit = None
        if finalize_draft:
            draft, draft_audit, draft_issue = self._draft_for_finalization(sample, task, style)
            if draft_issue:
                return self._protocol_rejection(sample, task, style, {
                    "reasoning_protocol": draft_issue, "raw_response": draft_audit["draft"],
                    "cot_finalization": draft_audit}, 0)
            # Draft content remains private editorial context. The actual
            # user/assistant/tool dialogue and source evidence stay intact.
            feedback = {"draft_to_finalize": draft}
            task.pop("reference_answer", None)
        # A protocol/content failure retains its draft. It is not permission
        # to buy another full rationale plus another native thinking pass.
        for attempt in range(1 if self._integrated_reflection() or finalize_draft or transfer else 2):
            if self._checklist() and self._checklist()["version"] == 6:
                # These grades belong to one draft. A later draft may stop at
                # the checklist before receiving a quality/style assessment.
                check, style_check, contract_check = None, None, None
            prefill_audit = {}
            if native_prefill:
                value, prefill_audit = self._port.generate_native_answer(sample, task, style, feedback, attempt)
                if value is None:
                    feedback = prefill_audit
                    if prefill_audit.get("protocol_terminal"):
                        return self._protocol_rejection(sample, task, style, prefill_audit, attempt)
                    if prefill_audit.get("checklist_unresolved"):
                        return self._unresolved_investigation(sample, task, style,
                            prefill_audit["reasoning_checklist"], {"reasoning": prefill_audit["reasoning"]})
                    if ((self.recipe.get("native_answer_retry_version") == 1 or self._integrated_reflection())
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
                            "repair_attempts": attempt,
                            **({"reasoning": [prefill_audit.get("reasoning", "")], "answer": ""}
                               if self._integrated_reflection() else {}), **qa_metadata(sample)}]
                    continue
            else:
                value, source_audit = self._author([sample["id"],
                             "cot_style_transfer_v2" if patch_restyle else
                             "cot_style_transfer_v1" if transfer else
                             "cot_finalization_submit_v1" if finalize_draft else "cot_generate_v1", attempt],
                             "workflow.cot_rationale" if transfer else "workflow.cot_generate",
                             {**task, "feedback": investigation_feedback(feedback)},
                              instruction=instruction,
                              **({"structured_response_attempts": 1} if self._integrated_reflection() or finalize_draft or transfer else {}))
                if source_audit and source_audit["status"] != "completed":
                    return self._protocol_rejection(sample, task, style, {
                        "reasoning_protocol": source_audit["issue"], "generation_sources": source_audit,
                        "raw_response": deepcopy(value),
                        **({"cot_finalization": draft_audit} if draft_audit else {})}, attempt)
            transport_audit = None
            if transfer:
                from ..domain.authored_trace_transport import prepare_authored_response
                raw_value = value
                value, transport_audit = prepare_authored_response(value, answer_required=False)
                if transport_audit["errors"] or transport_audit.get("content_issue"):
                    return self._protocol_rejection(sample, task, style, {
                        "reasoning_protocol": "authored_response_schema_invalid",
                        "response_transport": transport_audit, "raw_response": deepcopy(raw_value),
                        "reasoning": raw_value.get("reasoning", "") if isinstance(raw_value, dict) else "",
                        "answer": transfer["original"]["answer"]}, attempt)
                # The author never serializes or regenerates the fixed answer.
                value["answer"] = transfer["original"]["answer"]
            if self._trace_transport_enabled() and not native_prefill and not transfer:
                from ..domain.authored_trace_transport import prepare_authored_response
                raw_value = value
                value, transport_audit = prepare_authored_response(value, answer_required=True)
                if transport_audit["errors"]:
                    return self._protocol_rejection(sample, task, style, {
                        "reasoning_protocol": "authored_response_schema_invalid", "protocol_terminal": True,
                        "response_transport": transport_audit, "raw_response": deepcopy(raw_value),
                        **({"generation_sources": source_audit} if source_audit else {}),
                        **({"cot_finalization": draft_audit} if draft_audit else {}),
                        **({key: raw_value[key] for key in ("reasoning", "answer")
                            if isinstance(raw_value.get(key), str)} if isinstance(raw_value, dict) else {})}, attempt)
                if transport_audit.get("content_issue"):
                    feedback = {"reasoning_protocol": transport_audit["content_issue"],
                                "response_transport": transport_audit}
                    continue
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
            if draft_audit:
                reasoning_audit["cot_finalization"] = {**draft_audit,
                    **finalization_receipt(draft_audit["draft"], value)}
            if transport_audit:
                reasoning_audit["response_transport"] = transport_audit
            if source_audit:
                reasoning_audit["generation_sources"] = source_audit
            if surface_protocol:
                reasoning_audit["training_surface_protocol_version"] = self.recipe["training_surface_protocol_version"]
            if self._checklist() and not self._integrated_reflection():
                review_task = task
                if self._checklist().get("evidence_policy") in (1, 2) and prefill_audit.get("prefix_reasoning_checklist"):
                    review_task = {**task, "verified_prefix_review": prefill_audit["prefix_reasoning_checklist"]}
                checklist = CheckReasoning(self._port, self._checklist()).execute(
                    self._review_key(sample, "cot_checklist_final_v1", attempt, task, value), review_task, value, phase="final")
                reasoning_audit["reasoning_checklist"] = checklist
                if not checklist["passed"]:
                    if checklist["version"] in (3, 4, 5, 6) and checklist["issue"] != "reasoning_agent_found_gap":
                        return self._unresolved_investigation(sample, task, style, checklist, value)
                    feedback = {"reasoning_checklist": checklist,
                        "repair": "按具体缺口修订正文中的真实工作，再重新逐项核对。保留完整推理；不能只改自检结论。"}
                    continue
            if native_prefill:
                reasoning_audit.update(prefill_audit)
            if self._integrated_reflection():
                reasoning_audit.update(generation_check="structure_and_generation_reflection",
                    reasoning_reflection=reflection_receipt(self._checklist(), value["reasoning"], value["answer"]))
            from ..domain.authored_task_contract import task_contract_enabled, compile_task_contract, task_surface_receipt
            if task_contract_enabled(self.recipe):
                delivery = task_surface_receipt(task, value, compile_task_contract(task))
                reasoning_audit["task_delivery"] = delivery
                if not delivery["observable_passed"]:
                    return [{**self._port.reject(sample, "cot_task_delivery_mismatch"),
                             "question": deepcopy(task["prompt"]), "source_context": deepcopy(source),
                             "reasoning": [value["reasoning"]], "answer": value["answer"],
                             "generation_style": deepcopy(style), "feedback": delivery,
                             "repair_attempts": attempt, "reasoning_origin": "prompt_styled_generation",
                             **reasoning_audit, **qa_metadata(sample)}]
            if transfer:
                review_plan = cot_review_plan(self.recipe["node_generation"]["cot"], sample)
                if review_plan is not None:
                    reasoning_audit["cot_review_policy"] = review_plan
                if review_plan is not None and not review_plan["selected"]:
                    # Explicit sampling admits a locally valid candidate, not a
                    # semantically verified one. Keep its complete text and audit.
                    return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                             "question": task["prompt"], "reasoning": [value["reasoning"]], "answer": value["answer"],
                             "source_context": source, "quotes": sample.get("quotes", []), "generation_style": style,
                             "repair_attempts": attempt, "reasoning_origin": "prompt_styled_generation",
                             "evidence_level": sample["evidence_level"], **reasoning_audit, **qa_metadata(sample)}]
                review_task = task
                if patch_restyle:
                    from ..domain.authored_task_contract import project_task_context
                    review_task = project_task_context(task, compile_task_contract(task))
                light_review = review_plan is not None and review_plan["mode"] == "sampled"
                check = self._port.assess(
                    [sample["id"], "cot_style_transfer_review_v2" if patch_restyle else "cot_style_transfer_review_v1",
                     digest([review_task, value, review_plan] if review_plan is not None else [review_task, value])],
                    {**review_task, "style_transfer_review":
                        "Compare the original and replacement against actual evidence and user history. "
                        "An unchanged answer is not proof of correctness. Reject unsupported original answers, "
                        "lost necessary derivations, changed conditions, units, scope or invented checks. "
                        "Assess the entire candidate; stylistic elegance is not evidence."},
                    value, prompt_id="workflow.rationale_check", allow_reasoning_fallback=False,
                    instruction=SAMPLED_REVIEW_INSTRUCTION if light_review else
                                "对照 style_transfer.original、完整真实对话和来源，核验改写前后的事实与必要推导。"
                                "原答案不一定正确；答案未变不构成通过理由。原答案错误、遗漏关键条件、单位/范围变化、"
                                "删去必要论证或虚构检查均应拒绝。风格优美不等于正确。")
                reasoning_audit["style_transfer_review"] = check
                if review_plan is not None:
                    review_plan["semantic_status"] = "model_assessed" if accepted(check) else "not_accepted"
                if not accepted(check):
                    return [{**self._port.reject(sample, "cot_style_transfer_review_rejected"),
                             "question": deepcopy(task["prompt"]), "source_context": deepcopy(source),
                             "reasoning": [value["reasoning"]], "answer": value["answer"],
                             "judge": check, **reasoning_audit, **qa_metadata(sample)}]
            if self._integrated_reflection() or self.recipe.get("review_repair") is not None:
                return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                         "question": task["prompt"], "reasoning": [value["reasoning"]], "answer": value["answer"],
                         "source_context": source, "quotes": sample.get("quotes", []), "generation_style": style,
                         "repair_attempts": attempt, "reasoning_origin": "prompt_styled_generation",
                         "evidence_level": sample["evidence_level"],
                         **({"judge": check} if transfer else {}), **reasoning_audit, **qa_metadata(sample)}]
            check = check if transfer else self._port.assess(self._review_key(sample, "cot_generated_check_v1", attempt, task, value),
                                      {key: item for key, item in task.items() if key != "generation_style"},
                                      {"reasoning": value["reasoning"], "answer": value["answer"],
                                       **({"reasoning_trace": value["reasoning_trace"]} if protocol else {})},
                                      prompt_id="workflow.rationale_check", allow_reasoning_fallback=False)
            style_check = self._port.check_style(self._review_key(sample, "cot_style_v1", attempt, task, value), style,
                                          value["reasoning"], value["answer"])
            contract_check = (self._port.check_contract(self._review_key(sample, "cot_generated_directed_contract", attempt, task, value),
                sample, [*task["prompt"], {"role": "assistant", "content": value["answer"],
                    "reasoning_content": value["reasoning"]}], prompt_id="workflow.cot_directed_check")
                if sample.get("qa_contract") else None)
            presentation = style_admission(self._checklist(), style_check)
            if (accepted(check) and presentation["allowed"]
                    and (contract_check is None or (contract_check["keep"] and contract_check["adherence"] >= 4))):
                return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                         "question": task["prompt"], "reasoning": [value["reasoning"]], "answer": value["answer"],
                         "source_context": source, "quotes": sample.get("quotes", []),
                         "judge": check, "style_check": style_check, "generation_style": style,
                         **({"presentation_advisories": presentation["advisories"]} if presentation["advisories"] else {}),
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
                  # New protocols also retain the rejected body for inspection;
                  # quarantine does not make that body eligible for training.
                 "question": deepcopy(task["prompt"]), "source_context": deepcopy(source),
                 "input_digest": digest({"question": task["prompt"], "source_context": source}),
                 "sft_parent_id": sample["id"],
                 "generation_style": style, "judge": check, "style_check": style_check,
                 "feedback": feedback, "repair_attempts": attempt,
                 **({"generation_sources": source_audit} if source_audit else {}),
                 **({"cot_finalization": draft_audit} if draft_audit else {}),
                 "reasoning_origin": "prompt_styled_generation",
                 **({"reasoning": [value.get("reasoning", "")], "answer": value.get("answer", ""),
                     "reasoning_checklist": reasoning_audit.get("reasoning_checklist")}
                    if (transfer or self._checklist() and self._checklist()["version"] in (5, 6, 7)) and isinstance(value, dict) else {}),
                 **({"reasoning": [prefill_audit["reasoning"]], "answer": "",
                     "reasoning_checklist": prefill_audit.get("reasoning_checklist")}
                    if self._checklist() and (self._checklist().get("evidence_policy") == 2 or self._integrated_reflection())
                    and not isinstance(value, dict) and prefill_audit.get("reasoning") else {}),
                 **qa_metadata(sample, contract_check)}]

    def generate_native_answer(self, sample, task, style, feedback, attempt):
        """Reuse a validated authored prefix; answer via provider-native continuation."""
        from lib.domain.authored_reasoning import authored_rationale_issue
        from lib.domain.training_surface import training_surface_issue

        # No previous answer is supplied to this reasoning-only phase.
        rationale_task = {key: value for key, value in task.items() if key != "reference_answer"}
        instruction = "生成风格要求：\n" + style["instruction"] + self._checklist_instruction(phase="prefix")
        if self._trace_transport_enabled() and self.recipe.get("authored_submission_version") != 1:
            from ..domain.authored_trace_transport import authored_transport_instruction
            instruction += authored_transport_instruction(answer_required=False)
        rationale, source_audit = self._author([sample["id"], "cot_rationale_v1", attempt],
            "workflow.cot_rationale", {**rationale_task, "feedback": investigation_feedback(feedback)},
            instruction=instruction, allow_reasoning_fallback=False,
            **({"structured_response_attempts": 1} if self._integrated_reflection() else {}))
        if source_audit and source_audit["status"] != "completed":
            return None, {"reasoning_protocol": source_audit["issue"], "protocol_terminal": True,
                          "generation_sources": source_audit, "raw_response": deepcopy(rationale)}
        transport_audit = None
        if self._trace_transport_enabled():
            from ..domain.authored_trace_transport import prepare_authored_response
            raw_rationale = rationale
            rationale, transport_audit = prepare_authored_response(rationale, answer_required=False)
            if transport_audit["errors"]:
                return None, {"reasoning_protocol": "authored_response_schema_invalid", "protocol_terminal": True,
                    "response_transport": transport_audit, "raw_response": deepcopy(raw_rationale),
                    **({"generation_sources": source_audit} if source_audit else {}),
                    **({"reasoning": raw_rationale["reasoning"]}
                       if isinstance(raw_rationale, dict) and isinstance(raw_rationale.get("reasoning"), str) else {})}
        rejected_body = ({"reasoning": rationale["reasoning"],
                          "reasoning_trace": deepcopy(rationale.get("reasoning_trace"))}
            if (self._trace_transport_enabled() or self._integrated_reflection() or self._checklist() and self._checklist().get("evidence_policy") == 2)
            and isinstance(rationale, dict) and isinstance(rationale.get("reasoning"), str) else {})
        if transport_audit:
            rejected_body["response_transport"] = transport_audit
            if transport_audit.get("content_issue"):
                return None, {"reasoning_protocol": transport_audit["content_issue"], **rejected_body}
        if source_audit:
            rejected_body["generation_sources"] = source_audit
        if issue := authored_rationale_issue(rationale):
            # Rejection still stops the native answer. Keep the actual failed
            # draft for review, including a malformed trace, never as a pass.
            return None, {"reasoning_protocol": issue, **rejected_body}
        issue = training_surface_issue(rationale["reasoning"], "",
            visible_messages=task["prompt"], contract=sample.get("qa_contract"),
            protocol_version=self.recipe.get("training_surface_protocol_version", 1),
            **knowledge_voice_options(self.recipe))
        if issue:
            return None, {"training_surface": issue, **rejected_body}
        from ..domain.authored_task_contract import task_contract_enabled, compile_task_contract, task_surface_receipt
        if task_contract_enabled(self.recipe):
            delivery = task_surface_receipt(task, rationale, compile_task_contract(task), phase="prefix")
            if not delivery["observable_passed"]:
                return None, {"task_delivery": delivery, **rejected_body}
        prefix_checklist = None
        if self._checklist() and not self._integrated_reflection():
            prefix_checklist = CheckReasoning(self._port, self._checklist()).execute(
                self._review_key(sample, "cot_checklist_prefix_v1", attempt, rationale_task, rationale), rationale_task, rationale, phase="prefix")
            if not prefix_checklist["passed"]:
                return None, {"reasoning_checklist": prefix_checklist,
                    **({"response_transport": transport_audit} if transport_audit else {}),
                    **({"reasoning": rationale["reasoning"]} if self._checklist().get("evidence_policy") == 2 else {}),
                    **({"checklist_unresolved": True, "reasoning": rationale["reasoning"]}
                       if prefix_checklist["version"] in (3, 4, 5, 6) and prefix_checklist["issue"] != "reasoning_agent_found_gap" else {})}
        prefix_review = {"prompt": task["prompt"], "source": task["source"],
            "reasoning": rationale["reasoning"], "reasoning_trace": rationale["reasoning_trace"],
            **({"source_context_pack": task["source_context_pack"]} if "source_context_pack" in task else {})}
        prefix_options = {}
        if (type(self.recipe.get("native_prefix_review_style_version")) is int
                and self.recipe["native_prefix_review_style_version"] == 1):
            # The reviewer must judge the same frozen requirements as the
            # author. Never recover them from the candidate's own claims.
            prefix_review["generation_style"] = deepcopy(style)
            prefix_options["instruction"] = "本次推理前缀的冻结风格与深度要求：\n" + style["instruction"]
        checked = (None if self._integrated_reflection() else
            self._port.check_adherence([sample["id"], "cot_prefix_check_v1", attempt],
                "workflow.cot_prefix_check", prefix_review, **prefix_options))
        if checked is not None and (not checked["keep"] or checked["adherence"] < 4):
            return None, {"prefix_review": checked["reason"],
                **({"response_transport": transport_audit} if transport_audit else {}),
                **({"reasoning": rationale["reasoning"]} if self._trace_transport_enabled()
                   or self._checklist() and self._checklist().get("evidence_policy") == 2 else {})}
        # The dispatch/schema/audit trace never enter the reasoning channel.
        answer_task = {key: value for key, value in rationale_task.items()
                       if key not in {"generation_style", "reference_answer_status", "quotes"}}
        prefix_sha = hashlib.sha256(rationale["reasoning"].encode("utf-8")).hexdigest()
        retry_enabled = self.recipe.get("native_answer_retry_version") == 1 or self._integrated_reflection()
        answer_attempts = 2 if retry_enabled and not self._integrated_reflection() else 1
        answer_guidance = (answer_reflection_instruction(self._checklist())
            if self._integrated_reflection() and not trailing_reflection_enabled(self.recipe) else "")
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
                    **({"instruction": answer_guidance} if answer_guidance else {}),
                    # Reflection never repurchases an already-completed empty
                    # continuation. Legacy recipes retain their retry policy.
                    **({"native_request_attempts": 1} if answer_attempt or self._integrated_reflection() else {}))
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
                **({"prefix_review_checkpoint": {"stage": self._port.current_stage,
                    "sha256": digest(["call", [sample["id"], "cot_prefix_check_v1", attempt]])}}
                    if checked is not None else {})}
            return None, {"native_answer_terminal": terminal,
                          **(rejected_body if self._integrated_reflection() else {})}
        if not isinstance(answer, dict) or set(answer) != {"answer"} or text_issue(answer.get("answer")):
            raise ValueError("native_prefill_invalid_answer")
        details = getattr(answer, "response_details", {})
        receipt = details.get("reasoning_prefill", {})
        if receipt.get("prefix_sha256") != prefix_sha or receipt.get("transport") != "deepseek_chat_beta":
            raise ValueError("native_prefill_receipt_missing")
        return {**rationale, "answer": answer["answer"]}, {
            **({"generation_sources": source_audit} if source_audit else {}),
            **({"response_transport": transport_audit} if transport_audit else {}),
            **({"prefix_reasoning_checklist": prefix_checklist} if prefix_checklist else {}),
            "reasoning_origin": "authored_prefix_native_answer",
            "reasoning_prefill": deepcopy(receipt),
            **({"prefix_review": checked} if checked is not None else {}),
            "answer_response_details": deepcopy(details),
            **({"native_answer_retry": {"version": 1, "answer_attempts": len(empty_receipts) + 1,
                "empty_response_receipts": empty_receipts}} if retry_enabled else {}),
            "reasoning_scope": "authored_prefix_not_complete_provider_internal_reasoning"}
