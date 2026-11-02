"""Generate a new answer for an authenticated fixed task, without a director."""
from copy import deepcopy
from typing import Protocol
from lib.domain.human_augmentation import human_generation_instruction

from ..domain.dialogue import dialogue_digest as digest
from ..domain.planned_sft_reanswer import (planned_sft_prompt, reanswer_receipt, selected_reanswer_ids,
                                         reanswer_options, authenticated_teacher_text, planned_sft_task_instruction)


class PlannedSftPort(Protocol):
    def parent_recipe(self, context): ...
    def generate(self, **kwargs): ...


class CreatePlannedSftReanswer:
    def __init__(self, port):
        self.port = port

    def execute(self, parent_run_id, candidate_ids, agent_experience=None, *, task_instructions=None):
        selected = [self.port.select(parent_run_id, identifier)
                    for identifier in selected_reanswer_ids(candidate_ids)]
        parent = self.port.parent_configuration(selected)
        return self.port.create(**reanswer_options(parent, selected, agent_experience,
            **({"task_instructions": task_instructions} if task_instructions is not None else {})))


class RegeneratePlannedSft:
    def __init__(self, port, recipe, run_id):
        self.port, self.recipe, self.run_id = port, recipe, run_id

    def execute(self, item):
        original, context = item["record"], item["checkpoint_context"]
        parent = self.port.parent_recipe(context)
        prompt = planned_sft_prompt(original, parent)
        evidence = context["evidence"]
        identity = digest([self.run_id, "sft_reanswer_v1", context["parent_run_id"], context["candidate_id"]])
        unit = {"id": identity, "source_id": original["source_id"],
                "kind": (original.get("source_context") or {}).get("kind", "document"),
                "qa_contract": deepcopy(original["qa_contract"]),
                "family_id": original.get("family_id"), "parent_id": original["id"]}
        instruction = ("遵循用户的实际问题和指导员指定的回答条件，不把历史助手说法当作事实。\n"
            + parent["qa_director"].get("answer_rules", "") + "\n"
            + human_generation_instruction(original["qa_contract"]))
        if task_instruction := planned_sft_task_instruction(self.recipe, context["candidate_id"]):
            instruction += "\n" + task_instruction
        row = self.port.generate(stage="sft", unit=unit, messages=prompt,
            style=deepcopy(original.get("generation_style")),
            source_text=authenticated_teacher_text(original, evidence),
            source_scope=(original.get("provided_source_context") or {}).get("scope", "selected_lines"),
            instruction=instruction)
        row["qa_contract"] = deepcopy(original["qa_contract"])
        row.update(family_id=original.get("family_id"), parent_id=original["id"],
                   source_name=original.get("source_name"), location=original.get("location"))
        row["sft_reanswer"] = reanswer_receipt(original, context, prompt, row, parent, self.recipe)
        return [row]
