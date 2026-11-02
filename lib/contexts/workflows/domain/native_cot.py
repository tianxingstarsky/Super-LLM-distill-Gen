"""Native execution facts and observable leakage; never rewrite the output."""
import re

NATIVE_EXECUTION_STATE = "native_execution_state"
NATIVE_CONTEXT_CONTRACT = "native_context_contract"


def native_context_contract(recipe, sample, task, contract):
    """Expose authenticated task facts without classifying the user's wording."""
    version = recipe.get("native_context_contract_version")
    if version is None:
        return None
    if type(version) is not int or version != 1:
        raise ValueError("invalid_native_context_contract_version")
    from lib.domain.workflow_qa_director import LEARNING_GOALS, QA_TYPES
    from lib.domain.knowledge_voice import knowledge_voice_rules

    goal = recipe.get("qa_director", {}).get("learning_goal")
    goal = goal if goal in LEARNING_GOALS else None
    supplied = sample.get("qa_contract")
    qa_type = supplied.get("qa_type") if isinstance(supplied, dict) else None
    qa_type = qa_type if qa_type in QA_TYPES else None
    excerpt_only = contract["source_scope"] == "learner_excerpt_only"
    source = task.get("source")
    return {"version": 1, "learning_goal": goal, "qa_type": qa_type,
        "teacher_source_visibility": "private" if isinstance(source, dict) and source.get("text") else "not_provided",
        "learner_excerpt_only": excerpt_only,
        "knowledge_application_voice": bool(knowledge_voice_rules(goal, {"qa_type": qa_type})) and not excerpt_only}

_EXECUTION = re.compile(
    r"authored_task_contract|reasoning_depth_control|revision_context|revision[_ ]feedback"
    r"|显式推理语言要求|原生思考通道|输出全部原生思考"
    r"|\bsystem\s+(?:prompt|instructions?|says)\b|in this interface", re.I)
_ENVIRONMENT = re.compile(r"\bnative_execution_state\b", re.I)
_SYSTEM_REFERENCE = re.compile(r"system\s+(?:prompt|instructions?|says)", re.I)
_STYLE_SETTING = re.compile(
    r"\b(?:desired|required|target|configured)\s+(?:response\s+)?(?:over)?verbosity"
    r"(?:\s+level)?\s*(?:is|of|to|=|:)?\s*(?P<value>\d+(?:\.\d+)?)\b", re.I)
_OWN_RULE = re.compile(
    r"\b(?:I|we)\b.{0,80}\b(?:follow|obey|comply with|adhere to|abide by|instructed|directed|told|required)\b"
    r"|\b(?:need|must|have)\s+(?:to\s+)?(?:follow|obey|comply with)\b"
    r"|\b(?:my|our|hidden|private|current|actual)\s*$", re.I)
_ARTIFACT_ACTION = re.compile(
    r"\b(?:write|draft|create|design|prepare|compose|define|construct)\s+(?:(?:a|an|the|new)\s+)?"
    r"(?:system\s+)?(?:prompt|instruction)s?\b"
    r"|\b(?:send|pass|submit|supply|include).{0,100}\b(?:API|request|payload|message|chatbot)\b"
    r"|\b(?:set|configure|assign).{0,60}\b(?:demo|example|application|chatbot|client)\b"
    r"|\b(?:set|configure|assign)\s+(?:(?:desired|required|target|configured)\s+)?(?:over)?verbosity\b", re.I)
_ARTIFACT_TARGET = re.compile(
    r"\b(?:for|to|of)\s+(?:(?:a|an|the|this|that|example|demo)\s+)*"
    r"(?:API|app|application|chatbot|client|demo|example|request|bot)\b", re.I)
_ARTIFACT_OWNER = re.compile(r"\b(?:demo|example|application|app|chatbot|client)(?:'s|’s)?\s*$", re.I)


def _reference_clause(text, match):
    """Local sentence/list item only: a nearby API mention cannot exempt a trace."""
    start = max((text.rfind(char, 0, match.start()) for char in "\n.!?。；;"), default=-1) + 1
    ends = [position for char in "\n.!?。；;" if (position := text.find(char, match.end())) >= 0]
    return start, min(ends, default=len(text))


