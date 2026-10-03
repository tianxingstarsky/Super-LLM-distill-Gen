"""Pure rules for optional public web research in an open-brief workflow."""
from __future__ import annotations

import re
import unicodedata

from lib.domain.workflow_quality import text_issue


MAX_QUERY_CHARS = 160
MAX_RESULTS = 5
MAX_QUERIES = 5


def _public_query(value):
    if not isinstance(value, str):
        raise ValueError("web_research_query_private_or_invalid")
    query = " ".join(unicodedata.normalize("NFKC", value).split())
    if (not query or len(query) > MAX_QUERY_CHARS or len(query.split()) > 25
            or text_issue(query) or re.search(r"(?:https?|file)://|[A-Za-z]:[\\/]|\\\\|^/", query, re.I)
            or any(unicodedata.category(char).startswith("C") for char in query)):
        raise ValueError("web_research_query_private_or_invalid")
    return query


def validate_web_research(value, *, brief: str, sources=(), targets=None):
    """Only an explicitly supplied public query can leave the workstation.

    Uploaded source text and the complete open brief are never turned into a
    search query automatically. The API key belongs to the process environment,
    not to the immutable recipe.
    """
    if value is None:
        return None
    if (not isinstance(value, dict) or
            set(value) not in ({"provider", "query", "count"},
                               {"provider", "query", "count", "more_queries"})):
        raise ValueError("web_research_invalid_config")
    if sources or not isinstance(brief, str) or not brief.strip():
        raise ValueError("web_research_requires_open_brief")
    if targets is not None and not (set(targets) &
                                    {"cpt", "sft", "multiturn", "cot", "dpo", "orpo", "rlaif"}):
        raise ValueError("web_research_requires_planning_target")
    if value["provider"] != "brave" or type(value["count"]) is not int or not 1 <= value["count"] <= MAX_RESULTS:
        raise ValueError("web_research_invalid_config")
    extra = value.get("more_queries", [])
    if (not isinstance(extra, list) or len(extra) > MAX_QUERIES - 1
            or ("more_queries" in value and not extra)):
        raise ValueError("web_research_invalid_config")
    queries = [_public_query(item) for item in [value["query"], *extra]]
    if len({query.casefold() for query in queries}) != len(queries):
        raise ValueError("web_research_invalid_config")
    normalized = {"provider": "brave", "query": queries[0], "count": value["count"]}
    if extra:
        normalized["more_queries"] = queries[1:]
    return normalized
