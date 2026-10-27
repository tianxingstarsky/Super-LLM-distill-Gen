"""Read SDK failure types and response headers without guessing from class names."""


def response_headers(error):
    from httpx2 import Headers

    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        headers = getattr(error, "headers", None)
    return Headers(headers or {})


def provider_failure_kind(error):
    # Lazy imports keep SDKs out of configuration reads and pure use-case tests.
    import anthropic
    import httpx
    import httpx2
    import openai

    if isinstance(error, (TimeoutError, openai.APITimeoutError, anthropic.APITimeoutError,
                          httpx.TimeoutException, httpx2.TimeoutException)):
        return "timeout"
    if isinstance(error, (ConnectionError, openai.APIConnectionError, anthropic.APIConnectionError,
                          httpx.NetworkError, httpx2.NetworkError,
                          httpx.RemoteProtocolError, httpx2.RemoteProtocolError,
                          httpx.ProxyError, httpx2.ProxyError)):
        return "connection"
    if isinstance(error, (httpx.StreamError, httpx2.StreamError,
                          httpx.LocalProtocolError, httpx2.LocalProtocolError)):
        return "stream_state"
    if isinstance(error, (openai.APIError, anthropic.APIError)):
        return "api"
    return None
