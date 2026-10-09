"""Data-library presentation using the application contract."""
import html
import hashlib
from pathlib import PurePosixPath
import streamlit as st
from filelock import Timeout
from lib.application.asset_catalog_service import AssetCatalogApplication
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.i18n import translate
from lib.domain.dataset_assets import generation_source_mode


def _move_page(key: str, delta: int):
    st.session_state[key] += delta


def _asset_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _asset_folder(asset, language):
    origin = translate("来源" if asset.origin == "source" else "产物", language)
    parts = PurePosixPath(asset.relative_path).parent.parts
    if not parts:
        return origin
    # Show nearby folders so a shared, long run prefix does not hide the
    # distinction between artifacts and stage results. Full paths stay in details.
    nearby = [part if len(part) <= 18 else part[:8] + "…" for part in parts[-2:]]
    folder = "/".join((["…"] if len(parts) > 2 else []) + nearby)
    return f"{origin} / {folder}"


def _selection_asset(assets, positions, preferred_id=None):
    """Resolve table positions only against their current, bounded page."""
    if not assets:
        return None
    if (isinstance(positions, (list, tuple)) and len(positions) == 1
            and type(positions[0]) is int and 0 <= positions[0] < len(assets)):
        return assets[positions[0]]
    return next((asset for asset in assets if asset.id == preferred_id), assets[0])


def _table_identity(assets, context):
    # Any change to row order or file version creates a fresh selection lease.
    # An old browser event can therefore never select a different file by index.
    value = (context, [(asset.id, asset.size, asset.mtime_ns) for asset in assets])
    return hashlib.sha256(repr(value).encode("utf-8", "surrogatepass")).hexdigest()[:20]


def _use_source(catalog, workspace_id, asset, on_use_source):
    key = f"asset-handoff-error:{workspace_id}"
    try:
        on_use_source(catalog.generation_source(workspace_id, asset))
    except (OSError, ValueError, Timeout, TypeError, KeyError) as error:
        st.session_state[key] = ("单次生成最多使用 200 份资料。请先在工作台减少已选资料，再添加新文件。"
                                 if str(error) == "generation_source_limit_exceeded"
                                 else "资料未能加入生成草稿。请检查文件是否变化，或本机存储是否可用。")
    else:
        st.session_state.pop(key, None)


