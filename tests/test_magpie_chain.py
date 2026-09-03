"""M0 验证 2 测试：Magpie 链（无种子指令生成）在 mock 端点跑通 + 缓存。"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import mock_llm_server  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = 18765

os.environ.setdefault("OPENAI_API_KEY", "sk-mock")


def test_magpie_chain_runs_and_caches(tmp_path):
    from distilabel.pipeline import Pipeline

    pipeline = Pipeline.from_yaml(str(ROOT / "pipelines" / "01_magpie.yaml"))
    pipeline._cache_dir = tmp_path / "cache_magpie"

    before = mock_llm_server.MockLLMHandler.request_count
    distiset = pipeline.run(use_cache=True)
    first = mock_llm_server.MockLLMHandler.request_count - before
    # n_tasks=10、batch_size 默认 50 → 1 批 10 次请求
    assert first == 10, f"首轮应发出 10 次请求，实际 {first}"

    rows = [dict(r) for r in distiset["default"]["train"]]
    assert len(rows) == 10
    assert all("instruction" in r and r["instruction"] for r in rows)
    # mock 回复内容应透传到 instruction
    assert all(str(r["instruction"]).startswith("[mock 回复]") for r in rows)

    # 重跑：缓存命中 → 0 请求
    before_second = mock_llm_server.MockLLMHandler.request_count
    pipeline.run(use_cache=True)
    assert mock_llm_server.MockLLMHandler.request_count - before_second == 0
