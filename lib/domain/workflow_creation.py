"""Pure creation rules shared by every workflow entry point."""
from lib.domain.workflow_targets import TARGETS
from lib.domain.workflow_quality import text_issue
from lib.domain.workflow_scale import MAX_CANDIDATES, MAX_CONCURRENCY, MAX_BATCH_SIZE, validate_node_models


def validate_creation(*, targets=("cpt", "sft", "dpo"), max_units=100,
                      chunk_chars=2000, tasks=10, sample_count=None, concurrency=1,
                      batch_size=100, node_models=None, conversation_turns=3, brief=""):
    targets = list(dict.fromkeys(targets))
    if not targets or any(t not in TARGETS for t in targets):
        raise ValueError("请选择 CPT、SFT、DPO、RLAIF、GSM8K、CoT、ORPO、Agent 或多轮对话")
    if any(type(value) is not int for value in (max_units, chunk_chars, tasks)) or not 1 <= max_units <= MAX_CANDIDATES or not 200 <= chunk_chars <= 20000 or not 1 <= tasks <= MAX_CANDIDATES:
        raise ValueError("处理上限/分块大小/任务数超出允许范围")
    if sample_count is not None and (type(sample_count) is not int or not 1 <= sample_count <= MAX_CANDIDATES):
        raise ValueError("invalid_sample_count")
    if type(concurrency) is not int or not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError("invalid_workflow_concurrency")
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError("invalid_workflow_batch_size")
    node_models = validate_node_models(node_models)
    if type(conversation_turns) is not int or not 2 <= conversation_turns <= 8:
        raise ValueError("多轮对话轮次必须为 2 到 8")
    if not isinstance(brief, str) or len(brief) > 20000:
        raise ValueError("需求必须是最多 20000 字符的文本")
    if brief and text_issue(brief):
        raise ValueError("需求包含空文本、损坏编码或疑似密钥")
    return targets, node_models
