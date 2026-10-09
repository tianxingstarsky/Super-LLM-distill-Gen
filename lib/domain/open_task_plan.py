"""Bounded validation for model-planned open-brief tasks."""
import unicodedata
from collections.abc import Container

from lib.domain.workflow_quality import text_issue

MAX_TASK_CHARS = 512
PLAN_STOP_REASONS = {"source_exhausted", "no_new_grounded_scenario"}


def task_identity(task: str) -> str:
    # Preserve case: variable names and identifiers can be case-sensitive.
    return " ".join(unicodedata.normalize("NFC", task).split())


def task_plan_issue(tasks, expected: int, seen: Container[str], *, allow_short: bool = False) -> str | None:
    if not isinstance(tasks, list) or (len(tasks) > expected if allow_short else len(tasks) != expected):
        return "wrong_task_count"
    identities = set()
    for task in tasks:
        issue = text_issue(task)
        if issue:
            return issue
        if len(task) > MAX_TASK_CHARS:
            return "task_too_long"
        identity = task_identity(task)
        if identity in identities or identity in seen:
            return "duplicate_normalized_task"
        identities.add(identity)
    return None


def validate_short_task_plan(value, expected: int, seen: Container[str]) -> dict:
    """A useful subset is valid; absence of useful scenarios is control flow."""
    if not isinstance(value, dict) or set(value) - {"tasks", "exhausted", "stop_reason", "reason"}:
        raise ValueError("invalid_task_plan_response")
    issue = task_plan_issue(value.get("tasks"), expected, seen, allow_short=True)
    if issue:
        raise ValueError("invalid_task_plan_" + issue)
    exhausted = value.get("exhausted", False)
    stop = value.get("stop_reason")
    reason = value.get("reason", "")
    if (type(exhausted) is not bool or (stop is not None and not isinstance(stop, str))
            or (exhausted and stop not in PLAN_STOP_REASONS | {None})
            or (not exhausted and stop not in (None, ""))
            or not isinstance(reason, str) or len(reason) > 2000 or "\x00" in reason):
        raise ValueError("invalid_task_plan_stop_reason")
    declared = exhausted
    # An empty successful window must not turn into an endless planner loop.
    exhausted = exhausted or not value["tasks"]
    return {"tasks": list(value["tasks"]), "exhausted": exhausted,
            "stop_reason": (stop or "no_new_grounded_scenario") if exhausted else None,
            "reason": reason, "exhaustion_source": "planner_declared" if declared else
            "empty_window" if exhausted else None}
