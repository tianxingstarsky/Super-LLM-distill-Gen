"""Bind teacher experience to the same local server slot pool used by the guided workload."""

from urllib.parse import urlsplit

from lib.bootstrap.backends import backend_application
from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application
from lib.bootstrap.guided_answers import _native_slot_pool
from lib.contexts.models.infrastructure.request_runtime import ChatClient
from lib.contexts.workflows.infrastructure.guided_teacher_feedback import (
    TeacherFeedbackStore, reviewed_disclosure_for_learning, verify_applied,
)


def teacher_feedback_store(root):
    return TeacherFeedbackStore(root)


def verify_teacher_application(run, experience, selected):
    return verify_applied(run, experience, selected)


def local_teacher_induction(library, runtime_root, model, *, local_url=None, backend=None, context_tokens, max_tokens):
    if backend:
        local_url = backend_application(runtime_root).merged_backends()[backend]["base_url"]
    url = urlsplit(local_url or "")
    if (url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("teacher feedback induction requires a loopback model endpoint")
    if not model or not 0 < max_tokens < context_tokens:
        raise ValueError("a local model, positive output capacity and a larger context capacity are required")
    binding = {"backend": local_url.rstrip("/"), "model": model, "max_output_tokens": max_tokens,
               "context_window_tokens": context_tokens, "reasoning": {"mode": "disabled"}}
    def client_factory(selected):
        client = ChatClient(selected["backend"], "", selected["model"], context_window_tokens=context_tokens,
            reasoning=selected["reasoning"], model_info={"reasoning_format": "llama_cpp_qwen"},
            scheduler=_native_slot_pool(runtime_root, selected["backend"], 1), network_route="direct")
        return client, selected["model"]
    return google_reasoning_bank_application(binding, root=library, client_factory=client_factory)
