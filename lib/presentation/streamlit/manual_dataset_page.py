"""Create local, user-authored image and text examples without a model call."""
from __future__ import annotations

from pathlib import PurePath
from typing import Any, Iterable

from PIL import Image
import streamlit as st

from lib.presentation.streamlit.i18n import UntranslatedText
from lib.presentation.streamlit.shared import page_header, section_heading


_PAGE_SIZE = 8
_IMAGE_BYTES = 20 * 1024 * 1024
_BATCH_BYTES = 50 * 1024 * 1024
_ERRORS = {
    "invalid_manual_dataset_name": "请填写数据集名称，最多 100 字。",
    "invalid_manual_dataset_text": "请填写问题与参考答案；各最多 100,000 字，系统说明最多 20,000 字。",
    "invalid_manual_dataset_attachments": "每条最多 8 张图片，单张最多 20 MiB，合计最多 50 MiB。",
    "invalid_manual_dataset_image": "图片无法读取。请使用静态 PNG、JPEG 或 WEBP 图片。",
    "manual_dataset_not_found": "数据集暂时无法读取。请重新选择后再试。",
    "manual_media_not_found": "图片暂时无法读取，请检查本机保存的素材。",
    "manual_media_integrity_failed": "图片完整性校验失败，请检查本机保存的素材。",
    "manual_dataset_export_too_large": "数据集超过本次导出上限，请减少素材后再试。",
}


def _error(error: Exception, fallback: str) -> str:
    # Storage errors may include paths or user content; only known codes are public.
    return _ERRORS.get(str(error), fallback)


