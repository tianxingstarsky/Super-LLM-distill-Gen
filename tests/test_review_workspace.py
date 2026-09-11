"""审核工作台适配器测试：安全渲染/图片边界/不截断 + 事件处理（假 backend/gate）。

只测试本模块的纯函数与事件处理（不依赖 Streamlit 运行时、不启动 review_center HTTP、
不触碰真实数据库）：渲染安全性、本地图片作用域、工具元数据不截断、save/submit/ai
闸门与幂等去重。临时图片写入 F:\\无项目工作文件夹\\tools\\tmp（存在时），否则用
pytest 的 tmp_path。
"""
from __future__ import annotations

import base64
import json
import pathlib
import shutil
import sys
from uuid import uuid4

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import review_editor as editor  # noqa: E402
from lib import review_workspace as rw  # noqa: E402

F_TMP_ROOT = pathlib.Path("F:/无项目工作文件夹/tools/tmp")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
DATASET = "rollout_review"


@pytest.fixture
def tmp_area(tmp_path):
    """测试临时目录：优先 F 盘 tools/tmp（项目约定），否则退回 pytest tmp_path。"""
    if F_TMP_ROOT.parent.exists():
        area = F_TMP_ROOT / f"review-workspace-{uuid4().hex[:10]}"
        area.mkdir(parents=True, exist_ok=True)
        try:
            yield area
        finally:
            shutil.rmtree(area, ignore_errors=True)
    else:
        yield tmp_path


# ── 假后端 / 假闸门 ──────────────────────────────────────────────────────────
def _payload(sample_id="s1", text="旧正文", images=None, extra=None):
    sample = {
        "id": sample_id,
        "messages": [
            {"role": "system", "content": "系统"},
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": text, "reasoning_content": "原始思考"},
        ],
        "images": list(images or []),
        "tools": [{"type": "function", "function": {"name": "fn"}}],
        "model": "m",
        "finish_reason": "stop",
    }
    sample.update(extra or {})
    return editor.pack_sample(sample)


class FakeBackend:
    def __init__(self, records, pending=None, reviewed=()):
        self.records = {sid: dict(rec) for sid, rec in records.items()}
        self.pending = list(pending if pending is not None else records)
        self.reviewed = list(reviewed)
        self.calls = []
        self.raise_on = {}
        self.revise_result = None
        self.submit_count_override = None
        self.missing = set()

    def _item(self, sid):
        rec = self.records[sid]
        return {"record_id": rec["record_id"], "sample_id": sid,
                "instruction": rec.get("instruction", ""), "meta": rec.get("meta", ""),
                "sample_hash": rec["sample_hash"], "decision": rec.get("decision"),
                "reason": rec.get("reason")}

    def queue_page(self, dataset, username, *, status="pending", query="", offset=0, limit=30):
        self.calls.append(("queue_page", status, query, offset, limit))
        if "queue_page" in self.raise_on:
            raise self.raise_on["queue_page"]
        ids = {"pending": self.pending, "reviewed": self.reviewed,
               "all": self.pending + self.reviewed}[status]
        if query:
            ids = [sid for sid in ids if query in sid
                   or query in str(self.records[sid].get("instruction", ""))]
        items = [self._item(sid) for sid in ids[offset:offset + limit]]
        return {"items": items, "total": len(ids), "pending": len(self.pending),
                "reviewed": len(self.reviewed), "offset": offset, "limit": limit}

    def queue_position(self, dataset, username, *, status="pending", query="", sample_id=None):
        page = self.queue_page(dataset, username, status=status, query=query, offset=0, limit=10 ** 9)
        ids = [item["sample_id"] for item in page["items"]]
        return ids.index(sample_id) if sample_id in ids else None

    def get_record(self, dataset, username, *, record_id=None, sample_id=None):
        self.calls.append(("get_record", record_id, sample_id))
        if sample_id is None:
            sample_id = next((sid for sid, rec in self.records.items()
                              if rec["record_id"] == record_id), None)
        if sample_id not in self.records or sample_id in self.missing:
            raise ValueError("Record not found")
        return dict(self.records[sample_id])

    def revise_field(self, dataset, username, record_id, expected_hash, index, field, text, request_id):
        self.calls.append(("revise_field", record_id, expected_hash, index, field, text, request_id))
        if "revise_field" in self.raise_on:
            raise self.raise_on["revise_field"]
        return self.revise_result or "s1-r1"

    def submit(self, dataset, username, decisions):
        self.calls.append(("submit", [dict(row) for row in decisions]))
        if "submit" in self.raise_on:
            raise self.raise_on["submit"]
        if self.submit_count_override is not None:
            return self.submit_count_override
        count = 0
        for row in decisions:
            sid = next((s for s, rec in self.records.items()
                        if rec["record_id"] == row["record_id"]), None)
            if sid is None:
                raise ValueError("Record not found")
            self.records[sid]["decision"] = row["decision"]
            self.records[sid]["reason"] = row["reason"]
            if sid in self.pending:
                self.pending.remove(sid)
            if sid not in self.reviewed:
                self.reviewed.append(sid)
            count += 1
        return count


