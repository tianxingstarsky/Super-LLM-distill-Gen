"""Precise representation diagnostics; never edit evidence or grant a verdict."""


def schema_feedback(schema, arguments):
    """Use the existing JSON Schema library; do not repair or reinterpret values."""
    from jsonschema import Draft202012Validator
    return [{"path": "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path),
             "message": error.message} for error in Draft202012Validator(schema).iter_errors(arguments)]

def review_feedback(workspace, value):
    """Locate invalid entries without editing evidence, votes or the candidate."""
    from .reasoning_references import citation_diagnostics
    if not isinstance(value, dict):
        return []
    details = []
    sources = {p['id']: p['text'] for p in workspace.passages} | workspace.candidate
    rules = {r['id']: r for r in workspace.rules}
    fields = {'rule_id', 'status', 'evidence', 'reason'}
    statuses = ['met', 'unmet', 'uncertain', 'not_applicable']
    seen = set()
    entries = value.get('checks')
    for i, check in enumerate(entries if isinstance(entries, list) else []):
        path = f'$.checks[{i}]'
        if not isinstance(check, dict):
            details.append({'path': path, 'message': 'Check must be an object.'})
            continue
        rule_id = check.get('rule_id')
        if set(check) != fields:
            details.append({'path': path, 'message': 'Use exactly the documented check fields.',
                            'missing_fields': sorted(fields - set(check)),
                            'unexpected_fields': sorted(set(check) - fields)})
        if not isinstance(rule_id, str) or rule_id not in rules or rule_id in seen:
            details.append({'path': path + '.rule_id', 'message': 'Use each active rule ID exactly once.',
                            'expected_rule_ids': list(rules)})
        if isinstance(rule_id, str):
            seen.add(rule_id)
        if check.get('status') not in statuses:
            details.append({'path': path + '.status', 'message': 'Unknown status.', 'allowed': statuses})
        if not isinstance(check.get('reason'), str) or not check['reason'].strip():
            details.append({'path': path + '.reason', 'message': 'Explain the actual review decision.'})
        evidence = check.get('evidence')
        if not isinstance(evidence, list):
            details.append({'path': path + '.evidence', 'message': 'Evidence must be a list of literal citations.'})
            continue
        for j, citation in enumerate(evidence):
            citation_path = path + f'.evidence[{j}]'
            if (not isinstance(citation, dict) or set(citation) != {'source', 'quote'}
                    or not isinstance(citation.get('source'), str)
                    or not isinstance(citation.get('quote'), str)):
                details.append({'path': citation_path, 'message': 'Use a registered citation_id or literal string source/quote.'})
                continue
            for detail in citation_diagnostics([citation], sources):
                details.append({**detail, 'path': citation_path})
        if check.get('status') != 'not_applicable' and not any(
                isinstance(e, dict) and isinstance(e.get('source'), str)
                and e['source'] in workspace.candidate for e in evidence):
            details.append({'path': path + '.evidence', 'rule_id': rule_id,
                            'message': 'missing_candidate_evidence',
                            'instruction': 'Identify the exact proposition in the candidate that this rule evaluates. '
                            'Add its reasoning/answer section ID as a citation_id, keeping the source citations. '
                            'Source evidence alone does not show which candidate claim was checked. '
                            'Do not change the candidate, verdict or finding to satisfy this representation contract.',
                            'available_body_sections': [s['id'] for s in workspace.sections]})
    findings = value.get('findings')
    for i, finding in enumerate(findings if isinstance(findings, list) else []):
        if not isinstance(finding, dict):
            details.append({'path': f'$.findings[{i}]', 'message': 'Finding must be an object.'})
            continue
        citations = [(f'$.findings[{i}].body', finding.get('body'), True)]
        basis = finding.get('basis')
        if isinstance(basis, list):
            citations.extend((f'$.findings[{i}].basis[{j}]', cite, False) for j, cite in enumerate(basis))
        else:
            details.append({'path': f'$.findings[{i}].basis', 'message': 'Basis must be a list of literal citations.'})
        for path, cite, body_only in citations:
            if (not isinstance(cite, dict) or set(cite) != {'source', 'quote'}
                    or not isinstance(cite.get('source'), str) or not isinstance(cite.get('quote'), str)):
                details.append({'path': path, 'message': 'Use a registered citation_id or literal string source/quote.'})
                continue
            for detail in citation_diagnostics([cite], workspace.candidate if body_only else sources):
                details.append({**detail, 'path': path})
    return details
