"""Import preview state, explicit selection and language in the real Streamlit renderer."""
import io
import pytest
from streamlit.testing.v1 import AppTest


class Upload(io.BytesIO):
    name = "session.jsonl.zstd"


class ImportService:
    def __init__(self, blocked=False, warnings=(), truncated=False):
        self.reads = 0
        self.saved = []
        self.blocked = blocked
        self.warnings = warnings
        self.truncated = truncated

    def preview(self, name, content):
        self.reads += 1
        return {"import_id": "saved-import", "counts": {"records": 1, "messages": 2,
            "reasoning_messages": 0, "tool_calls": 0},
            "report": {"warnings": list(self.warnings)},
            "conversations": [{"messages": [{"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"}]}],
            "eligibility": {"sft": {"eligible_records": 0 if self.blocked else 1,
                "blocked_records": int(self.blocked), "eligible_ids": [] if self.blocked else ["one"],
                "eligible_ids_truncated": self.truncated,
                "reasons": {"invalid_role": 1} if self.blocked else {}}}}

    def confirm(self, import_id, selected_ids=None, *, target="sft"):
        self.saved.append((import_id, selected_ids, target))
        return [{"path": "persistent-source.jsonl"}]


def screen():
    import streamlit as st
    from lib.presentation.streamlit.conversation_import_controls import render_conversation_import
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    install_streamlit_localization()
    def selected(rows):
        st.session_state["selected"] = rows
    render_conversation_import(st.session_state["service"], "test", selected, targets=["sft"])


@pytest.mark.parametrize("language", ["zh", "en"])
def test_import_preview_runs_once_and_confirmation_selects_persistent_source(monkeypatch, language):
    import streamlit as st
    service = ImportService()
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: Upload(b"offline fixture"))
    app = AppTest.from_function(screen)
    app.session_state["service"] = service
    app.session_state["ui_language"] = language
    app.run()
    assert not app.exception
    assert service.reads == 1 and not service.saved
    assert not app.selectbox  # One source needs no redundant selection step.
    app.button(key="conversation-import:test:confirm").click().run()
    assert not app.exception
    assert service.reads == 1
    assert service.saved == [("saved-import", None, "sft")]
    assert app.session_state["selected"] == [{"path": "persistent-source.jsonl"}]
    assert app.success
    assert "已有推理" not in [item.label for item in app.expander]
    if language == "en":
        import re
        texts = [str(item.value) for kind in ("caption", "markdown", "success") for item in app.get(kind)]
        assert not any(re.search(r"[\u4e00-\u9fff]", text) for text in texts)


def test_unsupported_conversation_is_visible_but_cannot_be_selected(monkeypatch):
    import streamlit as st
    service = ImportService(blocked=True)
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: Upload(b"offline blocked fixture"))
    app = AppTest.from_function(screen)
    app.session_state["service"] = service
    app.session_state["ui_language"] = "zh"
    app.run()
    assert not app.exception
    assert app.warning
    assert app.button(key="conversation-import:test:confirm").disabled
    assert not service.saved


class RecoveringImportService(ImportService):
    def preview(self, name, content):
        if not self.reads:
            self.reads += 1
            raise RuntimeError("conversation_import_dependencies_missing")
        return super().preview(name, content)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_failed_import_can_retry_same_file_without_automatic_retries(monkeypatch, language):
    import streamlit as st
    service = RecoveringImportService()
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: Upload(b"offline fixture"))
    app = AppTest.from_function(screen)
    app.session_state["service"] = service
    app.session_state["ui_language"] = language
    app.run()
    assert not app.exception
    assert app.error and service.reads == 1
    assert not service.saved
    app.run()
    assert service.reads == 1
    retry = app.button(key="conversation-import:test:retry")
    assert retry.label == ("Retry import" if language == "en" else "重试导入")
    retry.click().run()
    assert not app.exception and not app.error
    assert service.reads == 2
    app.button(key="conversation-import:test:confirm").click().run()
    assert not app.exception and app.success
    assert service.reads == 2 and len(service.saved) == 1


def test_removing_upload_clears_failed_identity_before_same_file_returns(monkeypatch):
    import streamlit as st
    service = RecoveringImportService()
    upload = {"value": Upload(b"offline fixture")}
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: upload["value"])
    app = AppTest.from_function(screen)
    app.session_state["service"] = service
    app.session_state["ui_language"] = "zh"
    app.run()
    assert not app.exception and app.error and service.reads == 1
    upload["value"] = None
    app.run()
    assert not app.exception and not app.error
    assert "conversation-import:test:identity" not in app.session_state
    upload["value"] = Upload(b"offline fixture")
    app.run()
    assert not app.exception and not app.error
    assert service.reads == 2
    assert not app.button(key="conversation-import:test:confirm").disabled


@pytest.mark.parametrize("language", ["zh", "en"])
def test_adapter_notices_are_visible_localized_and_never_echo_plugin_text(monkeypatch, language):
    import streamlit as st
    from lib.presentation.streamlit.conversation_import_controls import conversation_import_warnings
    from lib.presentation.streamlit.i18n import translate
    codes = ["source_has_no_reasoning", "surface_replacements_preserved_in_audit",
             "inherited_prefix_preserved", "developer_role_preserved",
             "recorded_tool_errors_require_review", "source_tool_schemas_absent",
             "empty_settlement_events_preserved_in_audit"]
    raw = "untrusted-plugin-secret=do-not-render [unsafe](https://example.invalid)"
    service = ImportService(warnings=[*codes, codes[0], raw, raw])
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: Upload(b"offline fixture"))
    app = AppTest.from_function(screen)
    app.session_state["service"] = service
    app.session_state["ui_language"] = language
    app.run()
    assert not app.exception and len(app.info) == 1
    displayed = app.info[0].value
    assert raw not in displayed and "untrusted-plugin-secret" not in displayed
    labels = conversation_import_warnings([*codes, raw])
    assert len(labels) == 8
    for label in labels:
        assert displayed.count(translate(label, language)) == 1
    assert not service.saved
    assert [item.value for item in app.text] == ["hello", "world"]
    if language == "en":
        import re
        assert not re.search(r"[\u4e00-\u9fff]", displayed)


@pytest.mark.parametrize("code", ["cache_invalid", "cache_mismatch", "receipt_mismatch"])
def test_invalid_staged_cache_is_not_described_as_an_unsupported_source(code):
    from lib.presentation.streamlit.conversation_import_controls import conversation_import_error
    from lib.presentation.streamlit.i18n import translate
    message = conversation_import_error("conversation_import_" + code)
    assert message == "暂存会话校验失败，暂时无法确认导入。请修复本地导入缓存后重试。"
    assert "failed validation" in translate(message, "en")


@pytest.mark.parametrize("language", ["zh", "en"])
def test_truncated_selection_explains_why_confirmation_is_disabled(monkeypatch, language):
    import streamlit as st
    from lib.presentation.streamlit.i18n import translate
    service = ImportService(truncated=True)
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: Upload(b"offline fixture"))
    app = AppTest.from_function(screen)
    app.session_state["service"] = service
    app.session_state["ui_language"] = language
    app.run()
    assert not app.exception
    assert app.button(key="conversation-import:test:confirm").disabled
    assert translate("记录过多，请分批整理后导入。", language) in [item.value for item in app.caption]
    assert not service.saved
