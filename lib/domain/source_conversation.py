"""Quality rules for normalized source conversations, without storage or UI."""
from lib.domain.agent_trajectory import ReplayUnavailable, validate_tool_snapshots
from lib.domain.workflow_quality import canonical, conversation_issue, text_issue, tool_error_flag

MAX_SOURCE_CONTEXT_CHARS = 80000


def source_conversation_issue(sample: dict, targets) -> str | None:
    """Preserve recorded failures for Agent assessment, never bypass structure checks."""
    message_content = canonical(sample["messages"])
    oversized = len(message_content) > MAX_SOURCE_CONTEXT_CHARS
    issue = "context_exceeds_auto_limit" if oversized else conversation_issue(sample["messages"])
    if issue == "unresolved_tool_error" and "agent" in targets:
        # Keep observed failed tool steps for the separate negative
        # sidecar, but still require a structurally complete trace.
        cleaned = [{k: v for k, v in message.items() if k not in {"isError", "is_error"}}
                   for message in sample["messages"]]
        issue = next((tool_error_flag(message)[1] for message in sample["messages"]
                      if tool_error_flag(message)[1]), None) or conversation_issue(cleaned)
    if not oversized:
        message_metadata_issue = text_issue(message_content)
        if message_metadata_issue in {"potential_secret", "potential_personal_data"}:
            issue = message_metadata_issue
    tools_issue = text_issue(canonical(sample.get("tools", [])))
    if tools_issue in {"potential_secret", "potential_personal_data"}:
        issue = tools_issue
    tool_snapshots = sample.get("tool_snapshots")
    if tool_snapshots is not None:
        try:
            validate_tool_snapshots(tool_snapshots)
        except ReplayUnavailable as error:
            issue = str(error)
        else:
            snapshot_issue = text_issue(canonical(tool_snapshots))
            if snapshot_issue in {"potential_secret", "potential_personal_data"}:
                issue = snapshot_issue
    if sample.get("images"):
        issue = "multimodal_requires_dedicated_pipeline"
    return issue
