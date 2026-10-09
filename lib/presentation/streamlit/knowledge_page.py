"""Knowledge controls embedded in the workflow input node and source panel."""
from __future__ import annotations

import streamlit as st
from filelock import Timeout

from lib.presentation.streamlit.i18n import UntranslatedText
from lib.presentation.streamlit.shared import section_heading


ERRORS = {
    "knowledge_query_required": "请填写检索问题或主题。",
    "knowledge_connection_required": "请在输入解析节点保存 Qdrant 和嵌入模型连接。",
    "knowledge_connection_invalid": "连接配置无效，请检查地址、集合与模型名称。",
    "knowledge_connection_unavailable": "知识库或嵌入服务连接失败，请检查地址、密钥与服务状态。",
    "knowledge_vector_config_invalid": "找不到指定的向量配置，请核对集合及命名向量。",
    "knowledge_embedding_dimension_mismatch": "嵌入向量维度与集合不一致，请使用建库时的嵌入模型。",
    "knowledge_embedding_response_invalid": "嵌入服务未返回有效向量，请检查模型是否支持 embeddings。",
    "knowledge_payload_invalid": "检索结果缺少有效正文，请核对正文路径；单个片段最多 20,000 字符。",
    "knowledge_results_required": "请先检索并确认有可用正文。",
    "knowledge_source_changed": "索引期间资料发生变化，请重新建立索引。",
    "knowledge_index_selection_invalid": "请选择 1 至 200 份资料建立索引。",
    "knowledge_index_file_limit": "本地索引单文件最多 10 MiB，请先拆分资料。",
    "knowledge_index_text_limit": "解析正文超过索引限制，请先拆分资料。",
    "knowledge_pdf_limit": "PDF 已加密或超过 200 页，请解密或拆分后再索引。",
    "knowledge_docx_limit": "DOCX 展开内容过大，请先拆分资料。",
    "knowledge_document_parse_failed": "文档无法解析，请检查文件是否损坏或缺少解析组件。",
    "knowledge_latex_external_dependencies": "LaTeX 引用了外部文件；请先合并正文与参考文献，或上传包含图表的 PDF。不会读取本机引用路径。",
    "knowledge_latex_unsupported_syntax": "LaTeX 修改了字符解析规则，无法安全提取。请上传整理后的源文档或 PDF。",
    "knowledge_latex_invalid_source": "LaTeX 结构不完整，请检查文档环境、公式、表格和括号。",
    "knowledge_latex_atomic_block_limit": "LaTeX 单个公式、表格或代码块超过 20,000 字符。请缩小该结构后重新索引，系统不会切断它。",
    "knowledge_storage_unavailable": "本地知识库暂时无法读取，请检查索引文件与本机存储。",
}


def knowledge_error(error):
    return ERRORS.get(str(error), "知识库操作未完成，请检查资料、连接与本机存储。")


