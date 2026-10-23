"""Project accepted records to training rows without changing scored answers."""
from lib.contexts.workflows.contracts import training_record, rlaif_reward_model_record
from lib.domain.reasoning_fields import sft_training_messages


def preferred_training_record(target, row, preferences=None, *, reward_model=False,
                              sft_output_style=None):
    record = rlaif_reward_model_record(row) if reward_model else training_record(target, row)
    style = (sft_output_style if sft_output_style is not None else
             preferences["values"]["cot_style"] if preferences else None)
    if target not in {"sft", "multiturn"}:
        return record
    return {**record, "messages": sft_training_messages(record["messages"], drop=style == "drop")}
