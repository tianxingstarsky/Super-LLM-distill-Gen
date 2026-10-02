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
        if ("." not in host or host.endswith((".local", ".localhost", ".internal", ".test"))):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
    except ValueError:
        return None
    # Fragments have no server-side provenance; preserve query when needed for
    # the real source URL, but never download or follow any result URL.
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


def search(config: dict) -> list[dict[str, str]]:
    """Return at most five safe snippets; never fetch result pages or redirects."""
    config = validate_web_research(config, brief="enabled")
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise ValueError("web_search_not_configured")
    path = PATH + "?" + urlencode({"q": config["query"], "count": config["count"],
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
        connection.close()
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
        if len(results) >= config["count"]:
            break
    if not results:
        raise ValueError("web_search_no_safe_results")
    return results
