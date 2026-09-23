"""审核工作台适配器：数据信封 + 事件处理 + `st.components.v2` 注册。

webapp 在认证后的本机身份上调用 ``render_workspace(dataset, username, workspace,
gate=..., on_navigate=...)``；组件在 lib/components/review_workspace/（main 维护）。
信封字段：scope/workspace/identity/queue/query/status/record/notice/ack/suggestion；
事件 {id,action,record_id,sample_hash,...} 按 id 只处理一次并 rerun，失败保留记录。
安全：展示 HTML 全部本地生成并转义（远程图片降级为文本、链接限安全 scheme、
内联 data:image 有界）；本地图片仅限工作区源目录（拒绝 ..、链接、>10MB、非图片）；
源 JSONL 只读，保存经 rc.revise_field 生成新版本；事件不能指定 dataset/username。
"""
from __future__ import annotations

import base64
import binascii
import html as html_mod
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple
from uuid import uuid4

try:  # Streamlit 是控制台依赖；缺失时纯函数仍可测试
    import streamlit as st
except Exception:  # noqa: BLE001 - pragma: no cover
    st = None

from lib import review_editor as editor
from lib import workspace as WS

ROOT = Path(__file__).resolve().parent.parent
COMPONENT_DIR = Path(__file__).resolve().parent / "components" / "review_workspace"
INDEX_HTML = COMPONENT_DIR / "index.html"
STYLE_CSS = COMPONENT_DIR / "style.css"
INDEX_JS = COMPONENT_DIR / "index.js"
COMPONENT_NAME = "df_review_workspace"
COMPONENT_KEY = "df-review-workspace"          # 稳定会话键，绝不按记录变化
SESSION_PREFIX = COMPONENT_KEY + ":"

PAGE_LIMIT = 30
QUEUE_STATUSES = ("pending", "reviewed", "all")
ACTIONS = ("select", "query", "skip", "save", "ai", "submit", "navigate")
NAV_TARGETS = ("import", "settings")
NOTICE_KINDS = ("info", "success", "error")
EDITABLE_FIELDS = ("content", "reasoning_content")
MAX_QUERY_CHARS = 200
MAX_REASON_CHARS = 16000
MAX_EVENT_IDS = 200
MAX_ERROR_CHARS = 500

MAX_IMAGE_BYTES = 10 * 1024 * 1024           # 本地图片上限（10MB）
MAX_INLINE_DATA_CHARS = 2_000_000            # 内联 data URL 上限（有界放行）
_INLINE_DATA_RE = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,(.*)$", re.IGNORECASE | re.DOTALL)
_SAFE_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")

TEXT_PART_TYPES = ("text", "reasoning", "input_text", "output_text")


# ── 安全 Markdown 渲染（与 lib.render.render_md 同配置，附加图片/链接策略） ─────
def _plain_text_html(text: Any) -> str:
    """无 Markdown 引擎时的安全降级：纯转义文本（绝不输出图片/链接 HTML）。"""
    escaped = html_mod.escape("" if text is None else str(text))
    return f"<p>{escaped.replace(chr(10), '<br>')}</p>"


try:
    from markdown_it import MarkdownIt

    _MD = MarkdownIt("commonmark", {"breaks": True, "html": False, "linkify": False}).enable("table")

    def _rule_image(tokens, idx, options, env):  # markdown-it 规则签名（非绑定）
        token = tokens[idx]
        src = token.attrGet("src") or ""
        alt = token.content or ""
        safe = safe_inline_data_url(src)
        if safe:
            return (f'<img class="rw-img rw-md-img" src="{html_mod.escape(safe, quote=True)}"'
                    f' alt="{html_mod.escape(alt, quote=True)}" loading="lazy" />')
        label = html_mod.escape(alt) if alt else "无描述"
        return f'<span class="rw-img-blocked">［图片：{label}（远程或不受支持的图片已阻止，未自动加载）］</span>'

    def _link_is_safe(href: str) -> bool:
        if href.startswith("#") or href.startswith("?"):
            return True
        if href.startswith("//"):
            return False
        if _SAFE_SCHEME_RE.match(href):
            return href.lower().startswith(("http://", "https://", "mailto:"))
        return True  # 相对路径（不含 scheme）不会执行脚本

    def _rule_link_open(tokens, idx, options, env):
        href = tokens[idx].attrGet("href") or ""
        stack = env.setdefault("rw_link_stack", [])
        if _link_is_safe(href):
            stack.append("a")
            return (f'<a href="{html_mod.escape(href, quote=True)}"'
                    ' rel="noopener noreferrer" target="_blank">')
        stack.append("span")
        return '<span class="rw-link-blocked">'

    def _rule_link_close(tokens, idx, options, env):
        stack = env.get("rw_link_stack") or []
        kind = stack.pop() if stack else "a"
        return "</a>" if kind == "a" else "</span>"

    _MD.renderer.rules["image"] = _rule_image
    _MD.renderer.rules["link_open"] = _rule_link_open
    _MD.renderer.rules["link_close"] = _rule_link_close

    def render_text_html(text: Any) -> str:
        """Markdown → 安全 HTML：html=False、无远程图片 src、链接仅安全 scheme。"""
        if text is None:
            return ""
        return _MD.render(str(text), {})

except Exception:  # noqa: BLE001 - markdown-it 缺失/规则初始化失败时纯转义降级
    # 绝不退回 lib.render.render_md：其默认规则会输出远程图片 src，导致自动抓取。
    render_text_html = _plain_text_html