class FakeGate:
    def __init__(self, statuses):
        self.statuses = dict(statuses)
        self.status_calls = []
        self.proposed = []
        self.decided = []

    def status(self, gate_id):
        self.status_calls.append(gate_id)
        return self.statuses.get(gate_id, "pending")

    def propose(self, *args, **kwargs):
        self.proposed.append(args)
        raise AssertionError("适配器不得自动提议闸门")

    def decide(self, *args, **kwargs):
        self.decided.append(args)
        raise AssertionError("适配器不得自动决定闸门")


def _backend_three(pending=("s1", "s2", "s3")):
    records = {sid: {"record_id": index + 1, "sample_id": sid, "sample_hash": "h1",
                     "decision": None, "reason": None, "meta": "m", "instruction": "问题",
                     "payload": _payload(sid)} for index, sid in enumerate(pending)}
    return FakeBackend(records, pending=list(pending))


# ── 渲染安全：HTML 转义 / 远程图片 / 链接 scheme / 内联 data URL ─────────────
def test_markdown_escapes_raw_html_and_blocks_remote_images():
    html = rw.render_text_html('<img src=x onerror="alert(1)">')
    assert "<img" not in html and "&lt;img" in html  # 原始 HTML 只转义，不执行

    remote = rw.render_text_html("看图 ![截图](https://evil.example/p.png) 结束")
    assert "<img" not in remote and 'src="http' not in remote
    assert "https://evil.example" not in remote
    assert "图片" in remote and "结束" in remote


def test_markdown_links_only_safe_schemes():
    safe = rw.render_text_html("[文档](https://example.com/a?b=1&c=2)")
    assert 'href="https://example.com/a?b=1&amp;c=2"' in safe
    assert 'rel="noopener noreferrer"' in safe and 'target="_blank"' in safe

    for bad in ("javascript:alert(1)", "data:text/html,<script>1</script>", "file:///etc/passwd"):
        html = rw.render_text_html(f"[点我]({bad})")
        assert "<a " not in html and "href=" not in html


def test_inline_data_url_allowed_only_bounded_images(monkeypatch):
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    html = rw.render_text_html(f"![p]({data_url})")
    assert 'src="data:image/png;base64,' in html

    assert rw.safe_inline_data_url(data_url) == data_url
    assert rw.safe_inline_data_url("data:image/svg+xml;base64,PHN2Zz4=") is None
    assert rw.safe_inline_data_url("data:image/png;base64,!!!!") is None
    assert rw.safe_inline_data_url("data:image/png;base64," + base64.b64encode(b"not-png").decode()) is None

    monkeypatch.setattr(rw, "MAX_INLINE_DATA_CHARS", 40)
    blocked = rw.render_text_html(f"![p]({data_url})")
    assert "<img" not in blocked and "已阻止" in blocked


def test_plain_text_fallback_never_emits_images_or_links():
    """Markdown 引擎不可用时的降级必须是纯转义文本，绝不退回会带远程 src 的渲染。"""
    source = '<b>x</b> ![a](https://evil.example/e.png) [l](javascript:alert(1))\n第二行'
    out = rw._plain_text_html(source)
    assert "<img" not in out and "<a " not in out and "href" not in out
    assert "&lt;b&gt;" in out and "<br>" in out and "第二行" in out