def render_asset_catalog(catalog: AssetCatalogApplication, workspace_id: str, show_title=True, *,
                         on_use_source=None, on_import_sources=None):
    if show_title:
        page_header("数据管理", "浏览来源、对话样本、偏好数据和工作流产物。", "DATA LIBRARY", art_kind="library")
    from datetime import datetime
    from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES, TRAINING_CATEGORIES
    from lib.presentation.streamlit.data_management_style import DATA_MANAGEMENT_STYLE
    st.html(DATA_MANAGEMENT_STYLE)
    if message := st.session_state.get(f"asset-handoff-error:{workspace_id}"):
        st.error(message)
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
        f'<div class="df-data-stat"><b>◈</b><span>产物文件</span><strong>{output_count:,}{more}</strong></div>'
        f'<div class="df-data-stat"><b>◉</b><span>训练数据文件</span><strong>{training_count:,}{more}</strong></div>'
        f'<div class="df-data-stat"><b>◇</b><span>偏好文件</span><strong>{preference_count:,}{more}</strong></div>'
        '</div>'
    )
    if inventory.truncated:
        st.caption("文件扫描已达到安全上限，计数为当前已列出的数量；其余文件可从本机缓存目录查看。")
    if not inventory.assets:
        with st.container(key="asset-empty"):
            st.html('<div class="df-data-empty"><span aria-hidden="true">▤</span><div>'
                    '<strong>还没有来源或生成文件</strong>'
                    '<p>导入资料后，可在这里查看文件和生成结果。</p></div></div>')
            if on_import_sources is not None:
                st.button("导入资料", key=f"asset-import:{workspace_id}", type="primary",
                          on_click=on_import_sources)
        return
    controls = st.columns([1.4, 1, .55] if on_import_sources is not None else [1.4, 1],
                          vertical_alignment="bottom")
    filter_col, search_col = controls[:2]
    with filter_col:
        category = st.selectbox("文件分类", category_names, key=f"asset-category:{workspace_id}")
    with search_col:
        search = st.text_input("搜索文件", placeholder="按文件名或路径筛选…",
                               key=f"asset-search:{workspace_id}")
    if on_import_sources is not None:
        controls[2].button("导入资料", key=f"asset-import:{workspace_id}", width="stretch",
                           on_click=on_import_sources)
    filtered = catalog.select(inventory, category, search)
    if not filtered:
        with st.container(key="asset-empty"):
            st.html('<div class="df-data-empty"><span aria-hidden="true">⌕</span><div>'
                    '<strong>没有匹配的文件</strong>'
                    '<p>清除搜索词，或切换文件分类。</p></div></div>')
        return
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
    with list_col, st.container(border=True, key="asset-directory"):
        section_heading("文件目录", "点击文件行，查看详情或继续生成。", "▤")
        if not filtered:
            st.html('<div class="df-empty-state"><span class="df-empty-state-icon">▤</span><strong>没有匹配的文件</strong><p>清除搜索词，或切换文件分类。</p></div>')
        else:
            context = (workspace_id, category, search, int(page))
            remember_key = "asset-current:" + _table_identity([], context)
            preferred = _selection_asset(selected_rows, [], st.session_state.get(remember_key))
            ui_language = st.session_state.get("ui_language", "zh")
            headings = {label: translate(label, ui_language)
                        for label in ("文件名", "类型", "来源 / 目录", "大小")}
            rows = [{headings["文件名"]: asset.name,
                     headings["类型"]: asset.suffix.upper().lstrip(".") or "FILE",
                     headings["来源 / 目录"]: _asset_folder(asset, ui_language),
                     headings["大小"]: _asset_size(asset.size)}
                    for asset in selected_rows]
            event = st.dataframe(
                rows, hide_index=True, width="stretch", height=min(610, 42 + len(rows) * 44), row_height=44,
                column_config={
                    headings["文件名"]: st.column_config.TextColumn(headings["文件名"], width=200),
                    headings["类型"]: st.column_config.TextColumn(headings["类型"], width=64),
                    headings["来源 / 目录"]: st.column_config.TextColumn(headings["来源 / 目录"], width=210),
                    headings["大小"]: st.column_config.TextColumn(headings["大小"], width=72, alignment="right"),
                },
                on_select="rerun", selection_mode="single-row",
                selection_default={"selection": {"rows": [selected_rows.index(preferred)]}},
                key=f"asset-table:{workspace_id}:" + _table_identity(selected_rows, context),
            )
            selected = _selection_asset(selected_rows, event.selection.rows, preferred.id)
            st.session_state[remember_key] = selected.id
            st.caption(f"显示 {start + 1}–{start + len(selected_rows)} / {len(filtered)} 个文件")
    with detail_col, st.container(border=True, key="asset-details"):
        section_heading("文件详情", "查看路径、大小和内容摘录", "◉")
        if not filtered:
            st.caption("选择一个文件后，可在此查看详情并下载。")
        else:
            origin = selected.origin
            extension = html.escape(selected.suffix.upper().lstrip(".") or "FILE", quote=True)
            display_path = html.escape(translate(selected.label, ui_language))
            st.html(
                '<div class="df-data-selected-head">'
                f'<b data-ext="{extension}" aria-hidden="true">{extension}</b><div>'
                f'<strong data-user-content>{html.escape(selected.name)}</strong>'
                f'<span class="df-data-origin" data-origin="{html.escape(origin, quote=True)}">{"已有资料" if origin == "source" else "生成产物"}</span>'
                '</div></div>'
                '<dl class="df-data-file-facts">'
                f'<div><dt>大小</dt><dd>{_asset_size(selected.size)}</dd></div>'
                f'<div><dt>修改时间</dt><dd>{datetime.fromtimestamp(selected.mtime_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M")}</dd></div>'
                '</dl><div class="df-data-file-location"><span>位置</span>'
                f'<strong data-user-content>{display_path}</strong></div>'
            )
            try:
                generation_source_mode(selected)
            except ValueError:
                pass
            else:
                if on_use_source is not None:
                    st.button("用于生成", key=f"asset-use:{workspace_id}:{selected.id}",
                              type="primary", width="stretch", on_click=_use_source,
                              args=(catalog, workspace_id, selected, on_use_source),
                              help="加入当前生成草稿，保留已有目标和节点模型配置。")
            if selected.size <= DIRECT_DOWNLOAD_LIMIT_BYTES:
                try:
                    payload = catalog.download(workspace_id, selected)
                    st.download_button("下载所选文件", payload, file_name=selected.name,
                                       key=f"asset-download-button:{workspace_id}:{selected.id}", width="stretch")
                except (OSError, ValueError) as error:
                    st.warning(f"文件已变化或无法下载：{error}")
            else:
                st.caption("文件超过 50 MiB；请从本机缓存目录直接读取，或在输出打包中下载任务数据包。")
            try:
                excerpt, note = catalog.excerpt(workspace_id, selected)
                st.caption("内容摘录")
                if excerpt:
                    st.code(excerpt, language="json" if selected.suffix in (".json", ".jsonl") else "text", wrap_lines=True)
                st.html('<p class="df-data-note">' + html.escape(note) + '</p>')
            except (OSError, ValueError) as error:
                st.warning(f"文件已变化或无法预览：{error}")


