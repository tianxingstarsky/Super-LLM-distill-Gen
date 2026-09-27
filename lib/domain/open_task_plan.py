"""Bounded validation for model-planned open-brief tasks."""
import unicodedata
from collections.abc import Container

from lib.domain.workflow_quality import text_issue

MAX_TASK_CHARS = 512


def task_identity(task: str) -> str:
    # Preserve case: variable names and identifiers can be case-sensitive.
    return " ".join(unicodedata.normalize("NFC", task).split())


def task_plan_issue(tasks, expected: int, seen: Container[str]) -> str | None:
    if not isinstance(tasks, list) or len(tasks) != expected:
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
