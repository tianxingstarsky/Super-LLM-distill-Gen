"""Narrow checks of text actually exported for training.

These checks catch explicit source/process references. They do not establish
semantic grounding or prove that a generated sample contains no information
leakage; an independent review of the actual training messages is still needed.
"""
from __future__ import annotations

from collections.abc import Mapping
import re


TRAINING_SURFACE_PROTOCOL_VERSION = 2

_SOURCE_REFERENCE = re.compile(
    r"(?:核对|检查|确认)[：:][^。！？\n]{0,200}(?:按|与)(?:资料|材料|原文|文档)"
    r"(?:逐项|逐条|完全)?(?:对齐|一致|核对)|"
    r"(?:根据|依据|按照|参照|参考|结合)(?:所给的?|所提供的?|提供的?|给定的?|给出的?|上述|以上)"
    r"(?:资料|材料|文档|上下文|信息|内容)"
    r"|(?:所给的?|所提供的?|提供的|给定的)(?:资料|材料|文档)(?:中|里)?(?:指出|提到|显示|说明|可知|表明)"
    r"|(?:这份|这段|这块|这些)(?:资料|材料|文档|上下文)(?:里面|里|中|内)?"
    r"(?:并)?(?:没有|未给出|未提供|未提及|未涉及|不包含|指出|提到|显示|说明|记载|规定|明确)"
    r"|(?:according\s+to|based\s+on|from)\s+(?:the\s+)?(?:provided|given|supplied)\s+"
    r"(?:materials?|documents?|context|information|sources?)",
    re.IGNORECASE,
)
_PRIVATE_REFERENCE = re.compile(
    r"(?:教师|老师)(?:模型)?(?:提供的?|给出的?|隐藏的?|私有的?)?(?:资料|来源|证据|参考|记忆)"
    r"|(?:内部|隐藏|私有)(?:参考)?(?:记忆|资料|证据|上下文|知识库)"
    r"|(?:根据|依据|参照|结合)(?:知识库|检索)(?:的)?(?:检索)?(?:结果|资料|内容)"
    r"|(?:teacher|private|hidden|internal)\s+(?:reference\s+)?(?:evidence|memory|sources?|context)"
    r"|(?:according\s+to|based\s+on|from)\s+(?:the\s+)?(?:retrieval|retrieved|search)\s+results?",
    re.IGNORECASE,
)
_INTERNAL_FIELD = re.compile(
    r"\b(?:source_memory|dialogue_dispatch|teacher_evidence(?:_blocks)?|qa_contract|"
    r"reasoning_trace(?:_content_sha256|_status)?|generation_trace(?:_content_sha256|_scope)?|"
    r"source_context_pack|source_line_ranges|evidence_pack_sha256|review_feedback|"
    r"training_surface_protocol_version|generation_style)\b", re.IGNORECASE,
)
_PROCESS_CUE = re.compile(
    r"(?:我|我们|本次|当前|接下来|首先|现在).{0,40}(?:读取|检查|查看|遵循|参考|提取|解析|调用|生成|输出)"
    r"|(?:根据|依据|按照|遵循|读取|查看|检查|提取|解析)\s*[`\"']?(?:"
    r"source_memory|dialogue_dispatch|teacher_evidence|qa_contract|reasoning_trace|generation_trace)"
    r"|\b(?:I|we)\s+(?:will\s+|must\s+|should\s+|have\s+)?(?:read|check|inspect|follow|use|extract|generate)\b"
    r"|\b(?:based\s+on|according\s+to|following)\s+(?:the\s+)?(?:"
    r"source_memory|dialogue_dispatch|teacher_evidence|qa_contract|reasoning_trace|generation_trace)\b",
    re.IGNORECASE,
)
_EXPLANATION_CUE = re.compile(
    r"(?:措辞|表达|说法|术语|字段|含义|意思|引用|示例|不应|不要|避免)"
    r"|\b(?:wording|phrase|field|term|means|refers|example|avoid|should\s+not)\b", re.IGNORECASE,
)
_HIDDEN_PROTOCOL = re.compile(
    r"(?:系统|开发者)提示(?:词)?(?:中|里)?(?:要求|规定|让我)"
    r"|指导员(?:要求我|分配|安排)|遵循\s*[`\"']?qa_contract"
    r"|为了满足(?:本次)?(?:生成|评分|风格)(?:的)?要求"
    r"|(?:system|developer)\s+prompt\s+(?:requires|instructs\s+me|tells\s+me)"
    r"|(?:to\s+satisfy|to\s+meet)\s+(?:the\s+)?(?:generation|scoring|style)\s+requirements",
    re.IGNORECASE,
)
_TECHNICAL_TASK = re.compile(
    r"(?:什么|如何|为什么|解释|介绍|讨论|区别|作用|含义|设计|原理)"
    r"|\b(?:what|how|explain|describe|why)\b", re.IGNORECASE,
)
_VISIBLE_ARTICLE_BLOCK = re.compile(
    r"(?:\A|\n)\s*(?:资料|文章|文档|材料|阅读材料|article|passage|source\s+(?:text|material))"
    r"\s*[：:]\s*(?P<article>.+?)\n\s*(?:用户消息|用户问题|问题|user\s+message|question)\s*[：:]",
    re.IGNORECASE | re.DOTALL,
)
_RAG_TOPIC = re.compile(r"\bRAG\b|检索增强(?:生成)?|retrieval[- ]augmented(?:\s+generation)?", re.IGNORECASE)
_RAG_SYSTEM_SUBJECT = re.compile(
    r"(?:RAG|检索增强(?:生成)?)(?:\s*(?:系统|模型|生成器|管线))"
    r"|\b(?:RAG|retrieval[- ]augmented(?:\s+generation)?)\s+(?:systems?|models?|generators?|pipelines?)\b",
    re.IGNORECASE,
)
_RETRIEVAL_REFERENCE = re.compile(
    r"(?:根据|依据|参照|结合)(?:知识库|检索)(?:的)?(?:检索)?(?:结果|资料|内容)"
    r"|(?:according\s+to|based\s+on|from)\s+(?:the\s+)?(?:retrieval|retrieved|search)\s+results?",
    re.IGNORECASE,
)
_CURRENT_RESPONSE_ACTOR = re.compile(
    r"(?:我|我们|本次|当前|给你|为你)|\b(?:I|we|my|our|this\s+(?:answer|response)|for\s+you)\b",
    re.IGNORECASE,
)

