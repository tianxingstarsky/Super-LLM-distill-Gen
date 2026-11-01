"""Choose required dialogue design for new runs, without changing saved recipes."""
from lib.domain.workflow_qa_director import validate_qa_director, qa_director_applicable


def requires_dialogue_director(targets, *, recorded_conversations=False, repair=False):
    """New questions need a designer; preserved conversations and repairs do not."""
    return bool(qa_director_applicable(targets) and not recorded_conversations and not repair)


def prepare_new_dialogue_director(value, targets, *, recorded_conversations=False, repair=False):
    """Freeze the required designer once, at creation, retaining configured rules."""
    config = validate_qa_director(value)
    if (config["enabled"] or not requires_dialogue_director(
            targets, recorded_conversations=recorded_conversations, repair=repair)):
        return config
    return validate_qa_director({
        "enabled": True,
        "planning_mode": "adaptive",
        "question_separation": True,
        "context_mode": "persistent",
        "tools_mode": "auto",
        "learning_goal": "knowledge_application",
        "user_languages": ["zh", "en"],
    })
