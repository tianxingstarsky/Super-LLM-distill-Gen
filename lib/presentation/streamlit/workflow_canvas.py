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
    return labels.get(node, "评分与修正" if node == "review" else node)


def node_description(node, source_mode, *, node_generation=None, package_review=None, cpt_processing=None,
                     review_repair=None, recipe_version=None):
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
        "review": "在同一节点评分、修正并重新评分；通过后才进入打包，修正不会回到 SFT 生成。",
        "package": "检查结构、去重并导出训练文件、报告与清单。",
    }
    if node == "cot":
        generation = (node_generation or {}).get("cot")
        description = ("按风格提示词生成显式推理与答案，并核验结果。" if generation and generation.get("enabled", True)
                       else "核验上游已有推理与答案；本节点不重新撰写推理。")
    elif (node == "package" and package_review and package_review.get("enabled")
          and package_review.get("node") != "jev" and (recipe_version or 0) < 18):
        description = "执行规则检查、模型评审与导出，保留旧任务处理方式。"
    else:
        description = descriptions.get(node, "")
    roles = node_roles(node, source_mode, node_generation=node_generation,
                       package_review=package_review, cpt_processing=cpt_processing,
                       review_repair=review_repair, recipe_version=recipe_version)
    usage = ("人工评分与修正，不发起模型请求。" if node == "review" and not roles else
             "调用模型；可能产生 API 费用。" if roles else "本地处理，不发起模型请求。")
    return description, usage


