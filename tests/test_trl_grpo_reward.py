"""The official reward must avoid Windows nested timeout workers and unknowns."""
import sys
import threading
from types import SimpleNamespace

import pytest

from lib.contexts.training.infrastructure.trl_reward import verify_numeric_answers


def test_official_reward_runs_in_supported_worker_context_without_reasoning(monkeypatch):
    def accuracy_reward(*, completions, solution):
        assert threading.current_thread() is not threading.main_thread()
        assert completions == [[{"role": "assistant", "content": r"Answer: \boxed{42}"}]]
        assert solution == [r"\boxed{42}"]
        return [1.0]
    monkeypatch.setitem(sys.modules, "trl.rewards", SimpleNamespace(accuracy_reward=accuracy_reward))
    assert verify_numeric_answers([r"Answer: \boxed{42}"], ["42"]) == [1.0]


def test_unavailable_gold_verification_cannot_become_a_wrong_or_passed_sample(monkeypatch):
    monkeypatch.setitem(sys.modules, "trl.rewards", SimpleNamespace(accuracy_reward=lambda **kwargs: [None]))
    with pytest.raises(ValueError, match="unresolved"):
        verify_numeric_answers(["answer"], ["42"])
