"""Translate the judge's strictly earlier positions to stable, archived segment identifiers."""


def normalize_student_findings(findings, segment_ids):
    positions = {segment: position for position, segment in enumerate(segment_ids, 1)}
    normalized, issues, seen = [], [], set()
    if not isinstance(findings, list):
        return [], [{"issue": "expected_findings_array"}]
    for item_index, item in enumerate(findings):
        segment = item.get("segment_id") if isinstance(item, dict) else None
        issue = None
        if not isinstance(item, dict) or set(item) != {"segment_id", "basis_id", "depends_on"}:
            issue = "invalid_finding_fields"
        elif not isinstance(segment, str) or segment not in positions or segment in seen:
            issue = "unknown_or_repeated_finding"
        else:
            seen.add(segment)
            current = positions[segment]
            basis, dependencies = item["basis_id"], item["depends_on"]
            root = isinstance(basis, str) and basis in {"question", "arithmetic"}
            if not root and not (type(basis) is int and 1 <= basis < current):
                issue = "basis_must_be_an_independent_root_or_strictly_earlier_position"
            elif (not isinstance(dependencies, list)
                  or any(type(ref) is not int or not 1 <= ref < current for ref in dependencies)):
                issue = "dependencies_must_be_strictly_earlier_positions"
            else:
                normalized.append({"segment_id": segment, "basis_id": basis if root else segment_ids[basis - 1],
                                   "depends_on": [segment_ids[ref - 1] for ref in dependencies]})
        if issue:
            issues.append({"finding_index": item_index, "segment_id": segment if isinstance(segment, str) else None,
                           "issue": issue})
    return normalized, issues
