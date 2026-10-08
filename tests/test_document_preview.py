"""Source previews use actual parser semantics and never read outside local inputs."""
from pathlib import Path
import os
import zipfile

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.document_previews import document_preview_application
from lib.doc2corpus import chunk_text, import_text
from lib.infrastructure import document_preview_driver as driver


@pytest.fixture
def inputs(tmp_path):
    base = tmp_path / "data/seeds"
    base.mkdir(parents=True)
    return tmp_path, base


@pytest.mark.parametrize("name,encoding", [("manual.md", "utf-8"), ("manual.TXT", "gb18030")])
def test_actual_document_parser_and_chunk_boundaries(inputs, name, encoding):
    root, base = inputs
    source = base / name
    source.write_bytes(("# 设备说明\r\n\r\n" + "保留原文和编号123。" * 100
                        + "\r\n\r\n## 维修\r\n\r\n维修说明。" * 20).encode(encoding))
    application = document_preview_application(root=root)
    preview = application.preview(str(source), 200)
    actual_text = import_text(source).replace("\r\n", "\n").strip()
    assert preview["chunks"] == chunk_text(actual_text, 200)
    assert preview["characters"] == len(actual_text)
    assert preview["chunk_count"] == len(preview["chunks"])
    assert preview["name"] == name
    assert preview["label"] == name
    assert preview["signature"] == application.signature(str(source), 200)
    wider = application.preview(str(source), 1000)
    assert wider["chunk_count"] < preview["chunk_count"]
    assert wider["signature"] != preview["signature"]


def test_signature_changes_when_source_changes(inputs):
    root, base = inputs
    source = base / "source.txt"
    source.write_text("first version", encoding="utf-8")
    application = document_preview_application(root=root)
    original = application.signature(str(source), 200)
    source.write_text("a longer second version", encoding="utf-8")
    assert application.signature(str(source), 200) != original


@pytest.mark.parametrize("chunk_size", [0, 199, 20_001, True, 200.0])
def test_invalid_chunk_size_is_rejected_before_read(inputs, chunk_size):
    root, _ = inputs
    with pytest.raises(ValueError, match="invalid_preview_chunk_size"):
        document_preview_application(root=root).preview("not-a-path", chunk_size)


def test_outside_file_and_unsupported_file_are_rejected_before_parser(inputs, monkeypatch):
    root, base = inputs
    outside = root / "private.txt"
    outside.write_text("private", encoding="utf-8")
    wrong = base / "notes.json"
    wrong.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(driver, "import_text", lambda _: pytest.fail("parser must not run"))
    application = document_preview_application(root=root)
    with pytest.raises(ValueError, match="outside_cache"):
        application.preview(str(outside), 200)
    with pytest.raises(ValueError, match="unsupported_preview_type"):
        application.preview(str(wrong), 200)
    with pytest.raises(ValueError, match="outside_cache"):
        application.preview("../private.txt", 200)


def test_link_and_hardlink_are_rejected(inputs):
    root, base = inputs
    outside = root / "private.txt"
    outside.write_text("private", encoding="utf-8")
    hardlink = base / "linked.txt"
    try:
        os.link(outside, hardlink)
    except OSError:
        pytest.skip("hard links unavailable")
    with pytest.raises(ValueError, match="linked_upload_cache_path"):
        document_preview_application(root=root).preview(str(hardlink), 200)


def test_large_source_does_not_parse_and_remains_a_valid_selection(inputs, monkeypatch):
    root, base = inputs
    source = base / "large.txt"
    source.write_bytes(b"a" * 32)
    monkeypatch.setattr(driver, "MAX_PREVIEW_BYTES", 16)
    monkeypatch.setattr(driver, "import_text", lambda _: pytest.fail("parser must not run"))
    application = document_preview_application(root=root)
    assert application.describe(str(source), frozenset({".txt"}))["size"] == 32
    with pytest.raises(ValueError, match="preview_file_limit"):
        application.preview(str(source), 200)


def test_extracted_text_limit_is_explicit(inputs, monkeypatch):
    root, base = inputs
    source = base / "source.txt"
    source.write_text("a" * 300, encoding="utf-8")
    monkeypatch.setattr(driver, "MAX_PREVIEW_CHARS", 250)
    with pytest.raises(ValueError, match="preview_text_limit"):
        document_preview_application(root=root).preview(str(source), 200)


