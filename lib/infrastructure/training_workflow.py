"""Durable automatic training-data workflows, independent of browser sessions.

Each run owns immutable input copies, a pinned recipe, per-item checkpoints,
stage metrics and a hash-verified training bundle. No global output is overwritten.
"""
from __future__ import annotations

from copy import deepcopy
from collections import deque
from contextlib import ExitStack, closing
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid
from itertools import islice
import threading
import time

from lib.infrastructure.workflow_store import (
    now, digest, file_hash, read_json, run_path, list_runs, is_active, cancel, verify_artifacts,
)

from lib.application.trainer_export_service import prepare_trl_export
from lib.domain.workflow_quality import POLICY, accepted, canonical, conversation_issue, same_answer, text_issue, tool_error_flag, verdict
from lib.domain.agent_trajectory import REPLAY_POLICY_VERSION, assess_recorded_trajectory
from lib.domain.source_conversation import source_conversation_issue
from lib.domain.corpus_quality import inspect_corpus, summarize_corpus_sources
from lib.contexts.datasets.infrastructure.corpus_index import CorpusClusterSizes, CorpusNearDuplicateIndex
from lib.domain.math_tasks import build_gsm8k, validate_gsm8k, validate_math_candidate
from lib.domain.open_task_plan import MAX_TASK_CHARS, task_identity, task_plan_issue
from lib.domain.workflow_creation import validate_creation, validate_generation_review
from lib.domain.workflow_graph import SOURCE_CLEANING_RECIPE_VERSION, uses_shared_source_cleaning
from lib.domain.document_parser import validate_document_parser
from lib.domain.web_research import validate_web_research
from lib.domain.workflow_scale import PLAN_BATCH_SIZE
from lib.domain.workflow_generation import (validate_node_generation, style_for_sample,
                                             REASONING_LANGUAGE_CONTRACT_VERSION)
from lib.domain.knowledge_voice import knowledge_voice_options
from lib.domain.workflow_reasoning_route import cot_updates_sft, cot_with_sft_context, finalized_sft_rows
from lib.domain.reasoning_trim import validate_reasoning_trim, trim_prompt
from lib.domain.workflow_package_review import validate_package_review, package_review_stage
from lib.domain.cpt_processing import validate_cpt_processing
from lib.domain.source_quote import quote_spans
from lib.domain.workflow_qa_director import validate_qa_director
from lib.domain.review_repair import validate_review_repair, validate_repair_inputs
from lib.domain.human_augmentation import human_design, human_generation_instruction
from lib.domain.workflow_node_prompts import (
    validate_node_prompts, snapshot_node_prompts, validate_node_prompt_snapshot,
)
from lib.infrastructure.json_stream import iter_json_records, iter_source_json_records
from lib.infrastructure.source_snapshot import snapshot_source
from lib.infrastructure.generation_settings_file import preference_snapshot
from lib.contexts.datasets.contracts import preferred_training_record
from lib.infrastructure.brave_web_research import (
    search as search_web, validate_search_document, validate_search_topic_document,
)
from lib.infrastructure.workflow_rows import WorkflowRows, RowSpool, write_jsonl, write_json_array
from lib.infrastructure.workflow_row_checkpoint import row_checkpoint
from lib.domain.workflow_production import validate_production
from lib.infrastructure.workflow_production import WorkflowProduction
from lib.infrastructure.production_checkpoints import ProductionCheckpoints
from lib.infrastructure.production_review_quality import package_review_escalation
from lib.infrastructure.planning_identities import PlanningIdentities
from lib.infrastructure.workflow_qa_director import WorkflowQADirector, DialoguePlanError
from lib.infrastructure.workflow_knowledge_agent import WorkflowKnowledgeAgent
from lib.domain.knowledge_agent import KNOWLEDGE_AGENT_INSTRUCTION, KNOWLEDGE_AGENT_ARCHIVE_INSTRUCTION
from lib.infrastructure.workflow_cpt_processing import WorkflowCPTProcessing
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair
from lib.infrastructure.workflow_source_library import WorkflowSourceLibrary
from lib.domain.multiturn import completed_turn_ends
from lib.domain.workflow_targets import (INPUT_EXTENSIONS, PREFERENCE_TARGETS, STAGES, TARGETS,
                                         rlaif_feedback_issue, rlaif_reward_model_record, training_record)
from lib.infrastructure.corpus_reference import exact_release_match, released_corpus_index, snapshot_released_corpus
from lib.infrastructure.evaluation_reference import load_evaluation_index, snapshot_evaluation_sources
from lib.infrastructure.agent_docker_replay import (DockerLedgerReplay, IMAGE_ENV, RUNNER_SHA256,
                                                    validate_sandbox_image)
from lib.doc2corpus import SUPPORTED_EXTS, chunk_text, import_text
from lib.io_utils import _replace_state, atomic_json
from lib.llm_client import ChatClient, chat_json, load_backend, snapshot_backend_endpoint
from lib.infrastructure.workflow_stream_journal import StreamJournal
from lib.prompts import get, registry, render
from lib.contexts.workflows.domain.execution import WorkflowCancelled as Cancelled


EXTENSIONS = INPUT_EXTENSIONS
MAX_FILE_BYTES = 50 * 1024 * 1024
RECIPE_VERSION = 11
PRODUCTION_RECIPE_VERSION = 12
SOFT_PRODUCTION_RECIPE_VERSION = 13
DOCUMENT_PROCESSING_RECIPE_VERSION = 14
COT_SFT_RECIPE_VERSION = 15
JEV_NODE_RECIPE_VERSION = 16
HUMAN_AUGMENTATION_RECIPE_VERSION = 17
REVIEW_REPAIR_RECIPE_VERSION = 18
SUPPORTED_RECIPE_VERSIONS = frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22})

# The automatic workflow has no recipe pools or batch tagger. Keep those
# preferences visible as unapplied until their behavior can be implemented.
UNAPPLIED_PREFERENCE_FIELDS = (
    "preferences.*", "sampling.default_floor", "sampling.templates_per_dim",
    "sampling.shuffle_per_batch", "correction.*",
)


from lib.contexts.workflows.domain.dialogue import document_evidence_level


