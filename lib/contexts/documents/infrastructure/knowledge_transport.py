"""Official Qdrant/OpenAI clients; only source policy remains in our knowledge driver."""
from contextlib import closing, contextmanager

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _bounded_response(http):
    """Limit streamed bytes without replacing SDK proxy/transport selection."""
    class LimitedStream(http.SyncByteStream):
        def __init__(self, stream):
            self.stream = stream
        def __iter__(self):
            total = 0
            for chunk in self.stream:
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise ValueError("knowledge_response_too_large")
                yield chunk
        def close(self):
            self.stream.close()

    def limit(response):
        response.stream = LimitedStream(response.stream)
    return limit


@contextmanager
def _qdrant(config):
    import httpx
    from qdrant_client import QdrantClient
    with closing(QdrantClient(url=config["url"], api_key=config["api_key"] or None, timeout=30,
            check_compatibility=False, follow_redirects=False,
            event_hooks={"response": [_bounded_response(httpx)]})) as client:
        yield client


def _failure(error):
    # SDKs may wrap transport errors. Preserve the existing bounded-response code.
    pending, visited = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if type(current) is ValueError and str(current) == "knowledge_response_too_large":
            raise ValueError("knowledge_response_too_large") from None
        pending.extend(value for value in (current.__cause__, current.__context__,
                         getattr(current, "source", None)) if isinstance(value, BaseException))
    raise ValueError("knowledge_connection_unavailable") from None


def collection_info(config):
    try:
        with _qdrant(config) as client:
            return {"result": client.get_collection(config["collection"]).model_dump(mode="json")}
    except Exception as error:
        _failure(error)


def query_points(config, vector, limit):
    try:
        with _qdrant(config) as client:
            result = client.query_points(collection_name=config["collection"], query=vector,
                using=config["vector_name"] or None, limit=limit, with_payload=True, with_vectors=False)
            return {"result": result.model_dump(mode="json")}
    except Exception as error:
        _failure(error)


def embed_query(config, query):
    import httpx2
    from openai import OpenAI, Omit
    try:
        with OpenAI(base_url=config["embedding_url"], api_key=config["embedding_key"] or "local-without-auth",
                max_retries=0, timeout=30,
                http_client=httpx2.Client(event_hooks={"response": [_bounded_response(httpx2)]},
                                          follow_redirects=False)) as client:
            return client.embeddings.create(model=config["embedding_model"], input=query,
                encoding_format="float",
                extra_headers={} if config["embedding_key"] else {"Authorization": Omit()}).model_dump(mode="json")
    except Exception as error:
        _failure(error)
