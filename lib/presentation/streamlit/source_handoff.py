"""Add a validated library source to the existing durable creation draft."""
from __future__ import annotations

from pathlib import PurePath

import streamlit as st

from lib.domain.creation_draft import validate_creation_draft
from lib.domain.dataset_assets import DOCUMENT_SOURCE_SUFFIXES, CONVERSATION_SOURCE_SUFFIXES


def use_library_source(source: dict, workspace: str, draft_application) -> None:
    """Callback for ``render_asset_catalog(on_use_source=...)``.

    The catalog must resolve the source first. Persistence succeeds before any
    session state changes, so an unreadable draft never replaces current work.
    Node model references stay in their existing session fields.
    """
    if not isinstance(source, dict) or set(source) != {"path", "source_mode"}:
        raise ValueError("invalid_generation_source")
    path, mode = source["path"], source["source_mode"]
    suffixes = {"文档资料": DOCUMENT_SOURCE_SUFFIXES, "Agent 上下文": CONVERSATION_SOURCE_SUFFIXES}
    if (not isinstance(path, str) or not path or len(path) > 4096
            or not isinstance(mode, str) or mode not in suffixes
            or PurePath(path).suffix.casefold() not in suffixes[mode]):
        raise ValueError("invalid_generation_source")
    draft_key = f"workflow-form-draft:{workspace}"
    existing = st.session_state.get(draft_key)
    current = validate_creation_draft(existing if existing is not None else draft_application.load())
    sources_key = f"workflow-sources:{workspace}:{mode}"
    selected = st.session_state.get(sources_key, current.get(sources_key, []))
    # Validate before merging instead of coercing malformed or oversized lists.
    validate_creation_draft({sources_key: selected})
    sources = list(dict.fromkeys([*selected, path]))
    if len(sources) > 200:
        raise ValueError("generation_source_limit_exceeded")
    mode_key = f"workflow-source-mode:{workspace}"
    values = validate_creation_draft({**current, mode_key: mode, sources_key: sources})
    draft_application.replace(values)
    st.session_state[draft_key] = values
    st.session_state[mode_key] = mode
    st.session_state[sources_key] = sources
    st.session_state[f"workflow-draft-loaded:{workspace}"] = True
    st.session_state[f"workflow-creation-mode:{workspace}"] = "自动生成"
    st.session_state[f"workflow-setup-node:{workspace}"] = "ingest"
    st.session_state.pop(f"workflow-entry-target:{workspace}", None)
    st.session_state.pop(f"workflow-draft-error:{workspace}", None)
    st.session_state["nav"] = "自动工作流"
