"""CoT 风格偏好调教管线：风格画像 → 软采样注入生成 → 风格校验 → SFT 样本 + 风格 DPO 对。

三层设计：
  1. 风格画像（configs/cot_styles.yaml）：自然语言偏好 → 风格清单 + 权重；
  2. 生成期软采样注入：按权重轮换风格块（防重复，呼应"偏好绝不硬塞每条"）；
  3. 风格偏好调教：同一任务生成"有风格 vs 无风格"两版思考 → cotstyle.check 打分
     → 符合度高者为 chosen、低者为 rejected（DPO 对，让模型长期习得风格）。
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, List

import yaml

from lib.llm_client import chat_json
from lib.prefs import PreferenceSampler
from lib.prompts import get, render

DEFAULT_STYLE = "默认风格"


def load_styles(path: str | pathlib.Path, profile: str = "default") -> Dict[str, Any]:
    cfg = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    return {
        "styles": cfg["styles"],
        "weights": cfg["profiles"][profile]["weights"],
    }


def style_block(description: str | None) -> str:
    return DEFAULT_STYLE if not description else f"风格：{description}"


def generate_thinking(client: Any, goal: str, annotated_steps: str, style: str | None) -> Dict[str, Any]:
    return chat_json(client, [{"role": "user", "content": render(
        get("distill.generator"),
        goal=goal, annotated_steps=annotated_steps, style_guide=style_block(style),
    )}], temperature=0.9)


def style_check(client: Any, goal: str, thinking: str, style_description: str) -> Dict[str, Any]:
    return chat_json(client, [{"role": "user", "content": render(
        get("cotstyle.check"),
        goal=goal, thinking=thinking, style_description=style_description,
    )}], temperature=0.2)


def run(
    client: Any,
    tasks: List[Dict[str, str]],
    styles_cfg: Dict[str, Any],
    seed: int = 42,
) -> Dict[str, Any]:
    """tasks: [{goal, annotated_steps}]。产出带风格 SFT 样本 + 风格 DPO 对。"""
    sampler = PreferenceSampler(styles_cfg["weights"], default_floor=0.0, seed=seed)
    styles = styles_cfg["styles"]
    samples: List[Dict[str, Any]] = []
    dpo_pairs: List[Dict[str, Any]] = []
    stats = {"tasks": len(tasks), "kept_styled": 0, "dpo_pairs": 0, "style_hits": 0}

    for task in tasks:
        style_name = sampler.sample(1)[0]
        description = styles[style_name]["description"]
        styled = generate_thinking(client, task["goal"], task["annotated_steps"], description)
        baseline = generate_thinking(client, task["goal"], task["annotated_steps"], None)

        check_styled = style_check(client, task["goal"], styled["thinking"], description)
        check_base = style_check(client, task["goal"], baseline["thinking"], description)

        # chosen = 风格符合度更高的一版；rejected = 另一版
        if check_styled.get("adherence", 0) >= check_base.get("adherence", 0):
            chosen, rejected = styled, baseline
            chosen_adherence = check_styled.get("adherence", 0)
        else:
            chosen, rejected = baseline, styled
            chosen_adherence = check_base.get("adherence", 0)

        if chosen_adherence >= 4:
            stats["style_hits"] += 1
            samples.append({
                "id": f"cotstyle-{style_name}-{len(samples)}",
                "source": "cotstyle",
                "type": "sft",
                "style": style_name,
                "messages": [
                    {"role": "user", "content": task["goal"]},
                    {"role": "assistant", "content": chosen["final_answer"],
                     "reasoning_content": chosen["thinking"]},
                ],
                "style_check": check_styled if chosen is styled else check_base,
            })
        # 风格 DPO 对：prompt=任务，chosen/rejected=两版完整回答（思维链+最终回答）
        if abs(check_styled.get("adherence", 0) - check_base.get("adherence", 0)) >= 2:
            stats["dpo_pairs"] += 1
            dpo_pairs.append({
                "style": style_name,
                "prompt": [{"role": "user", "content": task["goal"]}],
                "chosen": [{"role": "assistant", "content": chosen["final_answer"],
                            "reasoning_content": chosen["thinking"]}],
                "rejected": [{"role": "assistant", "content": rejected["final_answer"],
                              "reasoning_content": rejected["thinking"]}],
            })
    stats["kept_styled"] = len(samples)
    return {"samples": samples, "dpo_pairs": dpo_pairs, "stats": stats}