def _public_rule_source(after_reference, visible_messages):
    """Authenticate an explicit rule quotation, not an inferred paraphrase."""
    claim = re.match(r"\s*(?:says?|requires?|instructs?|states?)\s+(?:that\s+)?(.+)",
                     after_reference, re.I)
    if not claim:
        return None
    quoted = claim[1].strip(" \t\n'\"‘’“”.:;").casefold()
    if not quoted:
        return None
    for index, message in enumerate(visible_messages):
        content = message.get("content")
        if message.get("role") == "system" and isinstance(content, str) and quoted in content.casefold():
            return index
    return None


def _visible_style_setting(value, visible_messages):
    # A quoted example or an assistant's earlier guess does not establish a
    # current instruction. Only explicit positive user/system instructions do.
    setting = r"(?:over)?verbosity(?:\s+level)?\s*(?:is|of|to|at|=|:)?\s*" + re.escape(value) + r"\b"
    instruction = re.compile(r"(?:^|\n)\s*(?:(?:for this (?:reply|answer),?\s*)?"
        r"(?:please\s+)?(?:use|set|keep)\s+(?:(?:a|the|your|response)\s+)*" + setting + r")", re.I)
    for index, message in enumerate(visible_messages):
        content = message.get("content")
        if message.get("role") in {"user", "system"} and isinstance(content, str):
            if instruction.search(content):
                return index
            if message.get("role") == "system" and re.search(
                    r"(?:^|\n)\s*(?:desired|required|target)\s+" + setting, content, re.I):
                return index
    return None


def native_instruction_scope_evidence(text, visible_messages, *, public_task_rules=False,
                                      environment_version=None, field="reasoning",
                                      public_system_positions=()):
    """Classify bounded references; uncertain ownership is unresolved, never passed.

    This is an observable scope check, not general semantic quality assessment.
    Every exemption carries its local evidence and applies to one occurrence.
    """
    if not isinstance(text, str):
        return []
    public_notes = [(index, visible_messages[index]) for index in public_system_positions
                    if type(index) is int and 0 <= index < len(visible_messages)
                    and visible_messages[index].get("role") == "system"
                    and isinstance(visible_messages[index].get("content"), str)]
    patterns = (_EXECUTION, _STYLE_SETTING) if field == "reasoning" else ()
    if environment_version == 1:
        patterns += (_ENVIRONMENT,)
    findings = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            clause_start, clause_end = _reference_clause(text, match)
            before, after = text[clause_start:match.start()], text[match.end():clause_end]
            clause = text[clause_start:clause_end]
            classification, basis, disposition = "private_execution_field", "private_execution_marker", "block"
            source_index = None
            if pattern is _STYLE_SETTING:
                source_index = _visible_style_setting(match["value"], visible_messages)
                if source_index is not None:
                    classification, basis, disposition = "public_task_rule", "explicit_visible_setting", "observe"
                elif _ARTIFACT_OWNER.search(before) or (
                        re.search(r"\b(?:set|configure|assign)\b[^.!?;\n]{0,60}$", before, re.I)
                        and re.search(r"\b(?:application|app|chatbot|client|API|request)\b", before, re.I)):
                    classification, basis, disposition = "task_artifact", "external_configuration_action", "observe"
                else:
                    classification, basis = "unsupported_runtime_setting", "no_visible_instruction_for_value"
            elif _SYSTEM_REFERENCE.fullmatch(match[0]):
                claimed_rule = "says " + after if match[0].lower().endswith("says") else after
                source_index = _public_rule_source(claimed_rule, visible_messages)
                if source_index is not None:
                    classification, basis, disposition = "public_task_rule", "quoted_visible_system_rule", "observe"
                elif public_notes and not re.search(
                        r"\b(?:hidden|private|internal|developer)\b|隐藏|私有|内部|开发者", clause, re.I):
                    # The application authenticated and exports these actual
                    # notes. Their natural paraphrase is not a private-channel
                    # leak; this observation does not certify its truth.
                    source_index = public_notes[-1][0]
                    classification, basis, disposition = "public_task_rule", "authenticated_public_review_note", "observe"
                elif (public_task_rules and not after.strip() and not re.search(r"\b(?:hidden|private)\b", before, re.I)
                        and any(message.get("role") == "system" and message.get("content") for message in visible_messages)):
                    classification, basis, disposition = "public_task_rule", "exported_public_rule_reference", "observe"
                elif _OWN_RULE.search(before) or re.match(
                        r"\s*,?\s*(?:(?:that|which)\s+)?(?:I|we)\b.{0,50}\b(?:follow|obey)\b", after, re.I) or re.match(
                        r"\s*(?:says?|requires?|tells?|instructs?|mandates?)\s+(?:me|us|I|we)\b", after, re.I):
                    classification, basis = "own_runtime_instruction", "current_author_instruction_claim"
                elif _ARTIFACT_ACTION.search(clause) or (
                        re.search(r"\b(?:use|add|include)\b", before, re.I) and _ARTIFACT_TARGET.search(clause)) or _ARTIFACT_OWNER.search(before):
                    classification, basis, disposition = "task_artifact", "local_prompt_construction_or_transport", "observe"
                elif re.match(r"\s*(?:says?|requires?|tells?|instructs?|mandates?)\b", after, re.I) or match[0].lower().endswith("says"):
                    classification, basis = "unresolved_reference", "claimed_rule_has_no_visible_source"
                elif any(message.get("role") == "user" and isinstance(message.get("content"), str)
                         and match[0].casefold() in message["content"].casefold() for message in visible_messages):
                    classification, basis, disposition = "public_technical_topic", "user_raised_reference_without_own_rule_claim", "observe"
                elif public_task_rules and any(message.get("role") == "system" and message.get("content")
                                               for message in visible_messages):
                    classification, basis, disposition = "public_task_rule", "exported_public_rule_context", "observe"
                else:
                    classification, basis = "unresolved_reference", "instruction_owner_not_established"
            start, end = max(0, match.start() - 80), min(len(text), match.end() + 160)
            finding = {"classification": classification, "disposition": disposition,
                "issue": ("native_instruction_reference_unresolved" if classification == "unresolved_reference"
                          else "native_generation_instruction_leakage") if disposition == "block" else None,
                "field": field, "start": start, "end": end, "quote": text[start:end],
                "reference": match[0], "reference_start": match.start(), "reference_end": match.end(),
                "basis": basis, "scope_start": clause_start, "scope_end": clause_end,
                "scope_quote": clause}
            if source_index is not None:
                finding["visible_message_index"] = source_index
            findings.append(finding)
    return sorted(findings, key=lambda finding: finding["reference_start"])


