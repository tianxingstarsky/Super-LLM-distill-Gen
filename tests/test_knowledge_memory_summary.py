"""Memory telemetry is compact, truthful, bilingual, and independent of APIs."""
from copy import deepcopy
from types import SimpleNamespace
import re

import pytest

from lib.presentation.streamlit import knowledge_memory_summary as memory_ui


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """These view tests never need an upstream or local model server."""
    yield


def _state(**metrics):
    return {"qa_director": {"knowledge_context": {"archive_version": 1, **metrics}}}


@pytest.mark.parametrize("language", ["zh", "en"])
def test_summary_renders_only_real_metrics_without_mutating_state(monkeypatch, language):
    state = _state(retained_messages=6, history_bytes=3072, history_window_max_bytes=49152,
                   archived_operations=37, archive_previews=4, archive_tools_available=True,
                   summary_kind="extractive_public_archive", history_is_not_evidence=True)
    before = deepcopy(state)
    captions = []
    monkeypatch.setattr(memory_ui, "st", SimpleNamespace(caption=captions.append))
    memory_ui.render_knowledge_memory_summary(state, language)
    assert state == before
    assert len(captions) == 2
    assert "3.0 / 48.0 KiB" in captions[0]
    assert "37" in captions[0] and "6" in captions[0] and "4" in captions[0]
    assert "token" in captions[1]
    if language == "en":
        assert not re.search(r"[\u4e00-\u9fff]", " ".join(captions))
        assert "not factual evidence" in captions[1]
        assert "retrieved as needed" in captions[1]
    else:
        assert "不作事实依据" in captions[1]
        assert "可按需检索" in captions[1]


@pytest.mark.parametrize("state", [
    {}, None, [], {"qa_director": None}, {"qa_director": []},
    {"qa_director": {"knowledge_context": None}},
    {"qa_director": {"knowledge_context": {"retained_messages": 10}}},
    _state(archive_version=0, retained_messages=10),
    _state(archive_version=True, retained_messages=10),
    _state(archive_version="1", retained_messages=10),
    _state(archive_version=2, retained_messages=10),
    _state(),
])
def test_absent_legacy_and_unconfirmed_archive_states_render_nothing(monkeypatch, state):
    captions = []
    monkeypatch.setattr(memory_ui, "st", SimpleNamespace(caption=captions.append))
    assert memory_ui.knowledge_memory_summary(state, "en") == ()
    memory_ui.render_knowledge_memory_summary(state)
    assert not captions


def test_zero_counts_are_real_and_tool_fallback_does_not_claim_retrieval():
    lines = memory_ui.knowledge_memory_summary(
        _state(retained_messages=0, history_bytes=0, archived_operations=0,
               archive_previews=0, archive_tools_available=False), "en")
    assert "0 recent messages" in lines[0]
    assert "0.0 KiB" in lines[0]
    assert "0 archived operations" in lines[0]
    assert "0 previews" in lines[0]
    assert "Archive previews" in lines[1]
    assert "retrieved" not in lines[1]
    assert "/" not in lines[0]


def test_partial_telemetry_does_not_fabricate_counts_or_status():
    lines = memory_ui.knowledge_memory_summary(_state(history_bytes=1024), "en")
    assert lines[0] == "**Director memory** · History 1.0 KiB"
    assert "retrieved" not in lines[1] and "previews are used" not in lines[1]


@pytest.mark.parametrize("value", [True, False, -1, 0.5, "10", [], {"count": 10}, None])
def test_malformed_metrics_never_become_zero_or_render_raw_values(value):
    state = _state(retained_messages=value, history_bytes=value, archived_operations=value,
                   archive_previews=value, history_window_max_bytes=value)
    assert memory_ui.knowledge_memory_summary(state, "en") == ()


def test_archive_bodies_and_untrusted_text_never_enter_summary():
    private = "<script>private chain with secret</script>"
    state = _state(retained_messages=2, history_bytes=1024,
                   archived_operations=private, archive_previews=[private],
                   archive_tools_available=private, messages=[private],
                   summary_kind=private, archive_body=private)
    lines = memory_ui.knowledge_memory_summary(state, "en")
    assert len(lines) == 2 and "1.0 KiB" in lines[0]
    assert private not in " ".join(lines)
    assert "retrieved" not in lines[1]


def test_node_help_distinguishes_public_history_tools_and_factual_sources():
    assert "新任务" in memory_ui.knowledge_memory_context_help("zh")
    assert "Private reasoning and tool bodies are not indexed" in memory_ui.knowledge_memory_tools_help("en")
    assert "tools disabled" in memory_ui.knowledge_memory_tools_help("en")
    assert "not factual evidence" in memory_ui.knowledge_memory_retention_caption("en")
    for helper in (memory_ui.knowledge_memory_context_help, memory_ui.knowledge_memory_tools_help,
                   memory_ui.knowledge_memory_retention_caption):
        assert not re.search(r"[\u4e00-\u9fff]", helper("en"))
        assert re.search(r"[\u4e00-\u9fff]", helper("zh"))
