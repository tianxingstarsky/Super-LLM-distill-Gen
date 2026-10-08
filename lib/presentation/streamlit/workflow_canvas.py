"""Clickable, keyboard-accessible DAG shared by setup and live execution."""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from lib.domain.workflow_graph import BASE_STAGES, DERIVED_STAGES, execution_graph
from lib.domain.workflow_scale import node_roles
from lib.presentation.streamlit.i18n import translate_label

_canvas = components.declare_component("workflow_canvas", path=str(Path(__file__).with_name("workflow_canvas_frontend")))


def canvas_spec(targets, stages, selected, labels, glyphs, bindings=None, *, language="zh", live=False,
                source_mode="文档资料"):
    nodes, edges = execution_graph(targets)
    base = [key for key in BASE_STAGES if key in nodes]
    derived = [key for key in DERIVED_STAGES if key in nodes]
    # Center each column within its actual rows. Leave a clear outer lane for
    # base outputs that bypass the derived column on their way to packaging.
    bypass = bool(derived) and any(a in base and b == "package" for a, b in edges)
    margin = 44 if bypass else 24
    height = 2 * margin + 78 + 100 * (max(1, len(base), len(derived)) - 1)
    width = 1080 if derived else 810
    positions = {"ingest": (24, (height - 78) / 2), "package": (width - 242, (height - 78) / 2)}
    for column, x in ((base, 294), (derived, 564)):
        top = (height - (78 + 100 * (len(column) - 1))) / 2
        for index, key in enumerate(column):
            positions[key] = (x, top + index * 100)
    data = []
    for key in nodes:
        metrics = stages.get(key, {})
        done, total = int(metrics.get("done", 0) or 0), int(metrics.get("total", 0) or 0)
        status = metrics.get("status", "pending")
        node_bindings = (bindings or {}).get(key, {})
        role = node_bindings.get("generation") or node_bindings.get("jev") or node_bindings.get("vision")
        status_label = {"completed": "完成", "running": "执行中", "failed": "失败", "cancelled": "已停止",
                        "pending": "等待", "queued": "待启动"}.get(status, "等待")
        subtitle = (f"{translate_label(status_label, language)} · {done:,} / {total:,}" if live else
                    translate_label("无需模型 · 查看步骤", language) if not node_roles(key, source_mode) else
                    translate_label("请选择可用模型", language) if status == "configuration_required" else
                    f"{role['backend']} · {role['model']}" if role else
                    translate_label("点击配置节点", language))
        models = []
        if not live:
            for role_key, zh, en in (("generation", "生成", "Generate"), ("jev", "评审", "Review"), ("vision", "识别", "Read")):
                binding = node_bindings.get(role_key)
                if binding:
                    models.append(f"{en if language == 'en' else zh}: {binding['backend']} · {binding['model']}")
        data.append({"id": key, "label": translate_label(labels[key], language), "glyph": glyphs[key],
                     "x": positions[key][0], "y": positions[key][1], "status": status,
                     "subtitle": subtitle, "models": models, "percent": min(100, done * 100 / total) if total else
                     100 if status == "completed" else 0,
                     "intermediate": key == "sft" and "sft" not in targets})
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


def render_canvas(spec, selection_key, *, key, follow_key=None):
    event = _canvas(spec=spec, key=key, default=None)
    if isinstance(event, dict) and event.get("node") in {node["id"] for node in spec["nodes"]}:
        consumed_key = f"canvas-event:{key}"
        if event.get("serial") != st.session_state.get(consumed_key):
            st.session_state[consumed_key] = event.get("serial")
            st.session_state[selection_key] = event["node"]
            if follow_key is not None:
                # The toggle already exists in this render. Apply the pause
                # before creating it on the next rerun instead of mutating a widget.
                st.session_state[f"canvas-pause:{follow_key}"] = True
                st.session_state[f"canvas-open:{key}"] = True
            st.rerun()
