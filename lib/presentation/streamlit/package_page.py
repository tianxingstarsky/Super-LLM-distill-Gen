"""Verified workflow artifacts, readable previews, and ZIP delivery."""
from __future__ import annotations

import html
import json
from typing import Any

import streamlit as st

from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_targets import TARGETS
from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES
from lib.presentation.streamlit.sample_preview import render_sample_preview
from lib.presentation.streamlit.package_style import PACKAGE_STYLE
from lib.presentation.streamlit.shared import page_header
from lib.presentation.streamlit.i18n import UntranslatedText


_STATUS = {
    "completed": ("已完成", "complete"),
    "needs_attention": ("需检查", "attention"),
    "running": ("处理中", "running"),
    "failed": ("失败", "failed"),
    "queued": ("排队中", "running"),
    "cancelled": ("已停止", "muted"),
}

_BUNDLE_PHASES = {"queued": "等待后台进程", "source": "来源校验", "archive": "压缩数据包",
                  "verify": "归档完整性校验", "recheck": "再次核对来源", "publish": "保存完整数据包"}


@st.fragment(run_every=2)
def _poll_bundle(application, run_id):
    job = application.bundle_job(run_id)
    if not job or job["status"] not in {"queued", "running"}:
        st.rerun()
    progress, actions = st.columns([1.88, 1], gap="large")
    with progress, st.container(border=True):
        st.html(_heading("▣", "后台打包进度", "可离开页面，返回后继续查看进度。"))
        phase = _BUNDLE_PHASES.get(job.get("phase"), "等待后台进程")
        st.info(phase)
        if job.get("total"):
            st.progress(min(job["done"] / job["total"], 1),
                        text=f"{_size(job['done'])} / {_size(job['total'])}")
        st.caption("进度来自实际写入或校验的字节数；每个阶段独立统计。")
    with actions, st.container(border=True):
        st.html(_heading("◈", "操作", "数据包完整写入并校验后才会显示下载入口。"))
        if st.button("停止打包", disabled=job.get("cancel_requested", False),
                     key=f"stop-package:{run_id}", width="stretch"):
            application.cancel_bundle(run_id)
            st.rerun(scope="fragment")
        if job.get("cancel_requested"):
            st.caption("正在停止，当前校验或文件处理完成后生效。")


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def _stamp(value: Any) -> str:
    return str(value or "")[:19].replace("T", " ") or "—"


def _heading(icon: str, title: str, subtitle: str = "") -> str:
    return (
        '<div class="df-pack-heading"><span class="df-pack-heading-icon" aria-hidden="true">'
        + _safe(icon) + '</span><span><strong>' + _safe(title) + '</strong>'
        + ("<small>" + _safe(subtitle) + "</small>" if subtitle else "") + "</span></div>"
    )


def _badge(status: str) -> str:
    label, kind = _STATUS.get(status, (str(status), "muted"))
    return f'<span class="df-pack-badge" data-status="{kind}">{_safe(label)}</span>'


def _file_description(name: str) -> str:
    if name == "manifest.json":
        return "来源、样本数量和 SHA-256 清单"
    if name == "quality.json":
        return "逐目标质量汇总与隔离原因"
    if name.endswith(".records.json"):
        return "逐条处理状态和来源证据"
    if name == "agent.negative.jsonl":
        return "隔离的 Agent 失败轨迹"
    if name.startswith("trl_"):
        return "TRL 兼容格式辅助文件"
    if name.endswith(".jsonl"):
        return "通过检查的训练样本"
    return "工作流产物"


