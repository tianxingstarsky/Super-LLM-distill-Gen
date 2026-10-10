"""Durable automatic training-data workflows, independent of browser sessions.

Each run owns immutable input copies, a pinned recipe, per-item checkpoints,
stage metrics and a hash-verified training bundle. No global output is overwritten.
"""
from __future__ import annotations

from copy import deepcopy
from collections import deque
from contextlib import ExitStack, closing
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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

from filelock import FileLock, Timeout

from lib.application.preference_service import preference_summary
from lib.application.trainer_export_service import prepare_trl_export
from lib.domain.workflow_quality import POLICY, accepted, canonical, conversation_issue, same_answer, text_issue, tool_error_flag, verdict
from lib.domain.agent_trajectory import REPLAY_POLICY_VERSION, assess_recorded_trajectory
from lib.domain.source_conversation import source_conversation_issue
from lib.domain.corpus_quality import (CorpusClusterSizes, CorpusNearDuplicateIndex,
                                       inspect_corpus, summarize_corpus_sources)
from lib.domain.math_tasks import build_gsm8k, validate_gsm8k, validate_math_candidate
from lib.domain.open_task_plan import MAX_TASK_CHARS, task_identity, task_plan_issue
from lib.domain.workflow_creation import validate_creation
from lib.domain.document_parser import validate_document_parser
from lib.domain.web_research import validate_web_research
from lib.domain.workflow_scale import PLAN_BATCH_SIZE
from lib.domain.workflow_generation import validate_node_generation, style_for_sample
from lib.domain.workflow_reasoning_route import cot_updates_sft, cot_with_sft_context, finalized_sft_rows
from lib.domain.reasoning_trim import validate_reasoning_trim, trim_prompt
from lib.domain.workflow_package_review import validate_package_review, package_review_stage
from lib.domain.cpt_processing import validate_cpt_processing
from lib.domain.source_quote import quote_spans
from lib.domain.workflow_qa_director import validate_qa_director
from lib.domain.human_augmentation import human_design, human_generation_instruction
from lib.domain.workflow_node_prompts import (
    validate_node_prompts, snapshot_node_prompts, validate_node_prompt_snapshot,
)
from lib.infrastructure.json_stream import iter_json_records, iter_source_json_records
from lib.infrastructure.source_snapshot import snapshot_source
from lib.infrastructure.generation_settings_file import FileGenerationSettingsDriver
from lib.infrastructure.brave_web_research import (
    search as search_web, validate_search_document, validate_search_topic_document,
)
from lib.infrastructure.workflow_rows import WorkflowRows, RowSpool, write_jsonl, write_json_array
from lib.infrastructure.workflow_row_checkpoint import row_checkpoint
from lib.infrastructure.workflow_candidates import prepare_generation_rows
from lib.domain.workflow_production import validate_production
from lib.infrastructure.workflow_production import WorkflowProduction
from lib.infrastructure.production_checkpoints import ProductionCheckpoints
from lib.infrastructure.production_review_quality import package_review_escalation
from lib.infrastructure.planning_identities import PlanningIdentities
from lib.infrastructure.workflow_qa_director import WorkflowQADirector, DialoguePlanError
from lib.infrastructure.workflow_cpt_processing import WorkflowCPTProcessing
from lib.domain.multiturn import completed_turn_ends
from lib.domain.workflow_targets import (INPUT_EXTENSIONS, PREFERENCE_TARGETS, STAGES, TARGETS,
                                         rlaif_feedback_issue, rlaif_reward_model_record, training_record)
from lib.infrastructure.corpus_reference import exact_release_match, released_corpus_index, snapshot_released_corpus
from lib.infrastructure.evaluation_reference import load_evaluation_index, snapshot_evaluation_sources
from lib.infrastructure.agent_docker_replay import (DockerLedgerReplay, IMAGE_ENV, RUNNER_SHA256,
                                                    validate_sandbox_image)
from lib.doc2corpus import SUPPORTED_EXTS, chunk_text, import_text
from lib.io_utils import _replace_state, atomic_json
from lib.llm_client import BudgetGuard, ChatClient, chat_json, load_backend, snapshot_backend_endpoint
from lib.infrastructure.workflow_stream_journal import StreamJournal
from lib.prompts import get, registry, render


EXTENSIONS = INPUT_EXTENSIONS
MAX_FILE_BYTES = 50 * 1024 * 1024
RECIPE_VERSION = 11
PRODUCTION_RECIPE_VERSION = 12
SOFT_PRODUCTION_RECIPE_VERSION = 13
DOCUMENT_PROCESSING_RECIPE_VERSION = 14
COT_SFT_RECIPE_VERSION = 15
JEV_NODE_RECIPE_VERSION = 16
HUMAN_AUGMENTATION_RECIPE_VERSION = 17
SUPPORTED_RECIPE_VERSIONS = frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17})

# The automatic workflow has no recipe pools or batch tagger. Keep those
# preferences visible as unapplied until their behavior can be implemented.
UNAPPLIED_PREFERENCE_FIELDS = (
    "preferences.*", "sampling.default_floor", "sampling.templates_per_dim",
    "sampling.shuffle_per_batch", "correction.*",
)


def preference_snapshot(root):
    """Validate and pin settings once, before a run or any input is created."""
    text = FileGenerationSettingsDriver(root).read("生成偏好")
    return {"version": 1, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "values": preference_summary(text)}


