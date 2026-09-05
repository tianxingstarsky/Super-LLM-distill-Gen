"""M1 第四批离线测试：审核决策逻辑 + 监控本地落盘（不依赖 Docker 服务）。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_decide_gate_thresholds():
    from lib.review import decide_gate

    # 条数不足不触发
    few = [{"sample_id": f"s{i}", "decision": "keep"} for i in range(9)]
    assert decide_gate(few)["release"] is False

    # 通过率不足不触发
    mixed = [{"sample_id": f"s{i}", "decision": "keep" if i < 8 else "reject"} for i in range(10)]
    assert decide_gate(mixed)["release"] is False  # 0.8 < 0.9

    # 达标触发
    good = [{"sample_id": f"s{i}", "decision": "keep" if i < 9 else "reject"} for i in range(10)]
    result = decide_gate(good)
    assert result["release"] is True
    assert result["pass_rate"] == 0.9


def test_build_records_plain_text_no_json_noise():
    from lib.adapters.rollout_import import iter_records, record_to_sample
    from lib.review import build_records

    recs = list(iter_records(ROOT / "tests" / "fixtures" / "rollout_sample.jsonl"))
    samples = [record_to_sample(r, "separated") for r in recs if not r.get("error")]
    records = build_records(samples, scores={})
    # 审核中心扁平记录格式：字段名直接作键
    conv = records[2]["conversation"]
    assert "【工具调用】Bash（" in conv
    assert "【工具结果❌】" in conv  # isError → 失败标记
    assert "{" not in conv and '"' not in conv.replace("【", "").replace("】", "")
    assert records[2]["sample_id"] == samples[2]["id"]
    # judge 建议注入（suggestion 字段，供人工参考）
    with_scores = build_records(samples, scores={samples[0]["id"]: '"keep": true'})
    assert with_scores[0]["suggestion"] == "keep"


def test_monitor_local_fallback(tmp_path, monkeypatch):
    from lib.monitor import trace_run

    monkeypatch.setattr("lib.monitor._langfuse_config", lambda root: None)  # 未配置 → 本地兜底
    trace_run(tmp_path, "distill", {"n": 1, "usage": {"calls": 3}})
    lines = (tmp_path / "data" / "output" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "distill" and entry["usage"]["calls"] == 3


def test_monitor_noop_when_langfuse_down(tmp_path, monkeypatch):
    from lib.monitor import trace_run

    # 配置存在但服务不可达 → 不抛异常，本地仍落盘
    monkeypatch.setattr(
        "lib.monitor._langfuse_config",
        lambda root: {"public_key": "x", "secret_key": "y", "host": "http://127.0.0.1:1"},
    )
    trace_run(tmp_path, "export", {"counts": {"sft": 1}})
    assert (tmp_path / "data" / "output" / "runs.jsonl").exists()
