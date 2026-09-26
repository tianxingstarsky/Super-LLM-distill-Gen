"""Compose node model selection with the shared backend inventory service."""
from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
from lib.bootstrap.backends import backend_application


def workflow_node_models_application(root):
    return WorkflowNodeModelsApplication(backend_application(root))
