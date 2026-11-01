"""Project accepted records to training rows without changing scored answers."""
from copy import deepcopy
from lib.contexts.workflows.contracts import training_record, rlaif_reward_model_record
from lib.domain.reasoning_fields import sft_training_messages
from .training_control import projected_training_control


def preferred_training_record(target, row, preferences=None, *, reward_model=False,
                              sft_output_style=None):
    record = rlaif_reward_model_record(row) if reward_model else training_record(target, row)
    if target in {"sft", "multiturn", "cot"} and "control_text_config" in row:
        record = {**record, "control_text_config": deepcopy(row["control_text_config"]),
                  **({"control_text_node": row["control_text_node"]} if "control_text_node" in row else {})}
    style = (sft_output_style if sft_output_style is not None else
             preferences["values"]["cot_style"] if preferences else None)
    if target == "cot":
        question = row["question"]
        prompt = question if isinstance(question, list) else [{"role": "user", "content": question}]
        control = projected_training_control(row, [*prompt, {"role": "assistant"}])
        return {**record, **({"training_control": control} if control else {})}
    if target not in {"sft", "multiturn"}:
        return record
    messages = sft_training_messages(record["messages"], drop=style == "drop")
    control = projected_training_control(row, messages)
    return {**record, "messages": messages, **({"training_control": control} if control else {})}
