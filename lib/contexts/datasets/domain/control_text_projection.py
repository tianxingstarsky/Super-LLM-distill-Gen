"""Expose paired control annotations in export views, preserving author text.

Only explicit v3 configurations opt in. The literal matching and tag handling
belong to ControlTextAdapter; this module only selects owned reasoning fields.
Target-model replacements remain a training-loader concern.
"""
from collections.abc import Mapping
from copy import deepcopy
from lib.domain.reasoning_fields import REASONING_FIELDS
from .control_text import ControlTextAdapter, PAIRED_SCHEMA


def with_source_control_text(messages, source):
    """Carry known v3 author rules with copied source assistants, never siblings."""
    config = source.get("control_text_config")
    if not isinstance(config, Mapping) or config.get("schema") != PAIRED_SCHEMA:
        return messages
    copied = deepcopy(messages)
    for message in copied:
        if message.get("role") == "assistant" and "control_text_config" not in message:
            message["control_text_config"] = deepcopy(config)
            if "control_text_node" in source:
                message["control_text_node"] = source["control_text_node"]
    return copied


def without_control_text_metadata(record):
    """Hide v3 export metadata from model-facing candidates, never prose.

    Only known record/message containers are visited. Tool schemas, arguments,
    user content and complete reasoning stay literal. Legacy-only records keep
    their exact historical request shape.
    """
    if not isinstance(record, Mapping):
        return record
    groups = [record.get(field) for field in ("messages", "prompt", "chosen", "rejected")]
    if isinstance(record.get("responses"), list):
        groups.extend(response.get("response") for response in record["responses"]
                      if isinstance(response, Mapping))
    messages = [message for group in groups if isinstance(group, list)
                for message in group if isinstance(message, Mapping)]
    if not any(isinstance(item.get("control_text_config"), Mapping)
               and item["control_text_config"].get("schema") == PAIRED_SCHEMA
               for item in [record, *messages]):
        return record

    def clean(item):
        return {key: value for key, value in item.items()
                if key not in {"control_text_config", "control_text_node"}}

    def clean_messages(group):
        return [clean(message) if isinstance(message, Mapping) else message for message in group]

    output = clean(record)
    for field in ("messages", "prompt", "chosen", "rejected"):
        if isinstance(record.get(field), list):
            output[field] = clean_messages(record[field])
    if isinstance(record.get("responses"), list):
        output["responses"] = [{**response, "response": clean_messages(response["response"])}
                               if isinstance(response, Mapping) and isinstance(response.get("response"), list)
                               else response for response in record["responses"]]
    return output


def _task_texts(message):
    if message.get("role") == "system":
        return []
    content = message.get("content", "")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [block["text"] for block in content if isinstance(block, Mapping)
                and isinstance(block.get("text"), str)]
    return []


def mark_training_control_text(record: Mapping) -> dict:
    """Copy only projected fields; never edit evidence or infer missing rules.

    Each assistant's frozen configuration overrides the record default. Task
    references are checked against that turn's own prefix, never later turns.
    Plain-string preference answers have no separate reasoning to annotate.
    """
    output = dict(record)
    configuration = record.get("control_text_config")
    adapters = {}

    def adapter_for(config):
        if not isinstance(config, Mapping) or config.get("schema") != PAIRED_SCHEMA:
            return None
        identity = id(config)
        if identity not in adapters:
            adapters[identity] = ControlTextAdapter(config)
        return adapters[identity]

    def messages_view(messages, prefix=()):
        if not isinstance(messages, list):
            return messages
        copied = []
        task_contents = [text for message in prefix if isinstance(message, Mapping)
                         for text in _task_texts(message)]
        for message in messages:
            if not isinstance(message, Mapping):
                copied.append(message)
                continue
            task_contents.extend(_task_texts(message))
            adapter = adapter_for(message.get("control_text_config", configuration))
            if message.get("role") != "assistant" or adapter is None:
                copied.append(message)
                continue
            changed = dict(message)
            for field in REASONING_FIELDS:
                if isinstance(message.get(field), str) and message[field]:
                    changed[field], _ = adapter.mark(message[field], task_contents=task_contents)
            copied.append(changed)
        return copied

    for field in ("messages", "prompt"):
        if field in record:
            output[field] = messages_view(record[field])
    prefix = record.get("prompt", [])
    prefix = prefix if isinstance(prefix, list) else []
    for field in ("chosen", "rejected"):
        if field in record:
            output[field] = messages_view(record[field], prefix)
    if isinstance(record.get("responses"), list):
        output["responses"] = [
            {**response, "response": messages_view(response["response"], prefix)}
            if isinstance(response, Mapping) and "response" in response else response
            for response in record["responses"]
        ]
    if isinstance(record.get("reasoning"), list):
        adapter = adapter_for(configuration)
        if adapter is not None:
            question = record.get("question", "")
            task_contents = ([question] if isinstance(question, str) else
                             [text for message in question if isinstance(message, Mapping)
                              for text in _task_texts(message)] if isinstance(question, list) else [])
            if isinstance(record.get("answer"), str):
                task_contents.append(record["answer"])
            output["reasoning"] = [adapter.mark(part, task_contents=task_contents)[0]
                                   if isinstance(part, str) else part for part in record["reasoning"]]
    return output
