"""Assemble the local answering endpoint, private tutor and durable guided workflow."""

from functools import partial
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from lib.contexts.workflows.infrastructure.guided_answer_records import location, run_guided_cases
from lib.contexts.workflows.infrastructure.guided_context_recovery import seal_context_proof
from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint, SAMPLING_PROFILES
from lib.model_request_scheduler import SharedModelScheduler, model_pool_identity


def _native_slot_pool(root, url, slots):
    endpoint = urlsplit(url)
    # The same loopback server may be named differently by separate runs.
    authority = endpoint.netloc
    if endpoint.hostname in {"127.0.0.1", "localhost", "::1"}:
        authority = "localhost" + (f":{endpoint.port}" if endpoint.port else "")
    server = urlunsplit((endpoint.scheme, authority, endpoint.path, "", ""))
    limit = min(slots, 4)
    pool = SharedModelScheduler(
        root, model_pool_identity(server, "", "guided-native-slots"),
        policy={"max_concurrency": limit, "request_queue_timeout_seconds": 3600},
    )
    pool.set_observed_capacity(limit)
    return pool


def seal_guided_overflows(parent, proof, selected):
    return seal_context_proof(parent, proof, selected, location)


def generate_guided_cases(
    input_path,
    output,
    *,
    student_url=None,
    student_model=None,
    coach_url=None,
    coach_model=None,
    student_api_key_env=None,
    coach_api_key_env=None,
    continuation_model=None,
    continuation_url=None,
    continuation_api_key_env=None,
    continuation_sampling=None,
    max_attempts=3,
    context_tokens=131072,
    max_tokens=32768,
    coach_thinking=None,
    student_sampling=None,
    coach_sampling=None,
    experience=None,
    export_only=False,
    first_pass_only=False,
    prefetch_worked_first=False,
    case_ids=None,
    protocol_version=None,
    until_correct=None,
    retry_interrupted=False,
    workers=4,
    reuse_first_from=None,
    reuse_worked_first_from=None,
    verified_step_receipts=None,
    reuse_matching_from=None,
    reuse_original_runs=None,
    recover_context_from=None,
    context_proof=None,
    context_proof_sha256=None,
    historical_first_from=None,
    historical_first_input=None,
):
    endpoints = {}
    if not export_only:
        if not student_url or not student_model:
            raise ValueError("student URL and model are required")
        coach_url = coach_url or student_url
        # Both roles and independent guided runs draw from the same four leases.
        root = Path(__file__).resolve().parents[2]
        pool = SharedModelScheduler(
            root,
            model_pool_identity("guided://local", "", "guided-answers"),
            policy={"max_concurrency": 4, "request_queue_timeout_seconds": 3600},
        )
        slot_pool = partial(_native_slot_pool, root)
        endpoints["student"] = GuidedChatEndpoint(
            student_url,
            student_model,
            student_api_key_env,
            max_tokens,
            local=True,
            context=context_tokens,
            thinking=False,
            sampling=student_sampling,
            pool=pool,
            slot_pool_factory=slot_pool,
        )
        if not coach_model and coach_url != student_url:
            raise ValueError("an independent tutor endpoint must name its model")
        coach_local = (
            urlsplit(coach_url).hostname in {"127.0.0.1", "localhost", "::1"}
            or coach_url == student_url
        )
        endpoints["coach"] = GuidedChatEndpoint(
            coach_url,
            coach_model or student_model,
            coach_api_key_env,
            max_tokens,
            local=coach_local,
            context=context_tokens,
            thinking=coach_thinking,
            sampling=coach_sampling,
            pool=pool,
            slot_pool_factory=slot_pool,
        )
        if continuation_model:
            if continuation_model == student_model:
                raise ValueError("name a distinct continuation model")
            url = continuation_url or coach_url
            endpoints["continuation"] = GuidedChatEndpoint(
                url, continuation_model, continuation_api_key_env or (coach_api_key_env if url == coach_url else None),
                max_tokens, local=True, context=context_tokens, thinking=False,
                sampling=continuation_sampling or (coach_sampling if continuation_model == (coach_model or student_model) else None),
                pool=pool, slot_pool_factory=slot_pool,
            )
        elif continuation_url or continuation_api_key_env or continuation_sampling:
            raise ValueError("a continuation endpoint requires its model name")
    return run_guided_cases(
        input_path,
        output,
        endpoints,
        experience if experience is not None else {"version": "initial"},
        max_attempts,
        export_only=export_only,
        first_pass_only=first_pass_only,
        prefetch_worked_first=prefetch_worked_first,
        case_ids=case_ids,
        protocol_version=protocol_version,
        until_correct=until_correct,
        retry_interrupted=retry_interrupted,
        workers=workers,
        reuse_first_from=reuse_first_from,
        reuse_worked_first_from=reuse_worked_first_from,
        verified_step_receipts=verified_step_receipts,
        reuse_matching_from=reuse_matching_from,
        reuse_original_runs=reuse_original_runs,
        recover_context_from=recover_context_from,
        context_proof=context_proof,
        context_proof_sha256=context_proof_sha256,
        historical_first_from=historical_first_from,
        historical_first_input=historical_first_input,
    )
