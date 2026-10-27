"""Stage and speaker boundaries for authored outputs, never learner content."""
from __future__ import annotations

import json


def authored_stage_contract(prompt_id: str) -> str:
    """A short production reminder after the complete, unmodified dialogue.

    This is selected by a frozen recipe version. It specifies the envelope,
    not a mandatory order, number of steps or phrase in the training rationale.
    """
    if prompt_id == "workflow.cot_native_answer":
        return ("<authored_stage_contract version=\"1\" stage=\"answer\">\n"
                "本轮只交付对最新真实用户消息的最终回复，输出纯文本，不加 JSON 包装。"
                "已有完整推理通过原生前缀传入；不复述后台流程或把它当作独立验证。"
                "回答者的判断与建议属于回答者，用户将执行的操作仍属于用户；"
                "代用户拟写的第一人称报告或角色台词必须明确是代写片段。\n"
                "</authored_stage_contract>")
    if prompt_id not in {"workflow.cot_rationale", "workflow.cot_generate"}:
        raise ValueError("invalid_authored_stage_contract")
    envelope = {"reasoning": "新撰写的完整推理正文",
                "reasoning_trace": {"task": "当前目标", "premises": ["具体依据"],
                    "steps": [{"basis": "所用依据", "inference": "支持关系", "result": "实际结论"}],
                    "checks": [{"check": "实际核对的疑点", "result": "核对依据与结果"}],
                    "limitations": []}}
    if prompt_id == "workflow.cot_generate":
        envelope["answer"] = "最终回复"
    stage = "rationale" if prompt_id == "workflow.cot_rationale" else "combined"
    return (f'<authored_stage_contract version="1" stage="{stage}">\n'
            "以上真实 user/assistant 轮次是当前要回应的对话，历史助手回复不是本轮输出格式的示例。"
            "本轮生成新稿：仅输出一个完整 JSON 对象，不直接输出普通聊天回复，不输出 Markdown 包装。"
            "reasoning 是完整推理正文，不能仅在原生思考通道或 reasoning_trace 中完成后省略正文。"
            "引述用户的话保持原说话者；叙述中的‘我’是正在理解、推导与建议的回答者，"
            "用户将运行的实验或部署不能写成我去执行。代用户拟写的第一人称片段须明确归属。"
            "实际任务与自定义思考要点决定内容，不规定句式或推理顺序。"
            "下面仅示意字段和类型，占位说明不能复制为本题事实；数组按实际论证填写，"
            "basis/inference/result 均为字符串，具体校验仍以上方完整契约为准。\n"
            + json.dumps(envelope, ensure_ascii=False)
            + "\n</authored_stage_contract>")
