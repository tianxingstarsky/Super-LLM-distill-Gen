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
from lib.contexts.workflows.contracts import director_delivery_audit
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
SUPPORTED_RECIPE_VERSIONS = frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41})

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
               review_repair=None, repair_inputs=None, agent_experience=None, reasoning_required=False, preference_design=None):
    from lib.contexts.workflows.contracts import new_preference_design, preference_design_requested
    contrastive_enabled = preference_design_requested(preference_design, node_prompts)
    preference_design = new_preference_design(preference_design)
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
        reasoning_required=reasoning_required, preference_design=preference_design)
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
    if (node_generation.get("cot", {}).get("self_check") or any(
            pid in {"workflow.cot_self_check", "workflow.cot_self_check_review"}
            for pid in node_prompts.get("cot", {}))):
        recipe_version = 23
    if (node_generation.get("cot", {}).get("self_check", {}).get("version") == 2 or any(
            pid in {"workflow.cot_claim_check", "workflow.cot_claim_review"} for pid in node_prompts.get("cot", {}))):
        recipe_version = 24
    if (node_generation.get("cot", {}).get("self_check", {}).get("version") == 3
            or "workflow.cot_check_agent" in node_prompts.get("cot", {})):
        recipe_version = 25
    if node_generation.get("cot", {}).get("self_check", {}).get("version") == 4:
        recipe_version = 26
    if node_generation.get("cot", {}).get("self_check", {}).get("version") == 5:
        recipe_version = 27
    if node_generation.get("cot", {}).get("self_check", {}).get("version") == 6:
        recipe_version = 28
    if node_generation.get("cot", {}).get("self_check", {}).get("evidence_policy") == 1:
        recipe_version = 29
    if node_generation.get("cot", {}).get("self_check", {}).get("evidence_policy") == 2:
        recipe_version = 32
        node_generation["cot"]["self_check"]["batch_submission_version"] = 1
    if node_generation.get("cot", {}).get("self_check", {}).get("version") == 7:
        recipe_version = 35
    if node_generation.get("cot", {}).get("answer_mode") == "native_joint":
        recipe_version = 40
    if node_generation.get("multiturn", {}).get("reading_session"):
        recipe_version = 41
    node_prompt_templates = snapshot_node_prompts(node_prompts, recipe_version=recipe_version,
        learner_context_review_version=1, claim_review_protocol_version=2, claim_review_format_feedback_version=2,
        claim_refutation_release_version=1, claim_reference_policy_version=1, claim_review_anchor_protocol_version=1,
        claim_review_adversarial_protocol_version=1, claim_review_wire_schema_version=1, claim_review_focus_version=1,
        claim_review_condition_contract_version=1, reasoning_depth_contract_version=1, claim_review_transport_version=1,
        claim_repair_feedback_version=1, preference_design_version=3 if contrastive_enabled else None,
        preference_design_prompt_version=1 if contrastive_enabled else None)
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
    author_endpoint = (endpoint_pins or {}).get("cot", {}).get("generation", {})
    if (recipe_version == 35 and author_endpoint.get("endpoint_origin") == "https://api.deepseek.com"
            and author_endpoint.get("api_format") == "chat"):
        # Freeze only the provider/protocol whose strict grammar is supported.
        # Historical runs and other providers retain their existing transport.
        recipe_version = 39
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
            claim_review_condition_contract_version=1, reasoning_depth_contract_version=1, claim_review_transport_version=1,
            claim_repair_feedback_version=1, preference_design_version=3 if contrastive_enabled else None,
            preference_design_prompt_version=1 if contrastive_enabled else None)
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
              "external_reasoning_import_version": 1,
              **({"source_cleaning": True, "source_context_pack_version": 1,
                  "source_line_protocol_version": 1, "source_context_capacity_version": 2,
                  "source_context_scope_version": 1} if source_cleaning else {}),
              "source_memory_protocol_version": 2,
              "provider_reasoning_capture_version": 1,
              "authored_reasoning_protocol_version": 1,
              **({"authored_trace_transport_version": 1} if recipe_version >= 31 else {}),
              **({"authored_stage_contract_version": 1} if recipe_version >= 34 else {}),
              **({"reflection_guidance_placement_version": 1} if recipe_version >= 35 else {}),
              **({"authored_submission_version": 1} if recipe_version >= 36 else {}),
              **({"authored_local_edit_version": 1} if recipe_version >= 36 else {}),
              **({"cot_style_transfer_version": 2} if recipe_version >= 36 else {}),
              **({"authored_task_contract_version": 3} if recipe_version >= 39 else {}),
              "authored_reasoning_warmup_version": 1,
              "reasoning_depth_contract_version": 1,
              "native_prefix_review_style_version": 1,
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
              "claim_review_transport_version": 1,
              "claim_repair_feedback_version": 1,
              "claim_context_quote_feedback_version": 1,
              "review_reference_reassessment_version": 1,
              "training_format_lineage_version": 1,
              **({"preference_design_version": 3, "preference_design_prompt_version": 1,
                  "preference_design": preference_design} if contrastive_enabled else {}),
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
              **({"native_experience_version": 1} if (node_generation.get("multiturn", {}).get("reading_session")
                  or node_generation.get("cot", {}).get("answer_mode") == "native_joint") else {}),
              **({"native_environment_version": 1}
                 if node_generation.get("cot", {}).get("answer_mode") == "native_joint" else {}),
              **({"reading_actor_protocol_version": 2} if node_generation.get("multiturn", {}).get("reading_session") else {}),
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
        from lib.bootstrap.workflow_requests import workflow_request_adapter
        return workflow_request_adapter(self, globals()).client(role, stage=stage)

    def historical_prompt(self, prompt_id):
        from lib.bootstrap.workflow_requests import workflow_request_adapter
        return workflow_request_adapter(self, globals()).historical_prompt(prompt_id)

    def prompt_text(self, prompt_id, *, stage=None):
        from lib.bootstrap.workflow_requests import workflow_request_adapter
        return workflow_request_adapter(self, globals()).prompt_text(prompt_id, stage=stage)

    def ask(self, key, role, prompt_id, data, *, image=None, instruction=None,
            allow_reasoning_fallback=True, model_stage=None, prompt_stage=None,
            capture_response_details=False, reasoning_prefix=None, native_request_attempts=None,
            structured_response_attempts=None):
        from lib.bootstrap.workflow_requests import workflow_model_request
        return workflow_model_request(self, globals()).execute(key, role, prompt_id, data,
            image=image, instruction=instruction, allow_reasoning_fallback=allow_reasoning_fallback,
            model_stage=model_stage, prompt_stage=prompt_stage,
            capture_response_details=capture_response_details, reasoning_prefix=reasoning_prefix,
            native_request_attempts=native_request_attempts,
            structured_response_attempts=structured_response_attempts)

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
        from lib.contexts.workflows.domain.generation import generation_style
        return generation_style(self.recipe, stage, sample_id)

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
        from lib.bootstrap.node_generation import sft_generator
        return sft_generator(self).execute(unit)

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
        if self.recipe.get("preference_design_version") in {1, 2, 3} and self.recipe.get("review_repair") is not None:
            from lib.bootstrap.node_generation import preference_generator
            return preference_generator(self).execute(sample)
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
        from lib.bootstrap.node_generation import cot_generator
        return cot_generator(self).execute(sample)

    def _cot_result(self, sample):
        from lib.bootstrap.node_generation import cot_generator
        return cot_generator(self).extract(sample)

    def styled_cot(self, sample, style):
        from lib.bootstrap.node_generation import cot_generator
        return cot_generator(self).generate_styled(sample, style)

    def native_styled_answer(self, sample, task, style, feedback, attempt):
        from lib.bootstrap.node_generation import cot_generator
        return cot_generator(self).generate_native_answer(sample, task, style, feedback, attempt)

    def trim_reasoning(self, item):
        from lib.bootstrap.node_generation import reasoning_trimmer
        return reasoning_trimmer(self).execute(item)

    def apply_reasoning_trim(self, collections, *, targets=None):
        from lib.bootstrap.node_generation import reasoning_trimmer
        return reasoning_trimmer(self).apply(collections, targets=targets)

    def finalize_reasoning_outputs(self, collections, *, delivery_targets=None):
        from lib.bootstrap.node_generation import reasoning_trimmer
        return reasoning_trimmer(self).finalize(collections, delivery_targets=delivery_targets)

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
            report["qa_director"] = director_delivery_audit(
                self.state.get("qa_director", {}), self.recipe["qa_director"]["type_weights"])
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
