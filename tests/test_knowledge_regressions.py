"""Knowledge retrieval state must survive harmless reruns and discard stale evidence."""
import os
from pathlib import Path
import stat

from streamlit.testing.v1 import AppTest

from lib.bootstrap.knowledge import knowledge_application
from lib.bootstrap.local_inputs import local_input_application
from lib.infrastructure.knowledge_driver import FilesystemKnowledgeDriver


def _knowledge_ui(tmp_path):
    source = local_input_application(tmp_path).store([
        ("manual.md", ("更换电池之前应断开电源，维修工具需定期检查。\n\n" * 30).encode())
    ])[0]["path"]
    knowledge_application(root=tmp_path).index([source], 2000)
    script = f'''
import streamlit as st
from pathlib import Path
from lib.bootstrap.knowledge import knowledge_application
from lib.presentation.streamlit.knowledge_page import render_knowledge_source
root = Path({str(tmp_path)!r})
source = {source!r}
st.session_state.setdefault("knowledge-index-sources:default", [source])
st.session_state.setdefault("workflow-knowledge-query:default", "更换电池")
selected, names = render_knowledge_source(knowledge_application(root=root), "default",
    [{{"path": source, "label": "manual.md"}}])
st.session_state["selected_sources"] = selected
'''
    return AppTest.from_string(script).run(), source


def _button(ui, label):
    return next(button for button in ui.button if button.label == label)


def test_index_fingerprint_separates_source_bytes_from_chunk_parameter(tmp_path):
    source = local_input_application(tmp_path).store([("manual.md", b"battery 2")])[0]["path"]
    application = knowledge_application(root=tmp_path)
    first = application.index([source], 200)
    first_hit = application.retrieve("local", "battery", 1)["hits"][0]
    # These legitimate inputs had the same raw + str(chunk_chars) concatenation.
    Path(source).write_bytes(b"battery ")
    changed = application.index([source], 2200)
    changed_hit = application.retrieve("local", "battery", 1)["hits"][0]
    assert changed["updated"] == 1 and changed["unchanged"] == 0
    assert changed["revision"] != first["revision"]
    assert first_hit["text"] == "battery 2" and changed_hit["text"] == "battery"
    assert changed_hit["fingerprint"] != first_hit["fingerprint"]


def test_index_fingerprint_and_revision_stay_stable_for_identical_inputs(tmp_path):
    source = local_input_application(tmp_path).store([("manual.md", b"battery 2")])[0]["path"]
    application = knowledge_application(root=tmp_path)
    first = application.index([source], 200)
    first_hits = application.retrieve("local", "battery", 1)["hits"]
    reopened = knowledge_application(root=tmp_path)
    unchanged = reopened.index([source], 200)
    assert unchanged["updated"] == 0 and unchanged["unchanged"] == 1
    assert unchanged["revision"] == first["revision"]
    assert reopened.retrieve("local", "battery", 1)["hits"] == first_hits


def test_reindex_unchanged_source_keeps_ui_signature_and_materialized_snapshot(tmp_path):
    ui, _ = _knowledge_ui(tmp_path)
    _button(ui, "检索并预览").click().run()
    assert not ui.exception
    before = ui.session_state["knowledge-result:default"]
    materialized = ui.session_state["knowledge-materialized:default"]
    assert ui.session_state["selected_sources"]
    _button(ui, "建立／更新本地索引").click().run()
    assert not ui.exception
    assert ui.session_state["knowledge-result:default"] == before
    assert ui.session_state["knowledge-materialized:default"] == materialized
    ui.run()
    assert ui.session_state["knowledge-result:default"] == before


def test_reindex_with_new_chunk_parameter_invalidates_old_ui_result(tmp_path):
    ui, _ = _knowledge_ui(tmp_path)
    _button(ui, "检索并预览").click().run()
    previous_revision = ui.session_state["knowledge-result:default"][1]["revision"]
    chunk = next(item for item in ui.number_input if item.label == "知识片段目标字符数")
    chunk.set_value(200).run()
    _button(ui, "建立／更新本地索引").click().run()
    assert not ui.exception
    assert knowledge_application(root=tmp_path).status()["revision"] != previous_revision
    assert "knowledge-result:default" not in ui.session_state
    assert "knowledge-materialized:default" not in ui.session_state
    assert ui.session_state["selected_sources"] == []
    ui.run()
    assert "knowledge-result:default" not in ui.session_state
    _button(ui, "检索并预览").click().run()
    assert not ui.exception
    new_result = ui.session_state["knowledge-result:default"]
    ui.run()
    assert ui.session_state["knowledge-result:default"] == new_result


def test_changing_query_clears_previous_sources_before_another_search(tmp_path):
    ui, _ = _knowledge_ui(tmp_path)
    _button(ui, "检索并预览").click().run()
    assert ui.session_state["selected_sources"]
    ui.text_area[0].set_value("完全不同的主题").run()
    assert not ui.exception
    assert "knowledge-result:default" not in ui.session_state
    assert "knowledge-materialized:default" not in ui.session_state
    assert ui.session_state["selected_sources"] == []


