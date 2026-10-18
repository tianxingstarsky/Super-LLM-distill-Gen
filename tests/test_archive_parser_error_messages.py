"""Local archive/parser failures stay actionable in both UI languages."""
import re

import pytest

from lib.presentation.streamlit.docling_settings import DOCLING_ERRORS
from lib.presentation.streamlit.i18n import translate_label
from lib.presentation.streamlit.workflow_page import _workflow_error


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.mark.parametrize("code", [
    *DOCLING_ERRORS,
    "document_docling_partial_conversion",
    "document_docling_source_changed",
    "knowledge_agent_archive_integrity_error",
    "knowledge_agent_archive_reference_invalid",
])
@pytest.mark.parametrize("error_type", [str, ValueError])
def test_archive_and_parser_failures_are_localized(code, error_type):
    chinese = _workflow_error(error_type(code))
    english = translate_label(chinese, "en")
    assert code not in chinese
    assert not re.search(r"[\u4e00-\u9fff]", english)
    assert english != chinese
    assert not any(text in chinese for text in ("充值", "更换模型", "删除缓存"))
    if code.startswith("knowledge_agent_archive_"):
        assert "保留任务与缓存" in chinese and "断点" in chinese
        assert "storage" in english and "checkpoint" in english
