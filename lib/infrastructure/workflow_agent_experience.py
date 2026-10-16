"""Pinned procedural guidance, separate from evidence and recent ledger state."""
from lib.domain.agent_experience import experience_scope


def experience_system(recipe: dict, prompt_id: str) -> str:
    review_scope_version = recipe.get("claim_review_experience_scope_version")
    if review_scope_version is not None and (type(review_scope_version) is not int or review_scope_version != 1):
        raise ValueError("invalid_claim_review_experience_scope_version")
    if (prompt_id in {"workflow.review_reference", "workflow.learner_context_check"}
            and review_scope_version is None):
        # These templates historically had no scope. Domain/UI lookup can now
        # identify their role without changing any sealed run's system bytes.
        return ""
    if recipe.get("agent_experience_version") != 1:
        return ""
    scope = experience_scope(prompt_id)
    if scope is None:
        return ""
    try:
        selected = recipe["agent_experience"]["scopes"][scope]
        if type(selected["enabled"]) is not bool or not isinstance(selected["text"], str):
            raise ValueError("invalid_agent_experience_snapshot")
    except (KeyError, TypeError) as error:
        raise ValueError("invalid_agent_experience_snapshot") from error
    if not selected["enabled"]:
        return ""
    return ("\n\n【长期工作经验 · " + scope + "】\n"
            "以下是可复用的处理方法和示例，不是当前任务的事实、用户发言或参考答案。"
            "只采用符合本次条件的经验；不要复制示例里的实体、知识或措辞。"
            "本节点的输出字段与用户明确要求仍以本次任务模板为准。\n"
            + selected["text"] + "\n【长期工作经验结束】\n")