# Version 2 treats provider reasoning as training text whenever it is exported.
# These are private production field names, not broad words such as JSON,
# prompt, quote, tool, reasoning or step. A visible technical task can discuss
# these fields; an unrelated answer cannot disclose the generator's wrappers.
_PRODUCTION_FIELD = re.compile(
    r"\b(?:source_memory|dialogue_dispatch|teacher_evidence(?:_blocks)?|qa_contract|"
    r"generation_style|answer_policy|dialogue_design|expression_design|"
    r"source_context_pack|source_line_ranges|evidence_pack_sha256|"
    r"training_surface_protocol_version|review_feedback|reasoning_trace(?:_status)?|"
    r"generation_trace(?:_scope)?)\b", re.IGNORECASE,
)
_PRIVATE_PROMPT_SELF = re.compile(
    r"(?:系统|开发者)(?:提示词?|指令)(?:中|里)?(?:说|写|要求|规定|让我)"
    r"|(?:隐藏|内部)(?:提示词?|生成规则|输出规则|提示指令)(?:中|里)?(?:说|写|要求|规定|让我)"
    r"|(?:本次|当前|我的)(?:提示词?|生成协议|输出协议|评分协议)"
    r"|\b(?:my|our|this)\s+(?:(?:system|developer|hidden|internal)\s+)?"
    r"(?:prompt|generation\s+protocol|output\s+protocol)\b"
    r"|\bthe\s+(?:system|developer)\s+(?:prompt|instructions?)\s+(?:says?|tells?|requires?)\b",
    re.IGNORECASE,
)
_PRIVATE_AUDIT_SCHEMA = re.compile(
    r"\b(?:quotes|uncertain|evidence_quotes)\b.{0,70}"
    r"(?:私有|内部|审计元数据|private\s+audit|internal\s+(?:audit|metadata))"
    r"|(?:私有|内部|private|internal).{0,35}(?:审计|audit).{0,35}\b(?:quotes|uncertain)\b",
    re.IGNORECASE,
)
_OUTPUT_SCHEMA_COMMAND = re.compile(
    r"(?:\b(?:need|must|should|will|let(?:'s| us))\b.{0,45}"
    r"\b(?:return|output|produce|emit)\b.{0,20}\bJSON\b"
    r"|\b(?:need|must|should)\s+(?:valid\s+)?JSON\b"
    r"|(?:仅|只|需要|必须|按要求)(?:返回|输出).{0,15}JSON)", re.IGNORECASE,
)
_SCHEMA_KEYS = re.compile(r"\b(?:answer|quotes|uncertain)\b", re.IGNORECASE)
_SELECTED_PRIVATE_BLOCK = re.compile(
    r"(?:quotes?|引文|引用).{0,70}(?:selected\s+(?:knowledge|evidence)\s+blocks|"
    r"选定的?(?:知识|证据)块|实际选中(?:的)?块)", re.IGNORECASE,
)