def preferred_training_record(target, row, preferences=None, *, reward_model=False,
                              sft_output_style=None):
    """Canonicalize SFT explanation fields without changing scored answers."""
    record = rlaif_reward_model_record(row) if reward_model else training_record(target, row)
    style = (sft_output_style if sft_output_style is not None else
             preferences["values"]["cot_style"] if preferences else None)
    if target != "sft":
        return record
    from lib.domain.reasoning_fields import sft_training_messages
    return {**record, "messages": sft_training_messages(record["messages"], drop=style == "drop")}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


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


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_path(output, run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("无效运行 ID")
    return Path(output) / "workflows" / run_id


def list_runs(output):
    result = []
    for path in (Path(output) / "workflows").glob("*/state.json"):
        try:
            result.append(read_json(path))
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def is_active(path):
    try:
        with FileLock(str(Path(path) / ".run.lock"), timeout=0):
            return False
    except Timeout:
        return True


def cancel(output, run_id):
    path = run_path(output, run_id)
    if not (path / "state.json").exists():
        raise ValueError("运行不存在")
    atomic_json(path / "cancel.json", {"requested_at": now()})


def verify_artifacts(path):
    path = Path(path)
    artifacts = path / "artifacts"
    manifest = read_json(artifacts / "manifest.json")
    hashes = manifest.get("sha256")
    if manifest.get("status") != "complete" or not isinstance(hashes, dict) or not hashes:
        raise ValueError("incomplete_artifact_manifest")
    actual = {item.name for item in artifacts.iterdir() if item.is_file() and item.name != "manifest.json"}
    if actual != set(hashes):
        raise ValueError("artifact_inventory_mismatch")
    for name, expected in hashes.items():
        item = artifacts / name
        if Path(name).name != name or item.is_symlink() or file_hash(item) != expected:
            raise ValueError("artifact_integrity_error")
    return manifest


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
               package_review=None, qa_director=None, production=None, cpt_processing=None, run_id=None):
    targets, node_models = validate_creation(
        targets=targets, max_units=max_units, chunk_chars=chunk_chars, tasks=tasks,
        sample_count=sample_count, concurrency=concurrency, batch_size=batch_size,
        node_models=node_models, conversation_turns=conversation_turns, brief=brief,
        agent_replay_mode=agent_replay_mode, evaluation_sources=evaluation_sources,
        web_research=web_research, sources=sources, node_generation=node_generation,
        reasoning_trim=reasoning_trim, node_prompts=node_prompts,
        package_review=package_review, qa_director=qa_director, production=production,
        cpt_processing=cpt_processing)
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
    from lib.domain.workflow_node_prompts import VERSION_13_NODE_PROMPT_IDS
    new_prompt_override = any(prompt_id not in VERSION_13_NODE_PROMPT_IDS.get(stage, ())
                              for stage, templates in node_prompts.items() for prompt_id in templates)
    human_prompt_override = any("workflow.human_augmentation_check" in templates for templates in node_prompts.values())
    recipe_version = (HUMAN_AUGMENTATION_RECIPE_VERSION if human["enabled"] or human_prompt_override else
                      JEV_NODE_RECIPE_VERSION if package_review_stage(package_review) == "jev" else
                      COT_SFT_RECIPE_VERSION if {"sft", "cot"}.issubset(targets) else
                      DOCUMENT_PROCESSING_RECIPE_VERSION if cpt_processing_supplied
                      or (isinstance(document_parser, dict) and document_parser.get("mode") == "model")
                      or new_prompt_override else
                      SOFT_PRODUCTION_RECIPE_VERSION if production and production["version"] == 2
                      else PRODUCTION_RECIPE_VERSION if production is not None else RECIPE_VERSION)
    node_prompt_templates = snapshot_node_prompts(node_prompts, recipe_version=recipe_version)
    node_prompt_system = render(get("workflow.system"))
    generation_prompts = {}
    for stage, config in node_generation.items():
        if config.get("enabled", True):
            presets = (("concise", "structured", "skeptical", "reflective")
                       if config["style"] == "mixed" else (config["style"],))
            generation_prompts[stage] = {preset: style_for_sample({**config, "style": preset}, "snapshot")["instruction"]
                                         for preset in presets}
    if (sft_output_style is not None and
            (type(sft_output_style) is not str or sft_output_style not in {"separated", "drop"}
             or "sft" not in targets)):
        raise ValueError("invalid_sft_output_style")
    preferences = preference_snapshot(settings_root or Path(__file__).resolve().parents[2])
    files = [Path(p).resolve(strict=True) for p in sources]
    if cpt_processing["mode"] == "model" and cpt_processing["review_mode"] == "vision":
        from lib.infrastructure.document_vision import require_vision_model
        binding = node_models.get("cpt", {}).get("jev")
        if settings_root is None or not binding:
            raise ValueError("cpt_visual_review_model_required")
        require_vision_model(Path(settings_root), binding)
        if not files:
            raise ValueError("cpt_visual_review_requires_documents")
    document_parser = validate_document_parser(document_parser)
    if document_parser["mode"] == "vision":
        from lib.infrastructure.document_vision import require_vision_model
        if settings_root is None or not files or any(path.suffix.lower() in {".json", ".jsonl"} for path in files):
            raise ValueError("document_vision_requires_document_sources")
        require_vision_model(Path(settings_root), document_parser["binding"])
        node_models.setdefault("ingest", {})["vision"] = document_parser["binding"]
    elif document_parser["mode"] == "model":
        if not files or any(path.suffix.lower() in {".json", ".jsonl", ".png", ".jpg", ".jpeg", ".webp"} for path in files):
            raise ValueError("document_text_requires_document_sources")
        node_models.setdefault("ingest", {})["generation"] = document_parser["binding"]
    elif any(path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} for path in files):
        raise ValueError("document_image_requires_vision_parser")
    if not files and not brief.strip() and not human["enabled"]:
        raise ValueError("请上传来源文件或填写开放性需求")
    if "agent" in targets and not files and not (set(targets) - {"agent"}):
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
                           if "agent" in targets and agent_replay_mode != "local" else None)
    if "agent" in targets and agent_replay_mode == "isolated" and not agent_sandbox_image:
        raise ValueError("agent_sandbox_not_configured")
    run_id = uuid.uuid4().hex if run_id is None else run_id
    # Human workspaces reserve this identity durably before creating the child.
    # The usual validated run path and exclusive input mkdir still prevent reuse.
    run_path(output, run_id)
    endpoint_pins = None
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
              "planning_policy_version": 2, "source_processing_version": 2,
              "cpt_reference_releases": references,
              "evaluation_references": evaluation_references,
              "agent_sandbox_image": agent_sandbox_image,
              "sample_count": sample_count, "concurrency": concurrency, "batch_size": batch_size,
              "node_models": node_models, "web_research": web_research,
              "document_parser": document_parser,
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
                "stages": {key: {"label": label, "status": "pending", "done": 0, "total": 0}
                           for key, label in STAGES.items()
                           if key != "jev" or (package_review["enabled"] and package_review_stage(package_review) == "jev")},
                "events": [], "usage": {}})
    return run_id


class Cancelled(Exception):
    pass


