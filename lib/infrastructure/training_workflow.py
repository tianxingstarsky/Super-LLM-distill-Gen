"""Durable automatic training-data workflows, independent of browser sessions.

Each run owns immutable input copies, a pinned recipe, per-item checkpoints,
stage metrics and a hash-verified training bundle. No global output is overwritten.
"""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from itertools import islice
import threading
import time

from filelock import FileLock, Timeout

from lib.application.trainer_export_service import prepare_trl_export
from lib.domain.workflow_quality import POLICY, accepted, canonical, conversation_issue, same_answer, text_issue, verdict
from lib.domain.agent_trajectory import (REPLAY_POLICY_VERSION, ReplayUnavailable,
                                         assess_recorded_trajectory, validate_tool_snapshots)
from lib.domain.corpus_quality import CorpusNearDuplicateIndex, inspect_corpus, summarize_corpus_sources
from lib.domain.math_tasks import build_gsm8k, validate_gsm8k
from lib.domain.workflow_scale import (MAX_CANDIDATES, MAX_CONCURRENCY, MAX_BATCH_SIZE,
                                      PLAN_BATCH_SIZE, generation_units, validate_node_models)
from lib.infrastructure.workflow_rows import WorkflowRows, RowSpool, write_jsonl, write_json_array
from lib.domain.multiturn import completed_turn_ends
from lib.domain.workflow_targets import (INPUT_EXTENSIONS, PREFERENCE_TARGETS, STAGES, TARGETS,
                                         rlaif_feedback_issue, rlaif_reward_model_record, training_record)
from lib.infrastructure.corpus_reference import exact_release_match, released_corpus_index, snapshot_released_corpus
from lib.infrastructure.evaluation_reference import load_evaluation_index, snapshot_evaluation_sources
from lib.infrastructure.agent_docker_replay import (DockerLedgerReplay, IMAGE_ENV, RUNNER_SHA256,
                                                    validate_sandbox_image)
from lib.doc2corpus import SUPPORTED_EXTS, chunk_text, import_text
from lib.io_utils import atomic_json
from lib.llm_client import chat_json, load_backend
from lib.prompts import get, registry, render


EXTENSIONS = INPUT_EXTENSIONS
MAX_FILE_BYTES = 50 * 1024 * 1024
RECIPE_VERSION = 5
SUPPORTED_RECIPE_VERSIONS = frozenset({2, 3, 4, 5})


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


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


