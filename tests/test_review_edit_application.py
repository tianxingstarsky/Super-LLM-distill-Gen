"""Review suggestions remain scoped, lossless, and isolated from the LLM adapter."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from lib.application.review_edit_service import ReviewEditApplication
from lib.domain.review_edit import validate_edits


class SuggestionSpy:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete_json(self, messages, *, temperature):
        self.calls.append((messages, temperature))
        return self.reply


def test_selected_replacement_preserves_other_turns_and_tool_metadata():
    original = [
        {"role": "user", "content": "请查天气", "images": ["weather.png"]},
        {"role": "assistant", "content": "北京晴，上海晴", "reasoning_content": "先查两个城市",
         "toolCalls": [{"name": "weather", "input": {"city": "北京"}}]},
        {"role": "tool", "content": "晴", "toolCallId": "call-1"},
    ]
    before = deepcopy(original)
    spy = SuggestionSpy({"text": "多云"})

    result = ReviewEditApplication(spy).propose_field(original, 1, "content", "修正上海天气", (6, 7))

    assert result[1]["content"] == "北京晴，上海多云"
    assert result[1]["toolCalls"] == original[1]["toolCalls"]
    assert result[0] == original[0] and result[2] == original[2]
    assert original == before and result is not original
    request, temperature = spy.calls[0]
    assert temperature == 0.2
    assert "不可信数据" in request[0]["content"]
    assert json.loads(request[1]["content"]) == {
        "requirement": "修正上海天气", "field": "content",
        "selected_text": "晴", "surrounding_text": "北京晴，上海晴",
    }


@pytest.mark.parametrize("selection", [(0, 0), (0, 999), (True, 2), ("0", 2), (1,)])
def test_invalid_selection_never_calls_model(selection):
    spy = SuggestionSpy({"text": "changed"})
    app = ReviewEditApplication(spy)
    messages = [{"role": "user", "content": "问题"}]
    with pytest.raises(ValueError):
        app.propose_field(messages, 0, "content", "改写", selection)
    assert spy.calls == []


def test_multimodal_target_never_calls_model():
    spy = SuggestionSpy({"text": "changed"})
    messages = [{"role": "user", "content": [{"type": "image_url", "url": "x"}]}]
    with pytest.raises(ValueError, match="需要文本内容"):
        ReviewEditApplication(spy).propose_field(messages, 0, "content", "改写")
    assert spy.calls == []


def test_malformed_model_reply_never_changes_draft():
    messages = [{"role": "assistant", "content": "原答案", "reasoning_content": "证据"}]
    original = deepcopy(messages)
    for reply in ({"text": "新答案", "role": "system"}, {"text": ["新答案"]}, "新答案"):
        with pytest.raises(ValueError, match="AI 返回格式错误"):
            ReviewEditApplication(SuggestionSpy(reply)).propose_field(messages, 0, "content", "修订")
        assert messages == original


def test_legacy_entrypoint_uses_layered_rules_and_driver():
    from lib import review_editor
    from lib.domain import review_edit

    assert review_editor.validate_edits is review_edit.validate_edits
    assert review_editor.normalize_sample is review_edit.normalize_sample

    class FakeClient:
        def chat(self, *_args, **_kwargs):
            return '{"text":"新答案"}'

    original = [{"role": "assistant", "content": "旧答案", "toolCalls": [{"name": "calc"}]}]
    result = review_editor.propose_field(original, 0, "content", "修订", FakeClient())
    assert result == [{"role": "assistant", "content": "新答案", "toolCalls": [{"name": "calc"}]}]
    assert original[0]["content"] == "旧答案"


def test_domain_rejects_flattening_a_multimodal_message():
    original = {"images": ["plot.png"], "messages": [{"role": "user", "content": [
        {"type": "text", "text": "看图"}, {"type": "image_url", "url": "data:image/png;base64,AA"},
    ]}]}
    with pytest.raises(ValueError, match="多模态"):
        validate_edits(original, [{"role": "user", "content": "看图"}])
    assert isinstance(original["messages"][0]["content"], list)