class Workflow(WorkflowProduction, WorkflowQADirector, WorkflowCPTProcessing):
    def __init__(self, output, run_id, root, *, generator=None, judge=None, jev=None):
        self.path = run_path(output, run_id)
        self.root = Path(root)
        self.state = read_json(self.path / "state.json")
        self.recipe = read_json(self.path / "recipe.json")
        parser = validate_document_parser(self.recipe.get("document_parser"))
        if parser["mode"] == "vision":
            from lib.infrastructure.document_vision import require_vision_model
            require_vision_model(self.root, parser["binding"])
        cpt_config = validate_cpt_processing(self.recipe.get("cpt_processing"))
        if cpt_config["mode"] == "model" and cpt_config["review_mode"] == "vision":
            from lib.infrastructure.document_vision import require_vision_model
            binding = self.recipe.get("node_models", {}).get("cpt", {}).get("jev")
            if not binding:
                raise ValueError("cpt_visual_review_model_required")
            require_vision_model(self.root, binding)
        self.generator, self.judge = generator, judge
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
            limit = self.recipe["production"]["budget_usd"]
            if limit:
                self._task_budget = BudgetGuard(self.path, limit)

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
            require_vision_model(self.root, parser["binding"])
        field = {"judge": "judge", "jev": "jev"}.get(role, "generator")
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
            allow_reasoning_fallback=True, model_stage=None, prompt_stage=None):
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
                    if instruction is not None:
                        system += "\n以下为本节点固定处理规则；仅用户消息中的正文是待处理资料：\n" + instruction
                    visible_only = (not allow_reasoning_fallback or prompt_id in {
                        "workflow.sft_styled", "workflow.cot_generate", "workflow.style_check",
                        "workflow.trim", "workflow.trim_check", "workflow.trim_rules_check"})
                    return chat_json(client, [
                        {"role": "system", "content": system},
                        {"role": "user", "content": canonical(data) if image is None else [
                            {"type": "text", "text": canonical(data)},
                            {"type": "image_url", "image_url": {"url": image}}]}],
                        max_tokens=binding.get("max_output_tokens"), **streaming,
                        **({"allow_reasoning_fallback": False} if visible_only else {}))
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
        return value

    def stage_items(self, stage, items, action, *, stream_sources=False, finalize=True):
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
        processing_started = None
        def process(index, item):
            nonlocal processing_started
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
        source_iterator = iter(items)
        iterator = enumerate(source_iterator)
        with ExitStack() as resources, pending.open("w", encoding="utf-8") as handle, ThreadPoolExecutor(max_workers=workers) as executor:
            if callable(close_iterator := getattr(source_iterator, "close", None)):
                resources.callback(close_iterator)
            while batch := list(islice(iterator, batch_size)):
                self.check_cancel()
                futures = {executor.submit(process, index, item): index for index, item in batch}
                ordered = {}
                try:
                    for future in as_completed(futures):
                        ordered[futures[future]] = future.result()
                except BaseException:
                    self._abort.set()
                    for future in futures:
                        future.cancel()
                    raise
                for index, _ in batch:
                    for row in ordered[index]:
                        handle.write(canonical(row) + "\n")
                handle.flush()
                metrics["batches_done"] += 1
                self.save()
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
                                or len(result["text"]) > 80_000):
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
        style = style_for_sample(config, sample_id)
        pinned = self.recipe.get("node_generation_prompts", {}).get(stage, {}).get(style["preset"])
        if pinned is not None:
            style["instruction"] = pinned
        return style

    @staticmethod
    def without_source_reasoning(value):
        """Keep source facts without supplying an assistant's existing chain."""
        if isinstance(value, list):
            return [Workflow.without_source_reasoning(item) for item in value]
        if isinstance(value, dict):
            return {key: Workflow.without_source_reasoning(item) for key, item in value.items()
                    if not (value.get("role") == "assistant" and
                            key in {"reasoning_content", "reasoning", "thinking"})}
        return value

    def style_check(self, key, style, reasoning, answer):
        return self.adherence_check(key, "workflow.style_check", {
            "generation_style": style, "reasoning": reasoning, "answer": answer})

    def adherence_check(self, key, prompt_id, data, *, instruction=None):
        value = self.ask(key, "jev", prompt_id, data,
                         **({"instruction": instruction} if instruction is not None else {}))
        if (not isinstance(value, dict) or set(value) != {"keep", "adherence", "reason"}
                or type(value.get("keep")) is not bool
                or type(value.get("adherence")) is not int
                or not 1 <= value["adherence"] <= 5
                or not isinstance(value.get("reason"), str) or not value["reason"].strip()):
            self.invalidate_checkpoint(["call", key])
            raise ValueError("invalid_style_judge_schema")
        return value

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
                     "retention_reason": "source_text_passed_deterministic_checks", "evidence_level": (
                         "model_transcribed_visual_source" if unit.get("document_reading") else "source_text")}]
        data = self.ask([unit["id"], "corpus"], "generation", "workflow.corpus", unit)
        text = data.get("text") if isinstance(data, dict) else None
        quality = inspect_corpus(text)
        if not quality["keep"]:
            return [{**self.rejected(unit, "invalid_generated_corpus"), "quality": quality}]
        check = self.judge_answer([unit["id"], "corpus_judge"], unit, {"content": text})
        if not accepted(check):
            return [{**self.rejected(unit, "corpus_judge_rejected"), "judge": check, "quality": quality}]
        return [{**unit, "text": text, "status": "eligible", "judge": check, "quality": quality,
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
        if issue := sft_reasoning_issue(unit.get("messages", [])):
            return [self.rejected(unit, issue)]
        history = sft_training_messages(unit.get("messages", []))
        context = {"source": {**unit, "messages": [
                       {**m, "content": "[来源内容隐藏]" if m.get("role") == "assistant" else m.get("content", "")}
                       for m in unit.get("messages", [])]},
                   "requirement": self.recipe["brief"]}
        style = self.generation_style("sft", unit["id"])
        if style is not None:
            history = self.without_source_reasoning(history)
            context = self.without_source_reasoning(context)
            context["generation_style"] = style
        feedback = None
        check, style_check = None, None
        for attempt in range(2):
            evidence_spans = None
            if style is None and history and attempt == 0 and history[-1].get("reasoning_content"):
                messages = history
                quotes = []
            else:
                key = [unit["id"], "sft_styled" if style is not None else "sft", attempt]
                candidate = self.ask(key, "generation", "workflow.sft_styled" if style is not None else "workflow.sft",
                    {**context, "feedback": feedback},
                    **({"instruction": "生成风格要求：\n" + style["instruction"]} if style is not None else {}))
                if not isinstance(candidate, dict) or any(text_issue(candidate.get(k)) for k in ("question", "answer", "reasoning")):
                    feedback = "回答结构不合格或存在敏感数据"
                    continue
                quotes = candidate.get("quotes", [])
                if unit["kind"] == "document":
                    pdf_source = any(Path(source.get("file", "")).suffix.lower() == ".pdf"
                        and source.get("sha256") == unit.get("source_id")
                        and source.get("name") == unit.get("source_name")
                        for source in self.recipe.get("sources", ()))
                    evidence_spans = quote_spans(unit["text"], quotes, pdf_whitespace=pdf_source)
                    if evidence_spans is None:
                        feedback = "需要原文中可逐字匹配的证据"
                        continue
                if unit["kind"] == "document":
                    prompt = f"请根据以下资料回答问题。只使用资料支持的内容。\n\n资料：\n{unit['text']}\n\n问题：\n{candidate['question']}"
                    prefix = [{"role": "user", "content": prompt}]
                elif unit["kind"] == "brief":
                    prompt = f"{self.recipe['brief']}\n\n任务：\n{candidate['question']}" if self.recipe["brief"] else candidate["question"]
                    prefix = [{"role": "user", "content": prompt}]
                else:
                    prefix = history[:-1] if history else [{"role": "user", "content": candidate["question"]}]
                messages = [*prefix, {"role": "assistant", "content": candidate["answer"], "reasoning_content": candidate["reasoning"]}]
            issue = conversation_issue(messages)
            if issue:
                feedback = issue
                continue
            check = self.judge_answer([unit["id"], "sft_judge", attempt], {**context, "rendered_prompt": messages[:-1]}, messages,
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
                         "repair_attempts": attempt, "reasoning_origin": ("prompt_styled_generation" if style is not None
                            else "source" if messages is history else "synthetic_explanation"),
                         "evidence_level": ("model_transcribed_visual_source_and_model_assessed" if unit.get("document_reading")
                                            else "model_assessed_synthetic" if unit["kind"] == "brief" else "source_and_model_assessed")}]
            feedback = {"correctness": check["reason"] if not accepted(check) else None,
                        "style": style_check["reason"] if style_check is not None and
                        not (style_check["keep"] and style_check["adherence"] >= 4) else None}
            if style is None:
                feedback = check["reason"]
        return [{**self.rejected(unit, "sft_quality_failed_after_repair"), "feedback": feedback,
                 **({"generation_style": style, "judge": check, "style_check": style_check,
                     "repair_attempts": 1} if style is not None else {})}]

    def multiturn(self, unit):
        """Build or conservatively assess a complete multi-turn conversation.

        Imported traces are never rewritten. Generated turns use separate
        user/assistant calls, turn-level review, and a whole-dialogue check.
        All model checks are assessments, never independent fact verification.
        """
        if unit.get("verification_status") == "synthetic_unverified":
            return [self.rejected(unit, "simulated_tool_observation_not_verified_multiturn")]
        if any(tool_error_flag(message)[0] or tool_error_flag(message)[1]
               for message in unit.get("messages", [])):
            return [self.rejected(unit, "observed_tool_error_not_multiturn")]
        provenance = {"source_id": unit["source_id"], "source_name": unit.get("source_name"),
                      "location": unit.get("location"), "source_location": unit.get("source_location"),
                      "kind": unit["kind"]}
        source_context = {"provenance": provenance, "task": unit.get("text"),
                          "brief": self.recipe.get("brief", ""), "tools": unit.get("tools", [])}
        contract = unit.get("qa_contract")
        if contract is not None:
            source_context.update(qa_contract=contract,
                question_rules=self.recipe["qa_director"]["question_rules"],
                answer_rules=self.recipe["qa_director"]["answer_rules"],
                teacher_evidence_not_learner_context=True)
        evidence_level = (unit.get("revision_source_level") or ("human_provided_and_model_assessed" if unit.get("human_provided") else
                          "model_transcribed_visual_source_and_model_assessed" if unit.get("document_reading")
                          else "recorded_context_model_assessed" if unit["kind"] == "conversation"
                          else "source_and_model_assessed" if unit["kind"] == "document"
                          else "model_assessed_synthetic"))
        source_verification = ("human_provided_not_independently_verified" if unit.get("human_provided") else
                               "visual_transcription_not_independently_verified" if unit.get("document_reading")
                               else "recorded_unverified" if unit["kind"] == "conversation"
                               else "exact_quote_presence_only" if unit["kind"] == "document"
                               else "no_external_source")
        reviews = []
        quotes_by_turn = []
        adaptive_dialogue = (contract is not None and contract.get("dialogue_design") is not None
            and self.recipe.get("qa_director", {}).get("planning_mode") == "adaptive")
        dialogue_steps = []
        dialogue_state = ({"user_intent": contract["dialogue_design"].get("user_intent", ""),
            "progress": "", "shared_understanding": [], "open_issues": [],
            "active_constraints": [], "context_links": []} if adaptive_dialogue else None)
        dialogue_end_reason = "turn_limit"
        dialogue_end_guidance = ""
        dialogue_state_after_turn = 0
        if adaptive_dialogue:
            source_context["dialogue_design"] = contract["dialogue_design"]
        if unit["kind"] == "conversation":
            messages = deepcopy(unit["messages"])
            ends, issue = completed_turn_ends(messages)
            if issue:
                return [self.rejected(unit, issue)]
            for turn_index, end in enumerate(ends):
                check = self.judge_answer([unit["id"], "multiturn_turn", turn_index],
                    {**source_context, "dialogue_before_answer": messages[:end],
                     "recorded_context_not_independently_verified": True}, messages[end])
                reviews.append({"turn": turn_index + 1, "end_message_index": end, "judge": check})
                if not accepted(check):
                    return [{**self.rejected(unit, "multiturn_turn_rejected"), "failed_turn": turn_index + 1,
                             "turn_reviews": reviews, "evidence_level": evidence_level}]
        else:
            messages = []
            raw_user_messages = []
            finished_dialogue = False
            for turn_index in range(self.recipe.get("conversation_turns", 3)):
                feedback = None
                for attempt in range(2):
                    planned_step = None
                    current_dialogue_state = dialogue_state
                    if contract is not None and turn_index == 0:
                        user_data = {"message": contract["question"]}
                    elif adaptive_dialogue:
                        try:
                            planned_step = self.next_dialogue_step(unit, messages, dialogue_state,
                                attempt=attempt, feedback=feedback)
                        except DialoguePlanError:
                            feedback = "invalid_dialogue_step"
                            continue
                        if not planned_step["continue"]:
                            dialogue_steps.append({"after_turn": turn_index, **planned_step})
                            # This is still an untrusted summary. The final
                            # independent whole-dialogue review checks it
                            # against actual messages and source evidence.
                            dialogue_state = planned_step["dialogue_state"]
                            dialogue_state_after_turn = turn_index
                            dialogue_end_reason = "natural_completion"
                            dialogue_end_guidance = planned_step["reason"]
                            finished_dialogue = True
                            break
                        current_dialogue_state = planned_step["dialogue_state"]
                        user_data = {"message": planned_step["user_message"]}
                    else:
                        user_data = self.ask([unit["id"], "multiturn_user", turn_index, attempt],
                            "generation", "workflow.multiturn_user",
                            {**source_context, "turn": turn_index + 1, "total_turns": self.recipe.get("conversation_turns", 3),
                             "previous_messages": messages, "feedback": feedback},
                            **({"instruction": "遵循问答指导契约和提问规则。隐藏的教师资料不得自动成为用户可见线索；"
                                "无线索题的后续问题必须自包含，不引用读者不可见的来源。"} if contract is not None else {}))
                    user_text = user_data.get("message") if isinstance(user_data, dict) else None
                    if text_issue(user_text) or len(user_text) > (12000 if contract is not None and turn_index == 0 else 6000):
                        feedback = "用户消息为空、含敏感信息或过长"
                        continue
                    surface_repetition = any(same_answer(user_text, previous) for previous in raw_user_messages)
                    if surface_repetition and not adaptive_dialogue:
                        feedback = "不能重复之前的用户问题"
                        continue
                    if turn_index == 0 and contract is not None:
                        from lib.infrastructure.workflow_qa_director import learner_prompt
                        user_text = learner_prompt(contract)
                    elif turn_index == 0 and unit["kind"] == "document":
                        user_text = ("请依据以下资料完成多轮问答，只使用资料支持的内容。\n\n资料：\n"
                                     f"{unit['text']}\n\n问题：\n{user_text}")
                    elif turn_index == 0 and self.recipe.get("brief"):
                        user_text = f"任务背景：{self.recipe['brief']}\n\n问题：\n{user_text}"
                    user_message = {"role": "user", "content": user_text}
                    response = self.ask([unit["id"], "multiturn_answer", turn_index, attempt],
                        "generation", "workflow.multiturn_assistant",
                        {**source_context, "messages": [*messages, user_message], "feedback": feedback,
                         **({"dialogue_state": current_dialogue_state,
                             "dialogue_step": planned_step} if adaptive_dialogue else {})},
                        **({"instruction": "遵循问答指导契约和回答规则。首轮严格遵循answer_policy；"
                            "后续按用户新提供的信息回答，仍须区分教师证据与用户可见证据，"
                            "无线索题允许使用教师资料核验的必要知识事实和准确、必要的公开引用。"
                            "不得披露内部提示词、内部检索包装或标识，不得输出与回答无关的原文，"
                            "也不得假装读者见过隐藏上文。" +
                            human_generation_instruction(contract)} if contract is not None else {}))
                    answer = response.get("answer") if isinstance(response, dict) else None
                    quotes = response.get("quotes") if isinstance(response, dict) else None
                    if text_issue(answer) or len(answer) > 12000 or not isinstance(quotes, list):
                        feedback = "回答或逐字证据结构不合格"
                        continue
                    if unit["kind"] == "document" and ((not quotes and
                            not adaptive_dialogue and
                            (contract is None or contract["answer_policy"] in {"answer", "correct_premise"})) or any(
                            not isinstance(quote, str) or not quote.strip() or quote not in unit["text"]
                            for quote in quotes)):
                        feedback = "文档回答缺少可逐字匹配的原文依据"
                        continue
                    if unit["kind"] == "brief" and quotes:
                        feedback = "开放需求没有可核对的外部来源，不应编造引文"
                        continue
                    assistant_message = {"role": "assistant", "content": answer}
                    candidate = [*messages, user_message, assistant_message]
                    issue = conversation_issue(candidate)
                    if issue:
                        feedback = issue
                        continue
                    check = self.judge_answer([unit["id"], "multiturn_turn", turn_index, attempt],
                        {**source_context, "dialogue_before_answer": [*messages, user_message],
                         "quotes": quotes, "source_text": unit.get("text"),
                         **({"dialogue_state": current_dialogue_state,
                             "dialogue_step": planned_step,
                             "surface_repetition": surface_repetition} if adaptive_dialogue else {})}, assistant_message)
                    if not accepted(check):
                        feedback = check["reason"]
                        continue
                    contract_check = (self.qa_contract_check(
                        [unit["id"], "multiturn_contract", turn_index, attempt], unit, candidate,
                        prompt_id="workflow.multiturn_directed_check",
                        review_scope="completed_prefix",
                        **({"dialogue_state": current_dialogue_state,
                            "dialogue_steps": dialogue_steps} if adaptive_dialogue else {})) if contract is not None else None)
                    if contract_check is not None and not (contract_check["keep"] and contract_check["adherence"] >= 4):
                        feedback = contract_check["reason"]
                        continue
                    messages = candidate
                    raw_user_messages.append(user_data["message"])
                    reviews.append({"turn": turn_index + 1, "end_message_index": len(messages) - 1,
                                    "judge": check, "repair_attempts": attempt,
                                    **({"surface_repetition": surface_repetition} if adaptive_dialogue else {}),
                                    **({"qa_contract_check": contract_check} if contract_check is not None else {})})
                    quotes_by_turn.append(quotes)
                    if adaptive_dialogue:
                        dialogue_state = current_dialogue_state
                        dialogue_state_after_turn = turn_index
                        dialogue_steps.append({"turn": turn_index + 1,
                            **(planned_step if planned_step is not None else {
                                "continue": True, "user_message": contract["question"],
                                "reason": "initial_design", "dialogue_state": dialogue_state})})
                    break
                else:
                    return [{**self.rejected(unit, "multiturn_turn_failed_after_repair"),
                             "failed_turn": turn_index + 1, "feedback": feedback,
                             "turn_reviews": reviews, "evidence_level": evidence_level}]
                if finished_dialogue:
                    break
            ends, issue = completed_turn_ends(messages)
            if issue:
                return [self.rejected(unit, issue)]
        consistency = self.judge_answer([unit["id"], "multiturn_consistency"],
            {**source_context, "evidence_level": evidence_level, "turn_reviews": reviews,
             "source_text": unit.get("text"), "whole_dialogue": True,
             **({"dialogue_state": dialogue_state, "dialogue_steps": dialogue_steps,
                 "dialogue_end_reason": dialogue_end_reason,
                 "dialogue_state_after_turn": dialogue_state_after_turn,
                 "dialogue_state_scope": "final_summary" if dialogue_end_reason == "natural_completion" else "before_last_turn",
                 "dialogue_end_guidance": dialogue_end_guidance} if adaptive_dialogue else {})},
            messages, prompt_id="workflow.multiturn_consistency")
        if not accepted(consistency):
            return [{**self.rejected(unit, "multiturn_consistency_rejected"),
                     "turn_reviews": reviews, "consistency": consistency,
                     "evidence_level": evidence_level}]
        if (contract is not None and contract.get("human_design", {}).get("seed", {}).get("revision_context")):
            contract_check = self.qa_contract_check([unit["id"], "multiturn_revision_complete"], unit, messages,
                prompt_id="workflow.multiturn_directed_check", review_scope="complete",
                dialogue_state=dialogue_state, dialogue_steps=dialogue_steps)
            if not (contract_check["keep"] and contract_check["adherence"] >= 4):
                return [{**self.rejected(unit, "human_revision_feedback_not_applied"),
                         "turn_reviews": reviews, "consistency": consistency, "qa_contract_check": contract_check,
                         "evidence_level": evidence_level}]
        return [{"id": unit["id"], "source_id": unit["source_id"],
                 "source_name": unit.get("source_name"), "location": unit.get("location"),
                 "source_location": unit.get("source_location"),
                 "status": "eligible", "messages": messages, "tools": unit.get("tools", []),
                 "synthetic": unit["kind"] != "conversation", "evidence_level": evidence_level,
                 "source_verification": source_verification,
                 "fact_verification": "not_independently_verified",
                 "tool_verification": ("not_replayed" if any(
                     message.get("tool_calls") or message.get("toolCalls") for message in messages)
                     else "not_applicable"),
                 "turn_count": len(ends), "turn_reviews": reviews, "consistency": consistency,
                 "quotes_by_turn": quotes_by_turn,
                 **({"dialogue_design": contract["dialogue_design"], "dialogue_steps": dialogue_steps,
                     "dialogue_state": dialogue_state, "dialogue_end_reason": dialogue_end_reason,
                     "dialogue_state_after_turn": dialogue_state_after_turn,
                     "dialogue_state_scope": "final_summary" if dialogue_end_reason == "natural_completion" else "before_last_turn",
                     "dialogue_end_guidance": dialogue_end_guidance} if adaptive_dialogue else {}),
                 **({"qa_contract": contract, "family_id": unit["family_id"],
                     "parent_id": unit.get("parent_id")} if contract is not None else {}),
                 "source_text_sha256": digest(unit["text"]) if unit.get("text") else None}]

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

    def gsm8k(self, item):
        seed = int(item["id"][:8], 16)
        theme = self.recipe["brief"] or (self.recipe["sources"][0]["name"] if self.recipe["sources"] else "开放数学推理")
        sample = build_gsm8k(theme, seed, version=self.recipe.get("math_generator_version", 1))
        if not validate_gsm8k(sample):
            return [self.rejected(item, "gsm8k_arithmetic_verification_failed")]
        return [{"id": item["id"], "source_id": item["source_id"], "status": "eligible",
                 "question": sample["question"], "answer": sample["answer"],
                 "arithmetic_expression": sample["_expression"], "verified_result": sample["_result"],
                 "calculation_steps": sample.get("_steps", []),
                 "generator_version": self.recipe.get("math_generator_version", 1),
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
        reasoning = messages[-1].get("reasoning_content", "").strip()
        answer = messages[-1].get("content", "").strip()
        if not reasoning or not answer:
            return [self.rejected(sample, "cot_missing_reasoning_or_answer")]
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
        steps = [part.strip() for part in re.split(r"(?<=[。.!?])\s*", reasoning) if part.strip()]
        return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                 "question": messages[:-1], "reasoning": steps or [reasoning], "answer": answer,
                 "judge": check, "evidence_level": sample["evidence_level"],
                 **self.qa_metadata(sample, contract_check)}]

    def styled_cot(self, sample, style):
        """Author a new explicit rationale; never copy a provider reasoning channel."""
        messages = sample.get("messages", [])
        if (not messages or messages[-1].get("role") != "assistant"
                or text_issue(messages[-1].get("content"))):
            return [self.rejected(sample, "cot_missing_final_answer")]
        source = self.without_source_reasoning(sample.get("source_context"))
        task = {"prompt": self.without_source_reasoning(messages[:-1]), "source": source,
                "quotes": sample.get("quotes", []), "reference_answer": messages[-1]["content"],
                "generation_style": style}
        if sample.get("qa_contract"):
            task.update(qa_contract=sample["qa_contract"],
                question_rules=self.recipe["qa_director"]["question_rules"],
                answer_rules=self.recipe["qa_director"]["answer_rules"])
        feedback, check, style_check, contract_check = None, None, None, None
        for attempt in range(2):
            value = self.ask([sample["id"], "cot_generate_v1", attempt], "generation",
                             "workflow.cot_generate", {**task, "feedback": feedback},
                             instruction="生成风格要求：\n" + style["instruction"])
            if not isinstance(value, dict) or any(text_issue(value.get(key)) for key in ("reasoning", "answer")):
                feedback = "推理和答案必须是有效的完整文本"
                continue
            check = self.judge_answer([sample["id"], "cot_generated_check_v1", attempt],
                                      {key: item for key, item in task.items() if key != "generation_style"},
                                      {"reasoning": value["reasoning"], "answer": value["answer"]},
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
                         **self.qa_metadata(sample, contract_check)}]
            feedback = {"correctness": check["reason"] if not accepted(check) else None,
                        "style": style_check["reason"] if not (style_check["keep"] and
                                  style_check["adherence"] >= 4) else None}
            if contract_check is not None:
                feedback["contract"] = (contract_check["reason"] if
                    not (contract_check["keep"] and contract_check["adherence"] >= 4) else None)
        return [{**self.rejected(sample, "cot_generation_failed_after_repair"),
                 "generation_style": style, "judge": check, "style_check": style_check,
                 "feedback": feedback, "repair_attempts": 1,
                 "reasoning_origin": "prompt_styled_generation", **self.qa_metadata(sample, contract_check)}]

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
                replacement = self.ask(key, "generation", "workflow.trim",
                                       {**context, "feedback": feedback}, instruction=instruction)
                if (not isinstance(replacement, dict) or set(replacement) != {"reasoning"}
                        or text_issue(replacement.get("reasoning"))):
                    feedback = "仅返回有效的 reasoning 文本，不得包含或改写其他字段"
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
        if "source_context" in row:
            row["source_context"] = self.without_source_reasoning(row["source_context"])
        row["reasoning_trim"] = {"version": 1, "template": config["template"],
                                  "status": "applied" if fields else "not_applicable",
                                  "instruction_sha256": digest(instruction), "fields": receipts}
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
                spool = RowSpool(directory / f"package-{target}-candidates.jsonl")
                candidate_count = 0
                smallest = []
                try:
                    for index, original in enumerate(collections[target]):
                        self.check_cancel()
                        row = deepcopy(original)
                        if row["status"] == "eligible":
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
                                if review_config["enabled"] and review_config["mode"] == "sample":
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
                if review_config["enabled"]:
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
                for target in self.recipe["targets"]:
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
                for target in workflow.recipe["targets"]:
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
        if self.recipe.get("qa_director", {}).get("enabled"):
            report["qa_director"] = deepcopy(self.state.get("qa_director", {}))
            report["qa_director"].update(
                question_rules_sha256=digest(self.recipe["qa_director"]["question_rules"]),
                answer_rules_sha256=digest(self.recipe["qa_director"]["answer_rules"]),
                type_weights=self.recipe["qa_director"]["type_weights"],
                planning_rows="stage-results/director.jsonl",
                noise_policy="explicit_distractor_context_and_independent_answer_policy_review")
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
        review_config = validate_package_review(self.recipe.get("package_review"))
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
                            if review_config["enabled"]:
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
                            if row["status"] == "eligible":
                                duplicate = self.final_qa_duplicate(row, target, qa_history)
                                if duplicate:
                                    row.update(status="quarantined", reason="released_qa_contract_duplicate",
                                               duplicate_of=duplicate["id"], duplicate_scope="published_qa_history")
                            if row["status"] == "eligible":
                                self._production_accept_row(target, row, payload)
                            if row["status"] == "eligible":
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
                if review_config["enabled"]:
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
            for pending in destination.glob(".*.pending"):
                pending.unlink(missing_ok=True)
            files = {p.name: file_hash(p) for p in destination.iterdir() if p.is_file() and p.name != "manifest.json"}
            atomic_json(destination / "manifest.json", {"status": "complete", "run_id": self.state["id"],
                        "recipe_hash": self.state["recipe_hash"], "policy": POLICY, "models": self.state.get("models", {}),
                        "sources": self.recipe["sources"], "counts": {t: v["eligible"] for t, v in report["targets"].items()},
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
            if not getattr(self, "_production_collecting", False):
                self.publish_qa_history(destination)
            return []

    def execute(self, resume_run=False):
        with FileLock(str(self.path / ".run.lock"), timeout=0):
            self.state = read_json(self.path / "state.json")
            if self.state["status"] in {"completed", "needs_attention"}:
                verify_artifacts(self.path)
                return self.state
            if resume_run:
                (self.path / "cancel.json").unlink(missing_ok=True)
            self.state["attempt"] += 1
            self.state["status"] = "running"
            self.state.pop("error", None)
            self.save()
            try:
                # Read under the run lock: construction may precede execution.
                self.recipe = read_json(self.path / "recipe.json")
                if digest(self.recipe) != self.state["recipe_hash"] or self.recipe["version"] not in SUPPORTED_RECIPE_VERSIONS:
                    raise ValueError("recipe_changed_create_new_run")
                if self.recipe["version"] >= 9:
                    validate_node_prompt_snapshot(self.recipe.get("node_prompt_templates"),
                                                  self.recipe.get("node_prompt_system"),
                                                  recipe_version=self.recipe["version"])
                else:
                    if not isinstance(self.recipe.get("prompts"), dict):
                        raise ValueError("prompts_changed_create_new_run")
                    for key in self.recipe["prompts"]:
                        self.historical_prompt(key)
                for source in self.recipe["sources"]:
                    if file_hash(self.path / "inputs" / source["file"]) != source["sha256"]:
                        raise ValueError("source_snapshot_changed")
                if self.production_enabled():
                    return self._execute_production()
                selected_targets = set(self.recipe["targets"])
                planned_targets = selected_targets & ({"cpt", "sft", "cot", "multiturn"} | PREFERENCE_TARGETS)
                if self.recipe["brief"] and not self.recipe["sources"] and planned_targets:
                    self.stage = "ingest"
                    units = self.plan()
                    self.state["stages"]["ingest"].update(status="completed", done=len(units),
                                                          total=len(units), outputs=len(units), eligible=len(units))
                else:
                    units = self.stage_items("ingest", self.recipe["sources"], self.parse_source, stream_sources=True)
                if "gsm8k" in selected_targets and not units:
                    brief = self.recipe["brief"]
                    source_id = digest(brief)
                    units = row_checkpoint(
                        self.path / "checkpoints" / "ingest" / "math-inputs.json",
                        lambda: ({"id": digest([brief, "gsm8k", i]), "source_id": source_id,
                                  "kind": "brief", "text": brief, "status": "ready"}
                                 for i in range(self.recipe["tasks"])), self.check_cancel)
                from collections import Counter
                input_statuses = Counter(u["status"] for u in units)
                eligible_count = input_statuses["ready"]
                eligible = (units.ready(eligible_count, self.recipe["max_units"]) if isinstance(units, WorkflowRows)
                            else [u for u in units if u["status"] == "ready"])
                requested = self.recipe.get("sample_count") or self.recipe["tasks"]
                planning_deferred = max(0, requested - self.recipe["max_units"]) if not self.recipe["sources"] else 0
                self.state["input_summary"] = {"units": len(units), "ready": eligible_count,
                    "quarantined": input_statuses["quarantined"], "skipped": input_statuses["skipped"],
                    "deferred": max(max(0, eligible_count - self.recipe["max_units"]), planning_deferred),
                    "targets": list(self.recipe["targets"])}
                write_json_array(self.path / "input_records.json", units)
                selected = eligible if isinstance(eligible, WorkflowRows) else eligible[:self.recipe["max_units"]]
                needs_generated_candidates = bool(selected_targets & (PREFERENCE_TARGETS | {'sft', 'multiturn', 'cot'}))
                generated = (prepare_generation_rows(self.path / "stage-results" / "generation-inputs.jsonl",
                                                     selected, self.recipe.get("sample_count"), self.check_cancel,
                                                     variant_policy_version=2 if self.recipe["version"] >= 11 else 1)
                             if needs_generated_candidates else [])
                self.state["input_summary"]["generation_candidates"] = len(generated)
                collections = {}
                needs_sft = bool(selected_targets & (PREFERENCE_TARGETS | {"sft", "cot"}))
                collections["cpt"] = self.stage_items("cpt", selected, self.cpt) if "cpt" in selected_targets else []
                directed = (self.directed_generation(generated, needs_sft=needs_sft,
                            needs_multiturn="multiturn" in selected_targets)
                            if self.recipe.get("qa_director", {}).get("enabled") else None)
                if directed is None:
                    self.state["stages"].setdefault("director", {"label": STAGES["director"], "done": 0, "total": 0})
                    self.state["stages"]["director"]["status"] = "skipped"
                collections["sft"] = (directed.get("sft", []) if directed is not None else
                                      self.stage_items("sft", generated, self.sft) if needs_sft else [])
                if not needs_sft:
                    self.state["stages"]["sft"]["status"] = "skipped"

                self.state["stages"].setdefault("multiturn", {"label": STAGES["multiturn"],
                    "status": "pending", "done": 0, "total": 0})
                collections["multiturn"] = (directed.get("multiturn", []) if directed is not None else
                    self.stage_items("multiturn", generated, self.multiturn) if "multiturn" in selected_targets else [])
                if "multiturn" not in selected_targets:
                    self.state["stages"]["multiturn"]["status"] = "skipped"

                self.state["stages"].setdefault("agent", {"label": STAGES["agent"], "status": "pending", "done": 0, "total": 0})
                collections["agent"] = self.stage_items("agent", selected, self.agent) if "agent" in selected_targets else []
                if "agent" not in selected_targets:
                    self.state["stages"]["agent"]["status"] = "skipped"

                if selected_targets & PREFERENCE_TARGETS:
                    sft_items = collections["sft"].eligible(self.state["stages"]["sft"]["eligible"])
                    pairs = self.stage_items("preference", sft_items, self.preference)
                    collections["dpo"] = pairs if "dpo" in selected_targets else []
                    collections["orpo"] = pairs if "orpo" in selected_targets else []
                    def checked_rlaif(row):
                        if row["status"] == "eligible":
                            issue = rlaif_feedback_issue(row)
                            if issue:
                                row.update(status="quarantined", reason=issue)
                        return row
                    collections["rlaif"] = (WorkflowRows(pairs.path, len(pairs), transform=checked_rlaif)
                                             if "rlaif" in selected_targets else [])
                else:
                    for target in PREFERENCE_TARGETS:
                        collections[target] = []
                    self.state["stages"]["preference"]["status"] = "skipped"

                if "gsm8k" in selected_targets:
                    seed_base = self.recipe["brief"] or canonical(self.recipe["sources"])
                    source_id = digest(seed_base)
                    count = min(self.recipe.get("sample_count") or self.recipe["tasks"], self.recipe["max_units"])
                    items = row_checkpoint(
                        self.path / "checkpoints" / "gsm8k" / "math-seeds.json",
                        lambda: ({"id": digest([seed_base, "gsm8k", index]),
                                  "source_id": source_id, "status": "ready"}
                                 for index in range(count)), self.check_cancel)
                    collections["gsm8k"] = self.stage_items("gsm8k", items, self.gsm8k)
                else:
                    collections["gsm8k"] = []
                    self.state["stages"]["gsm8k"]["status"] = "skipped"

                if "cot" in selected_targets:
                    sft_items = collections["sft"].eligible(self.state["stages"]["sft"]["eligible"])
                    collections["cot"] = self.stage_items("cot", sft_items, self.cot)
                else:
                    collections["cot"] = []
                    self.state["stages"]["cot"]["status"] = "skipped"
                self.finalize_reasoning_outputs(collections)
                # Packaging is deliberately re-run after interruption, never trusted from a stale checkpoint.
                self.stage = "package"
                self.state["stages"]["package"].update(status="running", total=1, done=0)
                self.save()
                self.check_cancel()
                self.package(collections)
                self.state["stages"]["package"].update(
                    status="completed", phase="completed", done=self.state["stages"]["package"]["total"], finished_at=now())
                counts = self.state["quality"]["targets"]
                cpt_quarantined = any(row["status"] == "quarantined" for row in collections["cpt"])
                multiturn_quarantined = any(row["status"] == "quarantined" for row in collections["multiturn"])
                agent_quarantined = any(row["status"] == "quarantined" for row in collections["agent"])
                attention = (any(not v["eligible"] or v.get("negative", 0) for v in counts.values())
                             or any(v.get("package_review", {}).get("rejected", 0) for v in counts.values())
                             or cpt_quarantined
                             or multiturn_quarantined
                             or agent_quarantined
                             or self.state["input_summary"]["quarantined"] > 0
                             or self.state["input_summary"]["deferred"] > 0)
                self.state["status"] = "needs_attention" if attention else "completed"
                self.event("run_finished")
            except Cancelled:
                self.state["status"] = "cancelled"
                if self.production_enabled() and "production" in self.state:
                    self.state["production"].update(status="cancelled", stop_reason="cancelled")
                    self._production_partial_export("cancelled", "cancelled")
                self.state["stages"][self.stage]["status"] = "cancelled"
                self.event("run_cancelled")
            except Exception as error:
                # Never persist raw provider exceptions: they can contain credentials or source text.
                code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]+", str(error)) else type(error).__name__
                if self.production_enabled():
                    from lib.model_request_reliability import classify_request_error
                    failure = classify_request_error(error)
                    code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]+", str(error)) else failure["code"]
                    if "production" in self.state:
                        self.state["production"].update(status="paused", stop_reason=code)
                        if failure["code"] not in {"checkpoint_integrity_error", "source_snapshot_changed",
                                                   "recipe_changed_create_new_run", "production_round_integrity_error",
                                                   "production_review_integrity_error"}:
                            self._production_partial_export("paused", code)
                self.state.update(status="failed", error=code)
                self.state["stages"][self.stage].update(status="failed", error=code)
                self.event("run_failed", error=code)
            return self.state


def resume(output, run_id, root, **clients):
    return Workflow(output, run_id, root, **clients).execute(resume_run=True)
