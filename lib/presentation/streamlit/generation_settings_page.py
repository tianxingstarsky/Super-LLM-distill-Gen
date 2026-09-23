"""Generation preference editor backed by application use cases."""
from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from lib.bootstrap.generation_settings import generation_settings_application
from lib.presentation.streamlit.settings_style import SETTINGS_STYLE
from lib.presentation.streamlit.shared import page_header, section_heading


def render_generation_settings(root: Path, show_title: bool = True) -> None:
    if show_title:
        page_header("生成偏好", "设置训练数据的生成倾向、推理风格与语言规则。", "GENERATION PREFERENCES")
    st.html(SETTINGS_STYLE)
    settings = generation_settings_application(root)
    area = st.segmented_control("配置类别", settings.categories(), default="生成偏好", key="preference-area",
                                format_func=lambda value: {"生成偏好": "偏好配比"}.get(value, value),
                                label_visibility="collapsed") or "生成偏好"
    if st.session_state.pop("preference-refresh", False):
        for widget_key in ("pref-default-share", "pref-templates", "pref-shuffle", "pref-correction",
                           "pref-threshold", "pref-tagger", "pref-cot-style",
                           "pref-tendency-reasoning", "pref-tendency-context_use",
                           "pref-tendency-tool_use", "pref-tendency-bilingual"):
            st.session_state.pop(widget_key, None)
    try:
        snapshot = settings.load(area)
    except (OSError, ValueError) as error:
        st.error(f"无法读取配置：{error}")
        return
    original, document = snapshot.text, snapshot.document
    if snapshot.parse_error:
        st.warning(f"配置暂时无法解析：{snapshot.parse_error}。可在下方高级编辑中修复。")

    if area == "生成偏好":
        try:
            summary = settings.summary(snapshot)
        except ValueError as error:
            st.error(f"生成偏好配置无法用于表单：{error}。请在下方高级编辑中修复。")
            summary = None
        if summary is not None:
            st.html(
                '<div class="df-settings-pref-overview">'
                f'<div><span>默认样本占比</span><strong>{summary["default_share"]:.0%}</strong><small>保留无风格注入的基线</small></div>'
                f'<div><span>每维模板</span><strong>{summary["templates_per_dim"]}</strong><small>轮换生成，减少重复</small></div>'
                f'<div><span>后验校正</span><strong>{"已开启" if summary["correction_enabled"] else "已关闭"}</strong><small>偏差超过阈值时调整下批采样</small></div>'
                f'<div><span>推理输出</span><strong>{html.escape({"separated": "分字段", "tags": "原生 token", "plain": "合并正文", "drop": "仅答案"}.get(summary["cot_style"], summary["cot_style"]))}</strong><small>匹配模型的思考格式</small></div>'
                '</div>'
            )
            share_labels = {
                "default": ("默认", "#a9b9cf"), "reasoning": ("推理与反思", "#2375df"),
                "context_use": ("长上下文", "#29acd0"), "tool_use": ("工具使用", "#1aaf86"),
                "bilingual": ("双语桥接", "#8f74d9"),
            }
            distribution = ''.join(
                f'<i style="width:{summary["weights"][key] * 100:.3f}%;background:{color}"></i>'
                for key, (_, color) in share_labels.items()
            )
            legend = ''.join(
                f'<span><i style="background:{color}"></i>{label} {summary["weights"][key]:.0%}</span>'
                for key, (label, color) in share_labels.items()
            )
            st.html('<div class="df-settings-distribution"><strong>当前已保存的生成倾向</strong>'
                    '<div class="df-settings-distribution-body"><div class="df-settings-distribution-bar">' +
                    distribution + '</div><div class="df-settings-distribution-legend">' + legend + '</div></div></div>')
            left, right = st.columns([1.25, 1], gap="large")
            with left, st.container(border=True, key="settings-pref-mix"):
                section_heading("训练数据配比", "保留默认基线，其他倾向会按相对权重自动归一化。", "◎")
                default_share = st.slider("默认样本占比", min_value=int(summary["default_floor"] * 100),
                                          max_value=95, value=round(summary["default_share"] * 100),
                                          format="%d%%", key="pref-default-share")
                descriptions = (
                    ("reasoning", "推理与反思"), ("context_use", "长上下文利用"),
                    ("tool_use", "工具使用"), ("bilingual", "双语与知识桥接"),
                )
                tendencies = {}
                for key, label in descriptions:
                    tendencies[key] = st.slider(label, 0, 100, round(summary["relative_tendencies"][key] * 100),
                                                 key=f"pref-tendency-{key}")
                st.caption("这四项表示相对倾向。保存时会在扣除默认样本占比后自动换算，不必手动凑到 100%。")
            with right, st.container(border=True, key="settings-pref-sampling"):
                section_heading("采样与质量校正", "减少模板重复，并在批次分布偏离目标时自动调整。", "◈")
                templates = st.number_input("每个维度轮换模板数", 1, 100, summary["templates_per_dim"],
                                            key="pref-templates")
                shuffle = st.toggle("批次内打乱模板顺序", value=summary["shuffle_per_batch"], key="pref-shuffle")
                correction = st.toggle("开启后验分布校正", value=summary["correction_enabled"], key="pref-correction")
                threshold = st.slider("触发校正的偏差阈值", 0, 100, round(summary["correction_threshold"] * 100),
                                      format="%d%%", key="pref-threshold")
                taggers = ("unitag", "ultrafeedback")
                tagger = st.selectbox("分布标记来源", taggers, index=taggers.index(summary["correction_tagger"]),
                                      key="pref-tagger")
                st.caption("校正会影响后续批次的采样权重，不会硬删已生成的数据。")
            with st.container(border=True, key="settings-pref-output"):
                section_heading("思考内容输出", "选择训练样本中推理过程的存放方式。", "◇")
                style_names = {"separated": "分字段保存", "tags": "模型原生思考 token", "plain": "合并到正文", "drop": "只保留答案"}
                style = st.selectbox("输出方式", tuple(style_names),
                                     index=tuple(style_names).index(summary["cot_style"]),
                                     format_func=lambda value: style_names[value], key="pref-cot-style")
                if style == "tags" and not all(summary["think_tokens"]):
                    st.warning("当前尚未配置模型原生思考 token。请在下方高级编辑里填写 think_tokens 后再保存此选项。")
                if st.button("保存生成偏好", type="primary", key="pref-save-form"):
                    try:
                        updated = settings.save_form(
                            snapshot, default_share=default_share / 100,
                            relative_tendencies=tendencies, templates_per_dim=int(templates),
                            shuffle_per_batch=shuffle, correction_enabled=correction,
                            correction_threshold=threshold / 100, correction_tagger=tagger, cot_style=style,
                        )
                        st.session_state[f"pref-yaml:{area}"] = updated
                        st.toast("生成偏好已保存，并已备份上一版本。")
                        st.session_state["preference-refresh"] = True
                        st.rerun()
                    except (ValueError, OSError) as error:
                        st.error(f"保存失败：{error}")
    elif area == "思考风格":
        with st.container(border=True, key="settings-pref-styles"):
            section_heading("风格画像", "生成时根据这些描述和相对权重轮换，不把风格文本直接塞进每条样本。", "◇")
            profile = document.get("profiles", {}).get("default", {}).get("weights", {}) if isinstance(document, dict) else {}
            styles = document.get("styles", {}) if isinstance(document, dict) else {}
            cards = []
            if isinstance(styles, dict):
                for key, details in styles.items():
                    description = details.get("description", "") if isinstance(details, dict) else ""
                    weight = profile.get(key, 0) if isinstance(profile, dict) else 0
                    cards.append('<div class="df-pref-style"><span class="df-pref-style-icon">◇</span>'
                                 f'<strong>{html.escape(str(key))}</strong><b>{html.escape(str(weight))}</b>'
                                 f'<p>{html.escape(str(description))}</p></div>')
            st.html('<div class="df-pref-style-grid">' + "".join(cards) + '</div>')
    else:
        with st.container(border=True, key="settings-pref-rules"):
            section_heading("语言规则", "用具体规则和修订示例约束数据的表达。", "✎")
            rules = document.get("rules", []) if isinstance(document, dict) else []
            exemplars = document.get("exemplars", []) if isinstance(document, dict) else []
            if isinstance(rules, list):
                st.html('<div class="df-pref-rule-list">' + "".join(
                    f'<div><b>{index:02d}</b><span>{html.escape(str(rule))}</span></div>'
                    for index, rule in enumerate(rules, 1)) + '</div>')
            if isinstance(exemplars, list) and exemplars:
                st.caption(f"另有 {len(exemplars)} 组修订示例，可在下方高级编辑中调整。")
    with st.expander("高级编辑：YAML 原始配置"):
        st.caption("需要修改示例、思考 token 或额外规则时，可在此编辑；保存前会检查 YAML 语法。")
        text = st.text_area("配置内容", original, height=300, key=f"pref-yaml:{area}")
        if st.button("保存原始配置", key=f"pref-save-yaml:{area}"):
            try:
                settings.save_raw(snapshot, text)
                st.toast("配置已保存，并已备份上一版本。")
                st.session_state["preference-refresh"] = True
                st.rerun()
            except (ValueError, OSError) as error:
                st.error(f"保存失败：{error}")
