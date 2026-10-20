"""Compact, persistent local parser setup inside the existing input node."""
from pathlib import Path

import streamlit as st

from lib.domain.document_parser import validate_document_parser
from lib.infrastructure.docling_document import _package_versions, _formula_package_versions, formula_models_path


DOCLING_ERRORS = {
    "document_docling_unavailable": "Docling 可选依赖尚未安装，请先按文档准备本地解析环境。",
    "document_docling_version_mismatch": "解析依赖版本不匹配。独立环境请使用 requirements-docling-runtime.txt，当前环境请使用 requirements-docling.txt。",
    "document_docling_local_models_required": "请填写已准备好的本地模型目录；解析不会自动下载模型。",
    "document_docling_formula_models_required": "公式识别模型未就绪。请在 Docling 模型目录中准备完整的 ds4sd--CodeFormulaV2 文件夹。",
    "document_docling_formula_undecoded": "此页含未解码公式，已隔离并保留原文与页码。请检查本地公式识别设置，并对照原 PDF 复核。",
    "document_docling_page_span_ambiguous": "跨页内容无法按原始位置可靠拆分，解析已停止。请对照原 PDF 检查，或改用其他解析方式。",
    "invalid_document_docling_ocr_languages": "OCR 语言代码无效，请使用 en、ch_sim 等 EasyOCR 语言代码。",
    "invalid_document_parser": "文档解析配置无效，请检查输入节点。",
    "document_docling_runtime_unavailable": "独立解析环境不可用，请检查 Python 路径或重新安装本地解析环境。",
    "document_docling_worker_runtime_unavailable": "独立解析环境不可用，请检查 Python 路径或重新安装本地解析环境。",
    "document_docling_worker_timeout": "本地解析超时，未发布不完整结果。可以检查文档后重试。",
    "document_docling_worker_failed": "本地解析进程未完成，未发布不完整结果。请检查模型文件与解析环境。",
    "document_docling_worker_stop_failed": "本地解析进程未能完全停止，请检查本机进程后重试。已有结果会保留。",
    "document_docling_runtime_models_required": "独立解析环境的版面或表格模型文件不完整，请检查本地模型目录。",
    "document_docling_runtime_formula_models_required": "本地公式模型文件不完整，请准备完整的 Pix2Text MFR 1.5 模型目录。",
    "document_docling_ocr_models_required": "本地 OCR 模型文件不完整，请准备检测模型及所选语言对应的识别模型。",
}
_INLINE_ERRORS = {
    "document_docling_inline_runtime_requires_formula": (
        "行内公式环境需要独立解析环境，并开启本地公式识别。",
        "Inline formulas require an isolated parser environment and local formula recognition."),
    "document_docling_inline_version_mismatch": (
        "行内公式环境版本不匹配，请使用 PaddleOCR 3.7.0、PaddleX 3.7.0 和 ONNX Runtime 1.31.0。",
        "The inline formula environment requires PaddleOCR 3.7.0, PaddleX 3.7.0 and ONNX Runtime 1.31.0."),
    "document_docling_inline_models_required": (
        "行内公式版面模型不完整，请在本地模型目录中准备完整的 PaddlePaddle--PP-DocLayoutV3_onnx 文件夹。",
        "Prepare the complete PaddlePaddle--PP-DocLayoutV3_onnx folder in the local model directory."),
}
# Existing submit/background error surfaces use this shared phrase dictionary.
# Register the new local phrases there as well as using bilingual widget copy.
DOCLING_ERRORS.update({code: phrases[0] for code, phrases in _INLINE_ERRORS.items()})
from lib.presentation.streamlit.i18n import ZH_EN
ZH_EN.update(dict(_INLINE_ERRORS.values()))
_FIELDS = {"artifacts": "", "ocr": False, "ocr-models": "", "languages": "ch_sim,en",
           "formula": False, "runtime": "", "inline-runtime": ""}


def _tr(zh, en):
    from lib.presentation.streamlit.i18n import language_code
    return en if language_code(st.session_state.get("ui_language", "zh")) == "en" else zh


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
    if _value(workspace, "runtime").strip():
        value["runtime_python"] = _value(workspace, "runtime").strip()
    if (value.get("runtime_python") and value["formula_enrichment"]
            and _value(workspace, "inline-runtime").strip()):
        value["inline_runtime_python"] = _value(workspace, "inline-runtime").strip()
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
        if config.get("runtime_python"):
            from lib.infrastructure.docling_preflight import inspect_runtime_cached, validate_runtime_assets
            info = inspect_runtime_cached(config["runtime_python"])
            validate_runtime_assets(config, runtime_info=info)
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
    st.text_input("独立解析环境 Python（可选）", key=_widget_key(workspace, "runtime"),
                  on_change=_save, args=(workspace, "runtime"),
                  help="使用独立环境的原生词级解析，改善表格单元格对齐。留空沿用当前环境；Windows 安装目录需使用英文路径。")
    st.checkbox("启用本地公式识别（LaTeX）", key=_widget_key(workspace, "formula"),
                on_change=_save, args=(workspace, "formula"),
                help="使用本地公式模型识别公式区域，不调用云端 API。行内公式、符号与复杂排版仍需对照原文核验。")
    if _value(workspace, "runtime").strip() and _value(workspace, "formula"):
        st.text_input(_tr("行内公式独立环境 Python（可选）", "Inline formula Python environment (optional)"),
                      key=_widget_key(workspace, "inline-runtime"),
                      on_change=_save, args=(workspace, "inline-runtime"),
                      help=_tr(
                          "填写独立的行内公式检测环境，启用本地检测与公式识别；留空保留现有解析方式。边界不确定时保留原文并标记待复核。",
                          "Set a separate environment for local inline formula detection and recognition. Leave blank to keep current parsing. Uncertain boundaries preserve the source for review."))
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
        st.warning(_tr(*_INLINE_ERRORS[issue]) if issue in _INLINE_ERRORS
                   else DOCLING_ERRORS.get(issue, DOCLING_ERRORS["invalid_document_parser"]))
    else:
        st.caption("本地依赖与目录已就绪；开始任务时会核验模型内容，识别结果仍需质量检查。")
    if not config["formula_enrichment"]:
        st.caption("公式识别未开启：检测到的公式区域可能仅保留未解码标记。")
    st.caption("此功能不会下载模型或调用云端 API。配置方法见 docs/open-source-solutions.md。")
    return config
