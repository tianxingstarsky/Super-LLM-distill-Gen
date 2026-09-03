"""M0 真实数据 spike：使用官方 DeepSeek V4 Flash。

A. Magpie 链真实生成（小预算 10 条）
B. 空 user 回合探测（F3 待验证点：API 是否接受空 user content）
C. 蒸馏演示：chatlog fixture → 文本化 Reflector/Generator/Summarizer → 样例

运行：.venv/Scripts/python.exe scripts/real_spike.py
密钥来源：configs/backends.local.yaml（gitignored，勿提交）
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from lib.adapters.chatlog_to_traj import turns_to_traj
from lib.adapters.distill_prompts import (
    GENERATOR_TEXT_PROMPT,
    MAGPIE_QUERY_SYSTEM_PROMPT,
    REFLECTOR_TEXT_PROMPT,
    SUMMARIZER_TEXT_PROMPT,
)

OUT_DIR = ROOT / "data" / "output" / "spike_real"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 读取本地配置 ────────────────────────────────────────────────────────────
local_cfg = yaml.safe_load((ROOT / "configs" / "backends.local.yaml").read_text(encoding="utf-8"))
ds = local_cfg["backends"]["deepseek"]
BASE_URL = ds["base_url"]
API_KEY = ds["api_key"]
MODEL = "deepseek-v4-flash"

os.environ["OPENAI_API_KEY"] = API_KEY
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

from openai import OpenAI  # noqa: E402

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
TOTAL_TOKENS = {"in": 0, "out": 0}


def chat(messages: list[dict], max_tokens: int = 1024, temperature: float = 0.7) -> str:
    """带空回复重试的调用（真实 API 偶发空 completion，spike 已验证）。"""
    last_err: Exception | None = None
    for _ in range(3):
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=messages, max_tokens=max_tokens, temperature=temperature
            )
            if resp.usage:
                TOTAL_TOKENS["in"] += resp.usage.prompt_tokens or 0
                TOTAL_TOKENS["out"] += resp.usage.completion_tokens or 0
            content = (resp.choices[0].message.content or "").strip()
            if content:
                return content
            last_err = ValueError("empty completion")
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(1.0)
    raise RuntimeError(f"chat failed after retries: {last_err}")


# ── B. 空 user 回合探测（先跑，记录结论；A 已改用新配方） ─────────────────
MAGPIE_SYSTEM = (
    "A chat between a curious user and an artificial intelligence assistant. "
    "The assistant gives helpful, detailed, and polite answers to the user's questions."
)
print("== B: 空 user 回合探测 ==")
probe_results = {}
for label, user_content in [("empty", ""), ("space", " ")]:
    try:
        out = chat(
            [{"role": "system", "content": MAGPIE_SYSTEM}, {"role": "user", "content": user_content}],
            max_tokens=128,
        )
        probe_results[label] = {"ok": True, "sample": out[:80]}
        print(f"  {label}: 接受，样例={out[:60]!r}")
    except Exception as e:
        probe_results[label] = {"ok": False, "error": str(e)[:200]}
        print(f"  {label}: 拒绝 → {str(e)[:120]}")

# ── A. Magpie 链真实生成（API 适配版：扮演好奇用户生成提问） ──────────────
print("== A: Magpie 链真实生成（API 适配版，10 条） ==")
instructions = []
for i in range(10):
    try:
        out = chat(
            [{"role": "system", "content": MAGPIE_QUERY_SYSTEM_PROMPT}, {"role": "user", "content": "Generate a question."}],
            max_tokens=256,
            temperature=1.0,
        )
        instructions.append(out.strip())
        print(f"  [{i+1:2d}] {out.strip()[:70]}")
    except Exception as e:
        instructions.append("")
        print(f"  [{i+1:2d}] 失败（重试 3 次后跳过）: {str(e)[:60]}")
    time.sleep(0.3)
instructions = [x for x in instructions if x]
(OUT_DIR / "magpie_instructions.jsonl").write_text(
    "\n".join(json.dumps({"instruction": x}, ensure_ascii=False) for x in instructions),
    encoding="utf-8",
)

# ── C. 蒸馏演示（fixture 2 个任务） ─────────────────────────────────────────
print("== C: 蒸馏演示（Reflector → Generator → Summarizer） ==")
fixture = ROOT / "tests" / "fixtures" / "chatlog_sample.jsonl"
samples = []
for line in fixture.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    rec = turns_to_traj(json.loads(line))
    steps_desc = []
    for s in rec["traj"]:
        code = s["value"]["code"]
        meta = s.get("meta", {})
        tag = " [错误]" if meta.get("error") else (" [用户纠正]" if meta.get("feedback") else "")
        steps_desc.append(f"- {code}{tag}")
    history = "\n".join(steps_desc[:-1]) or "（无）"
    last = steps_desc[-1] if steps_desc else "（无）"

    try:
        reflect = chat(
            [{"role": "user", "content": REFLECTOR_TEXT_PROMPT.format(
                goal=rec["instruction"], history_steps=history, last_step=last)}],
            max_tokens=256, temperature=0.2,
        )
    except Exception as e:
        reflect = f"ERROR: {e}"
    try:
        gen = chat(
            [{"role": "user", "content": GENERATOR_TEXT_PROMPT.format(
                goal=rec["instruction"], annotated_steps="\n".join(steps_desc))}],
            max_tokens=1024, temperature=0.7,
        )
    except Exception as e:
        gen = f"ERROR: {e}"
    try:
        summ = chat(
            [{"role": "user", "content": SUMMARIZER_TEXT_PROMPT.format(
                goal=rec["instruction"], thinking=gen[:1500], final_answer=gen[:1500])}],
            max_tokens=256, temperature=0.2,
        )
    except Exception as e:
        summ = f"ERROR: {e}"
    samples.append({
        "task_id": rec["task_id"], "instruction": rec["instruction"],
        "reflector": reflect, "generator": gen, "summarizer": summ,
    })
    print(f"  任务 {rec['task_id']}")
    print(f"    Reflector: {reflect[:100]!r}")
    print(f"    Generator 开头: {gen[:120]!r}")
    print(f"    Summarizer: {summ[:100]!r}")

(OUT_DIR / "distill_samples.jsonl").write_text(
    "\n".join(json.dumps(s, ensure_ascii=False) for s in samples), encoding="utf-8"
)

print(f"\n== 汇总：prompt_tokens={TOTAL_TOKENS['in']} completion_tokens={TOTAL_TOKENS['out']}")
print(f"== 产物目录: {OUT_DIR}")
