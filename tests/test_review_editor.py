"""审核编辑器后端测试：完整样本 payload、无损修订、scope 强制、原子修订号分配。

不触碰真实 DB/真实样本：conftest 的 isolated_review_store 已把 review_center.DB_PATH
指到每个测试自己的 tmp_path，这里只使用临时库。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
from copy import deepcopy
import json
import time

import pytest


def _sample():
    """带图片、工具、多字段元数据的完整样本。"""
    return {
        "id": "s1",
        "source": "rollout",
        "type": "sft",
        "model": "m1",
        "finish_reason": "tool-calls",
        "error_tool_steps": 1,
        "images": ["F:/imgs/a.png"],
        "tools": [{"name": "Bash", "description": "run shell"}],
        "caption": "一张示意图",
        "session_id": "sess-1",
        "usage": {"inputTokens": 3, "outputTokens": 2},
        "messages": [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "先查目录", "reasoning_content": "想一下",
             "toolCalls": [{"id": "c1", "name": "Bash", "input": {"command": "ls"}}]},
            {"role": "tool", "content": "no such file", "toolCallId": "c1", "isError": True},
        ],
    }


def _multimodal_sample():
    return {
        "id": "mm1",
        "images": ["F:/imgs/b.png"],
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "看这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]},
            {"role": "assistant", "content": "图里是一个方块"},
        ],
    }


def _push(dataset, sample):
    from lib.review import build_records
    from lib import review_center as rc
    rc.ensure_admin("k-admin")
    rc.add_records(dataset, build_records([sample], {}))


def _row(dataset, sample_id):
    from lib import review_center as rc
    for row in rc.pending(dataset, "admin", 100):
        if row["sample_id"] == sample_id:
            return row
    raise AssertionError(f"record not found: {sample_id}")


class _Fake:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, *args, **kwargs):
        return self.payload


# ── payload：完整样本 + 旧记录兼容 ────────────────────────────────────────────
def test_build_records_payload_is_full_sample():
    from lib.review import build_records
    from lib.quality import sample_hash

    sample = _sample()
    rec = build_records([sample], {})[0]
    assert json.loads(rec["payload"]) == sample          # 全字段无损（messages/images/tools/…）
    assert rec["sample_hash"] == sample_hash(sample)
    assert rec["meta"].endswith("images=1")              # meta 保留可读图片计数


def test_unpack_record_roundtrip_and_legacy_compat():
    from lib import review_editor as editor

    sample = _sample()
    record = {"sample_id": "s1", "payload": editor.pack_sample(sample), "meta": "images=1"}
    out = editor.unpack_record(record)
    assert out["images"] == ["F:/imgs/a.png"]
    assert out["tools"] == sample["tools"]
    assert out["caption"] == "一张示意图"
    assert out["usage"] == sample["usage"]
    assert out["messages"] == sample["messages"]

    # 旧版纯 messages 数组 payload：默认保持可读
    legacy = {"sample_id": "old-1", "meta": "model=m finish=stop 错误步骤=0 images=0",
              "payload": json.dumps(sample["messages"], ensure_ascii=False)}
    assert editor.unpack_record(legacy) == {"id": "old-1", "messages": sample["messages"]}

    # 旧版 meta 标记含图：payload 无图片信息 → 明确拒绝，不静默丢图
    with pytest.raises(ValueError, match="不会丢图"):
        editor.unpack_record({**legacy, "meta": "images=2"})
    # 更老的纯文本记录（无 payload）→ 明确报错
    with pytest.raises(ValueError):
        editor.unpack_record({"sample_id": "x", "payload": ""})
    # 非 dict/非 list 的 payload → 明确报错
    with pytest.raises(ValueError):
        editor.unpack_record({"sample_id": "x", "payload": '"plain text"'})


# ── 人工修订校验：深拷贝 + 元数据不可动 + 多模态不可压成文本 ──────────────────
def test_validate_edits_preserves_metadata_and_deep_copies():
    from lib import review_editor as editor

    original = _sample()
    edited = deepcopy(original["messages"])
    edited[2]["content"] = "查目录（人工修订）"
    edited[2]["reasoning_content"] = "想一下（修订）"
    edited[3]["content"] = "错误：文件不存在"
    result = editor.validate_edits(original, edited)

    assert result is not original and result["messages"] is not original["messages"]
    assert result["images"] == original["images"]
    assert result["tools"] == original["tools"]
    assert result["usage"] == original["usage"]
    assert result["caption"] == original["caption"]
    assert result["messages"][1] == original["messages"][1]                     # 非目标消息原样
    assert result["messages"][2]["toolCalls"] == original["messages"][2]["toolCalls"]
    assert result["messages"][3]["isError"] is True
    assert result["messages"][2]["content"] == "查目录（人工修订）"
    assert result["messages"][2]["reasoning_content"] == "想一下（修订）"

    # 深拷贝：改结果绝不改原样本
    result["images"].append("F:/imgs/x.png")
    result["messages"][2]["toolCalls"][0]["input"]["command"] = "rm -rf"
    assert original["images"] == ["F:/imgs/a.png"]
    assert original["messages"][2]["toolCalls"][0]["input"]["command"] == "ls"


def test_validate_edits_rejects_role_count_and_metadata_tampering():
    from lib import review_editor as editor

    original = _sample()
    msgs = original["messages"]
    with pytest.raises(ValueError, match="消息数量不一致"):
        editor.validate_edits(original, msgs[:-1])
    with pytest.raises(ValueError, match="消息数量不一致"):
        editor.validate_edits(original, msgs + [{"role": "user", "content": "多"}])

    changed_role = deepcopy(msgs)
    changed_role[1]["role"] = "assistant"
    with pytest.raises(ValueError, match="不允许增删消息或改变角色"):
        editor.validate_edits(original, changed_role)

    tampered = deepcopy(msgs)
    tampered[2]["toolCalls"][0]["name"] = "Other"
    with pytest.raises(ValueError, match="不能修改消息元数据：toolCalls"):
        editor.validate_edits(original, tampered)

    tampered = deepcopy(msgs)
    tampered[3]["isError"] = False
    with pytest.raises(ValueError, match="不能修改消息元数据：isError"):
        editor.validate_edits(original, tampered)

    tampered = deepcopy(msgs)
    tampered[0]["extra"] = "sneaky"
    with pytest.raises(ValueError, match="不能修改消息元数据：extra"):
        editor.validate_edits(original, tampered)

    # reasoning_content 仅 assistant 可改；user 上加/改同样属于元数据越权
    tampered = deepcopy(msgs)
    tampered[1]["reasoning_content"] = "偷偷加思考"
    with pytest.raises(ValueError, match="不能修改消息元数据：reasoning_content"):
        editor.validate_edits(original, tampered)

    # 非字符串正文（int / dict）→ 明确报错
    tampered = deepcopy(msgs)
    tampered[2]["content"] = 123
    with pytest.raises(ValueError, match="必须是字符串"):
        editor.validate_edits(original, tampered)


def test_validate_edits_multimodal_list_never_silently_dropped():
    from lib import review_editor as editor

    original = _multimodal_sample()
    # 原结构不动 → 通过
    same = editor.validate_edits(original, original["messages"])
    assert same["messages"][0]["content"] == original["messages"][0]["content"]
    assert same["images"] == ["F:/imgs/b.png"]

    # 结构化 parts 被替换成文本（编辑组件/AI 常见的压平）→ 明确报错，绝不丢图
    broken = deepcopy(original["messages"])
    broken[0]["content"] = "看这张图"
    with pytest.raises(ValueError, match="多模态|图片"):
        editor.validate_edits(original, broken)

    # 反向：纯文本改成结构化列表同样报错（类型被换）
    broken = deepcopy(original["messages"])
    broken[1]["content"] = [{"type": "text", "text": "被替换"}]
    with pytest.raises(ValueError, match="多模态|图片"):
        editor.validate_edits(original, broken)

    # 结构化列表内部被改写（文本 or 结构变化）同样不允许整体替换
    broken = deepcopy(original["messages"])
    broken[0]["content"][0]["text"] = "改标题"
    with pytest.raises(ValueError, match="多模态|图片"):
        editor.validate_edits(original, broken)


# ── 修订落库：新版本、元数据保留、旧记录可读 ──────────────────────────────────
def test_revise_sample_preserves_images_tools_metadata():
    from lib import review_center as rc
    from lib import review_editor as editor
    from lib.review import revise_sample

    dataset = "rollout_review"
    sample = _sample()
    _push(dataset, sample)
    row = _row(dataset, "s1")

    edited = deepcopy(editor.unpack_record(row)["messages"])
    edited[2]["content"] = "先查目录（人工修订）"
    new_id = revise_sample(dataset, row, edited, reviewer="tester")
    assert new_id == "s1-r1"

    revised = editor.unpack_record(_row(dataset, new_id))
    assert revised["images"] == sample["images"]
    assert revised["tools"] == sample["tools"]
    assert revised["caption"] == sample["caption"]
    assert revised["session_id"] == sample["session_id"]
    assert revised["usage"] == sample["usage"]
    assert revised["messages"][2]["toolCalls"] == sample["messages"][2]["toolCalls"]
    assert revised["messages"][3]["isError"] is True
    assert revised["messages"][3]["toolCallId"] == "c1"
    assert revised["messages"][2]["content"] == "先查目录（人工修订）"
    assert revised["messages"][1]["content"] == "你好"
    assert revised["source"] == "human-edit" and revised["model"] == "human-edit"

    new_row = _row(dataset, new_id)
    assert new_row["sample_hash"] != row["sample_hash"]
    assert "修订自 s1" in new_row["meta"]

    # 序号递增；原记录仍在
    assert revise_sample(dataset, row, edited, reviewer="tester") == "s1-r2"
    assert {"s1", "s1-r1", "s1-r2"} <= {r["sample_id"] for r in rc.pending(dataset, "admin", 100)}


def test_revise_sample_rejects_invalid_edits_before_any_write():
    from lib import review_center as rc
    from lib import review_editor as editor
    from lib.review import revise_sample

    dataset = "rollout_review"
    _push(dataset, _sample())
    row = _row(dataset, "s1")
    edited = deepcopy(editor.unpack_record(row)["messages"])
    edited[2]["toolCalls"][0]["name"] = "Other"
    with pytest.raises(ValueError, match="不能修改消息元数据"):
        revise_sample(dataset, row, edited, reviewer="tester")
    assert not [r for r in rc.pending(dataset, "admin", 100) if r["sample_id"].startswith("s1-r")]


def test_revise_sample_legacy_records():
    from lib import review_center as rc
    from lib.review import revise_sample

    dataset = "rollout_review"
    rc.ensure_admin("k-admin")
    plain = [{"role": "user", "content": "问"}, {"role": "assistant", "content": "答"}]
    rc.add_records(dataset, [
        {"sample_id": "legacy-plain", "sample_hash": "", "meta": "images=0",
         "payload": json.dumps(plain, ensure_ascii=False)},
        {"sample_id": "legacy-image", "sample_hash": "", "meta": "images=1",
         "payload": json.dumps(plain, ensure_ascii=False)},
    ])

    # 纯文本历史记录仍可修订（默认可读路径）
    edited = deepcopy(plain)
    edited[1]["content"] = "答（修订）"
    assert revise_sample(dataset, _row(dataset, "legacy-plain"), edited, "tester") == "legacy-plain-r1"
    new_payload = json.loads(_row(dataset, "legacy-plain-r1")["payload"])
    assert new_payload["messages"][1]["content"] == "答（修订）"

    # 含图历史记录 payload 无图 → 明确拒绝，且不落任何修订
    with pytest.raises(ValueError, match="不会丢图"):
        revise_sample(dataset, _row(dataset, "legacy-image"), edited, "tester")
    assert not [r for r in rc.pending(dataset, "admin", 100) if r["sample_id"].startswith("legacy-image-r")]


# ── 并发修订号：同事务分配 + 不覆盖 ──────────────────────────────────────────
def test_concurrent_revision_allocation_never_overwrites(monkeypatch):
    from lib import review_center as rc
    from lib.review import build_records

    dataset = "rollout_review"
    _push(dataset, _sample())
    rc.init_db()
    # 去掉进程内 RLock，让并发真正落到 SQLite BEGIN IMMEDIATE 事务层（验证原子性）
    monkeypatch.setattr(rc, "_lock", contextlib.nullcontext())

    def build(new_id):
        time.sleep(0.05)  # 放大持锁窗口：并发者必须在事务外等待
        return build_records([{**_sample(), "id": new_id}], {})[0]

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(lambda _: rc.add_revision(dataset, "s1", build), range(6)))
    assert sorted(ids) == [f"s1-r{i}" for i in range(1, 7)]

    rows = {r["sample_id"]: r for r in rc.pending(dataset, "admin", 100)}
    for sample_id in ids:
        assert sample_id in rows                                        # 每条都真实落库
        assert json.loads(rows[sample_id]["payload"])["id"] == sample_id  # 无同 id 覆盖
    assert "s1" in rows


def test_add_revision_skips_occupied_suffix_without_upsert():
    from lib import review_center as rc
    from lib.review import build_records

    dataset = "rollout_review"
    _push(dataset, _sample())
    rc.add_records(dataset, [{"sample_id": "s1-r1", "meta": "occupied", "payload": "[]"}])
    new_id = rc.add_revision(dataset, "s1", lambda nid: build_records([{**_sample(), "id": nid}], {})[0])
    assert new_id == "s1-r2"
    assert _row(dataset, "s1-r1")["meta"] == "occupied"                 # 已有序号内容未被覆盖


# ── AI 临时修订：scope 服务端强制 + 多模态 fail clear ────────────────────────
def test_propose_revision_scope_only_touches_own_role():
    from lib.review import propose_revision

    base = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "答案", "reasoning_content": "思考"},
    ]
    fake = _Fake(json.dumps({"messages": [
        {"role": "system", "content": "系统被改"},
        {"role": "user", "content": "问题被改"},
        {"role": "assistant", "content": "答案被改", "reasoning_content": "思考被改"},
    ]}, ensure_ascii=False))

    out = propose_revision(base, "压缩", "assistant", fake)
    assert [m["content"] for m in out] == ["系统", "问题", "答案被改"]        # 只动 assistant content
    assert out[2]["reasoning_content"] == "思考"                            # 思考不在 scope 内

    out = propose_revision(base, "重写思考", "thinking", fake)
    assert [m["content"] for m in out] == ["系统", "问题", "答案"]          # 正文不动
    assert out[2]["reasoning_content"] == "思考被改"

    out = propose_revision(base, "全改", "all", fake)
    assert [m["content"] for m in out] == ["系统被改", "问题被改", "答案被改"]
    assert out[2]["reasoning_content"] == "思考被改"

    # 输入不被就地修改
    assert base[2]["content"] == "答案" and base[2]["reasoning_content"] == "思考"


def test_propose_revision_multimodal_and_type_fail_clear():
    from lib.review import propose_revision

    base = _multimodal_sample()["messages"]
    flat = json.dumps({"messages": [
        {"role": "user", "content": "看这张图"},
        {"role": "assistant", "content": "答"},
    ]}, ensure_ascii=False)
    with pytest.raises(ValueError, match="多模态|图片"):
        propose_revision(base, "改用户", "all", _Fake(flat))
    # scope=assistant 不碰 user 的多模态结构 → 允许，且结构原样保留
    out = propose_revision(base, "改回答", "assistant", _Fake(json.dumps({"messages": [
        {"role": "user", "content": "看这张图"},
        {"role": "assistant", "content": "答（改）"},
    ]}, ensure_ascii=False)))
    assert out[0]["content"] == base[0]["content"]
    assert out[1]["content"] == "答（改）"

    non_string = json.dumps({"messages": [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": 123},
    ]}, ensure_ascii=False)
    text_base = [{"role": "user", "content": "问题"}, {"role": "assistant", "content": "答"}]
    with pytest.raises(ValueError, match="必须是字符串"):
        propose_revision(text_base, "改", "all", _Fake(non_string))


def test_plain_conversation_marks_structured_parts_without_base64():
    from lib.review import build_records

    rec = build_records([_multimodal_sample()], {})[0]
    assert "base64" not in rec["conversation"]
    assert "image_url" in rec["conversation"]      # 结构部分显式标注而非泄漏数据
    assert rec["instruction"].startswith("看这张图")


def test_propose_field_rejects_multimodal_target():
    from lib.review_editor import propose_field

    messages = _multimodal_sample()["messages"]
    with pytest.raises(ValueError, match="需要文本内容"):
        propose_field(messages, 0, "content", "改", _Fake(json.dumps({"text": "x"})))


# ── _merge_text_field：结构化/数值 base 一律拒绝扁平化为文本 ───────────────────
def test_merge_text_field_refuses_structured_or_numeric_base_to_text():
    from lib import review_editor as editor

    # dict base → 文本：明确拒绝，target 绝不被部分写入
    target = {"content": {"text": "结构化", "parts": [1]}}
    src = {"content": {"text": "结构化", "parts": [1]}}
    with pytest.raises(ValueError, match="原值不是文本"):
        editor._merge_text_field(target, src, {"content": "结构化"}, "content")
    assert target == {"content": {"text": "结构化", "parts": [1]}}

    # int / float / bool base → 文本：同样拒绝
    for base in (123, 1.5, True):
        target = {"content": base}
        with pytest.raises(ValueError, match="原值不是文本"):
            editor._merge_text_field(target, {"content": base}, {"content": "text"}, "content")
        assert target == {"content": base}

    # 数值 base 改成另一个数值（修订不是字符串）也按「原值不是文本」拒绝
    target = {"content": 7}
    with pytest.raises(ValueError, match="原值不是文本"):
        editor._merge_text_field(target, {"content": 7}, {"content": 8}, "content")
    assert target == {"content": 7}

    # 未改动的结构化/数值 base（dst 缺字段或同值）→ 无损透传
    for base in ({"a": 1}, 123, [{"type": "text", "text": "x"}]):
        target = {"content": base}
        editor._merge_text_field(target, {"content": base}, {}, "content")
        assert target["content"] == base
        editor._merge_text_field(target, {"content": base}, {"content": deepcopy(base)}, "content")
        assert target["content"] == base

    # 边界：base 缺失 → 允许新增文本；base=null → 允许填文本（都不属于结构化/数值）
    target = {}
    editor._merge_text_field(target, {}, {"content": "new"}, "content")
    assert target == {"content": "new"}
    target = {"content": None}
    editor._merge_text_field(target, {"content": None}, {"content": "filled"}, "content")
    assert target == {"content": "filled"}

    # 文本 base → 非字符串修订：走另一条明确报错，target 不变
    target = {"content": "a"}
    with pytest.raises(ValueError, match="必须是字符串"):
        editor._merge_text_field(target, {"content": "a"}, {"content": 5}, "content")
    assert target == {"content": "a"}


def test_merge_text_field_structured_base_through_validate_and_scope_paths():
    from lib import review_editor as editor

    structured = {"id": "d1", "messages": [
        {"role": "user", "content": {"text": "结构化问题", "meta": {"lang": "zh"}}},
        {"role": "assistant", "content": 42},
        {"role": "assistant", "content": "答"},
    ]}

    # 未改动：dict / 数值 content 全量保留（深拷贝不得压平或转字符串）
    same = editor.validate_edits(structured, deepcopy(structured["messages"]))
    assert same["messages"][0]["content"] == {"text": "结构化问题", "meta": {"lang": "zh"}}
    assert same["messages"][1]["content"] == 42

    # dict content 被压成字符串（编辑组件/AI 常见的扁平化）→ 明确拒绝
    broken = deepcopy(structured["messages"])
    broken[0]["content"] = "结构化问题"
    with pytest.raises(ValueError, match="原值不是文本"):
        editor.validate_edits(structured, broken)

    # 数值 content 被替换为文本 → 明确拒绝
    broken = deepcopy(structured["messages"])
    broken[1]["content"] = "42"
    with pytest.raises(ValueError, match="原值不是文本"):
        editor.validate_edits(structured, broken)

    # AI scope 路径（merge_scoped_fields）同样拒绝，未改动时结构无损
    src = {"role": "user", "content": {"text": "结构化问题"}}
    with pytest.raises(ValueError, match="原值不是文本"):
        editor.merge_scoped_fields(src, {"role": "user", "content": "结构化问题"}, ["content"])
    kept = editor.merge_scoped_fields(src, {"role": "user", "content": {"text": "结构化问题"}}, ["content"])
    assert kept["content"] == {"text": "结构化问题"}
    with pytest.raises(ValueError, match="原值不是文本"):
        editor.merge_scoped_fields({"role": "assistant", "content": 42},
                                   {"role": "assistant", "content": "42"}, ["content"])