def write_trainer_export(destination, target, records):
    """Convert rows individually while retaining the all-or-nothing TRL gate."""
    output = destination / f"trl_{target}.jsonl"
    pending = destination / f".trl_{target}.pending"
    total, compatible, failures, reasons = 0, 0, [], {}
    format_name = "trl_sft" if target in {"sft", "multiturn", "agent"} else "trl_preference"
    with pending.open("w", encoding="utf-8") as handle:
        for row in records:
            if row["status"] != "eligible":
                continue
            payload = {"id": row.get("id"), **(rlaif_reward_model_record(row) if target == "rlaif"
                                               else training_record(target, row))}
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
               sample_count=None, concurrency=1, batch_size=100, node_models=None):
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
    files = [Path(p).resolve(strict=True) for p in sources]
    if not files and not brief.strip():
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
    if evaluation_sources and "cpt" not in targets:
        raise ValueError("评测集参照仅用于 CPT 去污染检查")
    references = snapshot_released_corpus(Path(output)) if "cpt" in targets else []
    agent_sandbox_image = (validate_sandbox_image(os.environ.get(IMAGE_ENV))
                           if "agent" in targets else None)
    run_id = uuid.uuid4().hex
    path = run_path(output, run_id)
    (path / "inputs").mkdir(parents=True, exist_ok=False)
    evaluation_references = snapshot_evaluation_sources(
        path, evaluation_sources, evaluation_source_names) if evaluation_sources else []
    snapshots = []
    for index, source in enumerate(files):
        destination = path / "inputs" / f"{index:04d}{source.suffix.lower()}"
        data = source.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("来源在读取期间超出大小限制")
        destination.write_bytes(data)
        snapshots.append({"name": (source_names or {}).get(str(source), source.name), "file": destination.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    recipe = {"version": RECIPE_VERSION, "policy": POLICY, "sources": snapshots, "brief": brief.strip(),
              "targets": targets, "backend": backend, "model": model, "judge_backend": judge_backend,
              "judge_model": judge_model, "jev_backend": jev_backend, "jev_model": jev_model,
              "max_units": max_units, "chunk_chars": chunk_chars, "tasks": tasks,
              "conversation_turns": conversation_turns, "cpt_reference_releases": references,
              "evaluation_references": evaluation_references,
              "agent_sandbox_image": agent_sandbox_image,
              "sample_count": sample_count, "concurrency": concurrency, "batch_size": batch_size,
              "node_models": node_models,
              "prompts": prompt_versions()}
    atomic_json(path / "recipe.json", recipe)
    atomic_json(path / "state.json", {"id": run_id, "name": name[:100], "created_at": now(), "updated_at": now(),
                "status": "queued", "recipe_hash": digest(recipe), "targets": targets, "attempt": 0,
                "stages": {key: {"label": label, "status": "pending", "done": 0, "total": 0} for key, label in STAGES.items()},
                "events": [], "usage": {}})
    return run_id


class Cancelled(Exception):
    pass


class Workflow:
    def __init__(self, output, run_id, root, *, generator=None, judge=None, jev=None):
        self.path = run_path(output, run_id)
        self.root = Path(root)
        self.state = read_json(self.path / "state.json")
        self.recipe = read_json(self.path / "recipe.json")
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
        if self._abort.is_set() or (self.path / "cancel.json").exists():
            raise Cancelled()

    def checkpoint(self, key, action):
        self.check_cancel()
        destination = self.path / "checkpoints" / self.stage / f"{digest(key)}.json"
        if destination.exists():
            saved = read_json(destination)
            if digest(saved["data"]) != saved["sha256"]:
                raise ValueError("checkpoint_integrity_error")
            return saved["data"]
        value = action()
        atomic_json(destination, {"data": value, "sha256": digest(value)})
        return value

    def client(self, role):
        field = {"judge": "judge", "jev": "jev"}.get(role, "generator")
        client = getattr(self, field)
        if client is None:
            cache = getattr(self._local, "clients", None)
            if cache is None:
                cache = self._local.clients = {}
            cache_key = (self.stage, role)
            client = cache.get(cache_key)
            if client is None:
                prefix = f"{role}_" if role in {"judge", "jev"} else ""
                binding = self.recipe.get("node_models", {}).get(self.stage, {}).get(role, {})
                client, _ = load_backend(self.root, backend=binding.get("backend") or self.recipe.get(prefix + "backend"),
                                         model=binding.get("model") or self.recipe.get(prefix + "model"), role=role,
                                         allow_global_endpoint_override=not bool(binding))
                cache[cache_key] = client
        identity = {"model": getattr(client, "model", type(client).__name__),
                    "endpoint_hash": digest(str(getattr(getattr(client, "client", None), "base_url", "injected")))}
        model_key = f"{self.stage}.{role}" if self.recipe.get("node_models", {}).get(self.stage, {}).get(role) else role
        with self._lock:
            pinned = self.state.setdefault("models", {}).get(model_key)
            if pinned and pinned != identity:
                raise ValueError("model_configuration_changed_create_new_run")
            self.state["models"][model_key] = identity
            self._client_locks.setdefault(id(client), threading.Lock())
            self.save(force=False)
        return client

    def ask(self, key, role, prompt_id, data):
        def invoke():
            self.check_cancel()
            client = self.client(role)
            with self._client_locks[id(client)]:
                self.check_cancel()
                before = dict(getattr(client, "usage", {}))
                self.event("model_started", role=role)
                try:
                    return chat_json(client, [
                        {"role": "system", "content": render(get("workflow.system")) + "\n" + render(get(prompt_id))},
                        {"role": "user", "content": canonical(data)}])
                finally:
                    after = getattr(client, "usage", {})
                    with self._lock:
                        usage = self.state["usage"].setdefault(role, {})
                        for metric in ("calls", "prompt_tokens", "completion_tokens"):
                            usage[metric] = usage.get(metric, 0) + max(0, after.get(metric, 0) - before.get(metric, 0))
                        self.event("model_finished", role=role)
        return self.checkpoint(["call", key], invoke)

    def stage_items(self, stage, items, action):
        self.stage = stage
        self._abort.clear()
        metrics = self.state["stages"][stage]
        batch_size = self.recipe.get("batch_size", 100)
        workers = self.recipe.get("concurrency", 1) if stage not in {"ingest", "agent", "gsm8k"} else 1
        metrics.update(status="running", done=0, total=len(items), started_at=now(),
                       outputs=0, eligible=0, quarantined=0, cached=0,
                       batch_size=batch_size, concurrency=workers, batches_done=0,
                       batches_total=(len(items) + batch_size - 1) // batch_size)
        metrics.pop("error", None)
        self.event("stage_started")
        directory = self.path / "stage-results"
        directory.mkdir(exist_ok=True)
        destination = directory / f"{stage}.jsonl"
        pending = directory / f".{stage}.pending"
        started = time.monotonic()
        def process(index, item):
            checkpoint_key = ["item", index, digest(item)]
            if stage == "agent":
                checkpoint_key.extend((REPLAY_POLICY_VERSION, RUNNER_SHA256,
                                       self.recipe.get("agent_sandbox_image")))
            cached = (self.path / "checkpoints" / self.stage / f"{digest(checkpoint_key)}.json").exists()
            value = self.checkpoint(checkpoint_key, lambda: action(item))
            with self._lock:
                metrics["done"] += 1
                metrics["outputs"] += len(value)
                metrics["eligible"] += sum(row.get("status") in {"ready", "eligible"} for row in value)
                metrics["quarantined"] += sum(row.get("status") == "quarantined" for row in value)
                metrics["cached"] += int(cached)
                elapsed = max(0.001, time.monotonic() - started)
                metrics["rate_per_minute"] = round(metrics["done"] * 60 / elapsed, 1)
                metrics["eta_seconds"] = round((metrics["total"] - metrics["done"]) * elapsed / metrics["done"])
                self.save(force=False)
            return value
        iterator = iter(enumerate(items))
        with pending.open("w", encoding="utf-8") as handle, ThreadPoolExecutor(max_workers=workers) as executor:
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
        metrics.update(status="completed", finished_at=now())
        self.event("stage_completed", outputs=metrics["outputs"])
        return WorkflowRows(destination, metrics["outputs"])

    def rejected(self, unit, reason):
        provenance = {key: unit[key] for key in ("source_name", "location", "source_location") if key in unit}
        return {"id": unit["id"], "source_id": unit.get("source_id", unit["id"]),
                **provenance, "status": "quarantined", "reason": reason}

    def parse_source(self, source):
        path = self.path / "inputs" / source["file"]
        if file_hash(path) != source["sha256"]:
            raise ValueError("source_snapshot_changed")
        source_id = source["sha256"]
        def unit(index, **fields):
            return {"id": digest([source_id, source["file"], index]), "source_id": source_id,
                    "source_name": source["name"], "location": index, **fields}
        def documents(text, index):
            # Preserve numbers, headings and code; do not strip numeric lines as page noise.
            text = text.replace("\r\n", "\n").strip()
            issue = text_issue(text)
            if issue:
                return [unit(index, status="quarantined", reason=issue)]
            return [unit(f"{index}:chunk:{i}", source_location={"file": source["name"],
                         "record": index, "chunk": i}, status="quarantined", reason="oversized_source_block")
                    if len(chunk) > max(12000, self.recipe["chunk_chars"] * 4)
                    else unit(f"{index}:chunk:{i}", source_location={"file": source["name"],
                              "record": index, "chunk": i}, kind="document", text=chunk, status="ready")
                    for i, chunk in enumerate(chunk_text(text, self.recipe["chunk_chars"]))]
        if path.suffix in SUPPORTED_EXTS:
            try:
                content = import_text(path)
            except UnicodeError:
                return [unit("document", status="quarantined", reason="invalid_encoding")]
            return documents(content, "document")
        if path.suffix == ".jsonl":
            records = []
            for index, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    records.append((index, json.loads(line)))
                except ValueError:
                    records.append((index, None))
        else:
            raw = read_json(path)
            if (isinstance(raw, list) and raw and all(
                    isinstance(item, dict) and ("role" in item or "from" in item) for item in raw)):
                # A JSON file may itself be one complete messages array.
                records = [(1, {"messages": raw})]
            else:
                records = list(enumerate(raw if isinstance(raw, list) else [raw], 1))
        result = []
        for index, row in records:
            if not isinstance(row, dict):
                result.append(unit(index, status="quarantined", reason="invalid_json_record"))
                continue
            if isinstance(row.get("text"), str) and not (row.get("messages") or row.get("conversations")):
                result.extend(documents(row["text"], index))
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
                issue = conversation_issue(sample["messages"])
                if issue == "unresolved_tool_error" and "agent" in self.recipe["targets"]:
                    # Keep observed failed tool steps for the separate negative
                    # sidecar, but still require a structurally complete trace.
                    cleaned = [{k: v for k, v in message.items() if k != "isError"}
                               for message in sample["messages"]]
                    issue = conversation_issue(cleaned)
                message_metadata_issue = text_issue(canonical(sample["messages"]))
                if message_metadata_issue in {"potential_secret", "potential_personal_data"}:
                    issue = message_metadata_issue
                if len(canonical(sample["messages"])) > 80000:
                    issue = "context_exceeds_auto_limit"
                tools_issue = text_issue(canonical(sample.get("tools", [])))
                if tools_issue in {"potential_secret", "potential_personal_data"}:
                    issue = tools_issue
                tool_snapshots = sample.get("tool_snapshots")
                if tool_snapshots is not None:
                    try:
                        validate_tool_snapshots(tool_snapshots)
                    except ReplayUnavailable as error:
                        issue = str(error)
                    else:
                        snapshot_issue = text_issue(canonical(tool_snapshots))
                        if snapshot_issue in {"potential_secret", "potential_personal_data"}:
                            issue = snapshot_issue
                if sample.get("images"):
                    issue = "multimodal_requires_dedicated_pipeline"
                if issue:
                    result.append(unit(index, status="quarantined", reason=issue))
                    continue
                result.append(unit(index, kind="conversation", messages=sample["messages"],
                                   tools=sample.get("tools", []),
                                   tool_snapshots=tool_snapshots if "agent" in self.recipe["targets"] else None,
                                   verification_status=sample.get("verification_status"), status="ready"))
            except (ValueError, TypeError, KeyError, AttributeError):
                result.append(unit(index, status="quarantined", reason="invalid_or_incomplete_conversation"))
        return result

    def plan(self):
        count = min(self.recipe.get("sample_count") or self.recipe["tasks"], self.recipe["max_units"])
        tasks, seen = [], set()
        metrics = self.state["stages"]["ingest"]
        metrics.update(status="running", done=0, total=count, phase="planning")
        self.save()
        for offset in range(0, count, PLAN_BATCH_SIZE):
            size = min(PLAN_BATCH_SIZE, count - offset)
            key = "plan" if count <= PLAN_BATCH_SIZE else ["plan", offset]
            data = self.ask(key, "generation", "workflow.plan",
                {"brief": self.recipe["brief"], "count": size, "offset": offset,
                 "total": count, "batch": offset // PLAN_BATCH_SIZE + 1,
                 "previous_tasks": tasks[-10:],
                 "instruction": "只规划当前批；利用 batch 和 offset 覆盖不同主题与情境，避免重复之前任务。"})
            planned = data.get("tasks") if isinstance(data, dict) else None
            if (not isinstance(planned, list) or len(planned) != size or any(text_issue(t) for t in planned)
                    or len({task.strip() for task in planned}) != size or any(task.strip() in seen for task in planned)):
                (self.path / "checkpoints" / self.stage / f"{digest(['call', key])}.json").unlink(missing_ok=True)
                raise ValueError("invalid_task_plan")
            tasks.extend(planned)
            seen.update(task.strip() for task in planned)
            metrics.update(done=len(tasks), outputs=len(tasks), eligible=len(tasks))
            self.save()
        metrics.update(status="completed", finished_at=now())
        self.save()
        return [{"id": digest([self.recipe["brief"], task]), "source_id": digest(self.recipe["brief"]),
                 "source_name": "开放需求", "location": index + 1,
                 "source_location": {"brief_task": index + 1},
                 "kind": "brief", "text": task, "status": "ready", "synthetic": True}
                for index, task in enumerate(tasks)]

    def judge_answer(self, key, context, message, prompt_id="workflow.jev_score"):
        # A failed schema must not become a permanent successful call checkpoint.
        value = self.ask(key, "jev", prompt_id,
            {"context": context, "answer": message})
        try:
            return verdict(value)
        except ValueError:
            (self.path / "checkpoints" / self.stage / f"{digest(['call', key])}.json").unlink(missing_ok=True)
            raise

    def cpt(self, unit):
        if unit["kind"] == "conversation":
            return [self.rejected(unit, "conversation_not_knowledge_corpus")]
        if unit["kind"] == "document":
            quality = inspect_corpus(unit["text"])
            if not quality["keep"]:
                return [{**unit, "status": "quarantined", "reason": quality["reason"], "quality": quality}]
            return [{**unit, "status": "eligible", "text": unit["text"], "quality": quality,
                     "retention_reason": "source_text_passed_deterministic_checks", "evidence_level": "source_text"}]
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
        if unit.get("verification_status") == "synthetic_unverified":
            return [self.rejected(unit, "simulated_tool_observation_not_verified_sft")]
        if any(message.get("isError") for message in unit.get("messages", [])):
            return [self.rejected(unit, "observed_tool_error_not_sft")]
        history = deepcopy(unit.get("messages", []))
        context = {"source": {**unit, "messages": [
                       {**m, "content": "[来源内容隐藏]" if m.get("role") == "assistant" else m.get("content", "")}
                       for m in unit.get("messages", [])]},
                   "requirement": self.recipe["brief"]}
        feedback = None
        for attempt in range(2):
            if history and attempt == 0 and history[-1].get("reasoning_content"):
                messages = history
                quotes = []
            else:
                candidate = self.ask([unit["id"], "sft", attempt], "generation", "workflow.sft",
                    {**context, "feedback": feedback})
                if not isinstance(candidate, dict) or any(text_issue(candidate.get(k)) for k in ("question", "answer", "reasoning")):
                    feedback = "回答结构不合格或存在敏感数据"
                    continue
                quotes = candidate.get("quotes", [])
                if unit["kind"] == "document" and (not isinstance(quotes, list) or not quotes or
                        any(not isinstance(q, str) or not q.strip() or q not in unit["text"] for q in quotes)):
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
            check = self.judge_answer([unit["id"], "sft_judge", attempt], {**context, "rendered_prompt": messages[:-1]}, messages)
            if accepted(check):
                return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible", "messages": messages,
                         "tools": unit.get("tools", []), "quotes": quotes, "judge": check, "source_context": unit,
                         "repair_attempts": attempt, "reasoning_origin": "source" if messages is history else "synthetic_explanation",
                         "evidence_level": "model_assessed_synthetic" if unit["kind"] == "brief" else "source_and_model_assessed"}]
            feedback = check["reason"]
        return [{**self.rejected(unit, "sft_quality_failed_after_repair"), "feedback": feedback}]

    def multiturn(self, unit):
        """Build or conservatively assess a complete multi-turn conversation.

        Imported traces are never rewritten. Generated turns use separate
        user/assistant calls, turn-level review, and a whole-dialogue check.
        All model checks are assessments, never independent fact verification.
        """
        if unit.get("verification_status") == "synthetic_unverified":
            return [self.rejected(unit, "simulated_tool_observation_not_verified_multiturn")]
        if any(message.get("isError") for message in unit.get("messages", [])):
            return [self.rejected(unit, "observed_tool_error_not_multiturn")]
        provenance = {"source_id": unit["source_id"], "source_name": unit.get("source_name"),
                      "location": unit.get("location"), "source_location": unit.get("source_location"),
                      "kind": unit["kind"]}
        source_context = {"provenance": provenance, "task": unit.get("text"),
                          "brief": self.recipe.get("brief", ""), "tools": unit.get("tools", [])}
        evidence_level = ("recorded_context_model_assessed" if unit["kind"] == "conversation"
                          else "source_and_model_assessed" if unit["kind"] == "document"
                          else "model_assessed_synthetic")
        source_verification = ("recorded_unverified" if unit["kind"] == "conversation"
                               else "exact_quote_presence_only" if unit["kind"] == "document"
                               else "no_external_source")
        reviews = []
        quotes_by_turn = []
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
            for turn_index in range(self.recipe.get("conversation_turns", 3)):
                feedback = None
                for attempt in range(2):
                    user_data = self.ask([unit["id"], "multiturn_user", turn_index, attempt],
                        "generation", "workflow.multiturn_user",
                        {**source_context, "turn": turn_index + 1, "total_turns": self.recipe.get("conversation_turns", 3),
                         "previous_messages": messages, "feedback": feedback})
                    user_text = user_data.get("message") if isinstance(user_data, dict) else None
                    if text_issue(user_text) or len(user_text) > 6000:
                        feedback = "用户消息为空、含敏感信息或过长"
                        continue
                    if any(same_answer(user_text, previous) for previous in raw_user_messages):
                        feedback = "不能重复之前的用户问题"
                        continue
                    if turn_index == 0 and unit["kind"] == "document":
                        user_text = ("请依据以下资料完成多轮问答，只使用资料支持的内容。\n\n资料：\n"
                                     f"{unit['text']}\n\n问题：\n{user_text}")
                    elif turn_index == 0 and self.recipe.get("brief"):
                        user_text = f"任务背景：{self.recipe['brief']}\n\n问题：\n{user_text}"
                    user_message = {"role": "user", "content": user_text}
                    response = self.ask([unit["id"], "multiturn_answer", turn_index, attempt],
                        "generation", "workflow.multiturn_assistant",
                        {**source_context, "messages": [*messages, user_message], "feedback": feedback})
                    answer = response.get("answer") if isinstance(response, dict) else None
                    quotes = response.get("quotes") if isinstance(response, dict) else None
                    if text_issue(answer) or len(answer) > 12000 or not isinstance(quotes, list):
                        feedback = "回答或逐字证据结构不合格"
                        continue
                    if unit["kind"] == "document" and (not quotes or any(
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
                         "quotes": quotes, "source_text": unit.get("text")}, assistant_message)
                    if not accepted(check):
                        feedback = check["reason"]
                        continue
                    messages = candidate
                    raw_user_messages.append(user_data["message"])
                    reviews.append({"turn": turn_index + 1, "end_message_index": len(messages) - 1,
                                    "judge": check, "repair_attempts": attempt})
                    quotes_by_turn.append(quotes)
                    break
                else:
                    return [{**self.rejected(unit, "multiturn_turn_failed_after_repair"),
                             "failed_turn": turn_index + 1, "feedback": feedback,
                             "turn_reviews": reviews, "evidence_level": evidence_level}]
            ends, issue = completed_turn_ends(messages)
            if issue:
                return [self.rejected(unit, issue)]
        consistency = self.judge_answer([unit["id"], "multiturn_consistency"],
            {**source_context, "evidence_level": evidence_level, "turn_reviews": reviews,
             "source_text": unit.get("text"), "whole_dialogue": True},
            messages, prompt_id="workflow.multiturn_consistency")
        if not accepted(consistency):
            return [{**self.rejected(unit, "multiturn_consistency_rejected"),
                     "turn_reviews": reviews, "consistency": consistency,
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
        row = {"id": unit["id"], "source_id": unit["source_id"],
               "status": result["status"], "evidence_level": evidence,
               "source_location": unit.get("location"),
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
                               "source_location": unit.get("location"), "id": unit["id"],
                               "original_messages_sha256": row["original_messages_sha256"]}
        return [row]

    def dpo(self, sample):
        prefix, chosen = sample["messages"][:-1], sample["messages"][-1]
        value = self.ask([sample["id"], "alternative"], "generation", "workflow.alternative",
            {"prompt": prefix, "source": sample["source_context"]})
        if not isinstance(value, dict) or any(text_issue(value.get(k)) for k in ("answer", "reasoning")):
            return [self.rejected(sample, "invalid_dpo_candidate")]
        alternative = {"role": "assistant", "content": value["answer"], "reasoning_content": value["reasoning"]}
        if same_answer(chosen["content"], alternative["content"]):
            return [self.rejected(sample, "identical_dpo_answers")]
        check = self.judge_answer([sample["id"], "alternative_judge"],
                                  {"prompt": prefix, "source": sample["source_context"]}, alternative)
        node_models = self.recipe.get("node_models", {})
        chosen_check = sample["judge"]
        if node_models.get("sft", {}).get("jev") != node_models.get("preference", {}).get("jev"):
            chosen_check = self.judge_answer([sample["id"], "chosen_judge"],
                {"prompt": prefix, "source": sample["source_context"]}, chosen)
        choices = sorted([(chosen_check, chosen), (check, alternative)], key=lambda x: x[0]["correctness"])
        low, high = choices
        if not accepted(high[0]) or high[0]["correctness"] - low[0]["correctness"] < 2:
            return [{**self.rejected(sample, "insufficient_preference_evidence"), "checks": [low[0], high[0]]}]
        return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible", "prompt": prefix,
                 "chosen": [high[1]], "rejected": [low[1]], "tools": sample.get("tools", []),
                 "preference": {"dimension": "correctness", "chosen": high[0], "rejected": low[0], "minimum_gap": 2},
                 "evidence_level": sample["evidence_level"]}]

    def gsm8k(self, item):
        seed = int(item["id"][:8], 16)
        theme = self.recipe["brief"] or (self.recipe["sources"][0]["name"] if self.recipe["sources"] else "开放数学推理")
        sample = build_gsm8k(theme, seed)
        if not validate_gsm8k(sample):
            return [self.rejected(item, "gsm8k_arithmetic_verification_failed")]
        return [{"id": item["id"], "source_id": item["source_id"], "status": "eligible",
                 "question": sample["question"], "answer": sample["answer"],
                 "arithmetic_expression": sample["_expression"], "verified_result": sample["_result"],
                 "evidence_level": "deterministic_synthetic_arithmetic"}]

    def cot(self, sample):
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
            call_key = digest(["call", [sample["id"], "cot_check"]])
            (self.path / "checkpoints" / self.stage / f"{call_key}.json").unlink(missing_ok=True)
            raise
        if not accepted(check):
            return [{**self.rejected(sample, "cot_reasoning_rejected"), "judge": check}]
        steps = [part.strip() for part in re.split(r"(?<=[。.!?])\s*", reasoning) if part.strip()]
        return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                 "question": messages[:-1], "reasoning": steps or [reasoning], "answer": answer,
                 "judge": check, "evidence_level": sample["evidence_level"]}]

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
        destination = self.path / "artifacts"
        destination.mkdir(exist_ok=True)
        cpt_references = self.recipe.get("cpt_reference_releases", [])
        released_index, released_near_index, released_rows = (
            released_corpus_index(self.path.parent.parent, cpt_references)
            if "cpt" in self.recipe["targets"] else ({}, None, 0))
        report = {"policy": POLICY, "targets": {}, "trainer_exports": {}, "human_review": "not_performed",
                  "input_summary": self.state.get("input_summary", {}),
                  "input_issues": [{"id": u["id"], "source_id": u["source_id"],
                                    "source_name": u.get("source_name"), "location": u.get("location"),
                                    "source_location": u.get("source_location"), "reason": u.get("reason")}
                                   for u in read_json(self.path / "input_records.json") if u["status"] == "quarantined"],
                  "limitations": ["模型评审不等于事实证明", "字符分块不是 tokenizer 长度",
                                  "CPT 当前批次及同工作区已发布版本的近重复筛查不证明来源许可或事实正确",
                                  "CPT 跨批参照仅覆盖任务创建时同一工作区已发布且清单可校验的版本；不覆盖其他工作区或未审核候选",
                                  "CPT 近重复候选索引可能漏检；短于 300 字符的不同文本只做精确去重",
                                  "Agent 默认仅重放受限整数 calculator 和录制快照上的 json_pointer；显式配置固定摘要镜像后可隔离重放受限 sandbox_ledger 状态机，仍不证明上传快照的外部真实性；其他工具隔离",
                                  "GSM8K 是受限整数算术模板样例，不等同完整 GSM8K 基准",
                                  "TRL 格式相容不证明所选模型聊天模板或 tokenizer 可用于训练",
                                  "敏感信息规则不能覆盖所有隐私类型"]}
        if "cpt" in self.recipe["targets"]:
            report["limitations"].append(
                "CPT 评测集重叠检查仅覆盖本次上传并固定的参照；未提供的外部评测集污染状态未知"
                if evaluation_index is not None else
                "CPT 未配置评测集参照，外部评测集污染状态未知")
        if "rlaif" in self.recipe["targets"]:
            report["limitations"].append(
                "RLAIF 当前仅产出任务正确性准则下的 AI 反馈和奖励模型偏好候选；未训练奖励模型、提供在线奖励或运行强化学习")
        for target in self.recipe["targets"]:
            self.check_cancel()
            records = RowSpool(self.path / "stage-results" / f"package-{target}-records.jsonl")
            training = RowSpool(self.path / "stage-results" / f"package-{target}-training.jsonl")
            seen = set()
            corpus_index = CorpusNearDuplicateIndex() if target == "cpt" else None
            for original in collections[target]:
                self.check_cancel()
                row = deepcopy(original)
                if row["status"] == "eligible":
                    payload = training_record(target, row)
                    if corpus_index is not None:
                        evaluation_hit = evaluation_index.inspect(row["text"]) if evaluation_index else None
                        if evaluation_hit:
                            row.update(status="quarantined", reason=evaluation_hit["reason"],
                                       evaluation_reference=evaluation_hit)
                            # Preserve the audit fingerprint without reproducing
                            # held-out text in the downloadable quality sidecar.
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
                                    released_duplicate := released_near_index.match(row["text"])):
                                row.update(status="duplicate", reason="released_corpus_near_duplicate",
                                           duplicate_scope="prior_cpt_release",
                                           duplicate_of=released_duplicate["duplicate_of"],
                                           representative_source=released_duplicate["representative_source"],
                                           reference_release=released_duplicate["reference_release"],
                                           similarity=released_duplicate["similarity"])
                            else:
                                duplicate = corpus_index.check_and_add(row["text"], row)
                                if duplicate:
                                    row.update(status="duplicate", duplicate_scope="current_run", **duplicate)
                                else:
                                    training.append(payload)
                    else:
                        identity = digest(payload)
                        if identity in seen:
                            row["status"], row["reason"] = "duplicate", "duplicate_training_content"
                        else:
                            seen.add(identity)
                            training.append(payload)
                records.append(row)
            records.close()
            training.close()
            if target == "cpt":
                cluster_sizes = {}
                for row in records:
                    if row["status"] in {"eligible", "duplicate"}:
                        root_id = row.get("duplicate_of", row["id"])
                        cluster_sizes[root_id] = cluster_sizes.get(root_id, 0) + 1
                # A cross-run root is outside this candidate list, but still
                # represents one verified exemplar in the displayed cluster.
                for root_id in {row["duplicate_of"] for row in records
                                if row.get("duplicate_scope") == "prior_cpt_release"}:
                    cluster_sizes[root_id] += 1
                def with_cluster(row):
                    if row["status"] in {"eligible", "duplicate"}:
                        row["duplicate_cluster_size"] = cluster_sizes[row.get("duplicate_of", row["id"])]
                    return row
                records = WorkflowRows(records.path, len(records), transform=with_cluster)
            # Atomic checkpoints hold metadata; files contain only the selected training schema.
            output = destination / f"{target}.jsonl"
            write_jsonl(output, training)
            write_json_array(destination / f"{target}.records.json", records)
            reasons = {}
            for row in records:
                if row.get("reason"):
                    reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
            report["targets"][target] = {"eligible": len(training), "total": len(records), "reasons": reasons}
            if target in {"sft", "multiturn", "agent", "dpo", "orpo", "rlaif"}:
                # A trainer export is complete only if every eligible native row converts.
                # Keep the native target even when its optional TRL format is incompatible.
                report["trainer_exports"][target] = write_trainer_export(destination, target, records)
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
        atomic_json(destination / "quality.json", report)
        self.state["quality"] = {"policy": report["policy"], "targets": report["targets"],
                                 "human_review": report["human_review"],
                                 "trainer_exports": {target: {key: value for key, value in summary.items()
                                                            if key != "failures"}
                                                     for target, summary in report["trainer_exports"].items()}}
        for pending in destination.glob(".*.pending"):
            pending.unlink(missing_ok=True)
        files = {p.name: file_hash(p) for p in destination.iterdir() if p.is_file() and p.name != "manifest.json"}
        atomic_json(destination / "manifest.json", {"status": "complete", "run_id": self.state["id"],
                    "recipe_hash": self.state["recipe_hash"], "policy": POLICY, "models": self.state.get("models", {}),
                    "sources": self.recipe["sources"], "counts": {t: v["eligible"] for t, v in report["targets"].items()},
                    "trainer_counts": {t: v["summary"]["compatible"] for t, v in report["trainer_exports"].items()
                                       if v["status"] == "ready"},
                    "negative_counts": {t: v["negative"] for t, v in report["targets"].items() if v.get("negative")},
                    "cpt_reference_releases": cpt_references,
                    "evaluation_references": evaluation_references,
                    "sha256": files, "created_at": now(), "release_kind": "automatically_checked_candidate"})
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
                if digest(self.recipe) != self.state["recipe_hash"] or self.recipe["version"] not in SUPPORTED_RECIPE_VERSIONS:
                    raise ValueError("recipe_changed_create_new_run")
                current_prompts = prompt_versions()
                if any(current_prompts.get(key) != pinned for key, pinned in self.recipe["prompts"].items()):
                    raise ValueError("prompts_changed_create_new_run")
                for source in self.recipe["sources"]:
                    if file_hash(self.path / "inputs" / source["file"]) != source["sha256"]:
                        raise ValueError("source_snapshot_changed")
                units = self.stage_items("ingest", self.recipe["sources"], self.parse_source)
                selected_targets = set(self.recipe["targets"])
                planned_targets = selected_targets & ({"cpt", "sft", "cot", "multiturn"} | PREFERENCE_TARGETS)
                if self.recipe["brief"] and not self.recipe["sources"] and planned_targets:
                    self.stage = "ingest"
                    units = self.checkpoint("planned_tasks", self.plan)
                    self.state["stages"]["ingest"].update(status="completed", done=len(units),
                                                          total=len(units), outputs=len(units), eligible=len(units))
                if "gsm8k" in selected_targets and not units:
                    units = [{"id": digest([self.recipe["brief"], "gsm8k", i]), "source_id": digest(self.recipe["brief"]),
                              "kind": "brief", "text": self.recipe["brief"], "status": "ready"}
                             for i in range(self.recipe["tasks"])]
                eligible = [u for u in units if u["status"] == "ready"]
                requested = self.recipe.get("sample_count") or self.recipe["tasks"]
                planning_deferred = max(0, requested - self.recipe["max_units"]) if not self.recipe["sources"] else 0
                self.state["input_summary"] = {"units": len(units), "ready": len(eligible),
                    "quarantined": len(units) - len(eligible), "deferred": max(max(0, len(eligible) - self.recipe["max_units"]), planning_deferred),
                    "targets": list(self.recipe["targets"])}
                write_json_array(self.path / "input_records.json", units)
                selected = eligible[:self.recipe["max_units"]]
                generated = generation_units(selected, self.recipe.get("sample_count"))
                self.state["input_summary"]["generation_candidates"] = len(generated)
                collections = {}
                needs_sft = bool(selected_targets & (PREFERENCE_TARGETS | {"sft", "cot"}))
                collections["cpt"] = self.stage_items("cpt", selected, self.cpt) if "cpt" in selected_targets else []
                collections["sft"] = self.stage_items("sft", generated, self.sft) if needs_sft else []
                if not needs_sft:
                    self.state["stages"]["sft"]["status"] = "skipped"

                self.state["stages"].setdefault("multiturn", {"label": STAGES["multiturn"],
                    "status": "pending", "done": 0, "total": 0})
                collections["multiturn"] = (self.stage_items("multiturn", generated, self.multiturn)
                                            if "multiturn" in selected_targets else [])
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
                    items = [{"id": digest([seed_base, "gsm8k", index]),
                              "source_id": digest(seed_base), "status": "ready"}
                             for index in range(min(self.recipe.get("sample_count") or self.recipe["tasks"], self.recipe["max_units"]))]
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
                # Packaging is deliberately re-run after interruption, never trusted from a stale checkpoint.
                self.stage = "package"
                self.state["stages"]["package"].update(status="running", total=1, done=0)
                self.save()
                self.check_cancel()
                self.package(collections)
                self.state["stages"]["package"].update(status="completed", done=1, finished_at=now())
                counts = self.state["quality"]["targets"]
                cpt_quarantined = any(row["status"] == "quarantined" for row in collections["cpt"])
                multiturn_quarantined = any(row["status"] == "quarantined" for row in collections["multiturn"])
                agent_quarantined = any(row["status"] == "quarantined" for row in collections["agent"])
                attention = (any(not v["eligible"] or v.get("negative", 0) for v in counts.values())
                             or cpt_quarantined
                             or multiturn_quarantined
                             or agent_quarantined
                             or self.state["input_summary"]["quarantined"] > 0
                             or self.state["input_summary"]["deferred"] > 0)
                self.state["status"] = "needs_attention" if attention else "completed"
                self.event("run_finished")
            except Cancelled:
                self.state["status"] = "cancelled"
                self.state["stages"][self.stage]["status"] = "cancelled"
                self.event("run_cancelled")
            except Exception as error:
                # Never persist raw provider exceptions: they can contain credentials or source text.
                code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]+", str(error)) else type(error).__name__
                self.state.update(status="failed", error=code)
                self.state["stages"][self.stage].update(status="failed", error=code)
                self.event("run_failed", error=code)
            return self.state


def resume(output, run_id, root, **clients):
    return Workflow(output, run_id, root, **clients).execute(resume_run=True)