def _file_preview(files: list[dict]) -> str:
    rows = []
    for row in files:
        name = str(row.get("name", "未知文件"))
        suffix = name.rsplit(".", 1)[-1].upper() if "." in name else "FILE"
        digest = str(row.get("sha256", ""))
        rows.append(
            '<div class="df-pack-file-row">'
            '<span class="df-pack-filename"><b>' + _safe(suffix[:4]) + '</b><strong data-user-content title="'
            + _safe(name) + '">' + _safe(name) + '</strong></span>'
            '<span>' + _safe(_file_description(name)) + '</span>'
            '<span>' + _safe(_size(int(row.get("bytes", 0)))) + '</span>'
            '<code title="' + _safe(digest) + '">' + _safe(digest[:12] + "…" if digest else "—") + '</code></div>'
        )
    rows.append(
        '<div class="df-pack-file-row"><span class="df-pack-filename"><b>JSON</b>'
        '<strong>manifest.json</strong></span><span>来源、样本数量和 SHA-256 清单</span>'
        '<span>随 ZIP 导出</span><code>清单自身不哈希</code></div>'
    )
    return (
        '<div class="df-pack-files"><div class="df-pack-file-head">'
        '<span>文件名</span><span>真实内容</span><span>大小</span><span>SHA-256</span></div>'
        + "".join(rows) + "</div>"
    )


def _go_to_workflow() -> None:
    st.session_state["nav"] = "自动工作流"


_REVIEW_MODES = {
    "sft": ("SFT 数据调整", "sft-review-run"),
    "dpo": ("DPO 偏好优化", "preference-review-run"),
    "orpo": ("ORPO 偏好优化", "preference-review-run:orpo"),
    "rlaif": ("RLAIF 反馈审核", "preference-review-run:rlaif"),
    "cpt": ("CPT 语料审核", "corpus-review-run"),
}


def _review_choices(state: dict, manifest: dict) -> list[tuple[str, str]]:
    """Offer only review queues backed by a verified, nonempty native artifact."""
    counts = manifest.get("counts", {})
    targets = set(state.get("targets", []))
    return [(target, _REVIEW_MODES[target][0]) for target in _REVIEW_MODES
            if target in targets and int(counts.get(target, 0)) > 0]


def _go_to_review(run_id: str, target: str) -> None:
    mode, selector_key = _REVIEW_MODES[target]
    st.session_state[f"review-mode:{st.session_state['ws']}"] = mode
    st.session_state[selector_key] = run_id
    st.session_state["nav"] = "人工审核"


def _render_empty(runs: list[dict], has_releases: bool) -> None:
    left, right = st.columns([1.8, 1], gap="large")
    with left:
        with st.container(border=True):
            st.html(_heading("⇩", "暂无新的工作流训练包", "完成的自动工作流会在这里列出可验证产物"))
            st.html(
                '<div class="df-pack-empty"><span>▣</span>'
                '<strong>当前没有可打包的完成任务</strong>'
                '<p>' + ('已有发布版本可在下方查看、校验和下载。' if has_releases else '')
                + '上传文档、导入 Agent 上下文或描述开放需求，可生成新的训练数据包。</p>'
                '</div>'
            )
            st.button("前往自动工作流", type="primary", on_click=_go_to_workflow,
                      key="package-empty-workflow")
        with st.container(border=True):
            st.html(_heading("◈", "支持的训练目标", "同一批来源可产出多类数据"))
            st.html(
                '<div class="df-pack-targets">'
                '<div><b>CPT</b><strong>持续预训练</strong><small>文档清洗和语料整理</small></div>'
                '<div><b>SFT</b><strong>指令与对话</strong><small>问答和多轮上下文</small></div>'
                '<div><b>DPO</b><strong>偏好优化</strong><small>优选与对照回答</small></div>'
                '<div><b>AGENT</b><strong>工具轨迹</strong><small>重放、剪枝与失败证据</small></div>'
                '</div>'
            )
    with right:
        with st.container(border=True):
            st.html(_heading("✓", "导出前检查", "每个 ZIP 都附带校验清单"))
            st.html(
                '<div class="df-pack-steps">'
                '<div><b>01</b><span><strong>确认任务完成</strong><small>仅展示完成或需检查的任务</small></span></div>'
                '<div><b>02</b><span><strong>核对实际文件</strong><small>逐个匹配 SHA-256 指纹</small></span></div>'
                '<div><b>03</b><span><strong>生成完整 ZIP</strong><small>包含训练、质量与来源证据</small></span></div>'
                '</div>'
            )
            st.caption("自动检查产物仍需按用途进行人工复核。")
    if runs:
        with st.container(border=True):
            st.html(_heading("◷", "最近工作流", "完成后可在这里校验并导出"))
            for row in runs[:5]:
                label = _STATUS.get(str(row.get("status")), ("未知", "muted"))[0]
                st.html('<div class="df-pack-recent"><strong data-user-content>' + _safe(row.get("name", "未命名任务"))
                        + '</strong><span>' + _safe(label) + '</span><small>'
                        + _safe(_stamp(row.get("updated_at"))) + '</small></div>')


