"""Checkpoint individual calls and export finished cases in original JSONL order."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore

from filelock import FileLock

from lib.contexts.models.contracts import ModelQueueTimeout
from ..application.generate_guided_answer import GenerateGuidedAnswer
from ..domain.experience_evolution import digest
from ..domain.guided_answers import validate_source
from ..domain.guided_teacher_delivery import uses_direct_first
from .guided_llama import DIRECT_ANSWER_GRAMMARS, GuidedAdmissionTimeout, wire_body
from lib.io_utils import atomic_json
from lib.prompts.workflow_guided_answers import VERSION

PAIRED_DISCLOSURE_VERSIONS = {"4.2.0", "4.2.1", "4.3.0", "4.4.0"}
VERIFIED_STEP_VERSIONS = {"4.1.0", "4.1.1"} | PAIRED_DISCLOSURE_VERSIONS
TEACHER_DISCLOSURE_VERSIONS = {"3.9.0", "4.0.0"} | VERIFIED_STEP_VERSIONS
DIRECT_SYNTAX_VERSIONS = {"3.8.0"} | TEACHER_DISCLOSURE_VERSIONS
FINAL_CONTINUATION_VERSIONS = {"3.7.0"} | DIRECT_SYNTAX_VERSIONS
FIRST_ANSWER_VERSIONS = {"3.6.0"} | FINAL_CONTINUATION_VERSIONS
SUPPORTED_VERSIONS = {"3.0.0", "3.1.0", "3.2.0", "3.3.0", "3.4.0", "3.5.0"} | FIRST_ANSWER_VERSIONS


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def inputs(path, protocol_version=VERSION, trusted_step_receipts=frozenset()):
    verified_options = ({"allow_verified_teaching_steps": True, "trusted_step_receipts": trusted_step_receipts}
                        if protocol_version in VERIFIED_STEP_VERSIONS else {})
    with path.open(encoding="utf-8-sig") as source:
        for line in source:
            if line.strip():
                yield validate_source(json.loads(line), allow_answer_requirement=protocol_version in FIRST_ANSWER_VERSIONS,
                                      allow_task_system=protocol_version in FINAL_CONTINUATION_VERSIONS,
                                      allow_direct_syntax=protocol_version in DIRECT_SYNTAX_VERSIONS, **verified_options)


def verified_step_registry(output, receipt_path, frozen_config):
    """An explicitly supplied verifier registry grants trust; a row cannot grant trust to itself."""
    archived = output / "verified-step-receipts.json"
    pinned = (frozen_config or {}).get("verified_step_registry_id")
    if not receipt_path and not pinned:
        return None, frozenset()
    registry = read(receipt_path or archived)
    if not isinstance(registry, dict) or registry.get("schema") != "guided-teaching-verification/v1":
        raise ValueError("unsupported teaching-step verifier receipt schema")
    hashes = registry.get("receipts")
    if not isinstance(hashes, list) or not hashes or not all(isinstance(h, str) and re.fullmatch("[0-9a-f]{64}", h) for h in hashes):
        raise ValueError("teaching-step verification requires SHA-256 receipts")
    canonical = {"schema": registry["schema"], "receipts": sorted(set(hashes))}
    if frozen_config is not None and digest(canonical) != pinned:
        raise ValueError("teaching-step verifier registry differs from the frozen run")
    return canonical, frozenset(hashes)


def location(root, ident):
    return root / "cases" / hashlib.sha256(ident.encode()).hexdigest()


def unsent_admission(attempt):
    # Old guided transports could raise this typed scheduler code only on lease entry.
    return attempt == {"status": "interrupted", "error": "ModelQueueTimeout", "http_status": ModelQueueTimeout.code} or (
        attempt.get("status") == "awaiting_capacity" and attempt.get("model_request_sent") is False
        and attempt.get("code") == ModelQueueTimeout.code
    )


def can_resume_admission(receipt, binding, ident, role, index):
    attempts = receipt.get("attempts")
    return (
        receipt.get("status") in {"interrupted", "awaiting_capacity"} and "response" not in receipt
        and "origin" not in receipt and receipt.get("role") == role and receipt.get("attempt") == index
        and isinstance(receipt.get("request"), dict)
        and receipt.get("identity") == digest({"binding": binding, "id": ident, "payload": receipt["request"]})
        and bool(attempts) and all(unsent_admission(item) for item in attempts)
    )


def capacity_stop(stop, outstanding, binding, ident):
    if not outstanding or not all(outstanding.values()):
        return False
    if not stop:
        return True
    if stop.get("binding") != binding or stop.get("id") != ident:
        return False
    if "checkpoint" in stop and stop["checkpoint"] not in outstanding:
        return False
    return (stop.get("status") == "awaiting_capacity"
            or (stop.get("error") == "ModelQueueTimeout" and stop.get("detail") == ModelQueueTimeout.code)
            or stop.get("detail") == "request outcome unresolved; explicit retry required")


def disclosure_delivery(case, attempt, folder):
    """Pair a published hint with the exact next student request and completed response."""
    hint = attempt.get("disclosure")
    if not hint:
        return None
    index = attempt["attempt"] + 1
    filename = f"{index:03}-student.json"
    receipt = read(folder / filename)
    payload = receipt["request"]
    if (receipt["identity"] != digest({"binding": case["binding"], "id": case["id"], "payload": payload})
            or receipt["status"] != "complete" or receipt["role"] != "student" or receipt["attempt"] != index):
        raise ValueError("next student response is not bound to the published disclosure")
    frame = payload["messages"][-1]
    prefix = "Continue the solution from this current view.\n"
    if frame["role"] != "tool" or not frame["content"].startswith(prefix):
        raise ValueError("next student request is missing its disclosure")
    view = json.loads(frame["content"][len(prefix):])
    previous = [h["text"] for h in case["disclosures"][:hint["ordinal"] - 1]]
    if view["new_clue"] != hint["text"] or view["previously_disclosed_clues"] != previous:
        raise ValueError("student request differs from its reviewed disclosure history")
    return {"ordinal": hint["ordinal"], "student_attempt": index, "student_receipt": filename,
            "student_finish_reason": receipt["response"]["finish_reason"]}


def replay_first(path, ident, payload, prior_binding):
    relative = location(Path("."), ident) / "001-student.json"
    receipt_bytes = (path / relative).read_bytes()
    prior = json.loads(receipt_bytes)
    wire = wire_body(payload)
    identity = digest({"binding": prior_binding, "id": ident, "payload": payload})
    if (
        prior.get("status") != "complete"
        or prior.get("role") != "student"
        or prior.get("attempt") != 1
        or prior.get("identity") != identity
        or wire_body(prior["request"]) != wire
        or prior.get("origin", {}).get("kind") == "replayed_first"
        or not prior.get("attempts")
        or prior["attempts"][-1]["status"] != "complete"
    ):
        raise ValueError(
            "first-student replay requires an actual completed request with the same bound id and identical wire body"
        )
    return prior["response"], {
        "kind": "replayed_first",
        "source_binding": prior_binding,
        "source_receipt": relative.as_posix(),
        "source_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "source_identity": identity,
        "wire_request_sha256": hashlib.sha256(wire).hexdigest(),
        "response_sha256": digest(prior["response"]),
    }


def replay_matching_call(root, ident, role, index, payload, prior_binding):
    relative = location(Path("."), ident) / f"{index:03}-{role}.json"
    path = root / relative
    if not path.exists():
        return None
    raw = path.read_bytes()
    prior = json.loads(raw)
    envelope = {"binding": prior_binding, "id": ident, "payload": prior["request"]}
    if prior["identity"] != digest(envelope) or (prior["role"], prior["attempt"]) != (role, index):
        raise ValueError("prior guided receipt does not match its original task and role")
    wire = wire_body(payload)
    if wire != wire_body(prior["request"]):
        return None
    if prior.get("origin"):
        raise ValueError("matching receipt is itself a replay; use its original run for this call")
    if can_resume_admission(prior, prior_binding, ident, role, index):
        return None
    if prior["status"] != "complete" or prior["attempts"][-1]["status"] != "complete":
        raise ValueError("identical prior call has no confirmed response")
    return prior["response"], {
        "kind": "replayed_matching_call", "source_binding": prior_binding,
        "source_receipt": relative.as_posix(), "source_receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "source_identity": prior["identity"], "wire_request_sha256": hashlib.sha256(wire).hexdigest(),
        "response_sha256": digest(prior["response"]),
    }


def run_guided_cases(
    input_path,
    output,
    endpoints,
    experience,
    max_attempts,
    *,
    export_only=False,
    first_pass_only=False,
    protocol_version=None,
    until_correct=None,
    retry_interrupted=False,
    workers=4,
    reuse_first_from=None,
    verified_step_receipts=None,
    reuse_matching_from=None,
):
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / "run.lock"), timeout=0):
        manifest = output / "manifest.json"
        frozen_config = read(manifest) if manifest.exists() else None
        template_version = frozen_config["template_version"] if frozen_config else (protocol_version or VERSION)
        if protocol_version and protocol_version != template_version:
            raise ValueError("the requested protocol differs from the frozen run")
        if until_correct is None:
            until_correct = frozen_config.get("until_correct", False) if frozen_config else template_version in PAIRED_DISCLOSURE_VERSIONS
        if until_correct and template_version not in TEACHER_DISCLOSURE_VERSIONS:
            raise ValueError("progress-based continuation requires guided protocol 3.9.0 or later")
        registry, trusted = verified_step_registry(output, verified_step_receipts, frozen_config)
        if registry and template_version not in VERIFIED_STEP_VERSIONS:
            raise ValueError("verified teaching steps require guided protocol 4.1 or later")
        with input_path.open("rb") as fp:
            source_sha = hashlib.file_digest(fp, "sha256").hexdigest()
        seen = set()
        has_direct_questions = False
        for source in inputs(input_path, template_version, trusted):
            if source["id"] in seen:
                raise ValueError("duplicate guided source id")
            seen.add(source["id"])
            if uses_direct_first(source, template_version):
                has_direct_questions = True
            elif first_pass_only:
                raise ValueError("the independent first pass requires final_only on every input question and its delivery instructions")
        if not seen:
            raise ValueError("empty source file")
        if export_only:
            if read(manifest)["input_sha256"] != source_sha:
                raise ValueError("input differs from frozen guided run")
            return export(input_path, output)
        if (
            not isinstance(experience, dict)
            or not isinstance(experience.get("version"), str)
            or not experience["version"].strip()
        ):
            raise ValueError("experience must declare its version")
        if any(
            not isinstance(experience.get(role, ""), str)
            for role in ("student", "coach", "disclosure_teacher")
        ):
            raise ValueError("experience role instructions must be strings")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if type(workers) is not int or not 1 <= workers <= 4:
            raise ValueError("worker limit must be between one and four")
        capabilities = {role: ep.validate() for role, ep in endpoints.items()}
        if template_version not in SUPPORTED_VERSIONS:
            raise ValueError(
                "unsupported frozen generator version; use offline export or its original release"
            )
        capacities = {}
        role_servers = {}
        for role, spec in capabilities.items():
            server = spec.get("url", "").rstrip("/").removesuffix("/v1").rstrip("/")
            role_servers[role] = server
            slots = spec.get("slots") or workers
            capacities[server] = min(slots, capacities.get(server, slots))
        server_limits = {server: BoundedSemaphore(slots) for server, slots in capacities.items()}
        if template_version in FIRST_ANSWER_VERSIONS:
            concurrency = min(workers, sum(capacities.values()))
        else:
            concurrency = min(workers, *capacities.values())
        config = dict(
            schema="guided-answer-run/v3",
            input_sha256=source_sha,
            source_count=len(seen),
            max_attempts=max_attempts,
            concurrency=concurrency,
            template_version=template_version,
            experience=experience,
            endpoints=capabilities,
        )
        if template_version in TEACHER_DISCLOSURE_VERSIONS:
            config["until_correct"] = until_correct
        if registry:
            config["verified_step_registry_id"] = digest(registry)
        if reuse_matching_from:
            if reuse_matching_from.resolve() == output.resolve():
                raise ValueError("cross-run replay requires a different source run")
            prior_bytes = (reuse_matching_from / "manifest.json").read_bytes()
            prior = json.loads(prior_bytes)
            for key in ("endpoints", "input_sha256", "experience", "verified_step_registry_id"):
                if prior.get(key) != config.get(key):
                    raise ValueError(f"cross-run replay requires the same {key}")
            config["reuse_matching"] = {"source_binding": digest(prior),
                                        "source_manifest_sha256": hashlib.sha256(prior_bytes).hexdigest()}
        if reuse_first_from:
            if reuse_first_from.resolve() == output.resolve():
                raise ValueError(
                    "the first-answer replay source must be a different frozen run"
                )
            prior_bytes = (reuse_first_from / "manifest.json").read_bytes()
            prior_manifest = json.loads(prior_bytes)
            if (
                prior_manifest.get("endpoints", {}).get("student")
                != capabilities["student"]
            ):
                raise ValueError(
                    "first-student replay requires the same endpoint, served model and generation capabilities"
                )
            config["reuse_first"] = {
                "source_binding": digest(prior_manifest),
                "source_manifest_sha256": hashlib.sha256(prior_bytes).hexdigest(),
            }
        if frozen_config is not None and frozen_config != config:
            raise ValueError(
                "configuration, input or experience differs from the frozen run"
            )
        if not manifest.exists():
            if registry:
                atomic_json(output / "verified-step-receipts.json", registry)
            atomic_json(manifest, config)
        binding = digest(config)

        def process(source, first_pass):
            folder = location(output, source["id"])
            receipt = folder / "case.json"
            interruption = folder / "interruption.json"
            current_checkpoint = None
            resumed_interruption = False
            if receipt.exists():
                if read(receipt)["binding"] != binding:
                    raise ValueError("completed case has different run binding")
                return

            def request(role, index, messages, schema):
                nonlocal current_checkpoint, resumed_interruption
                checkpoint = folder / f"{index:03}-{role}.json"
                current_checkpoint = checkpoint.name
                serving_role = "student" if role in ("student", "final_answer") else "coach"
                endpoint = endpoints[serving_role]
                payload = (endpoint.final_answer_payload(messages) if role == "final_answer"
                           else endpoint.payload(messages, schema))
                syntax = source.get("direct_answer_syntax")
                first_direct = role == "student" and index == 1 and uses_direct_first(source, template_version)
                if syntax and (role == "final_answer" or first_direct):
                    payload["grammar"] = DIRECT_ANSWER_GRAMMARS[syntax]
                identity = digest(
                    {"binding": binding, "id": source["id"], "payload": payload}
                )
                saved = read(checkpoint) if checkpoint.exists() else {}
                if saved and saved["identity"] != identity:
                    raise ValueError("request differs from its saved checkpoint")
                if saved.get("status") == "complete":
                    return saved["response"]
                if saved and not retry_interrupted and not can_resume_admission(saved, binding, source["id"], role, index):
                    raise RuntimeError(
                        "request outcome unresolved; explicit retry required"
                    )
                if saved and interruption.exists():
                    prior_stop = read(interruption)
                    resumed_interruption = (
                        prior_stop.get("binding") == binding and prior_stop.get("id") == source["id"]
                        and prior_stop.get("checkpoint", current_checkpoint) == current_checkpoint
                    )
                previous = saved.get("attempts", [])
                replayed = None
                if (reuse_first_from and role == "student" and index == 1
                        and (template_version != "4.4.0" or first_direct)):
                    replayed = replay_first(
                        reuse_first_from,
                        source["id"],
                        payload,
                        config["reuse_first"]["source_binding"],
                    )
                elif reuse_matching_from:
                    replayed = replay_matching_call(reuse_matching_from, source["id"], role, index, payload,
                                                    config["reuse_matching"]["source_binding"])
                if replayed:
                    response, origin = replayed
                    atomic_json(
                        checkpoint,
                        dict(
                            identity=identity,
                            role=role,
                            attempt=index,
                            request=payload,
                            status="complete",
                            origin=origin,
                            response=response,
                            attempts=[*previous, {"status": "replayed", "new_model_calls": 0}],
                        ),
                    )
                    return response
                event = dict(
                    identity=identity,
                    role=role,
                    attempt=index,
                    request=payload,
                    status="pending",
                    attempts=[*previous, {"status": "pending"}],
                )
                atomic_json(checkpoint, event)
                try:
                    with server_limits[role_servers[serving_role]]:
                        response = endpoint.complete(payload)
                except GuidedAdmissionTimeout:
                    event["status"] = "awaiting_capacity"
                    event["attempts"][-1] = {
                        "status": "awaiting_capacity", "code": ModelQueueTimeout.code, "model_request_sent": False,
                    }
                    atomic_json(checkpoint, event)
                    raise
                except Exception as err:
                    event["status"] = "interrupted"
                    event["attempts"][-1] = {
                        "status": "interrupted",
                        "error": type(err).__name__,
                        "http_status": getattr(err, "code", None),
                        "dispatch": "unknown",
                    }
                    atomic_json(checkpoint, event)
                    raise
                event.update(status="complete", response=response)
                event["attempts"][-1] = {
                    "status": "complete",
                    "usage": response.get("usage", {}),
                }
                atomic_json(checkpoint, event)
                return response

            try:
                result = GenerateGuidedAnswer(request).run(
                    source,
                    experience,
                    max_attempts,
                    protocol_version=template_version,
                    first_pass_only=first_pass,
                    until_correct=until_correct,
                )
            except Exception as error:
                atomic_json(
                    interruption,
                    dict(
                        id=source["id"],
                        binding=binding,
                        error=type(error).__name__,
                        detail=str(error),
                        status="awaiting_capacity" if isinstance(error, GuidedAdmissionTimeout) else "interrupted",
                        checkpoint=current_checkpoint,
                    ),
                )
                return source["id"]
            if result is None:
                if resumed_interruption:
                    interruption.unlink(missing_ok=True)
                return
            atomic_json(
                receipt, {**result, "binding": binding, "source_sha256": digest(source)}
            )
            interruption.unlink(missing_ok=True)

        failed_first = set()
        def run_pass(first_pass):
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                pending = set()
                for source in inputs(input_path, template_version, trusted):
                    if first_pass and source.get("answer_requirement") != "final_only":
                        continue
                    if source["id"] in failed_first:
                        continue
                    pending.add(pool.submit(process, source, first_pass))
                    if len(pending) == concurrency:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for item in done:
                            failed = item.result()
                            if first_pass and failed:
                                failed_first.add(failed)
                for item in pending:
                    failed = item.result()
                    if first_pass and failed:
                        failed_first.add(failed)

        if has_direct_questions:
            run_pass(True)
            first_summary = export(input_path, output)
            if first_pass_only:
                first_summary["first_pass_complete"] = not failed_first
                atomic_json(output / "summary.json", first_summary)
                return first_summary
        run_pass(False)
        return export(input_path, output)


def export(input_path, output):
    manifest = read(output / "manifest.json")
    _, trusted = verified_step_registry(output, None, manifest)
    binding = digest(manifest)
    counts = dict(accepted=0, unverified=0, quarantine=0, interrupted=0, pending=0)
    accepted_routes = dict(nonthinking_direct=0, reviewed_solution=0)
    if manifest["template_version"] in TEACHER_DISCLOSURE_VERSIONS:
        counts["awaiting_guidance"] = 0
    if manifest["template_version"] in FINAL_CONTINUATION_VERSIONS:
        accepted_routes["guided_reasoning"] = 0
        accepted_routes["unaided_reasoning"] = 0
    calls = dict(new_completed=0, replayed_first=0, interrupted_or_pending=0)
    admission_timeouts = 0
    with TemporaryDirectory(dir=output) as temp:
        scratch = Path(temp)
        with ExitStack() as stack:
            streams = {
                name: stack.enter_context(
                    (scratch / f"{name}.jsonl").open(
                        "w", encoding="utf-8", newline="\n"
                    )
                )
                for name in ("accepted", "unverified", "quarantine", "feedback",
                             *(("awaiting_guidance", "teacher_feedback") if "awaiting_guidance" in counts else ()))
            }

            def emit(name, row):
                streams[name].write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )

            for source in inputs(input_path, manifest["template_version"], trusted):
                folder = location(output, source["id"])
                outstanding = {}
                for cp in folder.glob("[0-9]*-*.json"):
                    event = read(cp)
                    if event["status"] != "complete":
                        serial, role = cp.stem.split("-", 1)
                        outstanding[cp.name] = can_resume_admission(event, binding, source["id"], role, int(serial))
                    for item in event["attempts"]:
                        status = item["status"]
                        if unsent_admission(item):
                            admission_timeouts += 1
                        elif status == "replayed":
                            kind = ("replayed_matching" if event.get("origin", {}).get("kind") == "replayed_matching_call"
                                    else "replayed_first")
                            calls[kind] = calls.get(kind, 0) + 1
                        elif status == "complete":
                            calls["new_completed"] += 1
                        elif status in {"interrupted", "pending"}:
                            calls["interrupted_or_pending"] += 1
                if not (folder / "case.json").exists():
                    path = folder / "interruption.json"
                    stop = read(path) if path.exists() else {}
                    if capacity_stop(stop, outstanding, binding, source["id"]):
                        counts["awaiting_capacity"] = counts.get("awaiting_capacity", 0) + 1
                    else:
                        counts["interrupted" if stop or outstanding else "pending"] += 1
                        if stop:
                            emit("quarantine", stop)
                    continue
                case = read(folder / "case.json")
                if case["binding"] != binding or case["source_sha256"] != digest(
                    source
                ):
                    raise ValueError("case differs from its original source or run")
                status = case["status"]
                counts[status] += 1
                if status == "accepted" and manifest["template_version"] in FIRST_ANSWER_VERSIONS:
                    accepted_routes[case["training_route"]] += 1
                public_keys = (
                    "id",
                    "question",
                    "source",
                    "source_reasoning",
                    "gold",
                    "mode",
                    "answer_requirement",
                    "training_route",
                    "final_answer_provenance",
                    "status",
                    "verification",
                    "answer",
                    "messages",
                    "experience_version",
                    "disclosures",
                    "shared_student_findings",
                    "continuation",
                )
                emit(
                    status,
                    {
                        **{key: case[key] for key in public_keys if key in case},
                        "case_archive": (folder / "case.json")
                        .relative_to(output)
                        .as_posix(),
                    },
                )
                for attempt in case["attempts"]:
                    paired_protocol = manifest["template_version"] in PAIRED_DISCLOSURE_VERSIONS
                    delivery = disclosure_delivery(case, attempt, folder) if paired_protocol else None
                    for work in attempt.get("disclosure_work", []):
                        published = bool(work.get("validated_citations") and attempt.get("disclosure"))
                        if paired_protocol:
                            published = bool(delivery and work.get("review_checkpoint")
                                             == attempt["disclosure"]["review_checkpoint"])
                        emit("teacher_feedback", {
                            "id": case["id"], "case_archive": (folder / "case.json").relative_to(output).as_posix(),
                            "attempt": attempt["attempt"], "revision": work["revision"],
                            "draft": work.get("draft"), "assessment": work.get("assessment"),
                            **({"verified_operation": work["verified_operation"]} if "verified_operation" in work else {}),
                            "validated_citations": work.get("validated_citations", []),
                            "issue": work.get("issue"),
                            "published": published,
                            **({key: work[key] for key in ("review_checkpoint", "public_text", "question_quotations")
                                if key in work} if paired_protocol else {}),
                            **({"delivery": delivery} if paired_protocol and published else {}),
                            "experience_version": case["experience_version"],
                        })
                    review = attempt.get("coach") or {}
                    check = attempt["check"]
                    emit(
                        "feedback",
                        dict(
                            id=case["id"],
                            question=case["question"],
                            attempt=attempt["attempt"],
                            student_content=attempt.get(
                                "candidate_content", attempt["student"]["content"]
                            ),
                            student_suffix=attempt["student"]["content"],
                            answer_matches=check["answer_matches"],
                            format_ok=check["format_ok"],
                            issue=check["issue"],
                            verdict=review.get("verdict"),
                            diagnosis=review.get("diagnosis"),
                            hint_to_student=attempt["hint_to_student"],
                            mode=case["mode"],
                            status=status,
                            experience_version=case["experience_version"],
                        ),
                    )
        for path in scratch.glob("*.jsonl"):
            os.replace(path, output / path.name)
    summary = {
        "binding": binding,
        "source_count": manifest["source_count"],
        "counts": counts,
        **({"accepted_by_route": accepted_routes} if manifest["template_version"] in FIRST_ANSWER_VERSIONS else {}),
        "model_calls": calls,
        **({"admission_timeouts": admission_timeouts} if admission_timeouts else {}),
        "complete": counts["pending"] == counts["interrupted"] == counts.get("awaiting_capacity", 0) == 0,
    }
    atomic_json(output / "summary.json", summary)
    return summary
