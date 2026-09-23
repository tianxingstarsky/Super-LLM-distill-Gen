"""Composition root for the legacy human-review field editor."""
from __future__ import annotations

from typing import Any

from lib.application.review_edit_service import ReviewEditApplication
from lib.infrastructure.review_suggestion_driver import ChatReviewSuggestionDriver


def review_edit_application(client: Any) -> ReviewEditApplication:
    return ReviewEditApplication(ChatReviewSuggestionDriver(client))
