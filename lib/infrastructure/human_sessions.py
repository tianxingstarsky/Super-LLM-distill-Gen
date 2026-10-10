"""Local human workspaces with immutable child runs and explicit reviewed revisions.

This adapter prepares runs. It never starts a model request or a worker process.
The console uses the ordinary workflow resume command for every returned run ID.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import uuid

from filelock import FileLock, Timeout

from lib.bootstrap.workflows import workflow_application
from lib.domain.document_parser import validate_document_parser
from lib.domain.human_augmentation import HUMAN_QA_TARGETS, validate_human_augmentation
from lib.domain.human_session import (MAX_REVISION_DEPTH, MAX_SESSION_ROUNDS,
    identifier, request_identifier, validate_draft, validate_feedback)
from lib.domain.human_round_projection import project_feedback_branch
from lib.domain.workflow_creation import validate_creation
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.domain.workflow_qa_director import validate_qa_director
from lib.domain.workflow_targets import TARGETS, TRAINING_FIELDS
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.source_snapshot import snapshot_source
from lib.infrastructure.verified_preview import verified_snapshot, inventory_identity
from lib.infrastructure.human_revision import record_messages, revision_evidence
from lib.infrastructure.training_workflow import (create_run, digest, file_hash,
    read_json, run_path, verify_artifacts, EXTENSIONS, MAX_FILE_BYTES)
from lib.io_utils import atomic_json
from lib.llm_client import snapshot_backend_endpoint


def _now():
    return datetime.now(timezone.utc).isoformat()


def _messages(record):
    return record_messages(record)


def _record_revision(record):
    return (record.get("revision_context") or record.get("qa_contract", {}).get("human_design", {})
            .get("seed", {}).get("revision_context") or {})


class FilesystemHumanSessionDriver:
    def __init__(self, root: Path, output: Path):
        self.root, self.output = Path(root), Path(output)
        self.workflow = workflow_application(self.root, self.output)

    def _path(self, session_id):
        path = self.output / "human-sessions" / identifier(session_id)
        if not (path / "session.json").is_file():
            raise ValueError("human_session_not_found")
        return path

    def _read(self, path):
        state = read_json(path / "session.json")
        blueprint = read_json(path / "blueprint.json")
        if digest(blueprint) != state["blueprint_sha256"]:
            raise ValueError("human_session_configuration_changed")
        state["blueprint"] = blueprint
        return state

    def _save(self, path, state, *, bump=True):
        state = deepcopy(state)
        state.pop("blueprint", None)
        if bump:
            state["version"] += 1
            state["updated_at"] = _now()
        atomic_json(path / "session.json", state)

    @staticmethod
    def _cas(state, expected_version):
        if type(expected_version) is not int or expected_version != state["version"]:
            raise ValueError("human_session_version_conflict")

    def _round(self, state, round_id):
        identifier(round_id, "invalid_human_session_round_id")
        for row in state["rounds"]:
            if row["id"] == round_id:
                return row
        raise ValueError("human_session_round_not_found")

    def _view_round(self, row):
        row = deepcopy(row)
        try:
            child = self.workflow.state(row["run_id"])
            active = self.workflow.is_active(row["run_id"])
            try:
                with FileLock(str(run_path(self.output, row["run_id"]) / ".console-job.lock"), timeout=0):
                    pass
            except Timeout:
                active = True
        except (FileNotFoundError, KeyError):
            child, active = {}, False
        ledger_status = row["status"]
        status = child.get("status", ledger_status)
        if ledger_status == "cancelled":
            status = "cancelled"
        elif ledger_status == "cancel_requested" and active:
            status = "cancel_requested"
        elif ledger_status == "cancel_requested":
            status = "cancelled"
        elif ledger_status == "preparation_failed":
            status = "preparation_failed"
        elif (ledger_status == "resume_requested" and not active
              and child.get("attempt", 0) <= row.get("resume_after_attempt", 0)):
            status = "prepared"
        elif status == "queued":
            status = "prepared"
        elif status == "running" and not active:
            status = "interrupted"
        row.update(status=status, state=child, active=active,
                   launchable=not active and status in {"prepared", "interrupted", "failed"})
        return row

    def _view(self, state):
        view = deepcopy(state)
        view["rounds"] = [self._view_round(row) for row in state["rounds"]]
        current = next((row for row in reversed(view["rounds"]) if row["active"] or row["status"] in
            {"prepared", "running", "interrupted", "creating", "cancel_requested"}),
            view["rounds"][-1] if view["rounds"] else {})
        view.update(current_run_id=current.get("run_id"), current_round_id=current.get("id"))
        view.pop("blueprint_sha256", None)
        return view

    def session(self, session_id):
        path = self._path(session_id)
        with FileLock(str(path / ".session.lock")):
            return self._view(self._read(path))

    def list_sessions(self):
        rows = []
        for file in (self.output / "human-sessions").glob("*/session.json"):
            try:
                state = self.session(file.parent.name)
                rows.append({key: state[key] for key in ("id", "name", "version", "created_at", "updated_at",
                    "current_run_id", "current_round_id")} | {
                        "round_count": len(state["rounds"]), "targets": state["blueprint"]["targets"],
                        "status": state["rounds"][-1]["status"] if state["rounds"] else "draft"})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(rows, key=lambda row: row["updated_at"], reverse=True)

    def _blueprint(self, recipe, *, from_run_id=None):
        allowed = set(inspect.signature(create_run).parameters) - {"output", "settings_root", "run_id"}
        if set(recipe) - allowed:
            raise ValueError("invalid_human_session_configuration")
        parent = None
        if from_run_id is not None:
            parent_path = run_path(self.output, from_run_id)
            verify_artifacts(parent_path)
            parent = self.workflow.recipe(from_run_id)
            inherited = {key: deepcopy(parent[key]) for key in allowed if key in parent
                         and key not in {"sources", "source_names", "evaluation_sources", "evaluation_source_names"}}
            inherited["targets"] = list(parent["targets"])
            if "cpt" not in inherited["targets"]:
                inherited.pop("cpt_processing", None)
            inherited.pop("repair_inputs", None)
            if inherited.get("production"):
                inherited["production"]["goals"] = {target: count for target, count in inherited["production"]["goals"].items()
                    if target in inherited["targets"]}
            inherited["sources"] = [str(parent_path / "inputs" / source["file"])
                for source in parent["sources"] if source.get("kind") != "human_design"]
            inherited["source_names"] = {str(parent_path / "inputs" / source["file"]): source["name"]
                for source in parent["sources"] if source.get("kind") != "human_design"}
            inherited["node_prompts"] = parent.get("node_prompt_templates", inherited.get("node_prompts"))
            node_models = inherited.get("node_models", {})
            if not node_models.get("director", {}).get("generation"):
                binding = node_models.get("sft", {}).get("generation") or node_models.get("multiturn", {}).get("generation")
                if binding:
                    node_models.setdefault("director", {})["generation"] = deepcopy(binding)
            recipe = {**inherited, **recipe}
        blueprint = deepcopy(recipe)
        blueprint.setdefault("targets", ["sft"])
        if not set(blueprint["targets"]) <= (set(TARGETS) if parent else HUMAN_QA_TARGETS):
            raise ValueError("human_session_requires_qa_targets")
        human = (blueprint.get("qa_director") or {}).get("human_augmentation")
        director = {**(blueprint.get("qa_director") or {}), "enabled": set(blueprint["targets"]) <= HUMAN_QA_TARGETS,
                    "planning_mode": "adaptive", "human_augmentation": {"enabled": False}}
        blueprint["qa_director"] = validate_qa_director(director)
        fields = set(inspect.signature(validate_creation).parameters)
        targets, node_models = validate_creation(**{key: value for key, value in blueprint.items() if key in fields})
        blueprint["targets"], blueprint["node_models"] = targets, node_models
        blueprint["document_parser"] = validate_document_parser(blueprint.get("document_parser"))
        blueprint["node_prompts"] = snapshot_node_prompts(blueprint.get("node_prompts"),
            recipe_version=18 if blueprint.get("review_repair") else 17)
        blueprint.setdefault("name", "人工问答增强")
        blueprint["name"] = str(blueprint["name"])[:100]
        if human and human.get("enabled"):
            # An imported revision's lineage belongs to its sealed result, not
            # to a new editable draft. Preserve only its human design fields.
            human = {**human, "seeds": [{key: value for key, value in seed.items() if key != "revision_context"}
                                       for seed in human.get("seeds", [])]}
        return blueprint, validate_draft(human or {"enabled": True, "seeds": []}), parent

    def create_session(self, *, from_run_id=None, max_revision_depth=3, initial_draft=None, review_only=False, **recipe):
        if type(max_revision_depth) is not int or not 1 <= max_revision_depth <= MAX_REVISION_DEPTH:
            raise ValueError("invalid_human_session_revision_limit")
        if type(review_only) is not bool or review_only and from_run_id is None:
            raise ValueError("invalid_human_session_review_mode")
        blueprint, draft, parent = self._blueprint(recipe, from_run_id=from_run_id)
        if initial_draft is not None:
            draft = validate_draft(initial_draft)
        draft["enabled"] = True
        session_id = uuid.uuid4().hex
        path = self.output / "human-sessions" / session_id
        (path / "inputs").mkdir(parents=True, exist_ok=False)
        # These are the same bounded source snapshots used by normal workflows.
        sources = [Path(source).resolve(strict=True) for source in blueprint.get("sources", ())]
        if len(sources) > 200:
            raise ValueError("human_session_source_limit")
        names, snapshots, copied, paths = {}, [], 0, []
        for index, source in enumerate(sources):
            if not source.is_file() or source.suffix.lower() not in EXTENSIONS:
                raise ValueError("human_session_invalid_source")
            destination = path / "inputs" / f"{index:04d}{source.suffix.lower()}"
            snapshot = snapshot_source(source, destination, min(MAX_FILE_BYTES, 200 * 1024 * 1024 - copied))
            copied += snapshot["bytes"]
            source_name = (blueprint.get("source_names") or {}).get(str(source), source.name)
            names[str(destination)] = source_name
            paths.append(str(destination))
            snapshots.append({"file": destination.name, "name": source_name, **snapshot})
        blueprint["sources"], blueprint["source_names"] = paths, names
        pins = {stage: {role: snapshot_backend_endpoint(self.root, binding["backend"], binding["model"])
            for role, binding in roles.items()} for stage, roles in blueprint["node_models"].items()}
        blueprint["session_source_snapshots"] = snapshots
        blueprint["session_endpoint_pins"] = pins
        atomic_json(path / "blueprint.json", blueprint)
        state = {"id": session_id, "name": blueprint["name"], "version": 1,
                 "created_at": _now(), "updated_at": _now(), "blueprint_sha256": digest(blueprint),
                 "draft": draft, "rounds": [], "feedback": [],
                 "review_only": review_only or bool(parent and (parent.get("repair_inputs") or set(blueprint["targets"]) - HUMAN_QA_TARGETS)),
                 "limits": {"max_rounds": MAX_SESSION_ROUNDS, "max_revision_depth": max_revision_depth}}
        if from_run_id is not None:
            state["rounds"].append({"id": uuid.uuid4().hex, "run_id": from_run_id,
                "kind": "imported", "request_id": "import:" + from_run_id,
                "status": "imported", "created_at": _now(), "parent_results": [], "feedback_ids": []})
            state["rounds"][-1]["round_id"] = state["rounds"][-1]["id"]
        atomic_json(path / "session.json", state)
        return session_id

    def save_draft(self, session_id, human_augmentation, *, expected_version):
        draft, path = validate_draft(human_augmentation), self._path(session_id)
        with FileLock(str(path / ".session.lock")):
            state = self._read(path)
            self._cas(state, expected_version)
            state["draft"] = draft
            self._save(path, state)
            return self._view(self._read(path))

    def _existing_request(self, state, request_id, kind):
        for row in state["rounds"]:
            if row["request_id"] == request_id:
                if row["kind"] != kind:
                    raise ValueError("human_session_request_conflict")
                view = self._view_round(row)
                if view["status"] in {"cancelled", "preparation_failed"}:
                    raise ValueError("human_session_round_cancelled")
                if view["launchable"]:
                    self._assert_resume_safe(state, row["id"])
                return view
        return None

    def _can_prepare(self, state):
        if len(state["rounds"]) >= state["limits"]["max_rounds"]:
            raise ValueError("human_session_round_limit")
        for row in state["rounds"]:
            view = self._view_round(row)
            if view["active"] or view["status"] in {"prepared", "running", "interrupted", "creating", "cancel_requested"}:
                raise ValueError("human_session_round_pending")

    def _assert_resume_safe(self, state, round_id):
        for other in state["rounds"]:
            if other["id"] == round_id:
                continue
            current = self._view_round(other)
            if current["active"] or current["status"] in {"prepared", "running", "interrupted", "creating", "cancel_requested"}:
                raise ValueError("human_session_round_pending")

    def _reserve_resume(self, path, state, row):
        view = self._view_round(row)
        if view["status"] in {"failed", "interrupted"}:
            self._assert_resume_safe(state, row["id"])
            row.update(status="resume_requested", resume_after_attempt=view["state"].get("attempt", 0))
            self._save(path, state)
            view = self._view_round(row)
        return view

    def _checked_blueprint(self, path, state, *, check_models=True):
        blueprint = deepcopy(state["blueprint"])
        sources = blueprint.pop("session_source_snapshots")
        for source in sources:
            file = path / "inputs" / source["file"]
            if not file.is_file() or file.is_symlink() or file_hash(file) != source["sha256"]:
                raise ValueError("human_session_source_changed")
        pins = blueprint.pop("session_endpoint_pins")
        if not check_models:
            # Human review still authenticates source snapshots and parent
            # results, but it never depends on unused model service settings.
            blueprint["node_models"] = {}
            return blueprint, {}
        current = {stage: {role: snapshot_backend_endpoint(self.root, binding["backend"], binding["model"])
            for role, binding in roles.items()} for stage, roles in blueprint["node_models"].items()}
        if pins != current:
            raise ValueError("human_session_model_configuration_changed")
        return blueprint, pins

    def _prepare(self, path, state, human, *, request_id, kind, sample_count=None,
                 parents=None, feedback_ids=None, repair_inputs=None, review_mode=None):
        self._can_prepare(state)
        human_review = repair_inputs is not None and (review_mode or
            (state["blueprint"].get("review_repair") or {}).get("mode")) == "human"
        blueprint, pins = self._checked_blueprint(path, state, check_models=not human_review)
        blueprint["qa_director"] = {**blueprint["qa_director"], "human_augmentation": human}
        if repair_inputs is not None:
            # Existing answers are immutable repair evidence, never new SFT
            # seeds. This branch executes only the review node and packaging.
            blueprint["qa_director"] = {**blueprint["qa_director"], "enabled": False,
                                        "human_augmentation": {"enabled": False}}
            blueprint["repair_inputs"] = deepcopy(repair_inputs)
            blueprint["targets"] = list(dict.fromkeys(item["target"] for item in repair_inputs))
            if "sft" not in blueprint["targets"]:
                blueprint.pop("sft_output_style", None)
            if "cpt" not in blueprint["targets"]:
                blueprint.pop("cpt_processing", None)
            if not set(blueprint["targets"]) & {"sft", "cot"}:
                blueprint.pop("reasoning_trim", None)
            blueprint["review_repair"] = {**(blueprint.get("review_repair") or {
                "mode": "auto", "max_rounds": 2, "score_threshold": 0.8}),
                **({"mode": review_mode} if review_mode else {})}
            blueprint["production"] = None
            blueprint["web_research"] = None
            blueprint["brief"] = ""
            blueprint["evaluation_sources"] = []
            sample_count = len(repair_inputs)
            if blueprint["review_repair"]["mode"] == "auto":
                models = blueprint["node_models"]
                bindings = models.setdefault("review", {})
                target_node = "preference" if blueprint["targets"][0] in {"dpo", "rlaif", "orpo"} else blueprint["targets"][0]
                if not bindings.get("generation"):
                    binding = models.get(target_node, {}).get("generation") or models.get("sft", {}).get("generation")
                    if binding:
                        bindings["generation"] = deepcopy(binding)
                if (blueprint.get("package_review") or {}).get("enabled") and not bindings.get("jev"):
                    binding = (models.get("jev", {}).get("jev") or models.get("package", {}).get("jev")
                               or models.get(target_node, {}).get("jev") or bindings.get("generation"))
                    if binding:
                        bindings["jev"] = deepcopy(binding)
                active_roles = {"generation", "jev"} if (blueprint.get("package_review") or {}).get("enabled") else {"generation"}
                selected = {role: binding for role, binding in bindings.items() if role in active_roles}
                blueprint["node_models"] = {"review": selected} if selected else {}
            pins = {stage: {role: snapshot_backend_endpoint(self.root, binding["backend"], binding["model"])
                for role, binding in roles.items()} for stage, roles in blueprint["node_models"].items()}
        if sample_count is not None:
            blueprint["sample_count"] = sample_count
        if blueprint.get("sample_count") is None:
            blueprint["sample_count"] = len(human.get("seeds", []))
        blueprint["max_units"] = max(blueprint.get("max_units", 100), len(human.get("seeds", [])))
        # Human rounds use their explicit candidate budget, never a stale
        # automatic million-row goal inherited from another working mode.
        # Preserve an existing task cost cap and finite transport retry policy.
        if production := blueprint.get("production"):
            count = blueprint["sample_count"]
            blueprint["production"] = {**production, "version": 2, "quantity_policy": "quality_first",
                "goals": {target: count for target in blueprint["targets"]},
                "max_attempts": min(production.get("max_attempts", count), count),
                "round_size": min(production.get("round_size", count), count)}
        creation_fields = set(inspect.signature(validate_creation).parameters)
        validate_creation(**{key: value for key, value in blueprint.items() if key in creation_fields})
        run_id, round_id = uuid.uuid4().hex, uuid.uuid4().hex
        row = {"id": round_id, "round_id": round_id, "run_id": run_id, "kind": kind,
               "request_id": request_id, "status": "creating", "created_at": _now(),
               "design_version": state["version"], "human_augmentation_sha256": digest(human),
               "parent_results": deepcopy(parents or []), "feedback_ids": list(feedback_ids or [])}
        if repair_inputs is not None:
            row["repair_inputs_sha256"] = digest(repair_inputs)
        state["rounds"].append(row)
        # Consume selections in the same durable write as the reserved child.
        # A crash after child creation cannot leave these feedback IDs reusable.
        for feedback in state["feedback"]:
            if feedback["id"] in (feedback_ids or []):
                feedback["applied_round_id"] = round_id
        self._save(path, state)
        # Save the exact identity before creating the child. No caller can launch
        # a paid request until this method returns a persisted ready mapping.
        try:
            blueprint["name"] = f"{state['name']} · {len(state['rounds'])}"
            created = self.workflow.create_run(**blueprint, run_id=run_id)
            if created != run_id:
                raise ValueError("human_session_run_identity_mismatch")
            created_recipe = self.workflow.recipe(run_id)
            if created_recipe.get("endpoint_pins", {}) != pins:
                raise ValueError("human_session_model_configuration_changed")
        except BaseException:
            latest = self._read(path)
            self._round(latest, round_id)["status"] = "preparation_failed"
            for feedback in latest["feedback"]:
                if feedback.get("applied_round_id") == round_id:
                    feedback["applied_round_id"] = None
            self._save(path, latest, bump=False)
            raise
        latest = self._read(path)
        self._round(latest, round_id)["status"] = "prepared"
        for feedback in latest["feedback"]:
            if feedback["id"] in (feedback_ids or []):
                feedback["applied_round_id"] = round_id
        self._save(path, latest, bump=False)
        return self._view_round(self._round(latest, round_id))

    def generate_round(self, session_id, *, request_id, expected_version, sample_count=None):
        path, request_id = self._path(session_id), request_identifier(request_id)
        with FileLock(str(path / ".session.lock")):
            state = self._read(path)
            if existing := self._existing_request(state, request_id, "generation"):
                return self._reserve_resume(path, state, self._round(state, existing["id"]))
            self._cas(state, expected_version)
            if state.get("review_only"):
                raise ValueError("human_session_requires_qa_targets")
            human = validate_human_augmentation(state["draft"])
            if not human["enabled"]:
                raise ValueError("human_session_design_required")
            return self._prepare(path, state, human, request_id=request_id, kind="generation", sample_count=sample_count)

    def _records(self, run_id, target, *, strict=False):
        if target not in TARGETS:
            raise ValueError("human_session_requires_qa_targets")
        path = run_path(self.output, run_id)
        if strict:
            verify_artifacts(path)
        else:
            verified_snapshot(path)
        identity = inventory_identity(path)
        file = path / "artifacts" / f"{target}.records.json"
        if not file.is_file():
            raise ValueError("human_session_target_not_found")
        try:
            yield from iter_json_records(file, max_record_chars=2_000_000)
        finally:
            if inventory_identity(path) != identity:
                raise ValueError("human_session_result_changed")

    def _result(self, state, row, record, target):
        messages = _messages(record)
        question = next((message.get("content", "") for message in messages if message.get("role") == "user"), "")
        answer = next((message.get("content", "") for message in reversed(messages) if message.get("role") == "assistant"), "")
        feedback = [deepcopy(item) for item in state["feedback"] if item["round_id"] == row["id"]
                    and item["target"] == target and item["candidate_id"] == record["id"]]
        revision = _record_revision(record)
        return {"candidate_id": record["id"], "target": target, "round_id": row["id"],
                "run_id": row["run_id"], "record": record, "messages": messages,
                "question": question, "answer": answer, "content_sha256": digest(record),
                "lineage": deepcopy(revision), "feedback": feedback}

    def branch_projection(self, session_id, *, round_id=None, target=None, candidate_id=None, result=None):
        state = self.session(session_id)
        row = self._round(state, round_id or state.get("current_round_id")) if state["rounds"] else None
        target = target or (result or {}).get("target") or state["blueprint"]["targets"][0]
        if target not in TARGETS or target not in state["blueprint"]["targets"]:
            raise ValueError("human_session_requires_qa_targets")
        if row is None:
            return project_feedback_branch(state["blueprint"], {},
                {"status": "draft", "limits": state["limits"]}, target=target)
        recipe = self.workflow.recipe(row["run_id"])
        if result is not None and (result.get("run_id") != row["run_id"]
                or result.get("round_id") != row["id"] or result.get("target") != target):
            raise ValueError("human_session_result_changed")
        if candidate_id is not None:
            result = self._result(state, row, self._lookup(row, target, candidate_id), target)
        summary = {"pending": 0, "ready": 0, "blocked": 0, "applied": 0}
        for item in state["feedback"]:
            if item["round_id"] != row["id"] or item["target"] != target or item.get("superseded_by"):
                continue
            if item.get("applied_round_id"):
                summary["applied"] += 1
                continue
            if item["decision"] != "revise":
                continue
            summary["pending"] += 1
            depth = item.get("revision_depth")
            if depth is None:
                # Historical feedback did not store this small display field.
                # Its immutable snapshot suffices; do not scan training records.
                snapshot = read_json(self._path(session_id) / "feedback" / f"{item['id']}.json")
                depth = _record_revision(snapshot["record"]).get("depth", 0)
            summary["blocked" if depth >= state["limits"]["max_revision_depth"] else "ready"] += 1
        # Refreshes use persisted stage progress and the small sealed state
        # summary. They must not re-hash or enumerate a million result records.
        manifest_path = run_path(self.output, row["run_id"]) / "artifacts" / "manifest.json"
        quality, source = {}, "not_sealed"
        if manifest_path.is_file():
            manifest = read_json(manifest_path)
            if (manifest.get("status") == "complete" and manifest.get("run_id") == row["run_id"]
                    and manifest.get("recipe_hash") == row["state"].get("recipe_hash")):
                quality = row["state"].get("quality", {})
                if any(manifest.get("counts", {}).get(key) != value.get("eligible")
                       for key, value in quality.get("targets", {}).items()):
                    raise ValueError("artifact_integrity_error")
                source = "sealed_state_summary"
        pending = next((other["id"] for other in state["rounds"] if other["active"] or other["status"] in
            {"prepared", "running", "interrupted", "creating", "cancel_requested"}), None)
        projection = project_feedback_branch(recipe, row["state"],
            {**row, "limits": state["limits"], "feedback_summary": summary, "pending_round_id": pending},
            result, quality, target=target)
        projection["summary_source"] = source
        return projection

    def results(self, session_id, *, round_id=None, target="sft", offset=0, limit=20):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_human_session_page")
        state = self.session(session_id)
        row = self._round(state, round_id or state.get("current_round_id")) if state["rounds"] else None
        if row is None:
            return {"rows": [], "total": 0, "has_more": False, "offset": offset, "limit": limit,
                    "round_id": None, "run_id": None, "status": "draft", "state": {}}
        result = {"rows": [], "total": 0, "has_more": False, "offset": offset, "limit": limit,
                  "round_id": row["id"], "run_id": row["run_id"], "status": row["status"], "state": row["state"]}
        if not (run_path(self.output, row["run_id"]) / "artifacts" / "manifest.json").exists():
            return result
        count, records = 0, self._records(row["run_id"], target)
        quality_path = run_path(self.output, row["run_id"]) / "artifacts" / "quality.json"
        # The records are integrity checked above; the sealed quality count
        # lets a page stop after its selected rows without counting a million.
        total = read_json(quality_path).get("targets", {}).get(target, {}).get("total") if quality_path.exists() else None
        try:
            for record in records:
                if offset <= count < offset + limit:
                    result["rows"].append(self._result(state, row, record, target))
                count += 1
                if type(total) is int and count >= offset + limit:
                    break
        finally:
            records.close()
        count = total if type(total) is int else count
        result.update(total=count, has_more=count > offset + limit)
        return result

    def _lookup(self, row, target, candidate_id):
        if not isinstance(candidate_id, str) or not candidate_id or len(candidate_id) > 500:
            raise ValueError("invalid_human_session_candidate_id")
        records = self._records(row["run_id"], target, strict=True)
        try:
            for record in records:
                if record.get("id") == candidate_id:
                    return record
        finally:
            records.close()
        raise ValueError("human_session_candidate_not_found")

    def save_feedback(self, session_id, round_id, target, candidate_id, *, instruction="",
                      question=None, answer=None, decision="revise", expected_version):
        correction = validate_feedback(instruction=instruction, question=question, answer=answer, decision=decision)
        path = self._path(session_id)
        with FileLock(str(path / ".session.lock")):
            state = self._read(path)
            self._cas(state, expected_version)
            row = self._round(state, round_id)
            if self._view_round(row)["active"]:
                raise ValueError("human_session_round_pending")
            record = self._lookup(row, target, candidate_id)
            feedback_id = uuid.uuid4().hex
            feedback = {"id": feedback_id, "round_id": round_id, "run_id": row["run_id"],
                "target": target, "candidate_id": candidate_id, "content_sha256": digest(record),
                "revision_depth": _record_revision(record).get("depth", 0),
                "created_at": _now(), "applied_round_id": None, **correction}
            atomic_json(path / "feedback" / f"{feedback_id}.json", {"feedback": feedback, "record": record})
            # Latest feedback on a candidate supersedes its previous unapplied
            # instruction. Historical versions and their child runs remain intact.
            for previous in state["feedback"]:
                if (previous["round_id"], previous["target"], previous["candidate_id"]) == (round_id, target, candidate_id):
                    previous["superseded_by"] = feedback_id
            state["feedback"].append(feedback)
            self._save(path, state)
            return {**self._view(self._read(path)), "feedback_id": feedback_id, "new_feedback_id": feedback_id}

    def _revision_design(self, path, state, feedback):
        snapshot = read_json(path / "feedback" / f"{feedback['id']}.json")
        record = snapshot["record"]
        if snapshot["feedback"] != {key: value for key, value in feedback.items()
                                    if key not in {"superseded_by"}} or digest(record) != feedback["content_sha256"]:
            raise ValueError("human_session_feedback_changed")
        parent = self._round(state, feedback["round_id"])
        actual = self._lookup(parent, feedback["target"], feedback["candidate_id"])
        if digest(actual) != feedback["content_sha256"]:
            raise ValueError("human_session_result_changed")
        previous_design = record.get("qa_contract", {}).get("human_design", {})
        seed = previous_design.get("seed", {})
        previous_context = _record_revision(record)
        depth = previous_context.get("depth", 0) + 1
        if depth > state["limits"]["max_revision_depth"]:
            raise ValueError("human_session_revision_limit")
        messages = _messages(record)
        question = feedback["question"] or next((m["content"] for m in messages if m.get("role") == "user"), "") or seed.get("question", "")
        selected_answer = next((m["content"] for m in reversed(messages) if m.get("role") == "assistant"), "")
        answer = feedback["answer"] or (seed.get("answer") if feedback["target"] == "multiturn" else selected_answer) or selected_answer or seed.get("answer", "")
        # Keep the full exact selected conversation. It is revision context,
        # never a conversation input that the runner would merely preserve.
        context = {"session_id": state["id"], "round_id": feedback["round_id"],
                   "parent_run_id": feedback["run_id"], "target": feedback["target"],
                   "candidate_id": feedback["candidate_id"], "content_sha256": feedback["content_sha256"],
                   "messages": messages, "instruction": feedback["instruction"], "depth": depth,
                   "ancestors": [*previous_context.get("ancestors", []),
                                 {"run_id": feedback["run_id"], "candidate_id": feedback["candidate_id"]}],
                   "design_requirements": {field: previous_design.get(field) or
                       previous_context.get("design_requirements", {}).get(field, "")
                       for field in ("question_requirements", "answer_requirements")},
                   **revision_evidence(record, self.workflow.recipe(feedback["run_id"]))}
        return {"question": question, "answer": answer, "revision_context": context,
                "question_requirements": seed.get("question_requirements", ""),
                "answer_requirements": seed.get("answer_requirements", "")}

    def _repair_input(self, path, state, feedback):
        context = self._revision_design(path, state, feedback)["revision_context"]
        record = read_json(path / "feedback" / f"{feedback['id']}.json")["record"]
        return {"target": feedback["target"], "record": record, "revision_context": context,
                "instruction": feedback["instruction"],
                **{key: feedback[key] for key in ("question", "answer") if feedback.get(key) is not None}}

    def revise_round(self, session_id, *, request_id, expected_version,
                     selected_feedback_ids=None, sample_count=None):
        path, request_id = self._path(session_id), request_identifier(request_id)
        with FileLock(str(path / ".session.lock")):
            state = self._read(path)
            if existing := self._existing_request(state, request_id, "revision"):
                return self._reserve_resume(path, state, self._round(state, existing["id"]))
            self._cas(state, expected_version)
            selected = [item for item in state["feedback"] if item["decision"] == "revise"
                        and not item.get("applied_round_id") and not item.get("superseded_by")]
            if selected_feedback_ids is not None:
                if (not isinstance(selected_feedback_ids, list)
                        or any(not isinstance(value, str) for value in selected_feedback_ids)
                        or len(selected_feedback_ids) != len(set(selected_feedback_ids))
                        or not 1 <= len(selected_feedback_ids) <= 200):
                    raise ValueError("invalid_human_session_feedback_selection")
                selected = [item for item in selected if item["id"] in selected_feedback_ids]
                if len(selected) != len(selected_feedback_ids):
                    raise ValueError("human_session_feedback_not_available")
            if not 1 <= len(selected) <= 200:
                raise ValueError("human_session_feedback_required")
            inputs = [self._repair_input(path, state, item) for item in selected]
            parents = [{key: item[key] for key in ("run_id", "round_id", "target", "candidate_id", "content_sha256")}
                       for item in selected]
            return self._prepare(path, state, {"enabled": False}, request_id=request_id, kind="revision",
                sample_count=sample_count, parents=parents, feedback_ids=[item["id"] for item in selected],
                repair_inputs=inputs, review_mode="auto")

    def submit_manual_review(self, session_id, round_id, target, candidate_id, *, request_id,
                             score, decision="approve", instruction="", messages=None,
                             question=None, answer=None, corrected_record=None, expected_version):
        """Reserve a manually scored/corrected version; no model work here."""
        from math import isfinite
        from lib.domain.workflow_quality import conversation_issue
        if (type(score) not in {int, float} or not isfinite(score) or not 0 <= score <= 100
                or decision not in {"approve", "reject"}):
            raise ValueError("invalid_manual_review_score")
        correction = validate_feedback(instruction=instruction, question=question, answer=answer, decision=decision)
        if messages is not None and (conversation_issue(messages) or len(json.dumps(messages, ensure_ascii=False)) > 100_000):
            raise ValueError("invalid_manual_review_messages")
        if corrected_record is not None:
            from lib.domain.workflow_quality import text_issue, canonical
            if (not isinstance(corrected_record, dict) or not corrected_record
                    or set(corrected_record) - {key for fields in TRAINING_FIELDS.values() for key in fields}
                    or len(canonical(corrected_record)) > 100_000 or text_issue(canonical(corrected_record))):
                raise ValueError("invalid_manual_review_record")
            if messages is not None or question is not None or answer is not None:
                raise ValueError("invalid_manual_review_record")
        path, request_id = self._path(session_id), request_identifier(request_id)
        with FileLock(str(path / ".session.lock")):
            state = self._read(path)
            if existing := self._existing_request(state, request_id, "manual_review"):
                return self._reserve_resume(path, state, self._round(state, existing["id"]))
            self._cas(state, expected_version)
            self._can_prepare(state)
            parent = self._round(state, round_id)
            if self._view_round(parent)["active"]:
                raise ValueError("human_session_round_pending")
            record = self._lookup(parent, target, candidate_id)
            feedback_id = uuid.uuid4().hex
            feedback = {"id": feedback_id, "round_id": round_id, "run_id": parent["run_id"],
                "target": target, "candidate_id": candidate_id, "content_sha256": digest(record),
                "revision_depth": _record_revision(record).get("depth", 0),
                "created_at": _now(), "applied_round_id": None, "manual_score": float(score) / 100,
                **correction}
            if messages is not None:
                feedback["manual_messages"] = deepcopy(messages)
            if corrected_record is not None:
                feedback["corrected_record"] = deepcopy(corrected_record)
            atomic_json(path / "feedback" / f"{feedback_id}.json", {"feedback": feedback, "record": record})
            for previous in state["feedback"]:
                if (previous["round_id"], previous["target"], previous["candidate_id"]) == (round_id, target, candidate_id):
                    previous["superseded_by"] = feedback_id
            state["feedback"].append(feedback)
            item = self._repair_input(path, state, feedback)
            item.update(score=float(score) / 100, approved=decision == "approve")
            if messages is not None:
                item["messages"] = deepcopy(messages)
            if corrected_record is not None:
                item["corrected_record"] = deepcopy(corrected_record)
            parents = [{key: feedback[key] for key in ("run_id", "round_id", "target", "candidate_id", "content_sha256")}]
            return self._prepare(path, state, {"enabled": False}, request_id=request_id, kind="manual_review",
                parents=parents, feedback_ids=[feedback_id], repair_inputs=[item], review_mode="human")

    def resume_round(self, session_id, round_id):
        path = self._path(session_id)
        with FileLock(str(path / ".session.lock")):
            state = self._read(path)
            row = self._round(state, round_id)
            view = self._view_round(row)
            if view["status"] in {"cancelled", "cancel_requested", "preparation_failed", "creating"}:
                raise ValueError("human_session_round_cancelled")
            self._assert_resume_safe(state, round_id)
            # No new run, design, request key or checkpoint identity is created.
            return self._reserve_resume(path, state, row)

    def cancel_round(self, session_id, round_id):
        path = self._path(session_id)
        with FileLock(str(path / ".session.lock")):
            state = self._read(path)
            row = self._round(state, round_id)
            view = self._view_round(row)
            if view["status"] in {"completed", "needs_attention", "cancelled", "preparation_failed"}:
                return view
            if view["state"]:
                self.workflow.cancel(row["run_id"])
            row["status"] = "cancel_requested" if view["active"] else "cancelled"
            self._save(path, state)
            return self._view_round(row)