def _attachment_payload(files: Iterable[Any]) -> list[tuple[str, bytes]]:
    files = list(files)
    if len(files) > 8:
        raise ValueError("invalid_manual_dataset_attachments")
    result: list[tuple[str, bytes]] = []
    total = 0
    for file in files:
        name = str(file.name)
        if PurePath(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("invalid_manual_dataset_image")
        data = file.getvalue()
        if not isinstance(data, bytes) or not data or len(data) > _IMAGE_BYTES:
            raise ValueError("invalid_manual_dataset_attachments")
        total += len(data)
        if total > _BATCH_BYTES:
            raise ValueError("invalid_manual_dataset_attachments")
        result.append((name, data))
    return result


def _image_grid(images: Iterable[tuple[str, bytes]]) -> None:
    images = list(images)
    if not images:
        return
    columns = st.columns(min(4, len(images)), gap="small")
    for position, (name, data) in enumerate(images):
        with columns[position % len(columns)]:
            try:
                st.image(data, caption=UntranslatedText(name), width="stretch")
            except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
                st.warning("图片无法预览。请检查图片格式后重试。")


def _saved_images(application: Any, dataset_id: str, row: dict) -> None:
    images = []
    for media in (row.get("media") or [])[:8]:
        if not isinstance(media, dict) or media.get("kind") != "image":
            continue
        try:
            # Only the application resolves IDs. Never open the stored display path.
            data = application.read_media(dataset_id, media["id"])
            images.append((str(media.get("name", "")), data))
        except (OSError, ValueError, KeyError, TypeError) as error:
            st.warning(_error(error, "图片暂时无法读取，请检查本机保存的素材。"))
    _image_grid(images)


def _text_preview(question: str, answer: str, system: str = "") -> None:
    if system:
        st.caption("系统说明")
        st.text(UntranslatedText(system))
    if question:
        st.caption("问题")
        st.text(UntranslatedText(question))
    if answer:
        st.caption("参考答案")
        st.text(UntranslatedText(answer))


def _select_page(key: str, page: int) -> None:
    st.session_state[key] = page


def _stash_text(content_key: str, field: str, widget_key: str) -> None:
    content = dict(st.session_state.get(content_key, {}))
    content[field] = st.session_state.get(widget_key, "")
    st.session_state[content_key] = content


def _stash_images(content_key: str, widget_key: str) -> None:
    content = dict(st.session_state.get(content_key, {}))
    try:
        content["attachments"] = _attachment_payload(st.session_state.get(widget_key) or [])
        content["attachment_error"] = None
    except (OSError, ValueError, TypeError, AttributeError) as error:
        content["attachments"] = []
        content["attachment_error"] = _error(error, "图片无法读取。请重新选择后重试。")
    st.session_state[content_key] = content


def _saved_samples(application: Any, dataset: dict, prefix: str) -> None:
    dataset_id = dataset["id"]
    count = int(dataset.get("count", 0))
    page_key = f"{prefix}:page:{dataset_id}"
    pages = max(1, (count + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = min(max(1, int(st.session_state.get(page_key, 1))), pages)
    st.session_state[page_key] = page
    with st.container(border=True, key="manual-datasets-saved"):
        heading, export = st.columns([3, 1], vertical_alignment="center")
        with heading:
            section_heading("已保存样本", "人工制作 · 待复核", "▤")
            st.caption(UntranslatedText(f"{dataset['name']} · {count:,}"))
        export_key = f"{prefix}:export:{dataset_id}"
        cached = st.session_state.get(export_key)
        if cached and cached.get("count") != count:
            st.session_state.pop(export_key, None)
            cached = None
        with export:
            if not cached:
                if st.button("准备导出", key=f"{export_key}:prepare", disabled=not count, width="stretch"):
                    try:
                        with st.spinner("正在打包图片与样本…"):
                            data = application.export_dataset(dataset_id)
                        cached = {"count": count, "data": data}
                        st.session_state[export_key] = cached
                    except (OSError, ValueError, TypeError) as error:
                        st.error(_error(error, "数据包未能生成。请检查本机存储后重试。"))
            if cached:
                st.download_button("下载图片与样本包", cached["data"],
                                   file_name=f"manual-{dataset_id[:8]}.zip", mime="application/zip",
                                   key=f"{export_key}:download", width="stretch")
        if not count:
            st.info("还没有人工样本。在上方填写第一条并保存。")
            return
        try:
            rows = application.list_samples(dataset_id, limit=_PAGE_SIZE, offset=(page - 1) * _PAGE_SIZE)
        except (OSError, ValueError, KeyError, TypeError) as error:
            st.error(_error(error, "样本暂时无法读取。请检查本机存储后重试。"))
            return
        if not rows:
            st.info("当前页没有可读取的样本，请刷新后重试。")
            return
        by_id = {row["id"]: row for row in rows}
        sample_key = f"{prefix}:sample:{dataset_id}:{page}"
        if st.session_state.get(sample_key) not in by_id:
            st.session_state.pop(sample_key, None)
        picker, previous, following = st.columns([4, 1, 1], vertical_alignment="bottom")
        with picker:
            selected = st.selectbox("查看人工样本", list(by_id),
                                    key=sample_key,
                                    format_func=lambda value: UntranslatedText(
                                        f"{str(by_id[value].get('question', ''))[:70]} · {value[:8]}"))
        previous.button("上一页样本", disabled=page <= 1, width="stretch", key=f"{page_key}:previous",
                        on_click=_select_page, args=(page_key, page - 1))
        following.button("下一页样本", disabled=page >= pages, width="stretch", key=f"{page_key}:next",
                         on_click=_select_page, args=(page_key, page + 1))
        st.caption(UntranslatedText(f"{page} / {pages}"))
        row = by_id[selected]
        media, text = st.columns([1, 1.65], gap="medium") if row.get("media") else (None, st.container())
        if media is not None:
            with media:
                _saved_images(application, dataset_id, row)
        with text:
            _text_preview(str(row.get("question", "")), str(row.get("answer", "")), str(row.get("system", "")))
        st.caption("导出包含样本与原始图片。人工制作的内容仍需自行复核。")


def render_manual_datasets(application: Any, workspace: Any, *, show_title: bool = True) -> None:
    """Render the manual authoring mode within the existing generation workspace."""
    workspace_id = str(getattr(workspace, "id", workspace))
    prefix = f"manual-datasets:{workspace_id}"
    if show_title:
        page_header("人工制作数据", "添加图片，编写问题与参考答案，保存为自己的训练样本。", "图片 + 文字")
    st.caption("样本与图片保存在本机，关闭或重启后仍可继续。无需调用模型。")
    notice = st.session_state.pop(f"{prefix}:notice", None)
    if notice:
        st.success(notice)
    try:
        datasets = application.list_datasets()
    except (OSError, ValueError, KeyError, TypeError):
        st.error("数据集暂时无法读取。请检查本机存储后重试。")
        return
    by_id = {row["id"]: row for row in datasets}
    selection_key = f"{prefix}:dataset"
    pending = st.session_state.pop(f"{prefix}:pending-dataset", None)
    if pending in by_id:
        st.session_state[selection_key] = pending
        st.session_state[f"{prefix}:name"] = ""
    if st.session_state.get(selection_key) not in by_id:
        st.session_state.pop(selection_key, None)
    with st.container(border=True, key="manual-datasets-picker"):
        picker, new_name, create = st.columns([1.6, 1.6, .7], vertical_alignment="bottom")
        with picker:
            dataset_id = st.selectbox("人工数据集", list(by_id), disabled=not by_id,
                                      key=selection_key, placeholder="先创建一个数据集",
                                      format_func=lambda value: UntranslatedText(str(by_id[value]["name"])))
        with new_name:
            name = st.text_input("新数据集名称", key=f"{prefix}:name", max_chars=100,
                                 placeholder="例如：产品图片问答")
        with create:
            if st.button("创建数据集", key=f"{prefix}:create", width="stretch", disabled=not name.strip()):
                try:
                    created_id = application.create_dataset(name.strip())
                    st.session_state[f"{prefix}:pending-dataset"] = created_id
                    st.session_state[f"{prefix}:notice"] = "数据集已创建，可开始制作样本。"
                    st.rerun()
                except (OSError, ValueError, TypeError) as error:
                    st.error(_error(error, "数据集未能创建。请检查本机存储后重试。"))
    if not dataset_id:
        st.info("为这份工作创建一个数据集，再添加图片与问答。")
        return
    revision_key = f"{prefix}:revision:{dataset_id}"
    revision = int(st.session_state.get(revision_key, 0))
    draft = f"{prefix}:draft:{dataset_id}:{revision}"
    content_key = f"{prefix}:content:{dataset_id}"
    content = st.session_state.get(content_key, {})
    editor, preview = st.columns([1.05, 1], gap="medium")
    with editor, st.container(border=True, key="manual-datasets-editor"):
        section_heading("制作一条样本", "图片可选 · 问题与参考答案必填", "✎")
        files = st.file_uploader("添加图片", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True,
                                 key=f"{draft}:images", max_upload_size=20,
                                 on_change=_stash_images, args=(content_key, f"{draft}:images"),
                                 help="每条最多 8 张图片，单张最多 20 MiB，合计最多 50 MiB。")
        question = st.text_area("问题", key=f"{draft}:question", height=100, max_chars=100_000,
                                value=content.get("question", ""), on_change=_stash_text,
                                args=(content_key, "question", f"{draft}:question"),
                                placeholder="希望模型根据图片或文字回答什么？")
        answer = st.text_area("参考答案", key=f"{draft}:answer", height=130, max_chars=100_000,
                              value=content.get("answer", ""), on_change=_stash_text,
                              args=(content_key, "answer", f"{draft}:answer"),
                              placeholder="填写准确、完整的参考答案。")
        with st.expander("系统说明（可选）"):
            system = st.text_area("系统说明", key=f"{draft}:system", height=80, max_chars=20_000,
                                  value=content.get("system", ""), on_change=_stash_text,
                                  args=(content_key, "system", f"{draft}:system"),
                                  label_visibility="collapsed")
        attachments = content.get("attachments", [])
        attachment_error = content.get("attachment_error")
        if files:
            try:
                attachments = _attachment_payload(files)
            except (OSError, ValueError, TypeError, AttributeError) as error:
                attachment_error = _error(error, "图片无法读取。请重新选择后重试。")
        elif attachments:
            st.caption("已保留这份草稿的图片，可直接继续填写。")
            if st.button("清除草稿图片", key=f"{draft}:clear-images"):
                st.session_state[content_key] = {**content, "attachments": [], "attachment_error": None}
                st.rerun()
        if attachment_error:
            st.error(attachment_error)
        if st.button("保存并继续下一条", key=f"{draft}:save", type="primary", width="stretch",
                     disabled=not question.strip() or not answer.strip() or attachment_error is not None):
            submitted_key = f"{prefix}:last-submitted:{dataset_id}"
            if st.session_state.get(submitted_key) != revision:
                try:
                    saved = application.append_sample(dataset_id, question=question, answer=answer,
                                                      system=system, attachments=attachments)
                    st.session_state[submitted_key] = revision
                    st.session_state[revision_key] = revision + 1
                    st.session_state.pop(content_key, None)
                    st.session_state.pop(f"{prefix}:export:{dataset_id}", None)
                    st.session_state[f"{prefix}:page:{dataset_id}"] = 1
                    st.session_state[f"{prefix}:sample:{dataset_id}:1"] = saved["id"]
                    st.session_state[f"{prefix}:notice"] = "样本已保存。可以继续制作下一条；内容仍待复核。"
                    st.rerun()
                except (OSError, ValueError, TypeError) as error:
                    st.error(_error(error, "样本未能保存，输入仍保留。请检查本机存储后重试。"))
    with preview, st.container(border=True, key="manual-datasets-preview"):
        section_heading("样本预览", "保存前核对素材与回答", "◉")
        _image_grid(attachments)
        _text_preview(question, answer, system)
        if not attachments and not question and not answer:
            st.info("可直接保存文本问答，也可添加图片。")
        st.caption("预览不会调用模型，也不会自动将样本标记为已审核。")
    _saved_samples(application, by_id[dataset_id], prefix)
