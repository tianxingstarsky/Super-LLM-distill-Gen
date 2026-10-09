"""Compose the personal library for the current OS user or an explicit user key."""
from pathlib import Path

from lib.application.prompt_library_service import PromptLibraryApplication
from lib.infrastructure.prompt_library_sqlite import PromptLibrarySQLite


def prompt_library_application(*, root: Path | None = None,
                               user_key: str | None = None) -> PromptLibraryApplication:
    return PromptLibraryApplication(PromptLibrarySQLite(root=root, user_key=user_key))
