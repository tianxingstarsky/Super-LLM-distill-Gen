"""Compact, node-local experience editions distilled from complete DSH traces."""
from __future__ import annotations

from copy import deepcopy
import hashlib

import streamlit as st

from lib.domain.workflow_scale import validate_node_models
from lib.presentation.streamlit.i18n import UntranslatedText


def _binding(workspace, scope, prompt_id):
    node = ("multiturn" if prompt_id.startswith("workflow.reading_") else
            "cot" if prompt_id.startswith("workflow.cot_") else
            "trim" if prompt_id == "workflow.trim" else
            st.session_state.get(f"workflow-setup-node:{workspace}"))
    node = node or {"director": "director", "question": "director", "answer": "sft",
                    "reasoning": "cot", "review": "review", "cpt": "cpt"}.get(scope)
    key = f"workflow-node-bindings:{workspace}"
    saved = st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(key, {})
    bindings = st.session_state.get(key, saved)
    if not isinstance(bindings, dict) or not isinstance(bindings.get(node, {}), dict):
        return None
    roles = bindings.get(node, {})
    role = "generation" if "generation" in roles else "judge" if scope == "review" else "generation"
    binding = roles.get(role)
    if not binding:
        return None
    try:
        return validate_node_models({node: {role: binding}})[node][role]
    except (ValueError, TypeError, KeyError):
        return None


def _apply(workspace, scope, record, text_key, enabled_key, save_field):
    # This component renders before the experience editor. Save the whole
    # current choice before a rerun can hide any widget.
    st.session_state[text_key] = record["text"]
    st.session_state[enabled_key] = True
    save_field(workspace, text_key)
    save_field(workspace, enabled_key)
    _save_parent(workspace, scope, record["id"], save_field)


def _save_parent(workspace, scope, identifier, save_field):
    st.session_state[f"experience-evolution:{workspace}:{scope}:parent"] = identifier
    key = f"workflow-agent-experience-parent:{workspace}:{scope}"
    st.session_state[key] = identifier
    save_field(workspace, key)


def _remember(prefix, record):
    st.session_state[prefix + ":candidate"] = record
    st.session_state[prefix + ":text"] = record["text"]


def _error(error, en):
    code = str(error)
    if getattr(error, "code", None) in {"service_quota_exhausted", "insufficient_quota", "insufficient_balance"}:
        return ("The model provider has insufficient balance. Recharge there before proposing another edition."
                if en else "模型上游额度不足，请在对应平台充值后再提炼。")
    if "no_record_fits_limits" in code or "episodes_too_large" in code:
        return ("No complete conversation fits. Choose a smaller DSH session file; histories will not be cut."
                if en else "没有完整会话能装入本次提炼范围，请改选较小的 DSH 会话文件；不会截断过程。")
    if "evidence_mismatch" in code:
        return ("The proposal cited text absent from the source. It was not saved."
                if en else "提案引用了原始记录中不存在的内容，本次未保存。")
    if "binding" in code or "model" in code:
        return "Check this node's generation model." if en else "请检查当前节点的生成模型配置。"
    if "not_found" in code or "corrupt" in code or "cache_" in code or "receipt_" in code:
        return ("The saved source or edition could not be verified. Load it again before continuing."
                if en else "暂存来源或风格版本未能通过校验，请重新载入后继续。")
    return ("This operation did not complete. Your current experience is still in the editor."
            if en else "本次操作未完成，当前经验仍保留在编辑框中。")


def _use_initial(app, workspace, scope, prefix, text_key, enabled_key, save_field, en):
    # Callbacks execute before the parent toggle and preview textarea are
    # instantiated; adopting must not modify their state during rendering.
    st.session_state.pop(prefix + ":error", None)
    try:
        from lib.contexts.workflows.contracts import initial_experience_text
        record = app.save_version(scope, initial_experience_text(scope),
            label="Compact and complete · initial v1" if en else "简洁全面 · 初始 v1", status="initial")
        _remember(prefix, record)
        _apply(workspace, scope, record, text_key, enabled_key, save_field)
    except (OSError, ValueError, RuntimeError) as error:
        st.session_state[prefix + ":error"] = _error(error, en)


def _adopt(app, workspace, scope, prefix, text_key, enabled_key, save_field, en):
    st.session_state.pop(prefix + ":error", None)
    try:
        candidate = st.session_state[prefix + ":candidate"]
        text = st.session_state[prefix + ":text"]
        record = candidate
        if text != candidate["text"] or candidate.get("status") == "candidate":
            record = app.save_version(scope, text, parent_id=candidate["id"],
                label="My evolved style" if en else "我的进化风格")
        _apply(workspace, scope, record, text_key, enabled_key, save_field)
        _remember(prefix, record)
    except (OSError, ValueError, RuntimeError) as error:
        st.session_state[prefix + ":error"] = _error(error, en)


