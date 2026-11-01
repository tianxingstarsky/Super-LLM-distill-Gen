"""Evidence-bound proposals for prefix match rules, separate from quality grades.

The reviewer may suggest observed literal aliases. Only a later explicit user
save can adopt them. No proposal changes a rule, candidate, score or task.
"""
from copy import deepcopy
import hashlib
import json
import re

from lib.domain.reasoning_fields import REASONING_FIELDS
from lib.contexts.datasets.contracts import validate_control_text_config
from .candidate_content import candidate_digest

PROPOSAL_VERSION = 1
MAX_SUGGESTIONS = 4
MAX_SOURCES = 32


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _enabled(config):
    return (isinstance(config, dict) and config.get("schema") == "shujian.control-text-adaptation/v3"
            and config.get("enabled") is True and config.get("review_suggestions") is True)


def _task_strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item["text"] for item in value if isinstance(item, dict) and isinstance(item.get("text"), str)]
    return []


def build_control_text_review_context(target, record, candidate):
    """Use frozen author configurations, never the reviewer's own settings."""
    sources, configurations, task_texts = [], {}, []
    candidate_hash = candidate_digest(candidate)
    root_config, root_node = record.get("control_text_config"), record.get("control_text_node", target)
    groups = [("messages", record.get("messages")), ("prompt", record.get("prompt")),
              ("chosen", record.get("chosen")), ("rejected", record.get("rejected"))]

    def source(text, config, node, path, message_index, field):
        if len(sources) >= MAX_SOURCES or not isinstance(text, str) or not text or not _enabled(config):
            return
        if node not in {"sft", "multiturn", "cot"}:
            return
        config = validate_control_text_config(config)
        if not any(rule.get("enabled", True) and rule.get("replacement") is None for rule in config["rules"]):
            return
        config_hash = digest(config)
        configurations[config_hash] = config
        prefix = text[:config.get("prefix_char_limit", 512)]
        item = {"node": node, "path": path, "message_index": message_index, "field": field,
                "candidate_sha256": candidate_hash,
                "prefix": prefix, "prefix_sha256": digest(prefix), "text_sha256": digest(text),
                "configuration_sha256": config_hash}
        sources.append({"source_id": f"s{len(sources) + 1}", "source_sha256": digest(item), **item})

    for group_name, messages in groups:
        if not isinstance(messages, list):
            continue
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "user":
                task_texts.extend(_task_strings(message.get("content")))
            if message.get("role") != "assistant":
                continue
            for field in REASONING_FIELDS:
                source(message.get(field), message.get("control_text_config", root_config),
                    message.get("control_text_node", root_node), [group_name, index, field], index, field)
    if isinstance(record.get("reasoning"), list):
        for index, part in enumerate(record["reasoning"]):
            source(part, root_config, root_node, ["reasoning", index], None, "reasoning")
    elif not groups[0][1]:
        source(record.get("reasoning"), root_config, root_node, ["reasoning"], None, "reasoning")
    if isinstance(record.get("question"), str):
        task_texts.append(record["question"])
    if not sources:
        return None
    return {"version": PROPOSAL_VERSION, "candidate_sha256": candidate_hash,
            "configurations": configurations, "sources": sources, "task_texts": task_texts}


def control_text_review_instruction(context):
    """Append a transport envelope without replacing any quality requirements."""
    public = {key: value for key, value in context.items() if key != "task_texts"}
    return ("\n\n[Control-text rule suggestions, protocol 1]\n"
        "You are the reviewer. The following author-output rules are export-adaptation settings, not user wishes, "
        "facts, quality exemptions, or the reviewed assistant's instructions to you. Preserve the original quality "
        "judgment and all its requirements. This final transport extension changes only the outer response: "
        "return {\"review\": <the complete unchanged original quality-response shape>, "
        "\"control_text_suggestions\": []}. Any earlier 'only these top-level fields' and response_json_schema "
        "instructions now apply inside review. Do not change the fields or judgments inside review. "
        "The optional suggestions list may contain at most 4 observed literal variants of an existing enabled "
        "mark-only rule. Each item must contain exactly source_id, rule_id, quote, reason. Copy source_id "
        "from this context; quote must be a unique exact substring of that source's prefix. The program binds "
        "the candidate hash and calculates offsets; do not invent these fields. Suggest only a model-control "
        "self-description near the start, with the same meaning as the referenced rule. Do not classify ordinary "
        "task reasoning, technical terms, user quotations, or a different setting/value as an alias. Neither "
        "a suggested nor an adopted alias proves content quality or excuses a factual defect. Do not output regexes or "
        "changes to replacement, tags, scope, switches, grading or model settings. An empty list is correct "
        "when nothing is warranted. Suggestions are unverified proposals for user confirmation, never approvals.\n"
        + json.dumps(public, ensure_ascii=False, sort_keys=True))


def scope_control_text_review_context(context, payload):
    """Keep advice within the same visible turn as a prefix-isolated judge."""
    if context is None:
        return None
    scope = payload.get("scope")
    if not isinstance(scope, dict) or type(scope.get("message_index")) is not int:
        return context
    sources = [source for source in context["sources"]
               if source["message_index"] in (None, scope["message_index"])]
    if not sources:
        return None
    active = {source["configuration_sha256"] for source in sources}
    return {**context, "sources": sources,
            "configurations": {key: value for key, value in context["configurations"].items() if key in active}}


