"""Local console: drop files into a persistent cache and create training data."""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
_BRAND_MARK = base64.b64encode((ROOT / "assets" / "brand" / "shujian-cube-mark.png").read_bytes()).decode("ascii")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import streamlit as st
from lib.presentation.streamlit.i18n import canonical_navigation_route, initialize_language, install_streamlit_localization, set_language_from_choice, translate_label
from filelock import Timeout
from lib.render import MESSAGE_CSS, render_message_sequence
from lib.console_jobs import Job
from lib.bootstrap.workspaces import workspace_application
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.page_motion import PAGE_MOTION_CSS, page_surface_key

st.set_page_config(page_title="数简立方 · ShuJian Cube", layout="wide", initial_sidebar_state="expanded")
initialize_language(st)
install_streamlit_localization()
from lib.console_theme import CSS as CHROME_CSS
# Keep Streamlit's native sidebar controls in charge of opening and closing it.
st.html(f"<style>{MESSAGE_CSS}\n{CHROME_CSS}\n{PAGE_MOTION_CSS}</style>")
WORKSPACES = workspace_application()


def _ws_out():
    return WORKSPACES.output(st.session_state.get("ws", "default"))


def _OUT(name):
    return _ws_out() / name


def _select_page(page: str):
    st.session_state["nav"] = page


def _set_ui_language():
    set_language_from_choice(st, st.session_state.get("ui-language-choice", "简体中文"))


def _initialize_local_cache():
    st.sidebar.html(f'<div class="df-brand-line"><img class="df-brand-mark" src="data:image/png;base64,{_BRAND_MARK}" alt="" /><span><span class="df-brand">数简立方</span><span class="df-kicker">数据简单生成</span></span></div>')
    # Old links, CLI preferences and session values cannot switch cache roots.
    if st.session_state.get('ws', WORKSPACES.default_id) != WORKSPACES.default_id:
        st.query_params.pop('record', None)
    st.session_state['ws'] = WORKSPACES.default_id
    for key in ('folder-to-open', 'open-folder-dialog', 'open-folder-path', 'last-workspace'):
        st.session_state.pop(key, None)
    st.query_params.pop('ws', None)


def _sample_files():
    candidates = {p.resolve() for p in _ws_out().glob("*.jsonl") if "samples" in p.name or "combined_preview" in p.name}
    try:
        from lib.bootstrap.workflows import workflow_application
        candidates.update(Path(path).resolve() for path in workflow_application(ROOT, _ws_out()).reviewable_artifacts())
    except (OSError, ValueError):
        pass
    candidates.update(p.resolve() for p in WORKSPACES.source_files(st.session_state['ws'], suffixes=(".jsonl",)))
    return sorted(candidates)


@st.cache_data(show_spinner=False, max_entries=8)
def _load_normalized_samples(path_key: str, mtime_ns: int):
    """Cache complete normalized inputs for legacy bulk review, not file preview."""
    from pathlib import Path as _Path
    from lib.bootstrap.releases import release_application
    from lib.review_editor import normalize_sample
    return [normalize_sample(row) for row in release_application().read_samples(_Path(path_key))]


def _selected_samples(key, *, preview_only=False):
    files = _sample_files()
    if not files:
        st.info("本机暂无样本")
        return None, []
    source = st.selectbox("样本文件", files, format_func=lambda p: str(p.relative_to(WORKSPACES.folder(st.session_state['ws']))) if p.is_relative_to(WORKSPACES.folder(st.session_state['ws'])) else p.name,
                          key=f"{key}:{st.session_state['ws']}")
    if preview_only:
        from lib.bootstrap.releases import release_application
        return source, release_application().preview_samples(source)
    return source, _load_normalized_samples(str(source), source.stat().st_mtime_ns)


def _gate():
    from lib.gates import GateKeeper
    return GateKeeper(ROOT / "configs/gates.yaml", _OUT("gates_state.json"))


def _command_meta():
    from lib.extensions import load_commands
    return load_commands(ROOT)