def render_knowledge_settings(application, workspace):
    provider_key = f"workflow-knowledge-provider:{workspace}"
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    if st.session_state.get(provider_key, draft.get(provider_key, "local")) != "qdrant":
        section_heading("本地知识库", "全文检索 · 保存在本机", "⌕")
        st.html('<div class="df-knowledge-route"><i aria-hidden="true">⌕</i>'
                '<strong>让已有知识成为生成依据</strong>'
                '<p>添加资料，检索相关片段，核对正文后生成。</p>'
                '<div><span>资料索引</span><b>→</b><span>相关片段</span><b>→</b><span>训练样本</span></div></div>')
        st.caption("索引和检索快照会保留，关闭或重启后仍可使用。")
        return
    section_heading("知识库连接", "检索配置保存在本机，密钥不进入训练包。", "⌕")
    try:
        connection = application.connection()
    except (OSError, ValueError, Timeout):
        st.error("连接配置无法读取，请检查本机存储。")
        connection = {}
    if connection.get("configured"):
        st.caption(UntranslatedText("Qdrant · " + connection["collection"]))
    with st.expander("Qdrant 连接配置", expanded=not connection.get("configured")):
        with st.form(f"knowledge-connection:{workspace}", clear_on_submit=True):
            url = st.text_input("Qdrant 地址", value=connection.get("url", "http://127.0.0.1:6333"))
            collection = st.text_input("集合名称", value=connection.get("collection", ""))
            key = st.text_input("Qdrant API 密钥", type="password", help="已保存的密钥可留空保留；更换服务地址后需要重新填写。")
            embedding_url = st.text_input("嵌入服务地址", value=connection.get("embedding_url", "http://127.0.0.1:8000/v1"))
            embedding_model = st.text_input("嵌入模型", value=connection.get("embedding_model", ""),
                help="必须与建库时使用的嵌入模型一致。填写支持 /embeddings 的服务。")
            embedding_key = st.text_input("嵌入服务 API 密钥", type="password")
            text_field = st.text_input("正文字段路径", value=connection.get("text_field", "text"), help="支持嵌套路径，例如 content 或 metadata.text。")
            source_field = st.text_input("来源字段路径", value=connection.get("source_field", "source"))
            vector_name = st.text_input("命名向量（可选）", value=connection.get("vector_name", ""))
            st.caption("检索时只将查询文字发送给嵌入服务，再用返回向量查询 Qdrant。")
            saved = st.form_submit_button("保存知识库连接", width="stretch")
        if saved:
            try:
                application.save_connection({"url": url, "collection": collection,
                    "embedding_url": embedding_url, "embedding_model": embedding_model,
                    "text_field": text_field, "source_field": source_field, "vector_name": vector_name}, key, embedding_key)
            except (OSError, ValueError, Timeout) as error:
                st.error(knowledge_error(error))
            else:
                st.session_state[f"knowledge-connection-saved:{workspace}"] = True
                st.rerun()
    if st.session_state.pop(f"knowledge-connection-saved:{workspace}", False):
        st.success("知识库连接已保存。")
    if connection.get("configured") and st.button("检查集合连接", key=f"knowledge-check:{workspace}"):
        try:
            info = application.check_connection()
        except (OSError, ValueError, Timeout) as error:
            st.error(knowledge_error(error))
        else:
            st.success(f"集合可用 · 向量维度 {info['dimensions']:,}")


def _upload_sources(cache, workspace):
    uploads = st.session_state.get(f"knowledge-upload:{workspace}", [])
    try:
        rows = cache.store([(item.name, item.getvalue()) for item in uploads]) if uploads else []
        current = st.session_state.get(f"knowledge-index-sources:{workspace}", [])
        selected = list(dict.fromkeys([*current, *(row["path"] for row in rows)]))
        if len(selected) > 200:
            raise ValueError("knowledge_index_selection_invalid")
        st.session_state[f"knowledge-index-sources:{workspace}"] = selected
        st.session_state.pop(f"knowledge-upload-error:{workspace}", None)
    except (ValueError, OSError, Timeout):
        st.session_state[f"knowledge-upload-error:{workspace}"] = True


