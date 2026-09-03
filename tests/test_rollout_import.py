"""rollout 导入器离线测试（合成 fixture，复刻真实记录结构）。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "rollout_sample.jsonl"


def _load():
    from lib.adapters.rollout_import import iter_records

    return list(iter_records(FIXTURE))


def test_record_status():
    from lib.adapters.rollout_import import record_status

    recs = _load()
    assert [record_status(r) for r in recs] == ["ok", "ok", "ok", "error"]


def test_assistant_msg_cot_styles():
    from lib.adapters.rollout_import import assistant_msg_from_response

    rec2 = _load()[1]
    # separated（默认）：推理在 reasoning_content 字段，正文在 content
    sep = assistant_msg_from_response(rec2, "separated")
    assert sep["reasoning_content"] == "用户想查看目录，先列出文件。"
    assert sep["content"] == ""  # 纯工具调用轮无正文
    assert sep["toolCalls"][0]["name"] == "Bash"

    # tags：用配置的原生 token 包裹（默认空 token=前后直接拼接）
    tagged = assistant_msg_from_response(rec2, "tags", ("", ""))
    assert "先列出文件" in tagged["content"]

    # plain：合并为普通文本，无标记
    plain = assistant_msg_from_response(rec2, "plain")
    assert plain["content"] == "用户想查看目录，先列出文件。"

    # drop：丢弃推理
    dropped = assistant_msg_from_response(rec2, "drop")
    assert dropped["content"] == ""
    assert dropped["toolCalls"][0]["id"] == "call-1"

    # 无推理的轮次不产生 reasoning_content 字段
    rec1 = _load()[0]
    assert "reasoning_content" not in assistant_msg_from_response(rec1, "separated")


def test_record_to_sample_and_error_steps():
    from lib.adapters.rollout_import import record_to_sample

    rec3 = _load()[2]
    sample = record_to_sample(rec3, "separated")
    assert sample["type"] == "sft"
    assert sample["finish_reason"] == "stop"
    assert sample["error_tool_steps"] == 1  # isError 计入统计并保留在消息里（渲染/质检用）
    for m in sample["messages"]:
        assert "modelRef" not in m  # 内部字段被丢弃
    # 尾轮（separated 风格）：反思在 reasoning_content，正文只含正确结论
    final = sample["messages"][-1]
    assert "改用 pwd" in final["reasoning_content"]
    assert final["content"] == "当前目录为 F:\\work"
    # 历史里的工具结果保留（toolCallId 关联）；运行时事实信号保留
    tool_msgs = [m for m in sample["messages"] if m["role"] == "tool"]
    assert tool_msgs and "No such file" in tool_msgs[0]["content"]
    assert tool_msgs[0].get("isError") is True


def test_sample_id_deterministic():
    from lib.adapters.rollout_import import record_to_sample, sample_id

    rec1 = _load()[0]
    a = sample_id(record_to_sample(rec1)["messages"], "rollout")
    b = sample_id(record_to_sample(rec1)["messages"], "rollout")
    assert a == b and a.startswith("rollout-")


def test_record_id_style_independent():
    from lib.adapters.rollout_import import record_to_sample

    rec3 = _load()[2]
    # 同一记录换 CoT 风格 → 同一样本 id（manifest 跨风格零重复的前提）
    ids = {record_to_sample(rec3, style)["id"] for style in ("separated", "tags", "plain", "drop")}
    assert len(ids) == 1


def test_manifest_dedup(tmp_path):
    from lib.adapters.rollout_import import ManifestDedup

    manifest = tmp_path / "manifest.txt"
    dedup = ManifestDedup(manifest)
    dedup.add("a"); dedup.add("b"); dedup.add("a")
    assert dedup.new == 2 and dedup.hits == 1
    dedup.save()

    # 重新加载：跨运行零重复
    dedup2 = ManifestDedup(manifest)
    dedup2.add("a"); dedup2.add("c")
    assert dedup2.new == 1 and dedup2.hits == 1
    assert dedup2.seen("b")
