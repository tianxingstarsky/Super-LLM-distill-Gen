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
    r1 = assistant_msg_from_response(rec2, "r1")
    assert "<思考>" in r1["content"] and "先列出文件" in r1["content"]
    assert r1["toolCalls"][0]["name"] == "Bash"

    raw = assistant_msg_from_response(rec2, "raw")
    assert raw["content"] == ""  # 纯工具调用轮无正文
    assert raw["toolCalls"][0]["id"] == "call-1"

    rec1 = _load()[0]
    assert "<思考>" not in assistant_msg_from_response(rec1, "r1")["content"]


def test_record_to_sample_and_error_steps():
    from lib.adapters.rollout_import import record_to_sample

    rec3 = _load()[2]
    sample = record_to_sample(rec3, "r1")
    assert sample["type"] == "sft"
    assert sample["finish_reason"] == "stop"
    assert sample["error_tool_steps"] == 1  # isError 计入并剥离
    for m in sample["messages"]:
        assert "isError" not in m
        assert "modelRef" not in m  # 内部字段被丢弃
    # 尾轮：反思含"改用 pwd"，正文只含正确结论
    final = sample["messages"][-1]
    assert "改用 pwd" in final["content"] and "当前目录为" in final["content"]
    # 历史里的工具结果保留（toolCallId 关联）
    tool_msgs = [m for m in sample["messages"] if m["role"] == "tool"]
    assert tool_msgs and "No such file" in tool_msgs[0]["content"]


def test_sample_id_deterministic():
    from lib.adapters.rollout_import import record_to_sample, sample_id

    rec1 = _load()[0]
    a = sample_id(record_to_sample(rec1)["messages"], "rollout")
    b = sample_id(record_to_sample(rec1)["messages"], "rollout")
    assert a == b and a.startswith("rollout-")


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
