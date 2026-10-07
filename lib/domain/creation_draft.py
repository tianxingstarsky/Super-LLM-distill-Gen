"""Bounded form values eligible for local creation-draft storage."""
from copy import deepcopy
from lib.domain.workflow_scale import MAX_CANDIDATES, MAX_CONCURRENCY, MAX_BATCH_SIZE
from lib.domain.workflow_targets import TARGETS

NUMBER_FIELDS = {
    'workflow-count': (1, MAX_CANDIDATES), 'workflow-max-units': (1, MAX_CANDIDATES),
    'workflow-turns': (2, 8), 'workflow-concurrency': (1, MAX_CONCURRENCY),
    'workflow-batch-size': (1, MAX_BATCH_SIZE), 'workflow-chunk-chars': (200, 20000),
    'workflow-web-research-count': (1, 5),
}
TEXT_FIELDS = {'workflow-name': 100, 'workflow-open-brief': 20000,
               'workflow-source-brief': 20000, 'workflow-preset': 128, 'workflow-source-mode': 128,
               'workflow-web-research-query': 160, 'workflow-web-research-more': 700}
ENUM_FIELDS = {'workflow-sft-output-style': frozenset({'separated', 'drop'})}


def validate_creation_draft(values):
    if not isinstance(values, dict) or len(values) > 128:
        raise ValueError('invalid_creation_draft')
    for key, value in values.items():
        if not isinstance(key, str) or ':' not in key or len(key) > 512:
            raise ValueError('invalid_creation_draft')
        field = key.split(':', 1)[0]
        valid = False
        if field in NUMBER_FIELDS:
            low, high = NUMBER_FIELDS[field]
            valid = type(value) is int and low <= value <= high
        elif field in TEXT_FIELDS:
            valid = isinstance(value, str) and len(value) <= TEXT_FIELDS[field]
        elif field in ENUM_FIELDS:
            valid = isinstance(value, str) and value in ENUM_FIELDS[field]
        elif field == 'workflow-targets':
            valid = isinstance(value, list) and len(value) <= len(TARGETS) and all(isinstance(v, str) and v in TARGETS for v in value)
        elif field == 'workflow-sources':
            valid = isinstance(value, list) and len(value) <= 500 and all(isinstance(v, str) and len(v) <= 4096 for v in value)
        if not valid:
            raise ValueError('invalid_creation_draft')
    return deepcopy(values)
