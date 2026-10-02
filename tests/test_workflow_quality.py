"""Shared safety checks for text and conversation records."""

from lib.domain.workflow_quality import conversation_issue, text_issue


def test_bearer_authorization_values_are_rejected_before_model_use():
    token = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
    assert text_issue(f"Authorization: Bearer {token}") == "potential_secret"
    assert text_issue(f'{{"Authorization":"Bearer {token}"}}') == "potential_secret"
    assert conversation_issue([
        {"role": "user", "content": "Please summarize this trace."},
        {"role": "assistant", "content": "Done.",
         "metadata": {"request_headers": {"Authorization": f"Bearer {token}"}}},
    ]) == "potential_secret"


def test_bearer_scheme_discussion_and_placeholder_are_not_credentials():
    assert text_issue("Set Authorization: Bearer <token> in the request.") is None
    assert text_issue("The Bearer token format is part of the Authorization header.") is None
