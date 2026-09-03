"""DPO 偏好对增强管线：三种构造法 + 质量门槛。

  1. candidates：同 prompt 多候选采样（不同温度）→ judge.score 打分 → 分差 ≥2 才成对；
  2. refine：生成 v1 → dpo.refine 自反馈精炼 v2 → judge 对比 → v2 更优才成对；
  3. hallucinate：给定正确回答+事实依据 → dpo.hallucinate 生成隐蔽错误版 →
     judge 对比确认 rejected 更差才成对。
统一输出：{prompt, chosen, rejected, source}（chosen/rejected 为完整消息列表）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from lib.llm_client import chat_json
from lib.prompts import get, render

MIN_GAP = 2  # judge 分差门槛（correctness 维度），低于此不构成偏好对


def _judge(client: Any, prompt: str, answer: str) -> int:
    """judge.score 打分，返回 correctness（1-5）。"""
    out = chat_json(client, [{"role": "user", "content": render(
        get("judge.score"), goal=prompt, thinking="", final_answer=answer)}], temperature=0.2)
    try:
        return int(out.get("correctness", 1))
    except (TypeError, ValueError):
        return 1


def _answer(
    client: Any,
    prompt: str,
    temperature: float,
    thinking: bool = True,
    max_tokens: int | None = None,
) -> str:
    """生成一条回答（纯文本；json_mode 关闭）。"""
    return client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens, temperature=temperature, thinking=thinking,
    )


def pair_id(prompt: str, chosen: str, rejected: str) -> str:
    raw = json.dumps([prompt, chosen, rejected], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _mk_pair(prompt: str, chosen: str, rejected: str, source: str) -> Dict[str, Any]:
    return {
        "id": f"dpo-{pair_id(prompt, chosen, rejected)}",
        "source": source,
        "prompt": [{"role": "user", "content": prompt}],
        "chosen": [{"role": "assistant", "content": chosen}],
        "rejected": [{"role": "assistant", "content": rejected}],
    }


def candidates(
    client: Any,
    prompts: List[str],
    n_per_prompt: int = 3,
    extra_clients: List[Any] | None = None,
) -> List[Dict[str, Any]]:
    """多候选判分对比：单模型候选太一致（实测 flash 3 候选分差全 <2），
    故支持多模型采样（UltraFeedback 用 17 模型采样的同构做法），
    且末位候选强制截断（weak candidate，制造真实质量方差）。"""
    clients = [client] + list(extra_clients or [])
    pairs = []
    for p in prompts:
        answers: List[str] = []
        for i in range(n_per_prompt):
            c = clients[i % len(clients)]
            if i == n_per_prompt - 1:
                answers.append(_answer(c, p, 0.7, max_tokens=40))  # 截断弱候选
            else:
                answers.append(_answer(c, p, 0.5 + 0.35 * i))
        scored = sorted(((_judge(client, p, a), a) for a in answers), key=lambda x: x[0])
        low, high = scored[0], scored[-1]
        if high[0] - low[0] >= MIN_GAP:
            pairs.append(_mk_pair(p, high[1], low[1], "candidates"))
    return pairs


def refine(client: Any, prompts: List[str]) -> List[Dict[str, Any]]:
    pairs = []
    for p in prompts:
        v1 = _answer(client, p, 0.7)
        try:
            out = chat_json(client, [{"role": "user", "content": render(
                get("dpo.refine"), prompt=p, answer=v1)}], temperature=0.7)
        except Exception:  # noqa: BLE001
            continue
        v2 = out.get("refined", "").strip()
        if not v2 or v2 == v1:
            continue
        s1, s2 = _judge(client, p, v1), _judge(client, p, v2)
        if s2 - s1 >= 1:
            pairs.append(_mk_pair(p, v2, v1, "refine"))
    return pairs


def hallucinate(client: Any, items: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """items: [{prompt, answer(正确), facts}]。生成隐蔽错误版作 rejected。"""
    pairs = []
    for item in items:
        prompt, correct, facts = item["prompt"], item["answer"], item.get("facts", "")
        try:
            out = chat_json(client, [{"role": "user", "content": render(
                get("dpo.hallucinate"), prompt=prompt, facts=facts)}], temperature=0.9)
        except Exception:  # noqa: BLE001
            continue
        wrong = out.get("answer", "").strip()
        if not wrong or wrong == correct:
            continue
        s_correct, s_wrong = _judge(client, prompt, correct), _judge(client, prompt, wrong)
        if s_correct - s_wrong >= 1:
            pairs.append(_mk_pair(prompt, correct, wrong, "hallucinate"))
    return pairs


def merge_pairs(entries: List[Dict[str, Any]], manifest: set[str] | None = None) -> List[Dict[str, Any]]:
    """统一汇集：去重（id）+ 归一化 {prompt, chosen, rejected}。"""
    manifest = manifest if manifest is not None else set()
    out = []
    for e in entries:
        pid = e.get("id") or pair_id(
            json.dumps(e.get("prompt", []), ensure_ascii=False),
            json.dumps(e.get("chosen", []), ensure_ascii=False),
            json.dumps(e.get("rejected", []), ensure_ascii=False),
        )
        if pid in manifest:
            continue
        manifest.add(pid)
        out.append({"id": pid, "prompt": e["prompt"], "chosen": e["chosen"], "rejected": e["rejected"]})
    return out