def test_changing_source_content_and_reindexing_discards_old_hits(tmp_path):
    ui, source = _knowledge_ui(tmp_path)
    _button(ui, "检索并预览").click().run()
    Path(source).write_text("Rose plants need regular watering.", encoding="utf-8")
    _button(ui, "建立／更新本地索引").click().run()
    assert not ui.exception
    assert "knowledge-result:default" not in ui.session_state
    assert ui.session_state["selected_sources"] == []
    _button(ui, "检索并预览").click().run()
    assert not ui.exception
    assert ui.session_state["selected_sources"] == []
    assert ui.session_state["knowledge-result:default"][1]["hits"] == []


def test_invalid_docx_reports_an_error_without_crashing_the_page(tmp_path):
    source = local_input_application(tmp_path).store([("broken.docx", b"This is not a DOCX archive.")])[0]["path"]
    script = f'''
import streamlit as st
from pathlib import Path
from lib.bootstrap.knowledge import knowledge_application
from lib.presentation.streamlit.knowledge_page import render_knowledge_source
st.session_state.setdefault("knowledge-index-sources:default", [{source!r}])
render_knowledge_source(knowledge_application(root=Path({str(tmp_path)!r})), "default",
    [{{"path": {source!r}, "label": "broken.docx"}}])
'''
    ui = AppTest.from_string(script).run()
    _button(ui, "建立／更新本地索引").click().run()
    assert not ui.exception
    assert ui.error


def test_connection_credentials_are_protected_and_reopen_with_stable_revision(tmp_path):
    driver = FilesystemKnowledgeDriver(root=tmp_path)
    application = knowledge_application(root=tmp_path)
    application.save_connection({"url": "http://127.0.0.1:6333", "collection": "manuals",
        "embedding_url": "http://127.0.0.1:8000/v1", "embedding_model": "index-model",
        "text_field": "text", "source_field": "source", "vector_name": ""},
        "qdrant-only-for-test", "embedding-only-for-test")
    first = application.connection()
    assert first["has_api_key"] and first["has_embedding_key"]
    assert knowledge_application(root=tmp_path).connection() == first
    raw = driver.config_path.read_bytes()
    if os.name == "nt":
        assert b"qdrant-only-for-test" not in raw
        assert b"embedding-only-for-test" not in raw
    else:
        assert not stat.S_IMODE(driver.config_path.stat().st_mode) & 0o077
        assert not stat.S_IMODE(driver.config_path.parent.stat().st_mode) & 0o077


def test_corrupt_local_index_reports_error_without_crashing_the_page(tmp_path):
    driver = FilesystemKnowledgeDriver(root=tmp_path)
    driver.directory.mkdir(parents=True)
    (driver.directory / "index.sqlite3").write_bytes(b"Not a SQLite database")
    script = f'''
from pathlib import Path
from lib.bootstrap.knowledge import knowledge_application
from lib.presentation.streamlit.knowledge_page import render_knowledge_source
render_knowledge_source(knowledge_application(root=Path({str(tmp_path)!r})), "default", [])
'''
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    assert ui.error


def test_maximum_chunk_setting_produces_materializable_retrieval(tmp_path):
    content = "电池维护" * 2500 + "\n\n" + "设备检修" * 2500
    source = local_input_application(tmp_path).store([("large.md", content.encode())])[0]["path"]
    application = knowledge_application(root=tmp_path)
    application.index([source], 20000)
    result = application.retrieve("local", "电池维护", 2)
    assert result["hits"]
    saved = application.materialize(result)
    assert saved
    assert all(Path(row["path"]).is_file() for row in saved)


def test_restored_qdrant_draft_shows_connection_form_on_first_render(tmp_path):
    script = f'''
import streamlit as st
from pathlib import Path
from lib.bootstrap.knowledge import knowledge_application
from lib.presentation.streamlit.knowledge_page import render_knowledge_settings, render_knowledge_source
st.session_state["workflow-form-draft:default"] = {{"workflow-knowledge-provider:default": "qdrant"}}
application = knowledge_application(root=Path({str(tmp_path)!r}))
render_knowledge_settings(application, "default")
render_knowledge_source(application, "default", [])
'''
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    assert any(item.label == "Qdrant 地址" for item in ui.text_input)
    assert ui.session_state["workflow-knowledge-provider:default"] == "qdrant"


def test_selected_source_outside_listing_window_remains_available(tmp_path):
    source = local_input_application(tmp_path).store([("kept.md", b"Keep this selected document.")])[0]["path"]
    script = f'''
import streamlit as st
from pathlib import Path
from lib.bootstrap.knowledge import knowledge_application
from lib.presentation.streamlit.knowledge_page import render_knowledge_source
st.session_state.setdefault("knowledge-index-sources:default", [{source!r}])
render_knowledge_source(knowledge_application(root=Path({str(tmp_path)!r})), "default", [])
'''
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    assert ui.multiselect[0].value == [source]
    _button(ui, "建立／更新本地索引").click().run()
    assert not ui.exception
    assert knowledge_application(root=tmp_path).status()["documents"] == 1
