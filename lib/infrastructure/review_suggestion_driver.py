"""Existing OpenAI-compatible chat client adapter for review suggestions."""
from __future__ import annotations

from typing import Any

from lib.llm_client import chat_json


class ChatReviewSuggestionDriver:
    def __init__(self, client: Any):
        self._client = client

    def complete_json(self, messages: list[dict[str, str]], *, temperature: float) -> Any:
        return chat_json(self._client, messages, temperature=temperature)