def _render_releases(application: WorkflowApplication, releases: list[dict]) -> None:
    if not releases:
        return
    verified = sum(bool(row["verified"]) for row in releases)
    with st.container(border=True):
        st.html(_heading("◈", "已有本地发布版本",
                         f"当前工作区找到 {len(releases)} 个版本目录 · {verified} 个版本通过文件校验"))
        labels = {row["id"]: row for row in releases}
        release_id = st.selectbox(
            "选择本地发布版本", list(labels), key=f"package-release:{st.session_state['ws']}",
            format_func=lambda key: (
                ("人工审核 · " if labels[key]["kind"] == "review" else "历史导出 · ")
                + labels[key]["name"] + ("" if labels[key]["verified"] else " · 需检查")
            ),
        )
        release = labels[release_id]
        kind = "人工审核发布" if release["kind"] == "review" else "历史导出发布"
        target = str(release.get("target") or "—").upper()
        st.html(
            '<div class="df-pack-release-title" data-verified="'
            + str(release["verified"]).lower() + '"><span>✓</span><div><small>'
            + _safe(kind) + ' · ' + _safe(target) + '</small><strong>'
            + '<span data-user-content>' + _safe(release["name"]) + '</span></strong><small>创建于 '
            + _safe(_stamp(release.get("created_at"))) + '</small></div></div>'
        )
        st.caption("本地版本目录（复制后可在文件管理器中打开）")
        st.code(release["path"], language=None)
        if not release["verified"]:
            st.warning("此版本的文件暂不可下载：" + str(release.get("error", "校验未通过")))
            return
        files = release["files"]
        st.html('<div class="df-pack-release-files">' + ''.join(
            '<div><strong data-user-content>' + _safe(file["name"]) + '</strong><span>'
            + _safe(_size(file["bytes"])) + '</span><code>'
            + _safe(file["sha256"][:12] + "…" if file["sha256"] else "清单文件")
            + '</code></div>' for file in files
        ) + '</div>')
        unverified = release.get("unverified_files", [])
        if unverified:
            st.caption("以下历史文件未列入原始 SHA-256 清单，因此未提供已校验下载：")
            for file in unverified:
                st.code(file["path"], language=None)
        selected = st.selectbox(
            "选择要打开或下载的已校验文件", [file["name"] for file in files],
            key=f"package-release-file:{st.session_state['ws']}:{release_id}",
        )
        selected_file = next(file for file in files if file["name"] == selected)
        st.caption("本地文件路径（可在文件管理器中打开）")
        st.code(selected_file["path"], language=None)
        from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES
        if selected_file["bytes"] > DIRECT_DOWNLOAD_LIMIT_BYTES:
            st.caption("文件超过 50 MiB；为避免浏览器一次载入整个训练文件，请从上方本地路径读取。")
            return
        try:
            payload = application.release_file(release_id, selected)
        except (OSError, ValueError, TypeError, KeyError) as error:
            st.error(f"下载前文件校验失败：{error}")
        else:
            st.download_button("下载已校验文件", payload, file_name=selected,
                               mime="application/json" if selected.endswith(".json")
                               else "application/x-ndjson", key=f"package-release-download:{release_id}:{selected}",
                               width="stretch")
        st.caption("manifest.json 记录训练文件的 SHA-256，可与下载文件独立核对。")


