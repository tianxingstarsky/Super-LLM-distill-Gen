"""Use cases for reading and editing generation preferences."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import yaml

from lib.application.generation_settings_ports import GenerationSettingsPort
from lib.application.preference_service import preference_summary, update_preference_yaml


CATEGORIES = ("生成偏好", "思考风格", "语言规则")


@dataclass(frozen=True)
class SettingsSnapshot:
    category: str
    text: str
    document: dict[str, Any] | None
    parse_error: str | None = None


class GenerationSettingsApplication:
    def __init__(self, port: GenerationSettingsPort):
        self._port = port

    def categories(self) -> tuple[str, ...]:
        return CATEGORIES

    def load(self, category: str) -> SettingsSnapshot:
        if category not in CATEGORIES:
            raise ValueError("unknown_generation_settings_category")
        text = self._port.read(category)
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as error:
            return SettingsSnapshot(category, text, None, str(error))
        return SettingsSnapshot(category, text,
                                document if isinstance(document, dict) else None,
                                None if isinstance(document, dict) else "配置根节点必须是对象")

    def summary(self, snapshot: SettingsSnapshot) -> dict[str, Any]:
        if snapshot.category != "生成偏好":
            raise ValueError("summary_only_for_generation_preferences")
        return preference_summary(snapshot.text)

    def save_form(self, snapshot: SettingsSnapshot, *, default_share: float,
                  relative_tendencies: Mapping[str, float], templates_per_dim: int,
                  shuffle_per_batch: bool, correction_enabled: bool,
                  correction_threshold: float, correction_tagger: str, cot_style: str) -> str:
        if snapshot.category != "生成偏好":
            raise ValueError("form_only_for_generation_preferences")
        updated = update_preference_yaml(
            snapshot.text, default_share=default_share,
            relative_tendencies=relative_tendencies,
            templates_per_dim=templates_per_dim,
            shuffle_per_batch=shuffle_per_batch,
            correction_enabled=correction_enabled,
            correction_threshold=correction_threshold,
            correction_tagger=correction_tagger, cot_style=cot_style,
        )
        self._port.compare_and_swap(snapshot.category, snapshot.text, updated)
        return updated

    def save_raw(self, snapshot: SettingsSnapshot, replacement: str) -> None:
        try:
            parsed = yaml.safe_load(replacement)
        except yaml.YAMLError as error:
            raise ValueError("invalid_generation_settings_yaml") from error
        if not isinstance(parsed, dict):
            raise ValueError("配置根节点必须是对象")
        if snapshot.category == "生成偏好":
            preference_summary(replacement)
        self._port.compare_and_swap(snapshot.category, snapshot.text, replacement)
