"""Provider SDK HTTP settings for an explicitly selected network route."""
from lib.domain.model_network import validate_network_route


def sdk_network_options(network_route: str = "system", *, api_format: str = "chat") -> dict:
    """Keep SDK defaults for system; direct ignores env on this client only.

    Each SDK's own default transport preserves its connection limits, timeout
    and TLS verification defaults. No environment or process proxy is changed.
    """
    if validate_network_route(network_route) == "system":
        return {}
    if api_format == "anthropic":
        from anthropic import DefaultHttpxClient
    else:
        from openai import DefaultHttpxClient
    # Some SDK releases build proxy mounts themselves before delegating to
    # HTTPX. Explicit empty scheme mounts also override those SDK mounts;
    # trust_env=False alone is insufficient for that implementation.
    return {"http_client": DefaultHttpxClient(trust_env=False,
        mounts={"http://": None, "https://": None, "all://": None})}