def _production_protocol_reference(sentence, visible):
    """Recognize concrete generator wrappers without banning technical prose."""
    fields = list(_PRODUCTION_FIELD.finditer(sentence))
    for field in fields:
        # The actual question/source must contain the exact field. Neither
        # assistant text nor audit metadata can make it learner-visible.
        if field.group(0).casefold() not in visible.casefold():
            return True
    if _PRIVATE_AUDIT_SCHEMA.search(sentence):
        if not (any(term in visible.casefold() for term in ("quotes", "uncertain", "evidence_quotes"))
                and _TECHNICAL_TASK.search(visible) and not _PRIVATE_PROMPT_SELF.search(sentence)):
            return True
    if _PRIVATE_PROMPT_SELF.search(sentence):
        # Explaining a user-supplied quotation is valid, but "my system prompt"
        # is a disclosure of the current execution even in a prompt tutorial.
        quoted = (sentence.strip().casefold() in visible.casefold()
                  and _EXPLANATION_CUE.search(sentence) and not _PROCESS_CUE.search(sentence))
        general_discussion = (_TECHNICAL_TASK.search(visible)
                              and re.search(r"系统提示|开发者提示|system\s+prompt|developer\s+prompt", visible, re.I)
                              and not re.search(r"(?:我|本次|当前)|\b(?:I|we|my|our|this)\b", sentence, re.I))
        if not (quoted or general_discussion):
            return True
    if _OUTPUT_SCHEMA_COMMAND.search(sentence) and len(set(
            key.group(0).strip("\"'`").casefold() for key in _SCHEMA_KEYS.finditer(sentence))) >= 2:
        if not re.search(r"\bJSON\b|输出格式|返回格式|数据格式", visible, re.I):
            return True
    if _SELECTED_PRIVATE_BLOCK.search(sentence):
        if not re.search(r"selected\s+(?:knowledge|evidence)\s+blocks|(?:知识|证据)块|引文|引用|quote", visible, re.I):
            return True
    return False


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(part for part in value if isinstance(part, str))
    return ""


def _visible_text(messages):
    """Read only learner-visible input bodies; assistant/audit fields cannot grant access."""
    parts = []
    for message in messages if isinstance(messages, (list, tuple)) else ():
        if not isinstance(message, Mapping) or message.get("role") not in {"user", "tool"}:
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(part["text"] for part in content if isinstance(part, Mapping)
                         and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str))
    return "\n".join(parts)


def _article_is_visible(visible, contract):
    if contract is None:
        # Historical document tasks export an explicit article/question body
        # without a director contract. Merely claiming "there is an article"
        # grants no exception; actual nontrivial text must be in that body.
        return any(len(re.sub(r"\s+", "", match.group("article"))) >= 16
                   for match in _VISIBLE_ARTICLE_BLOCK.finditer(visible))
    if not isinstance(contract, Mapping) or contract.get("qa_type") not in {
            "grounded", "multi_source", "distractor", "partial"}:
        return False
    article = contract.get("visible_context")
    return isinstance(article, str) and bool(article.strip()) and article.strip() in visible


