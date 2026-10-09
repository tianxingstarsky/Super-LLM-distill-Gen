"""Bounded form values eligible for local creation-draft storage."""
from copy import deepcopy
from lib.domain.workflow_scale import MAX_CANDIDATES, MAX_CONCURRENCY, MAX_BATCH_SIZE
from lib.domain.workflow_targets import TARGETS
from lib.domain.workflow_generation import GENERATION_STYLES, MAX_GENERATION_INSTRUCTION_CHARS
from lib.domain.reasoning_trim import TRIM_TEMPLATE_NAMES, MAX_TRIM_INSTRUCTION_CHARS, MAX_TRIM_PROMPT_CHARS

NUMBER_FIELDS = {
    'workflow-count': (1, MAX_CANDIDATES), 'workflow-max-units': (1, MAX_CANDIDATES),
    'workflow-turns': (2, 8), 'workflow-concurrency': (1, MAX_CONCURRENCY),
    'workflow-batch-size': (1, MAX_BATCH_SIZE), 'workflow-chunk-chars': (200, 20000),
    'workflow-web-research-count': (1, 5),
    'workflow-knowledge-limit': (1, 50),
}
TEXT_FIELDS = {'workflow-name': 100, 'workflow-open-brief': 20000,
               'workflow-source-brief': 20000, 'workflow-preset': 128, 'workflow-source-mode': 128,
               'workflow-web-research-query': 160, 'workflow-web-research-more': 700,
               'workflow-knowledge-query': 2000,
               'workflow-generation-instruction': MAX_GENERATION_INSTRUCTION_CHARS,
               'workflow-trim-instruction': MAX_TRIM_INSTRUCTION_CHARS,
               'workflow-trim-prompt': MAX_TRIM_PROMPT_CHARS}
ENUM_FIELDS = {'workflow-sft-output-style': frozenset({'separated', 'drop'}),
               'workflow-knowledge-provider': frozenset({'local', 'qdrant'}),
               'workflow-generation-style': frozenset(GENERATION_STYLES),
               'workflow-trim-template': frozenset(TRIM_TEMPLATE_NAMES)}
BOOLEAN_FIELDS = {'workflow-generation-enabled', 'workflow-trim-enabled'}
NODE_GENERATION_FIELDS = {'workflow-generation-enabled', 'workflow-generation-style',
                          'workflow-generation-instruction'}


def validate_creation_draft(values):
    if not isinstance(values, dict) or len(values) > 128:
        raise ValueError('invalid_creation_draft')
    for key, value in values.items():
        if not isinstance(key, str) or ':' not in key or len(key) > 512:
            raise ValueError('invalid_creation_draft')
        field = key.split(':', 1)[0]
        if field in NODE_GENERATION_FIELDS:
            parts = key.split(':')
            if len(parts) != 3 or not parts[1] or parts[2] not in {'sft', 'cot'}:
                raise ValueError('invalid_creation_draft')
        valid = False
        if field in BOOLEAN_FIELDS:
            valid = type(value) is bool
        elif field in NUMBER_FIELDS:
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