def _begin(command):
    ws = st.session_state["ws"]
    job = Job(command, ws, ROOT)
    key = "job:" + ws + (":" + job.run_id if job.run_id else "")
    existing = st.session_state.get(key)
    if existing and existing.snapshot()[0] is None:
        st.warning("本次工作流已有运行任务" if job.run_id else "本机已有运行任务")
        return
    try:
        job.start()
    except Timeout:
        # Session jobs disappear on refresh; filesystem locks cover other sessions.
        st.warning("本次工作流已在其他窗口运行。" if job.run_id else
                   "已有任务在其他窗口运行，请等待完成后再试。")
        return
    except ValueError:
        st.error("无法启动任务，请刷新任务列表后重试。")
        return
    st.session_state[key] = job


@st.fragment(run_every=1)
def _job_status():
    job = st.session_state.get("job:" + st.session_state["ws"])
    if not job:
        return
    code, lines = job.snapshot()
    if code is None:
        st.info("任务运行中")
    elif code == 0:
        st.success("任务完成")
    else:
        st.error(f"任务未完成，退出码 {code}")
    st.code("\n".join(lines[-80:]) or "等待输出", language="text")


def page_overview():
    from lib.bootstrap.workflows import workflow_application
    from lib.presentation.streamlit.home_page import render_overview
    ws = st.session_state['ws']
    source = WORKSPACES.folder(ws)
    paths = WORKSPACES.source_files(ws, limit=501)
    inventory = []
    for path in paths[:6]:
        try:
            inventory.append({'name': str(path.relative_to(source)),
                              'extension': path.suffix.upper().lstrip('.'), 'size': path.stat().st_size})
        except OSError:
            continue
    render_overview(workflow_application(ROOT, _ws_out()), ws, inventory, len(paths), len(_sample_files()),
                    source.as_posix(), _ws_out().as_posix(), navigate=_select_page,
                    job_status=_job_status)


def page_preview(show_title=True):
    from lib.bootstrap.workflows import workflow_application
    from lib.presentation.streamlit.dataset_browser_page import render_dataset_preview

    render_dataset_preview(workflow_application(ROOT, _ws_out()), st.session_state["ws"],
                           lambda key: _selected_samples(key, preview_only=True), show_title=show_title)


def page_workflow():
    from lib.bootstrap.workflows import workflow_application
    from lib.bootstrap.local_inputs import local_input_application
    from lib.presentation.streamlit.workflow_page import render_workbench
    from lib.bootstrap.document_previews import document_preview_application
    from lib.bootstrap.knowledge import knowledge_application
    from lib.bootstrap.workflow_node_models import workflow_node_models_application
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.bootstrap.backends import backend_application
    from lib.bootstrap.manual_datasets import manual_dataset_application
    from lib.bootstrap.prompt_library import prompt_library_application
    render_workbench(workflow_application(ROOT, _ws_out()), _begin, workflow_node_models_application(ROOT),
                     input_cache=local_input_application(),
                     draft_application=creation_draft_application(_ws_out()),
                     backend_application=backend_application(ROOT),
                     manual_application=manual_dataset_application(_ws_out()),
                     document_preview=document_preview_application(st.session_state["ws"]),
                     knowledge_application=knowledge_application(st.session_state["ws"]),
                     prompt_library=prompt_library_application())