def render_knowledge_source(application, workspace, files, *, input_cache=None, save_field=None):
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    def control_key(field, default):
        key = f"{field}:{workspace}"
        if key not in st.session_state:
            st.session_state[key] = draft.get(key, default)
        return key
    def persist(key):
        return {"on_change": save_field, "args": (workspace, key)} if save_field else {}
    provider_key = control_key("workflow-knowledge-provider", "local")
    provider = st.segmented_control("知识库来源", ("local", "qdrant"), default=None,
        key=provider_key, format_func=lambda value: "本地知识库" if value == "local" else "Qdrant",
        **persist(provider_key)) or "local"
    try:
        status = application.status() if provider == "local" else application.connection()
    except (OSError, ValueError, Timeout):
        status = {"revision": "invalid"}
        st.error("知识库状态无法读取，请检查本机存储。")
    if provider == "local":
        st.caption(f"本地全文检索 · {status.get('documents', 0):,} 份资料 · {status.get('chunks', 0):,} 个片段")
        with st.expander("添加或更新知识资料", expanded=not status.get("chunks")):
            if input_cache is not None:
                st.file_uploader("上传知识资料", type=["md", "txt", "tex", "latex", "pdf", "docx"], accept_multiple_files=True,
                    key=f"knowledge-upload:{workspace}", on_change=_upload_sources, args=(input_cache, workspace), max_upload_size=10)
            if st.session_state.get(f"knowledge-upload-error:{workspace}"):
                st.error("知识资料未能保存，请检查文件格式与本机存储。")
            labels = {row["path"]: row["label"] for row in files}
            key = f"knowledge-index-sources:{workspace}"
            unavailable = 0
            if key in st.session_state:
                for path in st.session_state[key]:
                    if path not in labels:
                        try:
                            described = application.describe_source(path)
                            labels[path] = described["label"]
                        except (ValueError, OSError):
                            unavailable += 1
                st.session_state[key] = [path for path in st.session_state[key] if path in labels]
            if unavailable:
                st.warning("部分已选知识资料已失联，请重新上传或选择。")
            if len(files) >= 5000:
                st.caption("仅列出前 5,000 份来源，已选资料会保留；更多文件可从资料库搜索后添加。")
            sources = st.multiselect("加入索引的资料", list(labels), format_func=lambda path: UntranslatedText(labels[path]), key=key, max_selections=200)
            chunk = st.number_input("知识片段目标字符数", 200, 20000, 2000, key=f"knowledge-index-chunk:{workspace}")
            if st.button("建立／更新本地索引", disabled=not sources, key=f"knowledge-index:{workspace}", width="stretch"):
                try:
                    with st.spinner("正在建立本地索引…"):
                        status = application.index(sources, int(chunk))
                except (OSError, ValueError, ImportError, Timeout) as error:
                    st.error(knowledge_error(error))
                else:
                    st.success(f"索引已保存 · {status['documents']:,} 份资料 · {status['chunks']:,} 个片段")
    elif not status.get("configured"):
        st.info("点击输入解析节点，配置 Qdrant 连接与嵌入模型。")
    query_key = control_key("workflow-knowledge-query", "")
    query = st.text_area("检索问题或主题", key=query_key, height=90, max_chars=2000, **persist(query_key))
    limit_key = control_key("workflow-knowledge-limit", 10)
    limit = st.number_input("最多检索片段", 1, 50, key=limit_key, **persist(limit_key))
    signature = (provider, query.strip(), int(limit), status.get("revision"))
    result_key = f"knowledge-result:{workspace}"
    saved_key = f"knowledge-materialized:{workspace}"
    prior = st.session_state.get(result_key)
    if prior and prior[0] != signature:
        st.session_state.pop(result_key, None)
        st.session_state.pop(saved_key, None)
        st.caption("检索条件或知识库已变化，请重新检索。")
    if st.button("检索并预览", type="primary", key=f"knowledge-search:{workspace}",
                 disabled=not query.strip() or provider == "qdrant" and not status.get("configured"), width="stretch"):
        try:
            with st.spinner("正在检索知识片段…"):
                result = application.retrieve(provider, query, int(limit))
            st.session_state[result_key] = (signature, result)
            st.session_state.pop(saved_key, None)
        except (OSError, ValueError, Timeout) as error:
            st.session_state.pop(result_key, None)
            st.session_state.pop(saved_key, None)
            st.error(knowledge_error(error))
    prior = st.session_state.get(result_key)
    if not prior:
        return [], {}
    result = prior[1]
    if not result["hits"]:
        st.info("未找到相关片段，请调整检索词或补充知识资料。")
        return [], {}
    st.caption(f"已检索 {len(result['hits']):,} 个片段 · 正文将作为本次生成来源")
    if saved_key not in st.session_state:
        try:
            st.session_state[saved_key] = application.materialize(result)
        except (OSError, ValueError, Timeout) as error:
            st.error(knowledge_error(error))
            return [], {}
    saved = st.session_state[saved_key]
    return [row["path"] for row in saved], {row["path"]: row["name"] for row in saved}


def render_knowledge_preview(workspace):
    prior = st.session_state.get(f"knowledge-result:{workspace}")
    if not prior or not prior[1]["hits"]:
        st.caption("检索后在此核对命中正文与来源。")
        return
    signature, result = prior
    section_heading("知识片段预览", "本次任务使用检索时保存的正文快照。", "▤")
    labels = {i: f"{i + 1} · {hit['source']} · {hit['location']}" for i, hit in enumerate(result["hits"])}
    selected = st.selectbox("查看知识片段", list(labels), format_func=lambda i: UntranslatedText(labels[i]),
        key=f"knowledge-preview:{workspace}:{hash(signature)}")
    hit = result["hits"][selected]
    st.caption(UntranslatedText(f"{hit['location']} · score {hit['score']:.6g}"))
    st.code(hit["text"], language=None, wrap_lines=True, height=260)
