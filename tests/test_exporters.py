"""导出器离线测试（llamafactory sharegpt / chat messages / DPO）。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _samples():
    from lib.adapters.rollout_import import iter_records, record_to_sample

    recs = list(iter_records(ROOT / "tests" / "fixtures" / "rollout_sample.jsonl"))
    return [record_to_sample(r, "separated") for r in recs if not r.get("error")]


def test_to_sharegpt_mapping():
    from lib.exporters import to_sharegpt_sample

    s = _samples()[2]  # 含 tool-calls + tool 结果 + separated 思考
    conv = to_sharegpt_sample(s)
    assert conv is not None
    roles = [c["from"] for c in conv["conversations"]]
    # 历史含带推理块的 assistant 轮（合并进 gpt）+ 工具调用轮 + 工具结果 + 最终 gpt
    assert roles == ["human", "gpt", "human", "gpt", "function_call", "observation", "gpt"]
    # 工具调用轮 → function_call（JSON 字符串 {name, arguments}）
    fc = conv["conversations"][4]
    parsed = json.loads(fc["value"])
    assert parsed["name"] == "Bash" and "command" in parsed["arguments"]
    # separated 思考合并进最终 gpt 轮
    last_gpt = conv["conversations"][-1]["value"]
    assert "改用 pwd" in last_gpt and "当前目录为" in last_gpt
    # 工具结果 → observation
    assert "No such file" in conv["conversations"][5]["value"]


def test_to_chat_sample_keeps_reasoning_field():
    from lib.exporters import to_chat_sample

    s = _samples()[2]
    out = to_chat_sample(s)
    final = out["messages"][-1]
    assert final["reasoning_content"]  # separated：推理独立字段
    assert "改用 pwd" in final["reasoning_content"]
    assert final["content"] == "当前目录为 F:\\work"
    # toolCalls 保留标准 OpenAI 结构
    fc = [m for m in out["messages"] if m.get("toolCalls")]
    assert fc and fc[0]["toolCalls"][0]["name"] == "Bash"
    # 内部元数据（isError）不进入训练格式
    assert all("isError" not in m for m in out["messages"])


def test_to_dpo_sample():
    from lib.exporters import to_dpo_sample

    prompt = [{"role": "user", "content": "问题"}]
    chosen = [{"role": "assistant", "content": "正确回答"}]
    rejected = [{"role": "assistant", "content": "错误回答"}]
    pair = to_dpo_sample(prompt, chosen, rejected)
    assert pair == {"prompt": prompt, "chosen": chosen, "rejected": rejected}


def test_export_samples_writes_files(tmp_path):
    from lib.exporters import export_samples

    samples = _samples()
    out = tmp_path / "sft.jsonl"
    counts = export_samples(samples, "chat", out)
    assert counts["sft"] == 3
    lines = out.read_text(encoding="utf-8").splitlines()
    assert all("messages" in json.loads(l) for l in lines)

    out2 = tmp_path / "lf.jsonl"
    counts2 = export_samples(samples, "llamafactory", out2)
    assert counts2["sft"] == 3
    assert all("conversations" in json.loads(l) for l in out2.read_text(encoding="utf-8").splitlines())
