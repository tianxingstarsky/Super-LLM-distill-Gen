"""Keep configured node instructions separate from source and task data."""
from __future__ import annotations


def split_node_rules(data, *, instruction=None, qa_director=None):
    """Return a stable system suffix and an unmodified-content task projection.

    Only the explicit workflow instruction argument and rules matched against
    the immutable recipe are trusted. Never infer instructions from document
    text or arbitrary payload fields with names such as ``instruction``.
    """
    sections = []
    if instruction is not None:
        sections.append("本节点固定处理规则（node_task_instructions）：\n" + instruction)
    if not isinstance(data, dict):
        return ("\n" + "\n".join(sections) if sections else ""), data
    task = dict(data)
    containers = [task]
    # judge_answer wraps the workflow's task under context. Other nested
    # mappings, including uploaded source records, remain ordinary task data.
    if isinstance(data.get("context"), dict):
        task["context"] = dict(data["context"])
        containers.append(task["context"])
    config = qa_director or {}
    for field, label in (("question_rules", "提问规则"), ("answer_rules", "回答规则")):
        rule = config.get(field)
        if not isinstance(rule, str) or not rule:
            continue
        matched = [item for item in containers if item.get(field) == rule]
        if matched:
            sections.append(f"本节点用户配置的{label}（{field}）：\n" + rule)
            for item in matched:
                del item[field]
    return ("\n" + "\n".join(sections) if sections else ""), task
