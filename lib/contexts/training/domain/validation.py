"""Contracts for one reproducible, single-pass local training comparison."""
from dataclasses import dataclass
import hashlib
import json


STAGES = ("baseline", "sft_train", "sft_eval", "cpt_sft_train", "cpt_sft_eval")


@dataclass(frozen=True)
class TrainingValidationPlan:
    model: str
    sft: str
    validation: str
    cpt: str
    evaluation: str
    output: str
    seed: int = 1729
    epochs: float = 1.0
    max_length: int = 8192
    cpt_block_size: int = 1024
    max_new_tokens: int = 32768
    batch_size: int = 1
    gradient_accumulation: int = 8
    learning_rate: float = 2e-5
    lora_rank: int = 16
    system_message: str = "You are a helpful assistant."

    def __post_init__(self):
        for field in ("model", "sft", "validation", "cpt", "evaluation", "output"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                raise ValueError("training_validation_path_required:" + field)
        if self.epochs != 1 or isinstance(self.epochs, bool):
            raise ValueError("training_validation_requires_one_pass")
        for field in ("max_length", "cpt_block_size", "max_new_tokens", "batch_size",
                      "gradient_accumulation", "lora_rank"):
            if type(getattr(self, field)) is not int or getattr(self, field) < 1:
                raise ValueError("training_validation_invalid_limit:" + field)
        if self.cpt_block_size > self.max_length:
            raise ValueError("training_validation_cpt_block_exceeds_context")
        if not 0 < self.learning_rate <= .001:
            raise ValueError("training_validation_invalid_learning_rate")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def assert_disjoint_questions(train, validation, evaluation):
    """Check task leakage, allowing an explicitly shared paper knowledge source."""
    groups = [set(" ".join(q.split()).casefold() for q in questions)
              for questions in (train, validation, evaluation)]
    if any(groups[a] & groups[b] for a, b in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("training_validation_question_leakage")


def validate_supervision(example, *, eos_token_id, trailing_whitespace_ids=()):
    """Accept only a complete target with a masked prompt and its end marker."""
    ids, labels = example["input_ids"], example["labels"]
    if not ids or len(ids) != len(labels) or len(ids) != len(example["attention_mask"]):
        raise ValueError("training_validation_invalid_token_alignment")
    supervised = [i for i, label in enumerate(labels) if label != -100]
    ends = [i for i in supervised if labels[i] == eos_token_id]
    if (not supervised or supervised[0] == 0 or not ends
            or any(token not in trailing_whitespace_ids for token in ids[ends[-1] + 1:])
            or any(labels[i] != ids[i] for i in supervised)
            or supervised != list(range(supervised[0], len(ids)))):
        raise ValueError("training_validation_incomplete_supervision")
    return {"input_tokens": len(ids), "prompt_tokens": supervised[0],
            "supervised_tokens": len(supervised), "eos_token_id": eos_token_id,
            "eos_position": ends[-1]}
