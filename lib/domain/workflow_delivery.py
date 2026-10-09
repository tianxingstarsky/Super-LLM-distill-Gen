"""Readiness for verified final or explicitly committed partial results."""


def _partial_export(document):
    production = document.get("production") if isinstance(document, dict) else None
    return isinstance(production, dict) and production.get("partial_export") is True


def has_deliverable_results(state) -> bool:
    """A failure alone never makes incomplete checkpoints deliverable."""
    if not isinstance(state, dict):
        return False
    status = state.get("status")
    if not isinstance(status, str):
        return False
    return status in {"completed", "needs_attention"} or (
        status in {"failed", "cancelled"} and _partial_export(state))


def delivery_manifest_matches(state, manifest) -> bool:
    """Use alongside file verification; a flag does not prove integrity."""
    if not isinstance(state, dict) or not isinstance(manifest, dict):
        return False
    markers = []
    for document in (state, manifest):
        production = document.get("production")
        if production is None:
            markers.append(False)
            continue
        if not isinstance(production, dict):
            return False
        marker = production.get("partial_export", False)
        if type(marker) is not bool:
            return False
        markers.append(marker)
    return markers[0] is markers[1]
