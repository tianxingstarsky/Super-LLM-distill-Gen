"""Cheap, task-specific outcome checks do not certify a reasoning trace."""
from typing import Protocol


class MathReferencePort(Protocol):
    def compare(self, expected: str, candidate: str) -> dict: ...


class VerifyReferenceAnswer:
    def __init__(self, mathematics: MathReferencePort):
        self.mathematics = mathematics

    def execute(self, reference, candidate):
        scope = {"version": 1, "scope": "numeric_expression_only", "reasoning_verified": False}
        if reference is None:
            return {**scope, "status": "not_applicable", "reason": "no_explicit_reference"}
        if (not isinstance(reference, dict) or set(reference) != {"version", "kind", "expected"}
                or type(reference.get("version")) is not int or reference["version"] != 1
                or reference.get("kind") != "numeric_expression"
                or not isinstance(reference.get("expected"), str)
                or not isinstance(candidate, str)
                or not all(0 < len(s.strip()) <= 4096 for s in (reference["expected"], candidate))):
            return {**scope, "status": "unresolved", "reason": "unsupported_reference_contract"}
        return {**scope, **self.mathematics.compare(reference["expected"], candidate)}
