"""Pure budgets, held-out exact tasks and outcome metrics for local GRPO."""
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import math
import random


TASK_VERSION = "exact-numeric-v1"
ANSWER_RULE = r"Put your final numerical answer in \boxed{}."
TASK_FAMILIES = ("rational_expression", "trip_average_speed", "set_counting",
                 "divisibility_counting", "inventory_balance")


@dataclass(frozen=True)
class GRPOValidationPlan:
    model: str
    output: str
    task_source: str = "synthetic"
    parent_run: str = ""
    seed: int = 1729
    train_size: int = 32
    evaluation_size: int = 24
    epochs: float = 1.0
    num_iterations: int = 1
    num_generations: int = 4
    max_prompt_length: int = 512
    max_completion_length: int = 32768
    batch_size: int = 1
    gradient_accumulation: int = 4
    learning_rate: float = 5e-6
    lora_rank: int = 8
    temperature: float = 0.9
    beta: float = 0.0

    def __post_init__(self):
        if not isinstance(self.parent_run, str):
            raise ValueError("grpo_invalid_parent_run")
        if self.task_source not in ("synthetic", "gsm8k"):
            raise ValueError("grpo_invalid_task_source")
        for name in ("model", "output"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("grpo_path_required:" + name)
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("grpo_invalid_seed")
        for name in ("train_size", "evaluation_size", "num_generations",
                     "max_prompt_length", "max_completion_length", "batch_size",
                     "gradient_accumulation", "lora_rank"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError("grpo_invalid_limit:" + name)
        if not _finite_number(self.epochs) or not 0 < self.epochs <= 1:
            raise ValueError("grpo_requires_at_most_one_pass")
        if type(self.num_iterations) is not int or self.num_iterations != 1:
            raise ValueError("grpo_requires_one_iteration")
        if self.num_generations < 2:
            raise ValueError("grpo_requires_group_comparison")
        if self.batch_size * self.gradient_accumulation % self.num_generations:
            raise ValueError("grpo_batch_not_divisible_by_generations")
        if self.num_generations % self.batch_size:
            raise ValueError("grpo_generation_batch_not_divisible_by_device_batch")
        for name, upper in (("learning_rate", 0.001), ("temperature", 2.0)):
            value = getattr(self, name)
            if not _finite_number(value) or not 0 < value <= upper:
                raise ValueError("grpo_invalid_" + name)
        if not _finite_number(self.beta) or self.beta < 0:
            raise ValueError("grpo_invalid_beta")

    def to_dict(self):
        configuration = asdict(self)
        if not self.parent_run:
            configuration.pop("parent_run")
        return configuration


def validate_capacity_resume(parent_configuration: dict, child_plan):
    """A checkpoint continuation may increase capacity, never restart its budget.

    Return the normalized parent plan, including defaults absent in older
    manifests. Checkpoint identity and progress remain infrastructure concerns.
    """
    parent_plan = GRPOValidationPlan(**parent_configuration)
    if not child_plan.parent_run.strip():
        raise ValueError("grpo_capacity_resume_parent_required")
    if child_plan.max_completion_length <= parent_plan.max_completion_length:
        raise ValueError("grpo_capacity_resume_requires_increased_capacity")
    allowed = {"output", "parent_run", "max_completion_length"}
    changed = [name for name, value in parent_plan.to_dict().items()
               if name not in allowed and getattr(child_plan, name) != value]
    if changed:
        raise ValueError("grpo_capacity_resume_configuration_changed:" + ",".join(changed))
    return parent_plan


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class VerifiableTask:
    task_id: str
    family: str
    prompt: str
    expected_answer: str
    split: str

    def to_dict(self):
        """The target is audit metadata; only prompt is a model input."""
        return asdict(self)


def _answer_text(value):
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else str(value)


def _rational_expression(rng):
    a, c, e, g = (rng.randint(2, 53) for _ in range(4))
    b, d, f, h = (rng.randint(2, 29) for _ in range(4))
    question = f"Calculate the exact value of (({a}/{b}) - ({c}/{d})) / (({e}/{f}) + ({g}/{h}))."
    answer = (Fraction(a, b) - Fraction(c, d)) / (Fraction(e, f) + Fraction(g, h))
    return question, answer


def _trip_average_speed(rng):
    speed_kmh, first_seconds = rng.randint(13, 91), rng.randint(43, 257)
    speed_ms, second_minutes, rest_seconds = rng.randint(1, 6), rng.randint(2, 9), rng.randint(7, 67)
    question = (
        f"A traveler moves at {speed_kmh} km/h for {first_seconds} seconds, rests for "
        f"{rest_seconds} seconds, then moves at {speed_ms} m/s for {second_minutes} minutes. "
        "What is the average speed in m/s over the entire elapsed time, including the rest?"
    )
    distance = Fraction(speed_kmh * 5 * first_seconds, 18) + speed_ms * second_minutes * 60
    return question, distance / (first_seconds + rest_seconds + second_minutes * 60)


def _set_counting(rng):
    a_only, b_only, c_only = (rng.randint(9, 83) for _ in range(3))
    ab_only, ac_only, bc_only = (rng.randint(3, 31) for _ in range(3))
    abc, none = rng.randint(2, 17), rng.randint(6, 37)
    a = a_only + ab_only + ac_only + abc
    b = b_only + ab_only + bc_only + abc
    c = c_only + ac_only + bc_only + abc
    total = a_only + b_only + c_only + ab_only + ac_only + bc_only + abc + none
    question = (
        f"In a survey of {total} people, {a} speak language A, {b} speak B, and {c} speak C. "
        f"There are {ab_only + abc} who speak both A and B, {ac_only + abc} both A and C, "
        f"and {bc_only + abc} both B and C; these pair counts include the {abc} who speak all three. "
        "How many people speak exactly one of the three languages?"
    )
    return question, Fraction(a_only + b_only + c_only)


def _divisibility_counting(rng):
    lower = rng.randint(37, 563)
    upper = lower + rng.randint(213, 947)
    a, b, excluded = rng.sample(range(3, 24), 3)
    question = (
        f"How many integers n in the inclusive interval [{lower}, {upper}] are divisible "
        f"by {a} or {b} (or both), but are not divisible by {excluded}?"
    )

    def multiples(divisor):
        return upper // divisor - (lower - 1) // divisor

    answer = (multiples(a) + multiples(b) - multiples(math.lcm(a, b))
              - multiples(math.lcm(a, excluded)) - multiples(math.lcm(b, excluded))
              + multiples(math.lcm(a, b, excluded)))
    return question, Fraction(answer)


def _inventory_balance(rng):
    initial, added = rng.randint(41, 239), rng.randint(13, 97)
    first_den, second_den = rng.randint(3, 13), rng.randint(3, 13)
    first_num, second_num = rng.randint(1, first_den - 1), rng.randint(1, second_den - 1)
    question = (
        f"A tank initially contains {initial} liters. First, {first_num}/{first_den} of its "
        f"contents are removed. Next, {added} liters are added. Finally, {second_num}/{second_den} "
        "of the contents then in the tank are removed. How many liters remain?"
    )
    answer = (initial * (1 - Fraction(first_num, first_den)) + added) * (1 - Fraction(second_num, second_den))
    return question, answer


_BUILDERS = (_rational_expression, _trip_average_speed, _set_counting,
             _divisibility_counting, _inventory_balance)


def build_verifiable_tasks(seed=1729, train_size=32, evaluation_size=24):
    """Freeze both splits before evaluation; generation never receives targets."""
    if type(seed) is not int or seed < 0:
        raise ValueError("grpo_invalid_seed")
    if any(type(size) is not int or size < 1 for size in (train_size, evaluation_size)):
        raise ValueError("grpo_invalid_task_count")
    rng = random.Random(seed)
    seen = set()
    splits = []
    for split, size in (("train", train_size), ("evaluation", evaluation_size)):
        tasks = []
        for index in range(size):
            family_index = index % len(_BUILDERS)
            while True:
                question, answer = _BUILDERS[family_index](rng)
                if question not in seen:
                    break
            seen.add(question)
            prompt = question + "\n\n" + ANSWER_RULE
            identity = hashlib.sha256((TASK_VERSION + "\n" + question).encode("utf-8")).hexdigest()[:20]
            tasks.append(VerifiableTask(identity, TASK_FAMILIES[family_index], prompt,
                                        _answer_text(answer), split))
        splits.append(tuple(tasks))
    return tuple(splits)


def summarize_rewards(rewards):
    """Report an empty set as unknown rather than a successful evaluation."""
    values = tuple(rewards)
    if any(type(value) not in (int, float) or value not in (0, 1) for value in values):
        raise ValueError("grpo_invalid_correctness_reward")
    correct = sum(value == 1 for value in values)
    return {"count": len(values), "correct": correct,
            "accuracy": correct / len(values) if values else None}
