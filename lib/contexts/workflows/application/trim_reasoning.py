"""Trim explicit reasoning with immutable answers and bounded delivery projections."""
from __future__ import annotations
from copy import deepcopy
from .generation_ports import ReasoningDeliveryPort
from ..domain.dialogue import without_source_reasoning, dialogue_digest as digest
from lib.domain.workflow_quality import accepted, text_issue
from lib.domain.reasoning_trim import trim_prompt
from lib.domain.workflow_reasoning_route import cot_updates_sft
from lib.domain.knowledge_voice import knowledge_voice_options


class TrimReasoning:
    def __init__(self, port: ReasoningDeliveryPort, recipe: dict):
        self._port = port
        self.recipe = recipe

    def execute(self, item):
        """Transform only explicit SFT/CoT reasoning, retaining immutable answers."""
        target, sample = item["target"], item["sample"]
        row = deepcopy(sample)
        if row.get("status") != "eligible":
            return [{**row, "trim_target": target}]
        config = self.recipe["reasoning_trim"]
        instruction = self.recipe.get("reasoning_trim_prompt") or trim_prompt(config)
        fields = []
        linked_cot = target == "cot" and cot_updates_sft(self.recipe)
        if target == "sft" or linked_cot:
            for index, message in enumerate(row.get("messages", [])):
                if message.get("role") == "assistant" and message.get("reasoning_content"):
                    fields.append((f"messages.{index}.reasoning_content", message["reasoning_content"],
                                   without_source_reasoning(row["messages"][:index]), message["content"], index))
        elif target == "cot":
            reasoning = row.get("reasoning")
            if isinstance(reasoning, list):
                reasoning = "\n".join(reasoning)
            if reasoning:
                fields.append(("reasoning", reasoning, without_source_reasoning(row.get("question")), row["answer"], None))
        receipts = []
        for field, original, prompt, answer, index in fields:
            feedback, check, rules_check = None, None, None
            original_hash = digest(original)
            for attempt in range(2):
                key = [row["id"], "trim_v1", target, field, original_hash, attempt]
                context = {"prompt": prompt, "source": without_source_reasoning(row.get("source_context")),
                           "final_answer": answer, "original_reasoning": original}
                if row.get("qa_contract"):
                    context["qa_contract"] = deepcopy(row["qa_contract"])
                    context = self._port.prepare_source_request(context, row.get("source_context") or {}, row["qa_contract"])
                replacement = self._port.request(key, "generation", "workflow.trim",
                                       {**context, "feedback": feedback}, instruction=instruction)
                if (not isinstance(replacement, dict) or set(replacement) != {"reasoning"}
                        or text_issue(replacement.get("reasoning"))):
                    feedback = "仅返回有效的 reasoning 文本，不得包含或改写其他字段"
                    continue
                if (self.recipe.get("training_surface_protocol_version") in {1, 2}
                        and row.get("reasoning_origin") in {"prompt_styled_generation", "prompt_trim", "prompt_revision", "human_revision"}):
                    from lib.domain.training_surface import training_surface_issue
                    issue = training_surface_issue(replacement["reasoning"], answer,
                        visible_messages=prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}],
                        contract=row.get("qa_contract"),
                        protocol_version=self.recipe.get("training_surface_protocol_version", 1),
                        **knowledge_voice_options(self.recipe))
                    if issue:
                        feedback = {"training_surface": issue, "repair": "保留任务推导和事实，移除隐藏来源与制作过程自述。最终答案保持不变。"}
                        continue
                check = self._port.assess([*key, "meaning"], context,
                    {"replacement_reasoning": replacement["reasoning"], "final_answer": answer},
                    prompt_id="workflow.trim_check", instruction=instruction)
                rules_check = self._port.check_adherence([*key, "rules"], "workflow.trim_rules_check",
                    {"prompt": prompt, "original_reasoning": original,
                     "replacement_reasoning": replacement["reasoning"], "final_answer": answer},
                    instruction=instruction)
                contract_check = None
                if row.get("qa_contract"):
                    if target == "sft" or linked_cot:
                        contract_messages = deepcopy(row["messages"])
                        contract_messages[index]["reasoning_content"] = replacement["reasoning"]
                    else:
                        contract_messages = [*row["question"], {"role": "assistant", "content": answer,
                                             "reasoning_content": replacement["reasoning"]}]
                    contract_check = self._port.check_contract([*key, "qa_contract"], row, contract_messages,
                        prompt_id="workflow.trim_directed_check")
                if (accepted(check) and rules_check["keep"] and rules_check["adherence"] >= 4
                        and (contract_check is None or (contract_check["keep"] and contract_check["adherence"] >= 4))):
                    receipts.append({"field": field, "input_sha256": original_hash, "judge": check,
                                     "rules_check": rules_check, "repair_attempts": attempt})
                    if contract_check is not None:
                        row["qa_contract_check"] = contract_check
                    if target == "sft" or linked_cot:
                        row["messages"][index]["reasoning_content"] = replacement["reasoning"]
                    else:
                        row["reasoning"] = [replacement["reasoning"]]
                    break
                feedback = {"meaning": check["reason"] if not accepted(check) else None,
                            "rules": rules_check["reason"] if not (rules_check["keep"] and
                                      rules_check["adherence"] >= 4) else None}
                if contract_check is not None:
                    feedback["contract"] = (contract_check["reason"] if
                        not (contract_check["keep"] and contract_check["adherence"] >= 4) else None)
            else:
                return [{**self._port.reject(sample, "reasoning_trim_failed_after_repair"), "trim_target": target,
                         "reasoning_trim": {"version": 1, "template": config["template"], "status": "rejected",
                            "instruction_sha256": digest(instruction), "field": field,
                            "input_sha256": original_hash, "judge": check, "rules_check": rules_check,
                            "feedback": feedback, "repair_attempts": 1}}]
        if linked_cot:
            row["question"] = deepcopy(row["messages"][:-1])
            row["reasoning"] = [row["messages"][-1]["reasoning_content"]]
        if row.get("reasoning_protocol_version") == 1 and receipts:
            row["reasoning_trace_status"] = "superseded_by_trim"
        if "source_context" in row:
            row["source_context"] = without_source_reasoning(row["source_context"])
        row["reasoning_trim"] = {"version": 1, "template": config["template"],
                                  "status": "applied" if fields else "not_applicable",
                                  "instruction_sha256": digest(instruction), "fields": receipts}
        if receipts:
            from lib.contexts.review.contracts import preserve_provider_reasoning
            preserve_provider_reasoning(target, sample, row, origin="prompt_trim")
        return [{**row, "trim_target": target}]

    def apply(self, collections, *, targets=None):
        """Run optional trimming without materializing a dataset collection."""
        if not (self.recipe.get("reasoning_trim") or {}).get("enabled"):
            self._port.skip_trim_stage()
            return
        targets = ([target for target in ("sft", "cot") if target in self.recipe["targets"]]
                   if targets is None else list(targets))
        if not targets:
            raise ValueError("reasoning_trim_requires_reasoning_target")
        self._port.prepare_trim_stage()
        self._port.trim_collections(collections, targets)

    def finalize(self, collections, *, delivery_targets=None):
        """Finalize one reasoning result before exposing its selected formats."""
        selected = set(self.recipe["targets"] if delivery_targets is None else delivery_targets)
        if not cot_updates_sft(self.recipe):
            self._port.apply_reasoning_trim(collections)
            return
        if not selected.intersection({"sft", "cot"}):
            self._port.apply_reasoning_trim(collections, targets=("cot",))
            return
        self._port.apply_reasoning_trim(collections, targets=("cot",))
        if "sft" in selected:
            self._port.publish_finalized_sft(collections)
