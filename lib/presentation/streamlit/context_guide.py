"""In-page walkthroughs that point to controls already rendered by each page."""
from __future__ import annotations

import json
import re
from typing import Callable

import streamlit as st

from lib.presentation.streamlit.i18n import translate


# Guide text describes visible controls. Counts and task names always come from
# the page itself; a guide never supplies example data to a workspace.
_GUIDES: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, str] | None]] = {
    "总览": (
        (("开始制作数据", "在“开始制作数据”中导入 MD、TXT 或其他资料，也可以选择人工制作图文。"),
         ("选择数据类型", "CPT、SFT、DPO 等入口就在下方；进入工作台后可以组合多个目标。"),
         ("查看进度", "最近任务显示本机任务的真实状态；点击任务可查看工作流过程。")),
        ("开始生成", "自动工作流"),
    ),
    "自动工作流": (
        (("确定来源和目标", "选择来源类型与训练目标；文档或对话记录可在下方上传。"),
         ("配置节点模型", "点击工作流中的模型节点，在小窗中为本次任务选择模型。"),
         ("开始运行", "填写需求或来源与规模后，直接点击“开始自动生成”。")),
        None,
    ),
    "人工制作数据": (
        (("选择或创建数据集", "在上方选择已有人工数据集，或填写名称创建一份新数据集。"),
         ("添加图片与问答", "添加可选的 PNG、JPEG 或 WEBP 图片，填写问题与参考答案，在右侧核对预览。"),
         ("保存与导出", "点击“保存并继续下一条”，样本会保存在本机；在已保存样本中查看并导出图片与样本包。")),
        None,
    ),
    "数据管理": (
        (("切换数据视图", "在资产管理、数据预览和质量报告之间切换，留在当前页面。"),
         ("查看当前内容", "文件库可按分类筛选；有样本时可在预览或质量视图选择任务与文件。")),
        None,
    ),
    "任务管理": (
        (("切换任务视图", "日常生成任务在数据工作流中；高级工具和命令日志位于同页。"),
         ("找到要处理的任务", "筛选任务并选择运行；如果列表为空，可在这里新建工作流。"),
         ("查看运行过程", "所选任务的节点、日志与结果在右侧；Agent 任务完成后可在这里审查轨迹。")),
        ("进入人工审核", "人工审核"),
    ),
    "人工审核": (
        (("选择数据类型", "按 SFT、偏好对或 CPT 切换审核队列。"),
         ("打开审核队列", "上方卡片显示当前候选数，可直接打开对应队列。"),
         ("审核并发布", "有候选时核对来源与内容，通过、修订或退回；处理完后在同页发布审核版本。")),
        ("查看输出打包", "输出打包"),
    ),
    "输出打包": (
        (("选择任务或版本", "选择已完成任务或已有本地发布版本；没有结果时可先创建工作流。"),
         ("核对并导出", "核对文件、质量与来源。生成训练包，或打开已有版本的下载与本地路径。")),
        None,
    ),
    "系统设置": (
        (("选择界面语言", "在这里切换控制台语言。"),
         ("调整生成偏好", "选择偏好类别；任务模型仍在工作流节点配置。")),
        ("前往数据生成", "自动工作流"),
    ),
}

_ALIASES = {
    "首页": "总览", "数据生成": "自动工作流",
    # These routes are redirected by webapp.py before this guide is rendered.
    "模型与密钥": "系统设置", "闸门": "任务管理",
}

_REVIEW_KIND = {
    "SFT 数据调整": "sft", "DPO 偏好优化": "dpo",
    "ORPO 偏好优化": "orpo", "RLAIF 反馈审核": "rlaif",
    "CPT 语料审核": "cpt",
}


def _active_guide_route(page: str) -> str:
    """Resolve help from the visible mode, without creating a navigation route."""
    route = _ALIASES.get(page, page)
    workspace = st.session_state.get("ws", "default")
    if route == "自动工作流" and st.session_state.get(f"workflow-creation-mode:{workspace}") == "人工制作图文":
        return "人工制作数据"
    if route == "数据管理" and st.session_state.get(f"data-view:{workspace}") == "人工制作":
        return "人工制作数据"
    return route


