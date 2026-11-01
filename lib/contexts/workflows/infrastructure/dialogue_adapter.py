"""Checkpointed model requests and authenticated source memory for dialogue use cases."""
from __future__ import annotations
from copy import deepcopy
from ..domain.dialogue import DialogueSource


class CheckpointedDialogueAdapter:
    def __init__(self, runtime, *, model_stage=None):
        self._runtime = runtime
        self._model_stage = model_stage

    def source_evidence(self, source):
        from lib.infrastructure.workflow_source_context import pack_evidence_text
        return pack_evidence_text(source, self._runtime.path)

    def prepare_source(self, unit, style):
        runtime = self._runtime
        provenance = {"source_id": unit["source_id"], "source_name": unit.get("source_name"),
                      "location": unit.get("location"), "source_location": unit.get("source_location"),
                      "kind": unit["kind"]}
        if runtime.recipe.get("dialogue_context_scope_version") == 1:
            provenance.update(synthetic=unit.get("synthetic") is True,
                              human_provided=bool(unit.get("human_provided")))
        source_context = {"provenance": provenance, "task": unit.get("text"),
                          "brief": runtime.recipe.get("brief", ""), "tools": unit.get("tools", [])}
        from ..domain.dialogue_context import project_answer_context
        answer_scope = project_answer_context({}, unit,
            version=runtime.recipe.get("dialogue_context_scope_version")).get("answer_context_scope")
        if answer_scope is not None:
            source_context["answer_context_scope"] = answer_scope
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

    def prompt_text(self, prompt_id):
        return self._runtime.prompt_text(prompt_id, **({"stage": self._model_stage} if self._model_stage else {}))

    def generation_style(self, *args, **kwargs):
        return self._runtime.generation_style(*args, **kwargs)

    def reject(self, *args, **kwargs):
        return self._runtime.rejected(*args, **kwargs)

    def assess(self, *args, **kwargs):
        return self._runtime.judge_answer(*args, **self._node_options(kwargs))

    def next_dialogue_step(self, *args, **kwargs):
        return self._runtime.next_dialogue_step(*args, **kwargs)

    def request(self, *args, **kwargs):
        return self._runtime.ask(*args, **self._node_options(kwargs))

    def continue_native_feedback(self, key, session, assistant, *, findings):
        from lib.bootstrap.node_generation import native_feedback_repairer
        return native_feedback_repairer(self._runtime).execute(key, session, assistant, findings=findings)

    def _node_options(self, options):
        """Keep the authenticated dialogue node while a repair owns the stage."""
        if self._model_stage:
            options.setdefault("model_stage", self._model_stage)
            options.setdefault("prompt_stage", self._model_stage)
        return options

    def check_contract(self, *args, **kwargs):
        return self._runtime.qa_contract_check(*args, **self._node_options(kwargs))

    def check_style(self, *args, **kwargs):
        return self._runtime.style_check(*args, **self._node_options(kwargs))

    def separated_questions(self, *args, **kwargs):
        return self._runtime.separated_questions(*args, **kwargs)
