from copy import deepcopy
import pytest

from lib.domain.source_conversation import source_conversation_issue


def trace(flag=True):
    return {'messages': [
        {'role': 'user', 'content': 'Read the recorded result.'},
        {'role': 'assistant', 'tool_calls': [{'id': 'call', 'type': 'function',
         'function': {'name': 'read', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'call', 'content': 'Failed', 'is_error': flag},
        {'role': 'assistant', 'content': 'The operation failed.'},
    ]}


def test_recorded_failure_is_retained_only_for_agent_and_source_stays_intact():
    sample = trace()
    original = deepcopy(sample)
    assert source_conversation_issue(sample, ['sft']) == 'unresolved_tool_error'
    assert source_conversation_issue(sample, ['agent']) is None
    assert sample == original


@pytest.mark.parametrize('flag', ['false', 0, None, []])
def test_agent_exception_does_not_accept_invalid_error_flags(flag):
    assert source_conversation_issue(trace(flag), ['agent']) == 'invalid_tool_error_flag'


def test_agent_failure_exception_does_not_accept_orphan_results():
    sample = trace()
    sample['messages'][2]['tool_call_id'] = 'other'
    assert source_conversation_issue(sample, ['agent']) == 'orphan_tool_result'


def test_context_snapshot_and_multimodal_limits_remain_source_rules():
    sample = {'messages': [{'role': 'user', 'content': 'Q'}, {'role': 'assistant', 'content': 'A'}]}
    assert source_conversation_issue(sample, ['multiturn']) is None
    sample['tool_snapshots'] = []
    assert source_conversation_issue(sample, ['agent']) == 'invalid_tool_snapshots'
    sample.pop('tool_snapshots')
    sample['messages'][1]['content'] = 'A' * 80001
    assert source_conversation_issue(sample, ['agent']) == 'context_exceeds_auto_limit'
    sample['images'] = ['recorded.png']
    assert source_conversation_issue(sample, ['agent']) == 'multimodal_requires_dedicated_pipeline'


def test_oversized_messages_skip_structure_and_message_text_scans(monkeypatch):
    from lib.domain import source_conversation as rules
    def reject_scan(messages):
        raise AssertionError('oversized messages must be rejected before scanning')
    monkeypatch.setattr(rules, 'conversation_issue', reject_scan)
    original = rules.text_issue
    def bounded_text_scan(value):
        assert len(value) <= rules.MAX_SOURCE_CONTEXT_CHARS
        return original(value)
    monkeypatch.setattr(rules, 'text_issue', bounded_text_scan)
    sample = {'messages': [{'role': 'assistant', 'content': 'A' * 80001}]}
    assert rules.source_conversation_issue(sample, ['agent']) == 'context_exceeds_auto_limit'
