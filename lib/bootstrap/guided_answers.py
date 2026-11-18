"""Assemble the local answering endpoint, private tutor and durable guided workflow."""

from pathlib import Path
from urllib.parse import urlsplit

from lib.contexts.workflows.infrastructure.guided_answer_records import run_guided_cases
from lib.contexts.workflows.infrastructure.guided_llama import GuidedChatEndpoint
from lib.model_request_scheduler import SharedModelScheduler, model_pool_identity


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
    retry_interrupted=False,
    workers=4,
    reuse_first_from=None,
):
    endpoints = {}
    if not export_only:
        if not student_url or not student_model:
            raise ValueError("student URL and model are required")
        coach_url = coach_url or student_url
        # Both roles and independent guided runs draw from the same four leases.
        pool = SharedModelScheduler(
            Path(__file__).resolve().parents[2],
            model_pool_identity("guided://local", "", "guided-answers"),
            policy={"max_concurrency": 4, "request_queue_timeout_seconds": 3600},
        )
        endpoints["student"] = GuidedChatEndpoint(
            student_url,
            student_model,
            student_api_key_env,
            max_tokens,
            local=True,
            context=context_tokens,
            thinking=False,
            pool=pool,
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
        )
    return run_guided_cases(
        input_path,
        output,
        endpoints,
        experience if experience is not None else {"version": "initial"},
        max_attempts,
        export_only=export_only,
        retry_interrupted=retry_interrupted,
        workers=workers,
        reuse_first_from=reuse_first_from,
    )
