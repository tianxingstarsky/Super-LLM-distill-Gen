"""回归测试：本轮 bug 修复的针对性锁定（配置隔离 / 监控降级 / judge 建议 / 导入去重 / 编码）。

每个测试对应一个已确认缺陷，修复前的行为都会让断言失败。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── pipeline_config：跨调用污染 ─────────────────────────────────────────────
def test_load_pipeline_config_does_not_mutate_defaults():
    from lib import pipeline_config as pc

    baseline = json.dumps(pc.DEFAULTS, ensure_ascii=False, sort_keys=True)
    pc.load_pipeline_config("generation", ROOT)
    pc.load_pipeline_config("rollout", ROOT, overrides={"llm": {"temperature": 0.123}})
    assert json.dumps(pc.DEFAULTS, ensure_ascii=False, sort_keys=True) == baseline

    # 后续普通加载不受前一次 yaml/override 影响
    fresh = pc.load_pipeline_config("rollout", ROOT)
    assert fresh["llm"]["temperature"] == pc.DEFAULTS["llm"]["temperature"]


def test_load_pipeline_config_returns_independent_dicts():
    from lib import pipeline_config as pc

    first = pc.load_pipeline_config("rollout", ROOT)
    first["llm"]["temperature"] = 99
    second = pc.load_pipeline_config("rollout", ROOT)
    assert second["llm"]["temperature"] != 99
    assert pc.DEFAULTS["llm"]["temperature"] != 99


# ── monitor：监控配置损坏不得中断主流程 ─────────────────────────────────────
def test_trace_run_survives_corrupt_monitor_config(tmp_path, monkeypatch):
    from lib import monitor, workspace as WS

    output = tmp_path / "output"
    monkeypatch.setattr(WS, "resolve", lambda root=None: "default")
    monkeypatch.setattr(WS, "output_at", lambda root, name=None: output)

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "backends.local.yaml").write_text("langfuse: [unclosed", encoding="utf-8")

    monitor.trace_run(tmp_path, "unit", {"ok": True})  # 不得抛异常

    entries = [json.loads(line) for line in (output / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert entries and entries[-1]["kind"] == "unit" and entries[-1]["workspace"] == "default"


# ── review：judge 失败不得变成 reject 建议 ──────────────────────────────────
def test_judge_suggestion_ignores_failed_scores():
    from lib.review import _judge_suggestion

    # 失败/空/无法解析：不给建议（旧逻辑一律标 reject）
    assert _judge_suggestion(None) is None
    assert _judge_suggestion("") is None
    assert _judge_suggestion("judge call failed") is None
    assert _judge_suggestion(json.dumps({"score": 4})) is None  # 无 keep 字段：不给建议
    # 可解析结果按 keep 布尔取建议
    assert _judge_suggestion(json.dumps({"keep": True})) == "keep"
    assert _judge_suggestion(json.dumps({"keep": False})) == "reject"
    assert _judge_suggestion(True) == "keep"
    assert _judge_suggestion(False) == "reject"
    assert _judge_suggestion("True") == "keep"   # 大小写不敏感
    assert _judge_suggestion('"keep": true') == "keep"  # 片段化输出仍可用
    assert _judge_suggestion('"keep": true}') == "keep"


def test_build_records_omits_suggestion_for_errored_judge():
    from lib.review import build_records

    samples = [{"id": "s-ok", "messages": [{"role": "user", "content": "问"}, {"role": "assistant", "content": "答"}]},
               {"id": "s-err", "messages": [{"role": "user", "content": "问2"}, {"role": "assistant", "content": "答2"}]}]
    records = build_records(samples, {"s-ok": json.dumps({"keep": True}), "s-err": ""})
    by_id = {r["sample_id"]: r for r in records}
    assert by_id["s-ok"].get("suggestion") == "keep"
    assert "suggestion" not in by_id["s-err"]  # 失败调用不产生虚假 reject


def test_plain_messages_renders_openai_style_tool_calls():
    from lib.review import _plain_messages

    sample = {"messages": [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "search", "arguments": {"q": "天气"}}]},
    ]}
    text = _plain_messages(sample)
    assert "【工具调用】search" in text and "天气" in text


# ── doc2corpus：中文非 UTF-8 编码不再静默变乱码 ──────────────────────────────
def test_import_text_decodes_gb18030(tmp_path):
    from lib.doc2corpus import import_text

    target = tmp_path / "gbk.txt"
    target.write_bytes("中文知识内容：第一段。".encode("gb18030"))
    text = import_text(target)
    assert "中文知识内容" in text
    assert "\ufffd" not in text


def test_decode_text_prefers_utf8():
    from lib.doc2corpus import _decode_text

    assert _decode_text("UTF-8 中文".encode("utf-8")) == "UTF-8 中文"


# ── prefs：校正阶段沿用构造时的 default 下限 ────────────────────────────────
def test_corrected_weights_respect_instance_floor():
    from lib.prefs import PreferenceSampler

    sampler = PreferenceSampler({"a": 1.0}, default_floor=0.0, seed=1)
    corrected = sampler.corrected_weights({"a": 1.0})
    assert "default" in corrected
    assert corrected["default"] == 0.0  # 显式 0.0 不得被全局 0.15 覆盖


# ── review_editor：messages=null 时回退 conversations ───────────────────────
def test_normalize_sample_falls_back_when_messages_null():
    from lib.review_editor import normalize_sample

    sample = {"messages": None,
              "conversations": [{"role": "user", "content": "你好"},
                                {"role": "assistant", "content": "你好！"}]}
    normalized = normalize_sample(sample)
    assert [m["role"] for m in normalized["messages"]] == ["user", "assistant"]
