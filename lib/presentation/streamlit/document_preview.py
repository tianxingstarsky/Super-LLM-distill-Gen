"""Compact, optional source preview inside the selected input node."""
from __future__ import annotations

import streamlit as st

from lib.application.document_preview_service import DocumentPreviewApplication
from lib.presentation.streamlit.i18n import UntranslatedText
from lib.presentation.streamlit.shared import section_heading


_ERRORS = {
    "preview_file_limit": "预览限单文件 2 MiB；此限制不改变正式任务的输入范围。",
    "preview_text_limit": "解析文字超过预览上限 250,000 字符；请拆分文档后预览。",
    "preview_page_limit": "PDF 超过预览上限 40 页；请使用较短文档预览。",
    "preview_docx_expansion_limit": "DOCX 展开内容超过预览上限 4 MiB，或包含过多/加密附件。",
    "preview_encrypted_pdf": "PDF 已加密；请先在本机解密后重新上传。",
    "preview_parser_unavailable": "当前环境缺少 PDF 或 DOCX 解析组件；MD / TXT 仍可预览。",
    "preview_source_changed": "来源文件已变化，请重新预览。",
    "preview_parse_failed": "文档解析失败。请检查文件是否损坏、编码或格式是否正确。",
    "preview_latex_external_dependencies": "LaTeX 引用了外部文件；请先合并正文与参考文献，或上传包含图表的 PDF。不会读取本机引用路径。",
    "preview_latex_unsupported_syntax": "LaTeX 修改了字符解析规则，无法安全提取。请上传整理后的源文档或 PDF。",
    "preview_latex_invalid_source": "LaTeX 结构不完整，请检查文档环境、公式、表格和括号。",
}


def render_document_preview(application: DocumentPreviewApplication, workspace: str,
                            selected: list[str], labels: dict[str, str], chunk_chars: int) -> None:
    section_heading("来源分块预览", "按当前分块参数查看真实内容；不会调用模型。", "▤")
    if not selected:
        st.caption("先在左侧添加并选择一份文档，即可在此预览。")
        return
    key = f"document-preview:{workspace}"
    source_key = key + ":source"
    if st.session_state.get(source_key) not in selected:
        st.session_state[source_key] = selected[0]
    source = st.selectbox("预览文档", selected, key=source_key,
                          format_func=lambda path: UntranslatedText(labels.get(path, path)))
    try:
        signature = application.signature(source, chunk_chars)
    except (OSError, ValueError):
        st.session_state.pop(key, None)
        st.warning("无法读取所选来源，请重新选择本机缓存中的文档。")
        return
    previous = st.session_state.get(key)
    if previous and previous.get("signature") != signature:
        st.session_state.pop(key, None)
        previous = None
        st.caption("来源或分块参数已变化，请重新预览。")
    if st.button("预览解析与分块", key=key + ":read", width="stretch"):
        st.session_state.pop(key, None)
        try:
            with st.spinner("正在本机解析文档…"):
                previous = application.preview(source, chunk_chars)
            st.session_state[key] = previous
        except (OSError, ValueError) as error:
            st.error(_ERRORS.get(str(error), "无法读取所选来源，请重新选择本机缓存中的文档。"))
            previous = None
    if not previous:
        st.caption("按需预览单文件，最多 2 MiB、250,000 字符；PDF 最多 40 页。")
        return
    st.caption(UntranslatedText(previous["label"]))
    st.caption(f"解析字符数：{previous['characters']:,} · 分块数：{previous['chunk_count']:,}")
    if not previous["chunks"]:
        st.warning("文档未提取到文字；扫描 PDF 可能需要先做文字识别。")
        return
    position_key = key + ":position:" + previous["sha256"] + ":" + str(chunk_chars)
    position = st.number_input("片段序号", 1, previous["chunk_count"], value=1, step=1, key=position_key)
    chunk = previous["chunks"][int(position) - 1]
    st.caption(f"当前片段字符数：{len(chunk):,} · 来源位置：document:chunk:{int(position) - 1}")
    st.code(UntranslatedText(chunk), language=None, wrap_lines=True, height=260)
    st.caption("这里只展示解析与分块结果，尚未执行隐私筛查、去重或质量审核。")