# ── 本地图片：工作区目录内、拒绝穿越/链接/超限/非图片 ───────────────────────
def test_local_images_scoped_bounded_and_readonly(tmp_area):
    source = tmp_area / "ws"
    source.mkdir()
    (source / "pic.png").write_bytes(PNG)
    outside = tmp_area / "secret.png"
    outside.write_bytes(PNG)

    def render(ref):
        msg = {"role": "user", "content": [{"type": "text", "text": "看图"},
                                           {"type": "image_url", "image_url": {"url": ref}}]}
        return rw.build_message_view(msg, 0, source_dir=source)["content"]["html"]

    inside = render("pic.png")
    assert "data:image/png;base64," in inside and "看图" in inside

    for ref in ("../secret.png", str(outside), "..\\secret.png"):
        html = render(ref)
        assert 'src="' not in html and "data:image" not in html, ref
        assert "已拒绝" in html

    remote = render("https://evil.example/p.png")
    assert "<img" not in remote and 'src="' not in remote and "已阻止" in remote

    (source / "fake.png").write_bytes(b"definitely not an image")
    assert 'src="' not in render("fake.png")

    (source / "big.png").write_bytes(PNG + b"\x00" * 64)
    original = rw.MAX_IMAGE_BYTES
    try:
        rw.MAX_IMAGE_BYTES = 4
        html = render("big.png")
        assert 'src="' not in html and "超过" in html
    finally:
        rw.MAX_IMAGE_BYTES = original

    data, note = rw.resolve_local_image("pic.png", source)
    assert data and data.startswith("data:image/png;base64,") and note == ""


def test_local_image_rejects_links(monkeypatch, tmp_area):
    source = tmp_area / "ws"
    source.mkdir()
    (source / "pic.png").write_bytes(PNG)
    monkeypatch.setattr(rw.WS, "is_linked", lambda path: True)
    data, note = rw.resolve_local_image("pic.png", source)
    assert data is None and "链接" in note


# ── 工具元数据与长内容：完整展示，绝不 1200 字符截断 ────────────────────────
def test_tool_metadata_and_long_content_are_never_truncated():
    tail = "尾部标记-完整保留"
    long_text = "开头" + "x" * 5000 + tail
    view = rw.build_message_view({"role": "tool", "content": long_text,
                                  "toolCallId": "call-1", "isError": True}, 3)
    assert tail in view["content"]["html"]
    assert "截断" not in view["content"]["html"]
    assert "call-1" in view["extra_html"] and "isError" in view["extra_html"]

    args = {"blob": "y" * 4000, "path": "a/b/c.txt"}
    assistant = {"role": "assistant", "content": "ok",
                 "toolCalls": [{"name": "read_file", "input": args}]}
    extra = rw.build_message_view(assistant, 0)["extra_html"]
    assert "read_file" in extra and "y" * 4000 in extra
    assert "截断" not in extra


# ── 记录视图：多模态结构保留、样本图片挂载、历史纯文本降级 ──────────────────
def test_build_record_view_preserves_sample_metadata(tmp_area):
    source = tmp_area / "ws"
    source.mkdir()
    (source / "a.png").write_bytes(PNG)
    record = {"record_id": 7, "sample_id": "s1", "sample_hash": "h1", "decision": None,
              "reason": None, "meta": "images=1", "payload": _payload("s1", images=["a.png"])}
    view, warning = rw.build_record_view(record, source_dir=source)
    assert warning is None
    assert [m["index"] for m in view["messages"]] == [0, 1, 2]
    assert view["messages"][1]["role"] == "user"
    assert view["messages"][1]["content"]["editable"] is True
    assert view["messages"][2]["reasoning_content"]["editable"] is True
    assert view["messages"][1]["reasoning_content"] is None
    assert "原始思考" in view["messages"][2]["reasoning_content"]["html"]
    assert "data:image/png;base64," in view["messages"][0]["extra_html"]  # 样本图片卡片
    assert "工具定义" in view["messages"][0]["extra_html"]  # 工具 schema 完整转义
    assert view["decision"] is None and view["sample_hash"] == "h1"

    # 多模态 content 列表：文本渲染 Markdown，图片只读、绝不出现远程 src
    multimodal = {"role": "user", "content": [
        {"type": "text", "text": "看图 ![x](https://evil.example/x.png)"},
        {"type": "image_url", "image_url": {"url": "a.png"}},
    ]}
    field = rw.build_message_view(multimodal, 0, source_dir=source)["content"]
    assert field["editable"] is False
    assert "data:image/png;base64," in field["html"]
    assert "https://evil.example" not in field["html"]


