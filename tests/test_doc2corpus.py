"""doc2corpus 离线测试：导入/清洗/分块/全局去重（fixture 文档）。"""
from __future__ import annotations

import pathlib

from lib.doc2corpus import chunk_hash, chunk_text, clean_text, doc_to_corpus, import_text

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "doc_sample.md"


def test_import_and_clean_markdown():
    raw = import_text(FIXTURE)
    assert "# 第一章" in raw
    cleaned = clean_text("段落一\n\n\n\n段落二\n\n1234\n\n段落三")
    assert "段落一\n\n段落二" in cleaned
    assert "1234" not in cleaned  # 纯页码行被去除
    assert "段落三" in cleaned


def test_chunk_text_respects_paragraphs_and_headings():
    text = "\n\n".join(["# 标题A"] + [f"段落{i}：" + "知识" * 300 for i in range(10)])
    chunks = chunk_text(text, target_chars=2000)
    assert len(chunks) >= 2
    assert chunks[0].startswith("# 标题A")  # 标题硬边界开启首块
    joined = "".join(chunks)
    assert "段落9" in joined  # 内容完整保留


def test_doc_to_corpus_with_global_dedup(tmp_path):
    out = tmp_path / "corpus.jsonl"
    manifest: set[str] = set()
    r1 = doc_to_corpus(FIXTURE, target_chars=800, manifest=manifest)
    assert r1["stats"]["kept"] >= 2
    # 二次处理同文档：全部命中 manifest
    r2 = doc_to_corpus(FIXTURE, target_chars=800, manifest=manifest)
    assert r2["stats"]["kept"] == 0
    assert r2["stats"]["dups"] == r1["stats"]["kept"]

    from lib.doc2corpus import write_corpus_jsonl

    n = write_corpus_jsonl(r1["entries"], out)
    assert n == len(r1["entries"])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert all('"text"' in line and '"source"' in line for line in lines)


def test_chunk_hash_normalizes_whitespace():
    assert chunk_hash("你好 世界") == chunk_hash("你好\n世界")