def page_run(show_title=True):
    if show_title:
        page_header("高级单项工具", "按需独立运行文档整理、问答生成、质量报告或兼容导出。", "ADVANCED TOOLS")
    presets = {
        "minimind 兼容草稿导出": ("export", {"format": "minimind", "bulk": False}),
        "文档语料整理": ("doc2corpus", {}),
        "文档问答生成": ("doc2data", {}),
        "质量报告": ("quality-report", {}),
        "AI 审核小队": ("dsh", {"team": True, "task": "先读取 review-team 技能，依据本机资料与审核配置派发子智能体。未提供配置先报告缺项，不创建账号、不自行放行。"}),
        "协作者拉取": ("review-remote", {"action": "pull"}),
        "环境自检": ("doctor", {}),
        "全部命令": (None, {}),
    }
    descriptions = {
        "minimind 兼容草稿导出": "将已有审核结果转换为 MiniMind 可读取的草稿文件。",
        "文档语料整理": "解析文档并清洗、分块，形成可追溯语料。",
        "文档问答生成": "从文档生成问答候选，并保留来源片段。",
        "质量报告": "统计已有样本的质量问题与检查结果。",
        "AI 审核小队": "使用本机审核配置处理需要专家复核的任务。",
        "协作者拉取": "拉取已配置协作服务中的待审核记录。",
        "环境自检": "检查本机缓存与模型服务的运行条件。",
        "全部命令": "使用完整命令目录；适合熟悉命令行参数的操作者。",
    }
    with st.container(border=True):
        section_heading("选择单项工具", "这些工具可独立处理一项操作；日常 CPT、SFT、DPO 任务请在数据生成工作台运行。", "⚙")
        st.button("进入数据生成工作台 →", key="command-open-workflow", on_click=_select_page,
                  args=("自动工作流",))
        preset = st.selectbox("任务", list(presets), key=f"command-preset:{st.session_state['ws']}")
        st.caption(descriptions[preset])
    command, defaults = presets[preset]
    from lib.cli import build_parser
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
    if command is None:
        available = [c for c in _command_meta() if c not in ("review-server", "user")]
        command = st.selectbox("选择完整命令", available)
    action_parser = sub[command]
    keybase = f"form:{st.session_state['ws']}:{preset}:{command}"
    values = []
    actions = [a for a in action_parser._actions if a.dest not in ("help", "ws")]
    primary_dests = {
        "export": {"format", "input", "out"},
        "doc2corpus": {"input", "out", "chunk_size"},
        "doc2data": {"input", "mode", "qa_per_chunk"},
        "quality-report": {"input"},
        "dsh": {"task", "review_config"},
        "review-remote": {"action", "config", "batch"},
    }.get(command, {"input", "out", "task"})
    primary = [action for action in actions if action.required or action.dest in primary_dests]
    advanced = [action for action in actions if action not in primary]

    def render_field(action):
        default = defaults.get(action.dest, action.default)
        label = action.dest.replace("_", " ")
        labels = {"input": "输入路径", "out": "输出目录", "format": "训练格式", "bulk": "放量导出（需审核）", "tag": "版本标签", "model": "模型", "backend": "模型后端", "action": "操作", "config": "审核配置路径", "batch": "每批条数", "task": "任务要求", "team": "启用子智能体团队"}
        label = labels.get(action.dest, label)
        if action.required:
            label += "（必填）"
        key = keybase + ":" + action.dest
        if action.dest == "review_config":
            candidates = sorted(str(p) for p in (ROOT / "configs").glob("review_remote*.yaml") if "example" not in p.name)
            selected = st.multiselect("审核配置", candidates, format_func=lambda p: Path(p).name, key=key)
            value = []
            for filename in selected:
                import yaml
                config = yaml.safe_load(Path(filename).read_text(encoding="utf-8")) or {}
                value.append(config.get("policy", "safety" if "safety" in filename else "quality") + "=" + filename)
        elif isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            value = st.checkbox(label, value=bool(default), key=key)
        elif action.choices:
            choices = list(action.choices)
            if command == "review-remote" and action.dest == "action":
                choices = [c for c in choices if c not in ("setup", "human")]
            if command == "review" and action.dest == "action":
                choices = [c for c in choices if c != "app"]
            value = st.selectbox(label, choices, index=choices.index(default) if default in choices else 0, key=key)
        elif action.type is int and default is not None:
            value = st.number_input(label, min_value=0, value=int(default), step=1, key=key)
        else:
            value = st.text_input(label, value=str(default or ""), key=key)
        values.append((action, value))

    with st.container(border=True):
        section_heading("任务参数", descriptions[preset], "▤")
        with st.form(keybase):
            if primary:
                columns = st.columns(2)
                for index, action in enumerate(primary):
                    with columns[index % 2]:
                        render_field(action)
            else:
                st.info("这项检查不需要额外参数，可以直接运行。")
            if advanced:
                with st.expander(f"更多参数（{len(advanced)} 项）"):
                    columns = st.columns(2)
                    for index, action in enumerate(advanced):
                        with columns[index % 2]:
                            render_field(action)
            submit = st.form_submit_button("运行", type="primary")
    if submit:
        missing = [action.dest for action, value in values if action.required and value in (None, "")]
        if missing:
            # 必填参数留空直接提交只会得到晦涩的"退出码 2"；在提交前明确指出缺什么
            st.warning("请填写必填参数：" + "、".join(d.replace("_", " ") for d in missing))
        else:
            argv = [command]
            for action, value in values:
                if isinstance(action, argparse._AppendAction):
                    for entry in value:
                        argv.extend([action.option_strings[0], str(entry)])
                elif isinstance(action, argparse._StoreTrueAction):
                    if value:
                        argv.append(action.option_strings[0])
                elif value not in (None, ""):
                    if action.option_strings:
                        argv.append(action.option_strings[0])
                    argv.append(str(value))
            _begin(argv)
    _job_status()
    gate = _gate()
    focus_ids = [gid for gid in gate.defs if gate.status(gid) == "awaiting"]
    job = st.session_state.get("job:" + st.session_state["ws"])
    if job:
        _, recent_lines = job.snapshot()
        for gid in gate.defs:
            if (gid not in focus_ids and any("闸门" in line and
                    re.search(rf"\b{re.escape(gid)}\b", line) for line in recent_lines[-80:])):
                focus_ids.append(gid)
    show_legacy = bool(st.session_state.pop(f"command-confirm-open:{st.session_state['ws']}", False))
    if focus_ids or show_legacy:
        with st.expander("本次命令需要确认", expanded=True):
            st.caption("仅在这次命令涉及模型预算、私有记录或批量导出时显示。")
            page_gates(show_title=False, focus_ids=focus_ids)