@pytest.mark.parametrize("name,content", [("invalid.txt", b"\x81"), ("bad.pdf", b"not a pdf"),
                                         ("bad.docx", b"not a zip")])
def test_parse_failure_is_sanitized_without_provider_or_path_details(inputs, name, content):
    root, base = inputs
    source = base / name
    source.write_bytes(content)
    with pytest.raises(ValueError, match="^preview_parse_failed$"):
        document_preview_application(root=root).preview(str(source), 200)


def test_docx_expansion_limit_runs_before_document_parser(inputs, monkeypatch):
    root, base = inputs
    source = base / "compressed.docx"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "a" * 1000)
    monkeypatch.setattr(driver, "MAX_DOCX_EXPANDED_BYTES", 500)
    monkeypatch.setattr(driver, "import_text", lambda _: pytest.fail("parser must not run"))
    with pytest.raises(ValueError, match="preview_docx_expansion_limit"):
        document_preview_application(root=root).preview(str(source), 200)


def test_pdf_page_limit_runs_before_text_extraction(inputs, monkeypatch):
    from pypdf import PdfWriter

    root, base = inputs
    source = base / "many-pages.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=100, height=100)
    writer.write(source)
    monkeypatch.setattr(driver, "MAX_PREVIEW_PAGES", 2)
    monkeypatch.setattr(driver, "import_text", lambda _: pytest.fail("parser must not run"))
    with pytest.raises(ValueError, match="preview_page_limit"):
        document_preview_application(root=root).preview(str(source), 200)


def _preview_script(root, source):
    return f'''
import streamlit as st
from pathlib import Path
from lib.bootstrap.document_previews import document_preview_application
from lib.presentation.streamlit.document_preview import render_document_preview
chunk = st.number_input('Chunk size', 200, 20000, 200, step=200)
render_document_preview(document_preview_application(root=Path({str(root)!r})), 'default',
                        [{str(source)!r}], {{{str(source)!r}: 'manual.txt'}}, int(chunk))
'''.encode("ascii", "backslashreplace").decode("ascii")


def test_preview_is_lazy_and_parameter_change_removes_old_result(inputs, monkeypatch):
    root, base = inputs
    source = base / "manual.txt"
    source.write_text("a" * 900, encoding="utf-8")
    actual_parser = driver.import_text
    calls = []
    def parse(path):
        calls.append(path)
        return actual_parser(path)
    monkeypatch.setattr(driver, "import_text", parse)
    ui = AppTest.from_string(_preview_script(root, source)).run()
    assert not ui.exception
    assert calls == []
    ui.button(key="document-preview:default:read").click().run()
    assert not ui.exception
    assert len(calls) == 1
    assert ui.code[0].value == "a" * 200
    assert ui.session_state["document-preview:default"]["chunk_count"] == 5
    ui.number_input[0].set_value(400).run()
    assert not ui.exception
    assert len(calls) == 1
    assert not ui.code
    assert "document-preview:default" not in ui.session_state
    ui.button(key="document-preview:default:read").click().run()
    assert len(calls) == 2
    assert ui.session_state["document-preview:default"]["chunk_count"] == 3


def test_saved_source_beyond_inventory_is_retained_and_missing_file_is_explained(inputs):
    from tests.test_workflow_draft import SCRIPT

    root, base = inputs
    source = base / "selected.txt"
    source.write_text("selected source", encoding="utf-8")
    injected = ("from pathlib import Path\nfrom lib.bootstrap.document_previews import document_preview_application\n"
                f"preview = document_preview_application(root=Path({str(root)!r}))\n")
    code = SCRIPT.replace("import streamlit as st", "import streamlit as st\n" + injected)
    code = code.replace("WorkflowNodeModelsApplication(Inventory()))",
                        "WorkflowNodeModelsApplication(Inventory()), document_preview=preview)")
    ui = AppTest.from_string(code)
    key = "workflow-sources:fixture:文档资料"
    ui.session_state[key] = [str(source)]
    ui.run()
    assert not ui.exception
    assert ui.multiselect(key=key).value == [str(source)]
    source.unlink()
    ui.run()
    assert not ui.exception
    assert ui.multiselect(key=key).value == []
    assert any("已失联" in warning.value for warning in ui.warning)