def test_build_record_view_legacy_payloads():
    legacy = {"record_id": 2, "sample_id": "old", "sample_hash": "", "decision": None,
              "reason": None, "meta": "images=0",
              "payload": json.dumps([{"role": "user", "content": "q"},
                                     {"role": "assistant", "content": "a"}])}
    view, warning = rw.build_record_view(legacy)
    assert warning is None
    assert [m["role"] for m in view["messages"]] == ["user", "assistant"]

    # 历史图文记录缺图片元数据：不空白，完整展示只读 conversation
    text = "历史纯文本 第一行\n<img src=x onerror=alert(1)>\n最后一行"
    old_image = {**legacy, "meta": "images=2", "conversation": text}
    view2, warning2 = rw.build_record_view(old_image)
    assert warning2
    assert len(view2["messages"]) == 1
    message = view2["messages"][0]
    assert message["role"] == "legacy" and message["content"]["editable"] is False
    assert "最后一行" in message["content"]["html"]
    assert "<img" not in message["content"]["html"]  # 完整但转义

    plain = {**legacy, "payload": "只有纯文本，不是 JSON", "conversation": text}
    view3, warning3 = rw.build_record_view(plain)
    assert warning3 and "最后一行" in view3["messages"][0]["content"]["html"]


# ── 信封：固定字段与 scope ──────────────────────────────────────────────────
def test_envelope_shape_scope_and_state_isolation():
    assert rw.new_state()["initialized"] is False  # 深链只在首次初始化时读取
    state = rw.new_state()
    state.update(query="ab", status="reviewed", offset=30)
    queue = {"items": [{"sample_id": "s1"}], "total": 1, "pending": 0, "reviewed": 1,
             "offset": 0, "limit": 30}
    env = rw.build_envelope(dataset=DATASET, username="alice", workspace="ws", state=state,
                            queue=queue, record=None, notice={"kind": "success", "text": "ok"},
                            ack={"id": "e1", "ok": True, "action": "save"}, suggestion=None)
    assert set(env) == {"scope", "workspace", "identity", "queue", "query", "status",
                        "record", "notice", "ack", "suggestion"}
    assert env["scope"] == f"ws:{DATASET}:alice"
    assert env["identity"] == "alice" and env["status"] == "reviewed"
    assert env["queue"]["limit"] == 30 and env["record"] is None

    session = {}
    alice = rw.session_state(DATASET, "alice", "ws", session=session)
    bob = rw.session_state(DATASET, "bob", "ws", session=session)
    assert alice is not bob
    assert set(session) == {rw.state_key(DATASET, "alice", "ws"), rw.state_key(DATASET, "bob", "ws")}


# ── 共享主题令牌（追加在组件 CSS 之后，accent 保持组件亮色） ────────────────
def test_shared_dark_tokens_appended_after_component_css():
    from lib.console_theme import DARK_TOKENS

    base = ":host { --rw-bg:#11151c; --rw-accent:#9dbbff; }"
    css = rw.compose_css(base)
    assert css.startswith(base)  # 组件自有色板在前，共享令牌在后覆盖
    assert f"--rw-bg: {DARK_TOKENS['bg']};" in css
    assert f"--rw-panel: {DARK_TOKENS['layer1']};" in css
    assert f"--rw-raised: {DARK_TOKENS['layer2']};" in css
    assert f"--rw-text: {DARK_TOKENS['text']};" in css
    assert f"--rw-muted: {DARK_TOKENS['text2']};" in css
    assert f"--rw-line: {DARK_TOKENS['border2']};" in css
    assert "--rw-accent" not in css[len(base):]  # 不覆盖亮色 accent


# ── 视图解析：数据集未授权时向上抛，webapp 才能切换身份 ─────────────────────
def test_view_resolution_propagates_permission_error():
    backend = _backend_three()

    def deny(*args, **kwargs):
        raise PermissionError("Dataset access denied")

    backend.queue_page = deny
    with pytest.raises(PermissionError):
        rw._resolve_view(rw.new_state(), DATASET, "alice", backend, None)


# ── 事件：save（幂等 request_id / 失败保留记录） ────────────────────────────
def test_save_event_uses_request_id_and_dedups_by_id():
    backend = _backend_three()
    state = rw.new_state()
    state["current"] = "s1"
    event = {"id": "ev-1", "action": "save", "record_id": 1, "sample_hash": "h1",
             "index": 2, "field": "content", "text": "新正文"}
    assert rw.apply_event(state, event, dataset=DATASET, username="alice", backend=backend) is True
    calls = [call for call in backend.calls if call[0] == "revise_field"]
    assert calls == [("revise_field", 1, "h1", 2, "content", "新正文", "ev-1")]
    assert state["current"] == "s1-r1"
    assert state["ack"] == {"id": "ev-1", "ok": True, "action": "save"}
    assert state["notice"]["kind"] == "success"

    assert rw.apply_event(state, event, dataset=DATASET, username="alice", backend=backend) is False
    assert len([call for call in backend.calls if call[0] == "revise_field"]) == 1


