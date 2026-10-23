"""Supported model API dialect identifiers, independent from request serialization."""

API_FORMATS = ("chat", "responses", "anthropic")


def validate_api_format(value: str) -> str:
    if value not in API_FORMATS:
        raise ValueError("invalid_api_format")
    return value
