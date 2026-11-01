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
ANNOTATION_SCHEMA = "shujian.control-text-adaptation/v2"
PAIRED_SCHEMA = "shujian.control-text-adaptation/v3"
CURRENT_SCHEMA = PAIRED_SCHEMA


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

    V3 wraps matched original text in a configurable pair of tags. In v2,
    ``replacement=None`` annotates original spans without changing text.
    V1 retains its historical literal-to-placeholder behavior for frozen tasks.
    Empty ``matches`` supports existing-marker adaptation. Disabling a rule
    bypasses it; disabling the configuration does not disable model thinking.
    """

    def __init__(self, configuration: Mapping):
        if (not isinstance(configuration, Mapping)
                or configuration.get("schema") not in (SCHEMA, ANNOTATION_SCHEMA, CURRENT_SCHEMA)
                or type(configuration.get("enabled")) is not bool
                or set(configuration) - ({"schema", "enabled", "rules", "annotation_tag", "prefix_char_limit",
                                          "review_suggestions"}
                                         if configuration.get("schema") == CURRENT_SCHEMA
                                         else {"schema", "enabled", "rules"})):
            raise ValueError("invalid_control_text_configuration")
        self._configuration = deepcopy(dict(configuration))
        self._annotations_only = configuration["schema"] != SCHEMA
        self._inline_tags = configuration["schema"] == CURRENT_SCHEMA
        self._annotation_tag = configuration.get("annotation_tag", "reason-strength")
        self._prefix_char_limit = configuration.get("prefix_char_limit", 512)
        if self._inline_tags and type(configuration.get("review_suggestions", False)) is not bool:
            raise ValueError("invalid_control_text_review_suggestions")
        self._enabled = configuration["enabled"]
        self._rules: dict[str, dict] = {}
        self._tokens: dict[str, tuple[str, bool]] = {}
        self._pattern = None
        self._task_patterns: dict[str, re.Pattern] = {}
        # A saved disabled configuration may contain rules being edited. Its
        # switch must still stop adaptation without compiling those drafts.
        if not self._enabled:
            return
        if self._inline_tags:
            if (type(self._prefix_char_limit) is not int
                    or not 64 <= self._prefix_char_limit <= 2048):
                raise ValueError("invalid_control_text_prefix_limit")
            if (not isinstance(self._annotation_tag, str)
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self._annotation_tag)):
                raise ValueError("invalid_control_text_annotation_tag")
            self._opening_tag = f"<{self._annotation_tag}>"
            self._closing_tag = f"</{self._annotation_tag}>"
            self._tag_pattern = re.compile(re.escape(self._opening_tag) + "|" + re.escape(self._closing_tag))
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
            if self._inline_tags and replacement is not None and any(
                    tag in replacement for tag in (self._opening_tag, self._closing_tag)):
                raise ValueError("control_text_replacement_cascade")
        if self._inline_tags and any(tag in token for token in self._tokens
                                     for tag in (self._opening_tag, self._closing_tag)):
            raise ValueError("control_text_rule_conflict")
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

    def _wrapped_spans(self, text: str):
        """Locate prefix pairs; wrapper characters do not consume source scope.

        A block beginning inside scope may close outside it: retain that whole
        block, then stop. Tags appearing only in later prose are not parsed.
        """
        spans = []
        opening = None
        cursor = 0
        plain_offset = 0
        for token in self._tag_pattern.finditer(text):
            if opening is None:
                plain_offset += token.start() - cursor
                if plain_offset >= self._prefix_char_limit:
                    break
                if token.group() == self._closing_tag:
                    raise ValueError("control_text_unbalanced_tags")
                opening = token
            else:
                inner_end = plain_offset + token.start() - opening.end()
                if token.group() == self._opening_tag:
                    if inner_end < self._prefix_char_limit:
                        raise ValueError("control_text_unbalanced_tags")
                    # Outside the prefix, nested-looking prose is not our input.
                    continue
                spans.append((opening.start(), opening.end(), token.start(), token.end()))
                # Only a recognized control wrapper is program annotation.
                # Unknown tag examples still consume their complete raw span.
                known = self._pattern.fullmatch(text[opening.end():token.start()])
                plain_offset += (token.start() - opening.end() if known
                                 else token.end() - opening.start())
                cursor = token.end()
                opening = None
                if plain_offset >= self._prefix_char_limit:
                    break
        if opening is not None:
            raise ValueError("control_text_unbalanced_tags")
        return spans

    def _transform(self, text: str, *, target: bool) -> tuple[str, dict, set[str]]:
        edits = []
        annotations = []
        replacements = 0
        matched_rules: set[str] = set()
        metadata = {"source_control_edits": edits, "control_text_replacements": 0}
        if self._annotations_only:
            metadata["source_control_annotations"] = annotations
        if not self._enabled or self._pattern is None:
            return text, metadata, matched_rules

        def replace(match: re.Match[str], *, offset=0, wrapped_span=None) -> str:
            nonlocal replacements
            original = match.group()
            identity, is_marker = self._tokens[original]
            rule = self._rules[identity]
            marker = rule["marker"]
            matched_rules.add(identity)
            replacement = rule.get("replacement") if target else None
            if self._annotations_only:
                annotation = {"start": offset + match.start(), "end": offset + match.end(), "original": original,
                              "marker": marker, "rule_id": identity,
                              "evidence_kind": "observed_self_description",
                              "upstream_control_verified": False}
                for key in ("field", "observed_value"):
                    if key in rule:
                        annotation[key] = rule[key]
                if self._inline_tags:
                    annotation["annotation_tag"] = self._annotation_tag
                annotations.append(annotation)
                if replacement is None:
                    return (self._opening_tag + original + self._closing_tag
                            if self._inline_tags else original)
                replacements += 1
                edit = {**annotation, "replacement": replacement}
                if wrapped_span is not None:
                    start, end = wrapped_span
                    edit.update(start=start, end=end, original=text[start:end])
                edits.append(edit)
                return replacement
            if not is_marker:
                edit = {"start": match.start(), "end": match.end(), "original": original,
                        "replacement": marker, "rule_id": identity,
                        "evidence_kind": "observed_self_description", "upstream_control_verified": False}
                for key in ("field", "observed_value"):
                    if key in rule:
                        edit[key] = rule[key]
                edits.append(edit)
            if replacement is not None:
                replacements += 1
                return replacement
            return marker

        if self._inline_tags:
            fragments = []
            cursor = 0
            plain_offset = 0

            def prefix_segment(start, end, source_offset):
                segment = text[start:end]
                remaining = self._prefix_char_limit - source_offset
                if remaining <= 0:
                    return segment
                pieces = []
                copied = 0
                # Match against the full segment so longest-literal and numeric
                # boundaries remain real, even when a match straddles the limit.
                for match in self._pattern.finditer(segment):
                    if match.end() > remaining:
                        break
                    pieces.extend((segment[copied:match.start()], replace(match, offset=start)))
                    copied = match.end()
                pieces.append(segment[copied:])
                return "".join(pieces)

            for start, content_start, content_end, end in self._wrapped_spans(text):
                fragments.append(prefix_segment(cursor, start, plain_offset))
                plain_offset += start - cursor
                inner = text[content_start:content_end]
                known = self._pattern.fullmatch(inner)
                match = known if plain_offset + len(inner) <= self._prefix_char_limit else None
                fragments.append(replace(match, offset=content_start, wrapped_span=(start, end))
                                 if match else text[start:end])
                plain_offset += len(inner) if known else end - start
                cursor = end
            fragments.append(prefix_segment(cursor, len(text), plain_offset))
            adapted = "".join(fragments)
        else:
            adapted = self._pattern.sub(replace, text)
        metadata["control_text_replacements"] = replacements
        return adapted, metadata, matched_rules

    def mark(self, text: str, *, task_contents=()) -> tuple[str, list[dict]]:
        """Mark original text without applying target-model replacements."""
        adapted, metadata, matched_rules = self._transform(text, target=False)
        self._reject_task_references(matched_rules, task_contents)
        return adapted, metadata["source_control_annotations" if self._annotations_only else "source_control_edits"]

    def apply(self, text: str, *, task_contents=()) -> tuple[str, dict]:
        """Adapt current reasoning, rejecting references inside task content.

        V3 adds pairs around original text; v2 only records spans. Both report
        target replacements separately from annotations. V1 receipts remain unchanged. The
        replacement count includes markers already present in the input.
        """
        adapted, metadata, matched_rules = self._transform(text, target=True)
        self._reject_task_references(matched_rules, task_contents)
        return adapted, metadata

    def _reject_task_references(self, matched_rules, task_contents):
        if matched_rules:
            # Match every alias of relevant rules, including when this input was
            # marked previously and therefore produces no new literal edits.
            if any(self._task_patterns[identity].search(content)
                   for content in task_contents for identity in matched_rules):
                raise ValueError("control_text_reference_in_task_content")


_DEFAULT_CONFIGURATION = {
    "schema": CURRENT_SCHEMA,
    "annotation_tag": "reason-strength",
    "prefix_char_limit": 512,
    "review_suggestions": True,
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
            "marker": "[[SJ_REASONING_EFFORT]]", "replacement": None,
            "field": "reasoning_reference",
        },
    ],
}


def default_control_text_config() -> dict:
    """Return v3 defaults: wrap original control text, never erase it implicitly."""
    return deepcopy(_DEFAULT_CONFIGURATION)


def validate_control_text_config(configuration: Mapping) -> dict:
    """Validate an external configuration and return an independent snapshot."""
    return ControlTextAdapter(configuration).configuration


def preview_control_text(configuration: Mapping, text: str, *, task_contents=()) -> dict:
    """Preview the two adaptation phases without changing stored source text."""
    adapter = ControlTextAdapter(configuration)
    marked, annotations = adapter.mark(text)
    adapted, receipt = adapter.apply(text, task_contents=task_contents)
    return {"original_text": text, "marked_text": marked, "transformed_text": adapted,
            "match_count": (len(annotations) if adapter._inline_tags else
                            sum(1 for _ in adapter._pattern.finditer(text)) if adapter._pattern else 0),
            "receipt": receipt}
