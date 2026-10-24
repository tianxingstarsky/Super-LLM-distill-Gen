"""Locate invalid context quotations without fuzzy rebinding or grade edits."""
from .candidate_content import candidate_digest

_FIELDS = {"content", "reasoning_content", "reasoning", "thinking", "analysis"}
_BRANCHES = {"claim_checks", "condition_ledger", "challenge_checks", "issues", "premises", "basis", "evidence", "counter_evidence"}


def context_quote_feedback(response, request):
    messages = request.get("messages", [])[:request.get("scope", {}).get("message_index", 0)]
    contexts = [(index, field, message[field]) for index, message in enumerate(messages)
        if isinstance(message, dict) for field in sorted(_FIELDS) if isinstance(message.get(field), str)]
    failures, visited = [], 0

    def walk(value, path):
        nonlocal visited
        visited += 1
        if visited > 8192 or len(path) > 16 or len(failures) >= 24:
            return
        if isinstance(value, dict):
            quote, index, field = value.get("quote"), value.get("message_index"), value.get("field")
            if (isinstance(quote, str) and quote.strip() and type(index) is int
                    and isinstance(field, str) and field in _FIELDS):
                matches = [(i, f) for i, f, text in contexts if quote in text]
                if (index, field) not in matches:
                    actual = next((text for i, f, text in contexts if (i, f) == (index, field)), None)
                    failure = {"response_path": [*path, "quote"],
                        "code": "quote_at_other_context" if matches else "quote_not_in_context",
                        "exact_quote_context_locations": [{"message_index": i, "field": f} for i, f in matches],
                        "referenced_message_index": index, "referenced_field": field}
                    if actual is not None:
                        failure["actual_context_sha256"] = candidate_digest(actual)
                        if len(actual) <= 4000:
                            failure["actual_context_verbatim"] = actual
                    failures.append(failure)
            for key, child in value.items():
                if key in _BRANCHES:
                    walk(child, [*path, key])
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, [*path, index])

    walk(response, [])
    return {"invalid_context_quotations": failures} if failures else {}
