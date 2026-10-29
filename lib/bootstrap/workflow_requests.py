"""Compose workflow checkpointed requests without changing legacy injection hooks."""
from lib.contexts.workflows.application.request_node_model import RequestNodeModel
from lib.contexts.workflows.infrastructure.model_request_adapter import CheckpointedModelRequest


def workflow_request_adapter(runtime, hooks) -> CheckpointedModelRequest:
    """Bind checkpoint storage and legacy runtime hooks to one node request port."""
    return CheckpointedModelRequest(runtime, hooks)


def workflow_model_request(runtime, hooks) -> RequestNodeModel:
    """Build a resumable node request; provider retry is a separate model use case."""
    return RequestNodeModel(workflow_request_adapter(runtime, hooks))
