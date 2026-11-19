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

from ..application.generate_guided_answer import GenerateGuidedAnswer
from ..domain.experience_evolution import digest
from ..domain.guided_answers import validate_source
from .guided_llama import DIRECT_ANSWER_GRAMMARS, wire_body
from lib.io_utils import atomic_json
from lib.prompts.workflow_guided_answers import VERSION

VERIFIED_STEP_VERSIONS = {"4.1.0", "4.1.1"}
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
    until_correct=False,
    retry_interrupted=False,
    workers=4,
    reuse_first_from=None,
    verified_step_receipts=None,
):
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / "run.lock"), timeout=0):
        manifest = output / "manifest.json"
        frozen_config = read(manifest) if manifest.exists() else None
        template_version = frozen_config["template_version"] if frozen_config else (protocol_version or VERSION)
        if protocol_version and protocol_version != template_version:
            raise ValueError("the requested protocol differs from the frozen run")
        if until_correct and template_version not in TEACHER_DISCLOSURE_VERSIONS:
            raise ValueError("progress-based continuation requires guided protocol 3.9.0 or later")
        registry, trusted = verified_step_registry(output, verified_step_receipts, frozen_config)
        if registry and template_version not in VERIFIED_STEP_VERSIONS:
            raise ValueError("verified teaching steps require a supported 4.1 protocol")
        with input_path.open("rb") as fp:
            source_sha = hashlib.file_digest(fp, "sha256").hexdigest()
        seen = set()
        has_direct_questions = False
        for source in inputs(input_path, template_version, trusted):
            if source["id"] in seen:
                raise ValueError("duplicate guided source id")
            seen.add(source["id"])
            if source.get("answer_requirement") == "final_only":
                has_direct_questions = True
            elif first_pass_only:
                raise ValueError("the independent first pass requires final_only on every input question")
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
            if receipt.exists():
                if read(receipt)["binding"] != binding:
                    raise ValueError("completed case has different run binding")
                return

            def request(role, index, messages, schema):
                checkpoint = folder / f"{index:03}-{role}.json"
                serving_role = "student" if role in ("student", "final_answer") else "coach"
                endpoint = endpoints[serving_role]
                payload = (endpoint.final_answer_payload(messages) if role == "final_answer"
                           else endpoint.payload(messages, schema))
                syntax = source.get("direct_answer_syntax")
                if syntax and (role == "final_answer" or (role == "student" and index == 1)):
                    payload["grammar"] = DIRECT_ANSWER_GRAMMARS[syntax]
                identity = digest(
                    {"binding": binding, "id": source["id"], "payload": payload}
                )
                saved = read(checkpoint) if checkpoint.exists() else {}
                if saved and saved["identity"] != identity:
                    raise ValueError("request differs from its saved checkpoint")
                if saved.get("status") == "complete":
                    return saved["response"]
                if saved and not retry_interrupted:
                    raise RuntimeError(
                        "request outcome unresolved; explicit retry required"
                    )
                previous = saved.get("attempts", [])
                if reuse_first_from and role == "student" and index == 1:
                    response, origin = replay_first(
                        reuse_first_from,
                        source["id"],
                        payload,
                        config["reuse_first"]["source_binding"],
                    )
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
                            attempts=[{"status": "replayed", "new_model_calls": 0}],
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
                except Exception as err:
                    event["status"] = "interrupted"
                    event["attempts"][-1] = {
                        "status": "interrupted",
                        "error": type(err).__name__,
                        "http_status": getattr(err, "code", None),
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
                    folder / "interruption.json",
                    dict(
                        id=source["id"],
                        binding=binding,
                        error=type(error).__name__,
                        detail=str(error),
                        status="interrupted",
                    ),
                )
                return source["id"]
            if result is None:
                return
            atomic_json(
                receipt, {**result, "binding": binding, "source_sha256": digest(source)}
            )
            (folder / "interruption.json").unlink(missing_ok=True)

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
                first_summary["first_pass_complete"] = first_summary["counts"]["interrupted"] == 0
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
                for cp in folder.glob("[0-9]*-*.json"):
                    event = read(cp)
                    for item in event["attempts"]:
                        status = item["status"]
                        if status == "replayed":
                            calls["replayed_first"] += 1
                        elif status == "complete":
                            calls["new_completed"] += 1
                        elif status in {"interrupted", "pending"}:
                            calls["interrupted_or_pending"] += 1
                if not (folder / "case.json").exists():
                    broken = (folder / "interruption.json").exists()
                    status = "interrupted" if broken else "pending"
                    counts[status] += 1
                    if broken:
                        emit("quarantine", read(folder / "interruption.json"))
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
                    for work in attempt.get("disclosure_work", []):
                        emit("teacher_feedback", {
                            "id": case["id"], "case_archive": (folder / "case.json").relative_to(output).as_posix(),
                            "attempt": attempt["attempt"], "revision": work["revision"],
                            "draft": work.get("draft"), "assessment": work.get("assessment"),
                            **({"verified_operation": work["verified_operation"]} if "verified_operation" in work else {}),
                            "validated_citations": work.get("validated_citations", []),
                            "issue": work.get("issue"),
                            "published": bool(work.get("validated_citations") and attempt.get("disclosure")),
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
        "complete": counts["pending"] == counts["interrupted"] == 0,
    }
    atomic_json(output / "summary.json", summary)
    return summary
