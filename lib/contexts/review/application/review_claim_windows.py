"""Bounded judgments; complete candidate inputs and durable per-window calls."""
from typing import Protocol

from lib.contexts.models.contracts import ModelOutputLimitError
from ..domain.claim_windows import (
    plan_review_windows, split_review_window, assemble_review_windows, windowed_review_record,
)


class ClaimWindowPort(Protocol):
    def check_cancel(self) -> None: ...
    def review(self, request: dict) -> tuple[dict, dict]: ...
    def record_output_limit(self, request: dict) -> None: ...


class ReviewClaimWindows:
    def __init__(self, port: ClaimWindowPort):
        self.port = port

    def execute(self, request: dict) -> tuple[dict, dict]:
        completed = []

        def review(window):
            self.port.check_cancel()
            try:
                receipt, checkpoint = self.port.review(window)
            except ModelOutputLimitError:
                # Splitting removes no input text. An atomic code/formula
                # span that still exceeds output capacity remains unresolved.
                if len(window["review_window"]["unit_ids"]) < 2:
                    raise
                self.port.record_output_limit(window)
                for child in split_review_window(window):
                    review(child)
            else:
                completed.append({"review": receipt, "checkpoint": checkpoint})

        for window in plan_review_windows(request):
            review(window)
        receipt = assemble_review_windows(request, [item["review"] for item in completed])
        return receipt, windowed_review_record(receipt, completed)