def _esc(value: Any) -> str:
    return html_mod.escape("" if value is None else str(value), quote=True)


def _json_text(value: Any) -> str:
    """人类可读、完整（不截断）的 JSON 文本；不可序列化时退回 str。"""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _sniff_image_mime(data: bytes) -> Optional[str]:
    """按魔数识别 PNG/JPEG/GIF/WebP（不信任扩展名与声明）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def safe_inline_data_url(value: Any) -> Optional[str]:
    """有界的内联 data:image(PNG/JPEG/GIF/WebP)；超出大小/格式不符/解码失败 → None。"""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_INLINE_DATA_CHARS:
        return None
    matched = _INLINE_DATA_RE.match(candidate)
    if not matched:
        return None
    payload = re.sub(r"\s+", "", matched.group(2))
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None
    mime = _sniff_image_mime(raw)
    if mime is None or mime != "image/" + matched.group(1).lower():
        return None
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _relative_parts(child: Path, parent: Path) -> Optional[Tuple[str, ...]]:
    """词法包含判断（normcase 逐段比较，不解析链接）：child 相对 parent 的段。"""
    child_parts = child.parts
    parent_parts = parent.parts
    if len(child_parts) < len(parent_parts):
        return None
    for index, part in enumerate(parent_parts):
        if os.path.normcase(child_parts[index]) != os.path.normcase(part):
            return None
    return tuple(child_parts[len(parent_parts):])


def resolve_local_image(ref: Any, source_dir: Any) -> Tuple[Optional[str], str]:
    """工作区源目录内的本地图片 → data URL；返回 ``(data_url|None, 说明)``。

    先做词法检查（拒绝 ``..``、逐段查链接/junction），再 resolve 复核仍在源目录内，
    读前读后都校验 10MB 上限；不暴露任意文件系统读取。
    """
    if not isinstance(ref, str) or not ref.strip():
        return None, "图片引用为空，需显式查看"
    if source_dir is None:
        return None, "未绑定工作区目录，需显式查看原文件"
    base = Path(source_dir)
    try:
        base = base.resolve()
    except OSError:
        return None, "工作区目录不可解析，需显式查看"
    if not base.is_dir():
        return None, "工作区目录不可用，需显式查看"
    value = ref.strip()
    if "\x00" in value:
        return None, "非法路径，已拒绝"
    candidate = Path(value)
    if ".." in candidate.parts:
        return None, "路径包含 ..，已拒绝"
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        lexical = Path(os.path.normpath(str(candidate)))
    except (OSError, ValueError):
        return None, "路径不可解析，已拒绝"
    parts = _relative_parts(lexical, base)
    if parts is None:
        return None, "路径不在工作区目录内，已拒绝"
    current = base
    for part in parts:
        current = current / part
        if WS.is_linked(current):
            return None, "路径包含链接/junction，已拒绝"
    try:
        resolved = lexical.resolve()
    except OSError:
        return None, "路径不可解析，已拒绝"
    if _relative_parts(resolved, base) is None:
        return None, "路径不在工作区目录内，已拒绝"
    if not resolved.is_file():
        return None, "文件不存在，需显式查看"
    limit = (f"{MAX_IMAGE_BYTES // (1024 * 1024)}MB" if MAX_IMAGE_BYTES >= 1024 * 1024
             else f"{MAX_IMAGE_BYTES} 字节")
    try:
        if resolved.stat().st_size > MAX_IMAGE_BYTES:
            return None, f"图片超过上限（> {limit}），未内联"
        data = resolved.read_bytes()
    except OSError:
        return None, "文件不可读，需显式查看"
    if len(data) > MAX_IMAGE_BYTES:  # 读取期间文件增长，同样拒绝
        return None, f"图片超过上限（> {limit}），未内联"
    mime = _sniff_image_mime(data)
    if mime is None:
        return None, "不是受支持的图片（PNG/JPEG/GIF/WebP），未内联"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", ""


def _img_card(src: str, alt: str) -> str:
    return (f'<figure class="rw-img-card"><img class="rw-img" src="{_esc(src)}"'
            f' alt="{_esc(alt)}" loading="lazy" />'
            f'<figcaption class="rw-img-caption">{_esc(alt)}</figcaption></figure>')


def _blocked_card(ref: str, note: str) -> str:
    return (f'<figure class="rw-img-card rw-img-blocked"><div class="rw-img-note">🔒 {_esc(note)}'
            f'</div><figcaption class="rw-img-ref">{_esc(ref)}</figcaption></figure>')


def image_ref_html(ref: Any, *, source_dir: Any = None) -> str:
    """图片引用 → 只读卡片：内联/本地安全图片显示，远程图片绝不产生 src。"""
    if not isinstance(ref, str) or not ref.strip():
        return _blocked_card("", "图片引用为空，需显式查看")
    value = ref.strip()
    inline = safe_inline_data_url(value)
    if inline:
        return _img_card(inline, "内联图片")
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "//")):
        return _blocked_card(value, "远程图片已阻止（不自动加载，需显式查看原文件）")
    if lowered.startswith("data:"):
        return _blocked_card(value[:120] + ("…" if len(value) > 120 else ""),
                             "内联图片格式不受支持或超出大小限制，未显示")
    data_url, reason = resolve_local_image(value, source_dir)
    if data_url:
        return _img_card(data_url, Path(value).name or "本地图片")
    return _blocked_card(value, reason)


def _image_ref_text(ref: str) -> str:
    return f"［图片：{ref}］"


_IMAGE_EXTRACT_KEYS = ("image_url", "image", "image_path", "image_ref")


def _extract_image_ref(part: Mapping[str, Any]) -> Optional[str]:
    kind = str(part.get("type", "")).lower()
    keys = (["image_url", "image", "url", "path", "image_path", "src"] if "image" in kind
            else [key for key in _IMAGE_EXTRACT_KEYS if key in part])
    for key in keys:
        if key not in part:
            continue
        value = part[key]
        if isinstance(value, dict):
            for inner in ("url", "path", "src", "image_url"):
                candidate = value.get(inner)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
            continue
        if isinstance(value, str) and value.strip():
            return value
    return None


def _render_part(part: Any, *, source_dir: Any = None,
                 rendered_refs: Optional[set] = None) -> Tuple[str, str]:
    if isinstance(part, str):
        return render_text_html(part), part
    if not isinstance(part, dict):
        text = _json_text(part)
        return f"<pre>{_esc(text)}</pre>", text
    if str(part.get("type", "")).lower() in TEXT_PART_TYPES:
        text = part.get("text", "")
        if not isinstance(text, str):
            text = _json_text(text)
        return render_text_html(text), text
    ref = _extract_image_ref(part)
    if ref is not None:
        if rendered_refs is not None:
            rendered_refs.add(ref)
        return image_ref_html(ref, source_dir=source_dir), _image_ref_text(ref)
    text = _json_text(part)
    return f'<pre class="rw-part">{_esc(text)}</pre>', text


def build_field_view(value: Any, field: str, role: str, *, source_dir: Any = None,
                     rendered_refs: Optional[set] = None) -> Dict[str, Any]:
    """单个可编辑字段 → {text,html,editable}（html 已转义/已按策略降级）。"""
    if isinstance(value, str):
        return {
            "text": value,
            "html": render_text_html(value),
            "editable": field == "content" or role == "assistant",
        }
    if isinstance(value, list):
        texts: List[str] = []
        htmls: List[str] = []
        for part in value:
            part_html, part_text = _render_part(part, source_dir=source_dir, rendered_refs=rendered_refs)
            if part_html:
                htmls.append(part_html)
            if part_text:
                texts.append(part_text)
        return {"text": "\n\n".join(texts), "html": "".join(htmls), "editable": False}
    text = "" if value is None else _json_text(value)
    return {"text": text, "html": f"<pre>{_esc(text)}</pre>" if text else "", "editable": False}


_MESSAGE_CORE_KEYS = {"role", "content", "reasoning_content", "toolCalls"}
_TOOL_CORE_KEYS = {"name", "input", "arguments", "id", "toolCallId", "type"}


def build_extra_html(message: Mapping[str, Any]) -> str:
    """消息附加信息：工具调用/元数据，全部转义进 <pre>，完整不截断。"""
    chunks: List[str] = []
    tool_calls = message.get("toolCalls")
    if isinstance(tool_calls, list) and tool_calls:
        chunks.append('<div class="rw-tools">')
        for call in tool_calls:
            if not isinstance(call, dict):
                chunks.append(f'<pre class="rw-tool">{_esc(_json_text(call))}</pre>')
                continue
            name = call.get("name", "call")
            chunks.append(f'<div class="rw-tool"><span class="rw-tool-name">🔧 {_esc(name)}</span>')
            for key in ("id", "toolCallId", "type"):
                if key in call:
                    chunks.append(f'<span class="rw-tool-meta">{_esc(key)}={_esc(call[key])}</span>')
            args = call.get("input", call.get("arguments"))
            if args is not None:
                chunks.append(f'<pre class="rw-tool-args">{_esc(_json_text(args))}</pre>')
            others = {key: value for key, value in call.items() if key not in _TOOL_CORE_KEYS}
            if others:
                chunks.append(f'<pre class="rw-tool-extra">{_esc(_json_text(others))}</pre>')
            chunks.append("</div>")
        chunks.append("</div>")
    if str(message.get("role", "")) == "tool":
        status = []
        for key in ("toolCallId", "tool_call_id", "isError"):
            if key in message:
                status.append(f"{key}={_esc(message[key])}")
        if status:
            chunks.append(f'<div class="rw-tool-status">{" · ".join(status)}</div>')
    others = {key: value for key, value in message.items() if key not in _MESSAGE_CORE_KEYS}
    if others:
        chunks.append(f'<pre class="rw-meta">{_esc(_json_text(others))}</pre>')
    return "".join(chunks)


def build_message_view(message: Any, index: int, *, source_dir: Any = None,
                       rendered_refs: Optional[set] = None) -> Dict[str, Any]:
    if not isinstance(message, dict):
        message = {"role": "?", "content": str(message)}
    role = str(message.get("role", ""))
    reasoning = message.get("reasoning_content")
    return {
        "index": index,
        "role": role,
        "content": build_field_view(message.get("content", ""), "content", role,
                                    source_dir=source_dir, rendered_refs=rendered_refs),
        "reasoning_content": (build_field_view(reasoning, "reasoning_content", role,
                                               source_dir=source_dir, rendered_refs=rendered_refs)
                              if reasoning is not None else None),
        "extra_html": build_extra_html(message),
    }


def _image_ref_of(item: Any) -> Optional[str]:
    if isinstance(item, str) and item.strip():
        return item
    if isinstance(item, dict):
        for key in ("path", "url", "src", "image_path"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def sample_images_html(images: Any, *, source_dir: Any = None,
                       skip: Sequence[str] = ()) -> str:
    """样本级 images 列表 → 只读图片卡片（已在消息里展示过的引用不重复）。"""
    cards: List[str] = []
    skipped = set(skip or ())
    for item in images or []:
        ref = _image_ref_of(item)
        if not ref or ref in skipped:
            continue
        cards.append(image_ref_html(ref, source_dir=source_dir))
    if not cards:
        return ""
    return '<div class="rw-sample-images">' + "".join(cards) + "</div>"


def sample_tools_html(tools: Any) -> str:
    """样本级工具定义（函数 schema）→ 可折叠 pre，完整转义、不截断。"""
    if not tools:
        return ""
    try:
        count = len(tools)
    except TypeError:
        count = 1
    return ('<details class="rw-tools-schema"><summary>工具定义（'
            f'{count}）</summary><pre>{_esc(_json_text(tools))}</pre></details>')


def _legacy_message(conversation: str) -> Dict[str, Any]:
    """历史纯文本记录 → 只读合成消息（完整转义，不空白、不可编辑）。"""
    return {
        "index": 0,
        "role": "legacy",
        "content": {"text": conversation,
                    "html": f'<pre class="rw-legacy">{_esc(conversation)}</pre>',
                    "editable": False},
        "reasoning_content": None,
        "extra_html": '<div class="rw-legacy-note">历史记录（无结构化内容）：只读查看，不可编辑</div>',
    }


def build_record_view(record: Mapping[str, Any], *, source_dir: Any = None) -> Tuple[Dict[str, Any], Optional[str]]:
    """审核记录 → 前端记录视图；返回 ``(view, warning|None)``。

    payload 经 review_editor.unpack_record 标准化还原（元数据保留）；历史纯文本记录
    以只读合成消息完整展示 conversation，并带 warning，绝不空白放行。
    """
    view: Dict[str, Any] = {
        "record_id": record.get("record_id"),
        "sample_id": record.get("sample_id"),
        "sample_hash": record.get("sample_hash") or "",
        "decision": record.get("decision"),
        "reason": record.get("reason"),
        "meta": record.get("meta") or "",
        "messages": [],
    }
    warning: Optional[str] = None
    sample: Optional[dict] = None
    try:
        sample = editor.unpack_record(dict(record))
    except (ValueError, TypeError) as error:
        warning = str(error) or "记录内容无法解析，只能只读查看"
    if sample is None:
        conversation = record.get("conversation")
        if isinstance(conversation, str) and conversation.strip():
            view["messages"].append(_legacy_message(conversation))
        elif warning is None:
            warning = "记录没有可展示的内容"
        return view, warning
    rendered_refs: set = set()
    for index, message in enumerate(sample.get("messages") or []):
        view["messages"].append(build_message_view(message, index, source_dir=source_dir,
                                                   rendered_refs=rendered_refs))
    extras = sample_tools_html(sample.get("tools"))
    extras += sample_images_html(sample.get("images") or [], source_dir=source_dir,
                                 skip=rendered_refs)
    if extras and view["messages"]:
        view["messages"][0]["extra_html"] += extras
    elif extras:
        warning = warning or "样本包含工具/图片元数据，但当前记录没有消息可挂载显示"
    return view, warning


# ── 会话状态（按 workspace:dataset:username 隔离） ────────────────────────────
def new_state() -> Dict[str, Any]:
    return {
        "initialized": False,  # 是否已从 query_params 读取过初始样本
        "current": None,       # 当前样本 sample_id
        "query": "",
        "status": "pending",
        "offset": 0,
        "notice": None,
        "ack": None,
        "suggestion": None,
        "seen": [],            # 已处理事件 id（幂等去重）
    }


def state_key(dataset: str, username: str, workspace: str) -> str:
    return f"{SESSION_PREFIX}{workspace}:{dataset}:{username}"


def session_state(dataset: str, username: str, workspace: str, session: Any = None) -> MutableMapping:
    session = st.session_state if session is None else session
    key = state_key(dataset, username, workspace)
    state = session.get(key)
    if not isinstance(state, dict):
        state = new_state()
        session[key] = state
    return state


def build_envelope(*, dataset: str, username: str, workspace: str, state: Mapping[str, Any],
                   queue: Optional[Mapping[str, Any]] = None, record: Optional[Mapping[str, Any]] = None,
                   notice: Optional[Mapping[str, Any]] = None, ack: Optional[Mapping[str, Any]] = None,
                   suggestion: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    safe_queue = {
        "items": list(queue.get("items") or []) if isinstance(queue, Mapping) else [],
        "total": int(queue.get("total") or 0) if isinstance(queue, Mapping) else 0,
        "pending": int(queue.get("pending") or 0) if isinstance(queue, Mapping) else 0,
        "reviewed": int(queue.get("reviewed") or 0) if isinstance(queue, Mapping) else 0,
        "offset": int(queue.get("offset") or 0) if isinstance(queue, Mapping) else 0,
        "limit": int(queue.get("limit") or PAGE_LIMIT) if isinstance(queue, Mapping) else PAGE_LIMIT,
    }
    return {
        "scope": f"{workspace}:{dataset}:{username}",
        "workspace": workspace,
        "identity": username,
        "queue": safe_queue,
        "query": state.get("query", ""),
        "status": state.get("status", "pending"),
        "record": dict(record) if record is not None else None,
        "notice": dict(notice) if notice else None,
        "ack": dict(ack) if ack else None,
        "suggestion": dict(suggestion) if suggestion else None,
    }


# ── 事件处理（纯函数，注入 backend/gate/loader 便于测试） ─────────────────────
def _review_center():
    from lib import review_center as rc

    return rc


def _default_client_loader():
    from lib.llm_client import load_backend

    return load_backend(ROOT, role="refine")


def _event_id(event: Mapping[str, Any]) -> Optional[str]:
    value = event.get("id")
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _ack(state: MutableMapping, event_id: Optional[str], action: Any, ok: bool) -> None:
    state["ack"] = {"id": event_id, "ok": bool(ok), "action": action if isinstance(action, str) else None}


def _fail(state: MutableMapping, event_id: Optional[str], action: Any, text: Any) -> None:
    state["notice"] = {"kind": "error", "text": str(text)[:MAX_ERROR_CHARS] or "操作失败"}
    _ack(state, event_id, action, False)


def _success(state: MutableMapping, event_id: Optional[str], action: str, text: str) -> None:
    state["notice"] = {"kind": "success", "text": text}
    _ack(state, event_id, action, True)


def _current_record(state: Mapping[str, Any], dataset: str, username: str, backend: Any) -> dict:
    sample_id = state.get("current")
    if not isinstance(sample_id, str) or not sample_id:
        raise ValueError("请先选择一条记录")
    record = backend.get_record(dataset, username, sample_id=sample_id)
    if not isinstance(record, dict) or not record.get("sample_id"):
        raise ValueError("记录不存在或不可用")
    return record


def _messages_of(record: Mapping[str, Any]) -> Tuple[Optional[List[Any]], Optional[str]]:
    try:
        sample = editor.unpack_record(dict(record))
    except (ValueError, TypeError) as error:
        return None, str(error)
    messages = sample.get("messages")
    if not isinstance(messages, list):
        return None, "记录没有结构化的 messages"
    return messages, None


def _queue_ids(page: Mapping[str, Any]) -> List[str]:
    return [item.get("sample_id") for item in (page.get("items") or [])
            if isinstance(item, Mapping) and isinstance(item.get("sample_id"), str)]


def _fetch(state: Mapping[str, Any], dataset: str, username: str, backend: Any, *, status: str,
           offset: int, limit: int = PAGE_LIMIT) -> Mapping[str, Any]:
    return backend.queue_page(dataset, username, status=status,
                              query=state.get("query", ""), offset=offset, limit=limit)


def _current_position(state: Mapping[str, Any], dataset: str, username: str, backend: Any) -> int:
    """当前样本在 pending 队列中的精确位置（rc.queue_position 单条 COUNT）。

    返回 -1 表示位置未知（不在 pending 过滤结果中，如正在查看已审记录，或无精确
    接口的旧后端）：_advance_pending 会从头取第一条 pending，绝不泄漏 offset 位置。
    """
    sample_id = state.get("current")
    if not isinstance(sample_id, str) or not sample_id:
        return -1
    helper = getattr(backend, "queue_position", None)
    if not callable(helper):
        return -1
    position = helper(dataset, username, status="pending",
                      query=state.get("query", ""), sample_id=sample_id)
    return position if type(position) is int and position >= 0 else -1


def _advance_pending(state: MutableMapping, dataset: str, username: str, backend: Any, *,
                     position: int, removed: bool) -> None:
    """提交/跳过后自动切到下一条 pending；越界回卷到第一条（跳过分页）。"""
    first = _fetch(state, dataset, username, backend, status="pending", offset=0, limit=1)
    total = int(first.get("total") or 0)
    if total <= 0:
        state["offset"] = 0
        state["current"] = None
        return
    target = position if removed else position + 1
    if target >= total or target < 0:
        target = 0
    offset = (target // PAGE_LIMIT) * PAGE_LIMIT
    page = _fetch(state, dataset, username, backend, status="pending", offset=offset)
    items = page.get("items") or []
    state["offset"] = offset
    index = target - offset
    if 0 <= index < len(items) and isinstance(items[index], Mapping):
        state["current"] = items[index].get("sample_id")
    elif items and isinstance(items[0], Mapping):
        state["current"] = items[0].get("sample_id")
        state["offset"] = 0
    else:
        state["current"] = None
        state["offset"] = 0


def _event_binding(event: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    """校验事件绑定的是当前记录与当前视图哈希，返回事件哈希。

    必须携带 int record_id 与 str sample_hash；库内记录有指纹时必须一致（陈旧视图
    拒绝）。历史记录（库内指纹为空）沿用后端宽松规则，但绝不回退成"用当前哈希顶替"。
    """
    event_record_id = event.get("record_id")
    if type(event_record_id) is not int:
        raise ValueError("事件缺少 record_id，请刷新后重试")
    if event_record_id != record.get("record_id"):
        raise ValueError("事件记录与当前记录不一致，请刷新后重试")
    event_hash = event.get("sample_hash")
    if not isinstance(event_hash, str):
        raise ValueError("事件缺少 sample_hash，请刷新后重试")
    current_hash = record.get("sample_hash") or ""
    if current_hash and event_hash != current_hash:
        raise ValueError("内容已变化（哈希不一致），请刷新后重试")
    return event_hash


def _action_select(state: MutableMapping, event: Mapping[str, Any], event_id: Optional[str],
                   dataset: str, username: str, backend: Any) -> None:
    sample_id = event.get("sample_id")
    if not isinstance(sample_id, str) or not sample_id.strip():
        raise ValueError("select 事件缺少 sample_id")
    backend.get_record(dataset, username, sample_id=sample_id)  # 校验存在且已授权
    state["current"] = sample_id
    state["suggestion"] = None
    _ack(state, event_id, "select", True)


def _action_query(state: MutableMapping, event: Mapping[str, Any], event_id: Optional[str],
                  dataset: str, username: str, backend: Any) -> None:
    query = event.get("query", state.get("query", ""))
    status = event.get("status", state.get("status", "pending"))
    if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"搜索词必须是 {MAX_QUERY_CHARS} 字符以内的字符串")
    if status not in QUEUE_STATUSES:
        raise ValueError("status 必须是 pending/reviewed/all")
    changed = query != state.get("query") or status != state.get("status")
    offset = 0 if changed else event.get("offset", state.get("offset", 0))
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 必须是非负整数")
    # 先取结果再落状态：取数失败时 query/status/offset/current 全部保持原值
    page = backend.queue_page(dataset, username, status=status, query=query,
                              offset=offset, limit=PAGE_LIMIT)
    ids = _queue_ids(page)
    current = state.get("current")
    if current not in ids:
        current = ids[0] if ids else None
    state["query"] = query
    state["status"] = status
    state["offset"] = offset
    if current != state.get("current"):
        state["suggestion"] = None
    state["current"] = current
    _ack(state, event_id, "query", True)


def _action_skip(state: MutableMapping, event: Mapping[str, Any], event_id: Optional[str],
                 dataset: str, username: str, backend: Any) -> None:
    position = _current_position(state, dataset, username, backend)
    _advance_pending(state, dataset, username, backend, position=position, removed=False)
    state["status"] = "pending"  # 已切到 pending 队列，过滤条件同步，避免状态/记录不一致
    state["suggestion"] = None
    _ack(state, event_id, "skip", True)


def _action_save(state: MutableMapping, event: Mapping[str, Any], event_id: Optional[str],
                 dataset: str, username: str, backend: Any) -> None:
    record = _current_record(state, dataset, username, backend)
    expected_hash = _event_binding(event, record)
    index = event.get("index")
    field = event.get("field")
    text = event.get("text")
    if type(index) is not int or index < 0:
        raise ValueError("index 必须是非负整数")
    if field not in EDITABLE_FIELDS:
        raise ValueError("field 必须是 content/reasoning_content")
    if not isinstance(text, str):
        raise ValueError("text 必须是字符串")
    request_id = (event_id or uuid4().hex)[:128]
    new_id = backend.revise_field(dataset, username, record.get("record_id"), expected_hash,
                                  index, field, text, request_id)
    if not isinstance(new_id, str) or not new_id:
        raise ValueError("修订接口没有返回新版本号")
    state["current"] = new_id
    state["suggestion"] = None
    _success(state, event_id, "save", f"已保存为新版本：{new_id}")


def _action_ai(state: MutableMapping, event: Mapping[str, Any], event_id: Optional[str],
               dataset: str, username: str, gate: Any, backend: Any,
               load_client: Optional[Callable[[], Any]]) -> None:
    record = _current_record(state, dataset, username, backend)
    _event_binding(event, record)
    index = event.get("index")
    field = event.get("field")
    instruction = event.get("instruction")
    if type(index) is not int or index < 0:
        raise ValueError("index 必须是非负整数")
    if field not in EDITABLE_FIELDS:
        raise ValueError("field 必须是 content/reasoning_content")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("请填写修改要求")
    if gate is None:
        raise ValueError("AI 修订不可用：闸门未接入")
    g0 = gate.status("G0")
    g1 = gate.status("G1")
    if g0 != "approved" or g1 != "approved":
        raise PermissionError(
            f"AI 修订需要 G0 与 G1 均已通过（当前 G0={g0}, G1={g1}）；"
            "请在闸门页确认，不会自动放行"
        )
    messages, warning = _messages_of(record)
    if messages is None:
        raise ValueError(warning or "记录没有可修订的结构化内容")
    loader = load_client or _default_client_loader
    client, model = loader()
    edited = editor.propose_field(messages, index, field, instruction.strip(), client)
    if not isinstance(edited, list) or not 0 <= index < len(edited):
        raise ValueError("AI 输出与记录结构不一致，未采用")
    message = edited[index]
    value = message.get(field) if isinstance(message, dict) else None
    if not isinstance(value, str):
        raise ValueError("AI 未返回可用的文本，未采用")
    state["suggestion"] = {"index": index, "field": field, "text": value,
                           "html": render_text_html(value)}
    _success(state, event_id, "ai", f"已生成修改建议（模型 {model}），确认后再保存")


def _action_submit(state: MutableMapping, event: Mapping[str, Any], event_id: Optional[str],
                   dataset: str, username: str, backend: Any) -> None:
    record = _current_record(state, dataset, username, backend)
    expected_hash = _event_binding(event, record)
    decision = event.get("decision")
    reason = event.get("reason")
    if decision not in ("keep", "reject"):
        raise ValueError("decision 必须是 keep/reject")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("请填写判定理由")
    if len(reason) > MAX_REASON_CHARS:
        raise ValueError(f"判定理由最多 {MAX_REASON_CHARS} 字符")
    position = _current_position(state, dataset, username, backend)
    count = backend.submit(dataset, username, [{
        "record_id": record.get("record_id"),
        "decision": decision,
        "reason": reason.strip(),
        "model": "human",
        "sample_hash": expected_hash,
    }])
    state["suggestion"] = None
    try:
        _advance_pending(state, dataset, username, backend, position=position, removed=True)
    except Exception:  # noqa: BLE001 - 判定已提交生效，定位失败不改变成功回执
        pass
    state["status"] = "pending"  # 已切到 pending 队列，过滤条件同步
    if count:
        _success(state, event_id, "submit",
                 f"已提交判定：{'保留' if decision == 'keep' else '驳回'}")
    else:
        prior = {"keep": "保留", "reject": "驳回"}.get(record.get("decision"), "已提交")
        _success(state, event_id, "submit", f"此前已提交过（原判定：{prior}），未重复计票")


def _action_navigate(state: MutableMapping, event: Mapping[str, Any], event_id: Optional[str],
                     on_navigate: Optional[Callable[[str], Any]]) -> None:
    target = event.get("target")
    if target not in NAV_TARGETS:
        raise ValueError("navigate 目标必须是 import/settings")
    if not callable(on_navigate):
        raise ValueError("导航回调不可用，请在控制台侧切换页面")
    on_navigate(target)
    _ack(state, event_id, "navigate", True)


def apply_event(state: MutableMapping, event: Any, *, dataset: str, username: str,
                workspace: str = "", gate: Any = None, backend: Any = None,
                load_client: Optional[Callable[[], Any]] = None,
                on_navigate: Optional[Callable[[str], Any]] = None) -> bool:
    """处理一个组件事件并更新 session 状态。

    返回 True 表示事件已被处理（调用方应 rerun 呈现结果）；重复事件（同 id）返回
    False。任何失败都保持当前记录不变，只写 notice + ``ack.ok=false``。
    """
    if not isinstance(event, Mapping) or not event:
        return False
    if not isinstance(state, MutableMapping):
        raise TypeError("state 必须是可变映射（session_state）")
    backend = backend or _review_center()
    event_id = _event_id(event)
    seen = state.setdefault("seen", [])
    if event_id and event_id in seen:
        return False
    if event_id:
        seen.append(event_id)
        del seen[:-MAX_EVENT_IDS]
    action = event.get("action")
    try:
        if action == "select":
            _action_select(state, event, event_id, dataset, username, backend)
        elif action == "query":
            _action_query(state, event, event_id, dataset, username, backend)
        elif action == "skip":
            _action_skip(state, event, event_id, dataset, username, backend)
        elif action == "save":
            _action_save(state, event, event_id, dataset, username, backend)
        elif action == "ai":
            _action_ai(state, event, event_id, dataset, username, gate, backend, load_client)
        elif action == "submit":
            _action_submit(state, event, event_id, dataset, username, backend)
        elif action == "navigate":
            _action_navigate(state, event, event_id, on_navigate)
        else:
            _fail(state, event_id, action, f"未知操作：{action!r}")
    except PermissionError as error:
        _fail(state, event_id, action, f"没有权限：{error}")
    except ValueError as error:
        _fail(state, event_id, action, str(error))
    except Exception as error:  # noqa: BLE001 - UI 必须保持可用，错误只回执不中断
        _fail(state, event_id, action, f"操作失败：{type(error).__name__}: {str(error)[:300]}")
    return True


# ── Streamlit v2 注册与渲染 ───────────────────────────────────────────────────
def shared_token_css() -> str:
    """`:host` 产品主题令牌映射（console_theme.THEME_TOKENS → 组件 --rw-* 变量）。

    追加在组件 CSS 之后（同优先级后者生效），保证工作台与控制台/消息气泡同一
    色板；``--rw-accent`` 不覆盖，保留组件自己的亮色 #9dbbff。
    """
    try:
        from lib.console_theme import THEME_TOKENS
    except Exception:  # noqa: BLE001 - 主题模块缺失时保持组件自有色板
        return ""
    mapping = {
        "--rw-bg": THEME_TOKENS.get("bg"),          # 控制台背景
        "--rw-panel": THEME_TOKENS.get("layer1"),   # 侧栏/面板
        "--rw-raised": THEME_TOKENS.get("layer2"),  # 抬升层/输入框
        "--rw-text": THEME_TOKENS.get("text"),
        "--rw-muted": THEME_TOKENS.get("text2"),
        "--rw-line": THEME_TOKENS.get("border2"),
    }
    lines = "\n".join(f"  {key}: {value};" for key, value in mapping.items() if value)
    if not lines:
        return ""
    return ("\n/* 共享主题令牌（lib/console_theme.THEME_TOKENS）：追加在组件样式后覆盖 --rw-* */\n"
            f":host {{\n{lines}\n}}\n")


def compose_css(base_css: str) -> str:
    """组件 CSS + 共享产品主题令牌块（组件自带色板在前、共享覆盖在后）。"""
    return (base_css or "") + shared_token_css()


if st is not None:
    @st.cache_resource(show_spinner=False)
    def _register_component(files: tuple) -> Any:
        """文件内容以字符串注册；mtime 进缓存键 → 组件文件更新即重新注册。"""
        from streamlit.components.v2 import component

        html = Path(files[0][0]).read_text(encoding="utf-8")
        css = compose_css(Path(files[1][0]).read_text(encoding="utf-8"))
        js = Path(files[2][0]).read_text(encoding="utf-8")
        return component(COMPONENT_NAME, html=html, css=css, js=js, isolate_styles=True)
else:  # pragma: no cover - 无 Streamlit 环境（单元测试只覆盖纯函数）
    def _register_component(files: tuple) -> Any:
        raise RuntimeError("Streamlit 不可用：无法注册审核工作台组件")


def _component() -> Any:
    entries = []
    for path in (INDEX_HTML, STYLE_CSS, INDEX_JS):
        if not path.is_file():
            raise FileNotFoundError(f"审核工作台组件文件缺失：{path}")
        entries.append((str(path), path.stat().st_mtime_ns))
    return _register_component(tuple(entries))


def _query_param(name: str) -> Optional[str]:
    try:
        value = st.query_params.get(name)
    except Exception:  # noqa: BLE001 - 无运行时/不支持时忽略
        return None
    return value if isinstance(value, str) and value else None


def _set_query_param(name: str, value: Optional[str]) -> None:
    try:
        if value:
            st.query_params[name] = value
        else:
            st.query_params.pop(name, None)
    except Exception:  # noqa: BLE001 - 深链同步失败不影响审核
        pass


def _resolve_view(state: MutableMapping, dataset: str, username: str, backend: Any,
                  source_dir: Any) -> Tuple[Mapping[str, Any], Optional[Mapping[str, Any]], Optional[str]]:
    """加载队列与当前记录；数据集未授权（PermissionError）向上抛给 webapp 切换身份。"""
    queue: Mapping[str, Any] = {"items": [], "total": 0, "pending": 0, "reviewed": 0,
                                "offset": state.get("offset", 0), "limit": PAGE_LIMIT}
    warning: Optional[str] = None
    try:
        queue = backend.queue_page(dataset, username, status=state.get("status", "pending"),
                                   query=state.get("query", ""), offset=state.get("offset", 0),
                                   limit=PAGE_LIMIT)
    except PermissionError:
        raise
    except ValueError as error:
        warning = str(error)
    ids = _queue_ids(queue)
    if not state.get("current") and ids:
        state["current"] = ids[0]
        state["suggestion"] = None
    record_view: Optional[Mapping[str, Any]] = None
    if state.get("current"):
        try:
            raw = backend.get_record(dataset, username, sample_id=state["current"])
            record_view, record_warning = build_record_view(raw, source_dir=source_dir)
            warning = warning or record_warning
        except PermissionError:
            raise
        except ValueError as error:
            state["current"] = None
            state["suggestion"] = None  # 记录已变，旧建议立即失效
            warning = warning or str(error)
            if ids:
                try:
                    raw = backend.get_record(dataset, username, sample_id=ids[0])
                    state["current"] = ids[0]
                    record_view, record_warning = build_record_view(raw, source_dir=source_dir)
                    warning = warning or record_warning
                except PermissionError:
                    raise
                except ValueError:
                    record_view = None
    return queue, record_view, warning


def _result_event(result: Any) -> Optional[Mapping[str, Any]]:
    if result is None:
        return None
    try:
        value = result.get("event") if hasattr(result, "get") else getattr(result, "event", None)
    except Exception:  # noqa: BLE001
        return None
    return value if isinstance(value, Mapping) and value else None


def render_workspace(dataset: str, username: str, workspace: str, *, gate: Any,
                     on_navigate: Optional[Callable[[str], Any]] = None) -> None:
    """渲染审核工作台；webapp 在认证后的本机身份上调用。

    - 组件键固定为 COMPONENT_KEY（会话级，不随记录变化），height='content'；
    - 事件先处理再 rerun；失败保留当前记录并回 ack.ok=false；
    - 子状态（current/query/status/offset/notice/ack/suggestion）按
      workspace:dataset:username 隔离，query_params['record'] 跟随当前样本。
    """
    if st is None:  # pragma: no cover - 运行时必然有 Streamlit
        raise RuntimeError("Streamlit 不可用：无法渲染审核工作台")
    state = session_state(dataset, username, workspace)
    backend = _review_center()
    source_dir = None
    try:
        source_dir = WS.folder(workspace) if workspace else None
    except (ValueError, OSError):
        source_dir = None
    if not state.get("initialized"):
        state["initialized"] = True
        state["current"] = _query_param("record") or state.get("current")
    queue, record_view, warning = _resolve_view(state, dataset, username, backend, source_dir)
    _set_query_param("record", state.get("current"))
    notice = state.pop("notice", None)
    if not notice and warning:
        notice = {"kind": "info", "text": str(warning)[:MAX_ERROR_CHARS]}
    ack = state.pop("ack", None)
    envelope = build_envelope(dataset=dataset, username=username, workspace=workspace, state=state,
                              queue=queue, record=record_view, notice=notice, ack=ack,
                              suggestion=state.get("suggestion") if record_view else None)
    component = _component()
    result = component(data=envelope, key=COMPONENT_KEY, height="content",
                       on_event_change=lambda: None)
    event = _result_event(result)
    if event is not None and apply_event(state, event, dataset=dataset, username=username,
                                         workspace=workspace, gate=gate, backend=backend,
                                         on_navigate=on_navigate):
        # rerun 前同步深链：最后一条提交后 current=None，不能让旧 record 参数复活
        _set_query_param("record", state.get("current"))
        st.rerun()
