"""Bounded form values eligible for local creation-draft storage."""
from copy import deepcopy
from math import isfinite
import re
from lib.domain.workflow_scale import MAX_CANDIDATES, MAX_CONCURRENCY, MAX_BATCH_SIZE
from lib.domain.workflow_scale import NODE_ROLES, validate_node_models
from lib.domain.workflow_targets import TARGETS
from lib.domain.workflow_production import MAX_PRODUCTION_GOAL, validate_production
from lib.domain.workflow_generation import GENERATION_STYLES, MAX_GENERATION_INSTRUCTION_CHARS
from lib.domain.reasoning_trim import TRIM_TEMPLATE_NAMES, MAX_TRIM_INSTRUCTION_CHARS, MAX_TRIM_PROMPT_CHARS
from lib.domain.workflow_node_prompts import NODE_PROMPT_IDS, MAX_NODE_PROMPT_CHARS
from lib.domain.workflow_package_review import MAX_PACKAGE_REVIEW_SAMPLES, MIN_PACKAGE_REVIEW_PERCENT

NUMBER_FIELDS = {
    'workflow-count': (1, MAX_PRODUCTION_GOAL), 'workflow-max-units': (1, MAX_PRODUCTION_GOAL),
    'workflow-turns': (2, 8), 'workflow-concurrency': (1, MAX_CONCURRENCY),
    'workflow-batch-size': (1, MAX_BATCH_SIZE), 'workflow-chunk-chars': (200, 20000),
    'workflow-web-research-count': (1, 5),
    'workflow-knowledge-limit': (1, 50),
    'workflow-package-review-limit': (1, MAX_PACKAGE_REVIEW_SAMPLES),
    'workflow-director-batch': (1, 50), 'workflow-director-history': (0, 20),
    'workflow-director-weight': (0, 100),
}
DECIMAL_FIELDS = {'workflow-package-review-percent': (MIN_PACKAGE_REVIEW_PERCENT, 100),
                  'workflow-package-review-escalation': (0, 100),
                  'workflow-production-budget': (0, 1_000_000_000)}
TEXT_FIELDS = {'workflow-name': 100, 'workflow-open-brief': 20000,
               'workflow-source-brief': 20000, 'workflow-preset': 128, 'workflow-source-mode': 128,
               'workflow-web-research-query': 160, 'workflow-web-research-more': 700,
               'workflow-knowledge-query': 2000,
               'workflow-generation-instruction': MAX_GENERATION_INSTRUCTION_CHARS,
               'workflow-trim-instruction': MAX_TRIM_INSTRUCTION_CHARS,
               'workflow-trim-prompt': MAX_TRIM_PROMPT_CHARS,
               'workflow-director-question-rules': 12000,
               'workflow-director-answer-rules': 12000,
               'workflow-node-prompt': MAX_NODE_PROMPT_CHARS}
ENUM_FIELDS = {'workflow-sft-output-style': frozenset({'separated', 'drop'}),
               'workflow-knowledge-provider': frozenset({'local', 'qdrant'}),
               'workflow-generation-style': frozenset(GENERATION_STYLES),
               'workflow-trim-template': frozenset(TRIM_TEMPLATE_NAMES),
               'workflow-package-review-mode': frozenset({'sample', 'all'})}
ENUM_FIELDS.update({'workflow-document-parse-mode': frozenset({'native', 'model', 'vision'}),
                    'workflow-cpt-processing-mode': frozenset({'native', 'model'}),
                    'workflow-cpt-review-mode': frozenset({'text', 'vision'}),
                    'workflow-agent-mode': frozenset({'local', 'isolated'}),
                    'workflow-production-policy': frozenset({'quality_first', 'bounded_replenishment'}),
                    'workflow-director-mode': frozenset({'adaptive', 'balanced'})})
BOOLEAN_FIELDS = {'workflow-generation-enabled', 'workflow-trim-enabled',
                  'workflow-package-review-enabled', 'workflow-director-enabled',
                  'workflow-production-enabled'}
NODE_GENERATION_FIELDS = {'workflow-generation-enabled', 'workflow-generation-style',
                          'workflow-generation-instruction'}


