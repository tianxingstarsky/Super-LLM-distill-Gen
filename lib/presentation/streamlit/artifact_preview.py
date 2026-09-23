"""Human-readable previews for verified training artifact records."""
from __future__ import annotations

import html
import json
from typing import Any

from lib.render import render_message_sequence
from lib.presentation.streamlit.artifact_preview_style import ARTIFACT_PREVIEW_STYLE


_TARGET_TITLES = {
    "cpt": "预训练语料",
    "sft": "指令与多轮对话",
    "multiturn": "多轮对话与上下文",
    "agent": "Agent 工具轨迹",
    "agent_negative": "Agent 失败轨迹",
    "dpo": "偏好对照",
    "orpo": "ORPO 偏好对",
    "rlaif": "AI 反馈与排序",
    "gsm8k": "算术推理题",
    "cot": "可见推理解释",
}


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _messages(value: Any) -> list[dict]:
    return [message for message in value if isinstance(message, dict)] if isinstance(value, list) else []


def _conversation(title: str, value: Any) -> str:
    messages = _messages(value)
    if not messages:
        return '<div class="df-artifact-muted">暂无对话消息</div>'
    return (
        '<div class="df-artifact-conversation">'
        f'<div class="df-artifact-subhead">{_safe(title)}<span>{len(messages)} 条消息</span></div>'
        '<div class="bubbles">' + render_message_sequence(messages) + '</div></div>'
    )


def _tool_result_user(message: dict) -> bool:
    """Anthropic tool results are carried in a user-role content block."""
    return (message.get("role") == "user" and isinstance(message.get("content"), list)
            and any(isinstance(block, dict) and block.get("type") == "tool_result"
                    for block in message["content"]))


