"""Clickable, keyboard-accessible DAG shared by setup and live execution."""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from lib.domain.workflow_graph import BASE_STAGES, DERIVED_STAGES, execution_graph, sft_uses_cot_output
from lib.domain.workflow_scale import node_roles
from lib.presentation.streamlit.i18n import translate_label

_canvas = components.declare_component("workflow_canvas", path=str(Path(__file__).with_name("workflow_canvas_frontend")))


def node_display_label(node, labels, node_generation=None):
    """Name the actual CoT operation rather than implying a writer always runs."""
    if node == "cot":
        settings = (node_generation or {}).get("cot")
        generates = bool(settings and settings.get("enabled", True))
        return "CoT 推理生成" if generates else "CoT 推理核验"
    return labels[node]


def node_description(node, source_mode, *, node_generation=None, package_review=None, cpt_processing=None):
    """A short purpose and cost hint available on every node without another card."""
    descriptions = {
        "ingest": "读取来源并整理为可处理的文档片段或任务。",
        "director": "规划互动类型与上下文，指导生成节点安排多样化样本。",
        "cpt": "清洗预训练语料，检查内容质量并保留来源。",
        "sft": "生成指令对话并完成基础自检；为推理或偏好节点提供候选。",
        "multiturn": "生成连续多轮对话，检查逐轮质量与全段一致性。",
        "agent": "验证工具调用轨迹，保留可复查的执行证据。",
        "gsm8k": "构建算术任务并核对答案。",
        "preference": "比较候选回答，形成偏好训练数据。",
        "trim": "按提示词修剪推理中的泄漏或冗余，保留最终答案。",
        "jev": "额外的模型评分节点；按抽检或全量评审筛选最终样本。",
        "package": "检查结构、去重并导出训练文件、报告与清单。",
    }
    if node == "cot":
        generation = (node_generation or {}).get("cot")
        description = ("按风格提示词生成显式推理与答案，并核验结果。" if generation and generation.get("enabled", True)
                       else "核验上游已有推理与答案；本节点不重新撰写推理。")
    elif node == "package" and package_review and package_review.get("enabled") and package_review.get("node") != "jev":
        description = "执行规则检查、模型评审与导出，保留旧任务处理方式。"
    else:
        description = descriptions.get(node, "")
    roles = node_roles(node, source_mode, node_generation=node_generation,
                       package_review=package_review, cpt_processing=cpt_processing)
    usage = "调用模型；可能产生 API 费用。" if roles else "本地处理，不发起模型请求。"
    return description, usage


def canvas_spec(targets, stages, selected, labels, glyphs, bindings=None, *, language="zh", live=False,
                source_mode="文档资料", reasoning_trim=False, node_generation=None, package_review=None,
                qa_director=None, cpt_processing=None, recipe_version=16):
    nodes, edges = execution_graph(targets, reasoning_trim=reasoning_trim, qa_director=qa_director,
                                  package_review=package_review, recipe_version=recipe_version)
    base = [key for key in BASE_STAGES if key in nodes]
    derived = [key for key in DERIVED_STAGES if key in nodes]
    # Center each column within its actual rows. Leave a clear outer lane for
    # base outputs that bypass the derived column on their way to packaging.
    bypass = bool(derived or "trim" in nodes) and any(a in base and b in {"jev", "package"} for a, b in edges)
    margin = 44 if bypass else 24
    height = 2 * margin + 78 + 100 * (max(1, len(base), len(derived)) - 1)
    director_width = 270 if "director" in nodes else 0
    review_width = 270 if "jev" in nodes else 0
    width = (1080 if derived else 810) + (270 if "trim" in nodes else 0) + director_width + review_width
    positions = {"ingest": (24, (height - 78) / 2), "package": (width - 242, (height - 78) / 2)}
    if "director" in nodes:
        positions["director"] = (294, (height - 78) / 2)
    if "trim" in nodes:
        positions["trim"] = (width - 512 - review_width, (height - 78) / 2)
    if "jev" in nodes:
        positions["jev"] = (width - 512, (height - 78) / 2)
    for column, x in ((base, 294 + director_width), (derived, 564 + director_width)):
        top = (height - (78 + 100 * (len(column) - 1))) / 2
        for index, key in enumerate(column):
            positions[key] = (x, top + index * 100)
    data = []
    for key in nodes:
        metrics = stages.get(key, {})
        done, total = int(metrics.get("done", 0) or 0), int(metrics.get("total", 0) or 0)
        status = metrics.get("status", "pending")
        roles = node_roles(key, source_mode, node_generation=node_generation, package_review=package_review, cpt_processing=cpt_processing)
        node_bindings = {role: binding for role, binding in (bindings or {}).get(key, {}).items()
                         if role in roles}
        role = node_bindings.get("generation") or node_bindings.get("jev") or node_bindings.get("vision")
        status_label = {"completed": "完成", "running": "执行中", "failed": "失败", "cancelled": "已停止",
                        "pending": "等待", "queued": "待启动"}.get(status, "等待")
        subtitle = (f"{translate_label(status_label, language)} · {done:,} / {total:,}" if live else
                    translate_label("无需模型 · 查看步骤", language) if not roles else
                    translate_label("请选择可用模型", language) if status == "configuration_required" else
                    f"{role['backend']} · {role['model']}" if role else
                    translate_label("点击配置节点", language))
        models = []
        if not live:
            for role_key, zh, en in (("generation", "生成", "Generate"), ("jev", "评审", "Review"), ("vision", "识别", "Read")):
                binding = node_bindings.get(role_key)
                if binding:
                    models.append(f"{en if language == 'en' else zh}: {binding['backend']} · {binding['model']}")
        data.append({"id": key, "label": translate_label(node_display_label(key, labels, node_generation), language), "glyph": glyphs[key],
                     "x": positions[key][0], "y": positions[key][1], "status": status,
                     "description": " ".join(translate_label(text, language) for text in node_description(key, source_mode,
                         node_generation=node_generation, package_review=package_review, cpt_processing=cpt_processing)),
                     "subtitle": subtitle, "models": models, "percent": min(100, done * 100 / total) if total else
                     100 if status == "completed" else 0,
                     "intermediate": key == "sft" and ("sft" not in targets or
                         sft_uses_cot_output(targets, recipe_version))})
    english = language == "en"
    return {"nodes": data, "edges": edges, "selected": selected, "width": width, "height": height,
            "live": live, "language": language, "labels": {"node_picker": "Go to node" if english else "定位节点",
                                       "fit": "Fit" if english else "适应画布", "zoom_in": "Zoom in" if english else "放大",
                                       "zoom_out": "Zoom out" if english else "缩小",
                                       "focus": "Locate selected node" if english else "定位所选节点",
                                       "reset": "Actual size" if english else "实际大小",
                                       "completed": "Completed" if english else "已完成",
                                       "running": "Running" if english else "运行中",
                                       "failed": "Failed" if english else "失败",
                                       "overview": f"{len(nodes)} nodes · {len(edges)} links" if english else
                                       f"{len(nodes)} 个节点 · {len(edges)} 条连线",
                                       "hint": ("Select a node to inspect live model output. Drag to pan. Ctrl + scroll to zoom." if live else
                                                 "Select a node to inspect its settings. Drag to pan. Ctrl + scroll to zoom.") if english else
                                       ("点击节点查看实时输出 · 拖动平移 · Ctrl + 滚轮缩放" if live else
                                         "点击节点查看配置 · 拖动平移 · Ctrl + 滚轮缩放"),
                                       "lineage": "Arrows show data dependencies. Stages run in order." if english else
                                       "连线表示实际数据依赖，阶段按顺序执行。"}}


