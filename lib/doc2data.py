"""文档 → 问答 SFT 数据（表达层管线）：QA 生成 + 事实依据校验 + 全局去重。

定位：doc2corpus（CPT）之后的表达层——教模型"把文档知识在对话里讲出来"。
质量门：document.ground_check 逐条校验答案事实依据，无依据（含常识正确但文段
未提及的信息）一律驳回——这是文档数据防幻觉的关键闸门。
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, List

from lib.doc2corpus import chunk_hash, chunk_text, clean_text, import_text
from lib.llm_client import chat_json
from lib.prompts import get, render


def doc_to_samples(
    client: Any,
    path: str | pathlib.Path,
    qa_per_chunk: int = 3,
    max_chunks: int = 5,
    chunk_size: int = 2000,
    manifest: set[str] | None = None,
) -> Dict[str, Any]:
    """单文档 → 经事实校验的问答样本 + 统计。manifest 为跨文档/跨运行全局查重。"""
    manifest = manifest if manifest is not None else set()
    raw = clean_text(import_text(path))
    chunks = chunk_text(raw, chunk_size)[:max_chunks]

    samples: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    stats = {"chunks": len(chunks), "qa_generated": 0, "kept": 0, "ground_rejected": 0, "dups": 0}

    for ci, chunk in enumerate(chunks):
        if len(chunk) < 40:  # 过短片段无问答价值（低于 60 字符跳过）
            continue
        try:
            qa = chat_json(client, [{"role": "user", "content": render(
                get("document.qa_gen"), chunk=chunk, n=qa_per_chunk)}], temperature=0.8)["qa"]
        except Exception as e:  # noqa: BLE001
            rejected.append({"chunk": ci, "error": str(e)[:200]})
            continue
        for pair in qa:
            question = str(pair.get("question", "")).strip()
            answer = str(pair.get("answer", "")).strip()
            if not question or not answer:
                continue
            stats["qa_generated"] += 1
            qid = f"{chunk_hash(chunk)}:{chunk_hash(question)}"
            if qid in manifest:
                stats["dups"] += 1
                continue
            manifest.add(qid)
            try:
                check = chat_json(client, [{"role": "user", "content": render(
                    get("document.ground_check"), chunk=chunk, question=question, answer=answer)},
                ], temperature=0.2)
            except Exception as e:  # noqa: BLE001
                rejected.append({"question": question[:80], "error": str(e)[:200]})
                continue
            if not check.get("keep", False):
                stats["ground_rejected"] += 1
                rejected.append({"question": question[:80], "unsupported": check.get("unsupported", [])})
                continue
            stats["kept"] += 1
            samples.append({
                "id": f"doc-{qid}",
                "source": "document",
                "type": "sft",
                "doc_file": pathlib.Path(path).name,
                "messages": [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ],
                "ground_check": check,
            })

    stats["ground_reject_rate"] = round(
        stats["ground_rejected"] / max(stats["qa_generated"], 1), 3
    )
    return {"samples": samples, "rejected": rejected, "stats": stats}
