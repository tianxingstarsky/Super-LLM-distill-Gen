"""Bounded Brave Search adapter; only the explicitly supplied public query leaves the host."""
from __future__ import annotations

import html
import http.client
import ipaddress
import json
import os
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from lib.domain.web_research import validate_web_research
from lib.domain.workflow_quality import text_issue


KEY_ENV = "DATAFORGE_BRAVE_SEARCH_API_KEY"
HOST = "api.search.brave.com"
PATH = "/res/v1/web/search"
MAX_RESPONSE_BYTES = 256 * 1024


def configured() -> bool:
    return bool(os.environ.get(KEY_ENV, "").strip())


def check_connection() -> str:
    """Probe Brave on request without returning credentials or provider text.

    A fixed public query keeps private briefs and configured research topics out
    of connection checks. A valid empty result is still a working connection.
    """
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        return "not_configured"
    try:
        _search_one("Brave Search", 1, key)
    except Exception:
        return "unavailable"
    return "ready"


def _plain(value, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"<[^>]*>", " ", html.unescape(value))
    value = " ".join(value.split())[:max_chars]
    return value if not text_issue(value) else None


def _public_url(value) -> str | None:
    if (not isinstance(value, str) or len(value) > 1200 or text_issue(value)
            or re.search(r"[\x00-\x1f\x7f]", value)):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or len(parsed.query) > 256):
            return None
        host = parsed.hostname.rstrip(".").lower()
        if (not host.isascii() or "." not in host
                or host.endswith((".local", ".localhost", ".internal", ".test", ".lan", ".home", ".corp"))):
            return None
        try:
            # Search snippets should point to named public sites. Numeric IPs,
            # including browser-compatible short or decimal forms, are unsafe
            # links even though this adapter never downloads result pages.
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        labels = host.split(".")
        if (not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels)
                or not re.fullmatch(r"(?:[a-z]{2,63}|xn--[a-z0-9-]{4,59})", labels[-1])):
            return None
    except ValueError:
        return None
    # Fragments have no server-side provenance; preserve query when needed for
    # the real source URL, but never download or follow any result URL.
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


def _search_one(query: str, count: int, key: str) -> list[dict[str, str]]:
    path = PATH + "?" + urlencode({"q": query, "count": count,
                                  "safesearch": "strict", "result_filter": "web",
                                  "spellcheck": "false", "text_decorations": "false"})
    connection = http.client.HTTPSConnection(HOST, timeout=8)
    try:
        connection.request("GET", path, headers={"Accept": "application/json",
                                                    "X-Subscription-Token": key})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("web_search_provider_error")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("web_search_provider_error")
        data = json.loads(body)
    except Exception:
        # Provider errors may include the query or bearer token. Never persist
        # or surface their raw text through workflow state/events.
        raise ValueError("web_search_provider_error") from None
    finally:
        try:
            connection.close()
        except Exception:
            pass
    web = data.get("web") if isinstance(data, dict) else None
    raw_results = web.get("results") if isinstance(web, dict) else None
    if not isinstance(raw_results, list):
        raise ValueError("web_search_provider_error")
    results = []
    seen = set()
    for row in raw_results:
        if not isinstance(row, dict):
            continue
        url = _public_url(row.get("url"))
        title = _plain(row.get("title"), 200)
        snippet = _plain(row.get("description"), 500)
        if not url or not title or not snippet or url in seen:
            continue
        seen.add(url)
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= count:
            break
    return results


def search(config: dict, *, before_query=None, allow_empty: bool = False) -> list[dict[str, str]]:
    """Search up to five explicit public topics; never fetch result pages."""
    config = validate_web_research(config, brief="enabled")
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise ValueError("web_search_not_configured")
    results = []
    seen = set()
    queries = [config["query"], *config.get("more_queries", [])]
    for query in queries:
        if before_query is not None:
            before_query()
        for row in _search_one(query, config["count"], key):
            if row["url"] not in seen:
                seen.add(row["url"])
                results.append({**row, "query": query} if len(queries) > 1 else row)
    if not results and not allow_empty:
        raise ValueError("web_search_no_safe_results")
    return results


def validate_search_topic_document(config: dict, document) -> dict:
    """Check a per-topic checkpoint, including an empty but valid response."""
    config = validate_web_research(config, brief="enabled")
    if config.get("more_queries"):
        raise ValueError("web_research_integrity_error")
    if (not isinstance(document, dict) or document.get("provider") != "brave"
            or document.get("query") != config["query"]
            or set(document) != {"provider", "query", "retrieved_at", "results"}):
        raise ValueError("web_research_integrity_error")
    _valid_timestamp(document.get("retrieved_at"))
    _valid_results(document.get("results"), [config["query"]], config["count"], minimum=0)
    return document


def _valid_timestamp(retrieved_at) -> None:
    if (not isinstance(retrieved_at, str) or len(retrieved_at) > 40
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)",
                                retrieved_at)):
        raise ValueError("web_research_integrity_error")


def _valid_results(results, queries: list[str], count: int, *, minimum: int) -> None:
    if (not isinstance(results, list) or not minimum <= len(results) <= len(queries) * count):
        raise ValueError("web_research_integrity_error")
    seen = set()
    for row in results:
        expected_fields = ({"title", "url", "snippet", "query"} if len(queries) > 1
                           else {"title", "url", "snippet"})
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise ValueError("web_research_integrity_error")
        url, title, snippet = row.get("url"), row.get("title"), row.get("snippet")
        if (_public_url(url) != url or _plain(title, 200) != title
                or _plain(snippet, 500) != snippet or not title or not snippet
                or url in seen
                or (len(queries) > 1 and row["query"] not in queries)):
            raise ValueError("web_research_integrity_error")
        seen.add(url)


def validate_search_document(config: dict, document) -> dict:
    """Reject invalid cached search data before it reaches a model or reviewer."""
    config = validate_web_research(config, brief="enabled")
    queries = [config["query"], *config.get("more_queries", [])]
    if (not isinstance(document, dict) or document.get("provider") != "brave"
            or document.get("query") != queries[0]
            or (document.get("queries") if len(queries) > 1 else [queries[0]]) != queries):
        raise ValueError("web_research_integrity_error")
    _valid_timestamp(document.get("retrieved_at"))
    _valid_results(document.get("results"), queries, config["count"], minimum=1)
    topic_times = document.get("topic_retrieved_at")
    if topic_times is not None:
        if (len(queries) == 1 or not isinstance(topic_times, list)
                or len(topic_times) != len(queries)):
            raise ValueError("web_research_integrity_error")
        for expected_query, entry in zip(queries, topic_times):
            if (not isinstance(entry, dict) or set(entry) != {"query", "retrieved_at"}
                    or entry.get("query") != expected_query):
                raise ValueError("web_research_integrity_error")
            _valid_timestamp(entry.get("retrieved_at"))
        if document["retrieved_at"] != max(entry["retrieved_at"] for entry in topic_times):
            raise ValueError("web_research_integrity_error")
    return document
