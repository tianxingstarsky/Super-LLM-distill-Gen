"""Apply TRL's official mathematical accuracy reward to formal answers only."""
from concurrent.futures import ThreadPoolExecutor
import math


REWARD_PROTOCOL = "trl_accuracy_math_verify/v1"


def verify_numeric_answers(answers, expected):
    """Delegate extraction/equivalence, preserving unknown verifier outcomes.

    TRL disables Math-Verify's nested process/alarm timeouts outside the main
    thread. This supported path avoids Windows spawn-handle failures. Inputs
    here are the already-separated final answers, never native reasoning.
    """
    from trl.rewards import accuracy_reward

    if len(answers) != len(expected):
        raise ValueError("grpo_reward_alignment_invalid")
    completions = [[{"role": "assistant", "content": answer}] for answer in answers]
    solutions = [r"\boxed{" + value + "}" for value in expected]
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="trl-math-reward") as worker:
        rewards = worker.submit(accuracy_reward, completions=completions, solution=solutions).result()
    if len(rewards) != len(answers) or any(type(value) not in (int, float)
            or not math.isfinite(value) or value not in (0, 1) for value in rewards):
        # Unparseable gold or unavailable verification cannot silently become
        # a wrong answer, a positive reward, or an unreported skipped example.
        raise ValueError("grpo_math_reward_unresolved")
    return list(map(float, rewards))
