"""M0 验证 1 测试：distilabel 最小管线在 Windows 上跑通 + 步骤缓存生效。

运行方式：
    .venv/Scripts/python.exe -m pytest tests/test_pipeline_smoke.py -v
"""
from __future__ import annotations

import os
import pathlib
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import mock_llm_server  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = 8765

# 秘密字段不序列化进 YAML；distilabel 从环境变量读取（与 backends.yaml 的 api_key_env 一致）
os.environ.setdefault("OPENAI_API_KEY", "sk-mock")


@pytest.fixture(scope="module", autouse=True)
def mock_server():
    thread = threading.Thread(target=mock_llm_server.serve, args=(PORT,), daemon=True)
    thread.start()
    time.sleep(0.6)
    yield
    # 守护线程随测试进程退出；无需显式停机


def _fresh_pipeline(tmp_path: pathlib.Path, tag: str):
    from distilabel.pipeline import Pipeline

    pipeline = Pipeline.from_yaml(str(ROOT / "pipelines" / "00_smoke.yaml"))
    # YAML 中路径为相对项目根；测试中改为绝对路径
    pipeline.dag.set_step_attr(
        "load_data", "file_path", str(ROOT / "tests" / "fixtures" / "smoke_input.jsonl")
    )
    # 独立缓存目录：批次管理器状态落在 pipeline._cache_dir 下（类型须为 Path）；
    # dry_run 与 run 共用同一管线/缓存会互相污染（1.5.3 行为），故每阶段独立实例
    pipeline._cache_dir = tmp_path / f"cache_{tag}"
    return pipeline


def test_smoke_pipeline_runs_and_caches(tmp_path):
    # 阶段 1：dry-run 校验 DAG 可执行（batch_size=1 → 仅 1 行，真实调用 LLM 1 次）
    p1 = _fresh_pipeline(tmp_path, "dry")
    before_dry = mock_llm_server.MockLLMHandler.request_count
    dry = p1.dry_run(parameters={}, batch_size=1)
    assert dry is not None
    assert mock_llm_server.MockLLMHandler.request_count - before_dry == 1

    # 阶段 2：全新管线 + 全新缓存 → 完整冷跑 3 行
    p2 = _fresh_pipeline(tmp_path, "run1")
    before_run = mock_llm_server.MockLLMHandler.request_count
    distiset = p2.run(use_cache=True)
    assert mock_llm_server.MockLLMHandler.request_count - before_run == 3

    rows = [dict(r) for r in distiset["default"]["train"]]
    assert len(rows) == 3
    assert all("response" in r for r in rows)
    assert all(str(r["response"]).startswith("[mock 回复]") for r in rows)

    # 阶段 3：同一管线重跑 → 批次管理器/步骤缓存命中 → 0 次请求
    before_cached = mock_llm_server.MockLLMHandler.request_count
    p2.run(use_cache=True)
    assert (
        mock_llm_server.MockLLMHandler.request_count - before_cached == 0
    ), "缓存应生效，重跑不应再发请求"
