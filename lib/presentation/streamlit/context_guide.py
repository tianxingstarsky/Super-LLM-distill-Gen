"""Short, page-specific help beside the work the operator is doing."""
from __future__ import annotations

from typing import Callable

import streamlit as st

from lib.presentation.streamlit.i18n import translate


# These are instructions for visible controls, not a second navigation tree.
_GUIDES: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, str] | None]] = {
    "总览": (
        (("选择输入", "在生成类型中选择文档、Agent 上下文或开放需求。"),
         ("选择目标", "可以直接选 CPT、SFT 或偏好数据；进入工作台后仍能调整。"),
         ("查看进度", "最近任务和工作流卡片会显示当前工作区的真实状态。")),
        ("开始生成", "自动工作流"),
    ),
    "自动工作流": (
        (("确定来源和目标", "选择来源类型与训练目标；文档或对话记录可在下方上传。"),
         ("配置节点模型", "点击工作流中的模型节点，在右侧为本次任务选择模型。"),
         ("开始运行", "填写需求或来源与规模后，直接点击“开始自动生成”。")),
        None,
    ),
    "数据管理": (
        (("查看来源与产物", "切换文件分类，定位当前工作区的输入与生成结果。"),
         ("预览样本", "选择训练目标和任务，检查样本内容与质量结果。")),
        None,
    ),
    "任务管理": (
        (("跟踪运行", "选择任务后查看节点进度、日志、质量结果与失败原因。"),
         ("处理结果", "任务完成后进入人工审核；需要调整时返回工作流配置。")),
        ("进入人工审核", "人工审核"),
    ),
    "人工审核": (
        (("选择数据类型", "按 SFT、偏好对或 CPT 切换审核队列。"),
         ("逐条处理", "核对来源与生成内容，再通过、修订或退回。"),
         ("生成审核版本", "处理完当前队列后，在同一页面生成已审核版本。")),
        ("查看输出打包", "输出打包"),
    ),
    "输出打包": (
        (("选择完成的任务", "仅从当前工作区中选择已有结果，不会出现示例任务。"),
         ("核对并导出", "检查实际文件、质量与来源信息，再生成可校验的数据包。")),
        None,
    ),
    "模型与密钥": (
        (("连接模型服务", "在此登记服务地址和凭据。"),
         ("为任务选模型", "回到数据生成工作台，点击需要模型的节点完成选择。")),
        ("前往数据生成", "自动工作流"),
    ),
    "系统设置": (
        (("调整通用偏好", "在此设置界面语言和默认生成偏好。"),
         ("配置本次任务", "来源、目标、规模与模型都在数据生成工作台完成。")),
        ("前往数据生成", "自动工作流"),
    ),
}

_ALIASES = {
    "首页": "总览", "数据生成": "自动工作流",
    "资产管理": "数据管理", "数据预览": "数据管理", "质量报告": "数据管理",
    "管线运行": "任务管理", "运行监控": "任务管理", "监控": "任务管理",
    "偏好设置": "系统设置", "闸门": "任务管理",
}

_TOUR_TARGETS = {
    "总览": ("home-source-panel", "home-strategy-panel", "home-stats-panel"),
    "自动工作流": ("workbench-targets", "workbench-node-panel", "workbench-submit"),
}


def _set_tour_step(key: str, step: int | None) -> None:
    if step is None:
        st.session_state.pop(key, None)
    else:
        st.session_state[key] = step


def render_context_guide(page: str, navigate: Callable[[str], None]) -> None:
    """Keep help beside the work, with an optional highlighted walkthrough."""
    route = _ALIASES.get(page, page)
    steps, action = _GUIDES.get(route, _GUIDES["总览"])
    language = st.session_state.get("ui_language", "zh")
    tour_key = f"context-guide-step:{route}"
    _, help_column = st.columns([6, 1], gap="small")
    with help_column, st.popover("ⓘ 本页指引", key=f"context-guide:{route}", width="stretch"):
        st.caption(translate("按当前页面操作", language))
        if route in _TOUR_TARGETS:
            st.button("开始逐步引导", key=f"context-guide-start:{route}",
                      on_click=_set_tour_step, args=(tour_key, 0), width="stretch")
        for number, (title, detail) in enumerate(steps, 1):
            st.markdown(f"**{number:02d}　{translate(title, language)}**")
            st.caption(translate(detail, language))
        if action:
            label, target = action
            st.button(label, key=f"context-guide-action:{route}",
                      on_click=navigate, args=(target,), width="stretch")

    step_index = st.session_state.get(tour_key)
    if route not in _TOUR_TARGETS or not isinstance(step_index, int):
        return
    if step_index < 0 or step_index >= len(steps):
        _set_tour_step(tour_key, None)
        return
    target = _TOUR_TARGETS[route][step_index]
    st.html('<style>[class*="st-key-' + target + '"] {'
            'outline:2px solid #2877e1!important;outline-offset:3px;'
            'box-shadow:0 0 0 7px rgba(40,119,225,.12)!important;'
            'scroll-margin-top:110px}'
            '[class*="st-key-context-guide-tour"] {'
            'border-color:#c9dff7!important;border-radius:11px!important;'
            'background:linear-gradient(105deg,#eef6ff,#fff)!important;'
            'box-shadow:0 6px 18px rgba(37,104,191,.08)!important}</style>')
    title, detail = steps[step_index]
    with st.container(border=True, key="context-guide-tour"):
        text, previous, following = st.columns([5, 1, 1], gap="small", vertical_alignment="center")
        with text:
            st.caption(translate("本页逐步引导", language) + f" · {step_index + 1}/{len(steps)}")
            st.markdown(f"**{translate(title, language)}** · {translate(detail, language)}")
            st.caption(translate("下方蓝色高亮区域是这一步的操作位置。", language))
        with previous:
            if step_index:
                st.button("上一步", key=f"context-guide-previous:{route}",
                          on_click=_set_tour_step, args=(tour_key, step_index - 1), width="stretch")
        with following:
            if step_index + 1 < len(steps):
                st.button("下一步", key=f"context-guide-next:{route}",
                          on_click=_set_tour_step, args=(tour_key, step_index + 1), width="stretch")
            else:
                st.button("完成引导", key=f"context-guide-finish:{route}",
                          on_click=_set_tour_step, args=(tour_key, None), width="stretch")
