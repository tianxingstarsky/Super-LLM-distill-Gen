"""Composition root for human review of recorded Agent trajectories."""
from pathlib import Path

from lib.contexts.review.api import AgentReviewApplication
from lib.contexts.review.infrastructure.agent_driver import FilesystemAgentReviewAdapter


def agent_review_application(root: Path, output: Path) -> AgentReviewApplication:
    return AgentReviewApplication(FilesystemAgentReviewAdapter(root, output))
