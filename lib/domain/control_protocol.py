"""Versioned, provider-neutral requests for external workflow controllers."""
from __future__ import annotations

import re

PROTOCOL = "dataforge.control"
VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ACTIONS = {
    "capabilities": set(),
    "runs.list": {"offset", "limit"},
    "runs.inspect": {"run_id"},
    "runs.create": {"recipe"},
    "runs.execute": {"run_id"},
    "runs.cancel": {"run_id"},
    "runs.events": {"run_id", "offset", "limit"},
    "runs.streams": {"run_id", "stage"},
    "runs.stream_delta": {"run_id", "request_id", "offset", "limit_bytes", "stage"},
    "runs.preview": {"run_id", "target", "offset", "limit"},
    "runs.quality": {"run_id"},
    "models.list": set(),
    "models.status": {"backend", "model"},
    "models.limit": {"backend", "model", "max_concurrency", "request_queue_timeout_seconds"},
}
MUTATIONS = frozenset({"runs.create", "runs.execute", "runs.cancel", "models.limit"})
RECIPE_FIELDS = frozenset({
    "sources", "brief", "targets", "name", "backend", "model", "judge_backend", "judge_model",
    "jev_backend", "jev_model", "max_units", "chunk_chars", "tasks", "sample_count", "concurrency",
    "batch_size", "node_models", "conversation_turns", "document_parser", "cpt_processing",
    "node_generation", "reasoning_trim", "node_prompts", "qa_director", "production",
    "evaluation_sources", "web_research", "package_review", "review_repair", "agent_experience",
    "reasoning_required", "agent_replay_mode", "repair_inputs", "knowledge_retrieval",
    "sft_output_style", "source_names", "evaluation_source_names",
})


def integer(value, *, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid_control_integer")
    return value


def validate_request(value):
    if (not isinstance(value, dict) or set(value) != {"protocol", "version", "request_id", "action", "params"}
            or value["protocol"] != PROTOCOL or type(value["version"]) is not int or value["version"] != VERSION):
        raise ValueError("unsupported_control_protocol")
    request_id, action, params = value["request_id"], value["action"], value["params"]
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
        raise ValueError("invalid_control_request_id")
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("unknown_control_action")
    if not isinstance(params, dict) or set(params) - ACTIONS[action]:
        raise ValueError("invalid_control_parameters")
    if action.startswith("runs.") and action not in {"runs.list", "runs.create"}:
        if not isinstance(params.get("run_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", params["run_id"]):
            raise ValueError("invalid_control_run_id")
    for key in ("backend", "model", "request_id", "target", "stage"):
        if key in params and (not isinstance(params[key], str) or not params[key].strip()
                              or "\x00" in params[key] or len(params[key]) > 256):
            raise ValueError("invalid_control_parameters")
    if action in {"models.status", "models.limit"} and not {"backend", "model"} <= params.keys():
        raise ValueError("invalid_control_parameters")
    for key in ("offset", "limit", "limit_bytes"):
        if key in params:
            integer(params[key], minimum=1 if key != "offset" else 0,
                    maximum=100 if key == "limit" else 65536 if key == "limit_bytes" else 2**63 - 1)
    if action == "runs.create":
        recipe = params.get("recipe")
        if not isinstance(recipe, dict) or not recipe or set(recipe) - RECIPE_FIELDS:
            raise ValueError("invalid_control_recipe")
    if action == "runs.stream_delta" and "request_id" not in params:
        raise ValueError("invalid_control_parameters")
    if action == "runs.preview" and "target" not in params:
        raise ValueError("invalid_control_parameters")
    if action == "models.limit":
        integer(params.get("max_concurrency"), minimum=1, maximum=256)
        integer(params.get("request_queue_timeout_seconds", 300), minimum=1, maximum=3600)
    return value