def test_save_error_preserves_record_and_reports_failure():
    backend = _backend_three()
    backend.raise_on["revise_field"] = ValueError("Stale content hash: reload the record before revising")
    state = rw.new_state()
    state["current"] = "s1"
    event = {"id": "ev-2", "action": "save", "record_id": 1, "sample_hash": "h1",
             "index": 2, "field": "content", "text": "x"}
    assert rw.apply_event(state, event, dataset=DATASET, username="alice", backend=backend) is True
    assert state["current"] == "s1"
    assert state["ack"] == {"id": "ev-2", "ok": False, "action": "save"}
    assert state["notice"]["kind"] == "error" and "Stale" in state["notice"]["text"]

    # 事件 record_id 与当前记录不一致 → 拒绝，不调用修订接口
    state2 = rw.new_state()
    state2["current"] = "s1"
    before = len([call for call in backend.calls if call[0] == "revise_field"])
    rw.apply_event(state2, {"id": "ev-3", "action": "save", "record_id": 99, "index": 2,
                            "field": "content", "text": "x"},
                   dataset=DATASET, username="alice", backend=backend)
    assert state2["ack"]["ok"] is False
    assert len([call for call in backend.calls if call[0] == "revise_field"]) == before


# ── 事件：submit（哈希绑定、自动下一条、回卷） ──────────────────────────────
def test_submit_event_binds_hash_and_auto_advances_pending():
    backend = _backend_three()
    state = rw.new_state()
    state["current"] = "s1"
    assert rw.apply_event(state, {"id": "ev-4", "action": "submit", "decision": "keep",
                                  "reason": "  内容准确  ", "record_id": 1, "sample_hash": "h1"},
                          dataset=DATASET, username="alice", backend=backend) is True
    rows = [call for call in backend.calls if call[0] == "submit"][0][1]
    assert rows == [{"record_id": 1, "decision": "keep", "reason": "内容准确",
                     "model": "human", "sample_hash": "h1"}]
    assert backend.pending == ["s2", "s3"]
    assert state["current"] == "s2" and state["offset"] == 0
    assert state["ack"] == {"id": "ev-4", "ok": True, "action": "submit"}

    # 最后一条提交后回卷到剩余的第一条
    backend2 = _backend_three()
    state2 = rw.new_state()
    state2["current"] = "s3"
    rw.apply_event(state2, {"id": "ev-5", "action": "submit", "decision": "reject",
                            "reason": "事实错误", "record_id": 3, "sample_hash": "h1"},
                   dataset=DATASET, username="alice", backend=backend2)
    assert backend2.pending == ["s1", "s2"] and state2["current"] == "s1"


def test_submit_zero_count_reports_prior_decision():
    backend = _backend_three()
    backend.submit_count_override = 0
    backend.records["s1"]["decision"] = "keep"
    state = rw.new_state()
    state["current"] = "s1"
    rw.apply_event(state, {"id": "ev-prior", "action": "submit", "decision": "reject",
                           "reason": "改判", "record_id": 1, "sample_hash": "h1"},
                   dataset=DATASET, username="alice", backend=backend)
    assert state["ack"]["ok"] is True
    assert "原判定：保留" in state["notice"]["text"]
    assert "驳回" not in state["notice"]["text"]


def test_submit_requires_reason_hash_and_matching_record():
    backend = _backend_three()
    state = rw.new_state()
    state["current"] = "s1"
    base = {"action": "submit", "record_id": 1, "sample_hash": "h1"}

    assert rw.apply_event(state, {"id": "ev-6", **base, "decision": "keep", "reason": "  "},
                          dataset=DATASET, username="alice", backend=backend) is True
    assert state["ack"]["ok"] is False and state["current"] == "s1"

    assert rw.apply_event(state, {"id": "ev-7", **base, "decision": "bogus", "reason": "x"},
                          dataset=DATASET, username="alice", backend=backend) is True
    assert state["ack"]["ok"] is False

    # 缺少哈希/记录号 → 拒绝，绝不回退成"用当前哈希顶替"
    for offset, bad in enumerate(({"record_id": 1}, {"sample_hash": "h1"},
                                  {"record_id": 1, "sample_hash": "old"})):
        assert rw.apply_event(state, {"id": f"ev-bad-{offset}", "action": "submit",
                                      "decision": "keep", "reason": "x", **bad},
                              dataset=DATASET, username="alice", backend=backend) is True
        assert state["ack"]["ok"] is False
    assert not [call for call in backend.calls if call[0] == "submit"]


