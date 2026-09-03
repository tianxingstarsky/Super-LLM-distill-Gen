"""灵活性改造离线测试：管线配置层、提示词版本寻址、预算守卫、闸门豁免、多格式导出。"""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_pipeline_config_defaults_and_merge():
    from lib.pipeline_config import load_pipeline_config

    cfg = load_pipeline_config("translation", ROOT)
    assert cfg["translation"]["faithful_threshold"] == 4
    assert cfg["llm"]["thinking"] is False
    # 覆盖合并
    cfg2 = load_pipeline_config("translation", ROOT, overrides={"translation": {"faithful_threshold": 5}})
    assert cfg2["translation"]["faithful_threshold"] == 5
    assert cfg2["llm"]["json_mode"] is True  # 未覆盖部分保留


def test_prompt_version_addressing():
    from lib.prompts import get, registry_versions

    versions = registry_versions()
    assert "distill.generator" in versions
    latest = get("distill.generator")
    assert latest.version == max(versions["distill.generator"].keys())
    # 版本寻址 + 未知版本报错
    for ver, spec in versions["distill.generator"].items():
        assert get("distill.generator", ver) is spec
    with pytest.raises(KeyError):
        get("distill.generator", "0.0.0")


def test_prompt_eval_filters_and_custom_cases(tmp_path):
    from lib.prompt_eval import DEFAULT_CASES, load_cases, run_all

    class Fake:
        def chat(self, messages, **kwargs):
            return '{"ok": true}'

    # --ids 过滤：只跑指定提示词
    subset = run_all(Fake(), DEFAULT_CASES, ids=["magpie.query"])
    assert len(subset) == 1 and subset[0]["prompt_id"] == "magpie.query"

    # 自定义用例文件
    cases_file = tmp_path / "cases.yaml"
    cases_file.write_text(
        'name: 自定义\nprompt_id: "magpie.query"\nvariables: {}\nchecks:\n'
        "  - type: contains\n    args: [x]\n",
        encoding="utf-8",
    )
    custom = load_cases(str(cases_file))
    assert len(custom) == 1 and custom[0].checks[0][0] == "contains"


def test_budget_guard_enforces_and_persists(tmp_path):
    from lib.llm_client import BudgetExceeded, BudgetGuard

    guard = BudgetGuard(tmp_path, limit_usd=0.01, hard_stop=True)
    guard.add_usd(0.004)
    with pytest.raises(BudgetExceeded):
        guard.add_usd(0.008)  # 累计 0.012 ≥ 0.01
    # 持久化：新实例读回累计值
    guard2 = BudgetGuard(tmp_path, limit_usd=1.0)
    assert guard2.spent == pytest.approx(0.012, abs=1e-6)


def test_gate_auto_approve_declaration(tmp_path):
    from lib.gates import GateKeeper

    yaml_path = tmp_path / "gates.yaml"
    yaml_path.write_text(
        "gates:\n  - id: GX\n    title: 测试闸\n    trigger: 测试\n    prompt: 确认\n"
        "    requires: []\n    auto_approve: true\n",
        encoding="utf-8",
    )
    keeper = GateKeeper(yaml_path, tmp_path / "state.json")
    assert keeper.status("GX") == "approved"  # 显式声明豁免，无需交互
    keeper.require("GX")  # 不抛异常


def test_export_all_writes_both_formats(tmp_path):
    from lib.exporters import export_samples

    samples = [{"messages": [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]}] * 3
    out = tmp_path / "sft.jsonl"
    counts = export_samples(iter(samples), "all", out)
    assert counts["llamafactory"] == 3 and counts["chat"] == 3
    assert (tmp_path / "sft_llamafactory.jsonl").exists()
    assert (tmp_path / "sft_chat.jsonl").exists()


def test_chat_client_json_mode_fallback_remembers():
    from lib.llm_client import ChatClient, chat_json

    client = ChatClient(base_url="http://127.0.0.1:1/v1", api_key="sk-t", model="m")

    def fake_create(**kwargs):
        if "response_format" in kwargs:
            raise RuntimeError("400 response_format unsupported")
        msg = SimpleNamespace(content='{"ok": 1}', reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    client.client.chat.completions.create = fake_create
    assert chat_json(client, [{"role": "user", "content": "输出 JSON"}]) == {"ok": 1}
    assert client.json_supported is False