def extend_feedback_branch(spec, projection):
    """Show the conditional repair/recheck loop inside the real review stage.

    Repair is not a dependency back to a generation node. The attached control
    is an operation of the review stage and always opens that same stage.
    Historical graphs without this engine stage remain unchanged.
    """
    result = deepcopy(spec)
    if not isinstance(projection, dict):
        return result
    if result.get("feedback_branch") and "data_height" in result:
        # A refreshed projection replaces the attached branch; it must not
        # repeatedly shift the data graph or grow the review lane.
        top = result.get("data_top", 0)
        for node in result["nodes"]:
            node["y"] -= top
        result["height"] = result["data_height"] - top
        for key in ("control_nodes", "control_edges", "feedback_branch", "data_top", "data_height"):
            result.pop(key, None)
    nodes = {node["id"]: node for node in result["nodes"]}
    review = "review"
    if review not in nodes or "package" not in nodes:
        return result
    english = result.get("language") == "en"
    phase = projection.get("phase", "waiting")
    phase = phase if phase in {"waiting", "editing", "generating", "repairing", "reviewing", "packaging", "approved",
                              "needs_revision", "revision_limit", "interrupted"} else "waiting"
    revision = (projection.get("kind") == "revision" or bool(projection.get("parent_run_ids"))
                or bool(projection.get("repair_rounds", 0)))
    status = {"editing": "needs_revision", "needs_revision": "needs_revision", "revision_limit": "needs_revision",
              "interrupted": "needs_revision", "generating": "running", "repairing": "running",
              "reviewing": "completed", "packaging": "completed",
              "approved": "completed"}.get(phase, "pending")
    mode = projection.get("mode", result.get("review_repair", {}).get("mode", "auto"))
    human = mode == "human"
    actionable = (projection.get("actionable") is True and phase in {"editing", "approved", "needs_revision"}
                  if human else True)
    old_height = result["height"]
    for node in result["nodes"]:
        node["y"] += 36
    x = nodes[review]["x"]
    y = old_height + 90
    subtitles = {
        "waiting": ("Score first; repair only when needed", "先评分，未达标才进入修正分支"),
        "editing": ("Edit the answer and score it here", "在本节点修改答案并评分"),
        "generating": ("Repairing the answer inside this stage", "本节点正在修正答案"),
        "repairing": ("Repairing the answer inside this stage", "本节点正在修正答案"),
        "reviewing": ("Rechecking the repaired answer", "修正答案正在重新评分"),
        "packaging": ("Approved results move to packaging", "已通过结果进入打包"),
        "approved": ("Passed; preserve the approved answer", "评分通过，保留已通过答案"),
        "needs_revision": ("Below threshold; repair and score again", "评分未达标，修正后重新评分"),
        "revision_limit": ("The revision limit has been reached", "已达到此结果的修正次数上限"),
        "interrupted": ("Resume this review and repair operation", "恢复已有评分与修正任务"),
    }
    subtitle = subtitles[phase][0 if english else 1]
    correction = {"id": "review-repair", "select_node": review, "action": "feedback" if human else None,
                  "actionable": actionable, "label": ("Human score and repair" if english else "人工评分与修正") if human
                  else ("Repair and rescore" if english else "修正并重新评分"),
                  "subtitle": subtitle, "status": status, "x": x, "y": y,
                  "description": ("Scoring and repair are operations of the same review stage. The answer never returns to SFT generation."
                                  if english else "评分与修正在同一节点完成，错误答案不会回到 SFT 生成节点。")}
    scoring = projection.get("stages", {}).get("scoring", {})
    skipped_score = (scoring.get("status") == "not_selected" or
                     (projection.get("selected_result") or {}).get("optional_score_status") == "not_selected")
    pass_label = (("Base checks passed · not selected for scoring" if english else "基础核对通过 · 未抽中额外评分")
                  if skipped_score else "Passed · keep this version" if english else "通过 · 保留此版本")
    if phase != "approved":
        pass_label = "If passed · keep the result" if english else "通过后 · 保留结果"
    review_label = (("Human score + edit" if english else "人工评分 + 编辑答案") if human else
                    ("JEV score + repair model" if english else "评分模型 + 修正模型") if scoring.get("enabled")
                    else ("One model: score + repair" if english else "一个模型评分 + 修正"))
    routes = [
        {"id": "needs-correction", "from": review, "to": "review-repair", "kind": "correction",
         "label": "Below threshold" if english else "评分未达标",
         "status": "needs_revision" if phase in {"editing", "needs_revision", "revision_limit", "interrupted"} else "pending"},
        {"id": "repair-recheck", "from": "review-repair", "to": review, "kind": "return",
         "label": "Rescore" if english else "重新评分",
         "status": ("running" if phase in {"generating", "repairing", "reviewing"} else "completed" if phase in {"packaging", "approved"}
                    else "pending") if revision else "pending"},
        {"id": "passed-result", "from": review, "to": "package", "kind": "pass", "label": pass_label,
         "status": "completed" if phase == "approved" else "running" if phase == "packaging" else "pending"},
    ]
    result.update(control_nodes=[correction], control_edges=routes, height=y + 102,
                  data_top=36, data_height=old_height + 36,
                  feedback_branch={"phase": phase, "actionable": actionable,
                                   "generation_node": projection.get("generation_node"),
                                   "review_node": review, "title": "Score + repair · one stage" if english else "评分与修正 · 同一节点",
                                   "review_label": review_label, "heading_x": x,
                                   "heading_y": nodes[review]["y"] - 14,
                                   "revision": revision, "mode": mode,
                                   "group": {"x": x - 14, "y": nodes[review]["y"] - 34,
                                             "width": 246, "height": y + 112 - nodes[review]["y"]}})
    if "viewport_height" in result:
        result["viewport_height"] = min(600, max(result["viewport_height"], result["height"] + 32))
    result["labels"].update(waiting="Waiting" if english else "等待",
                            needs_revision="Needs correction" if english else "需修正")
    result["labels"]["lineage"] = ("Scoring → repair when needed → rescore, inside one stage. Approved results go to packaging."
                                    if english else "评分 → 未达标修正 → 重新评分，均在同一节点完成；通过后进入打包。")
    return result


