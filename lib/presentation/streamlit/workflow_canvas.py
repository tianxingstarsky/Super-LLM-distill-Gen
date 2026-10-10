"""Clickable, keyboard-accessible DAG shared by setup and live execution."""
from copy import deepcopy
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


def extend_feedback_branch(spec, projection):
    """Overlay a human revision loop without adding execution dependencies.

    The correction control selects a real generation node. Dashed routes describe
    explicit work between versions; they never create or run an engine stage.
    """
    result = deepcopy(spec)
    if not isinstance(projection, dict):
        return result
    nodes = {node["id"]: node for node in result["nodes"]}
    generation = projection.get("generation_node")
    review = projection.get("review_node") or generation
    if generation not in nodes or review not in nodes or "package" not in nodes:
        return result
    english = result.get("language") == "en"
    phase = projection.get("phase", "waiting")
    phase = phase if phase in {"waiting", "editing", "generating", "reviewing", "packaging", "approved",
                              "needs_revision", "revision_limit", "interrupted"} else "waiting"
    revision = projection.get("kind") == "revision" or bool(projection.get("parent_run_ids"))
    status = {"editing": "needs_revision", "needs_revision": "needs_revision", "revision_limit": "needs_revision",
              "interrupted": "needs_revision", "generating": "completed", "reviewing": "completed", "packaging": "completed",
              "approved": "completed"}.get(phase, "pending")
    if not revision and phase in {"generating", "reviewing", "packaging", "approved"}:
        status = "pending"
    actionable = projection.get("actionable") is True and phase in {"editing", "approved", "needs_revision"}
    old_height = result["height"]
    for node in result["nodes"]:
        node["y"] += 36
    x = (nodes[generation]["x"] + nodes[review]["x"]) / 2
    y = old_height + 96
    subtitles = {
        "waiting": ("Add feedback after results are available", "有结果后可提交意见回流修正"),
        "editing": ("Your changes are ready to submit", "人工修正已就绪，等待提交"),
        "generating": ("Generating a new version from your changes", "正在按照人工意见生成新版本"),
        "reviewing": ("The new version is being checked", "新版本正在重新核对质量"),
        "packaging": ("The new version is being checked and packaged", "新版本正在质检打包"),
        "approved": ("The new version passed its required checks", "新版本已通过必要质量检查"),
        "needs_revision": ("Review the result and describe the changes", "查看具体结果，填写需要修正的内容"),
        "revision_limit": ("The revision limit has been reached", "已达到此结果的修正次数上限"),
        "interrupted": ("Resume the existing version before a new revision", "先恢复已有版本，再进行下一次修正"),
    }
    subtitle = subtitles[phase][0 if english else 1]
    if not revision and phase in {"generating", "reviewing", "packaging"}:
        subtitle = subtitles["waiting"][0 if english else 1]
    elif not revision and phase == "approved":
        subtitle = ("The result passed its checks; feedback is optional" if english else
                    "结果已通过必要检查，可继续提交人工意见改善")
    correction = {"id": "human-feedback", "select_node": generation, "action": "feedback",
                  "actionable": actionable, "label": "Human correction" if english else "人工修正",
                  "subtitle": subtitle, "status": status, "x": x, "y": y,
                  "description": ("Open the selected result and human feedback. A new version is created only after submission."
                                  if english else "打开选中结果与人工意见，提交后才创建新版本。")}
    scoring = projection.get("stages", {}).get("scoring", {})
    skipped_score = (scoring.get("status") == "not_selected" or
                     (projection.get("selected_result") or {}).get("optional_score_status") == "not_selected")
    pass_label = (("Base checks passed · not selected for scoring" if english else "基础核对通过 · 未抽中额外评分")
                  if skipped_score else "Passed · keep this version" if english else "通过 · 保留此版本")
    if phase != "approved":
        pass_label = "If passed · keep the result" if english else "通过后 · 保留结果"
    review_label = ("Optional scoring" if english else "可选评分") if scoring.get("enabled") else (
        "Quality check during generation" if english else "生成内质量核对")
    routes = [
        {"id": "needs-correction", "from": review, "to": "human-feedback", "kind": "correction",
         "label": "Needs changes · human feedback" if english else "需修正 · 等待人工意见",
         "status": "needs_revision" if phase in {"editing", "needs_revision", "revision_limit", "interrupted"} else "pending"},
        {"id": "submit-revision", "from": "human-feedback", "to": generation, "kind": "return",
         "label": "Submit · generate a new version" if english else "提交修正 · 生成新版本",
         "status": ("running" if phase == "generating" else "completed" if phase in {"reviewing", "packaging", "approved"}
                    else "pending") if revision else "pending"},
        {"id": "passed-result", "from": review, "to": "package", "kind": "pass", "label": pass_label,
         "status": "completed" if phase == "approved" else "running" if phase == "packaging" else "pending"},
    ]
    result.update(control_nodes=[correction], control_edges=routes, height=y + 102,
                  data_top=36, data_height=old_height + 36,
                  feedback_branch={"phase": phase, "actionable": actionable, "generation_node": generation,
                                   "review_node": review, "title": "Human revision loop" if english else "人工回流修正",
                                   "review_label": review_label, "heading_x": x, "heading_y": y - 8,
                                   "revision": revision})
    if "viewport_height" in result:
        result["viewport_height"] = min(600, max(result["viewport_height"], result["height"] + 32))
    result["labels"].update(waiting="Waiting" if english else "等待",
                            needs_revision="Needs correction" if english else "需修正")
    result["labels"]["lineage"] = ("Solid: this version's data flow. Dashed: result routes and human revision. Submit feedback to rebuild."
                                    if english else "实线：本版本数据流；虚线：条件结果路由与人工回流，重造需手动提交。")
    return result


