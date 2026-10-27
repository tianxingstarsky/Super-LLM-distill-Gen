"""Continue the recorded author, without switching endpoint, account or model."""
from copy import deepcopy

from ..domain.reasoning_checklist import checklist_digest
from .reasoning_agent_adapter import CheckpointedReasoningAgent


def author_identity(client, binding):
    sdk = getattr(client, "client", None)
    return checklist_digest({"model": getattr(client, "model", type(client).__name__),
        "endpoint": str(getattr(sdk, "base_url", getattr(client, "base_url", "injected"))),
        "credential": str(getattr(sdk, "api_key", "injected")),
        "api_format": getattr(client, "api_format", "chat"),
        "reasoning": getattr(client, "reasoning", None), "binding": binding})


def author_session(client, binding, messages, tools, *, stage, role, max_tokens, temperature=0.2):
    return {"version": 1, "stage": stage, "role": role,
        "author": author_identity(client, binding),
        "messages": deepcopy(messages), "tools": deepcopy(tools),
        "request_options": {"max_tokens": max_tokens, "temperature": temperature}}


class AuthoredEditAdapter:
    def __init__(self, runtime):
        self.runtime = runtime

    def load_submission(self, key):
        def missing():
            raise ValueError("authored_edit_original_checkpoint_missing")
        record = self.runtime.checkpoint(["authored_submission_v1", key], missing)
        session = record.get("author_session")
        if not isinstance(session, dict) or session.get("version") != 1:
            raise ValueError("authored_edit_original_session_unavailable")
        # This entry edits CoT author output only; review and other node bindings
        # must never silently stand in for the actual author.
        if session["stage"] != "cot" or session["role"] != "generation":
            raise ValueError("authored_edit_original_role_unsupported")
        return deepcopy(record)

    def turn(self, key, session, messages, tools):
        runtime = self.runtime
        client = runtime.client("generation", stage="cot")
        binding = runtime.recipe.get("node_models", {}).get("cot", {}).get("generation", {})
        if author_identity(client, binding) != session["author"]:
            raise ValueError("authored_edit_original_model_changed")
        if messages[:len(session["messages"])] != session["messages"]:
            raise ValueError("authored_edit_original_prefix_changed")
        return CheckpointedReasoningAgent(runtime).prepared_tool_turn(
            [*key, session["author"]], "generation", messages, tools,
            strict=True, final=True, request_options=session["request_options"], capture_details=True)
