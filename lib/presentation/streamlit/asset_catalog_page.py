"""Data-library presentation using the application contract."""
import html
import streamlit as st
from lib.application.asset_catalog_service import AssetCatalogApplication
from lib.presentation.streamlit.shared import page_header, section_heading


def _move_page(key: str, delta: int):
    st.session_state[key] += delta


def _asset_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def render_asset_catalog(catalog: AssetCatalogApplication, workspace_id: str, show_title=True):
    if show_title:
        page_header("数据管理", "浏览来源、对话样本、偏好数据和工作流产物。", "DATA LIBRARY")
    from datetime import datetime
    from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES, TRAINING_CATEGORIES
    from lib.presentation.streamlit.data_management_style import DATA_MANAGEMENT_STYLE
    st.html(DATA_MANAGEMENT_STYLE)
    inventory = catalog.inventory(workspace_id)
    categories = catalog.categories(inventory)
    source_count = categories.get("来源文件", 0)
    output_count = len(inventory.assets) - source_count
    training_count = sum(categories.get(category, 0) for category in TRAINING_CATEGORIES)
    preference_count = categories.get("DPO 偏好对", 0) + categories.get("其他偏好数据", 0)
    category_names = ["常用文件", "全部文件", "来源文件", *(
        category for category in categories if category != "来源文件")]
    more = "+" if inventory.truncated else ""
    st.html(
        '<div class="df-data-stats">'
        f'<div class="df-data-stat"><b>▤</b><span>来源文件</span><strong>{source_count:,}{more}</strong></div>'
        f'<div class="df-data-stat"><b>◈</b><span>工作区产物</span><strong>{output_count:,}{more}</strong></div>'
        f'<div class="df-data-stat"><b>◉</b><span>训练数据文件</span><strong>{training_count:,}{more}</strong></div>'
        f'<div class="df-data-stat"><b>◇</b><span>偏好文件</span><strong>{preference_count:,}{more}</strong></div>'
        '</div>'
    )
    if inventory.truncated:
        st.caption("文件扫描已达到安全上限，计数为当前已列出的数量；请缩小工作区范围查看其余文件。")
    filter_col, search_col = st.columns([1.4, 1], vertical_alignment="bottom")
    with filter_col:
        category = st.selectbox("文件分类", category_names, key=f"asset-category:{workspace_id}")
    with search_col:
        search = st.text_input("搜索文件", placeholder="按文件名或路径筛选…",
                               key=f"asset-search:{workspace_id}")
    filtered = catalog.select(inventory, category, search)
    page_count = max(1, (len(filtered) + 99) // 100)
    page_key = f"asset-page:{workspace_id}:{category}:{search}"
    st.session_state[page_key] = min(max(1, st.session_state.get(page_key, 1)), page_count)
    page = 1
    if page_count > 1:
        previous, position, following = st.columns([1, 2, 1], vertical_alignment="bottom")
        with previous:
            st.button("上一页", disabled=st.session_state[page_key] <= 1,
                      on_click=_move_page, args=(page_key, -1), key=f"asset-prev:{workspace_id}", width="stretch")
        with position:
            page = st.number_input("页码", min_value=1, max_value=page_count, step=1, key=page_key)
        with following:
            st.button("下一页", disabled=st.session_state[page_key] >= page_count,
                      on_click=_move_page, args=(page_key, 1), key=f"asset-next:{workspace_id}", width="stretch")
    start = (int(page) - 1) * 100
    selected_rows = filtered[start:start + 100]
    list_col, detail_col = st.columns([1.55, 1], gap="large")
    with detail_col:
        detail_panel = st.container(border=True)
    with detail_panel:
        section_heading("文件详情", "查看路径、大小和内容摘录", "◉")
        if selected_rows:
            selected = st.selectbox(
                "查看文件详情", selected_rows,
                format_func=lambda asset: asset.label,
                key=f"asset-selected:{workspace_id}:{category}:{search}:{page}",
            )
    with list_col, st.container(border=True):
        section_heading("文件目录", f"{len(filtered)} 个匹配文件 · 来源优先", "▤")
        if not filtered:
            st.html('<div class="df-empty-state"><span class="df-empty-state-icon">▤</span><strong>没有匹配的文件</strong><p>清除搜索词，或切换文件分类。</p></div>')
        else:
            rows = []
            for asset in selected_rows:
                ext = asset.suffix.upper().lstrip(".") or "FILE"
                label = asset.label
                size = _asset_size(asset.size)
                rows.append(
                    f'<div class="df-data-list-row" data-selected="{str(asset == selected).lower()}">'
                    f'<div class="df-data-file"><b data-ext="{html.escape(ext, quote=True)}">{html.escape(ext[:5])}</b>'
                    f'<span title="{html.escape(label, quote=True)}">{html.escape(asset.name)}</span></div>'
                    f'<span>{html.escape(ext)}</span>'
                    f'<span title="{html.escape(label, quote=True)}">{html.escape(label)}</span>'
                    f'<span>{html.escape(size)}</span></div>'
                )
            st.html('<div class="df-data-list"><div class="df-data-list-head"><span>文件名</span><span>类型</span><span>来源 / 目录</span><span>大小</span></div>' + "".join(rows) + '</div>')
            st.caption(f"显示 {start + 1}–{start + len(selected_rows)} / {len(filtered)} 个文件")
    with detail_panel:
        if not filtered:
            st.caption("选择一个文件后，可在此查看详情并下载。")
        else:
            origin = selected.origin
            st.html(
                '<div class="df-data-detail">'
                f'<div><span>文件</span><strong>{html.escape(selected.name)}</strong></div>'
                f'<div><span>位置</span><strong>{html.escape(selected.label)}</strong></div>'
                f'<div><span>来源</span><strong class="df-data-origin" data-origin="{origin}">{"已有资料" if origin == "source" else "工作区产物"}</strong></div>'
                f'<div><span>大小</span><strong>{_asset_size(selected.size)}</strong></div>'
                f'<div><span>修改时间</span><strong>{datetime.fromtimestamp(selected.mtime_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M")}</strong></div>'
                '</div>'
            )
            if selected.size <= DIRECT_DOWNLOAD_LIMIT_BYTES:
                try:
                    payload = catalog.download(workspace_id, selected)
                    st.download_button("下载所选文件", payload, file_name=selected.name,
                                       key=f"asset-download-button:{workspace_id}:{selected.id}", width="stretch")
                except (OSError, ValueError) as error:
                    st.warning(f"文件已变化或无法下载：{error}")
            else:
                st.caption("文件超过 50 MiB；请从工作区目录直接读取，或在输出打包中下载任务数据包。")
            try:
                excerpt, note = catalog.excerpt(workspace_id, selected)
                st.caption("内容摘录")
                if excerpt:
                    st.code(excerpt, language="json" if selected.suffix in (".json", ".jsonl") else "text")
                st.html('<p class="df-data-note">' + html.escape(note) + '</p>')
            except (OSError, ValueError) as error:
                st.warning(f"文件已变化或无法预览：{error}")