# ── 事件：skip（翻页/回卷）、query、select、navigate ────────────────────────
def test_skip_advances_pages_and_wraps(monkeypatch):
    monkeypatch.setattr(rw, "PAGE_LIMIT", 2)
    backend = _backend_three()
    state = rw.new_state()
    state["current"] = "s2"
    rw.apply_event(state, {"id": "sk-1", "action": "skip"}, dataset=DATASET,
                   username="alice", backend=backend)
    assert state["current"] == "s3" and state["offset"] == 2

    rw.apply_event(state, {"id": "sk-2", "action": "skip"}, dataset=DATASET,
                   username="alice", backend=backend)
    assert state["current"] == "s1" and state["offset"] == 0  # 回卷第一页


def test_current_position_exact_helper_and_unknown_fallback():
    backend = _backend_three(pending=[f"s{i}" for i in range(1, 8)])
    state = rw.new_state()
    state["current"] = "s6"
    state["offset"] = 4
    assert rw._current_position(state, DATASET, "alice", backend) == 5  # 精确位置，非页游标

    class NoHelper:
        def __init__(self, backend):
            self._backend = backend

        def __getattr__(self, name):
            if name == "queue_position":
                raise AttributeError(name)
            return getattr(self._backend, name)

    # 旧后端无 queue_position / 记录不在 pending → -1（位置未知，不泄漏 offset）
    assert rw._current_position(state, DATASET, "alice", NoHelper(backend)) == -1
    state["current"] = "reviewed-sample"
    assert rw._current_position(state, DATASET, "alice", backend) == -1


def test_skip_reviewed_record_starts_first_pending():
    records = {sid: {"record_id": index + 1, "sample_id": sid, "sample_hash": "h1",
                     "decision": None, "reason": None, "meta": "m", "instruction": "q",
                     "payload": _payload(sid)} for index, sid in enumerate(("s1", "s2", "s3"))}
    records["r1"] = {"record_id": 9, "sample_id": "r1", "sample_hash": "h1",
                     "decision": "keep", "reason": "已审", "meta": "m", "instruction": "q",
                     "payload": _payload("r1")}
    backend = FakeBackend(records, pending=["s1", "s2", "s3"], reviewed=["r1"])
    state = rw.new_state()
    state["current"] = "r1"
    state["offset"] = 0
    state["status"] = "reviewed"
    rw.apply_event(state, {"id": "sk-reviewed", "action": "skip"}, dataset=DATASET,
                   username="alice", backend=backend)
    assert state["current"] == "s1"  # 不是从 offset 推进到第二条
    assert state["offset"] == 0 and state["status"] == "pending"


def test_resolve_view_fallback_clears_suggestion():
    backend = _backend_three()
    backend.records["gone"] = {"record_id": 99, "sample_id": "gone", "sample_hash": "h1",
                               "decision": None, "reason": None, "meta": "m",
                               "instruction": "q", "payload": _payload("gone")}
    backend.missing.add("gone")
    state = rw.new_state()
    state["current"] = "gone"
    state["suggestion"] = {"index": 2, "field": "content", "text": "旧建议", "html": ""}
    queue, record, warning = rw._resolve_view(state, DATASET, "alice", backend, None)
    assert state["current"] == "s1" and record["sample_id"] == "s1"
    assert state["suggestion"] is None  # 记录已变，建议立即清空
    assert warning

    empty = FakeBackend({}, pending=[])
    state2 = rw.new_state()
    state2["current"] = "gone"
    state2["suggestion"] = {"index": 2, "field": "content", "text": "旧建议", "html": ""}
    _, record2, _ = rw._resolve_view(state2, DATASET, "alice", empty, None)
    assert record2 is None and state2["current"] is None and state2["suggestion"] is None


