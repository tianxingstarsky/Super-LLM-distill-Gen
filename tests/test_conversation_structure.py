"""Conversation boundaries are pure and shared by window and timeline projections."""
from copy import deepcopy
import pytest

from lib.domain.conversation_structure import dialogue_ranges, trace_ranges, message_windows


@pytest.mark.parametrize('size', [0, -1, True, 1.5, '8'])
def test_window_size_rejects_invalid_values(size):
    with pytest.raises(ValueError, match='invalid_preview_window_size'):
        message_windows('agent', [], size)


@pytest.mark.parametrize('target', ['agent', 'agent_negative', 'sft', 'multiturn'])
def test_windows_and_display_ranges_cover_same_messages_without_mutation(target):
    messages = [{'role': 'system', 'content': 'Initial context'}]
    for index in range(20):
        messages.extend([
            {'role': 'user', 'content': f'Question {index}'},
            {'role': 'assistant', 'toolCalls': [{'id': str(index), 'name': 'read'}]},
            {'role': 'tool', 'toolCallId': str(index), 'content': 'Observation'},
            {'role': 'assistant', 'content': 'Answer'}])
    original = deepcopy(messages)
    ranges = trace_ranges if target.startswith('agent') else dialogue_ranges
    complete = list(ranges(messages))
    windows = message_windows(target, messages, size=3)
    assert windows[0][0] == 0 and windows[-1][1] == len(messages)
    rebuilt = []
    for start, end, offset in windows:
        local = [(a + start, b + start) for a, b in ranges(messages[start:end])]
        assert complete[offset:offset + len(local)] == local
        rebuilt.extend(local)
    assert rebuilt == complete
    assert messages == original


def test_duplicate_unrelated_and_mixed_results_remain_distinct():
    messages = [
        {'role': 'assistant', 'tool_calls': [{'id': 'a', 'function': {'name': 'read'}}]},
        {'role': 'tool', 'tool_call_id': 'a', 'content': 'first'},
        {'role': 'tool', 'tool_call_id': 'a', 'content': 'duplicate'},
        {'role': 'tool', 'tool_call_id': 'other', 'content': 'unrelated'},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a', 'content': 'old'},
                                    {'type': 'text', 'text': 'New question'}]},
        {'role': 'assistant', 'content': 'Answer'}]
    assert list(trace_ranges(messages)) == [(0, 2), (2, 3), (3, 4), (4, 5), (5, 6)]
    assert list(dialogue_ranges(messages)) == [(0, 4), (4, 6)]


def test_empty_records_have_no_ranges_or_windows():
    assert list(trace_ranges([])) == list(dialogue_ranges([])) == message_windows('sft', []) == []


def test_fifty_thousand_steps_build_compact_window_metadata():
    import tracemalloc
    messages = [{'role': 'assistant', 'content': 'Recorded response'}] * 50000
    tracemalloc.start()
    try:
        windows = message_windows('agent', messages)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(windows) == 6250
    assert windows[-1] == (49992, 50000, 49992)
    assert peak < 2 * 1024 * 1024