def _save_current(app, workspace, scope, prefix, text_key, save_field, en):
    st.session_state.pop(prefix + ":error", None)
    try:
        record = app.save_version(scope, st.session_state.get(text_key, ""),
            parent_id=st.session_state.get(prefix + ":parent"), label="My style" if en else "我的风格")
        _remember(prefix, record)
        _save_parent(workspace, scope, record["id"], save_field)
    except (OSError, ValueError, RuntimeError) as error:
        st.session_state[prefix + ":error"] = _error(error, en)


def _load_version(app, prefix, en):
    st.session_state.pop(prefix + ":error", None)
    try:
        _remember(prefix, app.get_version(st.session_state[prefix + ":version"]))
    except (OSError, ValueError, RuntimeError) as error:
        st.session_state[prefix + ":error"] = _error(error, en)


def _advance_sources(prefix, *, first=False):
    current = st.session_state.get(prefix + ":sources", {})
    cursor = None if first else current.get("next_after_id")
    if first or cursor is not None:
        st.session_state[prefix + ":after-id"] = cursor
        st.session_state.pop(prefix + ":sources", None)


def render_experience_evolution(workspace, scope, prompt_id, *, text_key, enabled_key, save_field):
    """Use the current node model only after a deliberate proposal click."""
    en = st.session_state.get("ui_language", "zh") == "en"
    prefix = f"experience-evolution:{workspace}:{scope}"
    parent_key = f"workflow-agent-experience-parent:{workspace}:{scope}"
    saved = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    if parent_key in st.session_state or parent_key in saved or prefix + ":parent" not in st.session_state:
        st.session_state[prefix + ":parent"] = st.session_state.get(parent_key, saved.get(parent_key))
    with st.expander("Evolve a style from traces" if en else "从轨迹进化风格"):
        st.caption("Keep useful lessons and counterexamples. Each edition retains its original history."
                   if en else "整理有效做法、反例与反思；每个版本保留原始操作历史。")
        try:
            from lib.bootstrap.experience_evolution import experience_evolution_application
            app = experience_evolution_application()
            versions = app.list_versions(scope)
        except (ImportError, OSError, ValueError, RuntimeError) as error:
            st.warning(_error(error, en))
            return
        initial, save = st.columns(2, gap="small")
        with initial:
            st.button("Use initial style" if en else "使用初始风格", key=prefix + ":initial", width="stretch",
                on_click=_use_initial, args=(app, workspace, scope, prefix, text_key, enabled_key, save_field, en))
        with save:
            st.button("Save current edition" if en else "保存当前风格版本", key=prefix + ":save", width="stretch",
                on_click=_save_current, args=(app, workspace, scope, prefix, text_key, save_field, en))
        if versions:
            by_id = {item["id"]: item for item in versions}
            selection_key = prefix + ":version"
            if st.session_state.get(selection_key) not in by_id:
                st.session_state[selection_key] = None
            st.selectbox("Saved editions" if en else "已保存版本", [None, *by_id], key=selection_key,
                format_func=lambda identifier: ("Select an edition" if en else "选择版本") if identifier is None else
                    UntranslatedText(by_id[identifier]["label"] + " · " + by_id[identifier]["created_at"][:16]
                                     + " · " + identifier[:6]))
            st.button("Load edition" if en else "载入所选版本", key=prefix + ":load",
                disabled=not st.session_state.get(selection_key), width="stretch",
                on_click=_load_version, args=(app, prefix, en))

        # Existing workbench imports already hold a durable, authenticated
        # staging id. Reusing it avoids converting the database again.
        preview = st.session_state.get(f"conversation-import:{workspace}:preview")
        own_upload = not isinstance(preview, dict) or not preview.get("import_id")
        if not own_upload:
            own_upload = st.checkbox("Choose another DSH session" if en else "另选 DSH 会话", key=prefix + ":other")
        try:
            from lib.bootstrap.conversation_import import conversation_import_application
            importer = conversation_import_application()
            if own_upload:
                upload = st.file_uploader("DSH session file" if en else "DSH 会话文件",
                    type=["jsonl", "zstd", "db", "sqlite", "sqlite3"], max_upload_size=50,
                    key=prefix + ":upload")
                preview = None
                if upload is not None:
                    content = upload.getvalue()
                    identity = (upload.name, hashlib.sha256(content).hexdigest())
                    if st.session_state.get(prefix + ":upload-identity") != identity:
                        st.session_state.pop(prefix + ":upload-preview", None)
                        st.session_state[prefix + ":upload-identity"] = identity
                        with st.spinner("Restoring complete DSH traces…" if en else "正在还原完整 DSH 轨迹…"):
                            st.session_state[prefix + ":upload-preview"] = importer.preview(upload.name, content)
                    preview = st.session_state.get(prefix + ":upload-preview")
            sources = None
            if isinstance(preview, dict) and preview.get("import_id"):
                identity = preview["import_id"]
                if st.session_state.get(prefix + ":source-import") != identity:
                    st.session_state[prefix + ":source-import"] = identity
                    st.session_state[prefix + ":after-id"] = None
                    st.session_state.pop(prefix + ":sources", None)
                after_id = st.session_state.get(prefix + ":after-id")
                cached = st.session_state.get(prefix + ":sources")
                if not cached or cached["import_id"] != identity or cached.get("after_id") != after_id:
                    cached = importer.evolution_sources(identity, max_records=8, max_chars=60000, after_id=after_id)
                    st.session_state[prefix + ":sources"] = cached
                sources = cached
                counts = sources["counts"]
                position = sources.get("position", {})
                span = f"{position.get('first_selected_index', 1)}–{position.get('last_selected_index', counts['selected_records'])}/{counts.get('total_records', counts['selected_records'])}"
                st.caption(UntranslatedText(
                    f"{sources['source_name']} · source positions {span} · {counts['selected_records']} complete conversations"
                    if en else f"{sources['source_name']} · 资料序号 {span} · 本组选入 {counts['selected_records']} 段完整会话"))
                if counts["skipped_records"]:
                    st.caption("Whole records are selected within 8 conversations / 60,000 characters; no messages are cut."
                               if en else "本次最多 8 段、60,000 字符；超限会话整体跳过，不裁剪中间过程。")
                first, next_batch = st.columns(2, gap="small")
                with first:
                    st.button("First group" if en else "回到首组", key=prefix + ":first-group", width="stretch",
                        disabled=after_id is None, on_click=_advance_sources, args=(prefix,), kwargs={"first": True})
                with next_batch:
                    st.button("Next conversations" if en else "下一组会话", key=prefix + ":next-group", width="stretch",
                        disabled=sources.get("next_after_id") is None, on_click=_advance_sources, args=(prefix,))
            else:
                st.caption("Select a DSH session, or use a session already imported in this workbench."
                           if en else "选择 DSH 会话，或复用工作台已导入的会话。失败记录也可用于提炼经验。")
        except (OSError, ValueError, RuntimeError) as error:
            sources = None
            st.warning(_error(error, en))
            if st.session_state.get(prefix + ":after-id") is not None:
                st.button("First group" if en else "回到首组", key=prefix + ":first-after-error", width="stretch",
                    on_click=_advance_sources, args=(prefix,), kwargs={"first": True})

        binding = _binding(workspace, scope, prompt_id)
        if binding:
            st.caption(UntranslatedText(("This node: " if en else "沿用本节点：")
                + binding["backend"] + " · " + binding["model"]))
        else:
            st.caption("Configure this node's generation model before proposing an edition."
                       if en else "先在当前节点配置生成模型，再提炼版本。")
        st.text_area("Style direction (optional)" if en else "本版方向（可选）", key=prefix + ":instruction",
            height=80, max_chars=4000,
            placeholder="For example: clear visual hierarchy and simple architecture." if en else "例如：清晰的视觉层级、容易发现的入口、简洁的架构。")
        if st.button("Propose one edition" if en else "提炼一版", key=prefix + ":propose", width="stretch",
                     disabled=not binding or sources is None):
            try:
                # Reauthenticate the stage at the action boundary. Cached UI
                # counts alone cannot authorize changed source bytes.
                sources = importer.evolution_sources(sources["import_id"], max_records=8, max_chars=60000,
                    after_id=sources.get("after_id"))
                source_receipt = {key: deepcopy(value) for key, value in sources.items() if key != "records"}
                with st.spinner("Distilling one edition…" if en else "正在提炼一版…"):
                    candidate = experience_evolution_application(binding=binding).propose(scope,
                        st.session_state.get(text_key, ""), sources["records"],
                        instruction=st.session_state.get(prefix + ":instruction", ""),
                        parent_id=st.session_state.get(prefix + ":parent"), source_receipt=source_receipt)
                _remember(prefix, candidate)
            except Exception as error:
                st.error(_error(error, en))
        if candidate := st.session_state.get(prefix + ":candidate"):
            st.caption(("Model proposal, not quality certification. Edit it before adopting."
                        if en else "模型提案不代表质量通过，可编辑后再采用。")
                       if candidate.get("status") == "candidate" else
                       ("Saved style edition; not independently verified." if en else "已保存的风格版本；未独立验证。"))
            st.text_area("Edition preview" if en else "版本预览", key=prefix + ":text", height=180, max_chars=32768)
            if candidate.get("rules"):
                st.json({"rules": candidate["rules"], "source_receipt": candidate.get("source_receipt", {})}, expanded=False)
            st.button("Use as current experience" if en else "采用为当前经验", key=prefix + ":adopt", width="stretch",
                on_click=_adopt, args=(app, workspace, scope, prefix, text_key, enabled_key, save_field, en))
        if error := st.session_state.get(prefix + ":error"):
            st.error(error)