def render_canvas(spec, selection_key, *, key, follow_key=None, inspector_key=None, expanded=False,
                  reveal_key=None, reveal_target_key="workbench-canvas-panel"):
    # Keep the real Streamlit form mounted when the window is closed. Widget
    # values and callbacks remain owned by Streamlit; the canvas only positions it.
    spec = dict(spec, expanded=expanded)
    if inspector_key is not None:
        spec["inspector"] = {"key": inspector_key,
                             "open": bool(st.session_state.get(f"canvas-open:{key}", False)),
                             "wide": bool(st.session_state.get(f"canvas-wide:{key}", False))}
    reveal = st.session_state.get(reveal_key) if reveal_key is not None else None
    if (isinstance(reveal, dict) and reveal.get("node") == spec["selected"]
            and type(reveal.get("serial")) in (str, int) and reveal["serial"] != ""
            and spec.get("inspector", {}).get("open")):
        spec["reveal"] = dict(reveal, key=reveal_target_key)
    elif reveal_key is not None and reveal is not None:
        # Closing the native form or selecting another node cancels navigation
        # even if its component cancellation event was superseded by a click.
        st.session_state.pop(reveal_key, None)
        reveal = None
    event = _canvas(spec=spec, key=key, default=None)
    # The bridge owns this UI-only receipt. Keep a request through extra reruns
    # until it explicitly completes or the user interrupts it. A stale receipt
    # cannot consume a newer click on the same unresolved configuration issue.
    if isinstance(event, dict) and event.get("action") == "reveal":
        if (isinstance(reveal, dict) and event.get("node") == reveal.get("node")
                and type(event.get("request_serial")) is type(reveal.get("serial"))
                and event.get("request_serial") == reveal.get("serial")
                and event.get("outcome") in ("completed", "cancelled")):
            st.session_state.pop(reveal_key, None)
        return
    if (isinstance(event, dict) and event.get("node") in {node["id"] for node in spec["nodes"]}
            and type(event.get("serial")) in (str, int) and event["serial"] != ""
            and event.get("action") in (None, "close")
            and (event.get("action") != "close" or event["node"] == spec["selected"])):
        consumed_key = f"canvas-event:{key}"
        if event.get("serial") != st.session_state.get(consumed_key):
            st.session_state[consumed_key] = event.get("serial")
            if reveal_key is not None:
                st.session_state.pop(reveal_key, None)
            if event.get("action") == "close":
                if inspector_key is not None:
                    st.session_state[f"canvas-open:{key}"] = False
                    st.rerun()
                return
            st.session_state[selection_key] = event["node"]
            if inspector_key is not None:
                st.session_state[f"canvas-open:{key}"] = True
            if follow_key is not None:
                # The toggle already exists in this render. Apply the pause
                # before creating it on the next rerun instead of mutating a widget.
                st.session_state[f"canvas-pause:{follow_key}"] = True
                st.session_state[f"canvas-open:{key}"] = True
            st.rerun()
