"""Assemble the bounded local queue of reviewer-proposed prefix aliases."""
from lib.contexts.review.api import ControlTextSuggestionApplication
from lib.contexts.review.infrastructure.control_text_suggestions import ControlTextSuggestionRepository


def control_text_suggestion_application(output):
    return ControlTextSuggestionApplication(ControlTextSuggestionRepository(output))


def request_answer_review(runtime, key, context, answer, prompt_id, **options):
    from lib.contexts.review.infrastructure.control_text_suggestions import request_answer_review as request
    return request(runtime, key, context, answer, prompt_id, **options)
