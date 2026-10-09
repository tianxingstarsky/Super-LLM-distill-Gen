"""A user's named prompt templates, independent from current workflow drafts."""
from __future__ import annotations

from typing import Protocol

from lib.domain.prompt_library import (
    validate_prompt_payload, validate_prompt_revision, validate_prompt_scope,
    validate_prompt_template_id, validate_prompt_template_name,
)


class PromptLibraryPort(Protocol):
    def list_templates(self, scope: str) -> list[dict]: ...
    def get_template(self, identifier: str, scope: str) -> dict: ...
    def save_template(self, scope: str, name: str, payload: dict) -> dict: ...
    def update_template(self, identifier: str, scope: str, name: str,
                        payload: dict, expected_revision: int) -> dict: ...


class PromptLibraryApplication:
    def __init__(self, port: PromptLibraryPort):
        self._port = port

    def list_templates(self, scope: str) -> list[dict]:
        return self._port.list_templates(validate_prompt_scope(scope))

    def get_template(self, identifier: str, scope: str) -> dict:
        return self._port.get_template(validate_prompt_template_id(identifier),
                                       validate_prompt_scope(scope))

    def save_template(self, scope: str, name: str, payload: dict) -> dict:
        return self._port.save_template(validate_prompt_scope(scope),
                                        validate_prompt_template_name(name),
                                        validate_prompt_payload(scope, payload))

    def update_template(self, identifier: str, scope: str, name: str,
                        payload: dict, expected_revision: int) -> dict:
        return self._port.update_template(
            validate_prompt_template_id(identifier), validate_prompt_scope(scope),
            validate_prompt_template_name(name), validate_prompt_payload(scope, payload),
            validate_prompt_revision(expected_revision))
