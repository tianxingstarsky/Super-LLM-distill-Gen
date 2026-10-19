"""Compact, persistent local parser setup inside the existing input node."""
from pathlib import Path

import streamlit as st

from lib.domain.document_parser import validate_document_parser
from lib.infrastructure.docling_document import _package_versions, _formula_package_versions, formula_models_path


DOCLING_ERRORS = {
    "document_docling_unavailable": "Docling 可选依赖尚未安装，请先按文档准备本地解析环境。",
    "document_docling_version_mismatch": "Docling 版本与适配器不一致，请使用 requirements-docling.txt 中的固定版本。",
    "document_docling_local_models_required": "请填写已准备好的本地模型目录；解析不会自动下载模型。",
    "document_docling_formula_models_required": "公式识别模型未就绪。请在 Docling 模型目录中准备完整的 ds4sd--CodeFormulaV2 文件夹。",
    "document_docling_formula_undecoded": "此页含未解码公式，已隔离并保留原文与页码。请检查本地公式识别设置，并对照原 PDF 复核。",
    "document_docling_page_span_ambiguous": "跨页内容无法按原始位置可靠拆分，解析已停止。请对照原 PDF 检查，或改用其他解析方式。",
    "invalid_document_docling_ocr_languages": "OCR 语言代码无效，请使用 en、ch_sim 等 EasyOCR 语言代码。",
    "invalid_document_parser": "文档解析配置无效，请检查输入节点。",
}
_FIELDS = {"artifacts": "", "ocr": False, "ocr-models": "", "languages": "ch_sim,en", "formula": False}


def _value(workspace, field):
    key = f"workflow-docling-{field}:{workspace}"
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    return st.session_state.get(key + ":draft", st.session_state.get(key, draft.get(key, _FIELDS[field])))


def _widget_key(workspace, field):
    key = f"workflow-docling-{field}:{workspace}"
    if key not in st.session_state:
        st.session_state[key] = _value(workspace, field)
    return key


def docling_snapshot(workspace):
    value = {"mode": "docling", "artifacts_path": _value(workspace, "artifacts"),
             "ocr": _value(workspace, "ocr"), "formula_enrichment": _value(workspace, "formula")}
    if value["ocr"]:
        value.update(ocr_models_path=_value(workspace, "ocr-models"),
                     ocr_languages=[item.strip() for item in _value(workspace, "languages").split(",")
                                    if item.strip()])
    return value


def docling_setup_issue(config, *, require_dependencies=True):
    """Cheap preflight only. Exact weight hashing belongs to submitted runs."""
    try:
        config = validate_document_parser(config)
        if not require_dependencies:
            return None
        _package_versions(config["ocr"])
        if config["formula_enrichment"]:
            _formula_package_versions()
            formula_models_path(config)
        artifacts = Path(config["artifacts_path"])
        required = [artifacts / "ds4sd--docling-layout-heron",
                    artifacts / "ds4sd--docling-models" / "model_artifacts" / "tableformer" / "accurate"]
        if config["ocr"]:
            required.append(Path(config["ocr_models_path"]))
        if any(not folder.is_dir() or not any(folder.iterdir()) for folder in required):
            return "document_docling_local_models_required"
    except ValueError as error:
        return str(error)
    except OSError:
        return "document_docling_local_models_required"
    return None


def _save(workspace, field):
    from lib.presentation.streamlit.workflow_node_settings import _persist_draft_value
    key = f"workflow-docling-{field}:{workspace}"
    value = st.session_state.get(key, _FIELDS[field])
    st.session_state[key + ":draft"] = value
    _persist_draft_value(workspace, key, value)


def render_docling_settings(workspace):
    st.caption("PDF 在本机识别版面与表格，解析结果存入可复用资料库。其他文档沿用本地文本解析。")
    st.text_input("Docling 本地模型目录", key=_widget_key(workspace, "artifacts"),
                  on_change=_save, args=(workspace, "artifacts"))
    st.checkbox("启用本地公式识别（LaTeX）", key=_widget_key(workspace, "formula"),
                on_change=_save, args=(workspace, "formula"),
                help="使用本地 CodeFormulaV2 识别公式区域，不调用云端 API。行内公式、符号与复杂排版仍需对照原文核验。")
    enabled = st.checkbox("启用本地 OCR（扫描 PDF）", key=_widget_key(workspace, "ocr"),
                          on_change=_save, args=(workspace, "ocr"))
    if enabled:
        st.text_input("EasyOCR 本地模型目录", key=_widget_key(workspace, "ocr-models"),
                      on_change=_save, args=(workspace, "ocr-models"))
        st.text_input("OCR 语言代码", key=_widget_key(workspace, "languages"),
                      on_change=_save, args=(workspace, "languages"), help="以逗号分隔，例如 ch_sim,en。")
    config = docling_snapshot(workspace)
    issue = docling_setup_issue(config)
    if issue:
        st.warning(DOCLING_ERRORS.get(issue, DOCLING_ERRORS["invalid_document_parser"]))
    else:
        st.caption("本地依赖与目录已就绪；开始任务时会核验模型内容，识别结果仍需质量检查。")
    if not config["formula_enrichment"]:
        st.caption("公式识别未开启：检测到的公式区域可能仅保留未解码标记。")
    st.caption("此功能不会下载模型或调用云端 API。配置方法见 docs/open-source-solutions.md。")
    return config