def training_surface_issue(reasoning, answer, *, visible_messages=(), contract=None, protocol_version=1,
                           knowledge_application_voice_version=None, learning_goal=None):
    """Return a stable issue code for explicit leakage in exported output text.

    ``contract`` is not evidence by itself: source references are allowed only
    when its article is present in actual learner messages. Technical discussion
    of prompt/RAG fields remains valid; self-descriptions of this generation's
    private wrappers do not. Input audit dictionaries are outside this surface.
    Version 1 preserves historical authored-only callers. Version 2 also checks
    native provider reasoning when the caller is about to export that text;
    its provenance never grants permission to leak the production protocol.
    """
    visible = _visible_text(visible_messages)
    article_visible = _article_is_visible(visible, contract)
    from lib.domain.knowledge_voice import knowledge_voice_rules, knowledge_voice_sentence_issue
    voice_active = knowledge_application_voice_version == 3 and bool(knowledge_voice_rules(learning_goal, contract))
    for text in (_text(reasoning), _text(answer)):
        for sentence in re.split(r"[。！？!?；;\n]+|(?<=[.!?])\s+", text):
            if voice_active and (issue := knowledge_voice_sentence_issue(sentence, visible, article_visible=article_visible)):
                return issue
            if protocol_version == 2 and _production_protocol_reference(sentence, visible):
                return "training_surface_internal_process_reference"
            if _INTERNAL_FIELD.search(sentence) and _PROCESS_CUE.search(sentence):
                return "training_surface_internal_process_reference"
            if match := _HIDDEN_PROTOCOL.search(sentence):
                # "What is a system prompt?" legitimately has the prompt as
                # its subject. It does not authorize "my hidden prompt says…".
                discussed_term = next((term for term in ("系统提示词", "系统提示", "开发者提示词",
                    "开发者提示", "system prompt", "developer prompt", "qa_contract", "指导员")
                    if term.casefold() in match.group(0).casefold()
                    and term.casefold() in visible.casefold()), None)
                discussion = (discussed_term and _TECHNICAL_TASK.search(visible)
                              and not re.search(r"(?:我|本次|当前)|\b(?:I|my|this)\b", sentence, re.IGNORECASE))
                quoted = (match.group(0).casefold() in visible.casefold()
                          and _EXPLANATION_CUE.search(sentence) and not _PROCESS_CUE.search(sentence))
                if not (discussion or quoted):
                    return "training_surface_internal_process_reference"
            for pattern, allowed in ((_PRIVATE_REFERENCE, False), (_SOURCE_REFERENCE, article_visible)):
                if allowed:
                    continue
                for match in pattern.finditer(sentence):
                    # A user may explicitly ask what a quoted phrase/field means.
                    # This exception cannot be granted by the generated answer.
                    quoted_discussion = (match.group(0).casefold() in visible.casefold()
                                         and _EXPLANATION_CUE.search(sentence)
                                         and not _PROCESS_CUE.search(sentence))
                    general_rag_behavior = (_RAG_TOPIC.search(visible)
                                            and _RAG_SYSTEM_SUBJECT.search(sentence)
                                            and not _CURRENT_RESPONSE_ACTOR.search(sentence)
                                            and _RETRIEVAL_REFERENCE.fullmatch(match.group(0)))
                    if not (quoted_discussion or general_rag_behavior):
                        return "training_surface_hidden_source_reference"
    return None


def training_record_surface_issue(target, row, *, protocol_version=None,
                                  knowledge_application_voice_version=None, learning_goal=None):
    """Inspect every reasoning body in a v2 training record, without mutation.

    This is a boundary check, not schema validation or a semantic judge. Private
    response histories and reasoning summaries are deliberately not exported
    and are not read. Assistant turns only inherit actual preceding user/tool
    messages, so a later user disclosure cannot retroactively excuse a leak.
    Callers retain the untouched candidate for bounded repair or quarantine.
    """
    if not isinstance(row, Mapping):
        return None
    version = protocol_version if protocol_version is not None else row.get("training_surface_protocol_version")
    if version != 2:
        return None
    contract = row.get("qa_contract")
    voice_options = ({"knowledge_application_voice_version": knowledge_application_voice_version,
                      "learning_goal": learning_goal} if knowledge_application_voice_version == 3 else {})
    if target == "cot":
        question = row.get("question")
        visible = question if isinstance(question, list) else [{"role": "user", "content": question}]
        return training_surface_issue(row.get("reasoning"), row.get("answer"),
                                      visible_messages=visible, contract=contract, protocol_version=2, **voice_options)
    if target in {"sft", "multiturn", "agent"}:
        branches = [((), row.get("messages"))]
    elif target in {"dpo", "orpo", "rlaif"}:
        branches = [(row.get("prompt", ()), row.get(field)) for field in ("chosen", "rejected")]
    else:
        return None
    for prefix, messages in branches:
        if not isinstance(messages, (list, tuple)):
            continue
        visible = list(prefix) if isinstance(prefix, (list, tuple)) else []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            if message.get("role") == "assistant":
                # Do not pick only the first populated alias: each of these
                # fields can appear in an exported trainer conversation.
                reasoning = "\n".join(_text(message.get(field))
                                      for field in ("reasoning_content", "reasoning", "thinking"))
                issue = training_surface_issue(reasoning, message.get("content"),
                    visible_messages=visible, contract=contract, protocol_version=2, **voice_options)
                if issue:
                    return issue
            visible.append(message)
    return None
