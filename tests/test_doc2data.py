"""doc2data 离线测试（fake client 脚本化全链路：生成→校验→去重→驳回路径）。"""
from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "doc_sample.md"


class FakeClient:
    """按提示词脚本化：qa_gen 给 2 对（一条含幻觉信息）、ground_check 判定。"""

    def __init__(self, ground_keep: bool = True):
        self.ground_keep = ground_keep
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        content = messages[0]["content"]
        if "事实依据" not in content and "校验规则" not in content:
            # document.qa_gen（含"文档片段"与"生成要求"）
            return json.dumps({"qa": [
                {"question": "本系统支持哪些文档格式？", "answer": "支持 Markdown、PDF 与 Word。"},
                {"question": "分块的硬边界是什么？", "answer": "Markdown 标题。"},
            ]}, ensure_ascii=False)
        # document.ground_check
        return json.dumps({
            "grounded": self.ground_keep, "unsupported": [] if self.ground_keep else ["Epub"],
            "keep": self.ground_keep,
        }, ensure_ascii=False)


def _run(tmp_path, client, qa_per_chunk=2):
    from lib.doc2data import doc_to_samples

    return doc_to_samples(client, FIXTURE, qa_per_chunk=qa_per_chunk, max_chunks=2, chunk_size=250)


def test_doc_to_samples_keeps_grounded(tmp_path):
    result = _run(tmp_path, FakeClient(ground_keep=True))
    assert result["stats"]["qa_generated"] == 4  # 2 块 × 2 对
    assert result["stats"]["kept"] == 4
    assert result["stats"]["ground_reject_rate"] == 0.0
    for s in result["samples"]:
        assert s["type"] == "sft" and s["source"] == "document"
        assert s["messages"][0]["role"] == "user"
        assert s["ground_check"]["keep"] is True


def test_doc_to_samples_rejects_ungrounded(tmp_path):
    result = _run(tmp_path, FakeClient(ground_keep=False))
    assert result["stats"]["kept"] == 0
    assert result["stats"]["ground_rejected"] == 4  # 防幻觉质量门全驳回
    assert result["rejected"][0]["unsupported"] == ["Epub"]


def test_doc_to_samples_global_dedup(tmp_path):
    from lib.doc2data import doc_to_samples

    manifest: set[str] = set()
    r1 = doc_to_samples(FakeClient(True), FIXTURE, qa_per_chunk=2, max_chunks=2, chunk_size=250, manifest=manifest)
    assert r1["stats"]["kept"] == 4
    r2 = doc_to_samples(FakeClient(True), FIXTURE, qa_per_chunk=2, max_chunks=2, chunk_size=250, manifest=manifest)
    assert r2["stats"]["kept"] == 0
    assert r2["stats"]["dups"] == 4  # 跨运行零重复