def _target_candidates(route: str, step: int) -> tuple[str, ...]:
    """Return existing Streamlit widget/container key prefixes in priority order.

    The later candidates are fallbacks for empty or in-progress states. They
    are never synthesized from task IDs or names, so they cannot point to a
    fictitious sample or leak workspace data into JavaScript.
    """
    workspace = st.session_state.get("ws", "default")
    if route == "总览":
        return (("home-source-panel", "home-source-entries"), ("home-strategy-options",),
                ("home-recent-panel",))[step]
    if route == "人工制作数据":
        return (("manual-datasets-picker",),
                ("manual-datasets-editor", "manual-datasets-preview", "manual-datasets-picker"),
                ("manual-datasets-saved", "manual-datasets-editor", "manual-datasets-picker"))[step]
    if route == "自动工作流":
        return (("workbench-targets",), ("workbench-node-panel", "workbench-canvas-panel", "workbench-targets"),
                ("workbench-submit",))[step]
    if route == "数据管理":
        if step == 0:
            return ("data-view:",)
        area = st.session_state.get(f"data-view:{workspace}", "资产管理")
        if area == "数据预览":
            return ("preview-source:", "data-preview-run:", "preview-file:", "data-view:")
        if area == "质量报告":
            return ("quality-source:", "quality-file:", "data-view:")
        return ("asset-category:",)
    if route == "任务管理":
        if step == 0:
            return ("task-view:",)
        area = st.session_state.get(f"task-view:{workspace}", "数据工作流")
        if area != "数据工作流":
            return ("task-view:",)
        if step == 1 and not st.session_state.get(f"task-center-focus:{workspace}"):
            return ("task-center-filter:", "task-center-create:", "task-center-new:")
        if step == 1:
            return ("task-center-focus:", "task-center-new:")
        # The run graph has this control whenever a task is selected. An empty
        # workspace still has its create button; the view switch is the last
        # resort when the user is on an advanced task view.
        return ("workflow-follow:", "task-center-create:",
                "task-center-focus:", "task-view:")
    if route == "人工审核":
        kind = _REVIEW_KIND.get(st.session_state.get(f"review-mode:{workspace}"), "sft")
        queue_button = f"review-overview-open-{kind}"
        if step == 0:
            return ("review-mode:",)
        if step == 1:
            return (queue_button,)
        return (f"df-review-actions-{kind}", queue_button)
    if route == "输出打包":
        if step == 0:
            return ("package-run:", "package-release:", "package-empty-workflow")
        return ("download-package:", "prepare-package:", "stop-package:",
                "package-release-download:", "package-release-file:",
                "package-releases-panel", "package-empty-workflow")
    if route == "系统设置":
        return (("ui-language-choice",), ("preference-area",))[step]
    return ()


def _set_tour_step(key: str, step: int | None) -> None:
    st.session_state[f"{key}:revision"] = st.session_state.get(f"{key}:revision", 0) + 1
    if step is None:
        st.session_state.pop(key, None)
        st.session_state["context-guide-clear"] = True
    else:
        st.session_state[key] = step


def _navigate_from_tour(key: str, target: str, navigate: Callable[[str], None]) -> None:
    _set_tour_step(key, None)
    navigate(target)


def _clear_highlight() -> None:
    # The script only removes our own decoration. It reads no page content.
    st.html('<script>window.__dfGuideCleanup?.();window.__dfGuideCleanup=null;'
            'window.__dfGuideToken=null;'
            'document.querySelectorAll(".df-context-guide-target").forEach('
            'element=>element.classList.remove("df-context-guide-target"));'
            '</script>', unsafe_allow_javascript=True)