def validate_creation_draft(values):
    if not isinstance(values, dict) or len(values) > 128:
        raise ValueError('invalid_creation_draft')
    scopes = set()
    for key, value in values.items():
        if not isinstance(key, str) or ':' not in key or len(key) > 512:
            raise ValueError('invalid_creation_draft')
        parts = key.split(':')
        field = parts[0]
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', parts[1])
                or any(ord(character) < 32 for character in key)):
            raise ValueError('invalid_creation_draft')
        scopes.add(parts[1])
        if len(scopes) > 1:
            raise ValueError('invalid_creation_draft')
        if field in {'workflow-node-bindings', 'workflow-node-model-confirmations', 'workflow-document-parse-mode', 'workflow-agent-mode',
                     'workflow-cpt-processing-mode', 'workflow-cpt-review-mode'} and len(parts) != 2:
            raise ValueError('invalid_creation_draft')
        if field in NODE_GENERATION_FIELDS:
            parts = key.split(':')
            if len(parts) != 3 or not parts[1] or parts[2] not in {'sft', 'cot'}:
                raise ValueError('invalid_creation_draft')
        if field == 'workflow-node-prompt':
            parts = key.split(':')
            if (len(parts) != 4 or not parts[1] or parts[2] not in NODE_PROMPT_IDS
                    or parts[3] not in NODE_PROMPT_IDS[parts[2]]):
                raise ValueError('invalid_creation_draft')
        if field == 'workflow-director-weight':
            parts = key.split(':')
            if (len(parts) != 3 or not parts[1] or parts[2] not in
                    {'closed_book', 'grounded', 'partial', 'multi_source', 'distractor'}):
                raise ValueError('invalid_creation_draft')
        valid = False
        if field in BOOLEAN_FIELDS:
            valid = type(value) is bool
        elif field in NUMBER_FIELDS:
            low, high = NUMBER_FIELDS[field]
            valid = type(value) is int and low <= value <= high
        elif field in DECIMAL_FIELDS:
            low, high = DECIMAL_FIELDS[field]
            valid = type(value) in {int, float} and low <= value <= high and isfinite(value)
        elif field in TEXT_FIELDS:
            valid = (isinstance(value, str) and len(value) <= TEXT_FIELDS[field]
                     and (field != 'workflow-node-prompt' or '\x00' not in value))
        elif field in ENUM_FIELDS:
            valid = isinstance(value, str) and value in ENUM_FIELDS[field]
        elif field == 'workflow-targets':
            valid = isinstance(value, list) and len(value) <= len(TARGETS) and all(isinstance(v, str) and v in TARGETS for v in value)
        elif field == 'workflow-sources':
            valid = isinstance(value, list) and len(value) <= 500 and all(isinstance(v, str) and len(v) <= 4096 for v in value)
        elif field == 'workflow-node-bindings':
            # Persist references and limits only. The model validator rejects
            # endpoint URLs, credentials and arbitrary node/role keys.
            try:
                valid = isinstance(value, dict) and validate_node_models(value) == value
            except ValueError:
                valid = False
        elif field == 'workflow-node-model-confirmations':
            valid = (isinstance(value, dict) and not set(value) - set(NODE_ROLES)
                     and all(isinstance(signature, str) and re.fullmatch(r'[0-9a-f]{64}', signature)
                             for signature in value.values()))
        elif field == 'workflow-production-goals':
            valid = (len(parts) == 2 and isinstance(value, dict) and not set(value) - set(TARGETS)
                     and all(type(count) is int and 1 <= count <= MAX_PRODUCTION_GOAL
                             for count in value.values()))
        elif field == 'workflow-production-limits':
            allowed = {'max_attempts', 'max_rounds', 'round_size', 'min_acceptance_rate',
                       'low_acceptance_rounds', 'item_retries'}
            try:
                valid = (len(parts) == 2 and isinstance(value, dict) and not set(value) - allowed
                         and validate_production(value, TARGETS) is not None)
            except ValueError:
                valid = False
        if not valid:
            raise ValueError('invalid_creation_draft')
    return deepcopy(values)
