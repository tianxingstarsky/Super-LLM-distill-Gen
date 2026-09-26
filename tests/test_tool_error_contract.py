"""Recorded error aliases cannot silently become positive training data."""
import pytest

from lib.domain.workflow_quality import conversation_issue
from lib.infrastructure.training_workflow import Workflow


@pytest.mark.parametrize("flags, issue", [
    ({"is_error": True}, "unresolved_tool_error"),
    ({"isError": True}, "unresolved_tool_error"),
    ({"is_error": False}, None),
    ({"isError": False, "is_error": False}, None),
    ({"isError": True, "is_error": False}, "invalid_tool_error_flag"),
    ({"is_error": "false"}, "invalid_tool_error_flag"),
    ({"is_error": {}}, "invalid_tool_error_flag"),
])
def test_shared_conversation_contract(flags, issue):
    messages = [{"role": "user", "content": "Compute 2+2"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "a", "function": {"name": "calculator", "arguments": '{"expression":"2+2"}'}}]},
                {"role": "tool", "tool_call_id": "a", "content": "4", **flags},
                {"role": "assistant", "content": "4"}]
    assert conversation_issue(messages) == issue


@pytest.mark.parametrize("stage", ["sft", "multiturn"])
@pytest.mark.parametrize("flags", [{"is_error": True}, {"is_error": "false"},
                                    {"isError": False, "is_error": True}])
def test_training_stage_rejects_error_before_model_access(stage, flags):
    workflow = Workflow.__new__(Workflow)
    unit = {"id": "trace", "messages": [{"role": "tool", "content": "4", **flags}]}
    result = getattr(workflow, stage)(unit)
    assert result[0]["status"] == "quarantined"
    assert result[0]["reason"] == f"observed_tool_error_not_{stage}"
