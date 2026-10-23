"""Legacy aggregate port. New use cases declare only their owning context port."""
from typing import Protocol
from lib.contexts.workflows.api import WorkflowRunPort
from lib.contexts.datasets.api import DatasetPort
from lib.contexts.review.api import AgentReviewPort


class WorkflowDriver(WorkflowRunPort, DatasetPort, AgentReviewPort, Protocol):
    """Compatibility only; do not add operations here."""
