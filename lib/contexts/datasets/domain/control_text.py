"""Apply a frozen, optional literal-control mapping to a reasoning view.

Configuration is checked once at construction. Text is never interpreted as
regex or code; transformations use one scan of the original input, so inserted
markers and target wording cannot trigger another rule in that scan.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import re


SCHEMA = "shujian.control-text-adaptation/v1"


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _literal_pattern(text: str) -> str:
    # Preserve whole-word and decimal boundaries without constraining literal
    # marker punctuation. In particular, a configured 5 must not match 50/5.0.
    left = r"(?<!\w)" if text[0].isalnum() or text[0] == "_" else ""
    right = r"(?!\w)" if text[-1].isalnum() or text[-1] == "_" else ""
    decimal = r"(?!\.\d)" if text[-1].isdigit() else ""
    return left + re.escape(text) + right + decimal


class ControlTextAdapter:
    """Snapshot caller-owned literal rules; never change source messages.

    ``replacement=None`` retains a rule's marker. Empty ``matches`` supports
    marker-only adaptation. Disabling the configuration bypasses every text
    rule, including existing markers; it does not disable model thinking.
    """

    def __init__(self, configuration: Mapping):
        if (not isinstance(configuration, Mapping)
                or configuration.get("schema") != SCHEMA
                or type(configuration.get("enabled")) is not bool
                or set(configuration) - {"schema", "enabled", "rules"}):
            raise ValueError("invalid_control_text_configuration")
        self._configuration = deepcopy(dict(configuration))
        self._enabled = configuration["enabled"]
        self._rules: dict[str, dict] = {}
        self._tokens: dict[str, tuple[str, bool]] = {}
        self._pattern = None
        self._task_patterns: dict[str, re.Pattern] = {}
        # A saved disabled configuration may contain rules being edited. Its
        # switch must still stop adaptation without compiling those drafts.
        if not self._enabled:
            return
        rules = self._configuration.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError("invalid_control_text_rules")
        allowed = {"id", "enabled", "matches", "marker", "replacement", "field", "observed_value"}
        for rule in rules:
            if (not isinstance(rule, dict) or set(rule) - allowed
                    or not _nonempty_text(rule.get("id"))
                    or type(rule.get("enabled", True)) is not bool):
                raise ValueError("invalid_control_text_rule")
            if not rule.get("enabled", True):
                continue
            matches = rule.get("matches")
            marker, replacement = rule.get("marker"), rule.get("replacement")
            if (not isinstance(matches, list) or any(not _nonempty_text(item) for item in matches)
                    or not _nonempty_text(marker)
                    or replacement is not None and not _nonempty_text(replacement)
                    or "field" in rule and not _nonempty_text(rule["field"])):
                raise ValueError("invalid_control_text_rule")
            value = rule.get("observed_value")
            if (type(value) not in (str, int, float, bool, type(None))
                    or type(value) is float and not math.isfinite(value)):
                raise ValueError("invalid_control_text_observed_value")
            identity = rule["id"]
            if identity in self._rules:
                raise ValueError("control_text_rule_conflict")
            self._rules[identity] = rule
            for literal in dict.fromkeys(matches):
                if literal in self._tokens:
                    raise ValueError("control_text_rule_conflict")
                self._tokens[literal] = (identity, False)
            if marker in self._tokens:
                raise ValueError("control_text_rule_conflict")
            self._tokens[marker] = (identity, True)
        # Nested markers and marker/literal containment make a repeated read
        # ambiguous. Ordinary overlapping literals instead use longest-first.
        markers = [token for token, (_, is_marker) in self._tokens.items() if is_marker]
        for marker in markers:
            if any(token != marker and (marker in token or token in marker) for token in self._tokens):
                raise ValueError("control_text_rule_conflict")
        for rule in self._rules.values():
            replacement = rule.get("replacement")
            if replacement is not None and any(token in replacement for token in self._tokens):
                raise ValueError("control_text_replacement_cascade")
        if self._tokens:
            ordered = sorted(self._tokens, key=lambda token: (-len(token), token))
            self._pattern = re.compile("|".join(_literal_pattern(token) for token in ordered))
            self._task_patterns = {
                identity: re.compile("|".join(_literal_pattern(token) for token in ordered
                                             if self._tokens[token][0] == identity))
                for identity in self._rules
            }

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def configuration(self) -> dict:
        return deepcopy(self._configuration)

    def _transform(self, text: str, *, target: bool) -> tuple[str, dict, set[str]]:
        edits = []
        replacements = 0
        matched_rules: set[str] = set()
        if not self._enabled or self._pattern is None:
            return text, {"source_control_edits": edits, "control_text_replacements": 0}, matched_rules

        def replace(match: re.Match[str]) -> str:
            nonlocal replacements
            original = match.group()
            identity, is_marker = self._tokens[original]
            rule = self._rules[identity]
            marker = rule["marker"]
            matched_rules.add(identity)
            if not is_marker:
                edit = {"start": match.start(), "end": match.end(), "original": original,
                        "replacement": marker, "rule_id": identity,
                        "evidence_kind": "observed_self_description", "upstream_control_verified": False}
                for key in ("field", "observed_value"):
                    if key in rule:
                        edit[key] = rule[key]
                edits.append(edit)
            replacement = rule.get("replacement") if target else None
            if replacement is not None:
                replacements += 1
                return replacement
            return marker

        adapted = self._pattern.sub(replace, text)
        return adapted, {"source_control_edits": edits, "control_text_replacements": replacements}, matched_rules

    def mark(self, text: str) -> tuple[str, list[dict]]:
        """Insert portable markers only; offsets refer to the original text."""
        adapted, metadata, _ = self._transform(text, target=False)
        return adapted, metadata["source_control_edits"]

    def apply(self, text: str, *, task_contents=()) -> tuple[str, dict]:
        """Adapt current reasoning, rejecting references inside task content.

        Literal-to-marker edits describe the intermediate view; the replacement
        count also includes markers already present in the input. The original
        text stays available to the caller, regardless of either transformation.
        """
        adapted, metadata, matched_rules = self._transform(text, target=True)
        if matched_rules:
            # Match every alias of relevant rules, including when this input was
            # marked previously and therefore produces no new literal edits.
            if any(self._task_patterns[identity].search(content)
                   for content in task_contents for identity in matched_rules):
                raise ValueError("control_text_reference_in_task_content")
        return adapted, metadata


_DEFAULT_CONFIGURATION = {
    "schema": SCHEMA,
    "enabled": True,
    "rules": [
        {
            "id": "deepseek-output-verbosity-5", "enabled": True,
            "matches": ["Desired oververbosity 5", "desired verbosity 5"],
            "marker": "[[SJ_OUTPUT_VERBOSITY:5]]", "replacement": None,
            "field": "output_verbosity", "observed_value": 5,
        },
        {
            "id": "reasoning-effort-reference", "enabled": True, "matches": [],
            "marker": "[[SJ_REASONING_EFFORT]]", "replacement": "思考已开启",
            "field": "reasoning_reference",
        },
    ],
}


def default_control_text_config() -> dict:
    """Return an editable copy of the built-in exact-match example."""
    return deepcopy(_DEFAULT_CONFIGURATION)


def validate_control_text_config(configuration: Mapping) -> dict:
    """Validate an external configuration and return an independent snapshot."""
    return ControlTextAdapter(configuration).configuration


def preview_control_text(configuration: Mapping, text: str, *, task_contents=()) -> dict:
    """Preview the two adaptation phases without changing stored source text."""
    adapter = ControlTextAdapter(configuration)
    marked, _ = adapter.mark(text)
    adapted, receipt = adapter.apply(text, task_contents=task_contents)
    return {"original_text": text, "marked_text": marked, "transformed_text": adapted,
            "match_count": sum(1 for _ in adapter._pattern.finditer(text)) if adapter._pattern else 0,
            "receipt": receipt}
