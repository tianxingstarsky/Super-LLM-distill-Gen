"""No-network SDK clients shared by independently runnable stream tests."""
import json
from pathlib import Path

import httpx

from lib.llm_client import ChatClient

FIXTURES = Path(__file__).parent / "fixtures/model_streaming"


def fixture(api):
    return (FIXTURES / f"{api}.sse").read_bytes()


def sdk_client(api, handler):
    client = ChatClient("https://offline.invalid/v1", "SUPER-SECRET-KEY", "writer", api_format=api)
    client.client.close()
    kwargs = {"base_url": "https://offline.invalid/v1", "api_key": "SUPER-SECRET-KEY",
              "max_retries": 0, "http_client": httpx.Client(transport=httpx.MockTransport(handler))}
    if api == "anthropic":
        from anthropic import Anthropic, _base_client
        http = getattr(_base_client, "httpx2", httpx)
        if http is not httpx:
            kwargs["http_client"].close()
            def adapt(request):
                response = handler(request)
                class Bridge(http.SyncByteStream):
                    def __iter__(self):
                        try:
                            yield from response.stream
                        except httpx.ReadError:
                            raise http.ReadError("offline interrupted stream") from None
                    def close(self):
                        response.close()
                return http.Response(response.status_code, headers=dict(response.headers), stream=Bridge())
            kwargs["http_client"] = http.Client(transport=http.MockTransport(adapt))
        client.client = Anthropic(**kwargs)
    else:
        from openai import OpenAI
        client.client = OpenAI(**kwargs)
    return client


def sse_response(data):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)


def unfinished(api):
    data = fixture(api)
    if api == "chat":
        data = data.replace(b'"finish_reason":"stop"', b'"finish_reason":null')
        return data.split(b'data: {"id":"chat-test","object":"chat.completion.chunk","created":1,"model":"writer","choices":[]')[0]
    marker = b"event: response.completed" if api == "responses" else b"event: message_delta"
    return data.split(marker)[0]
