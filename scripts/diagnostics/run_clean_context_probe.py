"""Run two pre-frozen public-dialogue controls with full client-side HTTP evidence."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lib.llm_client import load_backend
from lib.io_utils import atomic_json
from scripts.diagnostics.observe_http_json import observe_http_json

MODEL = "deepseek-flash"
CASES = ("01", "02")


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def freeze(path, value):
    if path.exists():
        if read(path) != value:
            raise ValueError(f"frozen_control_changed:{path.name}")
    else:
        atomic_json(path, value)


def validate_request(body, case, *, initial_system=None):
    expected = {"messages", "model", "max_tokens", "reasoning_effort", "temperature", "thinking"}
    if set(body) != expected or body["model"] != MODEL:
        raise ValueError("unexpected_control_parameters")
    messages = body["messages"]
    if initial_system is not None:
        if (not isinstance(initial_system, str) or not initial_system.strip()
                or not messages or messages[0] != {"role": "system", "content": initial_system}):
            raise ValueError("unexpected_initial_system")
        messages = messages[1:]
    if [m.get("role") for m in messages] != (["user"] if case == "01" else ["user", "assistant", "user"]):
        raise ValueError("unexpected_public_dialogue_roles")
    if any(set(m) != {"role", "content"} or not isinstance(m["content"], str) or not m["content"] for m in messages):
        raise ValueError("unexpected_public_message_fields")
    content = json.dumps(messages, ensure_ascii=False).lower()
    if any(term in content for term in ("verbosity", "previous_candidate", "revision_feedback", "reasoning_content")):
        raise ValueError("contaminated_control")
    if (body["max_tokens"] != 32768 or body["reasoning_effort"] != "low"
            or body["temperature"] != 0.7 or body["thinking"] != {"type": "enabled"}):
        raise ValueError("changed_control_parameters")


def sdk_arguments(body):
    result = deepcopy(body)
    result["extra_body"] = {"thinking": result.pop("thinking")}
    return result


def run_case(sdk, body, directory, case, *, network_route, initial_system=None):
    """No generated history is carried to the other case, and no attempt is retried."""
    validate_request(body, case, initial_system=initial_system)
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / "result.json"
    attempt_path = directory / "attempt.json"
    if result_path.exists():
        result = read(result_path)
        if result["request"] != body:
            raise ValueError("completed_request_changed")
        return result
    if attempt_path.exists():
        raise RuntimeError("prior_attempt_unresolved_do_not_repurchase")
    arguments = sdk_arguments(body)
    attempt = {"case_id": case, "started_at": now(), "status": "started",
               "request": body, "max_completion_calls": 1, "network_route": network_route,
               "sdk_max_retries": sdk.max_retries}
    if sdk.max_retries != 0:
        raise ValueError("sdk_retries_must_be_disabled")
    # Exclusive creation also prevents two processes from sending the same case.
    with attempt_path.open("x", encoding="utf-8") as stream:
        json.dump(attempt, stream, ensure_ascii=False, indent=2)
    started = time.perf_counter()
    guard_calls = []

    def guard(request):
        actual = json.loads(request.read())
        if (request.method != "POST" or request.url.host != "api.deepseek.com"
                or request.url.path != "/v1/chat/completions" or actual != body or guard_calls):
            raise ValueError("unexpected_final_http_request")
        guard_calls.append(True)

    http = sdk._client
    http.event_hooks.setdefault("request", []).append(guard)
    try:
        with observe_http_json(http, directory / "http", sdk_kwargs=arguments) as captured:
            raw = sdk.chat.completions.with_raw_response.create(**arguments)
            raw_bytes = raw.http_response.read()
            raw_path = directory / "response-body.json"
            raw_path.write_bytes(raw_bytes)
            parsed = json.loads(raw_bytes)
            if len(captured) != 1 or len(guard_calls) != 1:
                raise ValueError("expected_one_http_request")
            if parsed.get("model") != MODEL:
                raise ValueError("unexpected_response_model")
            result = {**attempt, "status": "completed", "completed_at": now(),
                      "elapsed_seconds": time.perf_counter() - started,
                      "request_capture": str(captured[0].relative_to(directory)),
                      "response_body_sha256": sha256(raw_bytes).hexdigest(),
                      "http_status": raw.http_response.status_code,
                      "response_request_id": raw.http_response.headers.get("x-request-id"),
                      "raw_response": parsed, "usage": parsed.get("usage"),
                      "training_status": "not_assessed", "independent_review_calls": 0}
            atomic_json(result_path, result)
            return result
    except Exception as error:
        atomic_json(directory / "error.json", {"type": type(error).__name__,
                    "code": getattr(error, "code", None), "status_code": getattr(error, "status_code", None),
                    "elapsed_seconds": time.perf_counter() - started,
                    "automatic_retry": False, "status": "unresolved_attempt"})
        raise
    finally:
        http.event_hooks["request"].remove(guard)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--initial-system", type=Path,
                        help="Optional frozen initial system text; public history is still checked separately.")
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    initial_system = options.initial_system.read_text(encoding="utf-8") if options.initial_system else None
    if initial_system is not None:
        freeze(options.output / "initial-system.json", {"content": initial_system,
               "sha256": sha256(initial_system.encode("utf-8")).hexdigest()})
    controls = {}
    provenance = []
    for case in CASES:
        path = options.requests / f"{case}-public-dialogue-request.json"
        body = read(path)
        validate_request(body, case, initial_system=initial_system)
        freeze(options.output / f"{case}-request.json", body)
        controls[case] = body
        provenance.append({"case_id": case, "source_path": str(path.resolve()),
                           "source_sha256": sha256(path.read_bytes()).hexdigest()})
    freeze(options.output / "design.json", {
        "model": MODEL, "expected_model_family": "DeepSeek-V4.1-Flash",
        "scope": "One isolated call for each original pending user turn, not a new two-turn dialogue.",
        "controls": provenance, "maximum_completion_calls": 2, "independent_review_calls": 0,
        "context": ("Frozen initial system followed by original public role/content messages. No native history, feedback or tools."
                    if initial_system is not None else
                    "Only original public role/content messages. No system, native history, feedback or tools."),
        "preservation": "Inputs are read-only; original archives are not rewritten.",
        "parameters": {k: v for k, v in controls['01'].items() if k != 'messages'},
        "sdk_versions": {k: importlib.metadata.version(k) for k in ("openai", "httpx")}})
    if not options.execute:
        print(json.dumps({"prepared_controls": 2, "model_calls": 0}))
        return
    client, selected = load_backend(ROOT, backend="deepseek", model=MODEL,
                                    allow_global_endpoint_override=False)
    try:
        if selected != MODEL or client.base_url.rstrip("/") != "https://api.deepseek.com/v1":
            raise ValueError("unexpected_configured_endpoint_or_model")
        identity = next(m.model_dump(mode="json") for m in client.client.models.list(timeout=25).data if m.id == MODEL)
        if not str(identity.get("name", "")).startswith("DeepSeek-V4.1-Flash"):
            raise ValueError("upstream_model_family_not_verified")
        atomic_json(options.output / "model-identity.json", {"checked_at": now(), "model": identity,
                    "network_route": client.network_route,
                    "proxy_environment_variables_present": [k for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") if os.getenv(k)],
                    "limitation": "Provider metadata, not independent attestation of remote weights or hidden instructions."})
    finally:
        client.client.close()
    for case, body in controls.items():
        client, _ = load_backend(ROOT, backend="deepseek", model=MODEL, allow_global_endpoint_override=False)
        try:
            if client.base_url.rstrip("/") != "https://api.deepseek.com/v1":
                raise ValueError("endpoint_changed")
            value = run_case(client.client, body, options.output / "calls" / case, case,
                             network_route=client.network_route, initial_system=initial_system)
            print(json.dumps({"case_id": case, "status": value["status"],
                              "elapsed_seconds": value["elapsed_seconds"], "usage": value["usage"]}), flush=True)
        finally:
            client.client.close()


if __name__ == "__main__":
    main()
