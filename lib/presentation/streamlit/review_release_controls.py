"""Shared publication controls that retain only a small release reference."""
from __future__ import annotations

import html
import streamlit as st


_PHASES = {"source": "来源校验", "audit": "审核记录校验", "data": "写出训练样本",
           "snapshot": "保存审核快照", "archive": "压缩数据包", "verify": "归档完整性校验"}


def _progress_card(job, widgets):
    phase = job.get("phase", "queued")
    keys = list(_PHASES)
    position = keys.index(phase) if phase in keys else -1
    steps = "".join(f'<span data-state="{"done" if i < position else "active" if i == position else "pending"}">'
                    f'<b>{"✓" if i < position else i + 1}</b>{name}</span>'
                    for i, name in enumerate(_PHASES.values()))
    widgets.html('<section class="df-review-release-progress"><header><b>后台打包进度</b>'
                 '<span>运行中</span></header><div>' + steps + '</div></section>')
    widgets.caption("可离开页面，返回后继续查看进度。打包期间暂停本批次审核。")
    if job.get("total"):
        widgets.progress(min(job["done"] / job["total"], 1),
                         text=f"{_PHASES.get(phase, '等待后台进程')} · {job['done']:,} / {job['total']:,}")
    else:
        widgets.info(_PHASES.get(phase, "等待后台进程"))


@st.fragment(run_every=2)
def _poll_release(application, run_id):
    job = application.release_job(run_id)
    if not job or job["status"] not in {"queued", "running"}:
        st.rerun()
    _progress_card(job, st)
    if st.button("停止打包", disabled=job.get("cancel_requested", False)):
        application.cancel_release(run_id)
        st.rerun(scope="fragment")
    if job.get("cancel_requested"):
        st.caption("正在停止，当前校验或文件处理完成后生效。")


def render_active_release(application, run_id, *, widgets=st):
    job = application.release_job(run_id)
    if job and job["status"] in {"queued", "running"}:
        _poll_release(application, run_id)
        return True
    return False


def render_review_release(application, run_id, *, target, session_key, download_key,
                          complete, approved, widgets=st):
    label = target.upper()
    job = application.release_job(run_id)
    if job and job.get("last_result"):
        widgets.session_state[session_key] = job["last_result"]
    if job and job["status"] == "completed":
        widgets.session_state[session_key] = job["result"]
    elif job and job["status"] in {"failed", "interrupted"}:
        widgets.warning("上次打包未完成，可重新生成审核版本。已生成的完整版本仍然保留。")
        error = str(job.get("error", ""))
        if "review_incomplete" in error:
            widgets.caption("仍有未完成审核的样本，请先完成审核。")
        elif "worker" in error:
            widgets.caption("后台进程未能完成本次打包，请重新启动。")
        else:
            widgets.caption("请检查来源产物与审核记录是否完整。")
    elif job and job["status"] == "cancelled":
        widgets.info("本次打包已停止，可重新生成。已完成版本仍然保留。")
    if widgets.button(f"生成已审核 {label} 版本", type="primary", disabled=not complete or approved == 0):
        try:
            application.start_release(run_id)
            widgets.rerun()
        except (OSError, PermissionError, ValueError) as error:
            widgets.error(f"无法生成审核版本：{error}")
    package = widgets.session_state.get(session_key)
    if package and not isinstance(package, dict):
        # Old session buffers can be released. Existing versions remain on disk.
        widgets.session_state.pop(session_key, None)
        package = None
    if package:
        size = package["bytes"] / (1024 * 1024)
        release_id = html.escape(str(package["id"]))
        widgets.html('<div class="df-review-release-ready"><span>✓</span><div><strong>审核版本已生成</strong>'
                     f'<small>{release_id} · {size:.2f} MB</small></div></div>')
        widgets.caption(f"已发布 {package['counts']['approved']:,} 条通过样本")
        widgets.download_button(f"下载人工审核 {label} ZIP",
            data=lambda: application.release_archive(run_id, package["id"], package["sha256"]),
            file_name=f"{target}-human-reviewed-{run_id[:8]}.zip", mime="application/zip",
            key=download_key, on_click="ignore", width="stretch")
