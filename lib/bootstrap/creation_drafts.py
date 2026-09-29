"""Compose one creation draft store per workspace output directory."""
from pathlib import Path
from lib.application.creation_draft_service import CreationDraftApplication
from lib.infrastructure.creation_draft_file import CreationDraftFile


def creation_draft_application(output: Path) -> CreationDraftApplication:
    return CreationDraftApplication(CreationDraftFile(output))