def page_review():
    from lib.review_management import render_management, reviewer_identity
    from lib.review_workspace import render_workspace

    dataset = WORKSPACES.dataset_name(st.session_state['ws'])
    mode = st.session_state.get('review-management')
    if mode:
        render_management(mode, dataset, lambda key: _selected_samples(key, preview_only=True))
        return

    def navigate(target):
        st.session_state['review-management'] = target

    try:
        username = reviewer_identity()
        render_workspace(dataset, username, st.session_state['ws'],
                         gate=_gate(), on_navigate=navigate)
    except PermissionError:
        st.error('当前身份没有此数据集的权限，请切换身份或由管理员授予权限。')
        if st.button('审核身份与接入'):
            navigate('settings')
            st.rerun()


def page_preference_review():
    from lib.bootstrap.preference_reviews import preference_review_application
    from lib.presentation.streamlit.preference_review_page import render_preference_review
    render_preference_review(preference_review_application(_ws_out()))


def page_corpus_review():
    from lib.bootstrap.corpus_reviews import corpus_review_application
    from lib.presentation.streamlit.corpus_review_page import render_corpus_review
    render_corpus_review(corpus_review_application(_ws_out()))


def page_human_review():
    page_header("人工审核 / 模型对齐", "在发布前检查样本质量、修订内容并保留审核历史。", "CPT　·　SFT　·　DPO　·　ORPO　·　RLAIF", art_kind="review")
    modes = ("SFT 数据调整", "DPO 偏好优化", "ORPO 偏好优化", "RLAIF 反馈审核", "CPT 语料审核")
    mode_key = f"review-mode:{st.session_state['ws']}"
    from lib.presentation.streamlit.review_overview import render_review_overview
    from lib.bootstrap.corpus_reviews import corpus_review_application
    from lib.bootstrap.preference_reviews import preference_review_application
    from lib.bootstrap.sft_reviews import sft_review_application
    output = _ws_out()
    applications = {
        modes[0]: sft_review_application(output),
        modes[1]: preference_review_application(output),
        modes[2]: preference_review_application(output, target="orpo"),
        modes[3]: preference_review_application(output, target="rlaif"),
        modes[4]: corpus_review_application(output),
    }
    if st.session_state.get(mode_key) not in modes:
        st.session_state[mode_key] = modes[0]
    render_review_overview(applications, mode_key)
    mode = st.session_state[mode_key]
    if mode == modes[0]:
        from lib.presentation.streamlit.sft_review_page import render_sft_review
        render_sft_review(applications[mode], legacy_review=page_review)
    elif mode == modes[1]:
        from lib.presentation.streamlit.preference_review_page import render_preference_review
        render_preference_review(applications[mode], show_header=False)
    elif mode in modes[2:4]:
        from lib.presentation.streamlit.preference_review_page import render_preference_review
        render_preference_review(applications[mode], show_header=False)
    else:
        from lib.presentation.streamlit.corpus_review_page import render_corpus_review
        render_corpus_review(applications[mode], show_header=False)


