"""doc2corpus 离线测试：导入/清洗/分块/全局去重（fixture 文档）。"""
from __future__ import annotations

import pathlib

import pytest

from lib.doc2corpus import chunk_hash, chunk_text, clean_text, doc_to_corpus, import_text

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "doc_sample.md"


def test_import_and_clean_markdown():
    raw = import_text(FIXTURE)
    assert "# 第一章" in raw
    cleaned = clean_text("段落一\n\n\n\n段落二\n\n1234\n\n段落三")
    assert "段落一\n\n段落二" in cleaned
    assert "1234" in cleaned  # 数字可能是正文、编号或表格值，不能自动删去。
    assert "段落三" in cleaned

    code = clean_text("说明\n\n    value = 1234\n    return value\n")
    assert "    value = 1234\n    return value" in code
    assert "    value = 1234\n    return value" in chunk_text(code)[0]


def test_chunk_text_respects_paragraphs_and_headings():
    text = "\n\n".join(["# 标题A"] + [f"段落{i}：" + "知识" * 300 for i in range(10)])
    chunks = chunk_text(text, target_chars=2000)
    assert len(chunks) >= 2
    assert chunks[0].startswith("# 标题A")  # 标题硬边界开启首块
    joined = "".join(chunks)
    assert "段落9" in joined  # 内容完整保留


def test_chunk_text_splits_oversized_single_paragraph_without_losing_characters():
    paragraph = "".join(f"编号{i:04d}对应参数{i % 31}。" for i in range(300))
    chunks = chunk_text(paragraph, target_chars=250)
    assert len(chunks) > 1
    assert all(len(chunk) <= 250 for chunk in chunks)
    assert "".join(chunks) == paragraph
    overlapped = chunk_text(paragraph, target_chars=250, overlap=30)
    assert all(len(chunk) <= 250 for chunk in overlapped)
    assert overlapped[0][-30:] == overlapped[1][:30]
    assert overlapped[-1].endswith(paragraph[-30:])


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


def test_import_text_decodes_non_utf8_chinese(tmp_path):
    """GB18030 中文文档不再被 errors=replace 静默转成 U+FFFD 乱码。"""
    target = tmp_path / "gbk.txt"
    target.write_bytes("中文知识：分块与去重。".encode("gb18030"))
    raw = import_text(target)
    assert "中文知识" in raw
    assert "\ufffd" not in raw


def test_undecodable_cpt_source_is_quarantined_instead_of_replaced(tmp_path):
    from lib.infrastructure.training_workflow import Workflow, create_run

    source = tmp_path / "broken.txt"
    source.write_bytes(b"\x81")  # Incomplete in both UTF-8 and GB18030.
    with pytest.raises(UnicodeError, match="invalid_text_encoding"):
        doc_to_corpus(source)

    run_id = create_run(tmp_path, sources=[source], targets=["cpt"])
    workflow = Workflow(tmp_path, run_id, tmp_path)
    records = workflow.parse_source(workflow.recipe["sources"][0])
    assert len(records) == 1
    assert records[0]["status"] == "quarantined"
    assert records[0]["reason"] == "invalid_encoding"


def test_cpt_corpus_keeps_standalone_numbers_and_code_indentation(tmp_path):
    source = tmp_path / "manual.md"
    source.write_text("设备编号\n\n1234\n\n    value = 1234\n    return value\n", encoding="utf-8")
    output = doc_to_corpus(source)
    text = "\n\n".join(entry["text"] for entry in output["entries"])
    assert "\n\n1234\n\n" in text
    assert "    value = 1234\n    return value" in text
