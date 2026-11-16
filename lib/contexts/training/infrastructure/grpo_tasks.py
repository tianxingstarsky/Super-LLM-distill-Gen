"""Load frozen exact tasks; optional GSM8K uses official held-out splits."""
from fractions import Fraction
import random

from ..domain.grpo import ANSWER_RULE, TASK_VERSION, build_verifiable_tasks
from ..domain.validation import assert_disjoint_questions


GSM8K_ID = "openai/gsm8k"
GSM8K_CONFIG = "main"
# Repository commit and MIT metadata verified against the Hugging Face API.
GSM8K_REVISION = "740312add88f781978c0658806c59bc2815b9866"


def _gsm_task(row, source_split, source_index, split):
    question, solution = row.get("question"), row.get("answer")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("grpo_gsm8k_invalid_question")
    if not isinstance(solution, str) or "####" not in solution:
        raise ValueError("grpo_gsm8k_final_answer_missing")
    target = solution.rsplit("####", 1)[1].strip().replace(",", "")
    try:
        exact = Fraction(target)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("grpo_gsm8k_invalid_final_answer") from error
    return {"task_id": f"gsm8k/{GSM8K_CONFIG}/{source_split}/{source_index}",
            "family": "gsm8k", "prompt": question.strip() + "\n\n" + ANSWER_RULE,
            "expected_answer": str(exact), "split": split,
            "source_split": source_split, "source_index": source_index}


def load_grpo_tasks(plan):
    """Choose by frozen seed and row index, never model answers or gold difficulty."""
    if plan.task_source == "synthetic":
        train, evaluation = build_verifiable_tasks(plan.seed, plan.train_size, plan.evaluation_size)
        return ([task.to_dict() for task in train], [task.to_dict() for task in evaluation],
                {"id": "synthetic", "revision": TASK_VERSION, "license": None,
                 "sampling": "deterministic_exact_task_generation", "seed": plan.seed})

    from datasets import load_dataset

    corpus = load_dataset(GSM8K_ID, GSM8K_CONFIG, revision=GSM8K_REVISION)
    selected = []
    for offset, (source_split, split, size) in enumerate((
            ("train", "train", plan.train_size), ("test", "evaluation", plan.evaluation_size))):
        records = corpus[source_split]
        if size > len(records):
            raise ValueError("grpo_gsm8k_sample_exceeds_split:" + source_split)
        indices = random.Random(plan.seed + offset).sample(range(len(records)), size)
        tasks = [_gsm_task(records[index], source_split, index, split) for index in indices]
        normalized = [" ".join(task["prompt"].split()).casefold() for task in tasks]
        if len(set(normalized)) != len(normalized):
            raise ValueError("grpo_gsm8k_duplicate_question:" + source_split)
        selected.append(tasks)
    train, evaluation = selected
    assert_disjoint_questions([task["prompt"] for task in train], [],
                              [task["prompt"] for task in evaluation])
    source = {"id": GSM8K_ID, "config": GSM8K_CONFIG, "revision": GSM8K_REVISION,
              "license": "MIT", "train_split": "train", "evaluation_split": "test",
              "sampling": "independent_seeded_random_without_replacement",
              "seed": plan.seed}
    return train, evaluation, source