def page_output_packages():
    from lib.bootstrap.workflows import workflow_application
    from lib.presentation.streamlit.package_page import render_package_page
    render_package_page(workflow_application(ROOT, _ws_out()))


def page_quality(show_title=True):
    from lib.bootstrap.workflows import workflow_application
    from lib.bootstrap.releases import release_application
    from lib.presentation.streamlit.quality_page import render_quality_page
    workspace_id = st.session_state["ws"]
    render_quality_page(workflow_application(ROOT, _ws_out()), release_application(), workspace_id,
                        WORKSPACES.folder(workspace_id), WORKSPACES.dataset_name(workspace_id),
                        _sample_files, show_title=show_title)


def page_monitor(show_title=True):
    from lib.bootstrap.monitor import monitor_application
    from lib.presentation.streamlit.monitor_page import render_monitor_page
    render_monitor_page(monitor_application(_OUT("runs.jsonl")), st.session_state["ws"],
                        _job_status, show_title=show_title)


def page_gates(show_title=True, focus_ids=None):
    if show_title:
        page_header("命令执行确认", "只处理高级命令实际需要的人工确认。", "ADVANCED COMMANDS")
    from lib.presentation.streamlit.settings_style import SETTINGS_STYLE

    st.html(SETTINGS_STYLE)
    gate = _gate()
    if not focus_ids:
        st.info("当前没有待处理的命令确认。运行相关高级命令时，这里会显示具体要求。")
        return
    selected_ids = tuple(gid for gid in gate.defs if gid in focus_ids)
    statuses = {gid: gate.status(gid) for gid in selected_ids}
    total = len(statuses)
    st.html(
        '<div class="df-settings-section"><span><i>◇</i><span><strong>当前命令确认</strong>'
        '<small>请核对下面与当前命令有关的条件</small></span></span>'
        f'<b>{total}</b></div>'
    )
    columns = st.columns(min(3, len(statuses)), gap="medium")
    status_labels = {"approved": "已确认", "awaiting": "待确认", "pending": "尚未触发", "rejected": "未通过"}
    titles = {"G0": "模型调用与预算", "G1": "私有记录导入", "G3": "批量导出"}
    for column, gid in zip(columns, selected_ids):
        definition = gate.defs[gid]
        status = statuses[gid]
        record = gate.records.get(gid)
        context = record.context if record else {}
        language = st.session_state.get("ui_language", "zh")
        templates_en = {
            "G0": "Check the model and budget: {default_model} ({default_backend}), with a total limit of ${max_total_usd}. The run stops at the limit.",
            "G1": "Import private conversation records from {rollout_dir} and send them to the {default_backend} cloud API. Check the source and upload permission.",
            "G3": "Review the small-batch preview report before bulk export. Otherwise, keep only the candidates.",
        }
        prompt_template = templates_en.get(gid, definition.prompt) if language == "en" else definition.prompt
        prompt = re.sub(r"\{([a-zA-Z0-9_]+)\}",
                        lambda match: str(context.get(match.group(1), "not provided" if language == "en" else "待任务提供")),
                        prompt_template)
        with column, st.container(border=True, key=f"settings-gate-{gid}"):
            st.html('<div class="df-settings-gate-head" data-status="' + html.escape(status, quote=True) +
                    '"><div><strong>' +
                    html.escape(titles.get(gid, definition.title)) + '</strong></div><span data-status="' + html.escape(status, quote=True) + '">' +
                    html.escape(status_labels.get(status, status)) + '</span></div>')
            if status == "approved":
                confirmed_scope = (prompt if all(context.get(field) not in (None, "")
                                                 for field in definition.requires)
                                   else "此确认点已通过；当时的确认参数未保存在记录中。")
                st.html('<div class="df-settings-gate-prompt" data-status="approved">'
                        + html.escape(confirmed_scope) + '</div>')
                if record and record.decided_at:
                    st.html('<div class="df-settings-gate-time"><b>✓</b> 确认时间 ' +
                            html.escape(record.decided_at[:19].replace("T", " ")) + '</div>')
            else:
                st.html('<div class="df-settings-gate-prompt">' + html.escape(prompt) + '</div>')
                if st.button("确认此项", key=f"confirm-command:{gid}", width="stretch"):
                    gate.decide(gid, True, note="控制台人工确认")
                    st.rerun()
                st.caption("确认后请重新运行上方命令。")
    st.html('<div class="df-settings-footnote">这些确认用于旧版命令与 AI 修订。'
            '自动工作流在任务中执行质检与人工审核；批量导出仍会核对当前样本的审核覆盖。</div>')