def test_skip_and_submit_force_pending_status():
    backend = _backend_three()
    state = rw.new_state()
    state["current"] = "s1"
    state["status"] = "reviewed"
    rw.apply_event(state, {"id": "sk-status", "action": "skip"}, dataset=DATASET,
                   username="alice", backend=backend)
    assert state["status"] == "pending" and state["current"] == "s2"

    backend2 = _backend_three()
    state2 = rw.new_state()
    state2["current"] = "s1"
    state2["status"] = "all"
    rw.apply_event(state2, {"id": "sub-status", "action": "submit", "decision": "keep",
                            "reason": "ok", "record_id": 1, "sample_hash": "h1"},
                   dataset=DATASET, username="alice", backend=backend2)
    assert state2["status"] == "pending" and state2["current"] == "s2"
    assert state2["ack"]["ok"] is True


def test_query_error_rolls_back_state_and_suggestion():
    backend = _backend_three()
    backend.raise_on["queue_page"] = PermissionError("Dataset access denied")
    state = rw.new_state()
    state.update(query="keep", status="reviewed", offset=1, current="s2")
    state["suggestion"] = {"index": 2, "field": "content", "text": "建议", "html": ""}
    assert rw.apply_event(state, {"id": "q-fail", "action": "query", "query": "new",
                                  "status": "all", "offset": 0},
                          dataset=DATASET, username="alice", backend=backend) is True
    assert state["ack"] == {"id": "q-fail", "ok": False, "action": "query"}
    assert (state["query"], state["status"], state["offset"], state["current"]) == \
           ("keep", "reviewed", 1, "s2")
    assert state["suggestion"] is not None


def test_query_and_select_events():
    backend = _backend_three()
    state = rw.new_state()
    state["current"] = "s1"
    state["suggestion"] = {"index": 2, "field": "content", "text": "旧建议", "html": ""}
    rw.apply_event(state, {"id": "q-1", "action": "query", "query": "s2",
                           "status": "pending", "offset": 0},
                   dataset=DATASET, username="alice", backend=backend)
    assert state["query"] == "s2" and state["current"] == "s2" and state["suggestion"] is None

    rw.apply_event(state, {"id": "q-2", "action": "query", "query": "",
                           "status": "reviewed", "offset": 0},
                   dataset=DATASET, username="alice", backend=backend)
    assert state["status"] == "reviewed" and state["current"] is None

    state["suggestion"] = {"index": 2, "field": "content", "text": "建议", "html": ""}
    rw.apply_event(state, {"id": "s-1", "action": "select", "sample_id": "s3"},
                   dataset=DATASET, username="alice", backend=backend)
    assert state["current"] == "s3" and state["suggestion"] is None
    assert state["ack"]["ok"] is True

    rw.apply_event(state, {"id": "s-2", "action": "select", "sample_id": "missing"},
                   dataset=DATASET, username="alice", backend=backend)
    assert state["ack"]["ok"] is False and state["current"] == "s3"

    rw.apply_event(state, {"id": "q-3", "action": "query", "query": "x" * 201},
                   dataset=DATASET, username="alice", backend=backend)
    assert state["ack"]["ok"] is False and state["query"] == ""


def test_navigate_invokes_callback_and_validates_target():
    backend = _backend_three()
    state = rw.new_state()
    seen = []
    rw.apply_event(state, {"id": "n-1", "action": "navigate", "target": "settings"},
                   dataset=DATASET, username="alice", backend=backend,
                   on_navigate=seen.append)
    assert seen == ["settings"] and state["ack"] == {"id": "n-1", "ok": True, "action": "navigate"}

    rw.apply_event(state, {"id": "n-2", "action": "navigate", "target": "bogus"},
                   dataset=DATASET, username="alice", backend=backend, on_navigate=seen.append)
    assert seen == ["settings"] and state["ack"]["ok"] is False

    rw.apply_event(state, {"id": "un-1", "action": "explode"},
                   dataset=DATASET, username="alice", backend=backend)
    assert state["ack"]["ok"] is False and "未知操作" in state["notice"]["text"]


