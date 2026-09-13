"""模型后端/密钥管理与本地端点测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def manager(monkeypatch, tmp_path):
    from lib import backend_manager as bm
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    (tmp_path / "configs").mkdir(parents=True)
    (tmp_path / "configs" / "backends.yaml").write_text(
        "backends:\n  deepseek:\n    base_url: https://api.deepseek.com/v1\n    api_key_env: DEEPSEEK_API_KEY\n    models: [deepseek-chat]\n"
        "default_backend: deepseek\njudge_model: deepseek-v4-pro\n"
        "model_roles:\n  generation: { backend: deepseek, model: deepseek-chat }\n  judge: { backend: deepseek, model: deepseek-v4-pro }\n"
        "budget:\n  max_total_usd: 5.0\n", encoding="utf-8")
    return bm


def test_list_masks_and_sources(manager, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-1234abcd")
    info = manager.list_backends()
    row = next(r for r in info["backends"] if r["name"] == "deepseek")
    assert row["api_key"]["source"] == "env:DEEPSEEK_API_KEY"
    assert row["api_key"]["status"] == "存在"
    assert "sk-test" not in str(info) and "1234abcd" not in str(info)  # 全量输出不含明文密钥
    assert row["roles"] == ["generation", "judge"] and row["is_default"] is True
    assert info["budget"]["max_total_usd"] == 5.0


def test_save_endpoint_merge_backup_and_validation(manager):
    manager.save_endpoint("local_gpu", "http://127.0.0.1:11434/v1", ["qwen:7b"], api_key_env="")
    assert manager.test_backend is not None  # 函数导出
    info = manager.list_backends()
    row = next(r for r in info["backends"] if r["name"] == "local_gpu")
    assert row["api_key"]["source"] == "未配置" and row["api_key"]["status"] == "缺失"
    # 覆盖保护
    with pytest.raises(FileExistsError):
        manager.save_endpoint("local_gpu", "http://x/v1", ["m"])
    manager.save_endpoint("local_gpu", "http://127.0.0.1:11434/v1", ["qwen:7b", "vl:7b"],
                          api_key_env="LOCAL_KEY", explicit_replace=True)
    info = manager.list_backends()
    row = next(r for r in info["backends"] if r["name"] == "local_gpu")
    assert row["models"] == ["qwen:7b", "vl:7b"]
    assert (manager.ROOT / "configs").exists() and list((manager.ROOT / "configs").glob("backends.local.yaml.*.bak"))
    # 本地覆盖文件必须是合法 YAML（旧实现写成 JSON，破坏该文件的 YAML 惯例）
    import yaml
    local = manager.ROOT / "configs" / "backends.local.yaml"
    written = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert written["backends"]["local_gpu"]["base_url"] == "http://127.0.0.1:11434/v1"
    # 校验
    with pytest.raises(ValueError):
        manager.save_endpoint("bad name", "http://x/v1", ["m"])
    with pytest.raises(ValueError):
        manager.save_endpoint("x", "ftp://x", ["m"])
    with pytest.raises(ValueError):
        manager.save_endpoint("x", "http://x/v1", [])


def test_role_switch_and_reset_budget(manager, monkeypatch):
    manager.save_endpoint("local_gpu", "http://127.0.0.1:11434/v1", ["q:7b"], api_key_env="")
    manager.set_role("judge", "local_gpu", "q:7b")
    assert manager.list_backends()["roles"]["judge"] == {"backend": "local_gpu", "model": "q:7b"}
    with pytest.raises(ValueError):
        manager.set_role("nope", "local_gpu", "q")
    monkeypatch.setattr(manager, "_limit", lambda: 5.0)
    (manager.ROOT / "data/output").mkdir(parents=True, exist_ok=True)
    import json
    (manager.ROOT / "data/output/budget.json").write_text('{"spent_usd": 2.5, "limit_usd": 5}', encoding="utf-8")
    spent = manager.reset_budget("test")
    assert spent == 2.5
    info = manager.list_backends()
    assert info["spent"] == 0.0
    audit = (manager.ROOT / "data/output/runs.jsonl")
    assert audit.exists() and "budget_reset" in audit.read_text(encoding="utf-8")


def test_backend_test_against_mock_server(manager):
    # mock 服务由 conftest 会话级提供（/v1/models 返回 mock-model）
    out = manager.test_backend("deepseek", overrides={"base_url": "http://127.0.0.1:18765/v1"})
    assert out["models"] == ["mock-model"]
