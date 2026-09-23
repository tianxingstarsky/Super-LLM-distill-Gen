"""Composition root for generation settings administration."""
from __future__ import annotations

from pathlib import Path

from lib.application.generation_settings_service import GenerationSettingsApplication
from lib.infrastructure.generation_settings_file import FileGenerationSettingsDriver


def generation_settings_application(root: Path) -> GenerationSettingsApplication:
    return GenerationSettingsApplication(FileGenerationSettingsDriver(root))