def canvas_spec(targets, stages, selected, labels, glyphs, bindings=None, *, language="zh", live=False,
                source_mode="文档资料", reasoning_trim=False, node_generation=None, package_review=None,
                qa_director=None, cpt_processing=None, recipe_version=16, feedback_branch=None):
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
        missing_roles = metrics.get("missing_roles", [role for role in roles if role not in node_bindings])
        role_names = ({"generation": "Generation model", "jev": "Review model", "vision": "Image-reading model"}
                      if language == "en" else {"generation": "生成模型", "jev": "核对模型", "vision": "图片识别模型"})
        missing_hint = (("Missing: " if language == "en" else "缺少：") +
                        (", " if language == "en" else "、").join(role_names[role] for role in missing_roles if role in role_names))
        subtitle = (f"{translate_label(status_label, language)} · {done:,} / {total:,}" if live else
                    translate_label("无需模型 · 查看步骤", language) if not roles else
                    missing_hint if status == "configuration_required" and missing_roles else
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
    result = {"nodes": data, "edges": edges, "selected": selected, "width": width, "height": height,
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
    return extend_feedback_branch(result, feedback_branch) if feedback_branch is not None else result


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
    feedback_nodes = {node["select_node"] for node in spec.get("control_nodes", ())
                      if node.get("action") == "feedback" and node.get("actionable") is True}
    if (isinstance(event, dict) and event.get("node") in {node["id"] for node in spec["nodes"]}
            and type(event.get("serial")) in (str, int) and event["serial"] != ""
            and (event.get("action") in (None, "close") or
                 event.get("action") == "feedback" and event.get("node") in feedback_nodes)
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
            if event.get("action") == "feedback":
                st.session_state[f"canvas-feedback:{key}"] = event["serial"]
            if inspector_key is not None:
                st.session_state[f"canvas-open:{key}"] = event.get("action") != "feedback"
            if follow_key is not None:
                # The toggle already exists in this render. Apply the pause
                # before creating it on the next rerun instead of mutating a widget.
                st.session_state[f"canvas-pause:{follow_key}"] = True
                st.session_state[f"canvas-open:{key}"] = event.get("action") != "feedback"
            st.rerun()
