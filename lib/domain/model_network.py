"""Explicit per-service routing; never infer a proxy bypass after a failure."""


def validate_network_route(value: str = "system") -> str:
    if type(value) is not str or value not in {"system", "direct"}:
        raise ValueError("invalid_model_network_route")
    return value
