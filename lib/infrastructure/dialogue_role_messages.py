"""Separate private memory, editorial dispatch, and actual dialogue roles."""
from __future__ import annotations

from copy import deepcopy
import hashlib

from lib.domain.workflow_quality import canonical
from lib.infrastructure.source_memory_messages import source_memory_messages


def dispatch_history(payload, *, transport="tool"):
    body = canonical(payload)
    if transport == "system":
        return [{"role": "system", "content": "<internal_dialogue_dispatch>\n" + body + "\n</internal_dialogue_dispatch>"}]
    if transport != "tool":
        raise ValueError("invalid_source_memory_transport")
    identity = "dispatch_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
    return [{"role": "user", "content": "Read the workflow's dialogue dispatch."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": identity, "type": "function",
                "function": {"name": "dialogue_dispatch", "arguments": "{}"}}]},
            {"role": "tool", "name": "dialogue_dispatch", "tool_call_id": identity, "content": body}]


def visible_dialogue(messages):
    if not isinstance(messages, list) or not messages:
        raise ValueError("invalid_visible_dialogue")
    result = []
    for index, message in enumerate(messages):
        expected = "user" if index % 2 == 0 else "assistant"
        if (not isinstance(message, dict) or message.get("role") != expected
                or not isinstance(message.get("content"), str) or not message["content"].strip()):
            raise ValueError("invalid_visible_dialogue")
        result.append({"role": expected, "content": message["content"]})
    if result[-1]["role"] != "user":
        raise ValueError("invalid_visible_dialogue")
    return result


def answer_role_messages(system, memory_context, data, *, transport="tool"):
    """The final user is the learner's real utterance, never a task envelope."""
    rules = data.get("answer_rules", "")
    if rules:
        system += "\n本节点用户配置的回答规则：\n" + rules
    instruction = (memory_context or {}).get("task_payload", {}).get("node_task_instructions")
    if instruction:
        system += "\n本节点固定处理要求：\n" + instruction
    system += ("\nsource_memory 是内部事实记忆；dialogue_dispatch 是经工作流验证的设计简报，"
               "不属于用户说过的话。实际用户/助手消息是模型当前要回应的对话。"
               "只生成本轮回答及审计元数据，不改写或生成用户问题。")
    context = memory_context or {"teacher_context_pack": [], "task_payload": {}}
    if not context["teacher_context_pack"]:
        # SFT wraps its source; multi-turn requests carry task/provenance at
        # the top level. Both forms need the same private-memory boundary even
        # when manual or open-task inputs have no persistent document pack.
        source = data.get("source_context")
        if not isinstance(source, dict):
            source = data
        text = source.get("text") or source.get("task")
        if isinstance(text, str) and text.strip():
            kind = source.get("kind") or source.get("provenance", {}).get("kind", "synthetic")
            content = {"version": 1, "materials": [{"text": text, "source_kind": kind}],
                       "verification": "application_source_only_not_independent_fact_verification"}
            identity = hashlib.sha256(canonical(content).encode("utf-8")).hexdigest()
            context = {"teacher_context_pack": [{**content, "id": identity, "prefix_sha256": identity}],
                       "task_payload": {}}
    prefix = source_memory_messages(system, {**context, "task_payload": {}}, transport=transport)[:-1]
    contract = data.get("qa_contract", {})
    dispatch = {key: deepcopy(contract[key]) for key in (
        "qa_type", "answer_policy", "guidance", "dialogue_design") if key in contract}
    requirement = data.get("requirement", data.get("brief"))
    if requirement:
        dispatch["requirement"] = deepcopy(requirement)
    for key in ("generation_style", "feedback", "dialogue_state", "dialogue_step"):
        if data.get(key) is not None:
            dispatch[key] = deepcopy(data[key])
    if isinstance(data.get("dialogue_step"), dict) and data["dialogue_step"].get("response_policy"):
        dispatch["answer_policy"] = data["dialogue_step"]["response_policy"]
    return [*prefix, *dispatch_history(dispatch, transport=transport),
            *visible_dialogue(data["learner_messages"])]


def question_role_messages(system, data, *, transport="tool"):
    """Only a validated brief and public turns can cross to the questioner."""
    from lib.domain.workflow_qa_director import validate_question_brief
    if set(data) - {"question_brief", "visible_dialogue", "question_rules", "retry_code"}:
        raise ValueError("private_context_in_question_request")
    brief = validate_question_brief(data["question_brief"])
    payload = {"question_brief": brief}
    if data.get("visible_dialogue"):
        # A completed visible conversation ends with an assistant response.
        previous = data["visible_dialogue"]
        validated = visible_dialogue([*previous, {"role": "user", "content": "next turn"}])[:-1]
        payload["visible_dialogue"] = validated
    if data.get("retry_code"):
        payload["retry_code"] = data["retry_code"]
    system += "\n用户配置的提问规则：\n" + data.get("question_rules", "")
    system += ("\n你只扮演发言者，不掌握内部资料或参考答案。简报仅用于情境与语言行为指导。"
               "从实际可见对话承接下一行动；没有历史时生成自包含的首条消息。"
               "可以提出请求、表达困惑、协商约束或发问，不假称见过资料、不预写助手答案。")
    return [{"role": "system", "content": system},
            *dispatch_history(payload, transport=transport),
            {"role": "user", "content": '仅返回 {"message":"下一条用户消息"}。'}]
