"""Pure creation rules shared by every workflow entry point."""
from pathlib import PurePath
from lib.contexts.workflows.domain.targets import TARGETS
from lib.domain.workflow_quality import text_issue
from lib.contexts.workflows.domain.scale import MAX_CANDIDATES, MAX_CONCURRENCY, MAX_BATCH_SIZE, validate_node_models
from lib.domain.web_research import validate_web_research
from lib.domain.workflow_generation import validate_node_generation
from lib.domain.reasoning_trim import validate_reasoning_trim
from lib.domain.workflow_node_prompts import validate_node_prompts
from lib.domain.workflow_package_review import validate_package_review
from lib.domain.workflow_qa_director import validate_qa_director, qa_director_applicable
from lib.contexts.workflows.domain.production import validate_production, MAX_PRODUCTION_GOAL
from lib.domain.cpt_processing import validate_cpt_processing
from lib.domain.human_augmentation import HUMAN_QA_TARGETS
from lib.domain.review_repair import validate_review_repair, validate_repair_inputs
from lib.contexts.workflows.domain.graph import SOURCE_QA_TARGETS, uses_shared_source_cleaning
from lib.domain.agent_experience import validate_agent_experience


def validate_generation_review(targets, review_repair, repair_inputs):
    """Check actual starts after creation validation, not imported review blueprints."""
    review_config = validate_review_repair(review_repair)
    if review_config and review_config.get("native_regeneration"):
        if not repair_inputs or set(targets) != {"cot"}:
            raise ValueError("native_regeneration_requires_authenticated_cot_repair")
    if ("rlaif" in targets and not repair_inputs
            and review_config is not None and review_config["mode"] == "human"):
        # New preference candidates have no AI evidence until automatic review.
        # Existing repair inputs retain their saved evidence and per-row checks.
        raise ValueError("rlaif_generation_requires_ai_review")


def validate_creation(*, targets=("cpt", "sft", "dpo"), max_units=100,
                      chunk_chars=2000, tasks=10, sample_count=None, concurrency=1,
                      batch_size=100, node_models=None, conversation_turns=3, brief="",
                      agent_replay_mode="configured", evaluation_sources=(),
                      web_research=None, sources=(), node_generation=None, reasoning_trim=None,
                      node_prompts=None, package_review=None, qa_director=None, production=None,
                      cpt_processing=None, review_repair=None, repair_inputs=None, agent_experience=None,
                      reasoning_required=False):
    if type(reasoning_required) is not bool:
        raise ValueError("invalid_reasoning_required")
    target_error = "请选择 CPT、SFT、DPO、RLAIF、GSM8K、CoT、ORPO、Agent 或多轮对话"
    if isinstance(targets, (str, bytes, dict)):
        raise ValueError(target_error)
    try:
        targets = list(targets)
    except TypeError as exc:
        raise ValueError(target_error) from exc
    if not targets or any(not isinstance(t, str) or t not in TARGETS for t in targets):
        raise ValueError("请选择 CPT、SFT、DPO、RLAIF、GSM8K、CoT、ORPO、Agent 或多轮对话")
    targets = list(dict.fromkeys(targets))
    production = validate_production(production, targets)
    if not isinstance(agent_replay_mode, str) or agent_replay_mode not in {"configured", "local", "isolated"}:
        raise ValueError("invalid_agent_replay_mode")
    if evaluation_sources and "cpt" not in targets:
        raise ValueError("评测集参照仅用于 CPT 去污染检查")
    capacity = MAX_PRODUCTION_GOAL if production is not None else MAX_CANDIDATES
    if any(type(value) is not int for value in (max_units, chunk_chars, tasks)) or not 1 <= max_units <= capacity or not 200 <= chunk_chars <= 20000 or not 1 <= tasks <= capacity:
        raise ValueError("处理上限/分块大小/任务数超出允许范围")
    if sample_count is not None and (type(sample_count) is not int or not 1 <= sample_count <= capacity):
        raise ValueError("invalid_sample_count")
    if type(concurrency) is not int or not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError("invalid_workflow_concurrency")
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError("invalid_workflow_batch_size")
    node_models = validate_node_models(node_models)
    review_config = validate_review_repair(review_repair)
    repair_rows = validate_repair_inputs(repair_inputs, review_config)
    if (review_config or {}).get("reference_sources"):
        if not repair_rows or not sources or any(PurePath(str(source)).suffix.lower() not in {".txt", ".md"} for source in sources):
            raise ValueError("review_reference_sources_require_repair_and_parsed_text")
    if repair_rows and set(row["target"] for row in repair_rows) - set(targets):
        raise ValueError("review_repair_target_mismatch")
    generation = validate_node_generation(node_generation)
    if review_config and review_config.get("native_regeneration"):
        validate_generation_review(targets, review_config, repair_rows)
        cot = generation.get("cot", {})
        if not cot.get("enabled") or cot.get("answer_mode") != "native_prefill":
            raise ValueError("native_regeneration_requires_native_cot_style")
    validate_node_prompts(node_prompts)
    validate_agent_experience(agent_experience)
    validate_package_review(package_review)
    cpt = validate_cpt_processing(cpt_processing)
    # JSON containers may hold plain document text or recorded conversations.
    # Their fixed snapshots are inspected at creation; cleaning then operates
    # only on document units, preserving actual messages unchanged.
    structured_sources = bool(set(targets).intersection(SOURCE_QA_TARGETS)) and any(
        PurePath(str(source)).suffix.lower() in {".json", ".jsonl"} for source in sources)
    if (not repair_rows and cpt["mode"] == "model" and "cpt" not in targets
            and not uses_shared_source_cleaning(targets, sources)
            and not structured_sources):
        raise ValueError("cpt_processing_requires_cpt")
    director = validate_qa_director(qa_director)
    if director["enabled"] and not qa_director_applicable(targets):
        raise ValueError("qa_director_requires_qa_target")
    if (director.get("human_augmentation", {}).get("enabled") and not sources
            and set(targets) - HUMAN_QA_TARGETS):
        raise ValueError("human_augmentation_requires_qa_sources")
    trim = validate_reasoning_trim(reasoning_trim)
    if trim and trim["enabled"] and not set(targets) & {"sft", "cot"}:
        raise ValueError("reasoning_trim_requires_reasoning_target")
    if type(conversation_turns) is not int or not 2 <= conversation_turns <= 8:
        raise ValueError("多轮对话轮次必须为 2 到 8")
    if not isinstance(brief, str) or len(brief) > 20000:
        raise ValueError("需求必须是最多 20000 字符的文本")
    if brief and text_issue(brief):
        raise ValueError("需求包含空文本、损坏编码或疑似密钥")
    validate_web_research(web_research, brief=brief, sources=sources, targets=targets)
    return targets, node_models
