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


# ── minimind（格式规范：dataset/lm_dataset.py 加载侧实证） ───────────────────
def test_to_minimind_sft_shape():
    """conversations[role/content]，assistant 保留 reasoning_content，tool_calls 为 JSON 字符串。"""
    from lib.exporters import to_minimind_sft

    sample = {
        "messages": [
            {"role": "user", "content": "帮我查一下天气"},
            {"role": "assistant", "content": "", "reasoning_content": "需要调用天气工具",
             "toolCalls": [{"name": "get_weather", "input": {"city": "北京"}}]},
            {"role": "tool", "content": "晴 25℃", "toolCallId": "call_1"},
            {"role": "assistant", "content": "北京今天晴，25 度。", "reasoning_content": "读数正常"},
        ],
    }
    out = to_minimind_sft(sample)
    conv = out["conversations"]
    assert [c["role"] for c in conv] == ["user", "assistant", "tool", "assistant"]
    # reasoning_content 逐消息保留（minimind 加载侧 .get() 读取）
    assert conv[1]["reasoning_content"] == "需要调用天气工具"
    # tool_calls 必须是 JSON 字符串（minimind 侧 json.loads 还原）
    import json as _j

    assert isinstance(conv[1]["tool_calls"], str)
    assert _j.loads(conv[1]["tool_calls"]) == [{"name": "get_weather", "arguments": {"city": "北京"}}]
    # 内部元数据剥离
    assert "toolCallId" not in conv[2] and "isError" not in str(conv)


def test_to_minimind_dpo_full_dialogues():
    """chosen/rejected = 含 prompt 前缀的完整消息列表（minimind 直接 apply_chat_template）。"""
    from lib.exporters import to_minimind_dpo

    pair = {
        "prompt": [{"role": "user", "content": "问题"}],
        "chosen": [{"role": "assistant", "content": "正确", "reasoning_content": "思路好"}],
        "rejected": [{"role": "assistant", "content": "错误"}],
    }
    out = to_minimind_dpo(pair)
    assert [m["role"] for m in out["chosen"]] == ["user", "assistant"]
    assert out["chosen"][1]["reasoning_content"] == "思路好"
    assert [m["role"] for m in out["rejected"]] == ["user", "assistant"]
    assert out["rejected"][1]["content"] == "错误"


def test_export_minimind_three_files(tmp_path):
    """三件套：sft_t2t.jsonl / pretrain_t2t.jsonl（语料存在才写）/ dpo.jsonl（对存在才写）。"""
    from lib.exporters import export_minimind, to_dpo_sample

    samples = [
        {"messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}]},
        {"messages": [{"role": "user", "content": "无回答"}]},  # 无 assistant 输出 → 被过滤
    ]
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        json.dumps({"text": "预训练语料一", "source": "a.md", "chunk_id": "a#0"}, ensure_ascii=False) + "\n"
        + json.dumps({"text": "", "chunk_id": "b#0"}, ensure_ascii=False) + "\n",  # 空文本 → 过滤
        encoding="utf-8",
    )
    dpo = tmp_path / "dpo.jsonl.src"
    dpo.write_text(json.dumps(
        to_dpo_sample([{"role": "user", "content": "q"}],
                      [{"role": "assistant", "content": "好"}],
                      [{"role": "assistant", "content": "差"}]), ensure_ascii=False) + "\n", encoding="utf-8")

    counts = export_minimind(samples, tmp_path / "export" / "sft.jsonl",
                             corpus_path=corpus, dpo_path=dpo)
    assert counts == {"sft": 1, "pretrain": 1, "dpo": 1}

    sft = [json.loads(l) for l in (tmp_path / "export" / "sft_t2t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sft[0]["conversations"][0]["role"] == "user"
    pre = [json.loads(l) for l in (tmp_path / "export" / "pretrain_t2t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert pre == [{"text": "预训练语料一"}]  # 仅 text 字段，source/chunk_id 剥离
    dpo_out = [json.loads(l) for l in (tmp_path / "export" / "dpo.jsonl").read_text(encoding="utf-8").splitlines()]
    assert dpo_out[0]["chosen"][-1]["content"] == "好" and dpo_out[0]["rejected"][-1]["content"] == "差"

    # 语料/DPO 源缺失 → 对应文件不写、不报错
    counts2 = export_minimind(samples, tmp_path / "e2" / "sft.jsonl")
    assert counts2["pretrain"] == 0 and counts2["dpo"] == 0
    assert not (tmp_path / "e2" / "pretrain_t2t.jsonl").exists()