def _render_summary(run: dict, state: dict, manifest: dict, quality: dict) -> None:
    counts = manifest.get("counts", {})
    eligible = sum(int(value) for value in counts.values())
    reports = quality.get("targets", {})
    candidates = sum(int(value.get("total", 0)) for value in reports.values())
    rate = round(100 * eligible / candidates) if candidates >= eligible and candidates else None
    status = str(run.get("status", "completed"))
    targets = [str(target).upper() for target in state.get("targets", [])]
    target_chips = "".join('<b>' + _safe(target) + '</b>' for target in targets) or '<span>—</span>'
    has_attention = status == "needs_attention"
    st.html(
        '<div class="df-pack-run-title"><span class="df-pack-success" data-attention="'
        + str(has_attention).lower() + '">' + ("!" if has_attention else "✓") + '</span><span>'
        '<span class="df-pack-eyebrow">' + ("已校验 · 质量需检查" if has_attention else "已完成 · 产物校验通过") + '</span><strong>'
        + '<span data-user-content>' + _safe(run.get("name", "未命名任务")) + '</span></strong><small>任务 ID：' + _safe(run.get("id", ""))
        + '　·　更新于 ' + _safe(_stamp(state.get("updated_at"))) + '</small></span>'
        + _badge(status) + '</div>'
    )
    st.html(
        '<div class="df-pack-metrics">'
        '<div><span>训练目标</span><div class="df-pack-target-chips">' + target_chips + '</div></div>'
        '<div><span>合格样本</span><strong>' + _safe(f"{eligible:,}") + '</strong><small>清单中的目标数量</small></div>'
        '<div><span>合格产出率</span><div class="df-pack-rate"><i style="--pack-rate:'
        + str(rate if rate is not None else 0) + '%"><b>'
        + _safe(f"{rate}%" if rate is not None else "—") + '</b></i></div><small>'
        + _safe(f"{eligible:,} / {candidates:,} 候选" if candidates else "无候选记录") + '</small></div>'
        '<div><span>更新时间</span><strong class="df-pack-date">'
        + _safe(_stamp(state.get("updated_at"))) + '</strong><small>当前任务</small></div>'
        '</div>'
    )


def _render_quality(quality: dict, manifest: dict) -> None:
    reports = quality.get("targets", {})
    counts = manifest.get("counts", {})
    st.html(_heading("◉", "质量与来源", "数量来自已验证清单；原因来自本次工作流质量记录"))
    rows = []
    for target, eligible in counts.items():
        summary = reports.get(target, {})
        total = int(summary.get("total", eligible))
        reasons = summary.get("reasons", {})
        reasons_text = "、".join(f"{key} ×{value}" for key, value in reasons.items()) if isinstance(reasons, dict) else ""
        rows.append(
            '<div class="df-pack-quality-row"><b>' + _safe(str(target).upper()) + '</b>'
            '<span><strong>' + _safe(eligible) + '</strong> 合格 / ' + _safe(total) + ' 候选</span>'
            '<small>' + _safe(reasons_text or "无隔离原因") + '</small></div>'
        )
    st.html('<div class="df-pack-quality">' + "".join(rows or ['<p>当前清单无训练目标。</p>']) + '</div>')
    sources = manifest.get("sources", [])
    with st.expander(f"查看来源记录（{len(sources)}）"):
        if not sources:
            st.caption("本次任务由开放需求生成，没有上传来源文件。")
        for source in sources:
            if not isinstance(source, dict):
                continue
            name = source.get("name", source.get("file", "来源文件"))
            digest = str(source.get("sha256", ""))
            st.html('<div class="df-pack-source"><strong data-user-content>' + _safe(name) + '</strong>'
                    '<small>SHA-256：' + _safe(digest or "未记录") + '</small></div>')


def _preview_targets(state: dict, inventory: dict) -> list[tuple[str, str]]:
    names = {row["name"] for row in inventory["files"]}
    targets = [(target, f"{target}.jsonl") for target in state.get("targets", [])
               if target in TARGETS and f"{target}.jsonl" in names]
    if "agent.negative.jsonl" in names:
        targets.append(("agent_negative", "agent.negative.jsonl"))
    return targets


