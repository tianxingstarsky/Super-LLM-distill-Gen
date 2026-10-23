"""Checkpointed model requests and authenticated source memory for dialogue use cases."""
from __future__ import annotations
from copy import deepcopy
from ..domain.dialogue import DialogueSource


class CheckpointedDialogueAdapter:
    def __init__(self, runtime):
        self._runtime = runtime

    def prepare_source(self, unit, style):
        runtime = self._runtime
        provenance = {"source_id": unit["source_id"], "source_name": unit.get("source_name"),
                      "location": unit.get("location"), "source_location": unit.get("source_location"),
                      "kind": unit["kind"]}
        source_context = {"provenance": provenance, "task": unit.get("text"),
                          "brief": runtime.recipe.get("brief", ""), "tools": unit.get("tools", [])}
        if style is not None:
            source_context["generation_style"] = deepcopy(style)
        pack_evidence = None
        selected_evidence_blocks = None
        if runtime.recipe.get("source_context_pack_version") == 1 and unit.get("source_context_pack"):
            from lib.infrastructure.workflow_source_context import pack_evidence_text
            source_context["source_context_pack"] = deepcopy(unit["source_context_pack"])
            pack_evidence = pack_evidence_text(unit, runtime.path)
        contract = unit.get("qa_contract")
        if contract is not None:
            source_context.update(qa_contract=contract,
                question_rules=runtime.recipe["qa_director"]["question_rules"],
                answer_rules=runtime.recipe["qa_director"]["answer_rules"],
                teacher_evidence_not_learner_context=True)
            from lib.infrastructure.source_memory_messages import directed_source_request, directed_evidence_text, directed_evidence_blocks
            source_context = directed_source_request(runtime.recipe, source_context, unit, contract)
            if source_context.get("source_line_scope") == "selected":
                source_context.pop("task", None)
                pack_evidence = directed_evidence_text(runtime.path, unit, contract)
                selected_evidence_blocks = directed_evidence_blocks(runtime.path, unit, contract)
        if runtime.recipe.get("source_memory_protocol_version") in {1, 2} and source_context.get("source_context_pack"):
            source_context.pop("task", None)
        return DialogueSource(source_context, pack_evidence, selected_evidence_blocks)

    def learner_prompt(self, contract):
        from lib.infrastructure.workflow_qa_director import learner_prompt
        return learner_prompt(contract)

    def generation_style(self, *args, **kwargs):
        return self._runtime.generation_style(*args, **kwargs)

    def reject(self, *args, **kwargs):
        return self._runtime.rejected(*args, **kwargs)

    def assess(self, *args, **kwargs):
        return self._runtime.judge_answer(*args, **kwargs)

    def next_dialogue_step(self, *args, **kwargs):
        return self._runtime.next_dialogue_step(*args, **kwargs)

    def request(self, *args, **kwargs):
        return self._runtime.ask(*args, **kwargs)

    def check_contract(self, *args, **kwargs):
        return self._runtime.qa_contract_check(*args, **kwargs)

    def check_style(self, *args, **kwargs):
        return self._runtime.style_check(*args, **kwargs)

    def separated_questions(self, *args, **kwargs):
        return self._runtime.separated_questions(*args, **kwargs)