def page_assets(show_title=True):
    from lib.bootstrap.asset_catalog import asset_catalog_application
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.presentation.streamlit.asset_catalog_page import render_asset_catalog
    from lib.presentation.streamlit.source_handoff import use_library_source

    ws = st.session_state["ws"]
    def import_sources():
        st.session_state[f"workflow-source-mode:{ws}"] = "文档资料"
        st.session_state[f"workflow-creation-mode:{ws}"] = "自动生成"
        st.session_state[f"workflow-upload-format:{ws}"] = "全部文档"
        st.session_state[f"workflow-setup-node:{ws}"] = "ingest"
        st.session_state.pop(f"workflow-entry-target:{ws}", None)
        _select_page("自动工作流")

    render_asset_catalog(
        asset_catalog_application(), ws, show_title=show_title,
        on_use_source=lambda source: use_library_source(source, ws, creation_draft_application(_ws_out())),
        on_import_sources=import_sources,
    )


def page_prefs(show_title=True):
    from lib.presentation.streamlit.generation_settings_page import render_generation_settings

    from lib.bootstrap.generation_settings import generation_settings_application
    render_generation_settings(generation_settings_application(ROOT), show_title=show_title)


def page_data_management():
    page_header("数据管理", "统一浏览来源与产物，预览各类训练样本并查看质量检查结果。", "DATA LIBRARY", art_kind="library")
    area = st.segmented_control(
        "数据视图", ("资产管理", "数据预览", "人工制作", "质量报告"),
        default="资产管理", key=f"data-view:{st.session_state['ws']}",
        label_visibility="collapsed",
    )
    if area == "数据预览":
        page_preview(show_title=False)
    elif area == "质量报告":
        page_quality(show_title=False)
    elif area == "人工制作":
        from lib.bootstrap.manual_datasets import manual_dataset_application
        from lib.presentation.streamlit.manual_dataset_page import render_manual_datasets
        render_manual_datasets(manual_dataset_application(_ws_out()), st.session_state['ws'], show_title=False)
    else:
        page_assets(show_title=False)


def page_task_manager():
    from lib.bootstrap.backends import backend_application
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.presentation.streamlit.work_drafts import render_work_drafts
    from lib.presentation.streamlit.task_management_styles import task_management_styles

    page_header("工作管理", "找回历史工作、继续配置草稿，或查看正在运行的工作流与结果。",
                art_kind="hero")
    st.html(task_management_styles())
    draft_application = creation_draft_application(_ws_out())
    with st.container(key="task-manager-navigation"):
        modes, drafts = st.columns([4, 1], gap="small", vertical_alignment="center")
        with modes:
            area = st.segmented_control(
                "任务视图", ("数据工作流", "命令管线", "运行日志"),
                default="数据工作流", key=f"task-view:{st.session_state['ws']}",
                format_func=lambda value: translate_label(
                    {"命令管线": "高级单项工具", "运行日志": "命令日志"}.get(value, value),
                    st.session_state.get("ui_language", "zh")),
                label_visibility="collapsed",
            )
        with drafts, st.popover("工作草稿", use_container_width=True):
            render_work_drafts(draft_application, st.session_state["ws"], _select_page)
    if area == "运行日志":
        page_monitor(show_title=False)
    elif area == "命令管线":
        page_run(show_title=False)
        with st.expander("高级命令偏好与模板"):
            page_prefs(show_title=False)
    else:
        from lib.bootstrap.workflows import workflow_application
        from lib.presentation.streamlit.task_management_page import render_task_management
        application = workflow_application(ROOT, _ws_out())
        render_task_management(
            application, st.session_state["ws"], _begin,
            lambda: _select_page("自动工作流"),
            draft_application=draft_application,
            backend_application=backend_application(ROOT),
        )