def canvas_spec(targets, stages, selected, labels, glyphs, bindings=None, *, language="zh", live=False,
                source_mode="文档资料", reasoning_trim=False, node_generation=None, package_review=None,
                qa_director=None, cpt_processing=None, recipe_version=16, feedback_branch=None,
                review_repair=None, repair_only=False):
    nodes, edges = execution_graph(targets, reasoning_trim=reasoning_trim, qa_director=qa_director,
                                  package_review=package_review, recipe_version=recipe_version,
                                  repair_only=repair_only)
    base = [key for key in BASE_STAGES if key in nodes]
    derived = [key for key in DERIVED_STAGES if key in nodes]
    # Center each column within its actual rows. Leave a clear outer lane for
    # base outputs that bypass the derived column on their way to packaging.
    bypass = bool(derived or "trim" in nodes) and any(a in base and b in {"jev", "review", "package"} for a, b in edges)
    margin = 44 if bypass else 24
    height = 2 * margin + 78 + 100 * (max(1, len(base), len(derived)) - 1)
    director_width = 270 if "director" in nodes else 0
    review_width = 270 if "jev" in nodes or "review" in nodes else 0
    width = (540 if repair_only else 1080 if derived else 810) + (270 if "trim" in nodes else 0) + director_width + review_width
    positions = {"ingest": (24, (height - 78) / 2), "package": (width - 242, (height - 78) / 2)}
    if "director" in nodes:
        positions["director"] = (294, (height - 78) / 2)
    if "trim" in nodes:
        positions["trim"] = (width - 512 - review_width, (height - 78) / 2)
    if "jev" in nodes:
        positions["jev"] = (width - 512, (height - 78) / 2)
    if "review" in nodes:
        positions["review"] = (width - 512, (height - 78) / 2)
    for column, x in ((base, 294 + director_width), (derived, 564 + director_width)):
        top = (height - (78 + 100 * (len(column) - 1))) / 2
        for index, key in enumerate(column):
            positions[key] = (x, top + index * 100)
    data = []
    for key in nodes:
        metrics = stages.get(key, {})
        done, total = int(metrics.get("done", 0) or 0), int(metrics.get("total", 0) or 0)
        status = metrics.get("status", "pending")
        roles = node_roles(key, source_mode, node_generation=node_generation, package_review=package_review,
                           cpt_processing=cpt_processing, review_repair=review_repair, recipe_version=recipe_version)
        if repair_only and key == "ingest":
            roles = ()
        node_bindings = {role: binding for role, binding in (bindings or {}).get(key, {}).items()
                         if role in roles}
        role = node_bindings.get("generation") or node_bindings.get("jev") or node_bindings.get("vision")
        status_label = {"completed": "完成", "running": "执行中", "failed": "失败", "cancelled": "已停止",
                        "pending": "等待", "queued": "待启动"}.get(status, "等待")
        missing_roles = metrics.get("missing_roles", [role for role in roles if role not in node_bindings])
        role_names = ({"generation": "Generation model", "jev": "Review model", "vision": "Image-reading model"}
                      if language == "en" else {"generation": "生成模型", "jev": "核对模型", "vision": "图片识别模型"})
        if key == "review":
            role_names.update(generation=("Repair model" if language == "en" else "修正模型") if "jev" in roles
                              else ("Scoring and repair model" if language == "en" else "评分与修正模型"),
                              jev="Scoring model" if language == "en" else "评分模型")
        missing_hint = (("Missing: " if language == "en" else "缺少：") +
                        (", " if language == "en" else "、").join(role_names[role] for role in missing_roles if role in role_names))
        subtitle = (f"{translate_label(status_label, language)} · {done:,} / {total:,}" if live else
                    translate_label("人工评分 · 编辑答案", language) if key == "review" and (review_repair or {}).get("mode") == "human" else
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
                    review_title = (("Repair" if language == "en" else "修正") if "jev" in roles else
                                    ("Score + repair" if language == "en" else "评分 + 修正")) if role_key == "generation" else (
                                        "Score" if language == "en" else "评分")
                    title = (review_title if key == "review" else en if language == "en" else zh)
                    models.append(f"{title}: {binding['backend']} · {binding['model']}")
        description = ("Verify the selected source and candidate, without another model parse." if language == "en" else
                       "核验选中的来源与候选结果，不重新调用模型解析。") if repair_only and key == "ingest" else " ".join(
                       translate_label(text, language) for text in node_description(key, source_mode,
                           node_generation=node_generation, package_review=package_review, cpt_processing=cpt_processing,
                           review_repair=review_repair, recipe_version=recipe_version))
        data.append({"id": key, "label": translate_label(node_display_label(key, labels, node_generation), language), "glyph": glyphs.get(key, "✓"),
                     "x": positions[key][0], "y": positions[key][1], "status": status,
                     "description": description,
                     "subtitle": subtitle, "models": models, "percent": min(100, done * 100 / total) if total else
                     100 if status == "completed" else 0,
                     "intermediate": key == "sft" and ("sft" not in targets or
                         sft_uses_cot_output(targets, recipe_version))})
    english = language == "en"
    result = {"nodes": data, "edges": edges, "selected": selected, "width": width, "height": height,
            "live": live, "language": language, "review_repair": dict(review_repair or {}),
            "labels": {"node_picker": "Go to node" if english else "定位节点",
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
    if "review" in nodes:
        if feedback_branch is None:
            phase = {"completed": "approved", "running": "reviewing", "failed": "needs_revision",
                     "waiting_human": "editing", "needs_attention": "needs_revision"}.get(
                         stages.get("review", {}).get("status"), "waiting")
            feedback_branch = {"phase": phase, "mode": (review_repair or {}).get("mode", "auto"),
                               "stages": {"scoring": {"enabled": bool((package_review or {}).get("enabled"))}}}
        return extend_feedback_branch(result, feedback_branch)
    return result


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
