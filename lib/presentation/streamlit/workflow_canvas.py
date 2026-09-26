"""Clickable, keyboard-accessible DAG shared by setup and live execution."""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from lib.domain.workflow_graph import BASE_STAGES, DERIVED_STAGES, execution_graph
from lib.presentation.streamlit.i18n import translate_label

_canvas = components.declare_component("workflow_canvas", path=str(Path(__file__).with_name("workflow_canvas_frontend")))


def canvas_spec(targets, stages, selected, labels, glyphs, bindings=None, *, language="zh", live=False):
    nodes, edges = execution_graph(targets)
    base = [key for key in BASE_STAGES if key in nodes]
    derived = [key for key in DERIVED_STAGES if key in nodes]
    height = max(310, 100 + 100 * len(base))
    width = 1080 if derived else 810
    positions = {"ingest": (24, height / 2 - 38), "package": (width - 242, height / 2 - 38)}
    for index, key in enumerate(base):
        positions[key] = (294, 70 + index * 100)
    for index, key in enumerate(derived):
        positions[key] = (564, (height - len(derived) * 100) / 2 + index * 100 + 10)
    data = []
    for key in nodes:
        metrics = stages.get(key, {})
        done, total = int(metrics.get("done", 0) or 0), int(metrics.get("total", 0) or 0)
        status = metrics.get("status", "pending")
        role = (bindings or {}).get(key, {}).get("generation") or (bindings or {}).get(key, {}).get("jev")
        status_label = {"completed": "完成", "running": "执行中", "failed": "失败", "cancelled": "已停止",
                        "pending": "等待", "queued": "待启动"}.get(status, "等待")
        subtitle = (f"{translate_label(status_label, language)} · {done:,} / {total:,}" if live else
                    f"{role['backend']} · {role['model']}" if role else
                    translate_label("点击配置节点", language))
        data.append({"id": key, "label": translate_label(labels[key], language), "glyph": glyphs[key],
                     "x": positions[key][0], "y": positions[key][1], "status": status,
                     "subtitle": subtitle, "percent": min(100, done * 100 / total) if total else
                     100 if status == "completed" else 0,
                     "intermediate": key == "sft" and "sft" not in targets})
    english = language == "en"
    return {"nodes": data, "edges": edges, "selected": selected, "width": width, "height": height,
            "live": live, "labels": {"fit": "Fit" if english else "适应画布", "zoom_in": "Zoom in" if english else "放大",
                                       "zoom_out": "Zoom out" if english else "缩小",
                                       "hint": "Select a node to configure it. Drag the canvas to pan." if english else
                                       "点击节点查看配置 · 拖动画布平移 · 支持缩放与键盘选择",
                                       "lineage": "Arrows show data dependencies. Stages run in order." if english else
                                       "连线表示实际数据依赖，阶段按顺序执行。"}}


def render_canvas(spec, selection_key, *, key):
    event = _canvas(spec=spec, key=key, default=None)
    if isinstance(event, dict) and event.get("node") in {node["id"] for node in spec["nodes"]}:
        consumed_key = f"canvas-event:{key}"
        if event.get("serial") != st.session_state.get(consumed_key):
            st.session_state[consumed_key] = event.get("serial")
            st.session_state[selection_key] = event["node"]
            st.rerun()