# ── 事件：ai（G0+G1 闸门、refine 后端、propose_field、建议安全 HTML） ───────
def test_ai_requires_g0_and_g1_and_never_touches_gates():
    backend = _backend_three()
    state = rw.new_state()
    state["current"] = "s1"
    gate = FakeGate({"G0": "pending", "G1": "approved"})
    calls = []
    ok = rw.apply_event(state, {"id": "ai-1", "action": "ai", "index": 2, "record_id": 1,
                                "sample_hash": "h1", "field": "content", "instruction": "压缩到两句"},
                        dataset=DATASET, username="alice", gate=gate, backend=backend,
                        load_client=lambda: calls.append("client") or (object(), "m"))
    assert ok is True
    assert state["ack"] == {"id": "ai-1", "ok": False, "action": "ai"}
    assert "G0" in state["notice"]["text"]
    assert calls == [] and gate.proposed == [] and gate.decided == []
    assert not [call for call in backend.calls if call[0] == "revise_field"]

    # G0/G1 都通过 → 调用 refine 角色后端与既有 propose_field，产出建议（未落库）
    gate2 = FakeGate({"G0": "approved", "G1": "approved"})
    state2 = rw.new_state()
    state2["current"] = "s1"
    captured = {}

    def fake_propose(messages, index, field, instruction, client):
        captured.update(index=index, field=field, instruction=instruction, client=client)
        edited = json.loads(json.dumps(messages))
        edited[index][field] = "远程图 ![x](https://evil.example/x.png) 结束"
        return edited

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(rw.editor, "propose_field", fake_propose)
        client = object()
        rw.apply_event(state2, {"id": "ai-2", "action": "ai", "index": 2, "record_id": 1,
                                "sample_hash": "h1", "field": "content", "instruction": "压缩"},
                       dataset=DATASET, username="alice", gate=gate2, backend=backend,
                       load_client=lambda: (client, "refine-model"))
    finally:
        monkeypatch.undo()
    assert captured["index"] == 2 and captured["field"] == "content"
    assert captured["client"] is client
    suggestion = state2["suggestion"]
    assert suggestion["text"].startswith("远程图") and suggestion["index"] == 2
    assert "<img" not in suggestion["html"] and "https://evil.example" not in suggestion["html"]
    assert state2["ack"] == {"id": "ai-2", "ok": True, "action": "ai"}
    assert state2["notice"]["kind"] == "success"
    assert not [call for call in backend.calls if call[0] == "revise_field"]  # 建议不落库


def test_ai_gate_read_failure_is_reported():
    backend = _backend_three()
    state = rw.new_state()
    state["current"] = "s1"
    rw.apply_event(state, {"id": "ai-3", "action": "ai", "index": 2, "field": "content",
                           "record_id": 1, "sample_hash": "h1", "instruction": "x"},
                   dataset=DATASET, username="alice", gate=FakeGate({"G1": "approved"}),
                   backend=backend, load_client=lambda: (object(), "m"))
    assert state["ack"]["ok"] is False and "G0" in state["notice"]["text"]


# ── 后端 helper：rc.queue_position 单条计数，深链接 > 每页上限也精确 ─────────
def test_rc_queue_position_exact_beyond_page(tmp_path, monkeypatch):
    from lib import review_center as rc

    monkeypatch.setattr(rc, "DB_PATH", tmp_path / "rc.db")
    rc.stop_thread()
    try:
        rc.init_db()
        rc.ensure_admin("k-admin")
        rc.add_records(DATASET, [{"sample_id": f"s{i:03d}", "instruction": "q",
                                  "conversation": "a", "meta": ""} for i in range(130)])
        items = rc.queue_page(DATASET, "admin", limit=200)["items"]
        rid = {item["sample_id"]: item["record_id"] for item in items}
        rc.submit(DATASET, "admin", [{"record_id": rid["s120"], "decision": "keep",
                                      "reason": "ok", "sample_hash": ""}])

        # pending 队列移除了 s120：s125 的精确位置（>100，页扫描路径会错）
        assert rc.queue_position(DATASET, "admin", sample_id="s125") == 124
        assert rc.queue_position(DATASET, "admin", status="reviewed", sample_id="s120") == 0
        assert rc.queue_position(DATASET, "admin", status="pending", sample_id="s120") is None
        assert rc.queue_position(DATASET, "admin", sample_id="missing") is None
        assert rc.queue_position(DATASET, "admin", status="all", sample_id="s129") == 129
        # 搜索过滤同样按 record id 计数（s121..s129 中 s125 排第 5）
        assert rc.queue_position(DATASET, "admin", query="s12", sample_id="s125") == 4
        with pytest.raises(ValueError):
            rc.queue_position(DATASET, "admin", sample_id="")
    finally:
        rc.stop_thread()
