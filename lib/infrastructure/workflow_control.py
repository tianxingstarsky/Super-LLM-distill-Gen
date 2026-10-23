"""Filesystem details of the generic local control protocol; no agent SDK."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from filelock import FileLock

from lib.domain.control_protocol import ACTIONS, MUTATIONS, PROTOCOL, VERSION
from lib.infrastructure.training_workflow import atomic_json, digest, read_json, run_path


def public_state(state):
    fields = ("id", "name", "status", "created_at", "updated_at", "targets", "attempt", "stages",
              "usage", "delivery", "source_library", "production")
    result = {key: state[key] for key in fields if key in state}
    # Sources and producer events are deliberately read through bounded views.
    library = result.get("source_library")
    if isinstance(library, dict):
        result["source_library"] = {key: library[key] for key in ("parts", "text_chars", "cached_sources") if key in library}
    result["events"] = state.get("events", [])[-5:]
    return result


def read_events(output, run_id, *, offset=0, limit=50):
    path = run_path(output, run_id) / "events.jsonl"
    if not (path.parent / "state.json").is_file():
        raise ValueError("control_run_not_found")
    if not path.exists():
        if offset:
            raise ValueError("invalid_event_cursor")
        return {"items": [], "next_offset": 0, "has_more": False, "incomplete": False}
    if path.is_symlink():
        raise ValueError("invalid_event_file")
    items, total, incomplete = [], 0, False
    with path.open("rb") as handle:
        size = path.stat().st_size
        if offset > size:
            raise ValueError("invalid_event_cursor")
        if offset:
            handle.seek(offset - 1)
            if handle.read(1) != b"\n":
                raise ValueError("invalid_event_cursor")
        handle.seek(offset)
        while len(items) < limit and total < 65536:
            start = handle.tell()
            raw = handle.readline(65537)
            if not raw:
                break
            if len(raw) > 65536:
                raise ValueError("control_event_too_large")
            if not raw.endswith(b"\n"):
                handle.seek(start)
                incomplete = True
                break
            if total and total + len(raw) > 65536:
                handle.seek(start)
                break
            event = json.loads(raw.decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError("invalid_control_event")
            items.append(event)
            total += len(raw)
        cursor = handle.tell()
    return {"items": items, "next_offset": cursor, "has_more": cursor < size, "incomplete": incomplete}


class WorkflowControl:
    def __init__(self, workflows, backends, output):
        self.workflows, self.backends, self.output = workflows, backends, Path(output)

    def _create(self, request):
        """Retry a confirmed create; never repeat an ambiguous interrupted create."""
        key = hashlib.sha256(request["request_id"].encode()).hexdigest()
        folder = self.output / ".control-requests"
        folder.mkdir(parents=True, exist_ok=True)
        receipt = folder / (key + ".json")
        payload_hash = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False,
                                                 separators=(",", ":")).encode()).hexdigest()
        with FileLock(str(receipt) + ".lock", timeout=5):
            if receipt.exists():
                previous = read_json(receipt)
                if previous.get("request_sha256") != payload_hash:
                    raise ValueError("control_request_id_conflict")
                if previous.get("status") == "created":
                    return {"run_id": previous["run_id"], "created": True, "replayed": True}
                reserved_id = previous["run_id"]
                run = run_path(self.output, reserved_id)
                if (run / "state.json").is_file() and (run / "recipe.json").is_file():
                    state, frozen = read_json(run / "state.json"), read_json(run / "recipe.json")
                    if state.get("id") != reserved_id or state.get("recipe_hash") != digest(frozen):
                        raise ValueError("control_create_outcome_unconfirmed")
                    atomic_json(receipt, {**previous, "status": "created"})
                    return {"run_id": reserved_id, "created": True, "replayed": True}
                if run.exists():
                    raise ValueError("control_create_outcome_unconfirmed")
            else:
                reserved_id = uuid.uuid4().hex
            recipe = dict(request["params"]["recipe"])
            for name in ("sources", "evaluation_sources"):
                if name in recipe:
                    if not isinstance(recipe[name], list) or any(not isinstance(p, str) for p in recipe[name]):
                        raise ValueError("invalid_control_recipe")
                    recipe[name] = [Path(p) for p in recipe[name]]
            atomic_json(receipt, {"status": "creating", "request_sha256": payload_hash, "run_id": reserved_id})
            try:
                run_id = self.workflows.create_run(**recipe, run_id=reserved_id)
            except (ValueError, FileNotFoundError):
                # Validation failure may still leave an incomplete directory;
                # retaining the receipt avoids inventing an exactly-once claim.
                raise
            atomic_json(receipt, {"status": "created", "request_sha256": payload_hash, "run_id": run_id})
            return {"run_id": run_id, "created": True, "replayed": False}

    def handle(self, request):
        action, params = request["action"], request["params"]
        app = self.workflows
        if action == "capabilities":
            return {"protocol": PROTOCOL, "version": VERSION, "actions": sorted(ACTIONS),
                    "mutations": sorted(MUTATIONS), "execution": "foreground_checkpointed",
                    "events_cursor": "utf8_byte_offset", "max_page_items": 100,
                    "model_admission": "shared_across_runs_nodes_and_roles"}
        if action == "runs.create":
            return self._create(request)
        if action == "runs.list":
            rows = app.task_runs()
            offset, limit = params.get("offset", 0), params.get("limit", 50)
            return {"items": rows[offset:offset + limit], "total": len(rows),
                    "next_offset": min(len(rows), offset + limit)}
        if action == "runs.inspect":
            run_id = params["run_id"]
            return {"state": public_state(app.state(run_id)), "active": app.is_active(run_id),
                    "node_models": app.recipe(run_id).get("node_models", {})}
        if action == "runs.execute":
            return {"state": public_state(app.resume(params["run_id"]))}
        if action == "runs.cancel":
            app.cancel(params["run_id"])
            return {"run_id": params["run_id"], "cancellation_requested": True,
                    "active": app.is_active(params["run_id"])}
        if action == "runs.events":
            return read_events(self.output, **params)
        if action == "runs.streams":
            return {"items": app.read_streams(**params)}
        if action == "runs.stream_delta":
            return app.read_stream_delta(**params)
        if action == "runs.preview":
            return {"items": app.artifact_preview(**params)}
        if action == "runs.quality":
            return app.package_contents(params["run_id"])
        if action == "models.status":
            return self.backends.get_model_scheduling(**params)
        if action == "models.list":
            inventory = self.backends.list_backends()
            return {"default_backend": inventory["default_backend"], "default_model": inventory["default_model"],
                    "items": [{"backend": item["name"], "api_format": item["api_format"],
                               "models": list(dict.fromkeys([*item.get("models", []), *item.get("discovered_models", []),
                                                               *item.get("model_capabilities", {})]))}
                              for item in inventory["backends"]]}
        if action == "models.limit":
            return self.backends.save_model_scheduling(**params)
        raise ValueError("unknown_control_action")
