"""Project one native SFT task without its first answer or private reasoning."""
from copy import deepcopy

from .planned_sft_reanswer import planned_sft_prompt, authenticated_teacher_text, reanswer_task_identity


def native_sft_optimization_input(record, recipe, evidence):
    messages = planned_sft_prompt(record, recipe)
    text = authenticated_teacher_text(record, evidence, recipe=recipe)
    unit = {"id": record["id"], "source_id": record["source_id"],
        "kind": (record.get("source_context") or {}).get("kind", "document"),
        "qa_contract": deepcopy(record["qa_contract"]), "family_id": record.get("family_id"),
        "parent_id": record["id"]}
    return {"messages": messages, "source_text": text,
        "source_scope": record["provided_source_context"]["scope"], "unit": unit,
        "task_sha256": reanswer_task_identity(messages, record["qa_contract"], evidence)}
