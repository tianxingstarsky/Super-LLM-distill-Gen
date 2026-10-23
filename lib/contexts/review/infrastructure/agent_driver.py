"""Agent review adapter; the existing transactional review store owns decisions."""
from pathlib import Path


class FilesystemAgentReviewAdapter:
    def __init__(self, root: Path, output: Path):
        self.root = Path(root)
        self.output = Path(output)

    def agent_review_queue(self, run_id: str, *, kind: str, offset: int,
                           limit: int, decision: str | None) -> dict:
        from lib.contexts.review.infrastructure.agent_review_driver import FilesystemAgentReviewDriver

        return FilesystemAgentReviewDriver(self.output).queue(
            run_id, kind=kind, offset=offset, limit=limit, decision=decision)

    def agent_review_decide(self, run_id: str, candidate_id: str, **decision) -> dict:
        from lib.contexts.review.infrastructure.agent_review_driver import FilesystemAgentReviewDriver

        return FilesystemAgentReviewDriver(self.output).decide(run_id, candidate_id, **decision)