def _write_quality_report(path: Path, report: dict, input_records: Path) -> None:
    """Write the full issue list without retaining its rows in package memory."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("{\n")
            for position, (key, value) in enumerate(report.items()):
                if position:
                    handle.write(",\n")
                handle.write("  " + json.dumps(key, ensure_ascii=False) + ": ")
                if key == "input_issues":
                    handle.write("[")
                    first = True
                    for row in iter_json_records(input_records):
                        if row["status"] != "quarantined":
                            continue
                        issue = {"id": row["id"], "source_id": row["source_id"],
                                 "source_name": row.get("source_name"),
                                 "location": row.get("location"),
                                 "source_location": row.get("source_location"),
                                 "reason": row.get("reason")}
                        handle.write("\n" if first else ",\n")
                        handle.write("    " + json.dumps(issue, ensure_ascii=False, indent=2)
                                     .replace("\n", "\n    "))
                        first = False
                    handle.write("\n  ]" if not first else "]")
                else:
                    handle.write(json.dumps(value, ensure_ascii=False, indent=2)
                                 .replace("\n", "\n  "))
            handle.write("\n}")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_state(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prompt_versions():
    return {key: {"version": value.version, "sha256": digest(value.template)}
            for key, value in registry().items() if key.startswith("workflow.")}


def write_trainer_export(destination, target, records, preferences=None, *,
                         sft_output_style=None):
    """Convert rows individually while retaining the all-or-nothing TRL gate."""
    output = destination / f"trl_{target}.jsonl"
    pending = destination / f".trl_{target}.pending"
    total, compatible, failures, reasons = 0, 0, [], {}
    format_name = "trl_sft" if target in {"sft", "multiturn", "agent"} else "trl_preference"
    with pending.open("w", encoding="utf-8") as handle:
        for row in records:
            if row["status"] != "eligible":
                continue
            payload = {"id": row.get("id"), **preferred_training_record(
                target, row, preferences, reward_model=target == "rlaif",
                sft_output_style=sft_output_style)}
            check = prepare_trl_export("dpo" if target == "rlaif" else target, [payload])
            if check["ready"]:
                compatible += 1
                handle.write(canonical(check["rows"][0]) + "\n")
            else:
                failure = {**check["failures"][0], "index": total}
                failures.append(failure)
                reasons[failure["reason"]] = reasons.get(failure["reason"], 0) + 1
            total += 1
    if not total:
        failures.append({"index": None, "id": None, "reason": "trl_no_records"})
        reasons["trl_no_records"] = 1
    ready = bool(total) and not failures
    if ready:
        pending.replace(output)
    else:
        pending.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
    return {"status": "ready" if ready else "incompatible", "format": format_name,
            "file": output.name if ready else None,
            "summary": {"total": total, "compatible": compatible, "incompatible": total - compatible,
                        "reasons": dict(sorted(reasons.items()))}, "failures": failures}


def create_run(output, *, sources=(), brief="", targets=("cpt", "sft", "dpo"),
               name="自动数据生成", backend=None, model=None, judge_backend=None,
               judge_model=None, jev_backend=None, jev_model=None,
               max_units=100, chunk_chars=2000, tasks=10, conversation_turns=3, source_names=None,
               evaluation_sources=(), evaluation_source_names=None,
               sample_count=None, concurrency=1, batch_size=100, node_models=None,
               agent_replay_mode="configured", web_research=None, settings_root=None,
               sft_output_style=None, document_parser=None, knowledge_retrieval=None,
               node_generation=None, reasoning_trim=None, node_prompts=None,
               package_review=None, qa_director=None, production=None, cpt_processing=None, run_id=None,
               review_repair=None, repair_inputs=None, agent_experience=None, reasoning_required=False):
    arithmetic_only = (targets == ["gsm8k"] or targets == ("gsm8k",) or targets == {"gsm8k"}) and not repair_inputs
    targets, node_models = validate_creation(
        targets=targets, max_units=max_units, chunk_chars=chunk_chars, tasks=tasks,
        sample_count=sample_count, concurrency=concurrency, batch_size=batch_size,
        node_models=node_models, conversation_turns=conversation_turns, brief=brief,
        agent_replay_mode=agent_replay_mode, evaluation_sources=evaluation_sources,
        web_research=web_research, sources=sources, node_generation=node_generation,
        reasoning_trim=reasoning_trim, node_prompts=node_prompts,
        package_review=package_review, qa_director=qa_director, production=production,
        cpt_processing=None if arithmetic_only else cpt_processing,
        review_repair=review_repair, repair_inputs=repair_inputs, agent_experience=agent_experience,
        reasoning_required=reasoning_required)
    review_repair = validate_review_repair(review_repair)
    repair_inputs = validate_repair_inputs(repair_inputs, review_repair)
    validate_generation_review(targets, review_repair, repair_inputs)
    cpt_only = targets == ["cpt"] and not repair_inputs
    production = validate_production(production, targets)
    web_research = validate_web_research(web_research, brief=brief, sources=sources, targets=targets)
    node_generation = validate_node_generation(node_generation)
    reasoning_trim = validate_reasoning_trim(reasoning_trim)
    package_review = validate_package_review(package_review)
    qa_director = validate_qa_director(qa_director)
    human = qa_director.get("human_augmentation", {"enabled": False})
    cpt_processing_supplied = cpt_processing is not None
    cpt_processing = validate_cpt_processing(cpt_processing)
    node_prompts = validate_node_prompts(node_prompts)
    from lib.domain.agent_experience import snapshot_agent_experience
    experience_snapshot = snapshot_agent_experience(agent_experience)
    if cpt_only:
        # CPT owns its source-preserving cleaning checks. Saved sample-review
        # choices are inactive here, including bindings to removed services.
        review_repair = None
        package_review = validate_package_review(None)
        node_models = {stage: roles for stage, roles in node_models.items()
                       if stage not in {"review", "jev", "package"}}
        node_prompts = {stage: templates for stage, templates in node_prompts.items()
                        if stage not in {"review", "jev", "package"}}
    from lib.domain.workflow_node_prompts import VERSION_13_NODE_PROMPT_IDS, VERSION_19_NODE_PROMPT_IDS
    new_prompt_override = any(prompt_id not in VERSION_13_NODE_PROMPT_IDS.get(stage, ())
                              for stage, templates in node_prompts.items() for prompt_id in templates)
    separated_prompt_override = any(prompt_id not in VERSION_19_NODE_PROMPT_IDS.get(stage, ())
                                    for stage, templates in node_prompts.items() for prompt_id in templates)
    human_prompt_override = any("workflow.human_augmentation_check" in templates for templates in node_prompts.values())
    source_cleaning = not repair_inputs and uses_shared_source_cleaning(targets, sources)
    recipe_version = (20 if qa_director.get("question_separation") or separated_prompt_override else
                      SOURCE_CLEANING_RECIPE_VERSION if source_cleaning else
                      REVIEW_REPAIR_RECIPE_VERSION if review_repair is not None else
                      HUMAN_AUGMENTATION_RECIPE_VERSION if human["enabled"] or human_prompt_override else
                      JEV_NODE_RECIPE_VERSION if package_review_stage(package_review) == "jev" else
                      COT_SFT_RECIPE_VERSION if {"sft", "cot"}.issubset(targets) else
                      DOCUMENT_PROCESSING_RECIPE_VERSION if cpt_processing_supplied
                      or (isinstance(document_parser, dict) and document_parser.get("mode") == "model")
                      or new_prompt_override else
                      SOFT_PRODUCTION_RECIPE_VERSION if production and production["version"] == 2
                      else PRODUCTION_RECIPE_VERSION if production is not None else RECIPE_VERSION)
    if (node_generation.get("cot", {}).get("answer_mode") == "native_prefill"
            or any(prompt_id in {"workflow.cot_rationale", "workflow.cot_native_answer", "workflow.cot_prefix_check"}
                   for templates in node_prompts.values() for prompt_id in templates)):
        recipe_version = 21
    multi_style = node_generation.get("multiturn", node_generation.get("sft")) or {}
    authored_language_review = any(config.get("enabled") is True and config.get("reasoning_language") == "zh_en"
        for stage, config in (("sft", node_generation.get("sft", {})), ("multiturn", multi_style))
        if stage in targets)
    if ("multiturn" in targets and multi_style.get("enabled", bool(multi_style))
            or "workflow.style_check" in node_prompts.get("multiturn", {})):
        recipe_version = 22
    node_prompt_templates = snapshot_node_prompts(node_prompts, recipe_version=recipe_version,
        learner_context_review_version=1, claim_review_protocol_version=2, claim_review_format_feedback_version=2,
        claim_refutation_release_version=1, claim_reference_policy_version=1, claim_review_anchor_protocol_version=1,
        claim_review_adversarial_protocol_version=1, claim_review_wire_schema_version=1, claim_review_focus_version=1,
        claim_review_condition_contract_version=1, reasoning_depth_contract_version=1)
    node_prompt_system = render(get("workflow.system"))
    generation_prompts = {}
    for stage, config in node_generation.items():
        if config.get("enabled", True):
            presets = (("concise", "structured", "skeptical", "reflective")
                       if config["style"] == "mixed" else (config["style"],))
            generation_prompts[stage] = {preset: style_for_sample({**config, "style": preset}, "snapshot",
                                            reasoning_language_version=REASONING_LANGUAGE_CONTRACT_VERSION)["instruction"]
                                         for preset in presets}
    if (sft_output_style is not None and
            (type(sft_output_style) is not str or sft_output_style not in {"separated", "drop"}
             or "sft" not in targets)):
        raise ValueError("invalid_sft_output_style")
    preferences = preference_snapshot(settings_root or Path(__file__).resolve().parents[2])
    files = [Path(p).resolve(strict=True) for p in sources]
    if not repair_inputs and not arithmetic_only and cpt_processing["mode"] == "model" and cpt_processing["review_mode"] == "vision":
        from lib.infrastructure.document_vision import require_vision_model
        binding = node_models.get("cpt", {}).get("jev")
        if settings_root is None or not binding:
            raise ValueError("cpt_visual_review_model_required")
        require_vision_model(Path(settings_root), binding)
        if not files:
            raise ValueError("cpt_visual_review_requires_documents")
    # Template arithmetic never consumes documents. Inactive parser settings
    # must not require a service, modality probe, or a paid parsing request.
    document_parser = validate_document_parser(None if arithmetic_only else document_parser)
    document_parser_signature = None
    if not repair_inputs and document_parser["mode"] == "docling" and any(
            path.suffix.lower() == ".pdf" for path in files):
        from lib.infrastructure.docling_document import docling_parse_signature
        document_parser_signature = docling_parse_signature(document_parser)
        if document_parser.get("assistance"):
            from lib.infrastructure.document_vision import require_vision_model
            binding=document_parser["assistance"]["binding"]
            if settings_root is None:raise ValueError("document_vision_model_required")
            require_vision_model(Path(settings_root),binding)
            node_models.setdefault("ingest",{})["vision"]=binding
    if not repair_inputs and document_parser["mode"] == "vision":
        from lib.infrastructure.document_vision import require_vision_model
        if settings_root is None or not files or any(path.suffix.lower() in {".json", ".jsonl"} for path in files):
            raise ValueError("document_vision_requires_document_sources")
        require_vision_model(Path(settings_root), document_parser["binding"])
        node_models.setdefault("ingest", {})["vision"] = document_parser["binding"]
    elif not repair_inputs and document_parser["mode"] == "model":
        if not files or any(path.suffix.lower() in {".json", ".jsonl", ".png", ".jpg", ".jpeg", ".webp"} for path in files):
            raise ValueError("document_text_requires_document_sources")
        node_models.setdefault("ingest", {})["generation"] = document_parser["binding"]
    elif not repair_inputs and not arithmetic_only and any(path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} for path in files):
        raise ValueError("document_image_requires_vision_parser")
    if not files and not brief.strip() and not human["enabled"] and not repair_inputs and not arithmetic_only:
        raise ValueError("请上传来源文件或填写开放性需求")
    if "agent" in targets and not files and not (set(targets) - {"agent"}) and not repair_inputs:
        raise ValueError("Agent 轨迹重放需要上传 JSON/JSONL 工具执行记录")
    if len(files) > 200:
        raise ValueError("单次运行最多 200 个文件")
    for path in files:
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            raise ValueError(f"不支持的来源文件：{path.name}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"来源超过 50 MiB：{path.name}")
    if sum(path.stat().st_size for path in files) > 200 * 1024 * 1024:
        raise ValueError("单次运行来源总大小最多 200 MiB，请拆分批次")
    from lib.infrastructure.knowledge_receipt import validate_knowledge_receipt
    if knowledge_retrieval is not None:
        validate_knowledge_receipt(knowledge_retrieval, [
            {"file": f"{index:04d}{source.suffix.lower()}", "sha256": file_hash(source)}
            for index, source in enumerate(files)])
    references = snapshot_released_corpus(Path(output)) if "cpt" in targets else []
    agent_sandbox_image = (validate_sandbox_image(os.environ.get(IMAGE_ENV))
                           if not repair_inputs and "agent" in targets and agent_replay_mode != "local" else None)
    if not repair_inputs and "agent" in targets and agent_replay_mode == "isolated" and not agent_sandbox_image:
        raise ValueError("agent_sandbox_not_configured")
    run_id = uuid.uuid4().hex if run_id is None else run_id
    # Human workspaces reserve this identity durably before creating the child.
    # The usual validated run path and exclusive input mkdir still prevent reuse.
    run_path(output, run_id)
    endpoint_pins = None
    if repair_inputs:
        # A correction authenticates the parent snapshots without executing its
        # parsing, generation, cleaning or replay nodes. Inherited bindings for
        # those nodes must not load clients or require still-active services.
        active_roles = ({"generation", "jev"} if package_review["enabled"] else {"generation"}) if review_repair["mode"] == "auto" else set()
        native_bindings = (dict(node_models.get("cot", {})) if review_repair.get("native_regeneration") else {})
        bindings = {role: binding for role, binding in node_models.get("review", {}).items() if role in active_roles}
        node_models = {"review": bindings} if bindings else {}
        if native_bindings:
            node_models["cot"] = native_bindings
    elif arithmetic_only:
        node_models = {stage: roles for stage, roles in node_models.items()
                       if stage in {"review", "jev", "package"}}
    if settings_root is not None and node_models:
        # A submitted node binding must not follow a mutable service name to a
        # different URL, protocol or model inventory while waiting to run.
        endpoint_pins = {stage: {role: snapshot_backend_endpoint(
            Path(settings_root), binding["backend"], binding["model"])
            for role, binding in roles.items()} for stage, roles in node_models.items()}
    path = run_path(output, run_id)
    (path / "inputs").mkdir(parents=True, exist_ok=False)
    evaluation_references = snapshot_evaluation_sources(
        path, evaluation_sources, evaluation_source_names) if evaluation_sources else []
    snapshots = []
    copied_bytes = 0
    for index, source in enumerate(files):
        destination = path / "inputs" / f"{index:04d}{source.suffix.lower()}"
        snapshot = snapshot_source(source, destination, min(MAX_FILE_BYTES, 200 * 1024 * 1024 - copied_bytes))
        copied_bytes += snapshot["bytes"]
        snapshots.append({"name": (source_names or {}).get(str(source), source.name),
                          "file": destination.name, **snapshot})
        if destination.suffix in {".json", ".jsonl"}:
            from lib.infrastructure.structured_source_kind import contains_document_records
            if contains_document_records(destination):
                snapshots[-1]["kind"] = "document_collection"
    # Classify the exact copied records, rather than a mutable upload path.
    # Structured document collections need the same shared evidence cleaning
    # as PDF/Markdown; actual conversation-only containers keep their path.
    if not repair_inputs and not source_cleaning and uses_shared_source_cleaning(targets, snapshots):
        source_cleaning = True
        recipe_version = recipe_version if recipe_version >= 20 else SOURCE_CLEANING_RECIPE_VERSION
        node_prompt_templates = snapshot_node_prompts(node_prompts, recipe_version=recipe_version,
            learner_context_review_version=1, claim_review_protocol_version=2, claim_review_format_feedback_version=2,
            claim_refutation_release_version=1, claim_reference_policy_version=1, claim_review_anchor_protocol_version=1,
            claim_review_adversarial_protocol_version=1, claim_review_wire_schema_version=1, claim_review_focus_version=1,
            claim_review_condition_contract_version=1, reasoning_depth_contract_version=1)
    if human["enabled"] and (not files or all("revision_context" in seed for seed in human["seeds"])):
        # Manual design is a real, immutable user-provided source. The parser
        # handles this descriptor explicitly, never treating Q/A as preapproved
        # recorded conversations or running model document parsing over it.
        destination = path / "inputs" / "human-seeds.json"
        atomic_json(destination, human)
        snapshots.append({"name": "人工设计问答", "file": destination.name, "kind": "human_design",
                          "sha256": file_hash(destination), "bytes": destination.stat().st_size})
    recipe = {"version": recipe_version, "policy": POLICY, "sources": snapshots, "brief": brief.strip(),
              "targets": targets, "backend": backend, "model": model, "judge_backend": judge_backend,
              "judge_model": judge_model, "jev_backend": jev_backend, "jev_model": jev_model,
              "max_units": max_units, "chunk_chars": chunk_chars, "tasks": tasks,
              "conversation_turns": conversation_turns, "math_generator_version": 2,
              **({"cpt_review_policy_version": 1} if "cpt" in targets and not repair_inputs else {}),
              **({"arithmetic_seed_protocol_version": 1, "arithmetic_registry_version": 1}
                 if "gsm8k" in targets and not repair_inputs else {}),
              "planning_policy_version": 2, "source_processing_version": 2, "source_library_version": 1,
              **({"source_cleaning": True, "source_context_pack_version": 1,
                  "source_line_protocol_version": 1, "source_context_capacity_version": 2,
                  "source_context_scope_version": 1} if source_cleaning else {}),
              "source_memory_protocol_version": 2,
              "provider_reasoning_capture_version": 1,
              "authored_reasoning_protocol_version": 1,
              "authored_reasoning_warmup_version": 1,
              "reasoning_depth_contract_version": 1,
              "authored_repair_instruction_protocol_version": 2,
              "reasoning_distillation_audit_version": 1,
              "style_verdict_protocol_version": 2,
              "question_brief_protocol_version": 2,
              "answer_dispatch_protocol_version": 2,
              "multiturn_failure_audit_version": 1,
              "authored_role_projection_version": 3,
              "review_context_protocol_version": 2,
              "turn_review_protocol_version": 1,
              "claim_review_protocol_version": 2,
              "claim_review_format_feedback_version": 2,
              "claim_refutation_release_version": 1,
              "claim_review_quote_matching_version": 1,
              "claim_reference_policy_version": 1,
              "claim_review_experience_scope_version": 1,
              "claim_review_anchor_protocol_version": 1,
              "claim_review_adversarial_protocol_version": 1,
              "claim_review_wire_schema_version": 1,
              "claim_review_focus_version": 1,
              "claim_review_condition_contract_version": 1,
              "claim_review_revision_instruction_version": 1,
              "reasoning_revision_protocol_version": 1,
              "multiturn_terminal_review_version": 1,
              "multiturn_generation_style_version": 1,
              "reasoning_language_contract_version": REASONING_LANGUAGE_CONTRACT_VERSION,
              **({"authored_reasoning_language_review_version": 3} if authored_language_review else {}),
              "knowledge_application_voice_version": 3,
              **({"reasoning_required": True} if reasoning_required else {}),
              "learner_context_review_version": 1,
              "learner_context_retry_version": 1,
              **({"knowledge_agent_context_version": 1,
                  "knowledge_agent_session_rules_version": 1,
                  "knowledge_agent_finalization_version": 1,
                  "knowledge_agent_archive_version": 1,
                  "qa_dialogue_agent_version": 1,
                  "knowledge_agent_instruction": KNOWLEDGE_AGENT_INSTRUCTION + "\n\n" + KNOWLEDGE_AGENT_ARCHIVE_INSTRUCTION}
                 if qa_director.get("context_mode") == "persistent" else {}),
              **({"native_regeneration_version": 1} if (review_repair or {}).get("native_regeneration") else {}),
              **({"repair_reference_sources_version": 1} if (review_repair or {}).get("reference_sources") else {}),
              **({"multiturn_stop_protocol_version": 1} if "multiturn" in targets else {}),
              **({"native_answer_retry_version": 1}
                 if node_generation.get("cot", {}).get("answer_mode") == "native_prefill" else {}),
              **({"independent_cot_input_version": 1} if targets == ["cot"]
                 and node_generation.get("cot", {}).get("enabled") is True else {}),
              "agent_experience_version": 1,
              "agent_experience": experience_snapshot,
              "qa_agent_memory_version": 1,
              "qa_expression_version": 2,
              "reasoning_shape_version": 1,
              "training_surface_protocol_version": 2,
              "cpt_reference_releases": references,
              "evaluation_references": evaluation_references,
              "agent_sandbox_image": agent_sandbox_image,
              "sample_count": sample_count, "concurrency": concurrency, "batch_size": batch_size,
              "node_models": node_models, "web_research": web_research,
              "document_parser": document_parser,
              **({"document_parser_signature": document_parser_signature}
                 if document_parser_signature is not None else {}),
              "node_generation": node_generation,
              "node_prompts": node_prompts,
              "node_prompt_templates": node_prompt_templates,
              "node_prompt_system": node_prompt_system,
              "node_generation_prompts": generation_prompts,
              "reasoning_trim": reasoning_trim,
              "package_review": package_review,
              "qa_director": qa_director,
              "reasoning_trim_prompt": (trim_prompt(reasoning_trim) if (reasoning_trim or {}).get("enabled") else None),
              "generation_preferences": preferences, "sft_output_style": sft_output_style,
              "prompts": prompt_versions()}
    if production is not None:
        recipe["production"] = production
    if review_repair is not None:
        recipe["review_repair"] = review_repair
    if repair_inputs is not None:
        recipe["repair_inputs"] = repair_inputs
    if cpt_processing_supplied:
        recipe["cpt_processing"] = cpt_processing
    if endpoint_pins is not None:
        recipe["endpoint_pins"] = endpoint_pins
    if knowledge_retrieval is not None:
        recipe["knowledge_retrieval"] = validate_knowledge_receipt(knowledge_retrieval, snapshots)
    atomic_json(path / "recipe.json", recipe)
    atomic_json(path / "state.json", {"id": run_id, "name": name[:100], "created_at": now(), "updated_at": now(),
                "status": "queued", "recipe_hash": digest(recipe), "targets": targets, "attempt": 0,
                "reasoning_trim_enabled": bool((reasoning_trim or {}).get("enabled")),
                "package_review_enabled": package_review["enabled"],
                "qa_director_enabled": qa_director["enabled"],
                "human_augmentation_enabled": human["enabled"],
                "stages": {key: {"label": "CPT 语料处理" if key == "cpt" else label,
                                 "status": "pending", "done": 0, "total": 0}
                           for key, label in STAGES.items()
                           if (key != "review" or review_repair is not None)
                           and (key != "jev" or (review_repair is None and package_review["enabled"]
                                                 and package_review_stage(package_review) == "jev"))},
                "events": [], "usage": {}})
    return run_id


class TrainingWorkflowRuntime(WorkflowProduction, WorkflowQADirector, WorkflowKnowledgeAgent, WorkflowCPTProcessing, WorkflowReviewRepair, WorkflowSourceLibrary):
    """Legacy node algorithms and checkpoint machinery, behind execution ports.

    Batch ordering and run lifecycle are composed in bootstrap/workflow_execution.
    New orchestration belongs there, rather than in another runtime mixin.
    """
    def __init__(self, output, run_id, root, *, generator=None, judge=None, jev=None, questioner=None):
        self.path = run_path(output, run_id)
        self.root = Path(root)
        self.state = read_json(self.path / "state.json")
        self.recipe = read_json(self.path / "recipe.json")
        parser = validate_document_parser(self.recipe.get("document_parser"))
        if not self.recipe.get("repair_inputs") and not self.arithmetic_only() and (parser["mode"] == "vision" or parser.get("assistance")):
            from lib.infrastructure.document_vision import require_vision_model
            require_vision_model(self.root, parser.get("assistance",parser)["binding"])
        cpt_config = validate_cpt_processing(self.recipe.get("cpt_processing"))
        if not self.recipe.get("repair_inputs") and not self.arithmetic_only() and cpt_config["mode"] == "model" and cpt_config["review_mode"] == "vision":
            from lib.infrastructure.document_vision import require_vision_model
            binding = self.recipe.get("node_models", {}).get("cpt", {}).get("jev")
            if not binding:
                raise ValueError("cpt_visual_review_model_required")
            require_vision_model(self.root, binding)
        self.generator, self.judge = generator, judge
        self.questioner = questioner
        # Keep the earlier judge injection point usable while all new workflow
        # calls and accounting use the dedicated JEV role.
        self.jev = jev if jev is not None else judge
        self.stage = "ingest"
        self._lock = threading.RLock()
        self._local = threading.local()
        self._client_locks = {}
        self._abort = threading.Event()
        self._last_save = 0.0
        self._research_document = None
        self._production_checkpoints = None
        self._task_budget = None
        if self.production_enabled():
            self._production_checkpoints = ProductionCheckpoints(self.path / "production-checkpoints.sqlite3")

    def save(self, *, force=True):
        with self._lock:
            moment = time.monotonic()
            if not force and moment - self._last_save < 0.5:
                return
            self.state["updated_at"] = now()
            atomic_json(self.path / "state.json", self.state)
            self._last_save = moment

    def event(self, kind, **fields):
        event = {"at": now(), "stage": self.stage, "kind": kind, **fields}
        with self._lock:
            with (self.path / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(canonical(event) + "\n")
            self.state["events"].append(event)
            self.state["events"] = self.state["events"][-300:]
            self.save(force=not kind.startswith("model_"))

    def check_cancel(self):
        if getattr(self, "_production_exporting_partial", False):
            return
        if self._abort.is_set() or (self.path / "cancel.json").exists():
            raise Cancelled()

    def checkpoint(self, key, action):
        self.check_cancel()
        if self._production_checkpoints is not None:
            cached, data = self._production_checkpoints.get(self.stage, digest(key))
            if cached:
                return data
            value = action()
            self._production_checkpoints.put(self.stage, digest(key), value)
            return value
        destination = self.path / "checkpoints" / self.stage / f"{digest(key)}.json"
        if destination.exists():
            saved = read_json(destination)
            if digest(saved["data"]) != saved["sha256"]:
                raise ValueError("checkpoint_integrity_error")
            return saved["data"]
        value = action()
        atomic_json(destination, {"data": value, "sha256": digest(value)})
        return value

    def checkpoint_has(self, key, *, stage=None):
        if self._production_checkpoints is not None:
            return self._production_checkpoints.get(stage or self.stage, digest(key))[0]
        return (self.path / "checkpoints" / (stage or self.stage) / f"{digest(key)}.json").exists()

    def invalidate_checkpoint(self, key, *, stage=None):
        if self._production_checkpoints is not None:
            self._production_checkpoints.delete(stage or self.stage, digest(key))
        (self.path / "checkpoints" / (stage or self.stage) / f"{digest(key)}.json").unlink(missing_ok=True)

    def client(self, role, *, stage=None):
        stage = stage or self.stage
        if role == "vision":
            from lib.infrastructure.document_vision import require_vision_model
            parser = validate_document_parser(self.recipe.get("document_parser"))
            require_vision_model(self.root, parser.get("assistance",parser)["binding"])
        field = {"judge": "judge", "jev": "jev", "question": "questioner"}.get(role, "generator")
        client = getattr(self, field)
        if client is None:
            cache = getattr(self._local, "clients", None)
            if cache is None:
                cache = self._local.clients = {}
            cache_key = (stage, role)
            client = cache.get(cache_key)
            if client is None:
                prefix = f"{role}_" if role in {"judge", "jev"} else ""
                binding = self.recipe.get("node_models", {}).get(stage, {}).get(role, {})
                pins = self.recipe.get("endpoint_pins")
                if pins is not None and not isinstance(pins, dict):
                    raise ValueError("workflow_endpoint_pin_invalid")
                stage_pins = pins.get(stage, {}) if pins is not None else {}
                if not isinstance(stage_pins, dict):
                    raise ValueError("workflow_endpoint_pin_invalid")
                pin = stage_pins.get(role)
                if pins is not None and binding and (not isinstance(pin, dict) or
                        pin.get("backend") != binding.get("backend") or pin.get("model") != binding.get("model")):
                    raise ValueError("workflow_endpoint_pin_invalid")
                client, _ = load_backend(self.root, backend=binding.get("backend") or self.recipe.get(prefix + "backend"),
                                         model=binding.get("model") or self.recipe.get(prefix + "model"), role=role,
                                         allow_global_endpoint_override=not bool(binding),
                                         context_window_tokens=binding.get("context_window_tokens"),
                                         **({"reasoning": binding["reasoning"]} if "reasoning" in binding else {}),
                                         **({"additional_budget": self._task_budget} if self._task_budget is not None else {}),
                                         **({"expected_endpoint_pin": pin} if pin is not None else {}))
                cache[cache_key] = client
        if self._task_budget is not None and isinstance(client, ChatClient):
            client.additional_budget = self._task_budget
        if isinstance(client, ChatClient):
            client.cancel_check = self.check_cancel
        endpoint_identity = str(getattr(getattr(client, "client", None), "base_url", "injected"))
        api_format = str(getattr(client, "api_format", "chat"))
        if api_format != "chat":
            endpoint_identity += "|" + api_format
        identity = {"model": getattr(client, "model", type(client).__name__),
                    "endpoint_hash": digest(endpoint_identity)}
        model_key = f"{stage}.{role}" if self.recipe.get("node_models", {}).get(stage, {}).get(role) else role
        with self._lock:
            pinned = self.state.setdefault("models", {}).get(model_key)
            if pinned and pinned != identity:
                raise ValueError("model_configuration_changed_create_new_run")
            self.state["models"][model_key] = identity
            self._client_locks.setdefault(id(client), threading.Lock())
            self.save(force=False)
        return client

    def historical_prompt(self, prompt_id):
        """Resolve a pre-node-template recipe by its saved asset version.

        Older catalogs may omit later prompt ids. Their historical fallback
        is version 1.0.0, never the newest registered default. A saved pin must
        still match the retained version's exact template hash.
        """
        pins = self.recipe.get("prompts", {})
        if not isinstance(pins, dict):
            raise ValueError("prompts_changed_create_new_run")
        pinned = pins.get(prompt_id)
        if pinned is not None and (not isinstance(pinned, dict) or set(pinned) != {"version", "sha256"}
                or not isinstance(pinned.get("version"), str)):
            raise ValueError("prompts_changed_create_new_run")
        version = pinned["version"] if pinned is not None else "1.0.0"
        try:
            spec = get(prompt_id, version=version)
        except (KeyError, TypeError) as error:
            raise ValueError("prompts_changed_create_new_run") from error
        if pinned is not None and digest(spec.template) != pinned["sha256"]:
            raise ValueError("prompts_changed_create_new_run")
        return spec

    def prompt_text(self, prompt_id, *, stage=None):
        """Resolve by node as well as step, without formatting user-owned text."""
        if self.recipe.get("version", 0) >= 9:
            try:
                text = self.recipe["node_prompt_templates"][stage or self.stage][prompt_id]
            except (KeyError, TypeError) as exc:
                raise ValueError("invalid_node_prompt_snapshot") from exc
            if not isinstance(text, str) or not text.strip():
                raise ValueError("invalid_node_prompt_snapshot")
            return text
        return render(self.historical_prompt(prompt_id))

    def ask(self, key, role, prompt_id, data, *, image=None, instruction=None,
            allow_reasoning_fallback=True, model_stage=None, prompt_stage=None,
            capture_response_details=False, reasoning_prefix=None, native_request_attempts=None):
        image_values=([image] if isinstance(image,str) else image) if image is not None else []
        if (not isinstance(image_values,list) or len(image_values)>8
                or any(not isinstance(value,str) or not value for value in image_values)):
            raise ValueError("invalid_document_images")
        if native_request_attempts is not None and (
                prompt_id != "workflow.cot_native_answer" or type(native_request_attempts) is not int
                or not 1 <= native_request_attempts <= 10):
            raise ValueError("invalid_model_request_attempts")
        capture = (capture_response_details
                   and self.recipe.get("provider_reasoning_capture_version") == 1)
        if (self.recipe.get("review_repair") is not None and role == "jev"
                and (model_stage or self.stage) in {"sft", "multiturn", "preference", "cot", "trim"}):
            # Generation-side contract/turn checks use that node's own model.
            # Only the review node owns the optional separate scoring model.
            role = "generation"
        journal = None
        def invoke():
            nonlocal journal
            self.check_cancel()
            client = self.client(role, stage=model_stage) if model_stage is not None else self.client(role)
            with self._client_locks[id(client)]:
                self.check_cancel()
                before = dict(getattr(client, "usage", {}))
                self.event("model_started", role=role)
                try:
                    binding = self.recipe.get("node_models", {}).get(model_stage or self.stage, {}).get(role, {})
                    streaming = {}
                    if isinstance(client, ChatClient):
                        first = key[0] if isinstance(key, (list, tuple)) and key else key
                        unit = first if isinstance(first, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", first) else digest(key)[:16]
                        journal = StreamJournal(self.path, stage=self.stage, unit=unit, role=role,
                                                run_attempt=self.state.get("attempt", 0),
                                                checkpoint=digest(["call", key]))
                        def received(event):
                            self.check_cancel()
                            if event.get("type") == "queued":
                                self.event("model_queued", role=role,
                                           **{name: event[name] for name in
                                              ("position", "active", "queued", "cooldown_seconds") if name in event})
                            journal(event)
                        streaming["on_stream"] = received
                    base_system = (self.recipe["node_prompt_system"] if self.recipe.get("version", 0) >= 9
                                   else render(self.historical_prompt("workflow.system")))
                    system = base_system + "\n" + (self.prompt_text(prompt_id, stage=prompt_stage)
                        if prompt_stage is not None else self.prompt_text(prompt_id))
                    stable_instruction, dynamic_review_instruction = instruction, None
                    if (isinstance(data, dict) and data.get("claim_review_protocol_version") == 4
                            and (self.recipe.get("claim_review_wire_schema_version") == 1
                                 or self.recipe.get("claim_review_focus_version") == 1
                                 or self.recipe.get("claim_review_condition_contract_version") == 1)
                            and isinstance(instruction, str)):
                        from lib.infrastructure.claim_review_wire_schema import CLAIM_REVIEW_DYNAMIC_WIRE_HEADER
                        if CLAIM_REVIEW_DYNAMIC_WIRE_HEADER in instruction:
                            stable_instruction, dynamic_review_instruction = instruction.split(CLAIM_REVIEW_DYNAMIC_WIRE_HEADER, 1)
                    from lib.infrastructure.workflow_node_messages import split_node_rules
                    rule_system, request_data = split_node_rules(
                        data, instruction=stable_instruction, qa_director=self.recipe.get("qa_director"),
                        knowledge_application_voice_version=self.recipe.get("knowledge_application_voice_version"))
                    from lib.infrastructure.document_reading_context import project_reading_context
                    request_data = project_reading_context(request_data)
                    if dynamic_review_instruction is not None:
                        # Scope hashes and current ids belong after the shared
                        # system/source prefix, not before its reusable memory.
                        request_data["node_task_instructions"] = dynamic_review_instruction
                    memory_version = self.recipe.get("source_memory_protocol_version")
                    sealed_review_request = (memory_version in {1, 2}
                                             and (memory_version == 2 or bool(self.recipe.get("repair_inputs")))
                                             and isinstance(data, dict)
                                             and isinstance(data.get("source_evidence"), dict)
                                             and not data.get("learner_context_only"))
                    source_pack_request = ((self.recipe.get("source_context_pack_version") == 1 or sealed_review_request)
                                           and image is None and isinstance(data, dict)
                                           and not data.get("learner_context_only"))
                    memory_context = None
                    if source_pack_request:
                        from lib.infrastructure.model_context_prefix import serialize_context_request, split_context_request, NODE_RULE_HEADER
                        if memory_version in {1, 2}:
                            if sealed_review_request:
                                from lib.infrastructure.source_memory_messages import sealed_review_memory_context
                                memory_context = sealed_review_memory_context(request_data, protocol_version=memory_version)
                            else:
                                memory_context = split_context_request(self.path, request_data, protocol_version=memory_version)
                            request_text = canonical(memory_context["task_payload"])
                            has_pack = bool(memory_context["teacher_context_pack"])
                        else:
                            request_text = serialize_context_request(self.path, request_data)
                            has_pack = request_text.startswith('{"teacher_context_pack":')
                        if has_pack:
                            pinned_prompt = (self.prompt_text(prompt_id, stage=prompt_stage)
                                             if prompt_stage is not None else self.prompt_text(prompt_id))
                            system = base_system + NODE_RULE_HEADER + "\n" + pinned_prompt
                    else:
                        request_text = canonical(request_data)
                    # Procedural experience belongs to the fixed system prefix,
                    # never to source facts, a user turn, or a generated answer.
                    from lib.infrastructure.workflow_agent_experience import experience_system
                    system += experience_system(self.recipe, prompt_id) + rule_system
                    request_messages = [
                        {"role": "system", "content": system},
                        {"role": "user", "content": request_text if image is None else [
                            {"type": "text", "text": request_text},
                            *[{"type": "image_url", "image_url": {"url": value}} for value in image_values]]}]
                    if source_pack_request and memory_version in {1, 2}:
                        from lib.infrastructure.source_memory_messages import source_memory_messages
                        transport = ("system" if memory_version == 2 or prompt_id == "workflow.cot_native_answer" else
                                     getattr(client, "source_memory_transport", "tool"))
                        if memory_context.get("claim_review_protocol_version") in {1, 2, 3, 4}:
                            transport = "system"
                        request_messages = source_memory_messages(system, memory_context, transport=transport)
                        if memory_context["teacher_context_pack"] or memory_context.get("claim_review_protocol_version") in {1, 2, 3, 4}:
                            with self._lock:
                                self.state.setdefault("source_memory_transports", {})[
                                    f"{model_stage or self.stage}.{role}"] = transport
                    if self.recipe.get("qa_director", {}).get("question_separation"):
                        from lib.infrastructure.dialogue_role_messages import answer_role_messages, question_role_messages
                        transport = ("system" if memory_version == 2 or prompt_id == "workflow.cot_native_answer" else
                                     getattr(client, "source_memory_transport", "tool"))
                        if prompt_id == "workflow.question_user":
                            from lib.domain.learner_context import prompt_at_least
                            first_turn_version = (2 if self.recipe.get("authored_role_projection_version", 1) >= 3
                                and prompt_at_least(self.recipe, prompt_id, (1, 2, 0)) else 1)
                            request_messages = question_role_messages(system, request_data, transport=transport,
                                first_turn_policy_version=first_turn_version,
                                question_brief_protocol_version=self.recipe.get("question_brief_protocol_version", 1),
                                **({"expression_guide_version": 2} if self.recipe.get("qa_expression_version") == 2 else {}))
                        elif prompt_id in {"workflow.sft_answer", "workflow.multiturn_assistant"}:
                            request_messages = answer_role_messages(system, memory_context, request_data, transport=transport,
                                source_memory_protocol_version=memory_version if memory_version in {1, 2} else None,
                                answer_dispatch_protocol_version=self.recipe.get("answer_dispatch_protocol_version", 1))
                    if (self.recipe.get("training_surface_protocol_version") in {1, 2}
                            and prompt_id == "workflow.sft_directed"
                            and isinstance(request_data, dict) and request_data.get("learner_messages")):
                        from lib.infrastructure.dialogue_role_messages import answer_role_messages
                        request_messages = answer_role_messages(system, memory_context, request_data,
                            transport="system" if memory_version == 2 else getattr(client, "source_memory_transport", "tool"),
                            include_question=True, source_memory_protocol_version=memory_version if memory_version in {1, 2} else None,
                            answer_dispatch_protocol_version=self.recipe.get("answer_dispatch_protocol_version", 1))
                    if (self.recipe.get("training_surface_protocol_version") in {1, 2}
                            and prompt_id in {"workflow.cot_generate", "workflow.cot_rationale", "workflow.cot_native_answer", "workflow.trim"}):
                        from lib.infrastructure.dialogue_role_messages import authored_role_messages
                        # DeepSeek beta disallows function-call history with
                        # prefix. Use its explicit private-memory transport;
                        # never erase or reclassify real learner tool turns.
                        transport = ("system" if memory_version == 2 or prompt_id == "workflow.cot_native_answer" else
                                     getattr(client, "source_memory_transport", "tool"))
                        authored_messages = authored_role_messages(system, memory_context, request_data, transport=transport,
                            role_projection_version=self.recipe.get("authored_role_projection_version", 1),
                            phase={"workflow.cot_rationale": "rationale", "workflow.cot_native_answer": "native_answer",
                                   "workflow.trim": "trim"}.get(prompt_id, "combined"),
                            source_memory_protocol_version=memory_version if memory_version in {1, 2} else None)
                        if authored_messages is not None:
                            request_messages = authored_messages
                        if prompt_id == "workflow.cot_native_answer":
                            with self._lock:
                                self.state.setdefault("source_memory_transports", {})[
                                    f"{model_stage or self.stage}.{role}.native_answer"] = transport
                    visible_only = (capture or not allow_reasoning_fallback or prompt_id in {
                        "workflow.sft_styled", "workflow.cot_generate", "workflow.style_check",
                        "workflow.cot_rationale", "workflow.cot_native_answer", "workflow.cot_prefix_check",
                        "workflow.trim", "workflow.trim_check", "workflow.trim_rules_check"})
                    response_details = {}
                    if prompt_id == "workflow.cot_native_answer":
                        # Prefix completion returns the real visible answer,
                        # not a model-owned JSON envelope. No text is invented
                        # or decoded from native reasoning to fill an answer.
                        visible = client.chat(request_messages, max_tokens=binding.get("max_output_tokens"),
                            reasoning_prefix=reasoning_prefix, allow_reasoning_fallback=False,
                            response_details=response_details, **streaming,
                            **({"retries": native_request_attempts} if native_request_attempts is not None else {}))
                        # Early beta recipes explicitly requested this exact
                        # one-field JSON schema. Decode their actual field,
                        # rather than double-wrap JSON as user-visible text.
                        # Current plain-text recipes retain the raw answer.
                        if '仅返回JSON：{"answer"' in self.prompt_text(prompt_id, stage=prompt_stage):
                            from lib.llm_client import parse_json_robust
                            try:
                                decoded = parse_json_robust(visible)
                            except ValueError:
                                decoded = None
                            if isinstance(decoded, dict) and set(decoded) == {"answer"} and isinstance(decoded["answer"], str):
                                response_details["visible_response_envelope"] = visible
                                visible = decoded["answer"]
                        if text_issue(visible):
                            raise ValueError("native_prefill_invalid_answer")
                        value = {"answer": visible}
                    else:
                        value = chat_json(client, request_messages,
                            max_tokens=binding.get("max_output_tokens"), **streaming,
                            **({"response_details": response_details} if capture and isinstance(client, ChatClient) else {}),
                            **({"reasoning_prefix": reasoning_prefix} if reasoning_prefix is not None else {}),
                            **({"allow_reasoning_fallback": False} if visible_only else {}))
                    if capture:
                        # The envelope is created by the worker, outside the model's
                        # JSON. It is committed atomically with this exact answer.
                        response_details["checkpoint"] = {"stage": self.stage,
                            "sha256": digest(["call", key])}
                        return {"model_json_response_version": 1, "data": value,
                                "response_details": response_details}
                    return value
                finally:
                    after = getattr(client, "usage", {})
                    with self._lock:
                        usage = self.state["usage"].setdefault(role, {})
                        for metric in ("calls", "prompt_tokens", "completion_tokens"):
                            usage[metric] = usage.get(metric, 0) + max(0, after.get(metric, 0) - before.get(metric, 0))
                        self.event("model_finished", role=role)
        try:
            value = self.checkpoint(["call", key], invoke)
        except BaseException:
            if journal is not None:
                try:
                    journal.interrupted()
                except (OSError, ValueError):
                    pass  # Preserve the original failure, never its provider message.
            raise
        if journal is not None:
            try:
                journal.completed()
            except (OSError, ValueError):
                pass  # The trusted checkpoint is already durable and reusable.
        if capture:
            from lib.model_response_details import ModelJSONResult
            if (not isinstance(value, dict) or value.get("model_json_response_version") != 1
                    or set(value) != {"model_json_response_version", "data", "response_details"}
                    or not isinstance(value["response_details"], dict)):
                raise ValueError("checkpoint_integrity_error")
            return (ModelJSONResult(value["data"], value["response_details"])
                    if isinstance(value["data"], dict) else value["data"])
        return value

    def stage_items(self, stage, items, action, *, stream_sources=False, finalize=True):
        from lib.infrastructure.workflow_stage_order import StageResultOrder

        self.stage = stage
        self._abort.clear()
        metrics = self.state["stages"][stage]
        batch_size = self.recipe.get("batch_size", 100)
        workers = self.recipe.get("concurrency", 1) if stage not in {"ingest", "agent", "gsm8k"} else 1
        metrics.update(status="running", done=0, total=len(items), started_at=now(),
                       outputs=0, eligible=0, quarantined=0, cached=0,
                       rate_per_minute=None, eta_seconds=None,
                       batch_size=batch_size, concurrency=workers, batches_done=0,
                       batches_total=(len(items) + batch_size - 1) // batch_size)
        metrics.pop("error", None)
        self.event("stage_started")
        directory = self.path / "stage-results"
        directory.mkdir(exist_ok=True)
        destination = directory / f"{stage}.jsonl"
        pending = directory / f".{stage}.pending"
        live = directory / f".{stage}.live.jsonl"
        processing_started = None
        first_error = None
        def process(index, item):
            nonlocal processing_started, first_error
            try:
                self.check_cancel()
                checkpoint_key = ["item", index, digest(item)]
                if stage == "agent":
                    checkpoint_key.extend((REPLAY_POLICY_VERSION, RUNNER_SHA256,
                                           self.recipe.get("agent_sandbox_image")))
                cached = self.checkpoint_has(checkpoint_key)
                if not cached:
                    with self._lock:
                        if processing_started is None:
                            processing_started = time.monotonic()
                value = (row_checkpoint(self.path / "checkpoints" / self.stage / f"{digest(checkpoint_key)}.json",
                                        lambda: self.iter_source_units(item), self.check_cancel)
                         if stream_sources else self.checkpoint(checkpoint_key, lambda: self.run_item_safely(stage, item, action)))
                with self._lock:
                    metrics["done"] += 1
                    metrics["outputs"] += len(value)
                    metrics["eligible"] += sum(row.get("status") in {"ready", "eligible"} for row in value)
                    metrics["quarantined"] += sum(row.get("status") == "quarantined" for row in value)
                    metrics["cached"] += int(cached)
                    processed = metrics["done"] - metrics["cached"]
                    if processed and processing_started is not None:
                        elapsed = max(0.001, time.monotonic() - processing_started)
                        metrics["rate_per_minute"] = round(processed * 60 / elapsed, 1)
                        metrics["eta_seconds"] = round((metrics["total"] - metrics["done"]) * elapsed / processed)
                    self.save(force=False)
                return value
            except BaseException as error:
                # Stop replenishment as soon as a worker fails, before the
                # coordinator observes its future. Preserve the original error
                # when sibling requests subsequently report cancellation.
                with self._lock:
                    if first_error is None:
                        first_error = error
                    self._abort.set()
                raise
        source_iterator = iter(items)
        iterator = enumerate(source_iterator)
        with ExitStack() as resources, pending.open("w", encoding="utf-8") as handle, \
                live.open("w", encoding="utf-8") as preview, ThreadPoolExecutor(max_workers=workers) as executor:
            if callable(close_iterator := getattr(source_iterator, "close", None)):
                resources.callback(close_iterator)
            ordered = resources.enter_context(StageResultOrder(directory, stage))
            futures = {}
            exhausted = False
            completed = 0
            def replenish():
                nonlocal exhausted
                while not exhausted and len(futures) < workers:
                    self.check_cancel()
                    try:
                        index, item = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    futures[executor.submit(process, index, item)] = index
            try:
                replenish()
                while futures:
                    # Only the running window is submitted. A completed request
                    # releases its slot immediately, regardless of batch_size or
                    # an earlier item's latency.
                    future = next(as_completed(futures))
                    index = futures.pop(future)
                    value = future.result()
                    ordered.append(index, value, preview)
                    del value, future
                    if ordered.flush(handle):
                        self.save(force=False)
                    completed += 1
                    batches_done = completed // batch_size
                    if completed == metrics["total"]:
                        batches_done = metrics["batches_total"]
                    if batches_done != metrics["batches_done"]:
                        metrics["batches_done"] = batches_done
                        self.save()
                    replenish()
                # Cancellation after the last completion must not promote an
                # interrupted attempt into the canonical stage artifact.
                self.check_cancel()
            except BaseException as error:
                self._abort.set()
                for future in futures:
                    future.cancel()
                if first_error is not None and isinstance(error, Cancelled):
                    raise first_error
                raise
            handle.flush()
        pending.replace(destination)
        if finalize:
            metrics.update(status="completed", finished_at=now())
            self.event("stage_completed", outputs=metrics["outputs"])
        else:
            metrics.pop("finished_at", None)
            self.save()
        return WorkflowRows(destination, metrics["outputs"])

    def rejected(self, unit, reason):
        provenance = {key: unit[key] for key in ("source_name", "location", "source_location") if key in unit}
        return {"id": unit["id"], "source_id": unit.get("source_id", unit["id"]),
                **provenance, "status": "quarantined", "reason": reason,
                **self.qa_metadata(unit)}

    def parse_source(self, source):
        """Compatibility entry point; execution consumes the streaming iterator."""
        return list(self.iter_source_units(source))

    def cpt_source_image(self, unit):
        """Load a bounded original-page raster from this run's fixed inputs."""
        from collections import OrderedDict
        from lib.infrastructure.document_vision import visual_part
        location = (unit.get("source_location") or {}).get("record")
        if not isinstance(location, str):
            return None
        candidates = [source for source in self.recipe["sources"]
                      if source["sha256"] == unit.get("source_id")
                      and source["name"] == unit.get("source_name")]
        if not candidates:
            return None
        source = candidates[0]
        path = self.path / "inputs" / source["file"]
        if Path(source["file"]).name != source["file"] or path.is_symlink():
            raise ValueError("source_snapshot_changed")
        with self._lock:
            info = path.stat()
            stamp = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)
            verified = getattr(self, "_visual_source_verified", {})
            if verified.get(source["file"]) != stamp:
                if file_hash(path) != source["sha256"]:
                    raise ValueError("source_snapshot_changed")
                verified[source["file"]] = stamp
                self._visual_source_verified = verified
            cache = getattr(self, "_visual_source_cache", OrderedDict())
            key = (source["file"], location, stamp)
            if key in cache:
                cache.move_to_end(key)
                part = cache[key]
            else:
                part = visual_part(path, location)
                final_info = path.stat()
                if (final_info.st_size, final_info.st_mtime_ns, final_info.st_ctime_ns, final_info.st_ino) != stamp:
                    raise ValueError("source_snapshot_changed")
                cache[key] = part
                while len(cache) > 16:
                    cache.popitem(last=False)
                self._visual_source_cache = cache
            expected = (unit.get("document_reading") or {}).get("image_sha256")
            if part and expected and part["image_sha256"] != expected:
                raise ValueError("document_source_image_changed")
            return part["image"] if part else None

    def iter_source_units(self, source):
        manual = self.recipe.get("qa_director", {}).get("human_augmentation", {})
        revision_round = bool(manual.get("enabled") and manual.get("seeds")
                              and all("revision_context" in seed for seed in manual["seeds"]))
        if ((self.recipe.get("source_library_version") == 1
                or self.recipe.get("document_parser", {}).get("mode") == "docling")
                and source.get("kind") != "human_design" and not revision_round):
            yield from self._iter_library_source_units(source)
            return
        yield from self._iter_source_units_uncached(source)

    def _iter_source_units_uncached(self, source):
        path = self.path / "inputs" / source["file"]
        if file_hash(path) != source["sha256"]:
            raise ValueError("source_snapshot_changed")
        source_id = source["sha256"]
        def unit(index, **fields):
            return {"id": digest([source_id, source["file"], index]), "source_id": source_id,
                    "source_name": source["name"], "location": index, **fields}
        manual_config = self.recipe.get("qa_director", {}).get("human_augmentation", {})
        revision_round = bool(manual_config.get("enabled") and manual_config.get("seeds")
                              and all("revision_context" in seed for seed in manual_config["seeds"]))
        if revision_round and source.get("kind") != "human_design":
            # Actual document copies remain pinned in the child manifest. The
            # selected sealed parent's evidence supplies each revision unit;
            # unrelated source chunks must not consume its candidate budget.
            return
        if source.get("kind") == "human_design":
            from lib.domain.human_augmentation import validate_human_augmentation
            manual = validate_human_augmentation(read_json(path))
            if manual != self.recipe.get("qa_director", {}).get("human_augmentation"):
                raise ValueError("human_augmentation_snapshot_changed")
            for index, seed in enumerate(manual["seeds"]):
                revision = seed.get("revision_context")
                if revision:
                    from lib.infrastructure.human_revision import verified_revision_parent
                    revision_parent = verified_revision_parent(self.path.parent.parent, self.state["id"], revision)
                    if (revision["source_kind"] == "document" and not any(
                            item.get("kind") != "human_design" and item["sha256"] == revision["source_id"]
                            for item in self.recipe["sources"])):
                        raise ValueError("human_session_source_changed")
                    yield {**unit(index, kind="brief" if revision["source_kind"] == "synthetic" else "document",
                                 text=revision["teacher_evidence"], status="ready",
                                 human_provided=revision["source_kind"] == "human_provided",
                                 revision_source_level=revision_parent.get("evidence_level"),
                                 human_design=human_design(manual, seed_id=seed["id"], validated=True),
                                 source_location={"file": source["name"], "seed": index + 1,
                                                  "parent_run_id": revision["parent_run_id"]}),
                           "source_id": revision["source_id"]}
                    continue
                yield unit(index, kind="document", text=seed["answer"], status="ready",
                           human_provided=True, human_design=human_design(manual, seed_id=seed["id"], validated=True),
                           source_location={"file": source["name"], "seed": index + 1})
            return
        def documents(text, index):
            # Preserve numbers, headings and code; do not strip numeric lines as page noise.
            text = text.replace("\r\n", "\n").strip()
            issue = text_issue(text)
            if issue:
                return [unit(index, status="quarantined", reason=issue)]
            knowledge_hits = (self.recipe.get("knowledge_retrieval") or {}).get("hits", [])
            retrieved_fragment = (self.recipe.get("version", 0) >= 14 and any(
                (hit.get("snapshot") or {}).get("file") == source["file"] for hit in knowledge_hits))
            if retrieved_fragment:
                # Retrieval already split and pinned this complete fragment.
                # Its Markdown storage suffix must not split formulas again.
                chunks = [text]
            elif path.suffix.lower() in {".tex", ".latex"}:
                from lib.infrastructure.latex_document import chunk_latex
                chunks = chunk_latex(text, self.recipe["chunk_chars"])
            else:
                chunks = chunk_text(text, self.recipe["chunk_chars"],
                                    source_processing_version=self.recipe.get("source_processing_version", 1))
            return [unit(f"{index}:chunk:{i}", source_location={"file": source["name"],
                         "record": index, "chunk": i}, status="quarantined", reason="oversized_source_block")
                    if len(chunk) > max(12000, self.recipe["chunk_chars"] * 4)
                    else unit(f"{index}:chunk:{i}", source_location={"file": source["name"],
                              "record": index, "chunk": i}, kind="document", text=chunk, status="ready")
                    for i, chunk in enumerate(chunks)]
        parser_mode = self.recipe.get("document_parser", {}).get("mode", "native")
        if parser_mode == "model" or (path.suffix == ".pdf" and parser_mode == "native" and (
                self.recipe.get("version", 0) >= 14 or self.recipe.get("source_processing_version", 1) >= 2)):
            from lib.infrastructure.document_text import text_parts
            try:
                for part in text_parts(path, source_processing_version=self.recipe.get("source_processing_version", 1)):
                    self.check_cancel()
                    location, text = part["location"], part["text"]
                    if part.get("requires_vision"):
                        yield unit(location, status="quarantined", reason="document_text_requires_vision",
                                   source_location={"file": source["name"], "record": location})
                        continue
                    if not text.strip():
                        yield unit(location, status="skipped", reason="empty_document_page",
                                   source_location={"file": source["name"], "record": location})
                        continue
                    for segment in documents(text, location):
                        if segment["status"] != "ready" or parser_mode == "native":
                            yield segment
                            continue
                        key = [segment["id"], "document_parse", digest(segment["text"])]
                        result = self.ask(key, "generation", "workflow.document_parse", {
                            "source": source["name"], "location": segment["location"], "text": segment["text"]},
                            allow_reasoning_fallback=False)
                        if (not isinstance(result, dict) or set(result) != {"text", "uncertain"}
                                or type(result.get("uncertain")) is not bool or not isinstance(result.get("text"), str)
                                or not result["text"].strip() or len(result["text"]) > 80_000):
                            self.invalidate_checkpoint(["call", key])
                            yield {**self.rejected(segment, "invalid_document_parse_schema")}
                            continue
                        if result["uncertain"]:
                            yield {**self.rejected(segment, "document_parse_uncertain")}
                            continue
                        reading = {"mode": "model", "source_text": segment["text"],
                                   "source_text_sha256": digest(segment["text"]),
                                   "parsed_text": result["text"], "parsed_text_sha256": digest(result["text"]),
                                   "evidence_level": "model_restructured_source_text"}
                        for index, row in enumerate(documents(result["text"], location)):
                            row["id"] = digest([segment["id"], "parsed", index])
                            row["document_reading"] = reading
                            yield row
            except UnicodeError:
                yield unit("document", status="quarantined", reason="invalid_encoding")
            except ValueError as error:
                if str(error) in {"checkpoint_integrity_error", "source_snapshot_changed", "workflow_endpoint_pin_invalid",
                                  "model_configuration_changed_create_new_run", "prompts_changed_create_new_run"}:
                    raise
                yield unit("document", status="quarantined", reason=str(error))
            return
        if (self.recipe.get("document_parser", {}).get("mode") == "vision"
                and path.suffix in {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"}):
            from lib.infrastructure.document_vision import visual_parts
            try:
                for part in visual_parts(path, source_processing_version=self.recipe.get("source_processing_version", 1)):
                    self.check_cancel()
                    location = part["location"]
                    if "image" in part:
                        result = self.ask([source_id, location, part["image_sha256"], "document_vision"],
                                          "vision", "workflow.document_vision",
                                          {"source": source["name"], "location": location}, image=part["image"])
                        if (not isinstance(result, dict) or result.get("uncertain") is not False
                                or not isinstance(result.get("text"), str) or len(result["text"]) > 120_000):
                            yield unit(location, status="quarantined", reason="document_vision_uncertain",
                                       source_location={"file": source["name"], "record": location})
                            continue
                        text = result["text"]
                    else:
                        text = part["text"]
                    for row in documents(text, location):
                        if "image" in part:
                            row["document_reading"] = {"mode": "vision", "image_sha256": part["image_sha256"],
                                                       "evidence_level": "model_transcribed_visual_source"}
                        yield row
            except ValueError as error:
                source_errors = {"document_docx_parse_failed", "document_docx_expansion_limit",
                                 "document_pdf_parse_failed", "document_pdf_page_invalid",
                                 "document_vision_page_limit", "invalid_document_image",
                                 "document_image_too_large", "document_external_image_unsupported"}
                if self.recipe.get("source_processing_version", 1) < 2 or str(error) not in source_errors:
                    raise
                yield unit("document", status="quarantined", reason=str(error),
                           source_location={"file": source["name"], "record": "document"})
            return
        if path.suffix in SUPPORTED_EXTS:
            try:
                content = import_text(path, source_processing_version=self.recipe.get("source_processing_version", 1))
            except UnicodeError:
                yield unit("document", status="quarantined", reason="invalid_encoding")
                return
            except ValueError as error:
                if (self.recipe.get("source_processing_version", 1) < 2 or not (
                        str(error) in {"document_docx_parse_failed", "document_docx_expansion_limit"}
                        or str(error).startswith("latex_"))):
                    raise
                yield unit("document", status="quarantined", reason=str(error),
                           source_location={"file": source["name"], "record": "document"})
                return
            yield from documents(content, "document")
            return
        if path.suffix == ".jsonl":
            def source_records():
                with path.open(encoding="utf-8-sig") as handle:
                    for index, line in enumerate(handle, 1):
                        if (index - 1) % 100 == 0:
                            self.check_cancel()
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            row = None
                        yield index, row
            records = source_records()
        else:
            records = enumerate(iter_source_json_records(path), 1)
        for index, row in records:
            if not isinstance(row, dict):
                yield unit(index, status="quarantined", reason="invalid_json_record")
                continue
            if isinstance(row.get("text"), str) and not (row.get("messages") or row.get("conversations")):
                yield from documents(row["text"], index)
                continue
            try:
                if "request" in row and "response" in row:
                    from lib.adapters.rollout_import import record_status, record_to_sample
                    if record_status(row) != "ok" or row["request"].get("messageOffset", 0):
                        raise ValueError("incomplete_rollout_context")
                    sample = record_to_sample(row)
                else:
                    from lib.review_editor import normalize_sample
                    sample = normalize_sample(row)
                issue = source_conversation_issue(sample, self.recipe["targets"])
                tool_snapshots = sample.get("tool_snapshots")
                if issue:
                    yield unit(index, status="quarantined", reason=issue)
                    continue
                yield unit(index, kind="conversation", messages=sample["messages"],
                                   tools=sample.get("tools", []),
                                   tool_snapshots=tool_snapshots if "agent" in self.recipe["targets"] else None,
                                   verification_status=sample.get("verification_status"), status="ready")
            except (ValueError, TypeError, KeyError, AttributeError):
                yield unit(index, status="quarantined", reason="invalid_or_incomplete_conversation")

    def plan(self):
        self.stage = 'ingest'
        self._research_document = self.research()
        destination = self.path / "checkpoints" / "ingest" / f"{digest('planned_tasks')}.json"
        cached = destination.exists()
        def rows():
            with closing(PlanningIdentities(self.path / "planning-identities.sqlite3")) as seen:
                yield from self._plan_rows(seen)
        planned = row_checkpoint(destination, rows, self.check_cancel)
        if cached:
            batches = (len(planned) + PLAN_BATCH_SIZE - 1) // PLAN_BATCH_SIZE
            self.state['stages']['ingest'].update(status='completed', phase='planning', done=len(planned),
                total=len(planned), outputs=len(planned), eligible=len(planned), cached=len(planned),
                batches_done=batches, batches_total=batches, batch_size=PLAN_BATCH_SIZE,
                concurrency=1, rate_per_minute=None, eta_seconds=None)
        self.state['stages']['ingest'].update(status='completed', finished_at=now())
        self.state['stages']['ingest'].pop('error', None)
        self.save()
        self.event('stage_completed', outputs=len(planned), cached=self.state['stages']['ingest'].get('cached', 0))
        return planned

    def research(self):
        config = self.recipe.get("web_research")
        if config is None:
            return None
        self.stage = "ingest"
        key = ["web_research", config]
        destination = self.path / "checkpoints" / "ingest" / f"{digest(key)}.json"
        cached = destination.exists()
        if not cached:
            self.event("web_search_started", provider="brave")
        def collect():
            queries = [config["query"], *config.get("more_queries", [])]
            if len(queries) == 1:
                return {"provider": "brave", "query": config["query"],
                        "retrieved_at": now(), "results": search_web(config, root=self.root,
                                                                      before_query=self.check_cancel),
                        "note": "Search snippets are planning leads, not independent fact verification."}
            topics = []
            for query in queries:
                topic_config = {"provider": "brave", "query": query, "count": config["count"]}
                topic_key = ["web_research_topic", config, query]
                topic_path = self.path / "checkpoints" / "ingest" / f"{digest(topic_key)}.json"
                if topic_path.is_symlink() or topic_path.parent.is_symlink():
                    raise ValueError("web_research_integrity_error")
                def fetch_topic(topic_config=topic_config, query=query):
                    return {"provider": "brave", "query": query, "retrieved_at": now(),
                            "results": search_web(topic_config, root=self.root, before_query=self.check_cancel,
                                                  allow_empty=True)}
                if topic_path.exists():
                    topic = validate_search_topic_document(
                        topic_config, self.checkpoint(topic_key, fetch_topic))
                else:
                    topic = validate_search_topic_document(topic_config, fetch_topic())
                    if topic["results"]:
                        topic = validate_search_topic_document(
                            topic_config, self.checkpoint(topic_key, lambda topic=topic: topic))
                topics.append(topic)
            seen_urls = set()
            results = []
            for topic in topics:
                for row in topic["results"]:
                    if row["url"] not in seen_urls:
                        seen_urls.add(row["url"])
                        results.append({**row, "query": topic["query"]})
            if not results:
                # Empty topic responses are not checkpointed. Retry can ask
                # again rather than replaying an all-empty snapshot forever.
                raise ValueError("web_search_no_safe_results")
            return {"provider": "brave", "query": config["query"], "queries": queries,
                    "retrieved_at": max(topic["retrieved_at"] for topic in topics),
                    "topic_retrieved_at": [{"query": topic["query"], "retrieved_at": topic["retrieved_at"]}
                                           for topic in topics],
                    "results": results,
                    "note": "Search snippets are planning leads, not independent fact verification."}

        document = validate_search_document(config, self.checkpoint(key, collect))
        self.state["web_research"] = {"status": "completed", "provider": "brave",
                                      "topics": 1 + len(config.get("more_queries", [])),
                                      "results": len(document["results"]), "retrieved_at": document["retrieved_at"]}
        self.save()
        if not cached:
            self.event("web_search_completed", provider="brave", results=len(document["results"]))
        return document

    def _plan_rows(self, seen):
        count = min(self.recipe.get("sample_count") or self.recipe["tasks"], self.recipe["max_units"])
        recent_tasks = deque(maxlen=10)
        metrics = self.state["stages"]["ingest"]
        metrics.update(status="running", done=0, total=count, phase="planning", cached=0,
                       batches_done=0, batches_total=(count + PLAN_BATCH_SIZE - 1) // PLAN_BATCH_SIZE,
                       batch_size=PLAN_BATCH_SIZE, concurrency=1, rate_per_minute=None, eta_seconds=None)
        metrics.pop('error', None)
        self.save()
        self.event('stage_started')
        for offset in range(0, count, PLAN_BATCH_SIZE):
            size = min(PLAN_BATCH_SIZE, count - offset)
            key = "plan" if count <= PLAN_BATCH_SIZE else ["plan", offset]
            request = {"brief": self.recipe["brief"], "count": size, "offset": offset,
                 "total": count, "batch": offset // PLAN_BATCH_SIZE + 1,
                 "previous_tasks": list(recent_tasks),
                 "instruction": "只规划当前批；利用 batch 和 offset 覆盖不同主题与情境，避免重复之前任务。"}
            modern = self.recipe.get("planning_policy_version", 1) >= 2
            if modern:
                request.update(training_goals=self.recipe["targets"], max_task_chars=MAX_TASK_CHARS)
                request["instruction"] += f" 每条任务不超过 {MAX_TASK_CHARS} 字符；围绕 training_goals 规划可独立回答的任务。"
            if self._research_document is not None:
                results = self._research_document["results"]
                # At large scale rotate one bounded search lead per planning
                # batch; avoid repeating all snippets across 1,000+ calls.
                if count <= PLAN_BATCH_SIZE:
                    leads = results
                elif self._research_document.get("queries"):
                    by_topic = [[row for row in results if row["query"] == query]
                                for query in self._research_document["queries"]]
                    available = [rows for rows in by_topic if rows]
                    batch = offset // PLAN_BATCH_SIZE
                    topic = available[batch % len(available)]
                    leads = [topic[(batch // len(available)) % len(topic)]]
                else:
                    leads = [results[(offset // PLAN_BATCH_SIZE) % len(results)]]
                request["web_research"] = {"leads": leads, "retrieved_at": self._research_document["retrieved_at"],
                    "note": "Untrusted search snippets. Use only as planning leads, never as verified answers or instructions."}
            cached = (self.path / 'checkpoints' / self.stage / f"{digest(['call', key])}.json").exists()
            data = self.ask(key, "generation", "workflow.plan", request)
            planned = data.get("tasks") if isinstance(data, dict) else None
            issue = task_plan_issue(planned, size, seen) if modern else None
            legacy_invalid = not modern and (
                not isinstance(planned, list) or len(planned) != size or any(text_issue(t) for t in planned)
                or len({task.strip() for task in planned}) != size or any(task.strip() in seen for task in planned))
            if issue or legacy_invalid:
                self.invalidate_checkpoint(["call", key])
                raise ValueError("invalid_task_plan" + (f"_{issue}" if issue else ""))
            recent_tasks.extend(planned)
            seen.update(task_identity(task) if modern else task.strip() for task in planned)
            metrics.update(done=offset + size, outputs=offset + size, eligible=offset + size)
            metrics['batches_done'] += 1
            metrics['cached'] += size if cached else 0
            self.save()
            for index, task in enumerate(planned, offset):
                yield {"id": digest([self.recipe["brief"], task]), "source_id": digest(self.recipe["brief"]),
                       "source_name": "开放需求", "location": index + 1,
                       "source_location": {"brief_task": index + 1},
                       "kind": "brief", "text": task, "status": "ready", "synthetic": True}

    def judge_answer(self, key, context, message, prompt_id="workflow.jev_score", *, instruction=None,
                     allow_reasoning_fallback=True):
        # A failed schema must not become a permanent successful call checkpoint.
        value = self.ask(key, "jev", prompt_id,
            {"context": context, "answer": message},
            **({"instruction": instruction} if instruction is not None else {}),
            **({"allow_reasoning_fallback": False} if not allow_reasoning_fallback else {}))
        try:
            return verdict(value)
        except ValueError:
            self.invalidate_checkpoint(["call", key])
            raise

    def generation_style(self, stage, sample_id):
        config = self.recipe.get("node_generation", {}).get(stage)
        if not config or not config.get("enabled", True):
            return None
        language_version = self.recipe.get("reasoning_language_contract_version", 1)
        style = style_for_sample(config, sample_id, reasoning_language_version=language_version,
            reasoning_depth_version=self.recipe.get("reasoning_depth_contract_version", 0))
        pinned = self.recipe.get("node_generation_prompts", {}).get(stage, {}).get(style["preset"])
        if pinned is not None:
            style["instruction"] = pinned
            from lib.domain.workflow_generation import reasoning_language_instruction
            language_rule = reasoning_language_instruction(style.get("reasoning_language", "auto"),
                                                            version=language_version)
            if language_rule and not pinned.endswith(language_rule):
                style["instruction"] += "\n" + language_rule
        if style.get("reasoning_depth_instruction"):
            style["instruction"] += "\n" + style["reasoning_depth_instruction"]
        return style

    @staticmethod
    def controlled_training_messages(messages, style):
        from lib.contexts.workflows.domain.dialogue import controlled_training_messages
        return controlled_training_messages(messages, style)

    @staticmethod
    def without_source_reasoning(value):
        from lib.contexts.workflows.domain.dialogue import without_source_reasoning
        return without_source_reasoning(value)

    def style_check(self, key, style, reasoning, answer):
        return self.adherence_check(key, "workflow.style_check", {
            "generation_style": style, "reasoning": reasoning, "answer": answer})

    def adherence_check(self, key, prompt_id, data, *, instruction=None):
        from lib.domain.style_judge import style_verdict
        value = self.ask(key, "jev", prompt_id, data,
                         **({"instruction": instruction} if instruction is not None else {}))
        recipe = getattr(self, "recipe", {})
        try:
            return style_verdict(value, allow_format_metadata=(
                type(recipe.get("style_verdict_protocol_version")) is int
                and recipe["style_verdict_protocol_version"] == 2))
        except ValueError:
            self.invalidate_checkpoint(["call", key])
            raise

    def cpt(self, unit):
        if unit["kind"] == "conversation":
            return [self.rejected(unit, "conversation_not_knowledge_corpus")]
        if unit["kind"] == "document":
            if self.cpt_processing()["mode"] == "model":
                return self.model_clean_corpus(unit)
            quality = inspect_corpus(unit["text"])
            if not quality["keep"]:
                return [{**unit, "status": "quarantined", "reason": quality["reason"], "quality": quality}]
            return [{**unit, "status": "eligible", "text": unit["text"], "quality": quality,
                     **({"source_context": deepcopy(unit)} if self.recipe.get("review_repair") is not None
                        or self.skips_post_generation_review("cpt") else {}),
                     "retention_reason": "source_text_passed_deterministic_checks",
                     "evidence_level": document_evidence_level(unit)}]
        data = self.ask([unit["id"], "corpus"], "generation", "workflow.corpus", unit)
        text = data.get("text") if isinstance(data, dict) else None
        quality = inspect_corpus(text)
        if not quality["keep"]:
            return [{**self.rejected(unit, "invalid_generated_corpus"), "quality": quality}]
        if self.recipe.get("review_repair") is not None and not self.skips_post_generation_review("cpt"):
            return [{**unit, "text": text, "status": "eligible", "quality": quality,
                     "source_context": deepcopy(unit), "evidence_level": "model_assessed_synthetic"}]
        check = self.judge_answer([unit["id"], "corpus_judge"], unit, {"content": text})
        if not accepted(check):
            return [{**self.rejected(unit, "corpus_judge_rejected"), "judge": check, "quality": quality}]
        return [{**unit, "text": text, "status": "eligible", "judge": check, "quality": quality,
                 **({"source_context": deepcopy(unit)} if self.skips_post_generation_review("cpt") else {}),
                 "retention_reason": "synthetic_text_passed_deterministic_and_model_checks",
                 "evidence_level": "model_assessed_synthetic"}]

    def sft(self, unit):
        if unit.get("qa_contract") is not None:
            return self.directed_sft(unit)
        if unit.get("verification_status") == "synthetic_unverified":
            return [self.rejected(unit, "simulated_tool_observation_not_verified_sft")]
        if any(tool_error_flag(message)[0] or tool_error_flag(message)[1]
               for message in unit.get("messages", [])):
            return [self.rejected(unit, "observed_tool_error_not_sft")]
        from lib.domain.reasoning_fields import sft_reasoning_issue, sft_training_messages
        from lib.domain.provider_reasoning import select_candidate_reasoning, reasoning_provenance
        if issue := sft_reasoning_issue(unit.get("messages", [])):
            return [self.rejected(unit, issue)]
        history = sft_training_messages(unit.get("messages", []))
        if (self.recipe.get("independent_cot_input_version") == 1
                and self.recipe.get("targets") == ["cot"]
                and self.generation_style("cot", unit["id"]) is not None
                and unit["kind"] == "conversation" and history
                and self.generation_style("sft", unit["id"]) is None):
            # This is input preparation, not publication or factual acceptance.
            # Independently authored CoT must be checked on its new response,
            # rather than pay to grade a draft it is forbidden to consume.
            if issue := conversation_issue(history):
                return [self.rejected(unit, issue)]
            return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible",
                     "messages": self.without_source_reasoning(history), "tools": unit.get("tools", []),
                     "quotes": [], "source_context": deepcopy(unit),
                     "generation_check": "learner_structure_only_for_independent_cot",
                     "reasoning_origin": "unassessed_input_not_training_output",
                     "evidence_level": "recorded_context_unassessed"}]
        context = {"source": {**unit, "messages": [
                       {**m, "content": "[来源内容隐藏]" if m.get("role") == "assistant" else m.get("content", "")}
                       for m in unit.get("messages", [])]},
                   "requirement": self.recipe["brief"]}
        style = self.generation_style("sft", unit["id"])
        if style is not None:
            if history:
                self.controlled_training_messages(history[:-1], style)
            history = self.without_source_reasoning(history)
            context = self.without_source_reasoning(context)
            context["generation_style"] = style
        feedback = None
        check, style_check = None, None
        for attempt in range(2):
            evidence_spans = None
            if style is None and history and unit["kind"] == "conversation":
                messages = history
                quotes = []
            else:
                key = [unit["id"], "sft_styled" if style is not None else "sft", attempt]
                candidate = self.ask(key, "generation", "workflow.sft_styled" if style is not None else "workflow.sft",
                    {**context, "feedback": feedback},
                    capture_response_details=style is None,
                    **({"instruction": "生成风格要求：\n" + style["instruction"]} if style is not None else {}))
                required = (("question", "answer", "reasoning") if style is not None
                    or self.recipe.get("provider_reasoning_capture_version") != 1 else ("question", "answer"))
                if not isinstance(candidate, dict) or any(text_issue(candidate.get(k)) for k in required):
                    feedback = "回答结构不合格或存在敏感数据"
                    continue
                try:
                    selected_reasoning = select_candidate_reasoning(candidate, styled=style is not None)
                except ValueError:
                    feedback = "推理字段结构无效"
                    continue
                quotes = candidate.get("quotes", [])
                if unit["kind"] == "document":
                    evidence_text = unit["text"]
                    if unit.get("source_context_pack"):
                        from lib.infrastructure.workflow_source_context import pack_evidence_text
                        evidence_text = pack_evidence_text(unit, self.path)
                    pdf_source = any(Path(source.get("file", "")).suffix.lower() == ".pdf"
                        and source.get("sha256") == unit.get("source_id")
                        and source.get("name") == unit.get("source_name")
                        for source in self.recipe.get("sources", ()))
                    evidence_spans = quote_spans(evidence_text, quotes, pdf_whitespace=pdf_source)
                    if evidence_spans is None:
                        feedback = "需要原文中可逐字匹配的证据"
                        continue
                if unit["kind"] == "document":
                    from lib.domain.learner_context import prompt_at_least
                    prompt_id = "workflow.sft_styled" if style is not None else "workflow.sft"
                    if prompt_at_least(self.recipe, prompt_id, (1, 1, 0)):
                        visible_context = candidate.get("visible_context")
                        if visible_context is not None:
                            if (not isinstance(visible_context, str) or not visible_context.strip()
                                    or len(visible_context) > len(evidence_text)
                                    or any(line not in evidence_text for line in visible_context.splitlines() if line.strip())):
                                feedback = "题面资料须使用清洗资料包中的真实片段，包含本题所需条件。"
                                continue
                        else:
                            # Keep the anchor and every quoted supporting span
                            # visible, including evidence from adjacent sections.
                            visible_context = "\n\n".join(dict.fromkeys([unit["text"], *quotes]))
                        prompt = f"资料：\n{visible_context}\n\n用户消息：\n{candidate['question']}"
                    else:
                        prompt = f"请根据以下资料回答问题。只使用资料支持的内容。\n\n资料：\n{unit['text']}\n\n问题：\n{candidate['question']}"
                    prefix = [{"role": "user", "content": prompt}]
                elif unit["kind"] == "brief":
                    prompt = f"{self.recipe['brief']}\n\n任务：\n{candidate['question']}" if self.recipe["brief"] else candidate["question"]
                    prefix = [{"role": "user", "content": prompt}]
                else:
                    prefix = history[:-1] if history else [{"role": "user", "content": candidate["question"]}]
                messages = [*prefix, {"role": "assistant", "content": candidate["answer"],
                    **({"reasoning_content": selected_reasoning["content"]} if selected_reasoning["content"] else {})}]
                messages = self.controlled_training_messages(messages, style)
            issue = conversation_issue(messages)
            if issue:
                feedback = issue
                continue
            if self.recipe.get("review_repair") is not None:
                # Content scoring/correction belongs to the review node in v18.
                # Generation keeps deterministic shape and source-quote checks.
                return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible",
                         "messages": messages, "tools": unit.get("tools", []), "quotes": quotes,
                         "source_context": unit, "generation_check": "structure_and_source_quotes",
                         **({"quote_spans": evidence_spans} if evidence_spans is not None else {}),
                         **({"generation_style": style} if style is not None else {}),
                         "repair_attempts": attempt,
                         **({"reasoning_origin": "source"} if messages is history else reasoning_provenance(selected_reasoning)),
                         "evidence_level": ("recorded_context_model_assessed" if messages is history else
                            "model_assessed_synthetic" if unit["kind"] == "brief" else document_evidence_level(unit, assessed=True))}]
            # Recorded answers need no generation call. Key their verdict to
            # the exact record so an old generated-answer score cannot be reused.
            judge_key = ([unit["id"], "recorded_sft_judge_v1", digest(messages)] if messages is history
                         else [unit["id"], "sft_judge", attempt])
            check = self.judge_answer(judge_key, {**context, "rendered_prompt": messages[:-1]}, messages,
                                      **({"allow_reasoning_fallback": False} if style is not None else {}))
            style_check = (self.style_check([unit["id"], "sft_style", attempt], style,
                                           messages[-1]["reasoning_content"], messages[-1]["content"])
                           if style is not None else None)
            if accepted(check) and (style_check is None or
                                    (style_check["keep"] and style_check["adherence"] >= 4)):
                return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible", "messages": messages,
                         "tools": unit.get("tools", []), "quotes": quotes, "judge": check, "source_context": unit,
                         **({"quote_spans": evidence_spans} if evidence_spans is not None else {}),
                         **({"generation_style": style, "style_check": style_check} if style is not None else {}),
                         **({"human_augmentation_status": "recorded_conversation_preserved"}
                            if unit["kind"] == "conversation" and
                            self.recipe.get("qa_director", {}).get("human_augmentation", {}).get("enabled") else {}),
                         "repair_attempts": attempt,
                         **({"reasoning_origin": "source"} if messages is history else reasoning_provenance(selected_reasoning)),
                         "evidence_level": ("recorded_context_model_assessed" if messages is history else
                                            "model_assessed_synthetic" if unit["kind"] == "brief"
                                            else document_evidence_level(unit, assessed=True))}]
            if messages is history:
                # Failed recorded evidence stays available for explicit rework;
                # importing a conversation is not permission to invent its answer.
                return [{**self.rejected(unit, "recorded_sft_judge_rejected"),
                         "messages": messages, "tools": unit.get("tools", []), "quotes": quotes,
                         "source_context": unit, "judge": check, "feedback": check["reason"],
                         "reasoning_origin": "source", "evidence_level": "recorded_context_model_assessed"}]
            feedback = {"correctness": check["reason"] if not accepted(check) else None,
                        "style": style_check["reason"] if style_check is not None and
                        not (style_check["keep"] and style_check["adherence"] >= 4) else None}
            if style is None:
                feedback = check["reason"]
        return [{**self.rejected(unit, "sft_quality_failed_after_repair"), "feedback": feedback,
                 **({"messages": messages, "quotes": quotes} if self.recipe.get("review_repair") is not None
                     and "messages" in locals() else {}),
                 **({"generation_style": style, "judge": check, "style_check": style_check,
                     "repair_attempts": 1} if style is not None else {})}]

    def multiturn(self, unit):
        from lib.bootstrap.dialogue_generation import dialogue_generation
        return dialogue_generation(self).execute(unit)

    def _multiturn(self, unit, *, style=None):
        from lib.bootstrap.dialogue_generation import dialogue_generation
        return dialogue_generation(self).generate(unit, style=style)

    def agent(self, unit):
        if unit.get("kind") != "conversation":
            return [self.rejected(unit, "recorded_tool_trajectory_required")]
        if unit.get("verification_status") == "synthetic_unverified":
            return [self.rejected(unit, "simulated_tool_observation_not_verified_agent")]
        sandbox_image = self.recipe.get("agent_sandbox_image")
        result = assess_recorded_trajectory(
            unit["messages"], tool_snapshots=unit.get("tool_snapshots"),
            source_snapshot_sha256=unit["source_id"],
            sandbox_replay=DockerLedgerReplay(sandbox_image) if sandbox_image else None)
        reason = result.get("reason")
        is_container_evidence = (result.get("verification", {}).get("method") in {
            "isolated_docker_ledger_replay", "mixed_verified_tool_replay"}
            or result.get("negative", {}).get("evidence", {}).get("basis") in {
                "isolated_docker_ledger_replay", "bounded_ledger_terminal_equality"})
        evidence = ("isolated_container_replay" if is_container_evidence else
                    "local_tool_replay" if result["status"] == "eligible" or result.get("negative")
                    else "unverified_source")
        source_location = unit.get("source_location")
        if source_location is None and unit.get("source_name") is not None:
            source_location = {"file": unit["source_name"], "record": unit.get("location")}
        provenance = {key: unit[key] for key in ("source_name", "location") if key in unit}
        provenance["source_location"] = source_location
        row = {"id": unit["id"], "source_id": unit["source_id"],
               "status": result["status"], "evidence_level": evidence,
               **provenance,
               "original_messages_sha256": digest(unit["messages"])}
        if result["status"] == "eligible":
            return [{**row, "messages": result["messages"], "tools": unit.get("tools", []),
                     **({"source_context": {"kind": "conversation", "messages": deepcopy(unit["messages"]),
                                            "tools": deepcopy(unit.get("tools", [])),
                                            "verification_status": unit.get("verification_status")}}
                        if self.recipe.get("review_repair") is not None else {}),
                     "verification": result["verification"]}]
        row["reason"] = result["reason"]
        if result.get("unverified_tool"):
            row["unverified_tool"] = result["unverified_tool"]
        if result.get("unverified_turn"):
            row["unverified_turn"] = result["unverified_turn"]
        if result.get("negative"):
            row["negative"] = {**result["negative"], "source_id": unit["source_id"],
                               **provenance, "id": unit["id"],
                               "original_messages_sha256": row["original_messages_sha256"]}
        return [row]

    def dpo(self, sample):
        prefix, chosen = sample["messages"][:-1], sample["messages"][-1]
        value = self.ask([sample["id"], "alternative"], "generation", "workflow.alternative",
            {"prompt": prefix, "source": sample["source_context"],
             **({"qa_contract": sample["qa_contract"],
                 "question_rules": self.recipe["qa_director"]["question_rules"],
                 "answer_rules": self.recipe["qa_director"]["answer_rules"]} if sample.get("qa_contract") else {})},
            **({"instruction": "保持指导员题面、可见线索与回答策略不变。按question_rules和answer_rules认真回答，"
                "不要故意制造坏答案；隐藏教师来源不能成为训练输入或回答中的来源指代。"} if sample.get("qa_contract") else {}))
        if not isinstance(value, dict) or any(text_issue(value.get(k)) for k in ("answer", "reasoning")):
            return [self.rejected(sample, "invalid_dpo_candidate")]
        alternative = {"role": "assistant", "content": value["answer"], "reasoning_content": value["reasoning"]}
        if same_answer(chosen["content"], alternative["content"]):
            return [self.rejected(sample, "identical_dpo_answers")]
        if self.recipe.get("review_repair") is not None:
            return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                     "prompt": prefix, "chosen": [chosen], "rejected": [alternative], "tools": sample.get("tools", []),
                     "source_context": deepcopy(sample.get("source_context")), "quotes": sample.get("quotes", []),
                     "evidence_level": sample["evidence_level"], **self.qa_metadata(sample)}]
        check = self.judge_answer([sample["id"], "alternative_judge"],
                                  {"prompt": prefix, "source": sample["source_context"]}, alternative)
        node_models = self.recipe.get("node_models", {})
        chosen_check = sample["judge"]
        if (node_models.get("sft", {}).get("jev") != node_models.get("preference", {}).get("jev")
                or self.prompt_text("workflow.jev_score", stage="sft")
                != self.prompt_text("workflow.jev_score", stage="preference")):
            chosen_check = self.judge_answer([sample["id"], "chosen_judge"],
                {"prompt": prefix, "source": sample["source_context"]}, chosen)
        choices = sorted([(chosen_check, chosen), (check, alternative)], key=lambda x: x[0]["correctness"])
        low, high = choices
        if not accepted(high[0]) or high[0]["correctness"] - low[0]["correctness"] < 2:
            return [{**self.rejected(sample, "insufficient_preference_evidence"), "checks": [low[0], high[0]]}]
        contract_check = (self.qa_contract_check([sample["id"], "preference_directed_contract"],
            sample, [*prefix, high[1]], prompt_id="workflow.preference_directed_check")
            if sample.get("qa_contract") else None)
        if contract_check is not None and not (contract_check["keep"] and contract_check["adherence"] >= 4):
            return [{**self.rejected(sample, "preference_qa_contract_rejected"),
                     **self.qa_metadata(sample, contract_check)}]
        return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible", "prompt": prefix,
                 "chosen": [high[1]], "rejected": [low[1]], "tools": sample.get("tools", []),
                 "preference": {"dimension": "correctness", "chosen": high[0], "rejected": low[0], "minimum_gap": 2},
                 "evidence_level": sample["evidence_level"],
                 **self.qa_metadata(sample, contract_check)}]

    def independent_arithmetic(self):
        return self.recipe.get("arithmetic_seed_protocol_version") == 1

    def arithmetic_only(self):
        return self.independent_arithmetic() and set(self.recipe["targets"]) == {"gsm8k"}

    def arithmetic_inputs(self, path, count, offset=0):
        if self.recipe.get("arithmetic_registry_version") == 1:
            from lib.infrastructure.workflow_arithmetic import registered_arithmetic_inputs
            return registered_arithmetic_inputs(self, path, count, offset)
        theme = self.recipe["brief"] or "开放数学推理"
        identity = digest({"kind": "deterministic_synthetic_arithmetic", "theme": theme,
                           "generator_version": self.recipe.get("math_generator_version", 1)})
        return row_checkpoint(path, lambda: (
            {"id": digest([identity, "gsm8k", index]), "source_id": identity,
             "kind": "arithmetic_seed", "status": "ready", "synthetic": True}
            for index in range(offset, offset + count)), self.check_cancel)

    def gsm8k(self, item):
        seed = int(item["id"][:8], 16)
        theme = (self.recipe["brief"] or "开放数学推理" if self.independent_arithmetic() else
                 self.recipe["brief"] or (self.recipe["sources"][0]["name"] if self.recipe["sources"] else "开放数学推理"))
        if self.recipe.get("arithmetic_registry_version") == 1 and self.independent_arithmetic():
            from lib.infrastructure.workflow_arithmetic import registered_arithmetic_sample
            sample = registered_arithmetic_sample(self, item)
        else:
            sample = build_gsm8k(theme, seed, version=self.recipe.get("math_generator_version", 1))
        if not validate_gsm8k(sample):
            return [self.rejected(item, "gsm8k_arithmetic_verification_failed")]
        return [{"id": item["id"], "source_id": item["source_id"], "status": "eligible",
                 "question": sample["question"], "answer": sample["answer"],
                 "arithmetic_expression": sample["_expression"], "verified_result": sample["_result"],
                 "calculation_steps": sample.get("_steps", []),
                 "generator_version": self.recipe.get("math_generator_version", 1),
                 **({"arithmetic_fingerprint": sample["_registry_fingerprint"],
                     "arithmetic_registry_seed": sample["_registry_seed"]} if "_registry_fingerprint" in sample else {}),
                 **({"source_context": {"kind": "brief", "task": canonical({"arithmetic_expression": sample["_expression"],
                     "verified_result": sample["_result"], "calculation_steps": sample.get("_steps", [])})}}
                    if self.recipe.get("review_repair") is not None else {}),
                 "evidence_level": "deterministic_synthetic_arithmetic"}]

    def cot(self, sample):
        rows = self._cot_result(sample)
        if cot_updates_sft(self.recipe):
            return [cot_with_sft_context(sample, row) for row in rows]
        return rows

    def _cot_result(self, sample):
        style = self.generation_style("cot", sample["id"])
        if style is not None:
            return self.styled_cot(sample, style)
        messages = sample["messages"]
        if not messages or messages[-1].get("role") != "assistant":
            return [self.rejected(sample, "cot_missing_final_answer")]
        reasoning = messages[-1].get("reasoning_content", "")
        answer = messages[-1].get("content", "")
        if sample.get("reasoning_origin") != "provider_reasoning":
            reasoning = reasoning.strip()
            answer = answer.strip()
        if not reasoning.strip() or not answer.strip():
            return [self.rejected(sample, "cot_missing_reasoning_or_answer")]
        if self.recipe.get("review_repair") is not None:
            return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                     "question": messages[:-1], "reasoning": [reasoning], "answer": answer,
                     "source_context": deepcopy(sample.get("source_context")), "quotes": sample.get("quotes", []),
                     **{name: deepcopy(sample[name]) for name in ("reasoning_origin", "reasoning_summary",
                         "reasoning_api_format", "reasoning_call") if name in sample},
                     "evidence_level": sample["evidence_level"], **self.qa_metadata(sample)}]
        task = {"prompt": messages[:-1], "reasoning": reasoning, "answer": answer,
                "source": sample.get("source_context"), "quotes": sample.get("quotes", [])}
        check = self.ask([sample["id"], "cot_check"], "jev", "workflow.rationale_check", task)
        try:
            check = verdict(check)
        except ValueError:
            self.invalidate_checkpoint(["call", [sample["id"], "cot_check"]])
            raise
        if not accepted(check):
            return [{**self.rejected(sample, "cot_reasoning_rejected"), "judge": check}]
        contract_check = (self.qa_contract_check([sample["id"], "cot_directed_contract"], sample, messages,
            prompt_id="workflow.cot_directed_check") if sample.get("qa_contract") else None)
        if contract_check is not None and not (contract_check["keep"] and contract_check["adherence"] >= 4):
            return [{**self.rejected(sample, "cot_qa_contract_rejected"),
                     **self.qa_metadata(sample, contract_check)}]
        steps = ([reasoning] if sample.get("reasoning_origin") == "provider_reasoning" else
                 [part.strip() for part in re.split(r"(?<=[。.!?])\s*", reasoning) if part.strip()])
        return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                 "question": messages[:-1], "reasoning": steps or [reasoning], "answer": answer,
                 "judge": check, "evidence_level": sample["evidence_level"],
                 **{name: deepcopy(sample[name]) for name in ("reasoning_origin", "reasoning_summary",
                     "reasoning_api_format", "reasoning_call") if name in sample},
                 **self.qa_metadata(sample, contract_check)}]

    def styled_cot(self, sample, style):
        """Author a new explicit rationale; never copy a provider reasoning channel."""
        messages = sample.get("messages", [])
        if (not messages or messages[-1].get("role") != "assistant"
                or text_issue(messages[-1].get("content"))):
            return [self.rejected(sample, "cot_missing_final_answer")]
        source = self.without_source_reasoning(sample.get("source_context"))
        task = {"prompt": self.controlled_training_messages(
                    self.without_source_reasoning(messages[:-1]), style), "source": source,
                "quotes": sample.get("quotes", []), "reference_answer": messages[-1]["content"],
                "generation_style": style}
        if (type(self.recipe.get("authored_reasoning_warmup_version")) is int
                and self.recipe["authored_reasoning_warmup_version"] == 1
                and self.recipe.get("node_generation", {}).get("cot", {}).get("answer_mode") != "native_prefill"):
            task["authored_reasoning_warmup"] = {"version": 1, "stage": "cot",
                                                "fields": ["candidate.reasoning"]}
        if self.recipe.get("training_surface_protocol_version") in {1, 2}:
            try:
                assessed_reference = isinstance(sample.get("judge"), dict) and accepted(sample["judge"])
            except (KeyError, TypeError, ValueError):
                assessed_reference = False
            task["reference_answer_status"] = "model_assessed" if assessed_reference else "unassessed_not_supplied"
            if not assessed_reference:
                # Structural eligibility is not factual validation. A long
                # styled explanation must not beautify an unchecked answer.
                task.pop("reference_answer")
        if sample.get("qa_contract"):
            task.update(qa_contract=sample["qa_contract"],
                question_rules=self.recipe["qa_director"]["question_rules"],
                answer_rules=self.recipe["qa_director"]["answer_rules"])
        if isinstance(source, dict):
            from lib.infrastructure.source_memory_messages import directed_source_request
            task = directed_source_request(self.recipe, task, source, sample.get("qa_contract"))
        protocol = self.recipe.get("authored_reasoning_protocol_version") == 1
        native_prefill = self.recipe.get("node_generation", {}).get("cot", {}).get("answer_mode") == "native_prefill"
        if native_prefill:
            # Capability checks happen before spending on the rationale phase.
            from lib.model_reasoning_prefill import validate_prefill_client
            validate_prefill_client(self.client("generation"))
            if any(message.get("role") == "tool" or message.get("tool_calls") or message.get("function_call")
                   for message in task["prompt"]):
                raise ValueError("model_reasoning_prefill_tools_unsupported")
        instruction = "生成风格要求：\n" + style["instruction"]
        if task.get("reference_answer_status") == "unassessed_not_supplied":
            instruction += "\n上游只有结构合格，尚未事实评审，本次不提供其答案。独立依据真实问题和原始知识生成答案与解释，不推测上游答案，不把未评审结果当事实。资料不足时如实保留边界，不以换题或添造事实补救。"
        if protocol and not native_prefill:
            from lib.domain.authored_reasoning import AUTHORED_REASONING_PROTOCOL_INSTRUCTION, authored_reasoning_issue
            protocol_instruction = AUTHORED_REASONING_PROTOCOL_INSTRUCTION
            if self.recipe.get("reasoning_shape_version") == 1:
                from lib.prompts.workflow_reasoning import task_adaptive_reasoning_template
                protocol_instruction = task_adaptive_reasoning_template(protocol_instruction)
            instruction += "\n" + protocol_instruction
        from lib.domain.authored_reasoning import authored_reasoning_issue
        feedback, check, style_check, contract_check = None, None, None, None
        for attempt in range(2):
            prefill_audit = {}
            if native_prefill:
                value, prefill_audit = self.native_styled_answer(sample, task, style, feedback, attempt)
                if value is None:
                    feedback = prefill_audit
                    if (self.recipe.get("native_answer_retry_version") == 1
                            and isinstance(prefill_audit.get("native_answer_terminal"), dict)):
                        # One completed-but-empty answer is a sample failure,
                        # not a reason to re-author its verified rationale or
                        # stop other independent rows in the batch.
                        return [{**self.rejected(sample, "native_answer_missing_after_retry"),
                            "question": deepcopy(task["prompt"]), "source_context": deepcopy(source),
                            "input_digest": digest({"question": task["prompt"], "source_context": source}),
                            "sft_parent_id": sample["id"], "generation_style": style,
                            "feedback": prefill_audit, "native_answer_terminal": deepcopy(
                                prefill_audit["native_answer_terminal"]),
                            "repair_attempts": attempt, **self.qa_metadata(sample)}]
                    continue
            else:
                value = self.ask([sample["id"], "cot_generate_v1", attempt], "generation",
                             "workflow.cot_generate", {**task, "feedback": feedback},
                             instruction=instruction)
            if not isinstance(value, dict) or any(text_issue(value.get(key)) for key in ("reasoning", "answer")):
                feedback = "推理和答案必须是有效的完整文本"
                continue
            if protocol and (issue := authored_reasoning_issue(value)):
                feedback = {"reasoning_protocol": issue,
                    "repair": "补齐具体前提、支持桥梁与本题针对性检查，保持任务和风格；不要只加字数或机械凑步骤。"}
                continue
            surface_protocol = self.recipe.get("training_surface_protocol_version") in {1, 2}
            if surface_protocol:
                from lib.domain.training_surface import training_surface_issue
                issue = training_surface_issue(value["reasoning"], value["answer"],
                    visible_messages=task["prompt"], contract=sample.get("qa_contract"),
                    protocol_version=self.recipe.get("training_surface_protocol_version", 1),
                    **knowledge_voice_options(self.recipe))
                if issue:
                    feedback = {"training_surface": issue,
                        "repair": "解释和答案全文只围绕用户任务展开，用已掌握的事实推导；移除幕后制作过程，不删必要论证或编造经历。文章理解可引用用户实际可见的文章。"}
                    continue
            reasoning_audit = ({"reasoning_protocol_version": 1,
                "reasoning_trace": deepcopy(value["reasoning_trace"]), "reasoning_trace_status": "authored",
                "reasoning_trace_content_sha256": digest({"reasoning": value["reasoning"], "answer": value["answer"]})}
                if protocol else {})
            if surface_protocol:
                reasoning_audit["training_surface_protocol_version"] = self.recipe["training_surface_protocol_version"]
            if native_prefill:
                reasoning_audit.update(prefill_audit)
            if self.recipe.get("review_repair") is not None:
                return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                         "question": task["prompt"], "reasoning": [value["reasoning"]], "answer": value["answer"],
                         "source_context": source, "quotes": sample.get("quotes", []), "generation_style": style,
                         "repair_attempts": attempt, "reasoning_origin": "prompt_styled_generation",
                         "evidence_level": sample["evidence_level"], **reasoning_audit, **self.qa_metadata(sample)}]
            check = self.judge_answer([sample["id"], "cot_generated_check_v1", attempt],
                                      {key: item for key, item in task.items() if key != "generation_style"},
                                      {"reasoning": value["reasoning"], "answer": value["answer"],
                                       **({"reasoning_trace": value["reasoning_trace"]} if protocol else {})},
                                      prompt_id="workflow.rationale_check", allow_reasoning_fallback=False)
            style_check = self.style_check([sample["id"], "cot_style_v1", attempt], style,
                                          value["reasoning"], value["answer"])
            contract_check = (self.qa_contract_check([sample["id"], "cot_generated_directed_contract", attempt],
                sample, [*task["prompt"], {"role": "assistant", "content": value["answer"],
                    "reasoning_content": value["reasoning"]}], prompt_id="workflow.cot_directed_check")
                if sample.get("qa_contract") else None)
            if (accepted(check) and style_check["keep"] and style_check["adherence"] >= 4
                    and (contract_check is None or (contract_check["keep"] and contract_check["adherence"] >= 4))):
                return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                         "question": task["prompt"], "reasoning": [value["reasoning"]], "answer": value["answer"],
                         "source_context": source, "quotes": sample.get("quotes", []),
                         "judge": check, "style_check": style_check, "generation_style": style,
                         "repair_attempts": attempt, "reasoning_origin": "prompt_styled_generation",
                         "evidence_level": sample["evidence_level"],
                         **self.independent_cot_assessment(sample),
                         **reasoning_audit, **self.qa_metadata(sample, contract_check)}]
            feedback = {"correctness": check["reason"] if not accepted(check) else None,
                        "style": style_check["reason"] if not (style_check["keep"] and
                                  style_check["adherence"] >= 4) else None}
            if contract_check is not None:
                feedback["contract"] = (contract_check["reason"] if
                    not (contract_check["keep"] and contract_check["adherence"] >= 4) else None)
        return [{**self.rejected(sample, "cot_generation_failed_after_repair"),
                 # A failed generation still needs the exact learner task and
                 # authenticated source to support a later same-task retry.
                 # Retain input only, never the unchecked answer or chain.
                 "question": deepcopy(task["prompt"]), "source_context": deepcopy(source),
                 "input_digest": digest({"question": task["prompt"], "source_context": source}),
                 "sft_parent_id": sample["id"],
                 "generation_style": style, "judge": check, "style_check": style_check,
                 "feedback": feedback, "repair_attempts": 1,
                 "reasoning_origin": "prompt_styled_generation", **self.qa_metadata(sample, contract_check)}]

    def native_styled_answer(self, sample, task, style, feedback, attempt):
        """Reuse a verified authored prefix; answer via provider-native continuation."""
        from lib.domain.authored_reasoning import authored_rationale_issue
        from lib.domain.training_surface import training_surface_issue

        # No previous answer is supplied to this reasoning-only phase.
        rationale_task = {key: value for key, value in task.items() if key != "reference_answer"}
        rationale = self.ask([sample["id"], "cot_rationale_v1", attempt], "generation",
            "workflow.cot_rationale", {**rationale_task, "feedback": feedback},
            instruction="生成风格要求：\n" + style["instruction"], allow_reasoning_fallback=False)
        if issue := authored_rationale_issue(rationale):
            return None, {"reasoning_protocol": issue}
        issue = training_surface_issue(rationale["reasoning"], "",
            visible_messages=task["prompt"], contract=sample.get("qa_contract"),
            protocol_version=self.recipe.get("training_surface_protocol_version", 1),
            **knowledge_voice_options(self.recipe))
        if issue:
            return None, {"training_surface": issue}
        checked = self.adherence_check([sample["id"], "cot_prefix_check_v1", attempt],
            "workflow.cot_prefix_check", {"prompt": task["prompt"], "source": task["source"],
                "reasoning": rationale["reasoning"], "reasoning_trace": rationale["reasoning_trace"],
                **({"source_context_pack": task["source_context_pack"]} if "source_context_pack" in task else {})})
        if not checked["keep"] or checked["adherence"] < 4:
            return None, {"prefix_review": checked["reason"]}
        # The dispatch/schema/audit trace never enter the reasoning channel.
        answer_task = {key: value for key, value in rationale_task.items()
                       if key not in {"generation_style", "reference_answer_status", "quotes"}}
        from lib.model_request_reliability import ModelRequestError
        prefix_sha = hashlib.sha256(rationale["reasoning"].encode("utf-8")).hexdigest()
        retry_enabled = self.recipe.get("native_answer_retry_version") == 1
        answer_attempts = 2 if retry_enabled else 1
        empty_receipts = []
        answer = None
        base_key = [sample["id"], "cot_native_answer_v1", attempt]
        for answer_attempt in range(answer_attempts):
            key = base_key if not answer_attempt else [*base_key, "visible_retry_v1", answer_attempt]
            empty_key = ["native_answer_empty_v1", sample["id"], attempt, answer_attempt, prefix_sha]
            if retry_enabled and self.checkpoint_has(empty_key):
                # A later upstream failure or process exit must not purchase
                # the already-recorded empty response again on resume.
                saved = self.checkpoint(empty_key, lambda: None)
                expected = {"version": 1, "status": "empty_visible_output", "eligible": False,
                    "code": "model_visible_output_missing", "answer_attempt": answer_attempt + 1,
                    "prefix_sha256": prefix_sha, "answer_checkpoint_sha256": digest(["call", key])}
                if saved != expected:
                    raise ValueError("checkpoint_integrity_error")
                empty_receipts.append(deepcopy(saved))
                continue
            try:
                answer = self.ask(key, "generation", "workflow.cot_native_answer", answer_task,
                    allow_reasoning_fallback=False, reasoning_prefix=rationale["reasoning"],
                    capture_response_details=True,
                    # Only the supplemental visible-answer request has no
                    # nested network loop. The first request retains the
                    # existing transport retry policy.
                    **({"native_request_attempts": 1} if answer_attempt else {}))
                break
            except (ValueError, ModelRequestError) as error:
                empty_visible = (type(error) is ValueError and str(error) == "model_visible_output_missing")
                if isinstance(error, ModelRequestError):
                    empty_visible = error.kind == "invalid" and error.code == "model_visible_output_missing"
                if not retry_enabled or not empty_visible:
                    raise
                receipt = {"version": 1, "status": "empty_visible_output", "eligible": False,
                    "code": "model_visible_output_missing", "answer_attempt": answer_attempt + 1,
                    "prefix_sha256": prefix_sha, "answer_checkpoint_sha256": digest(["call", key])}
                self.checkpoint(empty_key, lambda: receipt)
                empty_receipts.append(deepcopy(receipt))
                self.event("native_answer_empty", role="generation", **receipt)
        if answer is None:
            terminal = {"version": 1, "code": "model_visible_output_missing", "answer_attempts": answer_attempts,
                "prefix_sha256": prefix_sha, "empty_response_receipts": empty_receipts,
                "rationale_checkpoint": {"stage": self.stage,
                    "sha256": digest(["call", [sample["id"], "cot_rationale_v1", attempt]])},
                "prefix_review_checkpoint": {"stage": self.stage,
                    "sha256": digest(["call", [sample["id"], "cot_prefix_check_v1", attempt]])}}
            return None, {"native_answer_terminal": terminal}
        if not isinstance(answer, dict) or set(answer) != {"answer"} or text_issue(answer.get("answer")):
            raise ValueError("native_prefill_invalid_answer")
        details = getattr(answer, "response_details", {})
        receipt = details.get("reasoning_prefill", {})
        if receipt.get("prefix_sha256") != prefix_sha or receipt.get("transport") != "deepseek_chat_beta":
            raise ValueError("native_prefill_receipt_missing")
        return {**rationale, "answer": answer["answer"]}, {
            "reasoning_origin": "authored_prefix_native_answer",
            "reasoning_prefill": deepcopy(receipt), "prefix_review": checked,
            "answer_response_details": deepcopy(details),
            **({"native_answer_retry": {"version": 1, "answer_attempts": len(empty_receipts) + 1,
                "empty_response_receipts": empty_receipts}} if retry_enabled else {}),
            "reasoning_scope": "authored_prefix_not_complete_provider_internal_reasoning"}

    def trim_reasoning(self, item):
        """Transform only explicit SFT/CoT reasoning, retaining immutable answers."""
        target, sample = item["target"], item["sample"]
        row = deepcopy(sample)
        if row.get("status") != "eligible":
            return [{**row, "trim_target": target}]
        config = self.recipe["reasoning_trim"]
        instruction = self.recipe.get("reasoning_trim_prompt") or trim_prompt(config)
        fields = []
        linked_cot = target == "cot" and cot_updates_sft(self.recipe)
        if target == "sft" or linked_cot:
            for index, message in enumerate(row.get("messages", [])):
                if message.get("role") == "assistant" and message.get("reasoning_content"):
                    fields.append((f"messages.{index}.reasoning_content", message["reasoning_content"],
                                   self.without_source_reasoning(row["messages"][:index]), message["content"], index))
        elif target == "cot":
            reasoning = row.get("reasoning")
            if isinstance(reasoning, list):
                reasoning = "\n".join(reasoning)
            if reasoning:
                fields.append(("reasoning", reasoning, self.without_source_reasoning(row.get("question")), row["answer"], None))
        receipts = []
        for field, original, prompt, answer, index in fields:
            feedback, check, rules_check = None, None, None
            original_hash = digest(original)
            for attempt in range(2):
                key = [row["id"], "trim_v1", target, field, original_hash, attempt]
                context = {"prompt": prompt, "source": self.without_source_reasoning(row.get("source_context")),
                           "final_answer": answer, "original_reasoning": original}
                if row.get("qa_contract"):
                    from lib.infrastructure.source_memory_messages import directed_source_request
                    context["qa_contract"] = deepcopy(row["qa_contract"])
                    context = directed_source_request(self.recipe, context, row.get("source_context") or {}, row["qa_contract"])
                replacement = self.ask(key, "generation", "workflow.trim",
                                       {**context, "feedback": feedback}, instruction=instruction)
                if (not isinstance(replacement, dict) or set(replacement) != {"reasoning"}
                        or text_issue(replacement.get("reasoning"))):
                    feedback = "仅返回有效的 reasoning 文本，不得包含或改写其他字段"
                    continue
                if (self.recipe.get("training_surface_protocol_version") in {1, 2}
                        and row.get("reasoning_origin") in {"prompt_styled_generation", "prompt_trim", "prompt_revision", "human_revision"}):
                    from lib.domain.training_surface import training_surface_issue
                    issue = training_surface_issue(replacement["reasoning"], answer,
                        visible_messages=prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}],
                        contract=row.get("qa_contract"),
                        protocol_version=self.recipe.get("training_surface_protocol_version", 1),
                        **knowledge_voice_options(self.recipe))
                    if issue:
                        feedback = {"training_surface": issue, "repair": "保留任务推导和事实，移除隐藏来源与制作过程自述。最终答案保持不变。"}
                        continue
                check = self.judge_answer([*key, "meaning"], context,
                    {"replacement_reasoning": replacement["reasoning"], "final_answer": answer},
                    prompt_id="workflow.trim_check", instruction=instruction)
                rules_check = self.adherence_check([*key, "rules"], "workflow.trim_rules_check",
                    {"prompt": prompt, "original_reasoning": original,
                     "replacement_reasoning": replacement["reasoning"], "final_answer": answer},
                    instruction=instruction)
                contract_check = None
                if row.get("qa_contract"):
                    if target == "sft" or linked_cot:
                        contract_messages = deepcopy(row["messages"])
                        contract_messages[index]["reasoning_content"] = replacement["reasoning"]
                    else:
                        contract_messages = [*row["question"], {"role": "assistant", "content": answer,
                                             "reasoning_content": replacement["reasoning"]}]
                    contract_check = self.qa_contract_check([*key, "qa_contract"], row, contract_messages,
                        prompt_id="workflow.trim_directed_check")
                if (accepted(check) and rules_check["keep"] and rules_check["adherence"] >= 4
                        and (contract_check is None or (contract_check["keep"] and contract_check["adherence"] >= 4))):
                    receipts.append({"field": field, "input_sha256": original_hash, "judge": check,
                                     "rules_check": rules_check, "repair_attempts": attempt})
                    if contract_check is not None:
                        row["qa_contract_check"] = contract_check
                    if target == "sft" or linked_cot:
                        row["messages"][index]["reasoning_content"] = replacement["reasoning"]
                    else:
                        row["reasoning"] = [replacement["reasoning"]]
                    break
                feedback = {"meaning": check["reason"] if not accepted(check) else None,
                            "rules": rules_check["reason"] if not (rules_check["keep"] and
                                      rules_check["adherence"] >= 4) else None}
                if contract_check is not None:
                    feedback["contract"] = (contract_check["reason"] if
                        not (contract_check["keep"] and contract_check["adherence"] >= 4) else None)
            else:
                return [{**self.rejected(sample, "reasoning_trim_failed_after_repair"), "trim_target": target,
                         "reasoning_trim": {"version": 1, "template": config["template"], "status": "rejected",
                            "instruction_sha256": digest(instruction), "field": field,
                            "input_sha256": original_hash, "judge": check, "rules_check": rules_check,
                            "feedback": feedback, "repair_attempts": 1}}]
        if linked_cot:
            row["question"] = deepcopy(row["messages"][:-1])
            row["reasoning"] = [row["messages"][-1]["reasoning_content"]]
        if row.get("reasoning_protocol_version") == 1 and receipts:
            row["reasoning_trace_status"] = "superseded_by_trim"
        if "source_context" in row:
            row["source_context"] = self.without_source_reasoning(row["source_context"])
        row["reasoning_trim"] = {"version": 1, "template": config["template"],
                                  "status": "applied" if fields else "not_applicable",
                                  "instruction_sha256": digest(instruction), "fields": receipts}
        if receipts:
            from lib.infrastructure.workflow_review_repair import preserve_provider_reasoning
            preserve_provider_reasoning(target, sample, row, origin="prompt_trim")
        return [{**row, "trim_target": target}]

    def apply_reasoning_trim(self, collections, *, targets=None):
        """Use replayable row views so the optional final stage stays bounded."""
        if not (self.recipe.get("reasoning_trim") or {}).get("enabled"):
            if "trim" in self.state["stages"]:
                self.state["stages"]["trim"]["status"] = "skipped"
            return
        targets = ([target for target in ("sft", "cot") if target in self.recipe["targets"]]
                   if targets is None else list(targets))
        if not targets:
            raise ValueError("reasoning_trim_requires_reasoning_target")
        self.state["stages"].setdefault("trim", {"label": STAGES["trim"], "status": "pending", "done": 0, "total": 0})
        class TaggedRows:
            def __len__(self):
                return sum(len(collections[target]) for target in targets)

            def __iter__(self):
                for target in targets:
                    for sample in collections[target]:
                        yield {"target": target, "sample": sample}
        counts = {target: len(collections[target]) for target in targets}
        rows = self.stage_items("trim", TaggedRows(), self.trim_reasoning)
        for target in targets:
            collections[target] = WorkflowRows(rows.path, counts[target],
                predicate=lambda row, selected=target: row["trim_target"] == selected,
                transform=lambda row: {key: value for key, value in row.items() if key != "trim_target"})

    def finalize_reasoning_outputs(self, collections, *, delivery_targets=None):
        """Finalize one reasoning result before exposing its selected formats."""
        selected = set(self.recipe["targets"] if delivery_targets is None else delivery_targets)
        if not cot_updates_sft(self.recipe):
            self.apply_reasoning_trim(collections)
            return
        if not selected.intersection({"sft", "cot"}):
            self.apply_reasoning_trim(collections, targets=("cot",))
            return
        self.apply_reasoning_trim(collections, targets=("cot",))
        if "sft" in selected:
            originals = collections["sft"]
            destination = self.path / "stage-results" / "sft-cot-final.jsonl"

            def checked_rows():
                for row in finalized_sft_rows(originals, collections["cot"]):
                    self.check_cancel()
                    yield row

            write_jsonl(destination, checked_rows())
            collections["sft"] = WorkflowRows(destination, len(originals))

    def preference(self, sample):
        pairs = self.dpo(sample)
        if self.recipe.get("review_repair") is not None:
            return pairs
        if not pairs or pairs[0]["status"] != "eligible":
            return pairs
        pair = pairs[0]
        chosen_check, rejected_check = pair["preference"]["chosen"], pair["preference"]["rejected"]
        # Every target gets independent text/score/rationale; records remain tied to one verified comparison.
        pair["rlaif"] = {"criterion": "correctness", "chosen_feedback": chosen_check["reason"],
                         "rejected_feedback": rejected_check["reason"], "judge": "jev",
                         "chosen_dimensions": chosen_check["scores"], "rejected_dimensions": rejected_check["scores"]}
        return [pair]

    def package(self, collections):
        if self.recipe.get("review_repair") is not None:
            collections = self.review_repair_collections(collections)
            self.stage = "package"
            self.state["stages"]["package"].update(status="running", phase="deterministic_checks")
        evaluation_references = self.recipe.get("evaluation_references", [])
        evaluation_index = (load_evaluation_index(self.path, evaluation_references)
                            if "cpt" in self.recipe["targets"] else None)
        with ExitStack() as cleanup:
            if "cpt" in self.recipe["targets"]:
                directory = self.path / "stage-results"
                directory.mkdir(exist_ok=True)
                release_path = directory / "package-released-cpt.sqlite3"
                release_path.unlink(missing_ok=True)
                cleanup.callback(release_path.unlink, missing_ok=True)
                released_index, released_near_index, released_rows = released_corpus_index(
                    self.path.parent.parent, self.recipe.get("cpt_reference_releases", []),
                    sqlite_path=release_path)
                cleanup.callback(released_near_index.close)

                current_path = directory / "package-current-cpt.sqlite3"
                current_path.unlink(missing_ok=True)
                cleanup.callback(current_path.unlink, missing_ok=True)
                corpus_index = CorpusNearDuplicateIndex(current_path)
                cleanup.callback(corpus_index.close)

                cluster_path = directory / "package-cpt-clusters.sqlite3"
                cluster_path.unlink(missing_ok=True)
                cleanup.callback(cluster_path.unlink, missing_ok=True)
                cluster_index = CorpusClusterSizes(cluster_path)
                cleanup.callback(cluster_index.close)
            else:
                released_index, released_near_index, released_rows = {}, None, 0
                corpus_index = cluster_index = None
            return self._package_collections(
                collections, released_index, released_near_index, released_rows,
                corpus_index, cluster_index, evaluation_index)

    def _prepare_package_candidates(self, collections, released_index, released_near_index,
                                    corpus_index, evaluation_index, preferences, node_style,
                                    review_config):
        """Apply deterministic filters first; keep candidate rows and identities on disk."""
        directory = self.path / "stage-results"
        directory.mkdir(exist_ok=True)
        candidates, plans, selections = {}, {}, {}
        identity_path = directory / "package-training-identities.sqlite3"
        with ExitStack() as resources:
            resources.callback(identity_path.unlink, missing_ok=True)
            identities = resources.enter_context(closing(sqlite3.connect(identity_path)))
            identities.execute("DROP TABLE IF EXISTS identities")
            identities.execute("CREATE TABLE identities (target TEXT, sha256 TEXT, PRIMARY KEY(target, sha256))")
            for target in self.recipe["targets"]:
                target_review = review_config["enabled"] and not self.skips_post_generation_review(target)
                spool = RowSpool(directory / f"package-{target}-candidates.jsonl")
                candidate_count = 0
                smallest = []
                try:
                    for index, original in enumerate(collections[target]):
                        self.check_cancel()
                        row = deepcopy(original)
                        if row["status"] == "eligible" and not self.skips_post_generation_review(target):
                            if issue := self.human_package_issue(row, target):
                                row.update(status="quarantined", reason=issue)
                        if row["status"] == "eligible" and target == "sft":
                            from lib.domain.reasoning_fields import sft_reasoning_issue
                            if issue := sft_reasoning_issue(row["messages"]):
                                row.update(status="quarantined", reason=issue)
                        if row["status"] == "eligible" and target in {"dpo", "orpo"}:
                            from lib.domain.workflow_targets import preference_score_issue
                            if issue := preference_score_issue(row):
                                row.update(status="quarantined", reason=issue)
                        if target == "gsm8k" and row["status"] == "eligible" and not validate_math_candidate(row):
                            row.update(status="quarantined", reason="gsm8k_arithmetic_verification_failed")
                        if row["status"] == "eligible":
                            payload = preferred_training_record(target, row, preferences, sft_output_style=node_style)
                            if target == "cpt" and corpus_index is not None:
                                evaluation_hit = evaluation_index.inspect(row["text"]) if evaluation_index else None
                                if evaluation_hit:
                                    row.update(status="quarantined", reason=evaluation_hit["reason"],
                                               evaluation_reference=evaluation_hit)
                                    quarantined_text = row.pop("text")
                                    # The audit record must not re-export a
                                    # held-out passage through retained evidence.
                                    row.pop("source_context", None)
                                    row["quarantined_text_sha256"] = hashlib.sha256(
                                        quarantined_text.encode("utf-8")).hexdigest()
                                    row["quarantined_text_chars"] = len(quarantined_text)
                                else:
                                    release = exact_release_match(released_index, row["text"])
                                    if release:
                                        release_id = f"{release['run_id']}/cpt-v{release['version']:04d}/{release['line']}"
                                        row.update(status="duplicate", reason="released_corpus_exact_duplicate",
                                                   duplicate_of=release_id, duplicate_scope="prior_cpt_release",
                                                   reference_release=release, similarity=1.0)
                                    elif released_near_index is not None and (
                                            duplicate := released_near_index.match(row["text"])):
                                        row.update(status="duplicate", reason="released_corpus_near_duplicate",
                                                   duplicate_scope="prior_cpt_release",
                                                   duplicate_of=duplicate["duplicate_of"],
                                                   representative_source=duplicate["representative_source"],
                                                   reference_release=duplicate["reference_release"],
                                                   similarity=duplicate["similarity"])
                                    else:
                                        reference = {key: row[key] for key in ("id", "source_name", "reference_release")
                                                     if key in row}
                                        duplicate = corpus_index.check_and_add(row["text"], reference)
                                        if duplicate:
                                            row.update(status="duplicate", duplicate_scope="current_run", **duplicate)
                            else:
                                changed = identities.execute("INSERT OR IGNORE INTO identities VALUES (?, ?)",
                                                             (target, digest(payload))).rowcount
                                if not changed:
                                    row.update(status="duplicate", reason="duplicate_training_content")
                            if row["status"] == "eligible":
                                candidate_count += 1
                                if target_review and review_config["mode"] == "sample":
                                    # Select the lowest stable hashes. Memory is bounded by the
                                    # per-target cap, independent of candidate population size.
                                    rank = int(digest([self.state["id"], target, index, digest(payload)]), 16)
                                    entry = (-rank, -index)
                                    if len(smallest) < review_config["max_samples_per_target"]:
                                        heapq.heappush(smallest, entry)
                                    elif entry > smallest[0]:
                                        heapq.heapreplace(smallest, entry)
                        spool.append(row)
                finally:
                    spool.close()
                candidates[target] = spool
                if target_review:
                    planned = (candidate_count if review_config["mode"] == "all" else
                               min(candidate_count, review_config["max_samples_per_target"],
                                   math.ceil(candidate_count * review_config["sample_percent"] / 100)))
                    if getattr(self, "_production_finalizing", False):
                        planned = sum(row.get("status") == "eligible" and row.get("package_review", {}).get("status") == "accepted"
                                      for row in candidates[target])
                    selections[target] = (None if review_config["mode"] == "all" else
                                          {-index for _, index in sorted(smallest, reverse=True)[:planned]})
                    if getattr(self, "_production_finalizing", False):
                        selections[target] = None
                    plans[target] = {"candidates": candidate_count, "planned": planned,
                                     "reviewed": 0, "accepted": 0, "rejected": 0,
                                     "unreviewed": candidate_count - planned,
                                     "coverage_percent": round(100 * planned / candidate_count, 4) if candidate_count else 0.0,
                                     "status": ("no_candidates" if not candidate_count else
                                                "all_reviewed" if planned == candidate_count else "sampled")}
            identities.commit()
        return candidates, plans, selections

    def _review_package_candidates(self, candidates, plans, selections, preferences, node_style):
        if getattr(self, "_production_finalizing", False):
            spool = RowSpool(self.path / "stage-results" / "production-final-reviews.jsonl")
            try:
                for target in plans:
                    for index, row in enumerate(candidates[target]):
                        if row["status"] == "eligible" and row.get("package_review", {}).get("status") == "accepted":
                            payload = preferred_training_record(target, row, preferences, sft_output_style=node_style)
                            if row["package_review"].get("candidate_sha256") != digest(payload):
                                raise ValueError("production_review_integrity_error")
                            spool.append({"target": target, "index": index, "status": "eligible", "package_review": row["package_review"]})
            finally:
                spool.close()
            return spool
        workflow = self
        class SelectedRows:
            def __len__(self):
                return sum(plan["planned"] for plan in plans.values())

            def __iter__(self):
                for target in plans:
                    for index, row in enumerate(candidates[target]):
                        workflow.check_cancel()
                        if row["status"] != "eligible":
                            continue
                        if selections[target] is not None and index not in selections[target]:
                            continue
                        payload = preferred_training_record(target, row, preferences, sft_output_style=node_style)
                        yield {"target": target, "index": index, "id": row.get("id"), "payload": payload,
                               "source": row.get("source_context"),
                               "provenance": {key: row[key] for key in (
                                   "source_id", "source_name", "source_location", "evidence_level", "synthetic") if key in row}}

        def review(item):
            candidate_hash = digest(item["payload"])
            check = self.judge_answer(
                [item.get("id") or candidate_hash[:16], "package_review", item["target"], item["index"], candidate_hash],
                {"target": item["target"], "source": item["source"], "provenance": item["provenance"]},
                item["payload"], prompt_id="workflow.package_review", allow_reasoning_fallback=False)
            passed = accepted(check)
            return [{"target": item["target"], "index": item["index"],
                     "status": "eligible" if passed else "quarantined",
                     "package_review": {"status": "accepted" if passed else "rejected",
                                        "mode": self.recipe["package_review"]["mode"],
                                        "candidate_sha256": candidate_hash, "verdict": check}}]

        review_stage = package_review_stage(self.recipe.get("package_review"))
        self.state["stages"].setdefault(review_stage, {"label": STAGES[review_stage], "done": 0, "total": 0})
        self.state["stages"][review_stage]["phase"] = "ai_review"
        reviewed = self.stage_items(review_stage, SelectedRows(), review, finalize=False)
        if review_stage == "package":
            self.state["stages"]["package"]["phase"] = "writing_artifacts"
        self.save()
        return reviewed

    def _package_collections(self, collections, released_index, released_near_index,
                             released_rows, corpus_index, cluster_index, evaluation_index):
        evaluation_references = self.recipe.get("evaluation_references", [])
        destination = getattr(self, "_production_destination", None) or self.path / "artifacts"
        destination.mkdir(exist_ok=True)
        cpt_references = self.recipe.get("cpt_reference_releases", [])
        if self.recipe.get("knowledge_retrieval") is not None:
            atomic_json(destination / "knowledge_retrieval.json", self.recipe["knowledge_retrieval"])
        report = {"policy": POLICY, "targets": {}, "trainer_exports": {}, "human_review": "not_performed",
                  "input_summary": self.state.get("input_summary", {}),
                  "input_issues": None,  # streamed into quality.json after target packaging
                  "limitations": ["模型评审不等于事实证明", "字符分块不是 tokenizer 长度",
                                  "CPT 当前批次及同工作区已发布版本的近重复筛查不证明来源许可或事实正确",
                                  "CPT 跨批参照仅覆盖任务创建时同一工作区已发布且清单可校验的版本；不覆盖其他工作区或未审核候选",
                                  "CPT 近重复候选索引可能漏检；短于 300 字符的不同文本只做精确去重",
                                  "Agent 默认仅重放受限整数 calculator 和录制快照上的 json_pointer；显式配置固定摘要镜像后可隔离重放受限 sandbox_ledger 状态机，仍不证明上传快照的外部真实性；其他工具隔离",
                                  "GSM8K 是受限整数算术模板样例，不等同完整 GSM8K 基准",
                                  "TRL 格式相容不证明所选模型聊天模板或 tokenizer 可用于训练",
                                  "敏感信息规则不能覆盖所有隐私类型"]}
        if "arithmetic_registry" in self.state:
            report["arithmetic_registry"] = deepcopy(self.state["arithmetic_registry"])
        preferences = self.recipe.get("generation_preferences")
        node_style = self.recipe.get("sft_output_style")
        if node_style is not None and node_style not in {"separated", "drop"}:
            raise ValueError("invalid_sft_output_style")
        if preferences:
            sft_export = "sft" in self.recipe["targets"]
            configured_style = preferences["values"]["cot_style"]
            supported_style = configured_style in {"separated", "drop"}
            export_style = (node_style if node_style is not None else
                            configured_style if supported_style else "separated")
            report["generation_preferences"] = {
                "source_sha256": preferences["sha256"],
                "configured_reasoning_style": configured_style,
                "node_reasoning_style": node_style,
                "export_reasoning_style": export_style if sft_export else None,
                "style_source": ("sft_node" if node_style is not None else "generation_preferences"),
                "applied_targets": ["sft"] if sft_export and (node_style is not None or supported_style) else [],
                "unapplied_fields": [*UNAPPLIED_PREFERENCE_FIELDS,
                                     "cot.think_tokens",
                                     *([] if sft_export and node_style is None and supported_style
                                        else ["cot.style"])],
            }
            report["limitations"].append(
                "生成偏好的权重、模板轮换和后验校正尚未用于自动工作流；"
                "仅 SFT 训练文件支持分字段保留或删除推理字段；plain/tags 尚不应用于自动工作流。"
                "Agent 轨迹及偏好对保持原始证据。")
        else:
            report["generation_preferences"] = {"status": "legacy_run_without_snapshot"}
        if self.recipe.get("node_generation"):
            report["node_generation"] = {
                stage: {"enabled": config.get("enabled", True), "style": config["style"],
                        **({"reasoning_language": config["reasoning_language"]} if "reasoning_language" in config else {}),
                        **({"reasoning_depth": config["reasoning_depth"]} if "reasoning_depth" in config else {}),
                        "instruction_sha256": digest(config.get("instruction", "")),
                        "reasoning_origin": "prompt_styled_generation" if config.get("enabled", True) else "ordinary_distillation",
                        "maximum_repair_attempts": 1}
                for stage, config in self.recipe["node_generation"].items()}
        if self.recipe.get("node_prompt_templates"):
            report["node_prompts"] = {
                stage: {prompt_id: {"sha256": digest(text),
                                     "custom": prompt_id in self.recipe.get("node_prompts", {}).get(stage, {})}
                        for prompt_id, text in templates.items()}
                for stage, templates in self.recipe["node_prompt_templates"].items()}
        if self.recipe.get("agent_experience_version") == 1:
            experience = self.recipe["agent_experience"]
            report["agent_experience"] = {"version": experience["version"], "sha256": experience["sha256"],
                "kind": experience["kind"], "scopes": {scope: {key: guidance[key]
                    for key in ("enabled", "text_sha256", "origin")}
                    for scope, guidance in experience["scopes"].items()}}
        if self.recipe.get("qa_director", {}).get("enabled"):
            report["qa_director"] = deepcopy(self.state.get("qa_director", {}))
            report["qa_director"].update(
                question_rules_sha256=digest(self.recipe["qa_director"]["question_rules"]),
                answer_rules_sha256=digest(self.recipe["qa_director"]["answer_rules"]),
                type_weights=self.recipe["qa_director"]["type_weights"],
                planning_rows="stage-results/director.jsonl",
                noise_policy="explicit_distractor_context_and_independent_answer_policy_review")
            report["qa_director"].update({key: deepcopy(self.recipe["qa_director"][key])
                for key in ("learning_goal", "user_languages") if key in self.recipe["qa_director"]})
            if "learning_goal" in self.recipe["qa_director"]:
                report["knowledge_learning_evaluation"] = {"status": "not_performed",
                    "scope": "data_generation_and_sample_review_only",
                    "student_training_performed": False,
                    "note": "CPT节点产出训练语料；生成与评审结果不证明学生模型已学会。需训练后单独进行不附原文的留出测试。"}
            report["limitations"].append(
                "问答指导的历史检索为有界词法候选召回；精确契约去重不等于语义去重，"
                "AI 契约评审不能证明事实正确或清除全部隐含来源泄漏。开放需求产生的情境与答案为模型评估的合成数据。")
            if self.recipe["qa_director"].get("human_augmentation", {}).get("enabled"):
                report["limitations"].append(
                    "人工增强的种子问题、答案与设计要求由用户提供并固定；生成与设计一致性经过模型评审，"
                    "不表示人工答案已经独立事实核验。已有完整会话保持原记录，不自动改写为人工话术变体。")
        if (self.recipe.get("reasoning_trim") or {}).get("enabled"):
            report["reasoning_trim"] = {
                "enabled": True, "template": self.recipe["reasoning_trim"]["template"],
                "applied_targets": [target for target in ("sft", "cot") if target in self.recipe["targets"]],
                "instruction_sha256": digest(self.recipe.get("reasoning_trim_prompt") or trim_prompt(self.recipe["reasoning_trim"])),
                "checks": ["meaning_and_correctness", "editing_rules"], "maximum_repair_attempts": 1,
                "original_rows": "retained_in_upstream_stage_files"}
            report["limitations"].append("推理修剪使用提示词和独立模型评审，不保证移除所有泄漏；仅处理 SFT/CoT 的显式推理字段，偏好对与 Agent 轨迹保持原始证据")
        if self._research_document is not None:
            atomic_json(destination / "web_research.json", self._research_document)
            report["web_research"] = {"status": "planning_leads_only", "provider": "brave",
                                      "topics": 1 + len(self.recipe["web_research"].get("more_queries", [])),
                                      "results": len(self._research_document["results"]),
                                      "retrieved_at": self._research_document["retrieved_at"]}
            report["limitations"].append("联网检索只提供开放任务规划线索；网页摘要未经独立事实核实，也不是可重放的 Agent 工具证据")
        if "cpt" in self.recipe["targets"]:
            report["limitations"].append(
                "CPT 评测集重叠检查仅覆盖本次上传并固定的参照；未提供的外部评测集污染状态未知"
                if evaluation_index is not None else
                "CPT 未配置评测集参照，外部评测集污染状态未知")
        if "rlaif" in self.recipe["targets"]:
            report["limitations"].append(
                "RLAIF 当前仅产出任务正确性准则下的 AI 反馈和奖励模型偏好候选；未训练奖励模型、提供在线奖励或运行强化学习")
        review_targets = [target for target in self.recipe["targets"]
                          if not self.skips_post_generation_review(target)]
        review_config = (validate_package_review(None) if self.recipe.get("review_repair") is not None or not review_targets
                         else validate_package_review(self.recipe.get("package_review")))
        if self.skips_post_generation_review("cpt"):
            report["cpt_review_policy"] = {"version": 1, "post_generation_review": "not_applicable",
                "quality_checks": "cpt_node_and_deterministic_packaging"}
        if self.recipe.get("review_repair") is not None and review_targets:
            report["review_repair"] = {**self.recipe["review_repair"], "node": "review", "targets": {}}
            for target in review_targets:
                statuses, attempts = {}, 0
                for row in collections[target]:
                    receipt = row.get("review_repair", {})
                    status = receipt.get("status", "not_reviewed")
                    statuses[status] = statuses.get(status, 0) + 1
                    attempts += receipt.get("attempts", 0)
                report["review_repair"]["targets"][target] = {"statuses": statuses, "repair_attempts": attempts}
            if self.recipe["review_repair"]["mode"] == "human":
                report["human_review"] = "performed" if any(
                    row.get("review_repair", {}).get("score") is not None
                    for target in review_targets for row in collections[target]) else "pending"
        independent_review = (review_config["enabled"] and package_review_stage(review_config) == "jev"
                              and not getattr(self, "_production_finalizing", False))
        if independent_review:
            self.stage = "jev"
            self.state["stages"].setdefault("jev", {"label": STAGES["jev"], "done": 0, "total": 0})
            self.state["stages"]["jev"].update(status="running", phase="preparing_candidates", done=0, total=0)
            self.state["stages"]["package"].update(status="pending", phase="waiting_for_jev", done=0, total=1)
            self.save()
        else:
            self.state["stages"]["package"]["phase"] = "deterministic_checks"
        self.save(force=False)
        candidates, review_plans, selections = self._prepare_package_candidates(
            collections, released_index, released_near_index, corpus_index,
            evaluation_index, preferences, node_style, review_config)
        review_iterator, next_review = iter(()), None
        if review_config["enabled"]:
            reviewed = self._review_package_candidates(candidates, review_plans, selections, preferences, node_style)
            if self.production_enabled() and not getattr(self, "_production_finalizing", False):
                failures = {target: 0 for target in review_plans}
                for row in reviewed:
                    failures[row["target"]] += int(row["status"] != "eligible")
                expanded = False
                for target, plan in review_plans.items():
                    escalation = package_review_escalation(review_config, sampled=plan["planned"],
                        failed=failures[target], candidates=plan["candidates"], production=True)
                    if escalation:
                        plan.update(escalation=escalation, planned=plan["candidates"], unreviewed=0,
                                    coverage_percent=100.0, status="all_reviewed")
                        selections[target] = None
                        expanded = True
                if expanded:
                    self.event("package_review_expanded", targets=[target for target, plan in review_plans.items() if plan.get("escalation")])
                    reviewed = self._review_package_candidates(candidates, review_plans, selections, preferences, node_style)
            review_iterator = iter(reviewed)
            next_review = next(review_iterator, None)
            report["package_review"] = {**review_config, "status": "completed",
                                        "selection": ("stable_hash_per_target_per_round" if self.production_enabled() else "stable_hash_per_target"),
                                        "targets": review_plans}
            report["limitations"].append("打包 AI 评审只代表模型判断；抽检未选中的样本未经过该项评审，不能把抽检通过率当作全量正确率")
        else:
            report["package_review"] = {**review_config, "status": "disabled"}
        if independent_review:
            metrics = self.state["stages"]["jev"]
            metrics.update(status="completed", phase="completed", finished_at=now())
            self.event("stage_completed", outputs=metrics.get("outputs", 0))
            self.stage = "package"
            self.state["stages"]["package"].update(status="running", phase="writing_artifacts", done=0, total=1)
            self.save()
        with self.qa_publication_guard() as qa_history:
            for target in self.recipe["targets"]:
                self.check_cancel()
                records = RowSpool(self.path / "stage-results" / f"package-{target}-records.jsonl")
                training = RowSpool(self.path / "stage-results" / f"package-{target}-training.jsonl")
                try:
                    for index, row in enumerate(candidates[target]):
                        self.check_cancel()
                        if row["status"] == "eligible":
                            payload = preferred_training_record(target, row, preferences, sft_output_style=node_style)
                            if target in review_plans:
                                selected = selections[target] is None or index in selections[target]
                                if getattr(self, "_production_finalizing", False):
                                    selected = row.get("package_review", {}).get("status") == "accepted"
                                if selected:
                                    if next_review is None or (next_review["target"], next_review["index"]) != (target, index):
                                        raise ValueError("package_review_checkpoint_mismatch")
                                    row["package_review"] = next_review["package_review"]
                                    if row["package_review"]["candidate_sha256"] != digest(payload):
                                        raise ValueError("package_review_checkpoint_mismatch")
                                    plan = review_plans[target]
                                    plan["reviewed"] += 1
                                    if row["package_review"]["status"] == "accepted":
                                        plan["accepted"] += 1
                                    else:
                                        plan["rejected"] += 1
                                        row.update(status="quarantined", reason="package_ai_review_rejected")
                                    next_review = next(review_iterator, None)
                                else:
                                    row["package_review"] = {"status": "not_selected", "mode": review_config["mode"],
                                                             "candidate_sha256": digest(payload)}
                            if (row["status"] == "eligible"
                                    and self.recipe.get("training_surface_protocol_version") in {1, 2}):
                                from lib.infrastructure.workflow_review_repair import _row_training_surface_issue
                                if issue := _row_training_surface_issue(target, row, self.recipe):
                                    row.update(status="quarantined", reason=issue)
                            if (row["status"] == "eligible" and self.recipe.get("reasoning_required") is True
                                    and target in {"sft", "multiturn", "agent"}):
                                from lib.domain.turn_review import reasoning_text
                                # Check the actual trainer payload as well as
                                # the reviewed row: an output-style projection
                                # must not erase reasoning required by this run.
                                if any(message.get("role") == "assistant"
                                       and not (message.get("tool_calls") or message.get("toolCalls"))
                                       and not reasoning_text(message) for message in payload.get("messages", [])):
                                    row.update(status="quarantined", reason="training_reasoning_required")
                            if row["status"] == "eligible":
                                duplicate = self.final_qa_duplicate(row, target, qa_history)
                                if duplicate:
                                    row.update(status="quarantined", reason="released_qa_contract_duplicate",
                                               duplicate_of=duplicate["id"], duplicate_scope=duplicate.get("duplicate_scope", "published_qa_history"))
                            if row["status"] == "eligible":
                                self._production_accept_row(target, row, payload)
                            if row["status"] == "eligible":
                                if self.recipe.get("qa_agent_memory_version") == 1:
                                    self.register_final_qa_input(row, target)
                                training.append(payload)
                        records.append(row)
                finally:
                    records.close()
                    training.close()
                if target == "cpt":
                    for row in records:
                        cluster_index.add(row)
                    records = WorkflowRows(records.path, len(records), transform=cluster_index.annotate)
                # Atomic checkpoints hold metadata; files contain only the selected training schema.
                output = destination / f"{target}.jsonl"
                write_jsonl(output, training)
                write_json_array(destination / f"{target}.records.json", records)
                reasons = {}
                for row in records:
                    if row.get("reason"):
                        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
                report["targets"][target] = {"eligible": len(training), "total": len(records), "reasons": reasons}
                if target in review_plans:
                    report["targets"][target]["package_review"] = review_plans[target]
                if target in {"sft", "multiturn", "agent", "dpo", "orpo", "rlaif"}:
                    # A trainer export is complete only if every eligible native row converts.
                    # Keep the native target even when its optional TRL format is incompatible.
                    report["trainer_exports"][target] = write_trainer_export(
                        destination, target, records, preferences,
                        sft_output_style=node_style)
                if target == "cpt":
                    selected_count = len(collections[target])
                    quality_passed = sum(row["status"] == "eligible" for row in collections[target])
                    input_summary = self.state.get("input_summary", {})
                    def rate(numerator, denominator):
                        return round(numerator / denominator, 4) if denominator else None
                    report["targets"][target]["retention"] = {
                        "parsed_units": input_summary.get("units", 0),
                        "parsed_ready": input_summary.get("ready", 0),
                        "selected": selected_count,
                        "quality_passed": quality_passed,
                        "exact_duplicates": reasons.get("exact_duplicate_corpus", 0),
                        "near_duplicates": reasons.get("near_duplicate_corpus", 0),
                        "released_exact_duplicates": reasons.get("released_corpus_exact_duplicate", 0),
                        "released_near_duplicates": reasons.get("released_corpus_near_duplicate", 0),
                        "exported": len(training),
                        "parse_rate": rate(input_summary.get("ready", 0), input_summary.get("units", 0)),
                        "quality_rate": rate(quality_passed, selected_count),
                        "dedup_rate": rate(len(training), quality_passed),
                        "overall_rate": rate(len(training), selected_count),
                    }
                    report["targets"][target]["filter_policy"] = {
                        "near_duplicate": "NFC + whitespace-folded character 7-gram Jaccard",
                        "minimum_chars": 300, "jaccard_threshold": 0.90,
                        "short_text": "exact normalized deduplication only",
                        "structure_sensitive": "code, tables and equations use exact deduplication only",
                        "scope": ("current run exact/near; pinned same-workspace CPT releases exact/near"
                                  if self.recipe["version"] >= 4 else "legacy current-run exact/near only"),
                        "reference_releases": cpt_references,
                        "reference_rows": released_rows,
                    }
                    report["targets"][target]["decontamination"] = {
                        "status": "checked" if evaluation_index is not None else "not_configured",
                        "reference_files": len(evaluation_references),
                        "reference_rows": evaluation_index.reference_rows if evaluation_index else 0,
                        "short_for_embedded": evaluation_index.short_for_embedded if evaluation_index else 0,
                        "verbatim_overlaps": reasons.get("evaluation_verbatim_overlap", 0),
                        "near_overlaps": reasons.get("evaluation_near_overlap", 0),
                    }
                    report["targets"][target]["source_breakdown"] = summarize_corpus_sources(records)
                if target == "agent":
                    negative_count = sum(bool(row.get("negative")) for row in records)
                    sidecar = destination / "agent.negative.jsonl"
                    write_jsonl(sidecar, (row["negative"] for row in records if row.get("negative")))
                    report["targets"][target]["negative"] = negative_count
            if next_review is not None:
                raise ValueError("package_review_checkpoint_mismatch")
            if getattr(self, "_production_finalizing", False):
                self._production_report(report)
            input_records=self.path/"input_records.json"
            if input_records.is_file() and not getattr(self,"_production_collecting",False):
                from lib.infrastructure.document_figure_bundle import write_document_figure_bundle
                figures=write_document_figure_bundle(iter_json_records(input_records),destination/"source_figures.zip",
                    check_cancel=self.check_cancel)
                if figures["figures"]:report["source_figures"]=figures
            _write_quality_report(destination / "quality.json", report,
                                  self.path / "input_records.json")
            self.state["quality"] = {"policy": report["policy"], "targets": report["targets"],
                                     "human_review": report["human_review"],
                                     "package_review": report["package_review"],
                                     "trainer_exports": {target: {key: value for key, value in summary.items()
                                                                if key != "failures"}
                                                         for target, summary in report["trainer_exports"].items()}}
            if "production" in report:
                self.state["quality"]["production"] = report["production"]
            if "qa_director" in report:
                self.state["quality"]["qa_director"] = report["qa_director"]
            if "review_repair" in report:
                self.state["quality"]["review_repair"] = report["review_repair"]
            if "source_figures" in report:
                self.state["quality"]["source_figures"]=report["source_figures"]
            for pending in destination.glob(".*.pending"):
                pending.unlink(missing_ok=True)
            # Audit sidecars can contain the original rejected text. Scan the
            # whole package, not just its eligible trainer rows, before any
            # manifest or QA-history publication can make it deliverable.
            from lib.infrastructure.export_credentials import ExportCredentialGuard
            ExportCredentialGuard.from_root(self.root).check_folder(destination)
            files = {p.name: file_hash(p) for p in destination.iterdir() if p.is_file() and p.name != "manifest.json"}
            if not getattr(self, "_production_collecting", False):
                self.publish_qa_history(destination)
            atomic_json(destination / "manifest.json", {"status": "complete", "run_id": self.state["id"],
                        "recipe_hash": self.state["recipe_hash"], "policy": POLICY, "models": self.state.get("models", {}),
                        "sources": self.recipe["sources"], "counts": {t: v["eligible"] for t, v in report["targets"].items()},
                        "credential_scan": {"status": "passed", "method": "exact_configured_model_credentials"},
                        "trainer_counts": {t: v["summary"]["compatible"] for t, v in report["trainer_exports"].items()
                                           if v["status"] == "ready"},
                        "negative_counts": {t: v["negative"] for t, v in report["targets"].items() if v.get("negative")},
                        "package_review": report["package_review"],
                        **({"production": report["production"]} if "production" in report else {}),
                        **({"qa_director": {"coverage": report["qa_director"]["coverage"],
                                            "batches_planned": report["qa_director"]["batches_planned"]}}
                           if "qa_director" in report else {}),
                        "cpt_reference_releases": cpt_references,
                        "evaluation_references": evaluation_references,
                        "sha256": files, "created_at": now(), "release_kind": "automatically_checked_candidate"})
            return []

    def execute(self, resume_run=False):
        """Compatibility entry point; execution policy lives in the application."""
        from lib.bootstrap.workflow_execution import workflow_execution

        return workflow_execution(self).execute(resume=resume_run)


# Public compatibility alias: callers share one implementation and class identity.
Workflow = TrainingWorkflowRuntime


def resume(output, run_id, root, **clients):
    return Workflow(output, run_id, root, **clients).execute(resume_run=True)