def _render_previews(application: WorkflowApplication, run_id: str, state: dict, inventory: dict) -> None:
    choices = _preview_targets(state, inventory)
    if not choices:
        return
    st.html(_heading("◫", "真实样本预览", "直接读取已校验的训练 JSONL，展示前两条记录"))
    choice = st.selectbox("预览训练文件", choices, format_func=lambda pair: pair[1],
                          key=f"package-preview:{run_id}")
    try:
        rows = application.artifact_preview(run_id, choice[0], limit=2)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        st.error(f"样本预览失败：{error}")
        return
    if not rows:
        st.info("该文件没有可预览的合格样本。")
        return
    st.caption(f"{choice[1]} · 展示 {len(rows)} 条，完整记录请下载 ZIP。")
    for index, row in enumerate(rows, start=1):
        st.html('<div class="df-pack-preview-index">样本 ' + str(index) + '</div>')
        render_sample_preview(choice[0], row, key=f"package-messages:{run_id}:{choice[0]}:{index}")


def _render_trainer_exports(application, run_id, quality: dict, inventory: dict, manifest: dict,
                            package: dict | None) -> None:
    exports = quality.get("trainer_exports", {})
    if not exports:
        return
    st.html(_heading("◇", "TRL 训练器兼容性", "只有全部合格样本转换成功才生成辅助文件"))
    names = {row["name"] for row in inventory["files"]}
    for target, export in exports.items():
        if not isinstance(export, dict):
            continue
        summary = export.get("summary", {})
        filename = export.get("file")
        compatible = int(summary.get("compatible", 0))
        total = int(summary.get("total", 0))
        ready = (export.get("status") == "ready" and isinstance(filename, str)
                 and filename in names and manifest.get("trainer_counts", {}).get(target) == compatible)
        reasons = summary.get("reasons", {})
        reason_text = "、".join(str(reason) for reason in reasons) if isinstance(reasons, dict) else ""
        st.html(
            '<div class="df-pack-trainer"><b>' + _safe(str(target).upper()) + '</b>'
            '<span class="df-pack-trainer-status" data-ready="' + str(ready).lower() + '">'
            + ("兼容" if ready else "需处理") + '</span>'
            '<span>' + _safe(f"{compatible} / {total} 条") + '</span>'
            '<small>' + _safe(filename if ready else reason_text or "未生成完整辅助文件") + '</small></div>'
        )
        if ready and package is not None:
            size = next(row["bytes"] for row in inventory["files"] if row["name"] == filename)
            if size > DIRECT_DOWNLOAD_LIMIT_BYTES:
                st.caption("文件较大，请从本地数据包中读取，避免浏览器占用大量内存。")
            else:
                st.download_button("下载 " + filename,
                                   lambda name=filename: application.artifact_file(run_id, name), file_name=filename,
                                   mime="application/x-ndjson", key=f"package-trl:{target}:{filename}",
                                   on_click="ignore", width="stretch")
    if package is None:
        st.caption("先生成并校验完整 ZIP，即可单独下载其中的 TRL 文件。")
    st.caption("格式相容不代表模型聊天模板、分词器或训练参数已经验证。")


def _render_format_cards(inventory: dict) -> None:
    """Show only formats actually present in the verified artifact inventory."""
    names = [str(row["name"]) for row in inventory["files"]]
    native = sum(name.endswith(".jsonl") and not name.startswith("trl_") for name in names)
    trainer = sum(name.startswith("trl_") and name.endswith(".jsonl") for name in names)
    evidence = len(names) - native - trainer + 1  # manifest.json is generated into the ZIP.
    cards = (
        ("ZIP", "完整交付包", "训练文件、质量证据和来源清单", "推荐", "zip"),
        ("{}" , "原生 JSONL", f"{native} 个实际文件", "已包含" if native else "无文件", "jsonl"),
        ("TRL", "训练器辅助格式", f"{trainer} 个实际文件", "已包含" if trainer else "无文件", "trl"),
        ("✓", "质量与来源证据", f"{evidence} 个文件（含清单）", "已包含", "proof"),
    )
    st.html('<div class="df-pack-format-grid">' + "".join(
        '<div class="df-pack-format-card" data-kind="' + kind + '" data-available="'
        + str(kind in {"zip", "proof"} or (kind == "jsonl" and native > 0)
              or (kind == "trl" and trainer > 0)).lower() + '">'
        '<span class="df-pack-format-icon">' + _safe(icon) + '</span>'
        '<span class="df-pack-format-label">' + _safe(label) + '</span>'
        '<strong>' + _safe(title) + '</strong><small>' + _safe(description) + '</small></div>'
        for icon, title, description, label, kind in cards
    ) + '</div>')