def _tool_calls(message: dict) -> list[dict]:
    calls = message.get("toolCalls") or message.get("tool_calls") or []
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except ValueError:
            calls = []
    if isinstance(calls, dict):
        calls = [calls]
    result = [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []
    content = message.get("content")
    if isinstance(content, list):
        result.extend(block for block in content
                      if isinstance(block, dict) and block.get("type") in {"tool_use", "tool_call"})
    return result


def _tool_call_names(message: dict) -> list[str]:
    names = []
    for call in _tool_calls(message):
        function = call.get("function") if isinstance(call.get("function"), dict) else call
        name = function.get("name") or call.get("toolName")
        if name:
            names.append(str(name))
    return names


def _tool_call_ids(message: dict) -> list[str]:
    return [str(call_id) for call in _tool_calls(message)
            if (call_id := call.get("id") or call.get("toolCallId") or call.get("tool_call_id"))]


def _turn_card(number: int | None, messages: list[dict], *, context: bool = False) -> str:
    heading = "上下文指令" if context else f"第 {number:02d} 轮对话"
    marker = "•" if context else f"{number:02d}"
    if context:
        content = '<div class="df-artifact-turn-body"><div class="bubbles">' + render_message_sequence(messages) + '</div></div>'
    else:
        question = messages[:1] if messages and messages[0].get("role") == "user" else []
        answer = messages[len(question):]
        content = (
            '<div class="df-artifact-turn-body"><div class="df-artifact-turn-columns">'
            '<div class="df-artifact-turn-panel" data-role="input">'
            '<div class="df-artifact-panel-label"><b>问</b><span>本轮输入</span></div>'
            '<div class="bubbles">' + render_message_sequence(question) + '</div></div>'
            '<div class="df-artifact-turn-panel" data-role="output">'
            '<div class="df-artifact-panel-label"><b>答</b><span>助手回复与工具过程</span></div>'
            '<div class="bubbles">' + (render_message_sequence(answer) if answer else
                                      '<div class="df-artifact-muted">该轮尚无回复</div>')
            + '</div></div></div></div>'
        )
    return (
        f'<section class="df-artifact-turn" data-kind="{"context" if context else "turn"}">'
        '<div class="df-artifact-turn-head">'
        f'<span class="df-artifact-turn-number">{marker}</span><strong>{heading}</strong>'
        f'<small>{len(messages)} 条消息</small></div>'
        + content + '</section>'
    )


def _dialogue_flow(messages: list[dict]) -> str:
    """Group a recorded dialogue at actual user-turn boundaries."""
    context: list[dict] = []
    turns: list[list[dict]] = []
    for message in messages:
        if message.get("role") == "user" and not _tool_result_user(message):
            turns.append([message])
        elif turns:
            turns[-1].append(message)
        else:
            context.append(message)
    if not messages:
        return '<div class="df-artifact-muted">暂无对话消息</div>'
    cards = [_turn_card(None, context, context=True)] if context else []
    if not turns:
        return '<div class="df-artifact-flow">' + "".join(cards) + '</div>'
    cards.extend(_turn_card(index, turn) for index, turn in enumerate(turns[:3], start=1))
    if len(turns) > 3:
        later = "".join(_turn_card(index, turn) for index, turn in enumerate(turns[3:], start=4))
        cards.append(f'<details class="df-artifact-more"><summary>展开后续 {len(turns) - 3} 轮对话</summary>'
                     f'<div>{later}</div></details>')
    return ('<div class="df-artifact-flow"><div class="df-artifact-flow-head">'
            f'<strong>对话过程</strong><span>{len(turns)} 轮 · {len(messages)} 条消息</span></div>'
            + "".join(cards) + '</div>')


def _trace_flow(messages: list[dict], *, failure_step: int | None = None,
                verified_call_ids: set[str] | None = None) -> str:
    """Keep tool-call and matching result adjacent in a readable step timeline."""
    if not messages:
        return '<div class="df-artifact-muted">暂无轨迹消息</div>'
    steps = []
    index = 0
    while index < len(messages):
        message = messages[index]
        names = _tool_call_names(message) if message.get("role") == "assistant" else []
        end = index + 1
        if names:
            while end < len(messages) and (messages[end].get("role") == "tool"
                                           or _tool_result_user(messages[end])):
                end += 1
            kind, label = "tool", "工具调用"
            title = "、".join(dict.fromkeys(names))
        elif message.get("role") == "user" and not _tool_result_user(message):
            kind, label, title = "input", "任务输入", "用户目标"
        elif message.get("role") == "assistant":
            kind, label, title = "answer", "模型响应", "助手输出"
        elif message.get("role") == "tool" or _tool_result_user(message):
            kind, label, title = "tool", "工具返回", "未配对的结果"
        else:
            kind, label, title = "context", "运行上下文", str(message.get("role") or "其他消息")
        failed = isinstance(failure_step, int) and index <= failure_step < end
        call_ids = _tool_call_ids(message) if names else []
        verified = bool(call_ids and verified_call_ids
                        and all(call_id in verified_call_ids for call_id in call_ids) and not failed)
        if failed:
            status = "失败截断点"
        elif verified:
            status = "本地重放已核对"
        elif names and end == index + 1:
            status = "未记录工具返回"
        elif names:
            status = f"调用与返回 · {end - index} 条消息"
        else:
            status = f"{end - index} 条消息"
        card = (
            f'<div class="df-artifact-trace-item" data-kind="{kind}" data-failed="{str(failed).lower()}"'
            f' data-verified="{str(verified).lower()}">'
            f'<span class="df-artifact-trace-marker">{"!" if failed else len(steps) + 1}</span>'
            '<section class="df-artifact-trace-card"><div class="df-artifact-trace-head">'
            f'<b>{label}</b><strong>{_safe(title)}</strong>'
            f'<small>{_safe(status)}</small></div>'
            '<div class="df-artifact-trace-body"><div class="bubbles">'
            + render_message_sequence(messages[index:end]) + '</div></div></section></div>'
        )
        steps.append(card)
        index = end
    return ('<div class="df-artifact-flow"><div class="df-artifact-flow-head">'
            f'<strong>Agent 执行轨迹</strong><span>{len(steps)} 个步骤 · {len(messages)} 条消息</span></div>'
            '<div class="df-artifact-trace">' + "".join(steps) + '</div></div>')


def _agent_verification(row: dict) -> str:
    verification = row.get("verification")
    if not isinstance(verification, dict):
        return ""
    method = verification.get("method")
    labels = {
        "bounded_arithmetic_replay": "本地算术重放",
        "snapshot_json_pointer_replay": "JSON 快照重放",
        "bounded_local_replay": "受限本地重放",
        "isolated_docker_ledger_replay": "隔离容器重放",
        "mixed_verified_tool_replay": "混合工具重放",
    }
    verified = verification.get("verified_call_ids")
    pruned = verification.get("pruned_call_ids")
    turns = verification.get("verified_turns")
    verified_count = len(verified) if isinstance(verified, list) else 0
    pruned_count = len(pruned) if isinstance(pruned, list) else 0
    turn_count = len(set(turns)) if isinstance(turns, list) else 0
    facts = [labels.get(method, f"校验记录：{method or '未注明'}"),
             f"{verified_count} 次调用已核对", f"{pruned_count} 组重复调用已剪枝"]
    if turn_count:
        facts.append(f"{turn_count} 轮回答已核对")
    return '<div class="df-artifact-verification"><b>重放校验</b>' + ''.join(
        '<span>' + _safe(fact) + '</span>' for fact in facts) + '</div>'


_FAILURE_LABELS = {
    "recorded_tool_error": "来源记录的工具执行失败",
    "tool_observation_mismatch": "工具结果与本地重放不一致",
    "final_answer_mismatch": "最终答案与已验证结果不一致",
    "intermediate_answer_mismatch": "中间轮回答与已验证结果不一致",
    "terminal_state_mismatch": "终态与隔离重放结果不一致",
    "container_replay_unavailable": "隔离容器不可用",
}


def _failure_banner(row: dict) -> str:
    reason = str(row.get("failure") or "执行失败")
    message_index = row.get("failure_step")
    location = row.get("source_location")
    if isinstance(location, (list, dict)):
        location = json.dumps(location, ensure_ascii=False, default=str)
    details = []
    if isinstance(message_index, int):
        details.append(f"第 {message_index + 1} 条消息（索引 {message_index}）")
    if location:
        details.append(f"来源：{str(location)[:180]}")
    suffix = f' <small>{_safe(" · ".join(details))}</small>' if details else ""
    return ('<div class="df-artifact-failure"><strong>'
            + _safe(_FAILURE_LABELS.get(reason, "失败轨迹")) + '</strong>'
            + _safe(reason) + suffix + '</div>')


def _text(label: str, value: Any, modifier: str = "") -> str:
    content = _safe(value) if value is not None else ""
    return (
        f'<div class="df-artifact-text {modifier}">'
        f'<div class="df-artifact-subhead">{_safe(label)}</div>'
        f'<div class="df-artifact-body">{content or "（空内容）"}</div></div>'
    )


def render_training_sample(target: str, row: dict) -> str:
    """Project one training record into escaped, comparison-friendly HTML."""
    target = str(target).lower()
    title = _TARGET_TITLES.get(target, "训练样本")
    header = (
        ARTIFACT_PREVIEW_STYLE + '<article class="df-artifact-preview">'
        '<div class="df-artifact-heading">'
        f'<span class="df-artifact-badge" data-target="{_safe(target)}">{_safe(target.upper())}</span>'
        f'<strong>{_safe(title)}</strong></div>'
    )
    if not isinstance(row, dict):
        return header + _text("样本内容", row) + '</article>'
    if target in {"sft", "multiturn", "agent", "agent_negative"}:
        messages = _messages(row.get("messages"))
        tool_calls = sum(len(_tool_call_names(message)) for message in messages)
        user_turns = sum(message.get("role") == "user" and not _tool_result_user(message)
                         for message in messages)
        facts = [f"{len(messages)} 条消息", f"{user_turns} 轮用户交互"]
        if tool_calls:
            facts.append(f"{tool_calls} 次工具调用")
        if isinstance(row.get("tools"), list):
            facts.append(f"{len(row['tools'])} 个工具定义")
        meta = '<div class="df-artifact-meta">' + ''.join(
            f'<span>{_safe(fact)}</span>' for fact in facts) + '</div>'
        if target in {"agent", "agent_negative"}:
            failure_step = row.get("failure_step") if target == "agent_negative" else None
            verification = row.get("verification")
            verified_ids = (set(map(str, verification.get("verified_call_ids", [])))
                            if isinstance(verification, dict)
                            and isinstance(verification.get("verified_call_ids"), list) else None)
            body = meta + _agent_verification(row)
            if target == "agent_negative":
                body += _failure_banner(row)
            body += _trace_flow(messages, failure_step=failure_step, verified_call_ids=verified_ids)
        else:
            body = meta + _dialogue_flow(messages)
            if target == "multiturn":
                reviews = row.get("turn_reviews")
                if isinstance(reviews, list):
                    body += ('<div class="df-artifact-verification">'
                             f'<span>{len(reviews)} 轮模型评估记录</span>'
                             '<span>事实未独立核验</span></div>')
        if target == "agent_negative":
            if row.get("evidence"):
                evidence = json.dumps(row["evidence"], ensure_ascii=False, indent=2, default=str)[:6000]
                body += _text("执行证据", evidence)
    elif target in {"dpo", "orpo"}:
        body = (
            _conversation("共同提示上下文", row.get("prompt"))
            + '<div class="df-artifact-compare">'
            + '<div class="df-artifact-chosen">' + _conversation("更优回答 · chosen", row.get("chosen")) + '</div>'
            + '<div class="df-artifact-rejected">' + _conversation("对照回答 · rejected", row.get("rejected")) + '</div>'
            + '</div>'
        )
    elif target == "rlaif":
        responses = row.get("responses") if isinstance(row.get("responses"), list) else []
        cards = []
        for index, response in enumerate(responses[:4]):
            if not isinstance(response, dict):
                continue
            rank = response.get("preference_rank", index + 1)
            score = response.get("score", "—")
            cards.append(
                '<div class="df-artifact-feedback">'
                f'<div class="df-artifact-subhead">候选 {index + 1} · 排名 {_safe(rank)}'
                f'<span>AI 评分 {_safe(score)}</span></div>'
                + _conversation("候选回答", response.get("response"))
                + _text("AI 评语", response.get("feedback", ""))
                + '</div>'
            )
        body = _conversation("共同提示上下文", row.get("prompt")) + '<div class="df-artifact-compare">' + "".join(cards) + '</div>'
    elif target == "cpt":
        body = _text("连续训练语料", row.get("text", ""))
    elif target == "gsm8k":
        body = _text("题目", row.get("question", "")) + _text("计算与答案", row.get("answer", ""), "df-artifact-answer")
    elif target == "cot":
        steps = row.get("reasoning")
        steps = steps if isinstance(steps, list) else [steps] if steps else []
        reasoning = ''.join(
            f'<div class="df-artifact-step"><b>{index:02d}</b><span>{_safe(step)}</span></div>'
            for index, step in enumerate(steps, start=1)
        )
        body = (_conversation("问题上下文", row.get("question"))
                + '<div class="df-artifact-reasoning"><div class="df-artifact-subhead">可见推理步骤</div>'
                + (reasoning or '<div class="df-artifact-muted">暂无推理步骤</div>') + '</div>'
                + _text("最终答案", row.get("answer", ""), "df-artifact-answer"))
    else:
        raw = json.dumps(row, ensure_ascii=False, indent=2, default=str)[:10000]
        body = _text("结构化内容", raw)
    return header + body + '</article>'
