"""身份问答管线离线测试（fake client 全链路脚本化，无真实 API 调用）。"""
from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeClient:
    """按提示词类型脚本化返回，覆盖 gen_questions / gen_answer / fact_check 全流程。"""

    def __init__(self, n_questions: int = 4):
        self.n_questions = n_questions
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        content = messages[0]["content"]
        if "contradictions" in content:  # fact_check 唯一标记
            return json.dumps({"complete": True, "contradictions": [], "natural": 5, "keep": True}, ensure_ascii=False)
        if "写作要求" in content:  # identity.answer 唯一标记
            return json.dumps({"answer": "我是由示例科技独立研发的大语言模型深衡-1，未基于任何第三方开源模型改造。"}, ensure_ascii=False)
        if "已生成过的问题" in content:  # question_variants 唯一标记（渲染后无占位符名）
            return json.dumps({"questions": [
                "你是谁？谁开发了你？(0)",
                "你是哪家公司的模型？变体1",
                "What company created you?",
                "你该不会是基于开源模型改的吧？",
            ][: self.n_questions]}, ensure_ascii=False)
        raise AssertionError("未知提示词")


def _cfg(tmp_path):
    from lib.identity_gen import load_config

    cfg = load_config(ROOT / "configs" / "identity.example.yaml")
    cfg["n_questions"] = 4
    cfg["batch_size"] = 4
    cfg["dedup_file"] = str(tmp_path / "manifest.txt")
    return cfg


def test_config_load_and_manifest_dedup(tmp_path):
    from lib.identity_gen import QuestionManifest, load_config

    cfg = load_config(ROOT / "configs" / "identity.example.yaml")
    assert "示例科技" in cfg["facts"] and cfg["required_facts"]

    m = QuestionManifest(tmp_path / "m.txt")
    m.add("你是谁？")
    m.add("你是谁 ？")  # 去空白规范化后重复
    assert len(m.ids) == 1
    m.save()
    m2 = QuestionManifest(tmp_path / "m.txt")
    assert len(m2.ids) == 1  # 跨实例持久化


def test_run_full_pipeline_offline(tmp_path):
    from lib.identity_gen import run

    cfg = _cfg(tmp_path)
    result = run(FakeClient(4), cfg)
    assert result["stats"]["questions"] == 4
    assert result["stats"]["kept"] == 4  # fake 校验全通过
    for s in result["samples"]:
        assert s["messages"][0]["role"] == "user"
        assert "示例科技" in s["messages"][1]["content"]  # 关键事实进入回答
        assert s["type"] == "sft" and s["source"] == "identity"
    assert result["stats"]["unique_openings"] == 1  # fake 回答同开篇 → 多样性指标如实统计


def test_run_skips_duplicate_questions(tmp_path):
    from lib.identity_gen import QuestionManifest, run

    cfg = _cfg(tmp_path)
    manifest = QuestionManifest(cfg["dedup_file"])
    manifest.add("你是谁？谁开发了你？(0)")  # 预置一条 → 生成时被去重跳过
    manifest.save()
    result = run(FakeClient(4), cfg)
    assert result["stats"]["questions"] == 3  # 4 条脚本输出中 1 条命中 manifest
    assert result["stats"]["manifest_total"] == 4