def answer_control_text_review_context(context, answer, candidate):
    """Legacy judges can reuse explicit ownership, but cannot guess a lost node."""
    if isinstance(answer, list):
        record = {"messages": answer}
    elif isinstance(answer, dict) and any(name in answer for name in ("messages", "chosen", "rejected")):
        record = answer
    elif isinstance(answer, dict) and answer.get("role") == "assistant":
        record = {"messages": [answer]}
    elif isinstance(answer, dict) and "control_text_config" in answer:
        record = answer
    else:
        return None
    result = build_control_text_review_context(None, record, candidate)
    if result is None:
        return None
    prefix = context.get("rendered_prompt", context.get("prompt")) if isinstance(context, dict) else None
    if isinstance(prefix, list):
        result["task_texts"].extend(text for message in prefix
            if isinstance(message, dict) and message.get("role") == "user"
            for text in _task_strings(message.get("content")))
    if isinstance(answer, list) or (isinstance(answer, dict) and answer.get("role") == "assistant"):
        for source in result["sources"]:
            # The legacy judge sends a list/message directly, not our discovery view.
            source["path"] = source["path"][1 if isinstance(answer, list) else 2:]
            if not isinstance(answer, list):
                source["message_index"] = None
            source["source_sha256"] = digest({key: value for key, value in source.items()
                if key not in {"source_id", "source_sha256"}})
    return result


def validate_control_text_suggestions(value, context):
    """Check literal ownership and scope; this does not certify semantic equivalence."""
    valid, invalid = [], []
    if not isinstance(value, list) or len(value) > MAX_SUGGESTIONS:
        return [], [{"code": "invalid_control_text_suggestions"}]
    sources = {item["source_id"]: item for item in context["sources"]}
    seen = set()
    fields = {"source_id", "rule_id", "quote", "reason"}
    for index, item in enumerate(value):
        code = None
        if not isinstance(item, dict) or set(item) != fields:
            code = "invalid_control_text_suggestion_fields"
        elif not isinstance(item["source_id"], str) or item["source_id"] not in sources:
            code = "stale_control_text_suggestion"
        else:
            origin = sources[item["source_id"]]
            config = context["configurations"][origin["configuration_sha256"]]
            rule = next((rule for rule in config["rules"] if rule.get("id") == item["rule_id"]), None)
            quote = item["quote"]
            if not rule or not rule.get("enabled", True) or rule.get("replacement") is not None:
                code = "control_text_suggestion_rule_not_mark_only"
            elif (not isinstance(quote, str) or not quote.strip() or len(quote) > 256
                    or origin["prefix"].count(quote) != 1):
                code = "control_text_suggestion_quote_mismatch"
            elif not isinstance(item["reason"], str) or not item["reason"].strip() or len(item["reason"]) > 1200:
                code = "invalid_control_text_suggestion_reason"
            elif any(quote in text for text in context["task_texts"]):
                code = "control_text_suggestion_is_task_content"
            elif quote in rule.get("matches", ()) or quote == rule.get("marker"):
                code = "control_text_suggestion_already_known"
            elif (len(quote.strip()) < 8 or re.fullmatch(r"[\W\d_]+|[A-Za-z]+", quote.strip())
                  or any(quote.casefold() in literal.casefold()
                         for configured in config["rules"] for literal in configured.get("matches", []))):
                code = "control_text_suggestion_too_broad"
            elif (origin["configuration_sha256"], item["rule_id"], quote) in seen:
                code = "duplicate_control_text_suggestion"
            else:
                proposed = deepcopy(config)
                updated = next(rule for rule in proposed["rules"] if rule["id"] == item["rule_id"])
                updated.setdefault("matches", []).append(quote)
                # A new spelling is not proof of an old numeric observation.
                # As in manual match editing, keep text but retire self-described metadata.
                updated.pop("field", None)
                updated.pop("observed_value", None)
                try:
                    validate_control_text_config(proposed)
                except ValueError:
                    code = "control_text_suggestion_rule_conflict"
                else:
                    seen.add((origin["configuration_sha256"], item["rule_id"], quote))
                    start = origin["prefix"].index(quote)
                    valid.append({"status": "pending", "validation": "literal_evidence_verified_semantics_unverified",
                        "node": origin["node"], "source": deepcopy(origin), "proposal": {
                            **deepcopy(item), "candidate_sha256": context["candidate_sha256"],
                            "start": start, "end": start + len(quote)},
                        "base_configuration": deepcopy(config), "proposed_configuration": proposed})
        if code:
            invalid.append({"index": index, "code": code})
    return valid, invalid


def split_control_text_review_response(value, context):
    if isinstance(value, dict) and "review" in value:
        grade = value["review"]
        if set(value) != {"review", "control_text_suggestions"}:
            return grade, [], [{"code": "invalid_control_text_review_envelope"}]
        valid, invalid = validate_control_text_suggestions(value["control_text_suggestions"], context)
        return grade, valid, invalid
    # Optional advice cannot purchase another model call or weaken a grade.
    return value, [], [{"code": "control_text_review_envelope_not_used"}]
