"""Keep explicit page changes separate from persisted human work sessions."""
from __future__ import annotations

from collections.abc import MutableMapping


HUMAN_WORKSPACE_ROUTE = "人工问答增强"


def select_console_page(state: MutableMapping, query: MutableMapping, page: str) -> dict:
    """Select a route and return only creation-draft changes that need saving.

    A human session id is a location, not a persistent page override. Leaving
    the studio clears its URL and one-shot handoff, while keeping all session
    selections, editor values and worker state. The creation entry then opens
    in automatic mode instead of restoring the studio's entry mode.
    """
    was_human = (state.get("nav") == HUMAN_WORKSPACE_ROUTE
                 or query.get("page") == HUMAN_WORKSPACE_ROUTE
                 or bool(query.get("human")))
    changes = {}
    if page != HUMAN_WORKSPACE_ROUTE:
        query.pop("human", None)
        state.pop("human-open-session", None)
        if was_human:
            workspace = state.get("ws", "default")
            changes = {f"workflow-creation-mode:{workspace}": "自动生成",
                       f"workflow-human-enabled:{workspace}": False}
            state.update(changes)
            draft_key = f"workflow-form-draft:{workspace}"
            draft = state.get(draft_key)
            if not isinstance(draft, dict):
                draft = {}
                state[draft_key] = draft
            draft.update(changes)
    if state.get("nav") != page:
        state["nav"] = page
    if query.get("page") != page:
        query["page"] = page
    return changes