def native_execution_state(recipe, task, contract):
    """Project bounded facts, never another copy of the source or user words."""
    version = recipe.get("native_environment_version")
    if version is None:
        return None
    if type(version) is not int or version != 1:
        raise ValueError("invalid_native_environment_version")
    messages = task["prompt"]
    return {"version": version,
            "current_user_turn": sum(message.get("role") == "user" for message in messages),
            "source_scope": task.get("answer_context_scope", {}).get("source_scope", contract["source_scope"]),
            "assistant_history": "unverified" if any(message.get("role") == "assistant" for message in messages) else "absent",
            "previous_candidate": "proposal" if task.get("previous_candidate") else "absent",
            "tools": "none"}


def native_instruction_leakage(reasoning, visible_messages, *, public_task_rules=False,
                               environment_version=None, field="reasoning", scope_version=None,
                               public_system_positions=()):
    """Do not ban technical discussion explicitly present in the real dialogue."""
    if scope_version is not None or public_system_positions:
        if scope_version is not None and (type(scope_version) is not int or scope_version != 1):
            raise ValueError("invalid_native_instruction_scope_version")
        return [finding for finding in native_instruction_scope_evidence(reasoning, visible_messages,
            public_task_rules=public_task_rules, environment_version=environment_version, field=field,
            public_system_positions=public_system_positions)
            if finding["disposition"] == "block"]
    if not isinstance(reasoning, str):
        return []
    visible = "\n".join(m.get("content", "") for m in visible_messages
                        if isinstance(m.get("content"), str)).casefold()
    evidence = []
    # Existing execution-phrase checks stay on reasoning. Only the new private
    # state marker extends to answers; do not broaden legacy answer filtering.
    patterns = (_EXECUTION,) if field == "reasoning" else ()
    if environment_version == 1:
        patterns += (_ENVIRONMENT,)
    for match in (match for pattern in patterns for match in pattern.finditer(reasoning)):
        if (public_task_rules and any(m.get("role") == "system" and m.get("content") for m in visible_messages)
                and re.fullmatch(r"system\s+(?:prompt|instructions?|says)|显式推理语言要求", match[0], re.I)):
            # A reading-session export contains the actual system rules.
            # Mentioning those rules is no longer a hidden-context leak.
            continue
        if match[0].casefold() in visible:
            continue
        start, end = max(0, match.start()-80), min(len(reasoning), match.end()+160)
        evidence.append({"issue": "native_generation_instruction_leakage", "field": field,
            "start": start, "end": end, "quote": reasoning[start:end]})
    return evidence
