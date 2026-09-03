"""蒸馏质检离线测试：样本分类（运行时事实）+ DPO 负样本对提取。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "rollout_sample.jsonl"


def _samples():
    from lib.adapters.rollout_import import iter_records, record_to_sample

    recs = list(iter_records(FIXTURE))
    return [record_to_sample(r, "separated") for r in recs if not r.get("error")]


def test_classify_sample_uses_runtime_facts():
    from lib.adapters.distill import classify_sample

    samples = _samples()
    # rec3 含 1 个 isError 工具步骤 → recovery；其他 → clean
    tags = [classify_sample(s)["tag"] for s in samples]
    assert tags == ["clean", "clean", "recovery"]
    c = classify_sample(samples[2])
    assert c["error_tool_steps"] == 1
    assert c["has_reasoning"] is True  # separated 风格尾轮带 reasoning_content


def test_classify_report_counts():
    from lib.adapters.distill import classify_report

    report = classify_report(_samples())
    assert report["counts"] == {"clean/answer": 1, "clean/tool-calls": 1, "recovery/answer": 1}


def test_extract_dpo_pairs():
    from lib.adapters.distill import extract_dpo_pairs

    pairs = extract_dpo_pairs(str(FIXTURE), "separated")
    assert len(pairs) == 1
    pair = pairs[0]
    # prompt：错误工具调用之前的全部历史（截至用户"帮我查当前目录"）
    assert pair["prompt"][-1]["role"] == "user"
    # rejected：发出失败 ls 调用的 assistant 回合（含错误 toolCall）
    assert pair["rejected"][0]["toolCalls"][0]["name"] == "Bash"
    assert "ls" in json.dumps(pair["rejected"], ensure_ascii=False)
    # chosen：纠正后的最终回合（separated：reasoning_content 含"改用 pwd"）
    assert pair["chosen"][0]["reasoning_content"]
    assert "改用 pwd" in pair["chosen"][0]["reasoning_content"]
    assert pair["chosen"][0]["content"] == "当前目录为 F:\\work"
    # 结构完整性：错误回合（带 toolCalls 的 assistant）被移到 rejected，不在 prompt 里
    assert all(not m.get("toolCalls") for m in pair["prompt"] if m["role"] == "assistant")


def test_extract_dpo_pairs_skips_error_records():
    from lib.adapters.distill import extract_dpo_pairs

    # fixture 里错误记录（限流）不产生 DPO 对；同文件幂等去重
    pairs = extract_dpo_pairs(str(FIXTURE), "separated")
    assert all(p["chosen"][0].get("content") or p["chosen"][0].get("toolCalls") for p in pairs)
