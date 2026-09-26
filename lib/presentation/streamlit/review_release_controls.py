"""Shared publication controls that retain only a small release reference."""
from __future__ import annotations

import html
import streamlit as st


def render_review_release(application, run_id, *, target, session_key, download_key,
                          complete, approved, widgets=st):
    label = target.upper()
    if widgets.button(f"生成已审核 {label} 版本", type="primary", disabled=not complete or approved == 0):
        try:
            with widgets.spinner("正在生成审核版本…", show_time=True):
                widgets.session_state[session_key] = application.prepare_release(run_id)
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