def page_system_settings():
    page_header("系统设置", "管理界面语言与生成偏好。任务模型在工作流节点选择。", "LOCAL SETTINGS")
    from lib.presentation.streamlit.settings_style import SETTINGS_STYLE

    st.html(SETTINGS_STYLE)
    with st.container(border=True):
        section_heading("界面语言", "选择控制台显示语言。", "Aa")
        st.selectbox(
            "界面语言", ["简体中文", "English"], key="ui-language-choice",
            label_visibility="collapsed", on_change=_set_ui_language,
        )
    from lib.presentation.streamlit.generation_settings_page import render_workflow_defaults
    from lib.bootstrap.generation_settings import generation_settings_application
    render_workflow_defaults(generation_settings_application(ROOT))
    with st.expander("服务连接与预算（高级）", expanded=bool(st.session_state.pop("open-model-admin", False))):
        st.caption("每次任务使用的模型在工作流节点选择；这里仅维护共用连接与预算。")
        from lib.bootstrap.backends import backend_application
        from lib.presentation.streamlit.backend_page import render_backend_page
        render_backend_page(backend_application(ROOT), embedded=True)


PAGES = {
    "首页": page_overview, "总览": page_overview,
    "数据生成": page_workflow, "自动工作流": page_workflow,
    "数据管理": page_data_management, "资产管理": page_assets, "数据预览": page_preview,
    "人工审核": page_human_review, "任务管理": page_task_manager, "工作管理": page_task_manager, "管线运行": page_run,
    "命令管线": page_task_manager,
    "运行监控": page_monitor, "监控": page_monitor, "输出打包": page_output_packages, "质量报告": page_quality,
    "模型与密钥": page_system_settings, "系统设置": page_system_settings,
    "偏好设置": page_prefs,
}
try:
    _initialize_local_cache()
except (ValueError, OSError) as error:
    st.error(str(error))
    st.stop()
# A newly started workflow should open at its live task graph.  Consume the
# handoff before the nav radio exists; Streamlit forbids changing a widget's
# session value after it has been instantiated in the current render.
_workflow_handoff = st.session_state.pop("workflow-open-run", None)
if (isinstance(_workflow_handoff, dict)
        and _workflow_handoff.get("workspace") == st.session_state["ws"]
        and _workflow_handoff.get("run_id")):
    _handoff_run_id = str(_workflow_handoff["run_id"])
    st.session_state["nav"] = "任务管理"
    st.session_state[f"task-view:{st.session_state['ws']}"] = "数据工作流"
    st.session_state[f"task-center-run:{st.session_state['ws']}"] = _handoff_run_id
    st.session_state[f"task-center-filter:{st.session_state['ws']}"] = "全部"
    st.session_state[f"task-center-search:{st.session_state['ws']}"] = ""
    st.session_state[f"task-center-locate:{st.session_state['ws']}"] = _handoff_run_id
    st.session_state["workflow-scroll-top"] = True
_preview_handoff = st.session_state.pop("workflow-open-preview", None)
if (isinstance(_preview_handoff, dict)
        and _preview_handoff.get("workspace") == st.session_state["ws"]
        and _preview_handoff.get("run_id") and _preview_handoff.get("target")):
    _preview_ws = st.session_state["ws"]
    _preview_run = str(_preview_handoff["run_id"])
    st.session_state["nav"] = "数据管理"
    st.session_state[f"data-view:{_preview_ws}"] = "数据预览"
    st.session_state[f"preview-source:{_preview_ws}"] = "工作流产物"
    st.session_state[f"data-preview-run:{_preview_ws}"] = _preview_run
    st.session_state[f"data-preview-target:{_preview_ws}:{_preview_run}"] = str(_preview_handoff["target"])
# 深链：?page=人工审核&record=<sample_id>（协作者可直接分享定位链接）
_qp_page = st.query_params.get("page")
if _qp_page == "使用指南" or st.session_state.get("nav") == "使用指南":
    st.session_state["nav"] = "总览"