def _highlight(route: str, step: int, revision: int) -> None:
    # Only hard-coded widget-key prefixes are interpolated. No workspace ID,
    # task name, file content, or model response enters HTML or JavaScript.
    candidates = _target_candidates(route, step)
    # Streamlit 1.63 turns punctuation in keys (including ':') into '-'
    # before adding the st-key-* CSS class.
    selectors = [f'[class*="st-key-{re.sub(r"[^a-zA-Z0-9_-]", "-", key)}"]'
                 for key in candidates]
    token = json.dumps(f"{route}:{step}:{revision}", ensure_ascii=True)
    selector_json = json.dumps(selectors, ensure_ascii=True)
    st.html('''<style>
      .df-context-guide-target {
        outline:2px solid #2877e1!important; outline-offset:3px;
        box-shadow:0 0 0 7px rgba(40,119,225,.12)!important;
        scroll-margin-top:380px; border-radius:10px;
      }
      [class*="st-key-context-guide-tour"] {
        /* Sticky cannot follow a target outside this top-of-page container. */
        position:fixed!important; top:76px; right:20px; z-index:100;
        width:min(680px,calc(100vw - 280px)); max-height:min(280px,calc(100dvh - 100px));
        box-sizing:border-box; overflow-y:auto; overscroll-behavior:contain;
        border-color:#c9dff7!important; border-radius:11px!important;
        background:linear-gradient(105deg,#eef6ff,#fff)!important;
        box-shadow:0 10px 32px rgba(37,104,191,.19)!important;
      }
      [class*="st-key-context-guide-tour"] .stButton button p {
        white-space:normal!important; overflow:visible!important;
        text-overflow:clip!important; line-height:1.2;
      }
      @media (max-width:800px) {
        [class*="st-key-context-guide-tour"] {
          top:64px; right:12px; width:calc(100vw - 24px);
          max-height:min(280px,calc(100dvh - 88px));
        }
        .df-context-guide-target {scroll-margin-top:360px;}
      }
    </style><script>
    (() => {
      const token = ''' + token + ''';
      const selectors = ''' + selector_json + ''';
      window.__dfGuideCleanup?.();
      window.__dfGuideToken = token;
      document.querySelectorAll(".df-context-guide-target").forEach(
        element => element.classList.remove("df-context-guide-target"));
      let bestIndex = selectors.length;
      let marked = null;
      let frame = 0;
      function locate() {
        if (window.__dfGuideToken !== token) return;
        if (marked && !document.contains(marked)) {
          marked = null;
          bestIndex = selectors.length;
        }
        const matches = selectors.map(selector => document.querySelector(selector));
        const index = matches.findIndex(Boolean);
        if (index >= 0) {
          const target = matches[index];
          // React can replace the class attribute on the same DOM node.
          if (target !== marked || index < bestIndex ||
              !target.classList.contains("df-context-guide-target")) {
            document.querySelectorAll(".df-context-guide-target").forEach(
              element => element.classList.remove("df-context-guide-target"));
            target.classList.add("df-context-guide-target");
            marked = target;
            const scrolledIndex = Number.isInteger(window.__dfGuideScrollIndex)
              ? window.__dfGuideScrollIndex : selectors.length;
            if (window.__dfGuideScrolled !== token || index < scrolledIndex) {
              target.scrollIntoView({behavior:"smooth", block:"start"});
              window.__dfGuideScrolled = token;
              window.__dfGuideScrollIndex = index;
            }
          }
          bestIndex = index;
        }
      }
      function schedule() {
        if (frame || window.__dfGuideToken !== token) return;
        frame = window.requestAnimationFrame(() => {frame = 0; locate();});
      }
      // Page widgets may arrive late or rerender after the initial script.
      // Observe the active page until the step changes, without a timeout or
      // repeated scrolling when only the decoration needs to be restored.
      const observer = new MutationObserver(records => {
        if (records.some(record => record.type === "childList" || record.target === marked))
          schedule();
      });
      observer.observe(document.querySelector('[data-testid="stMainBlockContainer"]') || document.body,
                       {subtree:true, childList:true, attributes:true, attributeFilter:["class"]});
      window.__dfGuideCleanup = () => {
        observer.disconnect();
        if (frame) window.cancelAnimationFrame(frame);
        frame = 0;
      };
      schedule();
    })();
    </script>''', unsafe_allow_javascript=True)


def render_context_guide(page: str, navigate: Callable[[str], None]) -> None:
    """Render optional help and a step-by-step focus on the current page."""
    route = _active_guide_route(page)
    previous_route = st.session_state.get("context-guide-route")
    if previous_route and previous_route != route:
        st.session_state.pop(f"context-guide-step:{previous_route}", None)
        st.session_state["context-guide-clear"] = True
    st.session_state["context-guide-route"] = route

    # Legacy direct subpages are still valid deep links, but they do not have
    # the composite page's view switcher. Avoid an apparent tour target that
    # cannot exist on those screens.
    if route not in _GUIDES:
        if st.session_state.pop("context-guide-clear", False):
            _clear_highlight()
        return

    steps, action = _GUIDES[route]
    language = st.session_state.get("ui_language", "zh")
    tour_key = f"context-guide-step:{route}"
    _, help_column = st.columns([6, 1], gap="small")
    with help_column:
        st.button("ⓘ 本页指引", key=f"context-guide-start:{route}",
                  on_click=_set_tour_step, args=(tour_key, 0), width="stretch")

    step_index = st.session_state.get(tour_key)
    if not isinstance(step_index, int) or step_index < 0 or step_index >= len(steps):
        if isinstance(step_index, int):
            _set_tour_step(tour_key, None)
        if st.session_state.pop("context-guide-clear", False):
            _clear_highlight()
        return

    _highlight(route, step_index, st.session_state.get(f"{tour_key}:revision", 0))
    title, detail = steps[step_index]
    with st.container(border=True, key="context-guide-tour"):
        text, previous, following, close = st.columns([3.5, 1, 1.25, 1.25], gap="small",
                                                       vertical_alignment="center")
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
        with close:
            if step_index + 1 < len(steps):
                st.button("退出引导", key=f"context-guide-exit:{route}",
                          on_click=_set_tour_step, args=(tour_key, None), width="stretch")
        # Jump straight to a relevant control without stepping through earlier
        # instructions. The same banner remains on the current page.
        jump_columns = st.columns(len(steps) + int(action is not None), gap="small")
        for index, (jump_title, _) in enumerate(steps):
            with jump_columns[index]:
                st.button(f"{index + 1:02d}　{translate(jump_title, language)}",
                          key=f"context-guide-jump:{route}:{index}",
                          disabled=index == step_index,
                          on_click=_set_tour_step, args=(tour_key, index), width="stretch")
        if action:
            label, target = action
            with jump_columns[-1]:
                st.button(label, key=f"context-guide-action:{route}",
                          on_click=_navigate_from_tour,
                          args=(tour_key, target, navigate), width="stretch")
