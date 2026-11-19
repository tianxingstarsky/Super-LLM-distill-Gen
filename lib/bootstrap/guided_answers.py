"""Assemble the local answering endpoint, private tutor and durable guided workflow."""

from functools import partial
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases
from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint
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
    max_attempts=3,
    context_tokens=131072,
    max_tokens=32768,
    coach_thinking=False,
    experience=None,
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
            pool=pool,
            slot_pool_factory=slot_pool,
        )
    return run_guided_cases(
        input_path,
        output,
        endpoints,
        experience if experience is not None else {"version": "initial"},
        max_attempts,
        export_only=export_only,
        first_pass_only=first_pass_only,
        protocol_version=protocol_version,
        until_correct=until_correct,
        retry_interrupted=retry_interrupted,
        workers=workers,
        reuse_first_from=reuse_first_from,
        verified_step_receipts=verified_step_receipts,
        reuse_matching_from=reuse_matching_from,
    )
