"""Freeze original local receipts for a larger-slot continuation of an unsent chat."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import psutil

from ..domain.experience_evolution import digest
from .guided_llama import GuidedChatEndpoint


def endpoint_role(role):
    if role in {"student", "student_step", "final_answer", "verified_answer", "verified_answer_retry"}:
        return "student"
    if role in {"strong_continuation", "strong_final_answer", "verified_strong_answer"}:
        return "continuation"
    return "coach"


def native_instance(endpoint, url, model):
    """Bind HTTP metadata to the listening process, its GGUF file and serving configuration."""
    parsed = urlsplit(url)
    if (
        not isinstance(endpoint, GuidedChatEndpoint)
        or not endpoint.local
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("context recovery requires local llama.cpp endpoints")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    owners = {
        c.pid
        for c in psutil.net_connections("tcp")
        if c.status == psutil.CONN_LISTEN
        and c.laddr.port == port
        and c.laddr.ip in {"0.0.0.0", "::", "::1", "127.0.0.1"}
    }
    if len(owners) != 1 or None in owners:
        raise ValueError("cannot establish a unique local model process for context recovery")
    process = psutil.Process(owners.pop())
    if Path(process.exe()).stem.lower() != "llama-server":
        raise ValueError("context recovery requires a direct llama-server listener")
    started = process.create_time()
    root = url.rstrip("/").removesuffix("/v1")
    props = endpoint.http(root + "/props")
    slots = endpoint.http(root + "/slots")
    if (
        props.get("model_alias") != model
        or not slots
        or not props.get("build_info")
        or not props.get("chat_template")
    ):
        raise ValueError("local model identity, template or slots are unavailable")
    weight = (Path(process.cwd()) / props["model_path"]).resolve(strict=True)
    params = props["default_generation_settings"]["params"]
    if (
        weight.suffix.lower() != ".gguf"
        or re.search(r"-\d{5}-of-\d{5}\.gguf$", weight.name, re.I)
        or weight.stat().st_mtime > started
        or params.get("lora") != []
    ):
        raise ValueError("context recovery needs one unchanged GGUF without runtime adapters")
    args = iter(process.cmdline()[1:])
    # Device placement, address and memory capacity may differ. Preserve sampling,
    # chat modes, speculative decoding and every other serving option verbatim.
    operation_options = {
        "-m",
        "--model",
        "--host",
        "--port",
        "-c",
        "--ctx-size",
        "--device",
        "-ngl",
        "--gpu-layers",
        "--n-gpu-layers",
        "-t",
        "--threads",
        "-tb",
        "--threads-batch",
        "-b",
        "--batch-size",
        "-ub",
        "--ubatch-size",
    }
    serving = []
    for arg in args:
        if arg in operation_options:
            next(args)
        elif arg.startswith(("--model-draft", "--draft-model", "--lora", "--mmproj")) or arg == "-md":
            raise ValueError("external model components cannot be authenticated for context recovery")
        else:
            serving.append(arg)
    flags = {slot.get("speculative") for slot in slots}
    if len(flags) != 1 or not all(isinstance(value, bool) for value in flags):
        raise ValueError("native endpoint does not expose its speculative decoding mode")
    with weight.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    signature = {
        "model": model,
        "weights_sha256": checksum,
        "build": props["build_info"],
        "template_sha256": hashlib.sha256(props["chat_template"].encode()).hexdigest(),
        "params_sha256": digest(params),
        "launch_settings_sha256": digest(serving),
        "speculative": flags.pop(),
    }
    return signature, started, min(s["n_ctx"] for s in slots)


def freeze_context_recovery(parent, current, selected, endpoints, location):
    raw = (parent / "manifest.json").read_bytes()
    prior = json.loads(raw)
    binding = digest(prior)
    if {k: v for k, v in current.items() if k != "endpoints"} != {
        k: v for k, v in prior.items() if k != "endpoints"
    } or set(current["endpoints"]) != set(prior["endpoints"]):
        raise ValueError("context recovery requires the full original input and frozen generation protocol")
    changed = {}
    observed = {}
    for role, before in prior["endpoints"].items():
        after = current["endpoints"][role]
        if before != after and (
            {k: v for k, v in before.items() if k not in {"url", "context_tokens"}}
            != {k: v for k, v in after.items() if k not in {"url", "context_tokens"}}
            or after["context_tokens"] <= before["context_tokens"]
            or before["url"] == after["url"]
        ):
            raise ValueError("only a distinct local endpoint with an increased context may change")
        pair = []
        for spec in (before, after):
            key = (spec["url"], spec["model"])
            if key not in observed:
                observed[key] = native_instance(endpoints[role], *key)
            instance = observed[key]
            if instance[2] != spec["context_tokens"]:
                raise ValueError("original and recovery slots must both match their measured capacities")
            pair.append(instance)
        if pair[0][0] != pair[1][0]:
            raise ValueError("native weights, template, build or generation settings differ")
        if before != after:
            changed[role] = pair[0][0]
    cases = {}
    for ident in sorted(selected):
        folder = location(parent, ident)
        stop_bytes = (folder / "interruption.json").read_bytes()
        stop = json.loads(stop_bytes)
        if (
            stop.get("id") != ident
            or stop.get("binding") != binding
            or stop.get("status") != "interrupted"
            or stop.get("detail") != "complete transcript plus output reservation exceeds the native slot"
            or stop.get("error") not in {"GuidedPromptOverflow", "ValueError"}
            or (folder / "case.json").exists()
        ):
            raise ValueError("original case does not have a bound local chat preflight interruption")
        failed = stop["checkpoint"]
        hashes = {}
        proof = None
        for path in sorted(folder.glob("[0-9]*-*.json")):
            raw_request = path.read_bytes()
            event = json.loads(raw_request)
            role, index, request = event["role"], event["attempt"], event["request"]
            if (
                path.name != f"{index:03}-{role}.json"
                or event.get("origin")
                or event["identity"] != digest({"binding": binding, "id": ident, "payload": request})
            ):
                raise ValueError("original call identity, role or provenance differs")
            serving = endpoint_role(role)
            before = prior["endpoints"][serving]
            instance = observed[(before["url"], before["model"])]
            if instance[1] > path.stat().st_mtime:
                raise ValueError("the original model server restarted after a source receipt")
            if request.get("model") != before["model"]:
                raise ValueError("original call model differs from its frozen role")
            if path.name != failed:
                if (
                    event["status"] != "complete"
                    or [a["status"] for a in event["attempts"]] != ["complete"]
                    or not isinstance(event.get("response"), dict)
                    or event["response"].get("model") != before["model"]
                ):
                    raise ValueError("original case contains an unconfirmed or non-original response")
            else:
                if (
                    serving not in changed
                    or role in {"final_answer", "strong_final_answer"}
                    or event["status"] != "interrupted"
                    or "response" in event
                    or request.get("max_tokens") != before["max_tokens"]
                    or len(event["attempts"]) != 1
                ):
                    raise ValueError("context recovery requires a single, unsent local chat")
                attempt = event["attempts"][0]
                endpoint = endpoints[serving]
                old_root = before["url"].rstrip("/").removesuffix("/v1")
                tokens = endpoint.chat_prompt_tokens(request, root=old_root)
                if tokens != endpoint.chat_prompt_tokens(request):
                    raise ValueError(
                        "the recovery server does not render the identical native token sequence"
                    )
                counts = dict(
                    prompt_tokens=len(tokens), output_tokens=before["max_tokens"], context_tokens=instance[2]
                )
                overflow = len(tokens) + before["max_tokens"]
                legacy = {
                    "status": "interrupted",
                    "error": "ValueError",
                    "http_status": None,
                    "dispatch": "unknown",
                }
                typed = {
                    "status": "preflight_rejected",
                    "error": "GuidedPromptOverflow",
                    "model_request_sent": False,
                    **counts,
                }
                if (
                    attempt != (legacy if stop["error"] == "ValueError" else typed)
                    or not instance[2] < overflow <= current["endpoints"][serving]["context_tokens"]
                ):
                    raise ValueError("the exact parent request is not proven unsent and recoverable")
                proof = {
                    **counts,
                    "recovery_context_tokens": current["endpoints"][serving]["context_tokens"],
                    "tokens_sha256": digest(tokens),
                }
            hashes[path.name] = hashlib.sha256(raw_request).hexdigest()
        if proof is None:
            raise ValueError("the bound overflow checkpoint is missing")
        cases[ident] = {
            "failed": failed,
            "receipts": hashes,
            "preflight": proof,
            "stop_sha256": hashlib.sha256(stop_bytes).hexdigest(),
        }
    return {
        "source_binding": binding,
        "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "native_models": {r: observed[(v["url"], v["model"])][0] for r, v in prior["endpoints"].items()},
        "cases": cases,
    }
