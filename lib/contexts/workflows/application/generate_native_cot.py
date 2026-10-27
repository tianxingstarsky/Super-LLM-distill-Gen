"""One provider thinking response owns both the rationale and the answer."""
from copy import deepcopy

from .generation_ports import CotGenerationPort
from ..domain.dialogue import without_source_reasoning, dialogue_digest as digest
from ..domain.generation import qa_metadata
from ..domain.authored_task_contract import compile_task_contract, project_task_context, task_surface_receipt
from ..domain.cot_review_policy import cot_review_plan, SAMPLED_REVIEW_INSTRUCTION
from ..domain.integrated_reflection import integrated_reflection, reflection_instruction
from ..domain.native_cot import native_instruction_leakage
from lib.domain.workflow_quality import accepted, text_issue
from lib.domain.training_surface import training_surface_issue
from lib.domain.knowledge_voice import knowledge_voice_options


class GenerateNativeCot:
    def __init__(self, port: CotGenerationPort, recipe: dict):
        self.port, self.recipe = port, recipe

    def execute(self, sample, style):
        messages = sample.get("messages", [])
        if not messages or messages[-1].get("role") not in {"user", "assistant", "tool"}:
            return [self.port.reject(sample, "cot_missing_dialogue")]
        previous = messages[-1] if messages[-1].get("role") == "assistant" else None
        prompt = without_source_reasoning(messages[:-1] if previous else messages)
        from ..domain.public_experience import public_experience_text, with_public_experience
        prompt = with_public_experience(prompt, public_experience_text(self.recipe))
        task = {"prompt": prompt, "source": deepcopy(sample.get("source_context"))}
        contract = compile_task_contract(task)
        task = project_task_context(task, contract)
        # Authenticate cached source packs through the existing adapter. A
        # passage-only request never reads or carries the private full paper.
        if isinstance(task.get("source"), dict):
            task["source"] = {"text": self.port.source_evidence(task["source"])}
        if previous:
            task["previous_candidate"] = deepcopy(previous)
        if sample.get("native_revision_feedback"):
            task["revision_feedback"] = deepcopy(sample["native_revision_feedback"])
        config = self.recipe["node_generation"]["cot"]
        guidance = style.get("instruction", "")
        if integrated_reflection(config.get("self_check")):
            guidance += "\n" + reflection_instruction(config["self_check"])
        response = self.port.request([sample["id"], "cot_native_joint_v1", digest(task)],
            "generation", "workflow.cot_native_joint", task, instruction=guidance,
            capture_response_details=True, allow_reasoning_fallback=False, native_request_attempts=1)
        # These fields are assembled by the transport, never parsed from LLM
        # JSON. Missing native reasoning cannot be replaced by visible prose.
        reasoning, answer = response.get("reasoning", ""), response.get("answer", "")
        details = getattr(response, "response_details", {})
        issues = []
        if text_issue(reasoning):
            issues.append("native_reasoning_missing_or_invalid")
        if text_issue(answer):
            issues.append("native_answer_missing_or_invalid")
        surface = training_surface_issue(reasoning, answer, visible_messages=prompt,
            contract=sample.get("qa_contract"), protocol_version=self.recipe.get("training_surface_protocol_version", 2),
            **knowledge_voice_options(self.recipe))
        if surface:
            issues.append(surface)
        leakage = native_instruction_leakage(reasoning, prompt)
        if leakage:
            issues.append("native_generation_instruction_leakage")
        delivery = task_surface_receipt({"prompt": prompt, "source": sample.get("source_context")},
            {"reasoning": reasoning, "answer": answer}, contract)
        if not delivery["observable_passed"]:
            issues.append("cot_task_delivery_mismatch")
        plan = cot_review_plan(config, sample) or {
            "version": 1, "selected": False, "semantic_status": "not_assessed",
            "coverage": "local_checks_only", "selection": "no_independent_review_requested"}
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
                "history_status": "unchanged_not_assessed" if any(m.get("role") == "assistant" for m in prompt) else "no_assistant_history"},
            **qa_metadata(sample)}
        if issues:
            row.update(self.port.reject(sample, issues[0]))
            plan["semantic_status"] = "not_assessed"
        elif plan["selected"]:
            check = self.port.assess([sample["id"], "native_cot_review_v1", digest([task, reasoning, answer])],
                {k: v for k, v in task.items() if k not in {"previous_candidate", "revision_feedback"}},
                {"reasoning": reasoning, "answer": answer}, prompt_id="workflow.rationale_check",
                allow_reasoning_fallback=False, instruction=SAMPLED_REVIEW_INSTRUCTION)
            row["judge"] = check
            plan["semantic_status"] = "model_assessed" if accepted(check) else "not_accepted"
            if not accepted(check):
                row.update(self.port.reject(sample, "cot_native_review_rejected"))
        return [row]
