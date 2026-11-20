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


def _inspect_parent(parent, raw, selected, endpoints, location, *, confirm_listeners=False):
    prior = json.loads(raw)
    binding = digest(prior)
    observed = {}
    for role, before in prior["endpoints"].items():
        key = (before["url"], before["model"])
        if key not in observed:
            observed[key] = native_instance(endpoints[role], *key)
        if observed[key][2] != before["context_tokens"]:
            raise ValueError("original slot differs from its frozen capacity")
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
                    role in {"final_answer", "strong_final_answer"}
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
                    or not instance[2] < overflow
                ):
                    raise ValueError("the exact parent request is not proven unsent and recoverable")
                proof = {
                    **counts,
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
    if confirm_listeners:
        for (url, model), prior_instance in observed.items():
            role = next(r for r, s in prior["endpoints"].items() if (s["url"], s["model"]) == (url, model))
            if native_instance(endpoints[role], url, model) != prior_instance:
                raise ValueError("original native listener changed while witnessing the failed requests")
    return {
        "schema": "guided-local-overflow-proof/v1",
        "source_binding": binding,
        "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "native_models": {r: observed[(v["url"], v["model"])][0] for r, v in prior["endpoints"].items()},
        "process_started": {r: observed[(v["url"], v["model"])][1] for r, v in prior["endpoints"].items()},
        "cases": cases,
    }


def seal_context_proof(parent, output, selected, location):
    """Witness the original listener and exact rejected prompts before retiring it."""
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("seal one or more distinct original case IDs")
    if output.resolve().is_relative_to(parent.resolve()):
        raise ValueError("write the sealed proof outside the original run")
    if output.exists():
        raise FileExistsError(output)
    raw = (parent / "manifest.json").read_bytes()
    specs = json.loads(raw)["endpoints"]
    readers = {
        role: GuidedChatEndpoint(spec["url"], spec["model"], None, spec["max_tokens"],
                                 local=True, context=spec["context_tokens"], pool=None)
        for role, spec in specs.items()
    }
    proof = _inspect_parent(parent, raw, selected, readers, location, confirm_listeners=True)
    wire = json.dumps(proof, ensure_ascii=False, sort_keys=True, indent=2).encode()
    with output.open("xb") as file:
        file.write(wire)
    return {"sha256": hashlib.sha256(wire).hexdigest(), "cases": len(selected)}


def freeze_context_recovery(parent, current, selected, endpoints, location, proof_path=None, proof_sha256=None):
    raw = (parent / "manifest.json").read_bytes()
    prior = json.loads(raw)
    if {k: v for k, v in current.items() if k != "endpoints"} != {
        k: v for k, v in prior.items() if k != "endpoints"
    } or set(current["endpoints"]) != set(prior["endpoints"]):
        raise ValueError("context recovery requires the full original input and frozen generation protocol")
    if proof_path is None:
        proof = _inspect_parent(parent, raw, selected, endpoints, location)
    else:
        wire = proof_path.read_bytes()
        if hashlib.sha256(wire).hexdigest() != proof_sha256:
            raise ValueError("recovery proof does not match the pinned SHA-256")
        proof = json.loads(wire)
        if (proof["schema"] != "guided-local-overflow-proof/v1" or proof["source_binding"] != digest(prior)
                or proof["source_manifest_sha256"] != hashlib.sha256(raw).hexdigest()
                or set(selected) - proof["cases"].keys()):
            raise ValueError("sealed proof does not cover this source run and selected cases")

    observed = {}
    for role, before in prior["endpoints"].items():
        after = current["endpoints"][role]
        if before != after and (
            {k: v for k, v in before.items() if k not in {"url", "context_tokens"}}
            != {k: v for k, v in after.items() if k not in {"url", "context_tokens"}}
            or after["context_tokens"] <= before["context_tokens"]
            or (before["url"] == after["url"] and proof_path is None)
        ):
            raise ValueError("only a local replacement with an increased context may change")
        key = (after["url"], after["model"])
        if key not in observed:
            observed[key] = native_instance(endpoints[role], *key)
        signature, started, capacity = observed[key]
        if capacity != after["context_tokens"] or signature != proof["native_models"][role]:
            raise ValueError("native weights, template, build, generation settings or measured slot differ")
        if before != after and before["url"] == after["url"] and started <= proof["process_started"][role]:
            raise ValueError("same-address replacement requires a new native listener after sealing the original")

    cases = {}
    for ident in sorted(selected):
        folder = location(parent, ident)
        case = proof["cases"][ident]
        if proof_path is not None:
            if ((folder / "case.json").exists()
                    or hashlib.sha256((folder / "interruption.json").read_bytes()).hexdigest() != case["stop_sha256"]
                    or {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob("[0-9]*-*.json")}
                    != case["receipts"]):
                raise ValueError("original interruption or call receipts differ from the sealed proof")
        failed = (folder / case["failed"]).read_bytes()
        if hashlib.sha256(failed).hexdigest() != case["receipts"][case["failed"]]:
            raise ValueError("the original rejected request changed")
        event = json.loads(failed)
        role = endpoint_role(event["role"])
        tokens = endpoints[role].chat_prompt_tokens(event["request"])
        check = case["preflight"]
        if len(tokens) != check["prompt_tokens"] or digest(tokens) != check["tokens_sha256"]:
            raise ValueError("the recovery server does not render the identical native token sequence")
        capacity = current["endpoints"][role]["context_tokens"]
        if not check["context_tokens"] < len(tokens) + check["output_tokens"] <= capacity:
            raise ValueError("the full original request and output reservation do not fit the recovery slot")
        cases[ident] = {**case, "preflight": {**check, "recovery_context_tokens": capacity}}
    result = {k: proof[k] for k in ("source_binding", "source_manifest_sha256", "native_models")}
    result["cases"] = cases
    if proof_path is not None:
        result["source_proof_sha256"] = proof_sha256
    return result
