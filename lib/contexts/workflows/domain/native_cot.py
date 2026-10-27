"""Observable native-channel leakage; retain evidence rather than rewriting it."""
import re

_EXECUTION = re.compile(
    r"authored_task_contract|reasoning_depth_control|revision_context|revision[_ ]feedback"
    r"|显式推理语言要求|原生思考通道|输出全部原生思考"
    r"|\bsystem\s+(?:prompt|instructions?|says)\b|in this interface", re.I)


def native_instruction_leakage(reasoning, visible_messages, *, public_task_rules=False):
    """Do not ban technical discussion explicitly present in the real dialogue."""
    if not isinstance(reasoning, str):
        return []
    visible = "\n".join(m.get("content", "") for m in visible_messages
                        if isinstance(m.get("content"), str)).casefold()
    evidence = []
    for match in _EXECUTION.finditer(reasoning):
        if (public_task_rules and any(m.get("role") == "system" and m.get("content") for m in visible_messages)
                and re.fullmatch(r"system\s+(?:prompt|instructions?|says)|显式推理语言要求", match[0], re.I)):
            # A reading-session export contains the actual system rules.
            # Mentioning those rules is no longer a hidden-context leak.
            continue
        if match[0].casefold() in visible:
            continue
        start, end = max(0, match.start()-80), min(len(reasoning), match.end()+160)
        evidence.append({"issue": "native_generation_instruction_leakage", "field": "reasoning",
            "start": start, "end": end, "quote": reasoning[start:end]})
    return evidence