def render_package_page(application: WorkflowApplication) -> None:
    st.html(PACKAGE_STYLE)
    page_header("输出打包", "核对真实训练文件、质量证据和来源信息，导出可校验的完整数据包。", "DATASET RELEASE　·　完整性校验")
    runs = application.task_runs()
    releases = application.list_releases()
    ready = [row for row in runs if row.get("status") in {"completed", "needs_attention"} and row.get("id")]
    if not ready:
        _render_empty(runs, any(row["verified"] for row in releases))
        _render_releases(application, releases)
        return

    labels = {row["id"]: row for row in ready}
    navigation, selection = st.columns([1, 2.8], gap="small", vertical_alignment="bottom")
    with navigation:
        st.button("← 返回自动工作流", key="package-back", on_click=_go_to_workflow)
    with selection:
        selection_key = f"package-run:{st.session_state['ws']}"
        if st.session_state.get(selection_key) not in labels:
            st.session_state.pop(selection_key, None)
        run_id = st.selectbox(
            "选择已完成任务", list(labels), key=selection_key,
            format_func=lambda key: UntranslatedText(f"{labels[key].get('name', '未命名任务')} · {key[:8]}"),
        )
    run = labels[run_id]
    package_key = f"verified-package:{run_id}"
    job = application.bundle_job(run_id)
    if job and job["status"] in {"queued", "running"}:
        st.session_state.pop(package_key, None)
        _poll_bundle(application, run_id)
        return
    if job and job["status"] in {"failed", "interrupted"}:
        st.warning("上次自动数据包未完成，请检查产物后重新开始。已保存的完整数据包仍然保留。")
    elif job and job["status"] == "cancelled":
        st.info("本次打包已停止，可重新生成。已完成版本仍然保留。")
    try:
        state = application.state(run_id)
        contents = application.package_contents(run_id)
        package = contents.get("bundle")
    except (KeyError, OSError, ValueError, TypeError) as error:
        st.session_state.pop(package_key, None)
        st.error(f"无法读取或校验任务产物：{error}")
        _render_releases(application, releases)
        return
    manifest = contents["manifest"]
    inventory = {"manifest": manifest, "files": contents["files"]}
    quality = contents["quality"]
    review_choices = _review_choices(state, manifest)
    # Release legacy whole-archive buffers. Only a small verified reference is kept.
    st.session_state.pop(package_key, None)
    if package:
        st.session_state[package_key] = package

    left, right = st.columns([1.88, 1], gap="large")
    with left:
        with st.container(border=True):
            _render_summary(run, state, manifest, quality)
        if run.get("status") == "needs_attention":
            st.warning("本次运行有目标缺少合格样本或达到处理上限。质量记录和隔离原因仍会保留在 ZIP 中。")
        with st.container(border=True):
            st.html(_heading("⇩", "导出格式与交付内容", "根据当前任务已验证的文件展示可用格式"))
            _render_format_cards(inventory)
            st.caption(f"完整 ZIP 包含 {len(manifest.get('sha256', {}))} 个已校验文件和 manifest.json · "
                       f"{len(manifest.get('sources', []))} 个输入来源")
        with st.container(border=True):
            st.html(_heading("▤", "包内容预览", f"实际存在 {len(inventory['files']) + 1} 个文件 · "
                             f"已校验文件共 {_size(sum(int(row['bytes']) for row in inventory['files']))}"))
            st.html(_file_preview(inventory["files"]))
        with st.container(border=True):
            _render_previews(application, run_id, state, inventory)
        with st.container(border=True):
            _render_quality(quality, manifest)
    with right:
        with st.container(border=True):
            st.html(_heading("▣", "交付进度", "先校验文件，再生成并复核 ZIP"))
            st.progress(1.0 if package is not None else 0.5,
                        text="交付准备 2 / 2 · ZIP 已校验" if package is not None
                        else "交付准备 1 / 2 · 文件已校验")
            attention = run.get("status") == "needs_attention"
            st.html('<div class="df-pack-export-state" data-attention="' + str(attention).lower()
                    + '"><span>' + ("!" if attention else "✓") + '</span><div><strong>'
                    + ("文件完整，质量需检查" if attention else "文件完整，可生成 ZIP")
                    + '</strong><small>当前产物已逐个匹配 SHA-256 清单。</small></div></div>')
            st.html('<div class="df-pack-delivery-step" data-ready="' + str(package is not None).lower()
                    + '"><b>' + ("✓" if package is not None else "2") + '</b><span><strong>ZIP 封装与复核</strong>'
                    '<small>' + ("已生成并核对压缩包内全部文件" if package is not None
                                 else "点击生成后逐文件核对压缩包") + '</small></span></div>')
            if package is not None:
                st.html('<div class="df-pack-zip-ready"><b>ZIP</b><span><strong>已生成并再次校验</strong>'
                        '<small>' + _safe(f"training-{run_id[:8]}.zip") + ' · '
                        + _safe(_size(package["bytes"])) + '</small></span></div>')
            else:
                st.caption("ZIP 尚未生成；下方按钮会重新校验所有文件与打包内容。")
        with st.container(border=True):
            st.html(_heading("◈", "操作", "下载前生成并核对完整压缩包"))
            if st.button("生成并校验完整 ZIP", type="primary", key=f"prepare-package:{run_id}",
                         width="stretch"):
                try:
                    application.start_bundle(run_id)
                    st.rerun()
                except (OSError, ValueError, TypeError, KeyError,
                        json.JSONDecodeError) as error:
                    st.session_state.pop(package_key, None)
                    package = None
                    st.error(f"无法生成或校验数据包：{error}")
            if package is not None:
                st.caption("数据包已保存到本地，刷新页面后仍可使用。")
                st.code(package["path"], language=None)
                if package["bytes"] <= DIRECT_DOWNLOAD_LIMIT_BYTES:
                    st.download_button("下载完整训练数据 ZIP", lambda: application.bundle(run_id),
                                       file_name=f"training-{run_id[:8]}.zip", mime="application/zip",
                                       key=f"download-package:{run_id}", on_click="ignore", width="stretch")
                else:
                    st.info("数据包较大，请使用上方本地路径读取，避免浏览器占用大量内存。")
            st.caption("当前为自动检查候选；训练前可按用途进行人工审核。包内 SHA-256 可由 manifest.json 复核。")
        if quality.get("trainer_exports"):
            with st.container(border=True):
                _render_trainer_exports(application, run_id, quality, inventory, manifest, package)
        if review_choices:
            with st.container(border=True):
                st.html(_heading("✓", "下一步：人工审核", "按训练目标逐条审阅并单独发布人工审核版本"))
                if len(review_choices) == 1:
                    review_target = review_choices[0][0]
                    st.html('<div class="df-pack-review-target"><b>' + _safe(review_target.upper())
                            + '</b><span>' + _safe(review_choices[0][1]) + '</span></div>')
                else:
                    review_target = st.selectbox(
                        "选择审核目标", [target for target, _ in review_choices],
                        format_func=lambda target: _REVIEW_MODES[target][0],
                        key=f"package-review-target:{run_id}",
                    )
                st.button("进入当前任务的人工审核", on_click=_go_to_review,
                          args=(run_id, review_target), key=f"package-review:{run_id}",
                          width="stretch")
                st.caption("此入口仅显示本次任务中有合格原生样本的 CPT、SFT、DPO、ORPO、RLAIF 审核队列。")
        with st.container(border=True):
            st.html(_heading("◷", "最近可导出任务", "当前工作区已完成的工作流"))
            for recent in ready[:5]:
                targets = "、".join(str(target).upper() for target in recent.get("targets", [])) or "—"
                st.html('<div class="df-pack-recent"><strong data-user-content>' + _safe(recent.get("name", "未命名任务"))
                        + '</strong>' + _badge(str(recent.get("status", "completed"))) + '<small>'
                        + _safe(targets) + ' · ' + _safe(_stamp(recent.get("updated_at"))) + '</small></div>')
    _render_releases(application, releases)
