"""Compose persistent knowledge sources for the current local cache."""
from lib.application.knowledge_service import KnowledgeApplication
from lib.infrastructure.knowledge_driver import FilesystemKnowledgeDriver


def knowledge_application(workspace_id="default", root=None):
    return KnowledgeApplication(FilesystemKnowledgeDriver(workspace_id, root))
