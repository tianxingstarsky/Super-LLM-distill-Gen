"""Source-preserving CPT preparation configured on its workflow node."""
from copy import deepcopy

import streamlit as st

from lib.domain.document_parser import supports_vision, vision_connection_signature
from lib.presentation.streamlit.workflow_node_settings import render_node_models


def _field(workspace, name, default):
    key = f"workflow-cpt-{name}:{workspace}"
    return st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(key, st.session_state.get(key, default))


def cpt_processing_snapshot(workspace):
    mode = _field(workspace, "processing-mode", "model")
    return ({"mode": "native"} if mode == "native" else
            {"mode": "model", "review_mode": _field(workspace, "review-mode", "text")})


def cpt_vision_unconfirmed(config, bindings, endpoints):
    if config.get("mode") != "model" or config.get("review_mode") != "vision":
        return False
    binding = bindings.get("cpt", {}).get("jev")
    return not binding or not supports_vision(endpoints.get(binding["backend"], {}), binding["model"])


def render_cpt_settings(workspace, source_mode, bindings, endpoints, *, save_field, backend_application=None):
    if source_mode == "开放需求":
        st.caption("开放需求生成合成语料，由模型生成并独立评审；没有原文时不作原页保真声明。")
        render_node_models("cpt", source_mode, workspace, bindings, endpoints,
                           backend_application=backend_application)
        return {"mode": "native"}
    mode_key = f"workflow-cpt-processing-mode:{workspace}"
    st.session_state[mode_key] = _field(workspace, "processing-mode", "model")
    mode = st.radio("CPT 清洗方式", ("model", "native"), key=mode_key, horizontal=True,
                    format_func=lambda value: "模型清洗与评审" if value == "model" else "仅本地检查",
                    on_change=save_field, args=(workspace, mode_key))
    if mode == "model":
        review_key = f"workflow-cpt-review-mode:{workspace}"
        st.session_state[review_key] = _field(workspace, "review-mode", "text")
        st.radio("原文核对方式", ("text", "vision"), key=review_key, horizontal=True,
                 format_func=lambda value: "对照原文文字" if value == "text" else "多模态对照原页",
                 on_change=save_field, args=(workspace, review_key))
        st.caption("清洗排版与重复页眉，再独立核对事实、数字、单位、公式和表格；不扩写知识，不补足数量。")
    else:
        st.caption("保留原文，仅做结构、隐私与去重检查；不调用 CPT 清洗或评审模型。")
    config = cpt_processing_snapshot(workspace)
    render_node_models("cpt", source_mode, workspace, bindings, endpoints,
                       backend_application=backend_application, cpt_processing=config)
    if config.get("review_mode") == "vision":
        st.caption("PDF、图片与 DOCX 图片块对照原页；Markdown、TXT、TeX 和 DOCX 文字块对照原文文字。报告记录实际评审方式。")
        binding = bindings.get("cpt", {}).get("jev")
        if binding and cpt_vision_unconfirmed(config, bindings, endpoints):
            endpoint = endpoints.get(binding["backend"], {})
            confirmation = st.checkbox("我已核实评审模型支持图片输入", key=(
                f"cpt-vision-confirm:{workspace}:{binding['backend']}:{binding['model']}:"
                f"{vision_connection_signature(endpoint)}"))
            if st.button("确认评审模型的多模态能力", disabled=not confirmation or backend_application is None,
                         key=f"cpt-vision-save:{workspace}"):
                try:
                    backend_application.confirm_model_vision(binding["backend"], binding["model"], True)
                except (OSError, ValueError):
                    st.error("模型能力确认未保存，请检查本机配置后重试。")
                else:
                    st.rerun()
            st.warning("评审模型尚未确认图片输入能力，请先核实并确认。")
    st.info("来源解析 → CPT 清洗 → 原文核对 → 去重与打包")
    return deepcopy(config)