elif _qp_page == "闸门" or st.session_state.get("nav") == "闸门":
    st.session_state["nav"] = "任务管理"
    st.session_state[f"task-view:{st.session_state['ws']}"] = "命令管线"
    st.session_state[f"command-confirm-open:{st.session_state['ws']}"] = True
elif _qp_page in ("管线运行", "命令管线") or st.session_state.get("nav") in ("管线运行", "命令管线"):
    st.session_state["nav"] = "任务管理"
    st.session_state[f"task-view:{st.session_state['ws']}"] = "命令管线"
elif _qp_page == "模型与密钥" or st.session_state.get("nav") == "模型与密钥":
    st.session_state["nav"] = "系统设置"
    st.session_state["open-model-admin"] = True
_review_route = {"偏好审核": "DPO 偏好优化", "语料审核": "CPT 语料审核"}
if _qp_page in _review_route:
    st.session_state["nav"] = "人工审核"
    st.session_state[f"review-mode:{st.session_state['ws']}"] = _review_route[_qp_page]
if _qp_page in PAGES and "nav" not in st.session_state:
    st.session_state["nav"] = _qp_page
visible_nav = [
    ("首页", "总览", {"首页", "总览"}),
    ("数据生成", "自动工作流", {"数据生成", "自动工作流"}),
    ("数据管理", "数据管理", {"数据管理", "资产管理", "数据预览", "质量报告"}),
    ("人工审核", "人工审核", {"人工审核"}),
    ("工作管理", "任务管理", {"工作管理", "任务管理", "管线运行", "运行监控", "监控"}),
    ("输出打包", "输出打包", {"输出打包"}),
    ("系统设置", "系统设置", {"系统设置", "偏好设置"}),
]
icons = {"首页": "⌂", "数据生成": "◈", "数据管理": "▤", "人工审核": "✓", "工作管理": "⤴",
         "任务管理": "⤴", "输出打包": "⇩", "模型与密钥": "⬡", "模型服务": "⬡", "系统设置": "⚙"}
if "nav" not in st.session_state:
    st.session_state["nav"] = _qp_page if _qp_page in PAGES else "总览"
# Older sessions could retain a formatted label from the former hidden radio
# (for example, "⌂ Home") instead of a route key. Resolve it before dispatch.
_route_label = canonical_navigation_route(
    st.session_state.get("nav", "总览"), PAGES, icons,
)
if _route_label == "工作管理":
    _route_label = "任务管理"
if _route_label == "模型与密钥":
    _route_label = "系统设置"
    st.session_state["open-model-admin"] = True
if st.session_state.get("nav") != _route_label:
    st.session_state["nav"] = _route_label
page = _route_label
for label, target, active_pages in visible_nav:
    st.sidebar.button(
        f"{icons.get(label, '•')}　{label}",
        key=f"nav-button:{target}",
        type="primary" if page in active_pages else "secondary",
        on_click=_select_page,
        args=(target,),
        width="stretch",
    )
st.sidebar.divider()
st.sidebar.caption("本机保存 · 关闭后仍保留")
from lib.bootstrap.workflows import workflow_application as _sidebar_workflow_application
from lib.presentation.streamlit.sidebar_tasks import render_sidebar_tasks
try:
    _sidebar_output = _ws_out()
except FileNotFoundError:
    _sidebar_output = None
if _sidebar_output is not None:
    render_sidebar_tasks(_sidebar_workflow_application(ROOT, _sidebar_output), st.session_state["ws"])
top_page = translate_label("工作管理" if page == "任务管理" else page, st.session_state.get("ui_language", "zh"))
st.html(f'<div class="df-topbar"><div><strong>数简立方</strong><span>　/　{html.escape(top_page)}</span></div><div class="df-topbar-meta">本机缓存　·　数据简单生成</div></div>')
st.query_params['page'] = page
from lib.presentation.streamlit.context_guide import render_context_guide
render_context_guide(page, _select_page)
try:
    _page_renderer = PAGES[page]
    with st.container(key=page_surface_key(st.session_state, _page_renderer.__name__)):
        _page_renderer()
except (ValueError, OSError) as error:
    st.error(str(error))
